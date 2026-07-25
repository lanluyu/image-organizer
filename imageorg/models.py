# coding: utf-8
"""运行配置与计数器数据结构。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Stats:
    """汇总计数器: 末尾打印用"""
    scanned: int = 0       # 扫描到的文件总数 (含 skipped)
    moved: int = 0         # 成功归档
    duplicates: int = 0    # 字节重复，移到 duplicates 目录
    visual_duplicates: int = 0  # pHash 视觉重复
    sidecars_paired: int = 0    # AAE sidecar 跟随主文件
    livephotos_paired: int = 0  # Live Photo 配对成功
    skipped: int = 0       # 不支持的扩展名
    resumed: int = 0       # 断点续跑跳过的文件
    date_from_mtime: int = 0  # 无拍摄时间，按文件系统时间归档 (归档年份可能不准)
    failed: int = 0        # 业务失败 (例如完全读不到时间)
    errored: int = 0       # 脚本异常


@dataclass
class FailureRecord:
    """失败记录: 末尾汇总打印，定位问题用"""
    path: str
    kind: str          # "FAIL" | "ERROR"
    message: str       # 形如 "ValueError: invalid date"


@dataclass
class Config:
    """运行配置: 由 argparse 填充"""
    source_dir: Path
    target_dir: Path
    duplicate_dir: Path
    exiftool_path: Optional[Path] = None
    dry_run: bool = False
    use_phash: bool = False        # 启用感知哈希视觉去重
    phash_threshold: int = 4       # pHash 汉明距离阈值 (≤ 视为同图)
    workers: int = 4               # 并发 MD5 worker 数
    block_size: int = 1024 * 1024  # 哈希读取块大小
    no_resume: bool = False        # 禁用断点续跑
