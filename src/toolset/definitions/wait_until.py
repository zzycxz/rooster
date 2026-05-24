import asyncio
import logging
import time
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool

logger = logging.getLogger(__name__)

# Shell command patterns that should never appear in a condition-check command
_DANGEROUS_SHELL_PATTERNS = (
    "rm -rf",
    "rm -r /",
    "mkfs",
    "dd if=",
    "> /dev",
    "| sh",
    "| bash",
    "| zsh",
    "| python",
    "curl |",
    "wget |",
    "chmod 777",
    "chown",
    ":(){ :|:& };:",  # fork bomb
)


class WaitUntilArgs(BaseModel):
    condition: str = Field(description="轮询条件类型: file_exists | window_visible | process_running | custom")
    target: str = Field(description="条件目标: 文件路径 | 窗口标题关键词 | 进程名 | 自定义 shell 命令")
    timeout: int = Field(60, description="最大等待秒数，0 表示无限等待")
    interval: float = Field(2.0, description="轮询间隔(秒)")


class WaitUntilTool(BaseTool):
    """条件轮询等待工具 — 等待文件出现、窗口可见、进程启动等"""

    name: str = "wait_until"
    kit: str = "System"
    fc_hidden: bool = True  # [Round 10] Use python_interpreter with time.sleep/asyncio polling instead
    description: str = (
        "Wait until a condition is met by polling at regular intervals. "
        "Conditions: file_exists (file appears on disk), window_visible (window title matches), "
        "process_running (process name matches), custom (shell command returns exit code 0)."
    )
    domain: str = "system"
    args_schema: Optional[type] = WaitUntilArgs

    async def run(self, **kwargs) -> str:
        condition = kwargs.get("condition")
        target = kwargs.get("target")
        timeout = kwargs.get("timeout", 60)
        interval = kwargs.get("interval", 2.0)

        if not condition or not target:
            return "Error: 'condition' and 'target' are required."

        if interval < 0.5:
            interval = 0.5

        start = time.time()
        elapsed = 0.0

        while True:
            try:
                met = await self._check(condition, target)
            except Exception as e:
                return f"Check error after {elapsed:.1f}s: {type(e).__name__}: {e}"

            if met:
                elapsed = time.time() - start
                return f"Condition met after {elapsed:.1f}s: {condition}({target})"

            elapsed = time.time() - start
            if timeout > 0 and elapsed > timeout:
                return f"Timeout after {timeout}s: condition not met. {condition}({target})"

            await asyncio.sleep(interval)

    async def _check(self, condition: str, target: str) -> bool:
        if condition == "file_exists":
            return Path(target).exists()

        elif condition == "window_visible":
            return await self._check_window(target)

        elif condition == "process_running":
            return await self._check_process(target)

        elif condition == "custom":
            return await self._check_command(target)

        return False

    async def _check_window(self, keyword: str) -> bool:
        import platform

        if platform.system() == "Darwin":
            return await self._check_window_macos(keyword)
        try:
            import pygetwindow as gw

            windows = gw.getAllWindows()
            kw = keyword.lower()
            return any(kw in (w.title or "").lower() for w in windows if w.title)
        except Exception:
            return await self._check_window_ctypes(keyword)

    async def _check_window_macos(self, keyword: str) -> bool:
        """macOS: use osascript to list window titles."""
        script = 'tell application "System Events" to get the name of every window of every process'
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript",
                "-e",
                script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                return False
            kw = keyword.lower()
            return kw in stdout.decode("utf-8", errors="replace").lower()
        except Exception:
            return False

    async def _check_window_ctypes(self, keyword: str) -> bool:
        import ctypes
        import platform

        if platform.system().lower() != "windows":
            return False
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found = []

        def enum_callback(hwnd, _):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if keyword.lower() in buf.value.lower():
                    found.append(True)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        return len(found) > 0

    async def _check_process(self, name: str) -> bool:
        try:
            import psutil

            kw = name.lower()
            for proc in psutil.process_iter(["name"]):
                try:
                    if kw in (proc.info["name"] or "").lower():
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return False
        except ImportError:
            return False

    async def _check_command(self, cmd: str) -> bool:
        # Safety: block commands with dangerous shell patterns
        cmd_lower = cmd.lower()
        for pattern in _DANGEROUS_SHELL_PATTERNS:
            if pattern in cmd_lower:
                logger.warning(f"[WaitUntil] Blocked dangerous shell command: {cmd[:100]}")
                return False

        logger.info(f"[WaitUntil] Executing shell check: {cmd[:200]}")
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception:
            return False
