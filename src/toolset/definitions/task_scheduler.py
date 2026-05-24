import os
import re
import sys
import platform
import subprocess
from typing import Type, Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool

_IS_WINDOWS = platform.system().lower() == "windows"
_IS_MACOS = platform.system().lower() == "darwin"


class TaskSchedulerCreateArgs(BaseModel):
    task_name: str = Field(description="任务名称，仅含字母/数字/下划线，如 RoosterDailyWeather")
    script_path: str = Field(
        description=(
            "要定时执行的 Python 脚本路径。支持：\n"
            "1. 绝对路径：C:/path/to/script.py\n"
            "2. 相对于项目根目录的路径：scripts/daily_weather.py\n"
            "3. 内置别名：'daily_weather'（自动指向项目内的天气推送脚本）"
        )
    )
    run_time: str = Field(description="触发时间，24 小时制 HH:MM，如 12:00")
    frequency: str = Field(default="DAILY", description="触发频率：DAILY（每天）| WEEKLY（每周）| ONCE（单次）")


class TaskSchedulerDeleteArgs(BaseModel):
    task_name: str = Field(description="要删除的任务名称")


def _safe_task_name(name: str) -> bool:
    """Only allow letters, digits, underscores, hyphens to prevent command injection.
    只允许字母、数字、下划线、连字符，防止命令注入。"""
    return bool(re.fullmatch(r"[\w\-]{1,100}", name))


