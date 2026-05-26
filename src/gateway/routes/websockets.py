"""WebSocket and Webhook routes — gateway WS, node WS, webhook."""

import uuid
import json
import os
import asyncio
import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, HTTPException

from ..auth import HMACVerifier
from ..connection_manager import ConnectionManager
from ..schemas import GatewayRequest, GatewayResponse
from ..server_methods import dispatch_methods
from channels.registry import ChannelRegistry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])

# Shared state — wired by server.py
manager: ConnectionManager = None
channel_registry: ChannelRegistry = None


def wire(conn_manager: ConnectionManager, ch_registry: ChannelRegistry):
    global manager, channel_registry
    manager = conn_manager
    channel_registry = ch_registry


async def _authenticate_ws(websocket: WebSocket) -> bool:
    """Authenticate WebSocket via first-message token exchange.

    Protocol:
    1. Server accepts the WS connection immediately (always — even in no-auth mode).
    2. If GATEWAY_API_KEY is set, sends {"type":"auth_required"} and waits for client response.
    3. Client must respond with {"type":"auth","token":"<api_key>"}.
    4. Returns True if authenticated (or auth disabled), False otherwise.
       On False, the socket is already closed.

    IMPORTANT: This function always calls websocket.accept() so that downstream
    managers (ConnectionManager, DashboardManager) never need to accept again.
    """
    import hmac as hmac_mod
    from starlette.websockets import WebSocketState

    # Step 1: Always accept the connection
    if websocket.application_state == WebSocketState.CONNECTING:
        await websocket.accept()

    api_key = os.getenv("GATEWAY_API_KEY", "").strip()
    if not api_key:
        return True  # No auth configured

    # Step 2: Challenge
    try:
        await websocket.send_text(json.dumps({"type": "auth_required"}))
    except Exception:
        logger.info("WebSocket 认证中断：客户端已断开（正常现象）")
        return False

    # Step 3: Wait for client response
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
        logger.info("WebSocket 认证中断：客户端已断开（正常现象）")
        return False

    try:
        await websocket.close(code=4001, reason="Authentication failed")
    except Exception:
        pass
    return False


