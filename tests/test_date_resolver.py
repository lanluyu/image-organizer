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
        with patch("organizer.ExifToolDaemon", return_value=mock_daemon):
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
        with patch("organizer.ExifToolDaemon", side_effect=RuntimeError("启动失败")):
            r.prewarm([f])

        assert r._daemon_disabled is True
        # resolve 不应再触发 daemon (走 mtime fallback)
        with patch("organizer.ExifToolDaemon") as mock_class:
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
        with patch("organizer.ExifToolDaemon", return_value=mock_daemon):
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
        with patch("organizer.ExifToolDaemon", return_value=mock_daemon):
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
        with patch("organizer.ExifToolDaemon", return_value=mock_daemon):
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
