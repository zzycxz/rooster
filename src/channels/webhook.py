"""
src/channels/webhook.py

[P2-1] Webhook 通道适配器 — 通用 HTTP Webhook 接入。
基于 aiohttp 提供一个轻量 HTTP Server，接受 POST 请求触发 Agent。

请求格式（JSON）：
  {
    "text": "用户消息内容",
    "sender_id": "webhook_client",      // 可选
    "session_id": "custom_session_id"   // 可选，空=自动生成
  }

响应格式（JSON）：
  { "status": "accepted", "session_id": "..." }

启动方式：在 launcher.py 中注册：
  from channels.webhook import WebhookChannel
  registry.register(WebhookChannel(port=8766))
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Callable, Awaitable, Optional

from channels.base import BaseChannel, InboundMessage

logger = logging.getLogger(__name__)

# 可选依赖 aiohttp
# Optional dependency aiohttp
try:
    from aiohttp import web as _web

    _AIOHTTP_AVAILABLE = True
except ImportError:
    _web = None
    _AIOHTTP_AVAILABLE = False
    logger.warning(
        "[WebhookChannel] aiohttp 未安装，Webhook 通道不可用。pip install aiohttp 或 pip install rooster[webhook]"
    )


MessageHandler = Callable[[InboundMessage], Awaitable[None]]


class WebhookChannel(BaseChannel):
    """
    通用 HTTP Webhook 通道。
    收到 POST 请求后解析消息并调用注册的 on_message 处理器。
    """

    # Generic HTTP Webhook channel. Parse message on POST request and call registered on_message handler

    def __init__(
        self,
        port: int = 8766,
        path: str = "/webhook",
        secret_token: Optional[str] = None,
        on_message: Optional[MessageHandler] = None,
    ):
        super().__init__(channel_id="webhook")
        self.port = int(os.getenv("WEBHOOK_PORT", str(port)))
        self.path = path
        # 可选：校验 X-Webhook-Token 请求头
        # Optional: verify X-Webhook-Token request header
        self.secret_token = secret_token or os.getenv("WEBHOOK_SECRET_TOKEN", "")
        self._on_message = on_message
        self._runner: Optional[object] = None
        self._site: Optional[object] = None

    async def start(self) -> None:
        if not _AIOHTTP_AVAILABLE:
            logger.error("[WebhookChannel] aiohttp 未安装，无法启动 Webhook 服务")
            return

        app = _web.Application()
        app.router.add_post(self.path, self._handle_request)
        app.router.add_get("/health", self._handle_health)

        self._runner = _web.AppRunner(app)
        await self._runner.setup()
        self._site = _web.TCPSite(self._runner, "0.0.0.0", self.port)
        await self._site.start()
        self.is_running = True
        logger.info(f"[WebhookChannel] 已启动，监听 http://0.0.0.0:{self.port}{self.path}")

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self.is_running = False
        logger.info("[WebhookChannel] 已停止")

    async def send_message(self, to: str, text: str, **kwargs) -> None:
        """Webhook 为被动接收通道，send_message 记录日志但不主动推送。"""  # Webhook is a passive receive channel, send_message logs but doesn't actively push
        logger.info(f"[WebhookChannel] send_message to={to}: {text[:100]}")

    async def _handle_health(self, request: object) -> object:
        return _web.Response(text='{"status":"ok"}', content_type="application/json")

    async def _handle_request(self, request: object) -> object:
        # Token 校验
        if self.secret_token:
            token = request.headers.get("X-Webhook-Token", "")
            if token != self.secret_token:
                logger.warning("[WebhookChannel] 无效 Token，拒绝请求")
                return _web.Response(status=403, text='{"error":"invalid token"}', content_type="application/json")
        try:
            body = await request.json()
        except Exception:
            return _web.Response(status=400, text='{"error":"invalid JSON"}', content_type="application/json")

        text = body.get("text", "").strip()
        if not text:
            return _web.Response(status=400, text='{"error":"text is required"}', content_type="application/json")

        sender_id = body.get("sender_id", "webhook_client")
        session_id = body.get("session_id") or str(uuid.uuid4())

        msg = InboundMessage(
            sender_id=sender_id,
            text=text,
            channel_id=self.channel_id,
            session_id=session_id,
            raw_data=body,
        )

        logger.info(f"[WebhookChannel] 收到消息: session={session_id} text={text[:80]}")

        if self._on_message:
            asyncio.create_task(self._on_message(msg))

        resp = json.dumps({"status": "accepted", "session_id": session_id})
        return _web.Response(text=resp, content_type="application/json")

    def standardize_message(self, raw_data) -> InboundMessage:
        return InboundMessage(
            sender_id=raw_data.get("sender_id", "webhook_client"),
            text=raw_data.get("text", ""),
            channel_id=self.channel_id,
            session_id=raw_data.get("session_id") or str(uuid.uuid4()),
            raw_data=raw_data,
        )
