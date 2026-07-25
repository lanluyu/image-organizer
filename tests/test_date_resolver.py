# coding: utf-8
"""DateResolver 缓存层 + daemon 集成的单元测试"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from organizer import DateResolver, ExifToolDaemon, _normalize_path_key


class TestPrewarmAndCache:
    def test_no_exiftool_skips_daemon(self, tmp_path):
        """exiftool_path 不存在 → prewarm 是 no-op，resolve 走 fallback (mtime)"""
        f = tmp_path / "a.jpg"
        f.write_bytes(b"x")

        r = DateResolver(exiftool_path=None)
        r.prewarm([f])  # 不应抛错

        dt, src = r.resolve(f)
        assert src == "mtime"
        assert isinstance(dt, datetime)

    def test_prewarm_populates_cache(self, tmp_path):
        """daemon 返回成功的 record 应填充缓存，resolve 命中后标签为 exiftool-cache"""
        # 真实存在的 exiftool 路径 (随便一个文件冒充，DateResolver 只检查 exists())
        fake_exiftool = tmp_path / "exiftool.exe"
        fake_exiftool.write_bytes(b"fake")
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")

        # mock daemon 返回 2024-01-15 的 EXIF 时间
        mock_daemon = MagicMock(spec=ExifToolDaemon)
        mock_daemon.query_batch.return_value = {
            _normalize_path_key(f): {
                "SourceFile": str(f),
                "EXIF:DateTimeOriginal": "2024:01:15 10:30:45",
            }
        }

        r = DateResolver(exiftool_path=fake_exiftool)
        with patch("imageorg.exiftool.ExifToolDaemon", return_value=mock_daemon):
            r.prewarm([f])

        # 命中缓存
        dt, src = r.resolve(f)
        assert src == "exiftool-cache"
        assert dt == datetime(2024, 1, 15, 10, 30, 45)
        # resolve 命中后不应再调 daemon
        assert mock_daemon.query_batch.call_count == 1  # 只在 prewarm 时调过

    def test_prewarm_daemon_start_failure_disables(self, tmp_path):
        """daemon 启动直接抛错 → 标记 disabled，后续 resolve 不再尝试 daemon"""
        fake_exiftool = tmp_path / "exiftool.exe"
        fake_exiftool.write_bytes(b"fake")
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")

        r = DateResolver(exiftool_path=fake_exiftool)
        with patch("imageorg.exiftool.ExifToolDaemon", side_effect=RuntimeError("启动失败")):
            r.prewarm([f])

        assert r._daemon_disabled is True
        # resolve 不应再触发 daemon (走 mtime fallback)
        with patch("imageorg.exiftool.ExifToolDaemon") as mock_class:
            dt, src = r.resolve(f)
            assert mock_class.call_count == 0
        assert src == "mtime"

    def test_prewarm_batch_failure_continues(self, tmp_path):
        """单批 query_batch 抛 RuntimeError 不应禁用 daemon (可能只是单批问题)"""
        fake_exiftool = tmp_path / "exiftool.exe"
        fake_exiftool.write_bytes(b"fake")
        f1 = tmp_path / "p1.jpg"
        f2 = tmp_path / "p2.jpg"
        f1.write_bytes(b"x")
        f2.write_bytes(b"y")

        mock_daemon = MagicMock(spec=ExifToolDaemon)
        # 第一批抛错，第二批成功
        mock_daemon.query_batch.side_effect = [
            RuntimeError("批 1 失败"),
            {
                _normalize_path_key(f2): {
                    "SourceFile": str(f2),
                    "EXIF:DateTimeOriginal": "2024:02:20 15:00:00",
                }
            },
        ]

        r = DateResolver(exiftool_path=fake_exiftool)
        with patch("imageorg.exiftool.ExifToolDaemon", return_value=mock_daemon):
            r.prewarm([f1, f2], batch_size=1)

        assert r._daemon_disabled is False, "单批失败不应全局禁用"
        # f2 应该被缓存
        dt, src = r.resolve(f2)
        assert src == "exiftool-cache"
        assert dt.year == 2024 and dt.month == 2


class TestResolveFallback:
    def test_resolve_lazy_starts_daemon_on_miss(self, tmp_path):
        """缓存未命中 → resolve 应懒启动 daemon 单文件查询"""
        fake_exiftool = tmp_path / "exiftool.exe"
        fake_exiftool.write_bytes(b"fake")
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")

        mock_daemon = MagicMock(spec=ExifToolDaemon)
        mock_daemon.query_batch.return_value = {
            _normalize_path_key(f): {
                "EXIF:DateTimeOriginal": "2024:03:01 12:00:00",
            }
        }

        r = DateResolver(exiftool_path=fake_exiftool)  # 不调 prewarm
        with patch("imageorg.exiftool.ExifToolDaemon", return_value=mock_daemon):
            dt, src = r.resolve(f)

        assert src == "exiftool"
        assert dt == datetime(2024, 3, 1, 12, 0, 0)

    def test_resolve_daemon_dies_disables_and_falls_back(self, tmp_path):
        """daemon 单文件查询抛 RuntimeError → 全局禁用 + fallback 到 mtime"""
        fake_exiftool = tmp_path / "exiftool.exe"
        fake_exiftool.write_bytes(b"fake")
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")

        mock_daemon = MagicMock(spec=ExifToolDaemon)
        mock_daemon.query_batch.side_effect = RuntimeError("daemon 死了")

        r = DateResolver(exiftool_path=fake_exiftool)
        with patch("imageorg.exiftool.ExifToolDaemon", return_value=mock_daemon):
            dt, src = r.resolve(f)

        assert src == "mtime"
        assert r._daemon_disabled is True


class TestClose:
    def test_close_idempotent_no_daemon(self, tmp_path):
        """没启动 daemon 也能 close (幂等)"""
        r = DateResolver(exiftool_path=None)
        r.close()
        r.close()

    def test_close_calls_daemon_close(self, tmp_path):
        fake_exiftool = tmp_path / "exiftool.exe"
        fake_exiftool.write_bytes(b"fake")
        mock_daemon = MagicMock(spec=ExifToolDaemon)

        r = DateResolver(exiftool_path=fake_exiftool)
        r._daemon = mock_daemon
        r.close()

        assert mock_daemon.close.called
        assert r._daemon is None


class TestCaptureTimeProvenance:
    """归档时间的来源必须如实反映: 文件系统时间不能冒充拍摄时间。

    exiftool 对任何文件都会返回 File:FileModifyDate (即 mtime)。若把它当作
    有效的拍摄时间，则从 iPhone 拷出、EXIF 被剥掉的照片会按"拷贝时间"归档到
    错误的年份，而日志显示 source=exiftool，用户无从察觉。
    """

    def test_file_modify_date_alone_is_not_a_capture_time(self):
        """记录里只有 File:FileModifyDate → 视为没有拍摄时间"""
        rec = {"SourceFile": "x.jpg", "File:FileModifyDate": "2019:03:15 10:30:00+08:00"}
        assert DateResolver._parse_exif_record(rec) is None

    def test_real_exif_date_still_wins(self):
        """有真实 EXIF 拍摄时间时正常解析，不受上一条影响"""
        rec = {
            "SourceFile": "x.jpg",
            "EXIF:DateTimeOriginal": "2024:01:15 10:30:45",
            "File:FileModifyDate": "2019:03:15 10:30:00+08:00",
        }
        assert DateResolver._parse_exif_record(rec) == datetime(2024, 1, 15, 10, 30, 45)

    def test_no_exif_date_reports_mtime_source(self, tmp_path):
        """无任何 EXIF 时间字段 → resolve 必须标注来源为 mtime"""
        fake_exiftool = tmp_path / "exiftool.exe"
        fake_exiftool.write_bytes(b"fake")
        f = tmp_path / "photo.jpg"
        f.write_bytes(b"x")

        mock_daemon = MagicMock(spec=ExifToolDaemon)
        mock_daemon.query_batch.return_value = {
            _normalize_path_key(f): {
                "SourceFile": str(f),
                "File:FileModifyDate": "2019:03:15 10:30:00+08:00",
            }
        }

        r = DateResolver(exiftool_path=fake_exiftool)
        with patch("imageorg.exiftool.ExifToolDaemon", return_value=mock_daemon):
            r.prewarm([f])
            dt, src = r.resolve(f)

        assert src == "mtime", "只有文件系统时间时不得标注为 exiftool"


class TestMtimeFallbackCounter:
    """降级到文件系统时间的数量必须计数并出现在汇总里"""

    def test_stats_counts_mtime_fallback(self, tmp_path):
        from organizer import Config, FileGroup, Organizer

        src = tmp_path / "src"
        src.mkdir()
        f = src / "a.jpg"
        f.write_bytes(b"x" * 100)

        org = Organizer(Config(source_dir=src, target_dir=tmp_path / "tgt",
                               duplicate_dir=tmp_path / "dup", exiftool_path=None))
        org._process_group(FileGroup(primary=f, companions=[]), size=100, file_hash=None)

        assert org.stats.date_from_mtime == 1

    def test_stats_does_not_count_real_exif(self, tmp_path):
        from organizer import Config, FileGroup, Organizer

        src = tmp_path / "src"
        src.mkdir()
        f = src / "a.jpg"
        f.write_bytes(b"x" * 100)

        org = Organizer(Config(source_dir=src, target_dir=tmp_path / "tgt",
                               duplicate_dir=tmp_path / "dup", exiftool_path=None))
        with patch.object(org.date_resolver, "resolve",
                          return_value=(datetime(2024, 1, 15), "exiftool")):
            org._process_group(FileGroup(primary=f, companions=[]), size=100, file_hash=None)

        assert org.stats.date_from_mtime == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
