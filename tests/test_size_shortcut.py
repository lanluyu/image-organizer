# coding: utf-8
"""验证 size 短路: size 唯一的文件不应触发 MD5 计算"""
from pathlib import Path
from unittest.mock import patch

import pytest

from organizer import Config, FileGroup, Organizer


def _make_file(dir_path: Path, name: str, content: bytes) -> Path:
    p = dir_path / name
    p.write_bytes(content)
    return p


@pytest.fixture
def organizer(tmp_path):
    cfg = Config(
        source_dir=tmp_path / "src",
        target_dir=tmp_path / "tgt",
        duplicate_dir=tmp_path / "dup",
        no_resume=True,
    )
    cfg.source_dir.mkdir()
    return Organizer(cfg)


class TestSizeShortcut:
    def test_unique_sizes_skip_hash(self, organizer, tmp_path):
        """所有 size 都唯一 → _select_hash_candidates 应返回空列表"""
        f1 = _make_file(tmp_path / "src", "a.jpg", b"x" * 100)
        f2 = _make_file(tmp_path / "src", "b.jpg", b"y" * 200)
        f3 = _make_file(tmp_path / "src", "c.jpg", b"z" * 300)
        groups = [FileGroup(primary=p, companions=[]) for p in [f1, f2, f3]]

        sizes = organizer._stat_sizes(groups)
        candidates = organizer._select_hash_candidates(groups, sizes)

        assert candidates == [], "size 唯一时不应进入 MD5 候选"

    def test_duplicate_sizes_in_batch_need_hash(self, organizer, tmp_path):
        """同批次出现 size 重复 → 这些文件必须算 hash"""
        f1 = _make_file(tmp_path / "src", "a.jpg", b"x" * 100)
        f2 = _make_file(tmp_path / "src", "b.jpg", b"y" * 100)  # 同 size 不同内容
        f3 = _make_file(tmp_path / "src", "c.jpg", b"z" * 200)
        groups = [FileGroup(primary=p, companions=[]) for p in [f1, f2, f3]]

        sizes = organizer._stat_sizes(groups)
        candidates = organizer._select_hash_candidates(groups, sizes)
        candidate_paths = {g.primary for g in candidates}

        assert candidate_paths == {f1, f2}, "同 size 桶内必算 hash, 唯一 size 跳过"

    def test_size_in_history_needs_hash(self, organizer, tmp_path):
        """size 在历史 size_to_hashes 中出现过 → 即使本批次唯一也要算 hash"""
        f1 = _make_file(tmp_path / "src", "a.jpg", b"x" * 100)
        groups = [FileGroup(primary=f1, companions=[])]

        # 模拟上次运行已记录 size=100 的某个 hash
        organizer._size_to_hashes[100] = {"deadbeef" * 4}

        sizes = organizer._stat_sizes(groups)
        candidates = organizer._select_hash_candidates(groups, sizes)

        assert len(candidates) == 1, "size 在历史中出现过必须算 hash 比对"


class TestProcessGroupSizeShortcut:
    def test_unique_size_no_hash_call(self, organizer, tmp_path):
        """size 唯一的文件应直接归档，全程不调 md5_of"""
        f1 = _make_file(tmp_path / "src", "a.jpg", b"x" * 100)
        group = FileGroup(primary=f1, companions=[])

        with patch("organizer.md5_of") as mock_md5, \
             patch.object(organizer.date_resolver, "resolve",
                          return_value=(__import__("datetime").datetime(2024, 1, 15), "test")):
            organizer._process_group(group, size=100, file_hash=None)

        assert mock_md5.call_count == 0, "size 短路下不应调用 md5_of"
        assert organizer.stats.moved == 1
        assert 100 in organizer._size_to_hashes  # size 已登记

    def test_size_collision_triggers_hash(self, organizer, tmp_path):
        """size 已在 _size_to_hashes 中且 file_hash=None → 必须现场补算"""
        f1 = _make_file(tmp_path / "src", "a.jpg", b"hello")
        group = FileGroup(primary=f1, companions=[])
        organizer._size_to_hashes[5] = {"some_other_hash"}  # 同 size 历史

        with patch("organizer.md5_of", return_value="new_hash") as mock_md5, \
             patch.object(organizer.date_resolver, "resolve",
                          return_value=(__import__("datetime").datetime(2024, 1, 15), "test")):
            organizer._process_group(group, size=5, file_hash=None)

        assert mock_md5.call_count == 1, "size 历史撞了必须现场算 hash"
        assert "new_hash" in organizer._size_to_hashes[5]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