@router.post("/api/chat")
async def http_chat(request: Request):
    """HTTP bridge for scheduled tasks (Guardian) and external integrations.

    Accepts JSON: {"message": "...", "session_id": "..."}.
    Internally dispatches through the same chat.send pipeline used by WebSocket clients.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    message_text = body.get("message", "").strip()
    session_id = body.get("session_id", "scheduled")

    if not message_text:
        raise HTTPException(status_code=400, detail="Missing 'message' field")

    from ..run_manager import global_run_manager
    from ..event_handler import AgentEventHandler
    from sessions.store import global_session_store
    from ..router import global_router

    session = global_session_store.get_or_create(session_id)
    session.add_message(role="user", content=message_text)
    global_session_store.save_session(session_id)

    run = global_run_manager.create_run(session_id)

    async def _noop_broadcast(event_data: Dict[str, Any]):
        pass

    event_handler = AgentEventHandler(_noop_broadcast)
    await event_handler.emit(run.run_id, session_id, "lifecycle", {"phase": "start"})

    agent_task = asyncio.create_task(global_router.process_run(run, session, message_text, event_handler))
    global_run_manager.register_task(run.run_id, agent_task)

    return {"status": "started", "runId": run.run_id, "sessionKey": session_id}


@router.websocket("/ws/gateway")
async def websocket_endpoint(websocket: WebSocket):
    if not await _authenticate_ws(websocket):
        return

    connection_id = str(uuid.uuid4())
    await manager.connect(connection_id, websocket)

    def create_responder(req_id: Optional[str]):
        async def respond(success: bool, data: Any = None, error: Any = None):
            resp = GatewayResponse(status="success" if success else "error", data=data or {}, error=error, id=req_id)
            await manager.send_personal_message(resp.model_dump_json(), connection_id)

        return respond

    async def broadcast(event_data: Dict[str, Any]):
        payload = json.dumps({"type": "event", "event": "agent", "data": event_data})
        await manager.send_personal_message(payload, connection_id)

    try:
        while True:
            data = await websocket.receive_text()
            logger.debug(f"Received from {connection_id}: {data}")
            try:
                request = GatewayRequest.model_validate_json(data)
                await dispatch_methods(
                    method=request.method,
                    params=request.params or {},
                    respond=create_responder(request.id),
                    broadcast=broadcast,
                    manager=manager,
                    connection_id=connection_id,
                )
            except WebSocketDisconnect:
                raise  # 让外层 handler 处理
                # Let the outer handler deal with it
            except Exception as e:
                logger.error(f"Request handling error: {e}")
                error_resp = GatewayResponse(status="error", data={}, error=f"Internal Server Error: {str(e)}")
                await manager.send_personal_message(error_resp.model_dump_json(), connection_id)
    except WebSocketDisconnect:
        logger.info(f"WebSocket {connection_id} 断开连接（正常现象，无需担心）")
        # WebSocket disconnected (normal behavior, no concern)
    except Exception as ws_err:
        logger.error(f"WebSocket {connection_id} exception: {ws_err}")
    finally:
        manager.disconnect(connection_id)


@router.websocket("/v1/node/ws")
async def node_websocket_endpoint(websocket: WebSocket):
    if not await _authenticate_ws(websocket):
        return

    connection_id = str(uuid.uuid4())

    query_payload = {"method": "node.query", "params": {}, "id": str(uuid.uuid4())}
    await websocket.send_text(json.dumps(query_payload))

    try:
        while True:
            data_str = await websocket.receive_text()
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError as json_err:
                logger.warning(f"Node WS invalid JSON from {connection_id}: {json_err}")
                continue

            if "result" in data or "id" in data:
                await manager.handle_node_message(connection_id, websocket, data)
    except WebSocketDisconnect:
        logger.info(f"Node {connection_id} 断开连接（正常现象，无需担心）")
        # Node disconnected (normal behavior, no concern)
    except Exception as ws_err:
        logger.error(f"Node WS exception for {connection_id}: {ws_err}")
    finally:
        manager.remove_node(connection_id)


@router.post("/webhook/{channel_id}")
async def universal_webhook(channel_id: str, request: Request):

    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if HMACVerifier.is_configured() and not HMACVerifier.verify(raw_body, signature):
        raise HTTPException(status_code=403, detail="Invalid HMAC signature")

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    channel = channel_registry.get_channel(channel_id)

    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}

    if not channel:
        raise HTTPException(status_code=404, detail=f"Channel {channel_id} not found.")

    logger.info(f"Webhook event for channel: {channel_id}")
    try:
        header = data.get("header", {})
        event_type = header.get("event_type")

        if event_type == "card.action.trigger":
            event = data.get("event", {})
            action = event.get("action", {})
            sender = event.get("operator", {})
            sender_id = sender.get("open_id", "unknown")
            action_value = action.get("value", {})

            logger.info(f"Card action received: {action_value}")
            from gateway.router import global_router
            from gateway.schemas import GatewayRequest

            request = GatewayRequest(
                method="chat",
                params={"text": f"执行动作: {action_value}"},
                session_id=f"feishu_{sender_id}",
                id=str(uuid.uuid4()),
            )
            asyncio.create_task(global_router.route_request_and_push(request, channel))
            return {"status": "success", "action_received": True}

        inbound = channel.standardize_message(data)
        if inbound:
            from gateway.router import global_router
            from gateway.schemas import GatewayRequest

            request = GatewayRequest(
                method="chat", params={"text": inbound.text}, session_id=inbound.session_id, id=str(uuid.uuid4())
            )
            asyncio.create_task(global_router.route_request_and_push(request, channel))
            return {"status": "success", "event_received": True}

        return {"status": "ignored", "reason": "No business event extracted."}
    except Exception:
        logger.exception(f"Webhook processing error for {channel_id}")
        return {"status": "error", "message": "Internal error"}
