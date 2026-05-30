"""System routes — health, version, metrics, stats, toolset, guardian, sessions."""

import os
import logging
from typing import Dict, Any

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from utils.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

# Shared state — wired by app.py
_get_skill_loader_fn = None
_env_local_path_fn = None


def wire(get_skill_loader_fn, env_local_path_fn):
    global _get_skill_loader_fn, _env_local_path_fn
    _get_skill_loader_fn = get_skill_loader_fn
    _env_local_path_fn = env_local_path_fn


@router.get("/api/health")
async def api_health():
    checks: Dict[str, Dict[str, Any]] = {}

    _provider_keys = [
        "MIMO_KEY",
        "ZHIPU_KEY",
        "ZHIPU_GLM_KEY",
        "JIUTIAN_KEY",
        "OPENAI_KEY",
        "ANTHROPIC_KEY",
        "KIMI_KEY",
        "QWEN_KEY",
        "CLOUD_KEY",
        "LOCAL_KEY",
    ]
    configured = [k for k in _provider_keys if os.getenv(k, "").strip()]
    if configured:
        checks["llm_providers"] = {"ok": True, "providers": configured}
    else:
        checks["llm_providers"] = {"ok": False, "msg": "未配置任何 LLM，请前往初始配置"}

    env_exists = os.path.exists(_env_local_path_fn())
    checks["env_local"] = {"ok": env_exists, "msg": "Found" if env_exists else "Not found"}

    overall = all(v.get("ok") for v in checks.values())
    return {"ok": overall, "checks": checks}


@router.post("/api/cancel")
async def api_cancel():
    """Cancel all active runs (global kill-switch via HTTP)."""
    from gateway.run_manager import global_run_manager

    aborted = global_run_manager.abort_all()
    return {"ok": True, "aborted": aborted}


@router.get("/api/version")
async def api_version():
    import importlib.metadata

    try:
        ver = importlib.metadata.version("rooster")
    except importlib.metadata.PackageNotFoundError:
        # 动态探测 pyproject.toml 路径以适应不同的目录结构 (Dynamic check to support different layout structures)
        d4 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        toml_path = os.path.join(d4, "pyproject.toml")
        if not os.path.exists(toml_path):
            d5 = os.path.dirname(d4)
            if os.path.exists(os.path.join(d5, "pyproject.toml")):
                toml_path = os.path.join(d5, "pyproject.toml")
            elif os.path.exists(os.path.join(os.getcwd(), "pyproject.toml")):
                toml_path = os.path.join(os.getcwd(), "pyproject.toml")
        ver = "unknown"
        if os.path.exists(toml_path):
            for line in open(toml_path, encoding="utf-8"):
                if line.strip().startswith("version"):
                    ver = line.split("=")[1].strip().strip('"').strip("'")
                    break
    return {"version": ver}


@router.get("/api/metrics/summary")
async def api_metrics_summary():
    try:
        from gateway.metrics import metrics

        return {"ok": True, "metrics": metrics.expose_dict()}
    except Exception as exc:
        logger.exception("Failed to get metrics summary")
        return {"ok": False, "error": str(exc)}


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics_endpoint():
    from gateway.metrics import metrics as _metrics

    return _metrics.expose()


@router.get("/api/system/stats")
async def api_system_stats():
    import asyncio
    import psutil
    import platform as _platform

    cpu_percent = psutil.cpu_percent(interval=0)
    cpu_count = psutil.cpu_count(logical=True)
    cpu_freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    disks = []
    for d in psutil.disk_partitions():
        try:
            usage = psutil.disk_usage(d.mountpoint)
            disks.append(
                {
                    "mountpoint": d.mountpoint,
                    "total_gb": round(usage.total / 1024**3, 1),
                    "used_gb": round(usage.used / 1024**3, 1),
                    "percent": usage.percent,
                }
            )
        except (PermissionError, OSError):
            pass

    net = psutil.net_io_counters()

    def _scan_procs():
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_percent", "cpu_percent"]):
            try:
                info = p.info
                if info.get("memory_percent") and info["memory_percent"] > 0.5:
                    procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        procs.sort(key=lambda x: x.get("memory_percent", 0), reverse=True)
        return procs[:5]

    loop = asyncio.get_event_loop()
    top_procs = await loop.run_in_executor(None, _scan_procs)

    return {
        "ok": True,
        "platform": {
            "system": _platform.system(),
            "release": _platform.release(),
            "node": _platform.node(),
            "machine": _platform.machine(),
        },
        "cpu": {"percent": cpu_percent, "cores": cpu_count, "freq_mhz": round(cpu_freq.current) if cpu_freq else None},
        "memory": {
            "total_gb": round(mem.total / 1024**3, 1),
            "used_gb": round(mem.used / 1024**3, 1),
            "percent": mem.percent,
            "swap_gb": round(swap.total / 1024**3, 1) if swap.total else 0,
        },
        "disks": disks,
        "network": {"sent_mb": round(net.bytes_sent / 1024**2, 1), "recv_mb": round(net.bytes_recv / 1024**2, 1)},
        "top_processes": top_procs,
    }


