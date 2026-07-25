# coding: utf-8
"""PHashBKTree: 汉明距离 BK-tree 的单元测试"""
import random

import pytest

from organizer import PHashBKTree


class TestBasicOperations:
    def test_empty_tree_query_returns_empty(self):
        t = PHashBKTree()
        assert t.query(0xDEADBEEF, max_dist=10) == []
        assert len(t) == 0

    def test_single_node_exact_match(self):
        t = PHashBKTree()
        t.add(0xABCD, "photo_a.jpg")
        results = t.query(0xABCD, max_dist=0)
        assert results == [(0, "photo_a.jpg")]

    def test_single_node_within_threshold(self):
        t = PHashBKTree()
        t.add(0xFF00, "x.jpg")
        # 0xFF01 与 0xFF00 差 1 bit
        results = t.query(0xFF01, max_dist=1)
        assert results == [(1, "x.jpg")]

    def test_single_node_beyond_threshold(self):
        t = PHashBKTree()
        t.add(0xFF00, "x.jpg")
        # 0x00FF 与 0xFF00 差 16 bit (高 8 翻转 + 低 8 翻转)
        assert t.query(0x00FF, max_dist=4) == []


class TestMultipleNodes:
    def test_multiple_inserts_size_correct(self):
        t = PHashBKTree()
        for i in range(100):
            t.add(i, f"v{i}")
        assert len(t) == 100

    def test_query_returns_sorted_by_distance(self):
        t = PHashBKTree()
        # 0b0000, 0b0001, 0b0011, 0b0111, 0b1111 距 0b0000 分别为 0, 1, 2, 3, 4
        for v in [0, 1, 0b11, 0b111, 0b1111]:
            t.add(v, f"v{v}")
        results = t.query(0, max_dist=3)
        dists = [d for d, _ in results]
        assert dists == sorted(dists), "结果应按距离升序"
        assert all(d <= 3 for d in dists)
        assert (4, "v15") not in results, "距离 4 不应进入 max_dist=3 结果"


class TestPruningCorrectness:
    """关键正确性: BK-tree 三角不等式剪枝必须不漏掉任何真实近邻"""

    def test_pruning_does_not_miss_neighbors(self):
        """对比线性扫描结果，BK-tree 必须返回完全相同的近邻集"""
        rng = random.Random(42)
        values = [rng.getrandbits(64) for _ in range(500)]

        t = PHashBKTree()
        for i, v in enumerate(values):
            t.add(v, i)

        query = rng.getrandbits(64)
        max_dist = 8

        # 线性 ground truth
        expected = sorted(
            [(bin(v ^ query).count("1"), i) for i, v in enumerate(values)
             if bin(v ^ query).count("1") <= max_dist]
        )
        actual = t.query(query, max_dist)

        assert sorted(actual) == expected, "BK-tree 剪枝不能漏掉任何真实近邻"

    def test_high_threshold_returns_all(self):
        """max_dist=64 (full hash 长度) 应返回所有节点"""
        t = PHashBKTree()
        for i in range(50):
            t.add(i, i)
        results = t.query(0, max_dist=64)
        assert len(results) == 50


class TestOrganizerIntegration:
    """验证 Organizer._process_group 用 BK-tree 的视觉去重路径"""

    def test_phash_visual_duplicate_detected(self, tmp_path):
        """两张相似图 (phash 距离 ≤ threshold) → 第二张应进 duplicates"""
        from unittest.mock import patch
        from organizer import Config, FileGroup, Organizer

        cfg = Config(
            source_dir=tmp_path / "src",
            target_dir=tmp_path / "tgt",
            duplicate_dir=tmp_path / "dup",
            no_resume=True,
            use_phash=True,
            phash_threshold=4,
        )
        cfg.source_dir.mkdir()
        cfg.target_dir.mkdir()
        cfg.duplicate_dir.mkdir()
        org = Organizer(cfg)

        f1 = cfg.source_dir / "a.jpg"
        f2 = cfg.source_dir / "b.jpg"
        f1.write_bytes(b"x" * 100)
        f2.write_bytes(b"y" * 200)  # 不同 size 避免字节去重抢先

        # 让 phash_of 第一次返回 "ff00"，第二次返回 "ff01" (距离 1，命中阈值 4)
        from datetime import datetime
        with patch("imageorg.dedup.phash_of", side_effect=["ff00", "ff01"]), \
             patch.object(org.date_resolver, "resolve",
                          return_value=(datetime(2024, 1, 1), "test")):
            org._process_group(FileGroup(primary=f1, companions=[]), size=100, file_hash="h1")
            org._process_group(FileGroup(primary=f2, companions=[]), size=200, file_hash="h2")

        assert org.stats.moved == 1, "f1 应正常归档"
        assert org.stats.visual_duplicates == 1, "f2 应被识别为视觉重复"
        assert len(org._phash_tree) == 1, "只有 f1 进了 BK-tree"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
