import logging
import json
import uuid
import asyncio
from typing import Any, Callable, Dict
from .protocol import ErrorCodes, error_shape
from .run_manager import global_run_manager
from .event_handler import AgentEventHandler
from sessions.store import global_session_store

logger = logging.getLogger(__name__)


class MethodHandler:
    def __init__(self, broadcast_callback: Callable, manager: Any, connection_id: str):
        self.event_handler = AgentEventHandler(broadcast_callback)
        self.manager = manager
        self.connection_id = connection_id

    async def handle_connect(self, params: Dict[str, Any], respond: Callable):
        """处理 connect 指令：节点身份注册"""
        # Handle connect command: node identity registration
        role = params.get("role", "node")
        node_id = params.get("nodeId") or f"node_{str(uuid.uuid4())[:8]}"
        caps = params.get("caps", [])
        display_name = params.get("displayName")

        if role == "node":
            self.manager.register_node(self.connection_id, node_id, caps, display_name)
            await respond(True, data={"nodeId": node_id, "status": "registered"})
        else:
            await respond(True, data={"status": "connected", "role": role})

    async def handle_node_list(self, params: Dict[str, Any], respond: Callable):
        """处理 node.list 指令：列出在线受控节点"""
        # Handle node.list: list online managed nodes
        nodes = self.manager.list_nodes()
        await respond(True, data={"nodes": nodes})

    async def handle_node_invoke(self, params: Dict[str, Any], respond: Callable):
        """处理 node.invoke 指令：将指令转发给指定节点"""
        # Handle node.invoke: forward command to specified node
        node_id = params.get("nodeId")
        command = params.get("command")
        invoke_params = params.get("params", {})
        idempotency_key = params.get("idempotencyKey") or str(uuid.uuid4())

        if not node_id or not command:
            return await respond(False, error=error_shape(ErrorCodes.INVALID_REQUEST, "Missing nodeId or command"))

        # 寻找目标节点的连接
        # Find the target node's connection
        node_ws = self.manager.get_node_connection(node_id)
        if not node_ws:
            return await respond(
                False, error=error_shape(ErrorCodes.UNAVAILABLE, f"Node {node_id} not found or offline")
            )

        # 封装并转发请求 (Rooster Request 格式)
        # Package and forward the request (Rooster Request format)
        request_payload = {
            "method": "node.invoke",
            "params": {"command": command, "paramsJSON": json.dumps(invoke_params)},
            "id": idempotency_key,
        }

        try:
            await node_ws.send_text(json.dumps(request_payload))
            # 注意：此处通常为异步响应，通过事件流传回
            # Note: the response is typically asynchronous, returned via the event stream
            # 为了简化，此处先告诉调用方：指令已下发
            # For simplicity, tell the caller the command has been dispatched
            await respond(True, data={"status": "invoked", "idempotencyKey": idempotency_key})
        except Exception as e:
            logger.error(f"Failed to forward invoke to node {node_id}: {e}")
            await respond(False, error=error_shape(ErrorCodes.INTERNAL_ERROR, str(e)))

    async def handle_chat_send(self, params: Dict[str, Any], respond: Callable):
        """处理 chat.send 指令：仿照 Rooster 实现 Run 创建与流式反馈"""
        # Handle chat.send: create a Run and stream feedback, following the Rooster pattern
        session_id = params.get("sessionKey")
        message_text = params.get("message", "")

        if not session_id:
            return await respond(False, error=error_shape(ErrorCodes.INVALID_REQUEST, "Missing sessionKey"))

        # 1. 获取会话与路由适配
        session = global_session_store.get_or_create(session_id)

        images = params.get("images", [])
        if message_text or images:
            session.add_message(role="user", content=message_text, images=images)
            global_session_store.save_session(session_id)

        # 存储 model override（session 级别，前端选择的 provider）
        # Store model override (session-level, provider selected by frontend)
        model_override = params.get("modelOverride", "").strip()
        if model_override:
            session.metadata["model_override"] = model_override

        # Check if the session is currently waiting for input
        old_run_id = global_run_manager.session_to_run.get(session_id)
        if old_run_id:
            old_run = global_run_manager.active_runs.get(old_run_id)
            if old_run and old_run.status == "waiting_for_input":
                logger.info(f"Resuming Run {old_run_id} with user input: {message_text}")
                old_run.input_data = message_text
                old_run.status = "running"
                old_run.input_event.set()
                await respond(True, data={"runId": old_run_id, "status": "resumed"})
                return

        # 2. 创建运行任务 (Run)
        # 2. Create a Run
        run = global_run_manager.create_run(session_id)

        # 3. 立即回应：任务已通过校验并启动
        # 3. Respond immediately: task has passed validation and started
        await respond(True, data={"runId": run.run_id, "status": "started"})

        # 4. 模拟 AI 异步处理 (视觉版 Rooster)
        # 4. Simulate AI async processing (visual Rooster)
        await self.event_handler.emit(run.run_id, session_id, "lifecycle", {"phase": "start"})

        # 将消息传给 Router 进行推理 (此处后续会集成 Vision Tool)
        # Pass the message to Router for inference (Vision Tool integration planned)
        from .router import global_router

        agent_task = asyncio.create_task(global_router.process_run(run, session, message_text, self.event_handler))
        # Register the asyncio Task so abort_run() can actually cancel it
        global_run_manager.register_task(run.run_id, agent_task)

    async def handle_session_get_history(self, params: Dict[str, Any], respond: Callable):
        """处理 session.get_history 指令"""
        # Handle session.get_history command
        session_id = params.get("sessionKey")
        if not session_id:
            return await respond(False, error=error_shape(ErrorCodes.INVALID_REQUEST, "Missing sessionKey"))

        session = global_session_store.get_or_create(session_id)
        await respond(True, data={"history": [m.model_dump() for m in session.history]})

    async def handle_session_list(self, params: Dict[str, Any], respond: Callable):
        """处理 session.list 指令：返回所有历史会话的简要列表"""
        # Handle session.list: return a brief list of all historical sessions
        sessions = global_session_store.list_sessions()
        result = []
        for sid, s in sessions.items():
            last_msg = ""
            if s.history:
                # Find the last message that is not a tool response
                valid_msgs = [m.content for m in s.history if not m.content.startswith("<tool_response")]
                if valid_msgs:
                    last_msg = valid_msgs[-1][:30] + ("..." if len(valid_msgs[-1]) > 30 else "")
                else:
                    last_msg = s.history[-1].content[:30] + ("..." if len(s.history[-1].content) > 30 else "")

            title = s.metadata.get("title", sid)
            # 如果标题包含随机特征或是未命名的 SessionID
            # If the title contains random characteristics or is an unnamed SessionID
            if title == sid or title.startswith("dash_"):
                if s.history:
                    # 智能提炼第一条真实的用户输入消息作为标题（排除内部工具响应）
                    # Intelligently extract the first real user message as the title (excluding tool responses)
                    user_msgs = [
                        m.content for m in s.history if m.role == "user" and not m.content.startswith("<tool_response")
                    ]
                    title = user_msgs[0][:20] if user_msgs else last_msg
                else:
                    title = "新对话"  # New conversation

            result.append(
                {
                    "sessionId": sid,
                    "lastMessage": last_msg,
                    "title": title,
                }
            )
        await respond(True, data={"sessions": result})

    async def handle_session_delete(self, params: Dict[str, Any], respond: Callable):
        """处理 session.delete 指令：安全销毁会话的磁盘与内存记录"""
        # Handle session.delete: safely destroy session records from disk and memory
        session_id = params.get("sessionKey")
        if not session_id:
            return await respond(False, error=error_shape(ErrorCodes.INVALID_REQUEST, "Missing sessionKey"))
        await global_session_store.delete_session(session_id)
        await respond(True, data={"status": "deleted", "sessionKey": session_id})

    async def handle_chat_cancel(self, params: Dict[str, Any], respond: Callable):
        """处理 chat.cancel 指令：取消正在执行的 Run"""
        # Handle chat.cancel: cancel a currently running Run
        run_id = params.get("runId")
        session_id = params.get("sessionKey")

        if run_id:
            run = global_run_manager.active_runs.get(run_id)
            if run:
                global_run_manager.abort_run(run_id)
                return await respond(True, data={"runId": run_id, "status": "cancelled"})
        if session_id:
            rid = global_run_manager.session_to_run.get(session_id)
            if rid:
                global_run_manager.abort_run(rid)
                return await respond(True, data={"runId": rid, "status": "cancelled"})

        await respond(
            False, error=error_shape(ErrorCodes.INVALID_REQUEST, "No active run found for given runId/sessionKey")
        )


async def dispatch_methods(
    method: str, params: Dict[str, Any], respond: Callable, broadcast: Callable, manager: Any, connection_id: str
):
    """网关核心分发器"""
    # Gateway core dispatcher
    handler_instance = MethodHandler(broadcast, manager, connection_id)

    mapping = {
        "connect": handler_instance.handle_connect,
        "node.list": handler_instance.handle_node_list,
        "node.invoke": handler_instance.handle_node_invoke,
        "chat.send": handler_instance.handle_chat_send,
        "chat.cancel": handler_instance.handle_chat_cancel,
        "session.get_history": handler_instance.handle_session_get_history,
        "session.list": handler_instance.handle_session_list,
        "session.delete": handler_instance.handle_session_delete,
    }

    if method in mapping:
        await mapping[method](params, respond)
    else:
        await respond(False, error=error_shape(ErrorCodes.INVALID_REQUEST, f"Unknown method: {method}"))
