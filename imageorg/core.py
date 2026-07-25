# coding: utf-8
"""主调度器: 扫描 → 分组 → 并发哈希 → 去重判断 → 归档/移重复。"""
from __future__ import annotations

import errno
import logging
import os
import shutil
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import dedup, imaging
from .constants import (HEARTBEAT_INTERVAL_SEC, SIDECAR_EXTENSIONS,
                        SUPPORTED_EXTENSIONS)
from .dates import DateResolver
from .dedup import PHashBKTree
from .grouping import FileGroup, group_files
from .models import Config, FailureRecord, Stats
from .state import load_state, save_state


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
        # size→[归档后路径]: size 短路放行但未算哈希的欠账，同 size 再次出现时回补
        self._size_pending: dict[int, list[str]] = {}
        # pHash BK-tree: 视觉去重的近邻搜索 (vs O(N²) 线性扫)
        self._phash_tree: PHashBKTree = PHashBKTree()
        # 已处理路径: 断点续跑用
        self._processed_paths: set[str] = set()
        self._last_heartbeat = time.time()
        # 中止信号: 主线程 Ctrl+C 后，后台 prewarm 线程据此在批次间退出
        self._abort = threading.Event()

    # ---------- 入口 ----------
    def run(self) -> int:
        """主流程，返回退出码"""
        # dry-run 是预演: 不得在磁盘上留下任何痕迹 (包括空目录)
        if not self.cfg.dry_run:
            self.cfg.target_dir.mkdir(parents=True, exist_ok=True)
            self.cfg.duplicate_dir.mkdir(parents=True, exist_ok=True)

        # 加载断点状态
        if not self.cfg.no_resume:
            state = load_state(self.cfg.target_dir)
            self._size_to_hashes = state["size_hashes"]
            self._size_pending = state["size_pending"]
            self._processed_paths = state["processed_paths"]
            if self._size_to_hashes or self._processed_paths:
                total_hashes = sum(len(v) for v in self._size_to_hashes.values())
                self.logger.info("断点续跑: 已加载 %d 个 size 桶 / %d 个历史哈希 / %d 条历史路径",
                                 len(self._size_to_hashes), total_hashes, len(self._processed_paths))

        if self.cfg.use_phash and not imaging.HEIC_SUPPORTED:
            self.logger.warning(
                "已启用 --phash，但缺少 pillow-heif: HEIC 文件无法解码，"
                "视觉去重对它们不生效 (iPhone 图库主体是 HEIC)。安装: pip install pillow-heif"
            )

        # Ctrl+C 时存盘后再退出
        signal.signal(signal.SIGINT, self._on_sigint)

        try:
            return self._run_pipeline()
        except KeyboardInterrupt:
            # Ctrl+C 可能落在任何阶段 (扫描/MD5/pHash/预热/主循环)，统一在这里收口。
            # KeyboardInterrupt 不是 Exception 子类，若不在此接住会穿透 main()。
            self._flush_state()
            self.logger.warning("用户中断，状态已保存，可下次续跑")
            return 130
        finally:
            # 任何路径 (正常返回 / Ctrl+C / 顶层异常) 都要关闭 daemon 子进程，避免残留
            self.date_resolver.close()

    def _run_pipeline(self) -> int:
        """扫描 → 分组 → 并发哈希 → 去重 → 归档。中断与清理由 run() 统一处理。"""
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

        # 5. size 短路 + 前置计算。
        #    ExifTool 预热丢到后台线程，与 MD5/pHash 的线程池重叠 —— 前者是常驻 Perl
        #    进程的 stdin/stdout 往返，后者是磁盘 IO，串行跑等于白等一段。
        primary_sizes = self._stat_sizes(groups)
        need_hash = self._select_hash_candidates(groups, primary_sizes)

        prewarm_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="prewarm")
        try:
            prewarm_future = prewarm_pool.submit(self._prewarm_dates, groups)
            primary_hashes = self._parallel_hash([g.primary for g in need_hash])
            primary_phashes = self._parallel_phash(groups)
            prewarm_future.result()  # 传播后台线程里的异常
        finally:
            # wait=False + cancel_futures: Ctrl+C 时不阻塞在预热上，
            # prewarm 自己会在批次间看到 _abort 退出
            prewarm_pool.shutdown(wait=False, cancel_futures=True)

        # 6. 顺序处理每组 (移动操作必须串行，避免目标路径冲突)
        for idx, group in enumerate(groups, 1):
            self._heartbeat(idx, len(groups))
            try:
                key = str(group.primary)
                self._process_group(
                    group,
                    size=primary_sizes.get(key),
                    file_hash=primary_hashes.get(key),   # None = size 唯一，无需 hash 检查
                    phash_hex=primary_phashes.get(key),  # None = 未预算，"" = 算过但无 pHash
                )
            except Exception as e:
                # 顶层兜底: 单组失败记下来继续下一组，不让整个批量挂掉
                # (KeyboardInterrupt 是 BaseException，不会被这里吞掉)
                self.stats.errored += 1
                self.failures.append(FailureRecord(
                    path=str(group.primary), kind="ERROR",
                    message=f"{type(e).__name__}: {e}",
                ))
                self.logger.error("处理组失败 %s: %s", group.primary.name, e)

        # 7. 收尾: 存盘 + 打印汇总
        self._flush_state()
        self._print_summary()

        # 8. 退出码: 0 全成功 / 1 业务失败 / 2 脚本异常
        if self.stats.errored:
            return 2
        if self.stats.failed:
            return 1
        return 0

    def _prewarm_dates(self, groups: list[FileGroup]) -> None:
        """后台线程入口: 批量预热 ExifTool 时间缓存"""
        if not self.date_resolver.exiftool_path:
            return
        self.date_resolver.prewarm(
            [g.primary for g in groups], should_abort=self._abort.is_set,
        )

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
            - size 在历史 size_to_hashes 或 size_pending 中出现过 → 必算
            - 当前批次同 size 出现 ≥ 2 次 → 必算 (可能批次内互相重复)
            - 否则 size 唯一 → 跳过 hash 计算

        断点续跑场景: size_to_hashes / size_pending 由 load_state 复原，跨次去重保留。
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
            if sz in self._size_to_hashes or sz in self._size_pending or batch_sizes[sz] > 1:
                candidates.append(g)

        skipped = len(groups) - len(candidates)
        if skipped:
            self.logger.info(
                "size 短路: %d/%d 组 size 唯一，跳过 MD5 计算",
                skipped, len(groups),
            )
        return candidates

    def _parallel_map(
        self, fn: Callable[[Path], Optional[str]], paths: list[Path], label: str,
    ) -> dict[str, str]:
        """
        用线程池对一批文件并发求值，返回 {str(path): 结果}。

        fn 返回 None 或抛异常时该文件不进 dict，由调用方决定语义。
        shutdown(wait=False, cancel_futures=True): Ctrl+C 时立刻取消排队任务，
        只等在跑的那几个 —— 否则几千个已排队的任务会拖住退出。
        """
        if not paths:
            return {}
        result: dict[str, str] = {}
        self.logger.info("开始并发计算 %d 个文件的 %s (workers=%d)",
                         len(paths), label, self.cfg.workers)
        t0 = time.time()
        pool = ThreadPoolExecutor(max_workers=self.cfg.workers)
        try:
            futures = {pool.submit(fn, p): p for p in paths}
            done = 0
            for fut in as_completed(futures):
                p = futures[fut]
                done += 1
                try:
                    value = fut.result()
                    if value is not None:
                        result[str(p)] = value
                except Exception as e:
                    self.logger.warning("%s 失败 %s: %s", label, p.name, e)
                if done % 100 == 0:
                    self.logger.info("  %s 进度 %d/%d", label, done, len(paths))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        self.logger.info("%s 完成，耗时 %.1fs", label, time.time() - t0)
        return result

    def _parallel_hash(self, paths: list[Path]) -> dict[str, str]:
        """并发计算 MD5。单个文件失败时不进 dict (后续按需重算或视为新文件)。"""
        return self._parallel_map(
            lambda p: dedup.md5_of(p, self.cfg.block_size), paths, "MD5",
        )

    def _parallel_phash(self, groups: list[FileGroup]) -> dict[str, str]:
        """
        并发计算 pHash。图像解码比 MD5 昂贵得多，串行会让 --phash 成为整轮的瓶颈。

        算不出 pHash (非图片 / 解码失败) 的文件记 ""，与"没预算过"的 None 区分开，
        避免 _process_group 再徒劳地解码一次。
        """
        if not self.cfg.use_phash:
            return {}
        paths = [g.primary for g in groups]
        computed = self._parallel_map(lambda p: dedup.phash_of(p) or "", paths, "pHash")
        return {str(p): computed.get(str(p), "") for p in paths}

    def _process_group(
        self, group: FileGroup, size: Optional[int], file_hash: Optional[str],
        phash_hex: Optional[str] = None,
    ) -> None:
        """
        处理一个文件组: primary + companions 一起移动到同一目标目录。

        参数:
            size: primary 文件大小 (None 表示 stat 失败，会现场重试)
            file_hash: primary 的 MD5。None 有两种语义:
                - size 唯一且未在历史中出现 → 不需要 hash 检查 (size 短路)
                - 并发 hash 阶段失败 → 现场补算
            phash_hex: primary 的 pHash。None = 未预先计算，现场算；
                "" = 预算过但算不出 (非图片/解码失败)，不再重试
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
        # 区分依据: size 是否在历史 (_size_to_hashes / _size_pending) 中出现过。
        if file_hash is None and (size in self._size_to_hashes or size in self._size_pending):
            try:
                file_hash = dedup.md5_of(primary, self.cfg.block_size)
            except Exception as e:
                self.failures.append(FailureRecord(
                    path=str(primary), kind="ERROR",
                    message=f"MD5 失败: {type(e).__name__}: {e}",
                ))
                self.stats.errored += 1
                return

        # 短路放行的文件没算过哈希，本组移动成功后要把目标路径记进 _size_pending
        defer_hash = file_hash is None

        if file_hash is not None:
            # 先把同 size 的历史欠账补齐，否则跨次运行的重复文件会漏检
            self._backfill_pending(size)
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
            ph_hex = dedup.phash_of(primary) if phash_hex is None else phash_hex
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

        if source == "mtime":
            self.stats.date_from_mtime += 1

        target_dir = self.cfg.target_dir / f"{dt.year:04d}" / f"{dt.month:02d}"
        primary_target = self._move(primary, target_dir)

        if pending_phash_int is not None and primary_target:
            self._phash_tree.add(pending_phash_int, str(primary_target))

        # size 短路欠账: 记下归档后的位置，同 size 文件下次出现时回补哈希
        if defer_hash and primary_target and not self.cfg.dry_run:
            self._size_pending.setdefault(size, []).append(str(primary_target))

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

    def _backfill_pending(self, size: int) -> None:
        """
        回补 size 短路欠下的哈希: 对该 size 下已归档但没算过 MD5 的文件补算。

        文件可能已被用户手工移走/删除 —— 那就没有参照物了，跳过并告警，
        不记为失败 (不是本次处理的文件，也不影响本次归档结果)。
        """
        paths = self._size_pending.pop(size, [])
        if not paths:
            return
        bucket = self._size_to_hashes.setdefault(size, set())
        for p in paths:
            try:
                bucket.add(dedup.md5_of(Path(p), self.cfg.block_size))
            except OSError as e:
                self.logger.warning("回补已归档文件哈希失败，跳过: %s (%s)", p, e)

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
        target = self._unique_path(target_dir, source.name)
        if self.cfg.dry_run:
            self.logger.info("[DRY-RUN] %s -> %s", source, target)
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
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
            os.replace(str(source), str(target))  # 同卷原子
            return
        except OSError as e:
            # 只有"跨卷"才退到两阶段。权限拒绝、目标被占用等同样是 OSError，
            # 一并当跨卷处理会把本该失败的移动变成"复制一份再删源"。
            # Windows 上跨卷可能只带 winerror 17 (ERROR_NOT_SAME_DEVICE)。
            if e.errno != errno.EXDEV and getattr(e, "winerror", None) != 17:
                raise

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
            save_state(self.cfg.target_dir, self._size_to_hashes,
                       self._size_pending, self._processed_paths)
        except Exception as e:
            self.logger.warning("存盘失败: %s", e)

    def _on_sigint(self, signum, frame) -> None:
        """Ctrl+C: 置中止标志 (供后台 prewarm 线程感知) 并抛出，让外层在干净点位退出"""
        self._abort.set()
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

        if s.date_from_mtime:
            self.logger.warning(
                "%d 个文件没有 EXIF 拍摄时间，已按文件系统修改时间归档 —— "
                "若这些文件是拷贝得来的，归档年份可能不是真实拍摄年份",
                s.date_from_mtime,
            )

        if self.failures:
            self.logger.warning("失败清单 (共 %d 条):", len(self.failures))
            for f in self.failures:
                self.logger.warning("  [%s] %s -> %s", f.kind, f.path, f.message)
