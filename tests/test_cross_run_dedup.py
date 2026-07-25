# coding: utf-8
"""跨次运行去重: size 短路放行的文件，其哈希必须能在后续运行中参与比对。

size 短路只在"该 size 唯一"时跳过 MD5。若第一次运行放行了某个 size，
第二次运行遇到字节完全相同的副本时，必须能识别为重复——否则分批导入照片
会漏检跨批次的重复。
"""
from pathlib import Path

import pytest

from organizer import Config, Organizer


CONTENT = b"x" * 12345  # 单次运行内 size 唯一，必然触发 size 短路


def _dirs(tmp_path: Path):
    src, tgt, dup = tmp_path / "src", tmp_path / "tgt", tmp_path / "dup"
    src.mkdir()
    return src, tgt, dup


def _run(src: Path, tgt: Path, dup: Path) -> Organizer:
    org = Organizer(Config(source_dir=src, target_dir=tgt,
                           duplicate_dir=dup, exiftool_path=None))
    org.run()
    return org


class TestCrossRunDedup:
    def test_identical_copy_in_later_run_is_detected(self, tmp_path):
        """第 1 次 size 短路放行 → 第 2 次同内容文件应判为字节重复"""
        src, tgt, dup = _dirs(tmp_path)

        (src / "a.jpg").write_bytes(CONTENT)
        _run(src, tgt, dup)

        (src / "b.jpg").write_bytes(CONTENT)
        org2 = _run(src, tgt, dup)

        assert org2.stats.duplicates == 1, "字节相同的副本必须被识别为重复"
        assert (dup / "b.jpg").exists(), "重复文件应进 duplicates 目录"

    def test_different_content_same_size_in_later_run_is_kept(self, tmp_path):
        """同 size 但内容不同 → 必须正常归档，不能误判为重复"""
        src, tgt, dup = _dirs(tmp_path)

        (src / "a.jpg").write_bytes(CONTENT)
        _run(src, tgt, dup)

        (src / "b.jpg").write_bytes(b"y" * len(CONTENT))  # 同 size 不同内容
        org2 = _run(src, tgt, dup)

        assert org2.stats.duplicates == 0, "同 size 不同内容不应误判"
        assert org2.stats.moved == 1
        assert list(dup.glob("*")) == []

    def test_backfill_survives_missing_archived_file(self, tmp_path):
        """已归档文件被用户手工删除 → 补算哈希应跳过它而非崩溃"""
        src, tgt, dup = _dirs(tmp_path)

        (src / "a.jpg").write_bytes(CONTENT)
        _run(src, tgt, dup)

        for archived in tgt.rglob("a.jpg"):
            archived.unlink()

        (src / "b.jpg").write_bytes(CONTENT)
        org2 = _run(src, tgt, dup)

        assert org2.stats.errored == 0, "补算目标缺失不应记为脚本异常"
        assert org2.stats.moved == 1, "参照物没了，只能放行"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
