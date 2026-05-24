import logging
from typing import Any
from sessions.store import global_session_store
from gateway.schemas import GatewayRequest, GatewayResponse

logger = logging.getLogger(__name__)


class MessageRouter:
    def __init__(self):
        self.store = global_session_store

    async def route_request(self, request: GatewayRequest) -> GatewayResponse:
        """
        核心路由分发器：
        1. 锁定 session_id
        2. 提取并存储用户消息
        3. 返回响应
        """
        session_id = request.session_id or "default_user_session"
        session = self.store.get_or_create(session_id)

        # 优先使用 method/params，兼容 action/payload
        # Prefer method/params, fall back to action/payload for backward compatibility
        action = request.action or request.method
        payload = request.payload or request.params or {}

        if action == "chat":
            user_text = payload.get("text", "")
            session.add_message(role="user", content=user_text)

            logger.info(f"Routed chat to session {session_id}. History len: {len(session.history)}")

            ai_reply = f"Hello! Your session ID is {session_id}. I've received your message."
            session.add_message(role="assistant", content=ai_reply)

            return GatewayResponse(
                status="success",
                data={"reply": ai_reply, "history_count": len(session.history), "session_id": session_id},
            )
        elif action == "ping":
            return GatewayResponse(status="success", data={"pong": True})
        else:
            return GatewayResponse(status="error", data={}, error=f"Unsupported action: {action}")

    async def route_request_and_push(self, request: GatewayRequest, channel: Any):
        """
        飞书长连接等异步通道的路由推送逻辑。
        统一走主 Router 的 handle_inbound 路径。
        """
        from agents.router import Router

        session_id = request.session_id or "default_user_session"

        # 构造兼容 msg 对象
        # Build a compatible msg object
        user_text = request.params.get("text", "") if request.params else ""

        class FakeMsg:
            pass

        msg = FakeMsg()
        msg.session_id = session_id
        msg.sender_id = session_id.split("_", 1)[1] if "_" in session_id else session_id
        msg.text = user_text

        try:
            router = Router.get_instance()
            await router.handle_inbound(msg, channel)
        except Exception as e:
            logger.error(f"路由处理失败: {e}")
            await channel.send_message(to=msg.sender_id, text=f"引擎错误: {str(e)}")

    async def process_run(self, run: Any, session: Any, message_text: str, event_handler: Any):
        """
        处理一个 Run：构建内联 channel 把 send_message 转为 event_handler 流式事件，
        再调用 Router.handle_inbound 完成真正的 AI 推理。
        """
        from agents.router import Router
        from channels.base import InboundMessage, BaseChannel

        run_id = run.run_id
        session_id = run.session_id

        # 内联 channel：send_message -> event_handler.emit(stream="assistant")
        # Inline channel: send_message -> event_handler.emit(stream="assistant")
        class _InlineChannel(BaseChannel):
            def __init__(self_c):
                super().__init__("ws_inline")

            async def start(self_c):
                pass

            async def stop(self_c):
                pass

            async def send_message(self_c, to: str, text: str, **kwargs):
                await event_handler.emit(
                    run_id=run_id,
                    session_id=session_id,
                    stream="assistant",
                    data={"text": text, "status": "running"},
                )

        channel = _InlineChannel()

        msg = InboundMessage(
            sender_id=session_id,
            text=message_text,
            channel_id="ws_inline",
            session_id=session_id,
            raw_data={},
        )

        try:
            router = Router.get_instance()
            await router.handle_inbound(msg, channel)
        except Exception as e:
            logger.error(f"process_run 失败: {e}")
            await event_handler.emit(
                run_id, session_id, "lifecycle", {"status": "failed", "phase": "error", "message": str(e)}
            )
        finally:
            from .run_manager import global_run_manager

            global_run_manager.complete_run(run_id)
            await event_handler.emit(run_id, session_id, "lifecycle", {"status": "done", "phase": "end"})


# 单例
# Singleton
global_router = MessageRouter()
