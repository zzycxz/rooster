"""Schedule execution wrapper — invoked by schtasks/launchd instead of the target script directly.

Usage: python schedule_runner.py <schedule_id>

This wrapper:
1. Reads .rooster/schedules.json to find the task config
2. Records a pending→running entry in .rooster/schedule_history.json
3. Performs variable interpolation
4. Runs the target script with timeout
5. Records the result (success/failure/timeout)
6. Triggers notifications if configured
7. Cleans up old history entries
"""

import os
import sys
import json
import time
import subprocess
import logging
import urllib.request
import urllib.error
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [schedule_runner] %(message)s")
logger = logging.getLogger(__name__)

# Project root: scripts/schedule_runner.py → project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, ".rooster")
SCHEDULES_PATH = os.path.join(DATA_DIR, "schedules.json")
HISTORY_PATH = os.path.join(DATA_DIR, "schedule_history.json")


def read_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default if default is not None else []


def atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def append_history(entry):
    history = read_json(HISTORY_PATH, [])
    history.append(entry)
    atomic_write_json(HISTORY_PATH, history)


def update_schedule_status(schedule_id, status, started_at):
    schedules = read_json(SCHEDULES_PATH, [])
    for s in schedules:
        if s.get("id") == schedule_id:
            s["last_status"] = status
            s["last_run_at"] = started_at
            break
    atomic_write_json(SCHEDULES_PATH, schedules)


def cleanup_history(schedule_id, retention_days):
    """Remove history entries older than retention_days for this schedule."""
    history = read_json(HISTORY_PATH, [])
    cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
    original_len = len(history)
    history = [h for h in history
               if h.get("schedule_id") != schedule_id
               or h.get("started_at", "") >= cutoff]
    if len(history) < original_len:
        atomic_write_json(HISTORY_PATH, history)


def interpolate_vars(text, task_vars=None):
    """Replace {{now}}, {{date}}, {{last_output}} in strings."""
    if not isinstance(text, str):
        return text
    now = datetime.now()
    text = text.replace("{{now}}", now.isoformat())
    text = text.replace("{{date}}", now.strftime("%Y-%m-%d"))
    text = text.replace("{{time}}", now.strftime("%H:%M:%S"))
    if task_vars:
        for k, v in task_vars.items():
            text = text.replace("{{" + k + "}}", str(v))
    return text


