# coding: utf-8
"""状态文件版本迁移: v1 / v2 / v3 都必须能加载，且不丢断点路径。"""
import json

import pytest

from organizer import STATE_FILENAME, STATE_VERSION, load_state, save_state


def _write(tmp_path, payload):
    (tmp_path / STATE_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


class TestLoadState:
    def test_missing_file_returns_empty(self, tmp_path):
        s = load_state(tmp_path)
        assert s == {"size_hashes": {}, "size_pending": {}, "processed_paths": set()}

    def test_corrupt_file_returns_empty(self, tmp_path):
        (tmp_path / STATE_FILENAME).write_text("{ not json", encoding="utf-8")
        assert load_state(tmp_path)["processed_paths"] == set()

    def test_v1_drops_hashes_keeps_paths(self, tmp_path):
        """v1 没有 size 信息 → 哈希history 丢弃，断点路径保留"""
        _write(tmp_path, {"processed_hashes": ["aa", "bb"],
                          "processed_paths": ["D:/a.jpg"]})
        s = load_state(tmp_path)
        assert s["size_hashes"] == {}
        assert s["processed_paths"] == {"D:/a.jpg"}

    def test_v2_loads_hashes_with_empty_pending(self, tmp_path):
        """v2 无 size_pending 字段 → 按无欠账加载，不能崩"""
        _write(tmp_path, {"version": 2,
                          "size_hashes": {"100": ["aa"], "200": []},
                          "processed_paths": ["D:/a.jpg"]})
        s = load_state(tmp_path)
        assert s["size_hashes"] == {100: {"aa"}, 200: set()}
        assert s["size_pending"] == {}
        assert s["processed_paths"] == {"D:/a.jpg"}

    def test_v3_roundtrip(self, tmp_path):
        """v3 写出再读回必须完全等价"""
        hashes = {100: {"aa", "bb"}, 250: set()}
        pending = {250: ["D:/tgt/2024/01/x.jpg"]}
        paths = {"D:/src/x.jpg"}
        save_state(tmp_path, hashes, pending, paths)

        raw = json.loads((tmp_path / STATE_FILENAME).read_text(encoding="utf-8"))
        assert raw["version"] == STATE_VERSION == 3

        s = load_state(tmp_path)
        assert s["size_hashes"] == hashes
        assert s["size_pending"] == pending
        assert s["processed_paths"] == paths

    def test_empty_pending_buckets_not_persisted(self, tmp_path):
        """空欠账不写盘，避免状态文件无谓膨胀"""
        save_state(tmp_path, {}, {100: [], 200: ["D:/x.jpg"]}, set())
        raw = json.loads((tmp_path / STATE_FILENAME).read_text(encoding="utf-8"))
        assert raw["size_pending"] == {"200": ["D:/x.jpg"]}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
