# coding: utf-8
"""Ctrl+C 保护必须覆盖主循环之前的阶段。

MD5 并发计算和 ExifTool 预热都在主循环之前，且常常是整轮里最耗时的部分。
若这两段的 KeyboardInterrupt 没被接住:
    - KeyboardInterrupt 不是 Exception 子类，main() 的 except Exception 接不住
    - 状态不存盘，几千个文件的进度丢失
    - 退出码不是文档承诺的 130
"""
from unittest.mock import patch

import pytest

from organizer import STATE_FILENAME, Config, Organizer


def _cfg(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"x" * 100)
    return Config(source_dir=src, target_dir=tmp_path / "tgt",
                  duplicate_dir=tmp_path / "dup", exiftool_path=None)


class TestInterruptBeforeMainLoop:
    def test_interrupt_during_hashing_returns_130(self, tmp_path):
        cfg = _cfg(tmp_path)
        org = Organizer(cfg)
        with patch.object(org, "_parallel_hash", side_effect=KeyboardInterrupt):
            assert org.run() == 130

    def test_interrupt_during_hashing_saves_state(self, tmp_path):
        cfg = _cfg(tmp_path)
        org = Organizer(cfg)
        with patch.object(org, "_parallel_hash", side_effect=KeyboardInterrupt):
            org.run()
        assert (cfg.target_dir / STATE_FILENAME).exists(), "中断必须存盘，否则进度全丢"

    def test_interrupt_during_prewarm_returns_130(self, tmp_path):
        cfg = _cfg(tmp_path)
        org = Organizer(cfg)
        with patch.object(org, "_prewarm_dates", side_effect=KeyboardInterrupt):
            assert org.run() == 130

    def test_interrupt_closes_exiftool_daemon(self, tmp_path):
        """任何中断路径都要关掉 daemon 子进程，避免残留"""
        cfg = _cfg(tmp_path)
        org = Organizer(cfg)
        with patch.object(org, "_parallel_hash", side_effect=KeyboardInterrupt), \
             patch.object(org.date_resolver, "close") as mock_close:
            org.run()
        assert mock_close.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
