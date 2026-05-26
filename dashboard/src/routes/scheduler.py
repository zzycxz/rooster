"""Scheduler API routes — scheduled tasks CRUD, execution history, monitoring dashboard."""

import os
import re
import json
import time
import uuid
import subprocess
import logging
import platform
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, Body, Query, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])

# ── wire ────────────────────────────────────────────────────────────────
_rooster_dir: str = ".rooster"
_project_root: Optional[str] = None


def wire(rooster_dir: str):
    global _rooster_dir, _project_root
    _rooster_dir = rooster_dir
    # dashboard/src/routes/scheduler.py → dashboard/src/routes/ → dashboard/src/ → dashboard/ → project root
    _project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# ── paths ───────────────────────────────────────────────────────────────
def _schedules_path() -> str:
    return os.path.join(_rooster_dir, "schedules.json")


def _history_path() -> str:
    return os.path.join(_rooster_dir, "schedule_history.json")


def _atomic_write_json(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return default if default is not None else []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default if default is not None else []


# ── cron helpers ────────────────────────────────────────────────────────
_CRON_RE = re.compile(
    r"^\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$"
)

_WEEKDAY_MAP = {
    "MON": 0, "TUE": 1, "WED": 2, "THU": 3,
    "FRI": 4, "SAT": 5, "SUN": 6,
    "0": 6, "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
}


def _parse_cron_field(field: str, all_range: range) -> List[int]:
    """Parse a single cron field (e.g. '1,15', '*/5', '1-5', '*') into a list of ints."""
    if field == "*":
        return list(all_range)
    result = set()
    for part in field.split(","):
        if "/" in part:
            base, step = part.split("/", 1)
            step = int(step)
            if base == "*":
                start, end = all_range.start, all_range.stop - 1
            elif "-" in base:
                start, end = map(int, base.split("-", 1))
            else:
                start, end = int(base), all_range.stop - 1
            for v in range(start, end + 1, step):
                if v in all_range:
                    result.add(v)
        elif "-" in part:
            start, end = map(int, part.split("-", 1))
            for v in range(start, end + 1):
                if v in all_range:
                    result.add(v)
        else:
            v = int(part)
            if v in all_range:
                result.add(v)
    return sorted(result)


def _cron_matches(expr: str, dt: datetime) -> bool:
    """Check if a 5-field cron expression matches the given datetime."""
    m = _CRON_RE.match(expr)
    if not m:
        return False
    minute_f, hour_f, dom_f, month_f, dow_f = m.groups()
    minutes = _parse_cron_field(minute_f, range(0, 60))
    hours = _parse_cron_field(hour_f, range(0, 24))
    doms = _parse_cron_field(dom_f, range(1, 32))
    months = _parse_cron_field(month_f, range(1, 13))
    dows = []
    for d in _parse_cron_field(dow_f, range(0, 8)):
        dows.append(d % 7)  # normalize 7 → 0 (Sunday)

    return (
        dt.minute in minutes
        and dt.hour in hours
        and dt.day in doms
        and dt.month in months
        and dt.weekday() in dows  # Python: Monday=0, Sunday=6
    )


def _cron_is_simple_daily(expr: str) -> Optional[str]:
    """If the cron is a simple daily pattern like '30 8 * * *', return 'HH:MM'. Otherwise None."""
    m = _CRON_RE.match(expr)
    if not m:
        return None
    minute_f, hour_f, dom_f, month_f, dow_f = m.groups()
    if dom_f != "*" or month_f != "*":
        return None
    if dow_f != "*":
        return None
    if "," in hour_f or "-" in hour_f or "/" in hour_f:
        return None
    if "," in minute_f or "-" in minute_f or "/" in minute_f:
        return None
    return f"{int(hour_f):02d}:{int(minute_f):02d}"


def _cron_is_simple_weekly(expr: str) -> Optional[tuple]:
    """If cron is '30 8 * * MON', return ('HH:MM', 'MON'). Otherwise None."""
    m = _CRON_RE.match(expr)
    if not m:
        return None
    minute_f, hour_f, dom_f, month_f, dow_f = m.groups()
    if dom_f != "*" or month_f != "*":
        return None
    if "," in hour_f or "-" in hour_f or "/" in hour_f:
        return None
    if "," in minute_f or "-" in minute_f or "/" in minute_f:
        return None
    if "," in dow_f or "-" in dow_f or "/" in dow_f:
        return None
    hhmm = f"{int(hour_f):02d}:{int(minute_f):02d}"
    return (hhmm, dow_f.upper())


def _cron_to_human(expr: str) -> str:
    """Convert a cron expression to a human-readable description."""
    m = _CRON_RE.match(expr.strip())
    if not m:
        return expr
    minute_f, hour_f, dom_f, month_f, dow_f = m.groups()
    parts = []
    if month_f != "*":
        parts.append(f"{month_f}月")
    if dom_f != "*":
        parts.append(f"每月{dom_f}日")
    if dow_f != "*":
        dow_names = {"0": "周日", "1": "周一", "2": "周二", "3": "周三",
                     "4": "周四", "5": "周五", "6": "周六", "7": "周日",
                     "MON": "周一", "TUE": "周二", "WED": "周三", "THU": "周四",
                     "FRI": "周五", "SAT": "周六", "SUN": "周日"}
        parts.append(dow_names.get(dow_f, dow_f))
    if hour_f != "*" and minute_f != "*":
        if "," not in hour_f and "," not in minute_f:
            parts.append(f"{int(hour_f):02d}:{int(minute_f):02d}")
    elif hour_f == "*":
        parts.append("每小时")
    else:
        parts.append(f"{hour_f}时{minute_f}分")
    return "".join(parts) if parts else f"自定义({expr})"


# ── OS task helpers ─────────────────────────────────────────────────────
_IS_WINDOWS = platform.system().lower() == "windows"
_IS_MACOS = platform.system().lower() == "darwin"


def _os_create_task(task_name: str, script_path: str, run_time: str, frequency: str, schedule_id: str = "") -> str:
    """Create OS-level scheduled task. Uses schedule_runner.py if schedule_id provided."""
    import sys
    python_exe = sys.executable
    if schedule_id and _project_root:
        runner = os.path.join(_project_root, "scripts", "schedule_runner.py")
        tr = f'"{python_exe}" "{runner}" {schedule_id}'
    else:
        tr = f'"{python_exe}" "{script_path}"'

    if _IS_WINDOWS:
        cmd = ["schtasks", "/Create", "/F", "/TN", task_name, "/TR", tr, "/SC", frequency, "/ST", run_time]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            return "ok" if result.returncode == 0 else f"schtasks error: {result.stderr.strip()}"
        except Exception as e:
            return str(e)
    elif _IS_MACOS:
        hour, minute = run_time.split(":")
        plist_file = os.path.expanduser(f"~/Library/LaunchAgents/com.rooster.{task_name}.plist")
        os.makedirs(os.path.dirname(plist_file), exist_ok=True)
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.rooster.{task_name}</string>
<key>ProgramArguments</key><array>
<string>{python_exe}</string><string>{runner if schedule_id else script_path}</string>
{"<string>" + schedule_id + "</string>" if schedule_id else ""}
</array>
<key>WorkingDirectory</key><string>{_project_root or os.getcwd()}</string>
<key>StartCalendarInterval</key><dict>
<key>Hour</key><integer>{int(hour)}</integer>
<key>Minute</key><integer>{int(minute)}</integer>
</dict>
<key>StandardOutPath</key><string>/tmp/rooster_{task_name}.log</string>
<key>StandardErrorPath</key><string>/tmp/rooster_{task_name}.err</string>
</dict></plist>"""
        try:
            subprocess.run(["launchctl", "unload", plist_file], capture_output=True, text=True, timeout=5)
            with open(plist_file, "w", encoding="utf-8") as f:
                f.write(plist)
            result = subprocess.run(["launchctl", "load", plist_file], capture_output=True, text=True, timeout=15)
            return "ok" if result.returncode == 0 else f"launchctl error: {result.stderr.strip()}"
        except Exception as e:
            return str(e)
    return "Unsupported platform"


def _os_delete_task(task_name: str) -> str:
    if _IS_WINDOWS:
        try:
            result = subprocess.run(["schtasks", "/Delete", "/F", "/TN", task_name], capture_output=True, text=True, timeout=15)
            return "ok" if result.returncode == 0 else result.stderr.strip()
        except Exception as e:
            return str(e)
    elif _IS_MACOS:
        plist_file = os.path.expanduser(f"~/Library/LaunchAgents/com.rooster.{task_name}.plist")
        try:
            if os.path.exists(plist_file):
                subprocess.run(["launchctl", "unload", plist_file], capture_output=True, text=True, timeout=15)
                os.remove(plist_file)
            return "ok"
        except Exception as e:
            return str(e)
    return "Unsupported platform"


def _os_disable_task(task_name: str) -> str:
    if _IS_WINDOWS:
        try:
            result = subprocess.run(["schtasks", "/Change", "/TN", task_name, "/DISABLE"], capture_output=True, text=True, timeout=15)
            return "ok" if result.returncode == 0 else result.stderr.strip()
        except Exception as e:
            return str(e)
    return "ok"  # macOS launchd: handled by JSON enabled flag


def _os_enable_task(task_name: str) -> str:
    if _IS_WINDOWS:
        try:
            result = subprocess.run(["schtasks", "/Change", "/TN", task_name, "/ENABLE"], capture_output=True, text=True, timeout=15)
            return "ok" if result.returncode == 0 else result.stderr.strip()
        except Exception as e:
            return str(e)
    return "ok"


# ── schedule data helpers ───────────────────────────────────────────────
def _enrich_schedule(entry: Dict) -> Dict:
    """Ensure an entry has all new fields with defaults (backward compat)."""
    defaults = {
        "name": entry.get("task", "")[:50],
        "description": "",
        "tags": [],
        "priority": 5,
        "concurrency_policy": "skip",
        "retry": {"count": 0, "backoff_base_sec": 5, "backoff_max_sec": 300},
        "timeout_sec": 3600,
        "trigger": {
            "type": "cron",
            "cron_expr": f"{entry.get('time', '08:00').split(':')[1]} {entry.get('time', '08:00').split(':')[0]} * * *"
            if entry.get("time") else "0 8 * * *",
            "interval_sec": None,
            "run_once_at": None,
        },
        "execution": {
            "script_path": "",
            "args": [],
            "env_vars": {},
            "working_dir": None,
        },
        "notifications": [],
        "variables": {"global": {}, "task": {}},
        "history_retention_days": 30,
        "updated_at": entry.get("created_at", datetime.now().isoformat()),
        "last_run_at": None,
        "last_status": None,
        "next_run_at": None,
    }
    result = {**defaults, **entry}
    # Ensure nested dicts are merged, not replaced
    for key in ("trigger", "execution", "retry", "variables"):
        if key in entry and isinstance(entry[key], dict):
            result[key] = {**defaults.get(key, {}), **entry[key]}
    return result


def _compute_status(entry: Dict) -> str:
    """Compute display status: running / disabled / failed."""
    if not entry.get("enabled", True):
        return "disabled"
    if entry.get("last_status") == "failure":
        return "failed"
    return "running"


def _compute_next_run(entry: Dict) -> Optional[str]:
    """Compute next run time from trigger config."""
    trigger = entry.get("trigger", {})
    trigger_type = trigger.get("type", "cron")
    if trigger_type == "cron" and trigger.get("cron_expr"):
        # Simple estimation: find next matching minute within 7 days
        expr = trigger["cron_expr"]
        now = datetime.now()
        for i in range(1, 7 * 24 * 60):
            candidate = now + timedelta(minutes=i)
            if _cron_matches(expr, candidate):
                return candidate.strftime("%Y-%m-%d %H:%M")
        return None
    elif trigger_type == "interval" and trigger.get("interval_sec"):
        last = entry.get("last_run_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                next_dt = last_dt + timedelta(seconds=trigger["interval_sec"])
                return next_dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass
        return None
    elif trigger_type == "once" and trigger.get("run_once_at"):
        return trigger["run_once_at"]
    return None


# ── API endpoints ───────────────────────────────────────────────────────

@router.get("/tasks")
async def api_list_tasks(status: str = Query("", description="Filter: running|disabled|failed"),
                         sort: str = Query("next_run", description="Sort: next_run|name|created")):
    schedules = _read_json(_schedules_path(), [])
    enriched = [_enrich_schedule(s) for s in schedules]

    # Compute next_run and status
    for s in enriched:
        s["_status"] = _compute_status(s)
        s["_next_run"] = _compute_next_run(s)

    # Filter
    if status and status != "all":
        enriched = [s for s in enriched if s["_status"] == status]

    # Sort
    if sort == "name":
        enriched.sort(key=lambda s: s.get("name", "").lower())
    elif sort == "created":
        enriched.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    else:
        # next_run: entries with next_run first, sorted ascending; no next_run last
        with_next = [(s, s.get("_next_run") or "zzz") for s in enriched]
        with_next.sort(key=lambda x: x[1])
        enriched = [x[0] for x in with_next]

    return {"ok": True, "tasks": enriched, "total": len(enriched)}


@router.post("/tasks")
async def api_create_task(data: Dict[str, Any] = Body(...)):
    task_id = uuid.uuid4().hex[:8]
    now = datetime.now().isoformat()

    entry = {
        "id": task_id,
        "task": data.get("task", ""),
        "session_id": data.get("session_id", "sess_schedule"),
        "frequency": "DAILY",
        "time": "08:00",
        "created_at": now,
        "enabled": True,

        "name": data.get("name", ""),
        "description": data.get("description", ""),
        "tags": data.get("tags", []),
        "priority": data.get("priority", 5),
        "concurrency_policy": data.get("concurrency_policy", "skip"),
        "retry": data.get("retry", {"count": 0, "backoff_base_sec": 5, "backoff_max_sec": 300}),
        "timeout_sec": data.get("timeout_sec", 3600),
        "trigger": data.get("trigger", {"type": "cron", "cron_expr": "0 8 * * *"}),
        "execution": data.get("execution", {"script_path": "", "args": [], "env_vars": {}, "working_dir": None}),
        "notifications": data.get("notifications", []),
        "variables": data.get("variables", {"global": {}, "task": {}}),
        "history_retention_days": data.get("history_retention_days", 30),
        "updated_at": now,
        "last_run_at": None,
        "last_status": None,
        "next_run_at": None,
    }

    # Derive legacy fields from trigger
    trigger = entry.get("trigger", {})
    if trigger.get("type") == "cron" and trigger.get("cron_expr"):
        daily_hhmm = _cron_is_simple_daily(trigger["cron_expr"])
        if daily_hhmm:
            entry["frequency"] = "DAILY"
            entry["time"] = daily_hhmm
        else:
            weekly = _cron_is_simple_weekly(trigger["cron_expr"])
            if weekly:
                entry["frequency"] = "WEEKLY"
                entry["time"] = weekly[0]
            else:
                entry["frequency"] = "DAILY"
                # Complex cron: guardian will handle it
    elif trigger.get("type") == "once" and trigger.get("run_once_at"):
        entry["frequency"] = "ONCE"
        try:
            dt = datetime.fromisoformat(trigger["run_once_at"])
            entry["time"] = dt.strftime("%H:%M")
        except (ValueError, TypeError):
            pass

    # Create OS-level task if script is provided
    exec_config = entry.get("execution", {})
    script_path = exec_config.get("script_path", "")
    os_task_result = ""
    if script_path and trigger.get("type") in ("cron", "once"):
        # Resolve script path
        if not os.path.isabs(script_path) and _project_root:
            resolved = os.path.normpath(os.path.join(_project_root, script_path))
        else:
            resolved = script_path

        if os.path.exists(resolved):
            import asyncio
            os_name = f"RoosterTask_{task_id}"
            freq_map = {"DAILY": "DAILY", "WEEKLY": "WEEKLY", "ONCE": "ONCE"}
            os_task_result = await asyncio.to_thread(
                _os_create_task,
                os_name, resolved, entry["time"],
                freq_map.get(entry["frequency"], "DAILY"),
                schedule_id=task_id
            )

    # Save to JSON
    schedules = _read_json(_schedules_path(), [])
    schedules.append(entry)
    _atomic_write_json(_schedules_path(), schedules)

    return {"ok": True, "task": entry, "os_task_result": os_task_result}


@router.get("/tasks/{task_id}")
async def api_get_task(task_id: str):
    schedules = _read_json(_schedules_path(), [])
    for s in schedules:
        if s.get("id") == task_id:
            enriched = _enrich_schedule(s)
            enriched["_status"] = _compute_status(enriched)
            enriched["_next_run"] = _compute_next_run(enriched)
            return {"ok": True, "task": enriched}
    raise HTTPException(status_code=404, detail="Task not found")


@router.put("/tasks/{task_id}")
async def api_update_task(task_id: str, data: Dict[str, Any] = Body(...)):
    schedules = _read_json(_schedules_path(), [])
    idx = None
    for i, s in enumerate(schedules):
        if s.get("id") == task_id:
            idx = i
            break
    if idx is None:
        raise HTTPException(status_code=404, detail="Task not found")

    existing = schedules[idx]

    # Update allowed fields
    for field in ("name", "description", "tags", "priority", "concurrency_policy",
                  "retry", "timeout_sec", "trigger", "execution", "notifications",
                  "variables", "history_retention_days", "task"):
        if field in data:
            existing[field] = data[field]

    existing["task"] = existing.get("name", "")
    existing["updated_at"] = datetime.now().isoformat()

    # Re-derive legacy fields
    trigger = existing.get("trigger", {})
    if trigger.get("type") == "cron" and trigger.get("cron_expr"):
        daily_hhmm = _cron_is_simple_daily(trigger["cron_expr"])
        if daily_hhmm:
            existing["frequency"] = "DAILY"
            existing["time"] = daily_hhmm
        else:
            weekly = _cron_is_simple_weekly(trigger["cron_expr"])
            if weekly:
                existing["frequency"] = "WEEKLY"
                existing["time"] = weekly[0]

    # Re-create OS task if script changed
    exec_config = existing.get("execution", {})
    script_path = exec_config.get("script_path", "")
    os_result = ""
    if script_path:
        import asyncio
        # Delete old, create new
        old_os_name = f"RoosterTask_{task_id}"
        await asyncio.to_thread(_os_delete_task, old_os_name)

        if not os.path.isabs(script_path) and _project_root:
            resolved = os.path.normpath(os.path.join(_project_root, script_path))
        else:
            resolved = script_path
        if os.path.exists(resolved) and existing.get("enabled", True):
            freq_map = {"DAILY": "DAILY", "WEEKLY": "WEEKLY", "ONCE": "ONCE"}
            os_result = await asyncio.to_thread(
                _os_create_task,
                old_os_name, resolved, existing.get("time", "08:00"),
                freq_map.get(existing.get("frequency", "DAILY"), "DAILY"),
                schedule_id=task_id
            )

    schedules[idx] = existing
    _atomic_write_json(_schedules_path(), schedules)
    return {"ok": True, "task": existing, "os_result": os_result}


@router.delete("/tasks/{task_id}")
async def api_delete_task(task_id: str):
    schedules = _read_json(_schedules_path(), [])
    schedules = [s for s in schedules if s.get("id") != task_id]
    _atomic_write_json(_schedules_path(), schedules)

    # Remove OS task
    import asyncio
    os_name = f"RoosterTask_{task_id}"
    await asyncio.to_thread(_os_delete_task, os_name)
    return {"ok": True}


@router.post("/tasks/{task_id}/toggle")
async def api_toggle_task(task_id: str):
    schedules = _read_json(_schedules_path(), [])
    for s in schedules:
        if s.get("id") == task_id:
            new_enabled = not s.get("enabled", True)
            s["enabled"] = new_enabled

            os_name = f"RoosterTask_{task_id}"
            import asyncio
            if new_enabled:
                await asyncio.to_thread(_os_enable_task, os_name)
            else:
                await asyncio.to_thread(_os_disable_task, os_name)

            _atomic_write_json(_schedules_path(), schedules)
            return {"ok": True, "enabled": new_enabled}
    raise HTTPException(status_code=404, detail="Task not found")


@router.post("/tasks/{task_id}/duplicate")
async def api_duplicate_task(task_id: str):
    schedules = _read_json(_schedules_path(), [])
    source = None
    for s in schedules:
        if s.get("id") == task_id:
            source = s
            break
    if not source:
        raise HTTPException(status_code=404, detail="Task not found")

    new_id = uuid.uuid4().hex[:8]
    now = datetime.now().isoformat()
    dup = {**source, "id": new_id, "created_at": now, "updated_at": now,
           "last_run_at": None, "last_status": None, "next_run_at": None,
           "name": source.get("name", "") + " (copy)",
           "task": source.get("name", "") + " (copy)"}

    schedules.append(dup)
    _atomic_write_json(_schedules_path(), schedules)
    return {"ok": True, "task": dup}


@router.post("/tasks/{task_id}/execute")
async def api_test_execute(task_id: str):
    """Test-execute a task immediately and return the result."""
    schedules = _read_json(_schedules_path(), [])
    entry = None
    for s in schedules:
        if s.get("id") == task_id:
            entry = _enrich_schedule(s)
            break
    if not entry:
        raise HTTPException(status_code=404, detail="Task not found")

    exec_config = entry.get("execution", {})
    script_path = exec_config.get("script_path", "")
    if not script_path:
        return {"ok": False, "error": "No script path configured"}

    # Resolve script path
    if not os.path.isabs(script_path) and _project_root:
        resolved = os.path.normpath(os.path.join(_project_root, script_path))
    else:
        resolved = script_path

    if not os.path.exists(resolved):
        return {"ok": False, "error": f"Script not found: {resolved}"}

    # Variable interpolation
    import sys
    env_vars = {**os.environ}
    env_vars.update(exec_config.get("env_vars", {}))
    task_vars = entry.get("variables", {}).get("task", {})
    for k, v in task_vars.items():
        if isinstance(v, str):
            v = v.replace("{{now}}", datetime.now().isoformat())
        env_vars[k] = str(v)

    timeout = entry.get("timeout_sec", 3600) or 3600
    args = exec_config.get("args", [])

    start = time.time()
    run_id = f"run_{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    try:
        result = subprocess.run(
            [sys.executable, resolved] + args,
            capture_output=True, text=True,
            timeout=min(timeout, 300),  # Cap test runs at 5 minutes
            env=env_vars,
            cwd=exec_config.get("working_dir") or _project_root,
        )
        duration = round(time.time() - start, 3)
        status = "success" if result.returncode == 0 else "failure"

        history_entry = {
            "run_id": run_id,
            "schedule_id": task_id,
            "status": status,
            "started_at": datetime.fromtimestamp(start).isoformat(),
            "finished_at": datetime.now().isoformat(),
            "duration_sec": duration,
            "exit_code": result.returncode,
            "stdout": result.stdout[-5000:] if result.stdout else "",
            "stderr": result.stderr[-5000:] if result.stderr else "",
            "error_stack": result.stderr[-2000:] if result.stderr and result.returncode != 0 else None,
            "retry_attempt": 0,
        }

        # Save to history
        _append_history(history_entry)

        # Update schedule last_run/last_status
        _update_schedule_status(task_id, status, datetime.fromtimestamp(start).isoformat())

        return {"ok": True, "result": history_entry}

    except subprocess.TimeoutExpired:
        duration = round(time.time() - start, 3)
        history_entry = {
            "run_id": run_id,
            "schedule_id": task_id,
            "status": "timeout",
            "started_at": datetime.fromtimestamp(start).isoformat(),
            "finished_at": datetime.now().isoformat(),
            "duration_sec": duration,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Timeout after {timeout}s",
            "error_stack": None,
            "retry_attempt": 0,
        }
        _append_history(history_entry)
        _update_schedule_status(task_id, "timeout", datetime.fromtimestamp(start).isoformat())
        return {"ok": True, "result": history_entry}

    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/tasks/{task_id}/history")
async def api_task_history(task_id: str,
                           page: int = Query(1, ge=1),
                           limit: int = Query(50, ge=1, le=500)):
    history = _read_json(_history_path(), [])
    filtered = [h for h in history if h.get("schedule_id") == task_id]
    filtered.sort(key=lambda h: h.get("started_at", ""), reverse=True)

    total = len(filtered)
    start_idx = (page - 1) * limit
    page_items = filtered[start_idx:start_idx + limit]

    return {"ok": True, "history": page_items, "total": total, "page": page, "limit": limit}


@router.post("/history/{run_id}/rerun")
async def api_rerun(run_id: str):
    """Re-execute the task associated with a history entry."""
    history = _read_json(_history_path(), [])
    entry = None
    for h in history:
        if h.get("run_id") == run_id:
            entry = h
            break
    if not entry:
        raise HTTPException(status_code=404, detail="History entry not found")

    return await api_test_execute(entry["schedule_id"])


@router.get("/dashboard")
async def api_dashboard(days: int = Query(7, ge=1, le=90)):
    history = _read_json(_history_path(), [])
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    today_runs = [h for h in history if h.get("started_at", "") >= today_start.isoformat()]
    today_total = len(today_runs)
    today_success = len([h for h in today_runs if h.get("status") == "success"])
    success_rate = round(today_success / today_total * 100, 1) if today_total else 0
    avg_duration = round(
        sum(h.get("duration_sec", 0) for h in today_runs) / today_total, 2
    ) if today_total else 0

    recent_failures = [
        h for h in history if h.get("status") in ("failure", "timeout")
    ]
    recent_failures.sort(key=lambda h: h.get("started_at", ""), reverse=True)
    recent_failures = recent_failures[:20]

    # Trend: daily counts for N days
    trend = []
    for i in range(days - 1, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        day_runs = [h for h in history if h.get("started_at", "")[:10] == day]
        day_success = len([h for h in day_runs if h.get("status") == "success"])
        day_failure = len([h for h in day_runs if h.get("status") in ("failure", "timeout")])
        trend.append({"date": day, "total": len(day_runs), "success": day_success, "failure": day_failure})

    return {
        "ok": True,
        "today_total": today_total,
        "today_success": today_success,
        "success_rate": success_rate,
        "avg_duration": avg_duration,
        "recent_failures": recent_failures,
        "trend": trend,
    }


# ── internal helpers ────────────────────────────────────────────────────
def _append_history(entry: Dict) -> None:
    history = _read_json(_history_path(), [])
    history.append(entry)
    _atomic_write_json(_history_path(), history)


def _update_schedule_status(task_id: str, status: str, started_at: str) -> None:
    schedules = _read_json(_schedules_path(), [])
    for s in schedules:
        if s.get("id") == task_id:
            s["last_status"] = status
            s["last_run_at"] = started_at
            break
    _atomic_write_json(_schedules_path(), schedules)
