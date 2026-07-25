# coding: utf-8
"""可选图像依赖的统一入口。

Pillow / imagehash / pillow-heif 都是软依赖，缺任何一个都不该让主流程起不来。
HEIC_SUPPORTED 供上层判断是否要提醒用户 --phash 对 HEIC 无效。
"""
from __future__ import annotations


try:
    from PIL import Image
except ImportError:
    Image = None  # PIL 是软依赖，没装也能跑，仅丧失 Pillow 时间降级与 pHash

try:
    import imagehash  # 软依赖，仅 --phash 时需要
except ImportError:
    imagehash = None

# Pillow 本身不认 HEIC。iPhone 图库主体就是 HEIC，缺这个包会让 --phash 对绝大多数
# 文件静默失效 (phash_of 一律返回 None)，所以要显式记录支持状态并在启用时告警。
try:
    import pillow_heif

    if Image is not None:
        pillow_heif.register_heif_opener()
        HEIC_SUPPORTED = True
    else:
        HEIC_SUPPORTED = False
except ImportError:
    HEIC_SUPPORTED = False