def send_feishu_notification(webhook_url, title, content):
    """Send a notification to Feishu via webhook."""
    try:
        payload = json.dumps({
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "markdown", "content": content}],
            }
        }).encode("utf-8")
        req = urllib.request.Request(
            webhook_url, data=payload,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info(f"Feishu notification sent: {resp.status}")
    except Exception as e:
        logger.warning(f"Failed to send Feishu notification: {e}")


def trigger_downstream_task(target_task_id):
    """Trigger a downstream scheduled task by invoking schedule_runner recursively."""
    try:
        subprocess.run(
            [sys.executable, __file__, target_task_id],
            capture_output=True, text=True, timeout=60
        )
        logger.info(f"Triggered downstream task: {target_task_id}")
    except Exception as e:
        logger.warning(f"Failed to trigger downstream task {target_task_id}: {e}")


def check_notification_condition(notification, consecutive_failures):
    """Check if a notification should fire based on its trigger condition."""
    condition = notification.get("trigger_condition", "on_failure")
    trigger_count = notification.get("trigger_count", 1)

    if condition == "on_success":
        return True  # Caller checks actual status
    elif condition == "on_failure":
        return True
    elif condition == "on_n_failures":
        return consecutive_failures >= trigger_count
    return False


def run(schedule_id):
    if not os.path.exists(SCHEDULES_PATH):
        logger.error(f"schedules.json not found at {SCHEDULES_PATH}")
        sys.exit(1)

    schedules = read_json(SCHEDULES_PATH, [])
    entry = None
    for s in schedules:
        if s.get("id") == schedule_id:
            entry = s
            break

    if not entry:
        logger.error(f"Schedule {schedule_id} not found")
        sys.exit(1)

    # Ensure defaults
    entry.setdefault("execution", {})
    entry.setdefault("variables", {"global": {}, "task": {}})
    entry.setdefault("timeout_sec", 3600)
    entry.setdefault("history_retention_days", 30)
    entry.setdefault("notifications", [])

    exec_config = entry["execution"]
    script_path = exec_config.get("script_path", "")

    if not script_path:
        logger.error(f"No script_path for schedule {schedule_id}")
        sys.exit(1)

    # Resolve script path
    if not os.path.isabs(script_path):
        resolved = os.path.normpath(os.path.join(PROJECT_ROOT, script_path))
    else:
        resolved = script_path

    if not os.path.exists(resolved):
        logger.error(f"Script not found: {resolved}")
        sys.exit(1)

    # Variable interpolation
    env_vars = {**os.environ}
    env_vars.update(exec_config.get("env_vars", {}))
    task_vars = entry.get("variables", {}).get("task", {})
    for k, v in task_vars.items():
        env_vars[k] = str(interpolate_vars(v, task_vars))

    args = [interpolate_vars(a, task_vars) for a in exec_config.get("args", [])]
    timeout = entry.get("timeout_sec", 3600) or 3600
    run_id = f"run_{schedule_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    start = time.time()
    start_iso = datetime.fromtimestamp(start).isoformat()

    # Run the script
    status = "success"
    stdout = ""
    stderr = ""
    exit_code = 0

    try:
        result = subprocess.run(
            [sys.executable, resolved] + args,
            capture_output=True, text=True,
            timeout=timeout,
            env=env_vars,
            cwd=exec_config.get("working_dir") or PROJECT_ROOT,
        )
        exit_code = result.returncode
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        status = "success" if exit_code == 0 else "failure"
    except subprocess.TimeoutExpired:
        status = "timeout"
        exit_code = -1
        stderr = f"Timeout after {timeout}s"
    except Exception as e:
        status = "failure"
        exit_code = -1
        stderr = str(e)

    duration = round(time.time() - start, 3)
    end_iso = datetime.now().isoformat()

    # Record history
    history_entry = {
        "run_id": run_id,
        "schedule_id": schedule_id,
        "status": status,
        "started_at": start_iso,
        "finished_at": end_iso,
        "duration_sec": duration,
        "exit_code": exit_code,
        "stdout": stdout[-5000:] if stdout else "",
        "stderr": stderr[-5000:] if stderr else "",
        "error_stack": stderr[-2000:] if stderr and status != "success" else None,
        "retry_attempt": 0,
    }
    append_history(history_entry)
    update_schedule_status(schedule_id, status, start_iso)

    # Cleanup old history
    retention = entry.get("history_retention_days", 30)
    cleanup_history(schedule_id, retention)

    # Notifications
    notifications = entry.get("notifications", [])
    if notifications:
        # Count consecutive failures
        history = read_json(HISTORY_PATH, [])
        consecutive = 0
        for h in reversed(history):
            if h.get("schedule_id") == schedule_id:
                if h.get("status") in ("failure", "timeout"):
                    consecutive += 1
                else:
                    break

        is_success = status == "success"
        task_name = entry.get("name", schedule_id)

        for notif in notifications:
            if not notif.get("enabled", True):
                continue

            ntype = notif.get("type", "")
            condition = notif.get("trigger_condition", "on_failure")

            should_notify = False
            if condition == "on_success" and is_success:
                should_notify = True
            elif condition == "on_failure" and not is_success:
                should_notify = True
            elif condition == "on_n_failures":
                should_notify = consecutive >= notif.get("trigger_count", 1)

            if not should_notify:
                continue

            if ntype == "feishu":
                webhook_url = notif.get("webhook_url", "")
                if webhook_url:
                    title = f"{'✅' if is_success else '❌'} 定时任务: {task_name}"
                    content = f"**状态**: {status}\n**耗时**: {duration}s\n**退出码**: {exit_code}"
                    if not is_success and stderr:
                        content += f"\n**错误**:\n```\n{stderr[:1000]}\n```"
                    send_feishu_notification(webhook_url, title, content)

            elif ntype == "downstream_task":
                target_id = notif.get("target_task_id", "")
                if target_id and is_success:
                    trigger_downstream_task(target_id)

    logger.info(f"Task {schedule_id} ({task_name}): {status} in {duration}s (exit={exit_code})")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python schedule_runner.py <schedule_id>")
        sys.exit(1)
    run(sys.argv[1])
