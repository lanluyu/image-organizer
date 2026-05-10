# coding: utf-8
"""ExifToolDaemon: -stay_open 协议封装的单元测试"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from organizer import ExifToolDaemon, _normalize_path_key


def _make_proc_mock(readline_lines, poll_returns=None):
    """构造一个 fake subprocess.Popen 对象。
    readline_lines: stdout.readline 依次返回的字符串
    poll_returns: poll() 依次返回的值；默认始终 None (存活)
    """
    proc = MagicMock()
    proc.pid = 12345
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.readline.side_effect = list(readline_lines)
    if poll_returns is None:
        proc.poll.return_value = None
    else:
        proc.poll.side_effect = list(poll_returns)
    return proc


class TestQueryBatchProtocol:
    def test_protocol_writes_args_and_execute(self, tmp_path):
        """验证 stdin 写入包含所有时间字段、文件路径，并以 -execute 收尾"""
        json_line = (
            '[{"SourceFile":"/x/a.jpg","EXIF:DateTimeOriginal":"2024:01:15 10:00:00"}]\n'
        )
        proc = _make_proc_mock([json_line, "{ready}\n"])

        with patch("organizer.subprocess.Popen", return_value=proc):
            d = ExifToolDaemon(Path("exiftool.exe"))
            result = d.query_batch([Path("/x/a.jpg")])

        # 全部 stdin 写入拼起来检查协议
        all_written = "".join(c.args[0] for c in proc.stdin.write.call_args_list)
        assert "-DateTimeOriginal\n" in all_written
        assert "-CreateDate\n" in all_written
        assert "-execute\n" in all_written
        assert all_written.endswith("-execute\n")
        # /x/a.jpg 应作为参数出现
        assert "/x/a.jpg\n" in all_written or "\\x\\a.jpg\n" in all_written
        assert proc.stdin.flush.called

        # 返回结果以归一化 key 索引
        key = _normalize_path_key(Path("/x/a.jpg"))
        assert key in result
        assert result[key]["EXIF:DateTimeOriginal"] == "2024:01:15 10:00:00"

    def test_empty_paths_no_subprocess_call(self):
        """paths=[] 时不应启动子进程"""
        with patch("organizer.subprocess.Popen") as mock_popen:
            d = ExifToolDaemon(Path("exiftool.exe"))
            result = d.query_batch([])
        assert result == {}
        assert mock_popen.call_count == 0

    def test_eof_raises_runtime_error(self):
        """daemon 中途死掉 (stdout 返回空字符串 = EOF) 必须抛 RuntimeError"""
        proc = _make_proc_mock([""])  # 立刻 EOF
        proc.poll.return_value = 1  # 已死

        with patch("organizer.subprocess.Popen", return_value=proc):
            d = ExifToolDaemon(Path("exiftool.exe"))
            with pytest.raises(RuntimeError, match="EOF"):
                d.query_batch([Path("/x/a.jpg")])


class TestRestartLogic:
    def test_dead_process_triggers_restart(self):
        """proc.poll() 非 None → _ensure_alive 应重启 (Popen 调 2 次)"""
        # 第一个 proc: 立即在第二次调用时被发现已死
        json_line = '[{"SourceFile":"/x/a.jpg"}]\n'
        proc1 = _make_proc_mock([json_line, "{ready}\n"])
        proc2 = _make_proc_mock([json_line, "{ready}\n"])

        with patch("organizer.subprocess.Popen", side_effect=[proc1, proc2]) as mock_popen:
            d = ExifToolDaemon(Path("exiftool.exe"), max_restarts=3)
            d.query_batch([Path("/x/a.jpg")])  # 启动 proc1
            # 模拟 proc1 死了
            proc1.poll.return_value = 1
            d.query_batch([Path("/x/b.jpg")])  # 应触发重启 → proc2

        assert mock_popen.call_count == 2, "崩溃后必须重启"
        assert d._restart_count == 1

    def test_max_restart_exceeded_raises(self):
        """超过 max_restarts 次崩溃应抛 RuntimeError"""
        # 每个 proc 都立刻死: poll 永远返回 1
        def make_dead_proc():
            p = MagicMock()
            p.pid = 1
            p.poll.return_value = 1
            p.stdin = MagicMock()
            p.stdout = MagicMock()
            return p

        procs = [make_dead_proc() for _ in range(10)]

        with patch("organizer.subprocess.Popen", side_effect=procs):
            d = ExifToolDaemon(Path("exiftool.exe"), max_restarts=2)
            d._start()  # 手动启动 proc[0]
            # 现在 proc[0] 已死，下面 _ensure_alive 会重启
            d._ensure_alive()  # restart #1 → proc[1] (也是死的)
            d._ensure_alive()  # restart #2 → proc[2]
            with pytest.raises(RuntimeError, match="max restarts"):
                d._ensure_alive()  # restart #3 应被拒绝


class TestClose:
    def test_close_when_never_started_is_noop(self):
        """从未 _start 过，close 不应抛错"""
        d = ExifToolDaemon(Path("exiftool.exe"))
        d.close()
        d.close()  # 二次调用也 OK

    def test_close_sends_stay_open_false(self):
        """正常存活时 close 应写 -stay_open\\nFalse\\n 并 wait"""
        proc = _make_proc_mock([])
        proc.poll.return_value = None  # 存活
        proc.wait.return_value = 0

        with patch("organizer.subprocess.Popen", return_value=proc):
            d = ExifToolDaemon(Path("exiftool.exe"))
            d._start()
            d.close()

        all_written = "".join(c.args[0] for c in proc.stdin.write.call_args_list)
        assert "-stay_open\nFalse\n" in all_written
        assert proc.wait.called
        assert d.proc is None

    def test_close_already_dead_skips_stdin_write(self):
        """proc 已死 → close 不该尝试写 stdin"""
        proc = _make_proc_mock([])
        proc.poll.return_value = 0  # 已退出

        with patch("organizer.subprocess.Popen", return_value=proc):
            d = ExifToolDaemon(Path("exiftool.exe"))
            d._start()
            d.close()

        assert proc.stdin.write.call_count == 0
        assert d.proc is None


class TestNormalizeKey:
    def test_backslash_to_forward(self):
        assert _normalize_path_key(r"D:\x\a.jpg") == "d:/x/a.jpg"

    def test_already_forward(self):
        assert _normalize_path_key("D:/x/a.jpg") == "d:/x/a.jpg"

    def test_path_object(self):
        assert _normalize_path_key(Path("/x/A.JPG")).endswith("/x/a.jpg")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
