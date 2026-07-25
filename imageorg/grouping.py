# coding: utf-8
"""Live Photo / AAE sidecar 配对分组。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .constants import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, normalize_stem


@dataclass
class FileGroup:
    """
    一组应该一起移动的文件 (主文件 + 关联的 sidecar/配对文件)。

    iPhone 实况照片 (Live Photo) 是 IMG_xxxx.HEIC + IMG_xxxx.MOV 同名成对，
    单独按时间归档时，视频和图片的 Exif 时间常常差几秒，可能跨月份分到不同目录。
    AAE 编辑记录同理，必须跟原图同目录才有意义。

    分组策略: 按 (父目录, 主名 stem) 聚合，主文件 = 优先级最高的那一个。
    主文件优先级: 图片 > 视频 > sidecar
    """
    primary: Path            # 用此文件的时间决定归档目录
    companions: list[Path]   # 跟随移动的文件 (Live Photo 的另一半 + AAE)


def group_files(files: list[Path]) -> list[FileGroup]:
    """
    把扁平的文件列表分组为 FileGroup。

    例: [IMG_1.HEIC, IMG_1.MOV, IMG_1.AAE, IMG_2.JPG]
         → [Group(HEIC, [MOV, AAE]), Group(JPG, [])]
    """
    # key: (parent_dir, normalized_stem_lower) -> list[Path]
    buckets: dict[tuple[str, str], list[Path]] = {}
    for f in files:
        # 用绝对父路径 + 归一化小写 stem 做 key (Windows 大小写不敏感; IMG_E 与 IMG_ 视为同组)
        key = (str(f.parent), normalize_stem(f.stem).lower())
        buckets.setdefault(key, []).append(f)

    groups: list[FileGroup] = []
    for key, members in buckets.items():
        if len(members) == 1:
            groups.append(FileGroup(primary=members[0], companions=[]))
            continue

        # 多文件同 stem: 选优先级最高的当 primary
        # 图片 > 视频 > sidecar (sidecar 永远不会单独 primary)
        def priority(p: Path) -> int:
            ext = p.suffix.lower()
            if ext in IMAGE_EXTENSIONS:
                return 0
            if ext in VIDEO_EXTENSIONS:
                return 1
            return 2  # sidecar

        members_sorted = sorted(members, key=priority)
        primary = members_sorted[0]
        companions = members_sorted[1:]
        groups.append(FileGroup(primary=primary, companions=companions))

    return groups