def _safe_time(t: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", t))


def _resolve_script(script_path: str) -> str:
    """Path resolution: alias → relative path → absolute path.
    路径解析：别名 → 相对路径 → 绝对路径。"""
    # task_scheduler.py is at src/toolset/definitions/, so go up 4 levels to reach project root
    # task_scheduler.py 位于 src/toolset/definitions/，因此需要往上数 4 层到达根目录
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    if script_path in ("daily_weather", "weather", "daily_weather.py"):
        return os.path.join(project_root, "scripts", "daily_weather.py")
    elif not os.path.isabs(script_path):
        return os.path.normpath(os.path.join(project_root, script_path))
    return script_path


# --------------------------------------------------------------------------- #
#  Windows: schtasks
# --------------------------------------------------------------------------- #


def _create_windows(task_name: str, script_path: str, run_time: str, frequency: str) -> str:
    python_exe = sys.executable
    tr = f'"{python_exe}" "{script_path}"'
    cmd = [
        "schtasks",
        "/Create",
        "/F",
        "/TN",
        task_name,
        "/TR",
        tr,
        "/SC",
        frequency,
        "/ST",
        run_time,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return (
                f"✅ 定时任务 `{task_name}` 创建成功（Windows 任务计划程序）。\n"
                f"- 频率：{frequency}\n"
                f"- 触发时间：{run_time}\n"
                f"- 执行脚本：{script_path}\n"
                f"可在「任务计划程序」中搜索 `{task_name}` 查看或修改。"
            )
        else:
            return f"❌ schtasks 失败（code {result.returncode}）：{result.stderr.strip()}"
    except FileNotFoundError:
        return "❌ schtasks.exe 未找到，当前系统可能不是 Windows 或 PATH 异常。"
    except subprocess.TimeoutExpired:
        return "❌ schtasks 超时，任务可能未创建成功。"


def _delete_windows(task_name: str) -> str:
    cmd = ["schtasks", "/Delete", "/F", "/TN", task_name]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return f"✅ 任务 `{task_name}` 已删除。"
        else:
            return f"❌ 删除失败（code {result.returncode}）：{result.stderr.strip()}"
    except Exception as e:
        return f"❌ 删除任务时出错：{str(e)}"


# --------------------------------------------------------------------------- #
#  macOS: launchctl + plist
# --------------------------------------------------------------------------- #


def _plist_path(task_name: str) -> str:
    return os.path.expanduser(f"~/Library/LaunchAgents/com.rooster.{task_name}.plist")


def _create_macos(task_name: str, script_path: str, run_time: str, frequency: str) -> str:
    hour, minute = run_time.split(":")
    plist_file = _plist_path(task_name)
    os.makedirs(os.path.dirname(plist_file), exist_ok=True)

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    interval_block = f"""	<key>StartCalendarInterval</key>
	<dict>
		<key>Hour</key>
		<integer>{int(hour)}</integer>
		<key>Minute</key>
		<integer>{int(minute)}</integer>
	</dict>"""

    if frequency == "WEEKLY":
        # launchd does not directly support WEEKLY; fall back to daily trigger (user must check day of week in script)
        # launchd 不直接支持 WEEKLY，改用每天触发（用户需手动在脚本内判断星期）
        interval_block = f"""	<key>StartCalendarInterval</key>
	<dict>
		<key>Hour</key>
		<integer>{int(hour)}</integer>
		<key>Minute</key>
		<integer>{int(minute)}</integer>
	</dict>"""

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>com.rooster.{task_name}</string>
	<key>ProgramArguments</key>
	<array>
		<string>{sys.executable}</string>
		<string>{script_path}</string>
	</array>
	<key>WorkingDirectory</key>
	<string>{project_root}</string>
{interval_block}
	<key>StandardOutPath</key>
	<string>/tmp/rooster_{task_name}.log</string>
	<key>StandardErrorPath</key>
	<string>/tmp/rooster_{task_name}.err</string>
</dict>
</plist>
"""
    try:
        # Before writing, try silently unloading any existing task with the same name to prevent "service already loaded" errors
        # 写入前先尝试静默 unload 已经载入的同名任务，防止 "service already loaded" 报错
        subprocess.run(["launchctl", "unload", plist_file], capture_output=True, text=True, timeout=5)

        with open(plist_file, "w", encoding="utf-8") as f:
            f.write(plist_content)

        result = subprocess.run(
            ["launchctl", "load", plist_file],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return (
                f"✅ 定时任务 `{task_name}` 创建成功（macOS launchd）。\n"
                f"- 频率：{frequency}\n"
                f"- 触发时间：{run_time}\n"
                f"- 执行脚本：{script_path}\n"
                f"- plist：{plist_file}\n"
                f"日志：/tmp/rooster_{task_name}.log"
            )
        else:
            return f"❌ launchctl load 失败：{result.stderr.strip()}"
    except Exception as e:
        return f"❌ 创建 macOS 定时任务出错：{str(e)}"


def _delete_macos(task_name: str) -> str:
    plist_file = _plist_path(task_name)
    try:
        if os.path.exists(plist_file):
            subprocess.run(
                ["launchctl", "unload", plist_file],
                capture_output=True,
                text=True,
                timeout=15,
            )
            os.remove(plist_file)
            return f"✅ 任务 `{task_name}` 已删除（launchd plist 已移除）。"
        else:
            return f"❌ 未找到任务 `{task_name}` 的 plist 文件：{plist_file}"
    except Exception as e:
        return f"❌ 删除 macOS 任务出错：{str(e)}"


# --------------------------------------------------------------------------- #
#  Tools
# --------------------------------------------------------------------------- #


class TaskSchedulerTool(BaseTool):
    """
    创建定时任务。
    - Windows：调用 schtasks.exe（任务计划程序）
    - macOS：创建 launchd plist（~/Library/LaunchAgents/）
    """

    name: str = "task_scheduler_create"
    kit: str = "System"
    fc_hidden: bool = True  # [Round 10] Use task_scheduler(action="create") instead
    description: str = (
        "Create a scheduled task to run a Python script at a fixed time. "
        "Uses Windows Task Scheduler on Windows, launchd on macOS. "
        "Use this when the user asks to set up a recurring or one-time automatic task "
        "(e.g. 'send weather to Feishu every day at noon')."
    )
    domain: str = "system"
    platforms: list = ["Windows", "Darwin"]
    args_schema: Type[BaseModel] = TaskSchedulerCreateArgs

    async def run(self, **kwargs) -> str:
        task_name = kwargs.get("task_name", "").strip()
        script_path = kwargs.get("script_path", "").strip()
        run_time = kwargs.get("run_time", "").strip()
        frequency = kwargs.get("frequency", "DAILY").strip().upper()

        if not _safe_task_name(task_name):
            return "❌ 任务名称含非法字符，只允许字母/数字/下划线/连字符。"
        if not _safe_time(run_time):
            return "❌ 时间格式错误，请使用 HH:MM 格式，如 12:00。"
        if frequency not in ("DAILY", "WEEKLY", "ONCE"):
            frequency = "DAILY"

        script_path = _resolve_script(script_path)
        if not os.path.exists(script_path):
            return f"❌ 脚本不存在：{script_path}"

        if _IS_WINDOWS:
            return _create_windows(task_name, script_path, run_time, frequency)
        elif _IS_MACOS:
            return _create_macos(task_name, script_path, run_time, frequency)
        else:
            return "❌ 当前平台不支持定时任务（仅支持 Windows 和 macOS）。"


class TaskSchedulerDeleteTool(BaseTool):
    """
    删除定时任务。
    - Windows：从任务计划程序删除
    - macOS：unload 并移除 plist
    """

    name: str = "task_scheduler_delete"
    kit: str = "System"
    fc_hidden: bool = True  # [Round 10] Use task_scheduler(action="delete") instead
    description: str = (
        "Delete an existing scheduled task by name. Uses Windows Task Scheduler on Windows, launchd on macOS."
    )
    domain: str = "system"
    platforms: list = ["Windows", "Darwin"]
    args_schema: Type[BaseModel] = TaskSchedulerDeleteArgs

    async def run(self, **kwargs) -> str:
        task_name = kwargs.get("task_name", "").strip()
        if not _safe_task_name(task_name):
            return "❌ 任务名称含非法字符。"

        if _IS_WINDOWS:
            return _delete_windows(task_name)
        elif _IS_MACOS:
            return _delete_macos(task_name)
        else:
            return "❌ 当前平台不支持定时任务（仅支持 Windows 和 macOS）。"


# ---------------------------------------------------------------------------
# [Round 10] task_scheduler — unified scheduling macro
# Replaces: task_scheduler_create, task_scheduler_delete
# ---------------------------------------------------------------------------


class TaskSchedulerMacroArgs(BaseModel):
    action: str = Field(description="'create' to add a scheduled task, 'delete' to remove one")
    task_name: str = Field(description="任务名称，仅含字母/数字/下划线，如 RoosterDailyWeather")
    script_path: Optional[str] = Field(
        default=None, description=("[create] Python 脚本路径。支持绝对路径、相对路径或内置别名 'daily_weather'。")
    )
    run_time: Optional[str] = Field(default=None, description="[create] 触发时间，24 小时制 HH:MM，如 12:00")
    frequency: Optional[str] = Field(default="DAILY", description="[create] 触发频率：DAILY | WEEKLY | ONCE")


class TaskSchedulerMacroTool(BaseTool):
    """[Round 10] Unified task scheduler macro: create or delete scheduled tasks."""

    name: str = "task_scheduler"
    kit: str = "System"
    description: str = (
        "Manage scheduled tasks. Use action='create' to set up a Python script to run at a fixed time "
        "(Windows Task Scheduler / macOS launchd), or action='delete' to remove a task by name."
    )
    domain: str = "system"
    platforms: list = ["Windows", "Darwin"]
    args_schema: Type[BaseModel] = TaskSchedulerMacroArgs

    async def run(self, **kwargs) -> str:
        action = kwargs.get("action", "").lower()
        task_name = kwargs.get("task_name", "").strip()

        if not _safe_task_name(task_name):
            return "❌ 任务名称含非法字符，只允许字母/数字/下划线/连字符。"

        if action == "create":
            script_path = (kwargs.get("script_path") or "").strip()
            run_time = (kwargs.get("run_time") or "").strip()
            frequency = (kwargs.get("frequency") or "DAILY").strip().upper()

            if not _safe_time(run_time):
                return "❌ 时间格式错误，请使用 HH:MM 格式，如 12:00。"
            if frequency not in ("DAILY", "WEEKLY", "ONCE"):
                frequency = "DAILY"

            script_path = _resolve_script(script_path)
            if not os.path.exists(script_path):
                return f"❌ 脚本不存在：{script_path}"

            if _IS_WINDOWS:
                return _create_windows(task_name, script_path, run_time, frequency)
            elif _IS_MACOS:
                return _create_macos(task_name, script_path, run_time, frequency)
            else:
                return "❌ 当前平台不支持定时任务（仅支持 Windows 和 macOS）。"

        elif action == "delete":
            if _IS_WINDOWS:
                return _delete_windows(task_name)
            elif _IS_MACOS:
                return _delete_macos(task_name)
            else:
                return "❌ 当前平台不支持定时任务（仅支持 Windows 和 macOS）。"

        else:
            return f"❌ Unknown action '{action}'. Valid: 'create', 'delete'."
