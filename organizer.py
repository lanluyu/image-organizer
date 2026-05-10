# coding: utf-8
"""
iPhone 照片/视频整理工具 (新版)
================================

核心功能:
    1. 按拍摄时间 (Exif/QuickTime) 归档到 年/月 子目录
    2. 字节级去重 (size 短路 + MD5)，可选感知哈希 (pHash) 视觉去重
    3. Live Photo (HEIC+MOV / JPG+MOV 同名) 与 AAE sidecar 跟随主文件
    4. 断点续跑 (.organizer_state.json) + 单文件局部容错 + 末尾失败清单
    5. 心跳输出 + dry-run 预演 + 并发 MD5 计算

用法:
    python organizer.py --source <源目录> --target <目标目录> --duplicates <重复目录>
    python organizer.py --source ... --target ... --duplicates ... --dry-run
    python organizer.py --source ... --target ... --duplicates ... --phash --workers 8

退出码:
    0 = 全部成功
    1 = 有 FAIL (业务上已知错误，例如读不到时间但已用降级方案)
    2 = 有 ERROR (脚本本身异常，需人工排查)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

try:
    from PIL import Image
except ImportError:
    Image = None  # PIL 是软依赖，没装也能跑，仅丧失 Pillow 时间降级与 pHash

try:
    import imagehash  # 软依赖，仅 --phash 时需要
except ImportError:
    imagehash = None


# ============================================================
# 常量定义
# ============================================================

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
    "File:FileModifyDate",     # 文件系统时间，最弱
    # 兜底：不带组名的字段 (ExifTool 版本差异)
    "DateTimeOriginal", "CreateDate", "CreationDate", "ModifyDate",
]

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


# ============================================================
# 数据结构
# ============================================================

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


# ============================================================
# ExifTool 持久进程 (-stay_open 协议)
# ============================================================

def _normalize_path_key(p) -> str:
    """统一路径 key: 正斜杠 + 小写。
    ExifToolDaemon 和 DateResolver 都用它，确保 daemon 返回的 SourceFile 与 resolve 查询命中。
    放模块级 (而非类方法) 是为了让 patch ExifToolDaemon 类的测试不会破坏归一化。
    """
    return str(Path(p)).replace("\\", "/").lower()


class ExifToolDaemon:
    """
    长连接 exiftool 进程，避免每文件 fork 的启动开销 (~200-500ms)。

    协议要点:
        - 启动: exiftool -stay_open True -@ -
        - 命令: stdin 每行一个参数，最后写 "-execute\\n" 触发执行
        - 终止符: stdout 读到 "{ready}\\n" 表示当前命令结束
        - 关闭: 写 "-stay_open\\nFalse\\n"，wait 超时则 terminate

    崩溃恢复: 进程意外退出时 _ensure_alive 自动重启，超过 max_restarts 抛错让调用方降级。
    """

    READY_SENTINEL = "{ready}"

    def __init__(self, exiftool_path: Path, max_restarts: int = 3) -> None:
        self.exiftool_path = exiftool_path
        self.max_restarts = max_restarts
        self.logger = logging.getLogger("organizer.daemon")
        self.proc: Optional[subprocess.Popen] = None
        self._restart_count = 0

    def _start(self) -> None:
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        self.proc = subprocess.Popen(
            [
                str(self.exiftool_path),
                "-stay_open", "True", "-@", "-",
                "-common_args", "-charset", "filename=utf8",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            # stderr 必须 DEVNULL: 诊断行 ("X image files read") 默认走 stderr，
            # 若合并到 stdout 会污染 JSON 解析。注意不能加 -q 静默 — -q 会连
            # {ready} sentinel 一起压制，导致 readline 永久阻塞。
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            startupinfo=startupinfo,
        )
        self.logger.debug("exiftool daemon started, pid=%s", self.proc.pid)

    def _ensure_alive(self) -> None:
        """进程未启动或已退出 → 启动；超过最大重启次数则抛错"""
        if self.proc and self.proc.poll() is None:
            return
        if self.proc is not None:
            if self._restart_count >= self.max_restarts:
                raise RuntimeError(
                    f"exiftool daemon dead and max restarts ({self.max_restarts}) exceeded"
                )
            self._restart_count += 1
            self.logger.warning(
                "exiftool daemon dead (exit=%s), restart #%d",
                self.proc.poll(), self._restart_count,
            )
        self._start()

    def _send_command(self, args: list[str]) -> str:
        """写命令 + flush，读 stdout 直到 {ready} sentinel。返回原始输出（不含 sentinel）"""
        self._ensure_alive()
        cmd_text = "\n".join(args) + "\n-execute\n"
        try:
            self.proc.stdin.write(cmd_text)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise RuntimeError(f"daemon stdin write failed: {e}") from e

        output_lines: list[str] = []
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"daemon stdout EOF, exit={self.proc.poll()}")
            if line.rstrip("\r\n") == self.READY_SENTINEL:
                break
            output_lines.append(line)
        return "".join(output_lines)

    def query_batch(self, paths: list[Path]) -> dict[str, dict]:
        """
        批量查询多个文件的时间字段。返回 {normalized_path_key: exif_record}。
        单文件解析失败不抛错，只是不进 dict（调用方可降级）。
        """
        if not paths:
            return {}
        args = [
            "-j", "-G",
            "-DateTimeOriginal", "-CreateDate", "-CreationDate",
            "-MediaCreateDate", "-TrackCreateDate", "-ContentCreateDate",
            "-ModifyDate", "-FileModifyDate",
        ]
        args.extend(str(p) for p in paths)

        output = self._send_command(args)
        if not output.strip():
            return {}
        try:
            records = json.loads(output)
        except json.JSONDecodeError as e:
            self.logger.warning("daemon JSON 解析失败: %s | head=%r", e, output[:200])
            return {}

        out: dict[str, dict] = {}
        for rec in records:
            src = rec.get("SourceFile")
            if src:
                out[_normalize_path_key(src)] = rec
        return out

    def close(self) -> None:
        """优雅关闭 daemon。幂等，多次调用安全。异常路径用 terminate/kill 兜底。"""
        if not self.proc:
            return
        if self.proc.poll() is not None:
            self.proc = None
            return
        try:
            self.proc.stdin.write("-stay_open\nFalse\n")
            self.proc.stdin.flush()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.logger.warning("exiftool daemon close 超时，terminate")
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def __enter__(self) -> "ExifToolDaemon":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ============================================================
# 时间解析器
# ============================================================

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
        self._daemon: Optional[ExifToolDaemon] = None
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

    def prewarm(self, paths: list[Path], batch_size: int = 100) -> None:
        """
        批量预查询 exiftool，结果填充到 self._cache。
        没有 exiftool 时直接 no-op；daemon 失败时跳过本批次，不影响后续 resolve fallback。
        """
        if not self.exiftool_path or not paths or self._daemon_disabled:
            return
        if self._daemon is None:
            try:
                self._daemon = ExifToolDaemon(self.exiftool_path)
            except Exception as e:
                self.logger.warning("daemon 启动失败，降级到 Pillow/mtime: %s", e)
                self._daemon_disabled = True
                return

        t0 = time.time()
        hit = 0
        for i in range(0, len(paths), batch_size):
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
                self._daemon = ExifToolDaemon(self.exiftool_path)
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


# ============================================================
# 文件分组: Live Photo / AAE sidecar 配对
# ============================================================

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


# ============================================================
# 哈希 / 去重
# ============================================================

def md5_of(path: Path, block_size: int = 1024 * 1024) -> str:
    """计算文件 MD5 (分块读取避免大文件爆内存)"""
    h = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(block_size), b""):
            h.update(block)
    return h.hexdigest()


def phash_of(path: Path) -> Optional[str]:
    """
    感知哈希 (pHash): 同一张照片不同压缩/分辨率会得到接近的哈希。
    返回 None 表示当前文件不适用 (非图片/Pillow 打不开)。
    """
    if imagehash is None or Image is None:
        return None
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return None
    try:
        with Image.open(path) as img:
            return str(imagehash.phash(img))
    except Exception:
        return None


def hamming(a: str, b: str) -> int:
    """两个十六进制 pHash 字符串的汉明距离"""
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 999  # 解析失败视为完全不同


class PHashBKTree:
    """
    汉明距离度量空间的 BK-tree，用于 pHash 视觉去重的近邻搜索。

    旧实现是 O(N²) 线性扫描 (每张新图 hamming 比对所有历史)，1 万张照片需要 ~5000 万次。
    BK-tree 利用三角不等式 |d(x,p) - d(y,p)| ≤ d(x,y) ≤ d(x,p) + d(y,p) 剪枝，
    实际查询接近 O(log N)，对小阈值 (≤4) 加速尤其明显。

    每个节点是 [hash_int, value, children_dict]，children 按"到父节点的距离"分桶。
    用 list 而非 tuple 是为了原地变更 children，避免重建。
    """

    def __init__(self) -> None:
        self.root: Optional[list] = None
        self._size = 0

    def __len__(self) -> int:
        return self._size

    @staticmethod
    def _dist(a: int, b: int) -> int:
        return bin(a ^ b).count("1")

    def add(self, hash_int: int, value) -> None:
        self._size += 1
        if self.root is None:
            self.root = [hash_int, value, {}]
            return
        node = self.root
        while True:
            d = self._dist(hash_int, node[0])
            child = node[2].get(d)
            if child is None:
                node[2][d] = [hash_int, value, {}]
                return
            node = child

    def query(self, hash_int: int, max_dist: int) -> list[tuple[int, object]]:
        """返回所有距离 ≤ max_dist 的 (距离, value)，按距离升序"""
        if self.root is None:
            return []
        results: list[tuple[int, object]] = []
        stack = [self.root]
        while stack:
            node = stack.pop()
            d = self._dist(hash_int, node[0])
            if d <= max_dist:
                results.append((d, node[1]))
            # 三角不等式: 子节点到 node 的距离若不在 [d-max_dist, d+max_dist]，
            # 则不可能到 hash_int 距离 ≤ max_dist，整子树跳过
            lo, hi = d - max_dist, d + max_dist
            for child_d, child in node[2].items():
                if lo <= child_d <= hi:
                    stack.append(child)
        results.sort(key=lambda x: x[0])
        return results


# ============================================================
# 状态持久化 (断点续跑)
# ============================================================

STATE_VERSION = 2


def load_state(target_dir: Path) -> dict:
    """
    加载上次运行的状态，返回结构: {size_hashes: {size: set[hash]}, processed_paths: set[str]}。

    版本兼容:
        v1 (旧): {processed_hashes: [hash, ...], processed_paths: [...]}
                 旧版没存 size，无法支持 size 短路；加载时丢弃 hash 历史并提示用户
                 一次重新累积，processed_paths 仍可用于断点续跑。
        v2 (新): {version: 2, size_hashes: {str(size): [hash, ...]}, processed_paths: [...]}
    """
    empty = {"size_hashes": {}, "processed_paths": set()}
    state_path = target_dir / STATE_FILENAME
    if not state_path.exists():
        return empty
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        logging.warning("状态文件损坏，重新开始: %s", state_path)
        return empty

    paths = set(raw.get("processed_paths", []))

    if raw.get("version") == STATE_VERSION:
        size_hashes = {int(k): set(v) for k, v in raw.get("size_hashes", {}).items()}
        return {"size_hashes": size_hashes, "processed_paths": paths}

    # v1 → v2: 丢弃 hash 历史 (无 size 信息无法支持 size 短路)
    if raw.get("processed_hashes"):
        logging.warning(
            "状态文件为旧版格式，已丢弃 %d 条 hash 历史 (本次起按 size+hash 重新累积)；"
            "断点续跑路径仍生效",
            len(raw["processed_hashes"]),
        )
    return {"size_hashes": {}, "processed_paths": paths}


def save_state(target_dir: Path, size_hashes: dict[int, set[str]], paths: set[str]) -> None:
    """原子写入状态: 先写 .tmp 再 rename，避免中途 Ctrl+C 留下半截文件"""
    state_path = target_dir / STATE_FILENAME
    tmp_path = state_path.with_suffix(".tmp")
    payload = {
        "version": STATE_VERSION,
        "size_hashes": {str(sz): sorted(hs) for sz, hs in size_hashes.items()},
        "processed_paths": sorted(paths),
        "saved_at": datetime.now().isoformat(),
    }
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(state_path)


# ============================================================
# 主流程
# ============================================================

class Organizer:
    """
    主调度器: 扫描 → 分组 → 并发哈希 → 去重判断 → 归档/移重复。

    每个 FileGroup 独立 try/except，单点失败不影响整体流程；
    失败收集到 self.failures，末尾统一打印。
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.logger = logging.getLogger("organizer")
        self.stats = Stats()
        self.failures: list[FailureRecord] = []
        self.date_resolver = DateResolver(cfg.exiftool_path)

        # 已见 size→{hash} 字典: 字节去重 (size 短路: size 唯一时无需算 hash)
        self._size_to_hashes: dict[int, set[str]] = {}
        # pHash BK-tree: 视觉去重的近邻搜索 (vs O(N²) 线性扫)
        self._phash_tree: PHashBKTree = PHashBKTree()
        # 已处理路径: 断点续跑用
        self._processed_paths: set[str] = set()
        self._last_heartbeat = time.time()

    # ---------- 入口 ----------
    def run(self) -> int:
        """主流程，返回退出码"""
        self.cfg.target_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.duplicate_dir.mkdir(parents=True, exist_ok=True)

        # 加载断点状态
        if not self.cfg.no_resume:
            state = load_state(self.cfg.target_dir)
            self._size_to_hashes = state["size_hashes"]
            self._processed_paths = state["processed_paths"]
            if self._size_to_hashes or self._processed_paths:
                total_hashes = sum(len(v) for v in self._size_to_hashes.values())
                self.logger.info("断点续跑: 已加载 %d 个 size 桶 / %d 个历史哈希 / %d 条历史路径",
                                 len(self._size_to_hashes), total_hashes, len(self._processed_paths))

        # Ctrl+C 时存盘后再退出
        signal.signal(signal.SIGINT, self._on_sigint)

        try:
            # 1. 扫描所有候选文件
            all_files = list(self._iter_files(self.cfg.source_dir))
            self.stats.scanned = len(all_files)
            self.logger.info("扫描到 %d 个文件", len(all_files))

            # 2. 按支持扩展过滤
            media_files = [f for f in all_files if f.suffix.lower() in SUPPORTED_EXTENSIONS]
            self.stats.skipped = len(all_files) - len(media_files)

            # 3. 断点续跑过滤
            if self._processed_paths:
                before = len(media_files)
                media_files = [f for f in media_files if str(f) not in self._processed_paths]
                self.stats.resumed = before - len(media_files)
                if self.stats.resumed:
                    self.logger.info("跳过 %d 个已处理文件 (断点续跑)", self.stats.resumed)

            # 4. Live Photo / AAE sidecar 分组
            groups = group_files(media_files)
            self.logger.info("分组后 %d 组待处理 (含 %d 个 sidecar/配对)",
                             len(groups), len(media_files) - len(groups))

            # 5. size 短路: 先按 size 分桶，只对 size 在历史中出现过 / 当前批次重复的桶算 MD5
            primary_sizes = self._stat_sizes(groups)
            need_hash = self._select_hash_candidates(groups, primary_sizes)
            primary_hashes = self._parallel_hash([g.primary for g in need_hash])

            # 6. ExifTool 持久进程批量预热: 一次喂 100 文件给常驻 daemon，命中后 resolve 零开销
            if self.date_resolver.exiftool_path:
                self.date_resolver.prewarm([g.primary for g in groups])

            # 7. 顺序处理每组 (移动操作必须串行，避免目标路径冲突)
            for idx, group in enumerate(groups, 1):
                self._heartbeat(idx, len(groups))
                try:
                    size = primary_sizes.get(str(group.primary))
                    file_hash = primary_hashes.get(str(group.primary))  # None = size 唯一，无需 hash 检查
                    self._process_group(group, size, file_hash)
                except KeyboardInterrupt:
                    # Ctrl+C: 保存状态后干净退出 (finally 块会 close daemon)
                    self._flush_state()
                    self.logger.warning("用户中断，状态已保存，可下次续跑")
                    return 130
                except Exception as e:
                    # 顶层兜底: 单组失败记下来继续下一组，不让整个批量挂掉
                    self.stats.errored += 1
                    self.failures.append(FailureRecord(
                        path=str(group.primary), kind="ERROR",
                        message=f"{type(e).__name__}: {e}",
                    ))
                    self.logger.error("处理组失败 %s: %s", group.primary.name, e)

            # 8. 收尾: 存盘 + 打印汇总
            self._flush_state()
            self._print_summary()

            # 9. 退出码: 0 全成功 / 1 业务失败 / 2 脚本异常
            if self.stats.errored:
                return 2
            if self.stats.failed:
                return 1
            return 0
        finally:
            # 任何路径 (正常返回 / Ctrl+C / 顶层异常) 都要关闭 daemon 子进程，避免残留
            self.date_resolver.close()

    # ---------- 内部实现 ----------
    def _iter_files(self, src: Path) -> Iterable[Path]:
        """递归遍历，跳过隐藏文件和目标/重复目录 (防止误把已归档的扫回来)"""
        target_resolved = self.cfg.target_dir.resolve()
        dup_resolved = self.cfg.duplicate_dir.resolve()
        for root, dirs, files in os.walk(src):
            root_path = Path(root).resolve()
            # 跳过目标目录子树
            if target_resolved in root_path.parents or root_path == target_resolved:
                dirs[:] = []
                continue
            if dup_resolved in root_path.parents or root_path == dup_resolved:
                dirs[:] = []
                continue
            for name in files:
                if name.startswith(".") or name.lower() in {"thumbs.db", "desktop.ini"}:
                    continue
                yield Path(root) / name

    def _stat_sizes(self, groups: list[FileGroup]) -> dict[str, int]:
        """一次性 stat 所有 primary，拿 size。stat 失败的文件不进 dict (后续 _process_group 兜底重试)。"""
        out: dict[str, int] = {}
        for g in groups:
            try:
                out[str(g.primary)] = g.primary.stat().st_size
            except OSError as e:
                self.logger.warning("stat 失败 %s: %s", g.primary.name, e)
        return out

    def _select_hash_candidates(
        self, groups: list[FileGroup], sizes: dict[str, int],
    ) -> list[FileGroup]:
        """
        size 短路核心: 只挑出真正需要算 MD5 的文件。
            - size 在历史 size_to_hashes 中出现过 → 必算 (可能与历史撞 hash)
            - 当前批次同 size 出现 ≥ 2 次 → 必算 (可能批次内互相重复)
            - 否则 size 唯一 → 跳过 hash 计算

        断点续跑场景: size_to_hashes 由 load_state 复原，跨次去重保留。
        """
        # 当前批次按 size 分桶
        batch_sizes: dict[int, int] = {}
        for g in groups:
            sz = sizes.get(str(g.primary))
            if sz is None:
                continue
            batch_sizes[sz] = batch_sizes.get(sz, 0) + 1

        candidates: list[FileGroup] = []
        for g in groups:
            sz = sizes.get(str(g.primary))
            if sz is None:
                # stat 失败 → 保守起见仍算 hash
                candidates.append(g)
                continue
            if sz in self._size_to_hashes or batch_sizes[sz] > 1:
                candidates.append(g)

        skipped = len(groups) - len(candidates)
        if skipped:
            self.logger.info(
                "size 短路: %d/%d 组 size 唯一，跳过 MD5 计算",
                skipped, len(groups),
            )
        return candidates

    def _parallel_hash(self, paths: list[Path]) -> dict[str, str]:
        """
        并发计算多个文件的 MD5。
        返回 {str(path): md5}，单个文件失败时不进 dict (后续按需重算或视为新文件)。
        """
        if not paths:
            return {}
        result: dict[str, str] = {}
        self.logger.info("开始并发计算 %d 个文件的 MD5 (workers=%d)", len(paths), self.cfg.workers)
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=self.cfg.workers) as pool:
            futures = {pool.submit(md5_of, p, self.cfg.block_size): p for p in paths}
            done = 0
            for fut in as_completed(futures):
                p = futures[fut]
                done += 1
                try:
                    result[str(p)] = fut.result()
                except Exception as e:
                    self.logger.warning("MD5 失败 %s: %s", p.name, e)
                # 每 100 个或 30 秒输出一次进度
                if done % 100 == 0:
                    self.logger.info("  MD5 进度 %d/%d", done, len(paths))
        self.logger.info("MD5 完成，耗时 %.1fs", time.time() - t0)
        return result

    def _process_group(
        self, group: FileGroup, size: Optional[int], file_hash: Optional[str],
    ) -> None:
        """
        处理一个文件组: primary + companions 一起移动到同一目标目录。

        参数:
            size: primary 文件大小 (None 表示 stat 失败，会现场重试)
            file_hash: primary 的 MD5。None 有两种语义:
                - size 唯一且未在历史中出现 → 不需要 hash 检查 (size 短路)
                - 并发 hash 阶段失败 → 现场补算
        """
        primary = group.primary

        # size 缺失 → 现场重试 stat
        if size is None:
            try:
                size = primary.stat().st_size
            except OSError as e:
                self.failures.append(FailureRecord(
                    path=str(primary), kind="ERROR",
                    message=f"stat 失败: {type(e).__name__}: {e}",
                ))
                self.stats.errored += 1
                return

        # ---------- 字节去重: size 短路 ----------
        # file_hash 为 None 有两种情况，必须区分:
        #   1. size 短路决策"无需算 hash" (size 唯一且历史无此 size) → 直接放行
        #   2. 并发 hash 阶段失败 → 必须现场补算
        # 区分依据: size 是否在 _size_to_hashes / 当前批次出现过。candidate 阶段已选过，
        # 所以这里如果 file_hash is None 而 size 在 _size_to_hashes 中 → 是阶段 2，需要补算。
        if file_hash is None and size in self._size_to_hashes:
            try:
                file_hash = md5_of(primary, self.cfg.block_size)
            except Exception as e:
                self.failures.append(FailureRecord(
                    path=str(primary), kind="ERROR",
                    message=f"MD5 失败: {type(e).__name__}: {e}",
                ))
                self.stats.errored += 1
                return

        if file_hash is not None:
            bucket = self._size_to_hashes.setdefault(size, set())
            if file_hash in bucket:
                self._move_group_to_duplicates(group)
                self.stats.duplicates += 1
                return
            bucket.add(file_hash)
        else:
            # size 短路放行: 标记 size 已见，下次同 size 会触发 hash 检查
            self._size_to_hashes.setdefault(size, set())

        # ---------- 视觉去重 (pHash, 可选) ----------
        # pending_phash_int: 本组待加入 BK-tree 的 hash (None 表示无 phash 或本组重复)
        # 用局部变量而非 instance attribute，避免组间状态泄漏
        pending_phash_int: Optional[int] = None
        if self.cfg.use_phash:
            ph_hex = phash_of(primary)
            if ph_hex:
                try:
                    ph_int = int(ph_hex, 16)
                except ValueError:
                    ph_int = None
                if ph_int is not None:
                    matches = self._phash_tree.query(ph_int, self.cfg.phash_threshold)
                    if matches:
                        # 取距离最近的命中作为参考路径
                        nearest_dist, nearest_target = matches[0]
                        self.logger.info(
                            "pHash 视觉重复 (dist=%d): %s ≈ %s",
                            nearest_dist, primary.name, nearest_target,
                        )
                        self._move_group_to_duplicates(group)
                        self.stats.visual_duplicates += 1
                        return
                    pending_phash_int = ph_int

        # ---------- 按时间归档 ----------
        try:
            dt, source = self.date_resolver.resolve(primary)
        except Exception as e:
            self.failures.append(FailureRecord(
                path=str(primary), kind="FAIL",
                message=f"读取时间失败: {type(e).__name__}: {e}",
            ))
            self.stats.failed += 1
            return

        target_dir = self.cfg.target_dir / f"{dt.year:04d}" / f"{dt.month:02d}"
        primary_target = self._move(primary, target_dir)

        if pending_phash_int is not None and primary_target:
            self._phash_tree.add(pending_phash_int, str(primary_target))

        # 移动伴随文件到同一目录
        for comp in group.companions:
            try:
                self._move(comp, target_dir)
                if comp.suffix.lower() in SIDECAR_EXTENSIONS:
                    self.stats.sidecars_paired += 1
                else:
                    self.stats.livephotos_paired += 1
            except Exception as e:
                self.failures.append(FailureRecord(
                    path=str(comp), kind="ERROR",
                    message=f"伴随文件移动失败: {type(e).__name__}: {e}",
                ))
                self.stats.errored += 1

        self.stats.moved += 1
        self.logger.debug("归档 [%s] %s -> %s", source, primary.name, target_dir)

    def _move_group_to_duplicates(self, group: FileGroup) -> None:
        """整组移到 duplicates (主文件 + 所有伴随文件)"""
        for f in [group.primary, *group.companions]:
            try:
                self._move(f, self.cfg.duplicate_dir)
            except Exception as e:
                self.failures.append(FailureRecord(
                    path=str(f), kind="ERROR",
                    message=f"移动到 duplicates 失败: {type(e).__name__}: {e}",
                ))
                self.stats.errored += 1

    def _move(self, source: Path, target_dir: Path) -> Optional[Path]:
        """
        实际移动文件，处理同名冲突 (追加 _1, _2)。
        dry-run 模式只打日志不动文件。返回目标路径 (dry-run 时也返回预期路径)。

        原子性保证:
            - 同卷: os.replace 是原子操作
            - 跨卷: 两阶段提交 — copy 到 target.partial → fsync → 原子 rename → 删源
                    任何阶段中断都不会出现"目标已成型 + 源未删"的污染状态
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        target = self._unique_path(target_dir, source.name)
        if self.cfg.dry_run:
            self.logger.info("[DRY-RUN] %s -> %s", source, target)
        else:
            self._atomic_move(source, target)
            self.logger.debug("MOVE %s -> %s", source.name, target)
        self._processed_paths.add(str(source))
        return target

    @staticmethod
    def _atomic_move(source: Path, target: Path) -> None:
        """
        原子移动 source → target。target 必须事先唯一 (调用方保证)。

        跨卷场景的失败模型:
            1. copy 到 .partial 中途崩 → 残留 .partial，源完好 → 下次扫描重做即可
            2. fsync 后、rename 前崩 → 同 1
            3. rename 后、unlink 前崩 → target 已成形 + 源残留 → 下次扫描会把残留源
               识别为字节重复 (hash 相同) 进 duplicates，不会丢数据
            4. 直接 shutil.move 跨卷崩在 copy 中途 → target 半成品 + 源完好 → 下次
               扫描会"成功"归档源，target 半成品永久残留 (旧实现的真正问题)
        """
        try:
            os.replace(str(source), str(target))  # 同卷原子；跨卷抛 OSError
            return
        except OSError:
            pass  # 跨卷，走两阶段

        partial = target.with_suffix(target.suffix + ".partial")
        try:
            shutil.copy2(str(source), str(partial))
            # 跨卷场景必须 fsync，否则 rename 后内容可能还在 page cache，断电丢数据。
            # Windows 要求可写句柄才能 fsync，所以用 r+b。fsync 失败不致命 (NTFS 有
            # 事务日志兜底)，记 warning 继续 rename。
            try:
                with open(partial, "r+b") as f:
                    os.fsync(f.fileno())
            except OSError as e:
                logging.getLogger("organizer").debug("fsync 跳过 (%s): %s", partial.name, e)
            os.replace(str(partial), str(target))
        except Exception:
            if partial.exists():
                try:
                    partial.unlink()
                except OSError:
                    pass
            raise

        try:
            os.unlink(str(source))
        except OSError as e:
            # target 已经原子就位，源没删干净是次要污染 (下次扫描会因 hash 重复进 duplicates)
            logging.getLogger("organizer").warning(
                "跨卷 move 后删源失败 (数据已就位): %s -> %s", source, e,
            )

    @staticmethod
    def _unique_path(directory: Path, filename: str) -> Path:
        """目标已存在同名文件时，追加 _1, _2 ... 直到唯一"""
        path = directory / filename
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        i = 1
        while True:
            candidate = directory / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                return candidate
            i += 1

    def _heartbeat(self, current: int, total: int) -> None:
        """定时心跳: 长跑必须有进度反馈，否则用户没法判断卡住还是在跑"""
        now = time.time()
        if now - self._last_heartbeat >= HEARTBEAT_INTERVAL_SEC:
            self.logger.info("心跳 %d/%d (%.1f%%)", current, total, 100.0 * current / total)
            self._last_heartbeat = now
            # 顺便存盘，避免突然断电丢全部进度
            self._flush_state()

    def _flush_state(self) -> None:
        """存盘断点状态 (dry-run 不存)"""
        if self.cfg.dry_run or self.cfg.no_resume:
            return
        try:
            save_state(self.cfg.target_dir, self._size_to_hashes, self._processed_paths)
        except Exception as e:
            self.logger.warning("存盘失败: %s", e)

    def _on_sigint(self, signum, frame) -> None:
        """Ctrl+C 时抛出 KeyboardInterrupt，让外层在干净点位退出"""
        raise KeyboardInterrupt()

    def _print_summary(self) -> None:
        """末尾汇总: 数字 + 失败清单"""
        s = self.stats
        self.logger.info("=" * 60)
        self.logger.info("处理完成%s", " (DRY-RUN 未实际移动)" if self.cfg.dry_run else "")
        self.logger.info("  扫描:       %d", s.scanned)
        self.logger.info("  归档成功:   %d", s.moved)
        self.logger.info("  字节重复:   %d", s.duplicates)
        if self.cfg.use_phash:
            self.logger.info("  视觉重复:   %d", s.visual_duplicates)
        self.logger.info("  AAE 跟随:   %d", s.sidecars_paired)
        self.logger.info("  Live配对:   %d", s.livephotos_paired)
        self.logger.info("  跳过(扩展): %d", s.skipped)
        self.logger.info("  跳过(续跑): %d", s.resumed)
        self.logger.info("  业务失败:   %d", s.failed)
        self.logger.info("  脚本异常:   %d", s.errored)
        self.logger.info("=" * 60)

        if self.failures:
            self.logger.warning("失败清单 (共 %d 条):", len(self.failures))
            for f in self.failures:
                self.logger.warning("  [%s] %s -> %s", f.kind, f.path, f.message)


# ============================================================
# CLI
# ============================================================

def detect_default_exiftool() -> Optional[Path]:
    """
    按优先级查找 exiftool.exe，支持两种官方部署方式:
        1. 项目根目录 ./exiftool.exe (官方 windows zip 解压后的 launcher，依赖在 ./exiftool_files/)
        2. 子目录 ./exiftool/exiftool.exe (旧约定/手工放置)
    """
    base = Path(__file__).parent
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


if __name__ == "__main__":
    sys.exit(main())
