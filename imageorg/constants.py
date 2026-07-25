# coding: utf-8
"""扩展名集合、ExifTool 时间字段优先级、运行常量与文件名归一。"""
from __future__ import annotations

import re


# 支持的媒体扩展名 (其它文件直接 skipped 计数，不动)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".dng",
                    ".tiff", ".tif", ".jfif", ".ico", ".bmp", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm", ".m4v", ".3gp"}
SIDECAR_EXTENSIONS = {".aae"}  # 跟随主文件走，不独立归档
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | SIDECAR_EXTENSIONS

# ExifTool 时间字段优先级 (前面优先级更高)
DATE_KEYS = [
    "EXIF:DateTimeOriginal",   # 相机拍摄原始时间，最权威
    "EXIF:CreateDate",
    "QuickTime:CreateDate",    # 视频常见
    "QuickTime:MediaCreateDate",
    "QuickTime:TrackCreateDate",
    "QuickTime:CreationDate",
    "XMP:CreateDate",
    "XMP:ModifyDate",
    # 兜底：不带组名的字段 (ExifTool 版本差异)
    "DateTimeOriginal", "CreateDate", "CreationDate", "ModifyDate",
]
# 注意 File:FileModifyDate 刻意不在上表内。它对任何文件都存在 (就是 mtime)，
# 一旦纳入，_parse_exif_record 永远有返回值 → Pillow 分支变成死代码，且从 iPhone
# 拷出、EXIF 被剥掉的照片会按"拷贝时间"归档到错误年份，日志却显示 source=exiftool。
# 没有真实拍摄时间时必须一路降级到 mtime，由 source 标签如实反映。

# 心跳输出间隔 (秒): 长批处理必须有进度反馈，否则没法判断卡住还是在跑
HEARTBEAT_INTERVAL_SEC = 30

# 状态文件名 (放在 target_dir 下，用于断点续跑)
STATE_FILENAME = ".organizer_state.json"

# iPhone 编辑过的照片会得到 IMG_E1234.{HEIC,JPG,AAE}，与原图 IMG_1234.HEIC 同属一组。
# 配对前先把 stem 做归一化，否则 AAE 会被当成独立 primary 单独归档。
_IMG_E_PREFIX = re.compile(r"^(IMG_)E(\d)", re.IGNORECASE)


def normalize_stem(stem: str) -> str:
    """
    把 iPhone 编辑变体的文件名归一到原始 stem，用于 Live Photo / AAE 配对。
        IMG_E1234 -> IMG_1234
        IMG_1234  -> IMG_1234 (不变)
    """
    return _IMG_E_PREFIX.sub(r"\1\2", stem)
