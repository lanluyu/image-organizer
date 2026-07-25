# coding: utf-8
"""命令行入口。"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Optional

from .core import Organizer
from .imaging import imagehash
from .models import Config


def detect_default_exiftool() -> Optional[Path]:
    """
    按优先级查找 exiftool.exe，支持两种官方部署方式:
        1. 项目根目录 ./exiftool.exe (官方 windows zip 解压后的 launcher，依赖在 ./exiftool_files/)
        2. 子目录 ./exiftool/exiftool.exe (旧约定/手工放置)
    """
    base = Path(__file__).resolve().parent.parent
    for candidate in [base / "exiftool.exe", base / "exiftool" / "exiftool.exe"]:
        if candidate.exists():
            return candidate
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="iPhone 照片/视频整理工具: 按时间归档 + 去重 + 配对 sidecar",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--source", required=True, type=Path, help="源目录 (待整理)")
    p.add_argument("--target", required=True, type=Path, help="目标目录 (按 年/月 归档)")
    p.add_argument("--duplicates", required=True, type=Path, help="重复文件存放目录")
    p.add_argument("--exiftool", type=Path, default=detect_default_exiftool(),
                   help="exiftool.exe 路径 (默认: ./exiftool/exiftool.exe)")
    p.add_argument("--dry-run", action="store_true", help="预演模式，只打印不移动")
    p.add_argument("--phash", dest="use_phash", action="store_true",
                   help="启用感知哈希视觉去重 (需要 imagehash 库)")
    p.add_argument("--phash-threshold", type=int, default=4,
                   help="pHash 汉明距离阈值，越小越严格 (默认 4)")
    p.add_argument("--workers", type=int, default=4, help="并发哈希 worker 数 (默认 4)")
    p.add_argument("--no-resume", action="store_true", help="禁用断点续跑")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    if args.use_phash and imagehash is None:
        logging.error("--phash 需要 imagehash 库: pip install imagehash")
        return 2

    cfg = Config(
        source_dir=args.source,
        target_dir=args.target,
        duplicate_dir=args.duplicates,
        exiftool_path=args.exiftool,
        dry_run=args.dry_run,
        use_phash=args.use_phash,
        phash_threshold=args.phash_threshold,
        workers=args.workers,
        no_resume=args.no_resume,
    )

    if not cfg.source_dir.exists():
        logging.error("源目录不存在: %s", cfg.source_dir)
        return 2

    try:
        return Organizer(cfg).run()
    except Exception as e:
        # 顶层兜底: 任何意料外异常都先打 traceback 再退出，便于事后排查
        logging.exception("顶层异常: %s", e)
        return 2
