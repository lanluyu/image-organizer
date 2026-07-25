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

        with patch("imageorg.core.shutil.copy2") as mock_copy:
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

        with patch("imageorg.core.os.replace", side_effect=fake_replace):
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

        with patch("imageorg.core.os.replace", side_effect=cross_volume), \
             patch("imageorg.core.shutil.copy2", side_effect=copy_fail):
            with pytest.raises(IOError):
                Organizer._atomic_move(src, tgt)

        assert src.read_bytes() == b"hello", "源文件必须完好"
        assert not tgt.exists(), "target 不应成形"
        assert not (tgt.parent / "a.jpg.partial").exists(), ".partial 必须清理"


class TestNonCrossVolumeErrors:
    """只有"跨卷"才该退到 copy 两阶段。其它 OSError 必须原样抛出。

    权限拒绝、目标被占用等都是 OSError，若一律当跨卷处理，就会把一个本该失败的
    移动变成"复制一份再删源"，在删源同样失败时留下两份数据。
    """

    def test_permission_error_is_not_treated_as_cross_volume(self, tmp_path):
        src = tmp_path / "a.jpg"
        src.write_bytes(b"hello")
        tgt = tmp_path / "sub" / "a.jpg"
        tgt.parent.mkdir()

        def denied(s, t):
            raise PermissionError(13, "Permission denied")

        with patch("imageorg.core.os.replace", side_effect=denied), \
             patch("imageorg.core.shutil.copy2") as mock_copy:
            with pytest.raises(PermissionError):
                Organizer._atomic_move(src, tgt)

        assert mock_copy.call_count == 0, "权限错误不该退化成 copy"
        assert src.read_bytes() == b"hello", "源文件必须完好"

    def test_windows_not_same_device_is_cross_volume(self, tmp_path):
        """Windows 上跨卷可能只带 winerror 17 (ERROR_NOT_SAME_DEVICE)"""
        src = tmp_path / "a.jpg"
        src.write_bytes(b"hello")
        tgt = tmp_path / "sub" / "a.jpg"
        tgt.parent.mkdir()

        original_replace = os.replace
        calls = {"n": 0}

        def fake_replace(s, t):
            calls["n"] += 1
            if calls["n"] == 1:
                err = OSError(0, "cannot move the file to a different disk drive")
                err.winerror = 17
                raise err
            return original_replace(s, t)

        with patch("imageorg.core.os.replace", side_effect=fake_replace):
            Organizer._atomic_move(src, tgt)

        assert tgt.read_bytes() == b"hello"
        assert not src.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
