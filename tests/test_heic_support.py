# coding: utf-8
"""HEIC 解码支持: iPhone 图库主体是 HEIC，--phash 必须能真正读到它们。

Pillow 本身不认 HEIC。没有 pillow-heif 时 phash_of() 对 .heic 一律返回 None，
等于 --phash 对整个 iPhone 图库静默失效——用户开了开关却什么也没做。
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from imageorg import imaging
from organizer import Config, Organizer, phash_of

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_HEIC = ROOT / "image" / "IMG_0086.HEIC"


class TestHeicDecoding:
    @pytest.mark.skipif(not SAMPLE_HEIC.exists(), reason="仓库内无样本 HEIC")
    def test_phash_of_reads_heic(self):
        """装了 pillow-heif 时，phash_of 必须能对 HEIC 算出哈希"""
        pytest.importorskip("pillow_heif")
        assert phash_of(SAMPLE_HEIC) is not None, "HEIC 应能解码出 pHash"

    @pytest.mark.skipif(not SAMPLE_HEIC.exists(), reason="仓库内无样本 HEIC")
    def test_heic_phash_is_stable(self):
        """同一文件两次计算结果一致 (排除随机性)"""
        pytest.importorskip("pillow_heif")
        assert phash_of(SAMPLE_HEIC) == phash_of(SAMPLE_HEIC)


class TestHeicUnsupportedWarning:
    """缺 pillow-heif 时不能静默: 必须告诉用户 --phash 对 HEIC 无效"""

    def test_warns_when_phash_enabled_without_heic_support(self, tmp_path, caplog):
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.heic").write_bytes(b"not really heic")

        cfg = Config(source_dir=src, target_dir=tmp_path / "tgt",
                     duplicate_dir=tmp_path / "dup",
                     exiftool_path=None, use_phash=True, no_resume=True)

        with patch.object(imaging, "HEIC_SUPPORTED", False):
            with caplog.at_level("WARNING"):
                Organizer(cfg).run()

        assert any("pillow-heif" in r.message for r in caplog.records), \
            "缺 HEIC 解码支持时必须告警"

    def test_no_warning_when_phash_disabled(self, tmp_path, caplog):
        """没开 --phash 就与 HEIC 解码无关，不该无端告警"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.heic").write_bytes(b"not really heic")

        cfg = Config(source_dir=src, target_dir=tmp_path / "tgt",
                     duplicate_dir=tmp_path / "dup",
                     exiftool_path=None, use_phash=False, no_resume=True)

        with patch.object(imaging, "HEIC_SUPPORTED", False):
            with caplog.at_level("WARNING"):
                Organizer(cfg).run()

        assert not any("pillow-heif" in r.message for r in caplog.records)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
