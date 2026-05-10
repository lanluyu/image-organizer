# coding: utf-8
"""group_files / normalize_stem 的核心配对逻辑测试"""
from pathlib import Path

import pytest

from organizer import group_files, normalize_stem


class TestNormalizeStem:
    def test_img_e_prefix_normalized(self):
        assert normalize_stem("IMG_E1234") == "IMG_1234"

    def test_img_e_lowercase(self):
        assert normalize_stem("img_e1234") == "img_1234"

    def test_plain_img_unchanged(self):
        assert normalize_stem("IMG_1234") == "IMG_1234"

    def test_non_img_unchanged(self):
        assert normalize_stem("foo_bar") == "foo_bar"

    def test_e_in_middle_unchanged(self):
        # IMG_E 模式只匹配开头，避免误伤
        assert normalize_stem("FOOIMG_E1234") == "FOOIMG_E1234"


class TestGroupFiles:
    def test_single_file_alone(self):
        files = [Path("/x/IMG_1234.HEIC")]
        groups = group_files(files)
        assert len(groups) == 1
        assert groups[0].primary == files[0]
        assert groups[0].companions == []

    def test_live_photo_pair(self):
        # HEIC + MOV 同名 → 同组，HEIC 当 primary (图片优先于视频)
        files = [Path("/x/IMG_1234.HEIC"), Path("/x/IMG_1234.MOV")]
        groups = group_files(files)
        assert len(groups) == 1
        assert groups[0].primary.suffix == ".HEIC"
        assert len(groups[0].companions) == 1
        assert groups[0].companions[0].suffix == ".MOV"

    def test_aae_sidecar_with_img_e_prefix(self):
        """关键回归: 编辑过的 IMG_E1234.AAE 必须与 IMG_1234.HEIC 配对"""
        files = [
            Path("/x/IMG_1234.HEIC"),
            Path("/x/IMG_E1234.AAE"),
        ]
        groups = group_files(files)
        assert len(groups) == 1, "IMG_E AAE 应与原图 IMG_ 归到同组"
        assert groups[0].primary.suffix == ".HEIC"
        assert groups[0].companions[0].name == "IMG_E1234.AAE"

    def test_full_iphone_bundle(self):
        # 完整场景: 原图 HEIC + Live MOV + 编辑后 JPG + 编辑参数 AAE
        files = [
            Path("/x/IMG_1234.HEIC"),
            Path("/x/IMG_1234.MOV"),
            Path("/x/IMG_E1234.JPG"),
            Path("/x/IMG_E1234.AAE"),
        ]
        groups = group_files(files)
        assert len(groups) == 1
        # primary 应该是图片之一 (HEIC 或 JPG，都是 priority 0)
        assert groups[0].primary.suffix.lower() in {".heic", ".jpg"}
        assert len(groups[0].companions) == 3

    def test_different_dirs_not_grouped(self):
        # 不同目录同名也不该配对 (避免跨目录误聚合)
        files = [Path("/a/IMG_1.HEIC"), Path("/b/IMG_1.HEIC")]
        groups = group_files(files)
        assert len(groups) == 2

    def test_case_insensitive_stem(self):
        files = [Path("/x/IMG_1234.HEIC"), Path("/x/img_1234.MOV")]
        groups = group_files(files)
        assert len(groups) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
