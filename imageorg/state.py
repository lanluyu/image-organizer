# coding: utf-8
"""断点续跑状态的加载与原子写入。"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from .constants import STATE_FILENAME


STATE_VERSION = 3


def load_state(target_dir: Path) -> dict:
    """
    加载上次运行的状态，返回结构:
        {size_hashes: {size: set[hash]}, size_pending: {size: [归档后路径]},
         processed_paths: set[str]}

    size_pending 是 size 短路的欠账: 该 size 当时唯一，MD5 没算过。下次遇到同 size
    文件时必须先回补这些路径的哈希，否则跨次运行的重复文件会漏检。

    版本兼容:
        v1: {processed_hashes: [...], processed_paths: [...]}
            无 size 信息，丢弃 hash 历史，仅保留断点路径。
        v2: {version: 2, size_hashes: {...}, processed_paths: [...]}
            无 pending 记录 → 那批短路文件的哈希已永久缺失，只能按无欠账加载
            (行为等同旧版，不会更差)。
        v3: 增加 size_pending。
    """
    empty = {"size_hashes": {}, "size_pending": {}, "processed_paths": set()}
    state_path = target_dir / STATE_FILENAME
    if not state_path.exists():
        return empty
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        logging.warning("状态文件损坏，重新开始: %s", state_path)
        return empty

    paths = set(raw.get("processed_paths", []))
    version = raw.get("version")

    if version in (2, STATE_VERSION):
        size_hashes = {int(k): set(v) for k, v in raw.get("size_hashes", {}).items()}
        size_pending = {int(k): list(v) for k, v in raw.get("size_pending", {}).items()}
        if version == 2:
            logging.warning(
                "状态文件为 v2 格式: 早前 size 短路放行的文件未记录哈希，"
                "与它们重复的文件本次仍可能漏检 (本次起已开始记录)"
            )
        return {"size_hashes": size_hashes, "size_pending": size_pending,
                "processed_paths": paths}

    # v1 → v3: 丢弃 hash 历史 (无 size 信息无法支持 size 短路)
    if raw.get("processed_hashes"):
        logging.warning(
            "状态文件为旧版格式，已丢弃 %d 条 hash 历史 (本次起按 size+hash 重新累积)；"
            "断点续跑路径仍生效",
            len(raw["processed_hashes"]),
        )
    return {"size_hashes": {}, "size_pending": {}, "processed_paths": paths}


def save_state(
    target_dir: Path,
    size_hashes: dict[int, set[str]],
    size_pending: dict[int, list[str]],
    paths: set[str],
) -> None:
    """原子写入状态: 先写 .tmp 再 rename，避免中途 Ctrl+C 留下半截文件"""
    state_path = target_dir / STATE_FILENAME
    tmp_path = state_path.with_suffix(".tmp")
    payload = {
        "version": STATE_VERSION,
        "size_hashes": {str(sz): sorted(hs) for sz, hs in size_hashes.items()},
        "size_pending": {str(sz): ps for sz, ps in size_pending.items() if ps},
        "processed_paths": sorted(paths),
        "saved_at": datetime.now().isoformat(),
    }
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(state_path)
