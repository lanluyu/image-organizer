# coding: utf-8
"""
iPhone 照片/视频整理工具 — 兼容入口
====================================

核心功能:
    1. 按拍摄时间 (Exif/QuickTime) 归档到 年/月 子目录
    2. 字节级去重 (size 短路 + MD5)，可选感知哈希 (pHash) 视觉去重
    3. Live Photo (HEIC+MOV / JPG+MOV 同名) 与 AAE sidecar 跟随主文件
    4. 断点续跑 (.organizer_state.json) + 单文件局部容错 + 末尾失败清单
    5. 心跳输出 + dry-run 预演 + 并发 MD5/pHash 计算

用法:
    python organizer.py --source <源目录> --target <目标目录> --duplicates <重复目录>
    python organizer.py --source ... --target ... --duplicates ... --dry-run
    python organizer.py --source ... --target ... --duplicates ... --phash --workers 8

退出码:
    0 = 全部成功
    1 = 有 FAIL (业务上已知错误，例如读不到时间但已用降级方案)
    2 = 有 ERROR (脚本本身异常，需人工排查)
    130 = 用户 Ctrl+C 中断 (状态已存盘，可续跑)

实现已按职责拆分到 imageorg/ 包，本文件只做再导出，保持
`python organizer.py ...` 与 `from organizer import X` 两种既有用法可用。
"""

from __future__ import annotations

import sys

from imageorg.cli import build_parser, detect_default_exiftool, main
from imageorg.constants import (DATE_KEYS, HEARTBEAT_INTERVAL_SEC,
                                IMAGE_EXTENSIONS, SIDECAR_EXTENSIONS,
                                STATE_FILENAME, SUPPORTED_EXTENSIONS,
                                VIDEO_EXTENSIONS, normalize_stem)
from imageorg.core import Organizer
from imageorg.dates import DateResolver
from imageorg.dedup import PHashBKTree, hamming, md5_of, phash_of
from imageorg.exiftool import ExifToolDaemon, _normalize_path_key
from imageorg.grouping import FileGroup, group_files
from imageorg.imaging import HEIC_SUPPORTED, Image, imagehash
from imageorg.models import Config, FailureRecord, Stats
from imageorg.state import STATE_VERSION, load_state, save_state

__all__ = [
    "Config", "DateResolver", "ExifToolDaemon", "FailureRecord", "FileGroup",
    "HEIC_SUPPORTED", "Image", "Organizer", "PHashBKTree", "Stats",
    "DATE_KEYS", "HEARTBEAT_INTERVAL_SEC", "IMAGE_EXTENSIONS",
    "SIDECAR_EXTENSIONS", "STATE_FILENAME", "STATE_VERSION",
    "SUPPORTED_EXTENSIONS", "VIDEO_EXTENSIONS",
    "build_parser", "detect_default_exiftool", "group_files", "hamming",
    "imagehash", "load_state", "main", "md5_of", "normalize_stem",
    "phash_of", "save_state", "_normalize_path_key",
]


if __name__ == "__main__":
    sys.exit(main())
