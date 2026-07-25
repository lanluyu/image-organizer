# coding: utf-8
"""多策略拍摄时间解析: ExifTool → Pillow → 文件 mtime。"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import exiftool
from .constants import DATE_KEYS, IMAGE_EXTENSIONS
from .exiftool import _normalize_path_key
from .imaging import Image


class DateResolver:
    """
    多策略读取媒体拍摄时间。

    优先级 (从高到低):
        1. ExifTool (覆盖图片/视频/HEIC/RAW，最权威)
        2. Pillow Exif (仅常见图片格式，备选)
        3. 文件 mtime (最后兜底)

    AAE 文件不在此处理 (作为 sidecar 跟随主文件，不独立归档)。
    """

    def __init__(self, exiftool_path: Optional[Path]) -> None:
        self.exiftool_path = exiftool_path if exiftool_path and exiftool_path.exists() else None
        self.logger = logging.getLogger("organizer.date")
        # 缓存: prewarm 阶段批量填充，resolve() 命中后零开销
        self._cache: dict[str, datetime] = {}
        # 持久 daemon: 懒启动 (prewarm 或首次未命中 resolve 时)
        self._daemon: Optional[exiftool.ExifToolDaemon] = None
        # daemon 已彻底失效 → 后续 resolve 直接跳过 ExifTool 走 Pillow / mtime
        self._daemon_disabled = False

    @staticmethod
    def _parse_exif_record(rec: dict) -> Optional[datetime]:
        """从 exiftool JSON 记录里按优先级提取第一个能解析的时间字段"""
        for key in DATE_KEYS:
            raw = rec.get(key)
            if not raw:
                continue
            try:
                return datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S")
            except ValueError:
                continue
        return None

    def prewarm(
        self,
        paths: list[Path],
        batch_size: int = 100,
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> None:
        """
        批量预查询 exiftool，结果填充到 self._cache。
        没有 exiftool 时直接 no-op；daemon 失败时跳过本批次，不影响后续 resolve fallback。

        should_abort: 批次间的中止钩子。prewarm 可能在后台线程里跑几分钟，
        用户 Ctrl+C 时必须能在一个批次 (~100 文件) 内退出，而不是拖住整个进程。
        """
        if not self.exiftool_path or not paths or self._daemon_disabled:
            return
        if self._daemon is None:
            try:
                self._daemon = exiftool.ExifToolDaemon(self.exiftool_path)
            except Exception as e:
                self.logger.warning("daemon 启动失败，降级到 Pillow/mtime: %s", e)
                self._daemon_disabled = True
                return

        t0 = time.time()
        hit = 0
        for i in range(0, len(paths), batch_size):
            if should_abort is not None and should_abort():
                self.logger.info("ExifTool prewarm 收到中止信号，已完成 %d 个", hit)
                return
            batch = paths[i:i + batch_size]
            try:
                records = self._daemon.query_batch(batch)
            except RuntimeError as e:
                self.logger.warning("daemon 批量查询失败 (本批 %d 文件降级): %s", len(batch), e)
                # 单批失败不全局禁用 daemon (可能只是单批文件触发的临时问题)
                continue
            for p in batch:
                rec = records.get(_normalize_path_key(p))
                if not rec:
                    continue
                dt = self._parse_exif_record(rec)
                if dt:
                    self._cache[_normalize_path_key(p)] = dt
                    hit += 1
        self.logger.info(
            "ExifTool prewarm: %d/%d 命中，耗时 %.1fs",
            hit, len(paths), time.time() - t0,
        )

    def resolve(self, file_path: Path) -> tuple[datetime, str]:
        """
        返回 (时间, 来源标签)。来源标签用于日志，便于排查归档时间是否可信。
        优先级: 缓存 → daemon 单文件查询 → Pillow → mtime
        """
        cached = self._cache.get(_normalize_path_key(file_path))
        if cached:
            return cached, "exiftool-cache"

        dt = self._from_exiftool(file_path)
        if dt:
            return dt, "exiftool"

        dt = self._from_pillow(file_path)
        if dt:
            return dt, "pillow"

        # 文件 mtime 兜底: 不抛异常，保证主流程不中断
        return datetime.fromtimestamp(file_path.stat().st_mtime), "mtime"

    def _from_exiftool(self, file_path: Path) -> Optional[datetime]:
        """单文件查询 (缓存未命中走这里)。复用 daemon 进程，daemon 不可用则返回 None"""
        if not self.exiftool_path or self._daemon_disabled:
            return None
        if self._daemon is None:
            try:
                self._daemon = exiftool.ExifToolDaemon(self.exiftool_path)
            except Exception as e:
                self.logger.debug("daemon 懒启动失败: %s", e)
                self._daemon_disabled = True
                return None

        try:
            records = self._daemon.query_batch([file_path])
        except RuntimeError as e:
            # daemon 已彻底坏掉 (重启次数超限) → 全局禁用避免每文件重试
            self.logger.warning("daemon 不可用，禁用 ExifTool 走 Pillow/mtime: %s", e)
            self._daemon_disabled = True
            return None

        rec = records.get(_normalize_path_key(file_path))
        if not rec:
            return None
        return self._parse_exif_record(rec)

    def close(self) -> None:
        """关闭 daemon 子进程。幂等。"""
        if self._daemon is not None:
            self._daemon.close()
            self._daemon = None

    def _from_pillow(self, file_path: Path) -> Optional[datetime]:
        """Pillow 备选: 仅支持常见图片，HEIC/RAW 等 Pillow 读不了"""
        if Image is None or file_path.suffix.lower() not in IMAGE_EXTENSIONS:
            return None
        try:
            with Image.open(file_path) as img:
                exif_data = getattr(img, "_getexif", lambda: None)()
            if not exif_data:
                return None
            # 36867 = DateTimeOriginal, 306 = DateTime
            raw = exif_data.get(36867) or exif_data.get(306)
            if not raw:
                return None
            return datetime.strptime(str(raw)[:19], "%Y:%m:%d %H:%M:%S")
        except Exception:
            return None
