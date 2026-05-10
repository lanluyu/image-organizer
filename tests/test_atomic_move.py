# coding: utf-8
"""验证 _atomic_move 的同卷/跨卷行为"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from organizer import Organizer


class TestAtomicMove:
    def test_same_volume_uses_replace(self, tmp_path):
        """同卷场景应走 os.replace 单步原子，不进 fallback 分支"""
        src = tmp_path / "a.jpg"
        src.write_bytes(b"hello")
        tgt = tmp_path / "sub" / "a.jpg"
        tgt.parent.mkdir()

        with patch("organizer.shutil.copy2") as mock_copy:
            Organizer._atomic_move(src, tgt)

        assert tgt.read_bytes() == b"hello"
        assert not src.exists()
        assert mock_copy.call_count == 0, "同卷不应走 copy 分支"

    def test_cross_volume_falls_back_to_two_phase(self, tmp_path):
        """模拟 os.replace 抛 OSError (跨卷)，应走 copy → fsync → replace → unlink"""
        src = tmp_path / "a.jpg"
        src.write_bytes(b"hello")
        tgt = tmp_path / "sub" / "a.jpg"
        tgt.parent.mkdir()

        original_replace = os.replace
        replace_calls = {"n": 0}

        def fake_replace(s, t):
            replace_calls["n"] += 1
            if replace_calls["n"] == 1:
                # 第一次 (source → target) 模拟跨卷
                raise OSError(18, "Invalid cross-device link")
            # 第二次 (.partial → target) 让它真做
            return original_replace(s, t)

        with patch("organizer.os.replace", side_effect=fake_replace):
            Organizer._atomic_move(src, tgt)

        assert tgt.read_bytes() == b"hello"
        assert not src.exists()
        # .partial 应该已被清理 (rename 后就不存在了)
        assert not (tgt.parent / "a.jpg.partial").exists()

    def test_cross_volume_copy_failure_cleans_partial(self, tmp_path):
        """跨卷 copy 失败应清理 .partial 残留，源文件保持完整"""
        src = tmp_path / "a.jpg"
        src.write_bytes(b"hello")
        tgt = tmp_path / "sub" / "a.jpg"
        tgt.parent.mkdir()

        def cross_volume(s, t):
            raise OSError(18, "Invalid cross-device link")

        def copy_fail(s, t):
            # 模拟 copy 写到一半: 先创建 .partial，再抛错
            Path(t).write_bytes(b"hal")
            raise IOError("disk full")

        with patch("organizer.os.replace", side_effect=cross_volume), \
             patch("organizer.shutil.copy2", side_effect=copy_fail):
            with pytest.raises(IOError):
                Organizer._atomic_move(src, tgt)

        assert src.read_bytes() == b"hello", "源文件必须完好"
        assert not tgt.exists(), "target 不应成形"
        assert not (tgt.parent / "a.jpg.partial").exists(), ".partial 必须清理"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
