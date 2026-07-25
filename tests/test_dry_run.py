# coding: utf-8
"""dry-run 是预演，不得对磁盘产生任何可见副作用。"""
from pathlib import Path

import pytest

from organizer import Config, Organizer


def _src_with_one_photo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"x" * 100)
    return src


class TestDryRunHasNoSideEffects:
    def test_creates_no_directories(self, tmp_path):
        """dry-run 不应创建 target / duplicates / 年月子目录"""
        src = _src_with_one_photo(tmp_path)
        tgt, dup = tmp_path / "tgt", tmp_path / "dup"

        Organizer(Config(source_dir=src, target_dir=tgt, duplicate_dir=dup,
                         exiftool_path=None, dry_run=True)).run()

        created = sorted(p.relative_to(tmp_path).as_posix()
                         for p in tmp_path.rglob("*") if p.is_dir())
        assert created == ["src"], f"dry-run 创建了目录: {created}"

    def test_source_file_untouched(self, tmp_path):
        """dry-run 后源文件必须原地不动"""
        src = _src_with_one_photo(tmp_path)

        Organizer(Config(source_dir=src, target_dir=tmp_path / "tgt",
                         duplicate_dir=tmp_path / "dup",
                         exiftool_path=None, dry_run=True)).run()

        assert (src / "a.jpg").read_bytes() == b"x" * 100

    def test_still_reports_planned_moves(self, tmp_path):
        """不建目录不代表不统计: 预演仍需报告将归档多少文件"""
        src = _src_with_one_photo(tmp_path)

        org = Organizer(Config(source_dir=src, target_dir=tmp_path / "tgt",
                               duplicate_dir=tmp_path / "dup",
                               exiftool_path=None, dry_run=True))
        org.run()

        assert org.stats.moved == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
