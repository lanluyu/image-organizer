# coding: utf-8
"""ExifTool -stay_open 持久进程封装。"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional


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
            "-ModifyDate",
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