@router.get("/api/guardian/status")
async def api_guardian_status():
    from pathlib import Path

    try:
        status_path = (
            Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
            / ".rooster"
            / "guardian_status.json"
        )
        if not status_path.exists() or status_path.stat().st_size == 0:
            return {"ok": True, "guardian": None, "message": "Guardian not running"}
        import json

        with open(status_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"ok": True, "guardian": data}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Failed to read guardian status: {exc}")
        return {"ok": False, "error": str(exc)}


@router.get("/api/sessions")
async def api_sessions_list():
    try:
        from sessions.store import SessionStore

        store = SessionStore.get_instance()
        sessions = store.list_sessions()
        items = []
        for sid, sess in sessions.items():
            title = sess.metadata.get("title")
            if not title:
                for m in sess.history:
                    if m.role == "user" and not m.content.startswith("<tool_response"):
                        title = m.content[:20]
                        break
                if not title:
                    # Fall back to the first non-tool response message in the history
                    valid_msgs = [m.content for m in sess.history if not m.content.startswith("<tool_response")]
                    title = valid_msgs[-1][:30] if valid_msgs else "新对话"
            items.append(
                {
                    "session_id": sid,
                    "message_count": len(sess.history),
                    "created_at": sess.created_at,
                    "updated_at": sess.updated_at,
                    "title": title,
                }
            )
        items.sort(key=lambda x: x["updated_at"] or "", reverse=True)
        return {"ok": True, "sessions": items, "total": len(items)}
    except Exception as exc:
        logger.exception("Failed to list sessions")
        return {"ok": False, "error": str(exc)}


@router.get("/api/toolset")
async def api_toolset_list():
    try:
        from toolset.registry import global_tool_registry

        tools_by_kit = {}
        for name, tool in global_tool_registry._tools.items():
            kit_name = getattr(tool, "kit", "General")
            if kit_name not in tools_by_kit:
                tools_by_kit[kit_name] = []
            schema = tool.get_schema()
            tools_by_kit[kit_name].append(
                {
                    "name": tool.name,
                    "kit": kit_name,
                    "description": tool.description,
                    "domain": getattr(tool, "domain", "general"),
                    "parameters": list(schema.get("parameters", {}).get("properties", {}).keys()),
                    "required": schema.get("parameters", {}).get("required", []),
                }
            )
        for kit_name in tools_by_kit:
            tools_by_kit[kit_name].sort(key=lambda t: t["name"])
        return {
            "ok": True,
            "toolset": tools_by_kit,
            "total_kits": len(tools_by_kit),
            "total_tools": len(global_tool_registry._tools),
        }
    except Exception as exc:
        logger.exception("Failed to list toolset")
        return {"ok": False, "error": str(exc)}


@router.get("/api/security/status")
async def api_security_status():
    raw_paths = os.getenv("ALLOWED_PATHS", "")
    return {"ok": True, "is_wildcard_paths": "*" in raw_paths, "is_gateway_key_set": bool(settings.GATEWAY_API_KEY)}


@router.get("/api/aria2/status")
async def api_aria2_status():
    """Check whether aria2c RPC is reachable."""
    import http.client
    port = int(os.environ.get("ARIA2_RPC_PORT", "6800"))
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request(
            "POST", "/jsonrpc",
            body=b'{"jsonrpc":"2.0","id":"ping","method":"aria2.getVersion","params":[]}',
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        conn.close()
        running = resp.status < 400
    except Exception:
        running = False
    return {"ok": True, "running": running, "port": port}


@router.post("/api/aria2/start")
async def api_aria2_start():
    """Attempt to launch aria2c as a background daemon."""
    import asyncio
    import http.client
    import shutil
    import subprocess
    port = int(os.environ.get("ARIA2_RPC_PORT", "6800"))
    token = os.environ.get("ARIA2_TOKEN") or os.environ.get("ARIA2_RPC_SECRET", "")
    aria2c_bin = shutil.which("aria2c")
    if not aria2c_bin:
        return {"ok": False, "started": False, "error": "aria2c not found in PATH. Install via: winget install aria2  (Windows) or  brew install aria2  (macOS) or  apt install aria2  (Linux)"}
    try:
        cmd = [
            aria2c_bin,
            "--enable-rpc",
            "--rpc-listen-all=true",
            f"--rpc-listen-port={port}",
            "--rpc-allow-origin-all",
            "--daemon",
        ]
        if token:
            cmd.append(f"--rpc-secret={token}")
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await asyncio.sleep(1.2)
        # Verify it actually started
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request(
                "POST", "/jsonrpc",
                body=b'{"jsonrpc":"2.0","id":"ping","method":"aria2.getVersion","params":[]}',
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            conn.close()
            started = resp.status < 400
        except Exception:
            started = False
        return {"ok": True, "started": started, "port": port}
    except Exception as exc:
        logger.warning(f"Failed to start aria2c: {exc}")
        return {"ok": False, "started": False, "error": str(exc)}
