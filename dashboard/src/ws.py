"""Dashboard WebSocket endpoint — /ws/dashboard."""

import json
import os
import asyncio
import time
import logging
from typing import Dict, Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from gateway.auth import mask_secret
from gateway.security import validate_config_keys, validate_config_values

from .dashboard_ws import dashboard_manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard-ws"])

# Wired by app.py
_config_save_fn = None


def wire(config_save_fn):
    global _config_save_fn
    _config_save_fn = config_save_fn


_MASK_KEYS = frozenset(
    {
        "ZHIPU_KEY",
        "ZHIPU_GLM_KEY",
        "OPENAI_KEY",
        "ANTHROPIC_KEY",
        "KIMI_KEY",
        "QWEN_KEY",
        "CLOUD_KEY",
        "MIMO_KEY",
        "JIUTIAN_KEY",
        "EMBEDDING_KEY",
        "TAVILY_API_KEY",
        "E2B_API_KEY",
        "LOCAL_KEY",
        "GATEWAY_API_KEY",
        "WEBHOOK_HMAC_SECRET",
        "FEISHU_APP_SECRET",
        "FEISHU_USER_OPEN_ID",
        "ARIA2_TOKEN",
    }
)


async def _authenticate_ws(websocket: WebSocket) -> bool:
    """Authenticate WebSocket via first-message token exchange."""
    import hmac as hmac_mod
    from starlette.websockets import WebSocketState

    if websocket.application_state == WebSocketState.CONNECTING:
        await websocket.accept()

    api_key = os.getenv("GATEWAY_API_KEY", "").strip()
    if not api_key:
        return True

    try:
        await websocket.send_text(json.dumps({"type": "auth_required"}))
    except Exception:
        return False

    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        msg = json.loads(raw)
        token = msg.get("token", "")
        if hmac_mod.compare_digest(token, api_key):
            await websocket.send_text(json.dumps({"type": "auth_ok"}))
            return True
    except asyncio.TimeoutError:
        pass
    except (json.JSONDecodeError, KeyError):
        pass
    except Exception:
        return False

    try:
        await websocket.close(code=4001, reason="Authentication failed")
    except Exception:
        pass
    return False


@router.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    if not await _authenticate_ws(websocket):
        return

    await dashboard_manager.connect(websocket)

    # Send current config snapshot from .env
    try:
        # 动态探测 .env 路径以适应不同的目录结构 (Dynamic check to support different layout structures)
        d4 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        env_path = os.path.join(d4, ".env")
        if not os.path.exists(env_path):
            d5 = os.path.dirname(d4)
            if os.path.exists(os.path.join(d5, ".env")):
                env_path = os.path.join(d5, ".env")
            elif os.path.exists(os.path.join(os.getcwd(), ".env")):
                env_path = os.path.join(os.getcwd(), ".env")
        config_data: Dict[str, Any] = {}
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    k, _, v = stripped.partition("=")
                    k, v = k.strip(), v.strip()
                    if k in _MASK_KEYS and v:
                        v = mask_secret(v)
                    config_data[k] = v

        await websocket.send_text(
            json.dumps({"type": "config", "ts": time.time(), "data": config_data}, ensure_ascii=False)
        )
    except Exception as exc:
        logger.warning(f"Could not send initial config to dashboard: {exc}")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg_in = json.loads(raw)
                msg_type = msg_in.get("type", "")
                if msg_type == "config.save":
                    data = msg_in.get("data", {})
                    rejected = validate_config_keys(data)
                    if rejected:
                        result = {"ok": False, "error": f"Keys not allowed: {rejected}"}
                    else:
                        oversized = validate_config_values(data)
                        if oversized:
                            result = {"ok": False, "error": f"Values too long for keys: {oversized}"}
                        else:
                            result = await _config_save_fn(data)
                    await websocket.send_text(
                        json.dumps(
                            {"type": "config.save.result", "ts": time.time(), "data": result}, ensure_ascii=False
                        )
                    )
            except json.JSONDecodeError as exc:
                logger.debug(f"Dashboard WS message decode error: {exc}")
            except Exception as exc:
                logger.debug(f"Dashboard WS message handling error: {exc}")
    except WebSocketDisconnect:
        logger.info("Dashboard WebSocket 断开连接（页面刷新或关闭，正常现象）")
        dashboard_manager.disconnect(websocket)
