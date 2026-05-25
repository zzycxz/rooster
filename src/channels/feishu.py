import logging
import os
import json
import time
import hashlib
import asyncio
import threading
from typing import Any, Dict, Optional
from cachetools import TTLCache


# 飞书 SDK 核心组件
# Feishu SDK core components
from lark_oapi.client import Client
from lark_oapi.ws.client import Client as WSClient
from lark_oapi.api.im.v1 import *

# 依据物理源码第 44/185 行，准确导入分发器处理逻辑
# Per physical source lines 44/185, accurately import dispatcher handler logic
from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

import lark_oapi.ws.client as sdk_ws_module

from .base import BaseChannel, InboundMessage

logger = logging.getLogger("FeishuChannel")


class FeishuChannel(BaseChannel):
    """
    FeishuChannel: 飞书消息通道。
    适配飞书 SDK WebSocket 长连接模式。
    """

    # FeishuChannel: Feishu message channel. Adapted for Feishu SDK WebSocket long-connection mode

    def __init__(self, channel_id: str = "feishu"):
        super().__init__(channel_id)
        from utils.config import settings

        self.app_id = settings.CH_FEISHU_ID
        self.app_secret = settings.CH_FEISHU_SECRET

        # 1. 业务客户端
        # 1. Business client
        self.lark_client = Client.builder().app_id(self.app_id).app_secret(self.app_secret).build()

        # 3. ⚡ 最终修正：使用官方 builder 模式注册事件
        # 3. Final fix: use official builder pattern to register events
        self.event_handler = (
            EventDispatcherHandler.builder(encrypt_key="", verification_token="")
            .register_p2_im_message_receive_v1(self._do_recv_message_v2)
            .register_p2_im_message_message_read_v1(self._handle_message_read)  # 处理已读事件，消除报错
            .build()
        )

        # [去重扩展] 缓存最近 10 分钟的消息 ID，防止断网重连时的重复规划
        # [Deduplication] Cache recent 10 min message IDs, prevent duplicate planning on reconnect
        self.processed_msg_ids = TTLCache(maxsize=1000, ttl=600)
        # [V2 增强] 内容指纹缓存，防止同内容多 ID 攻击，有效期 60 秒
        # [V2 Enhanced] Content fingerprint cache, prevent same-content multi-ID attack, TTL 60 seconds
        self.processed_fingerprints = TTLCache(maxsize=1000, ttl=60)

        self.ws_client = None
        self.startup_error: str = ""

    async def start(self):
        self.is_running = True
        logger.info(
            "🌩️ 正在启动飞书高级长连接隧道 (WebSocket)..."
        )  # Starting Feishu advanced long-connection tunnel (WebSocket)
        # 捕获主循环供回调使用
        # Capture main loop for callback use
        self.main_loop = asyncio.get_running_loop()

        # 开启独立驱动线程 (注意：WSClient 的实例化将移至线程内部)
        # Start independent driver thread (note: WSClient instantiation moved inside thread)
        t = threading.Thread(target=self._executor_thread, daemon=True)
        t.start()

    def _executor_thread(self):
        try:
            # 1. 物理创建并激活子线程私有 Loop
            # 1. Physically create and activate thread-private Loop
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)

            # 2. 重定向 SDK 内部的模块级全局 Loop
            # 2. Redirect SDK's module-level global Loop
            sdk_ws_module.loop = new_loop

            # 3. 在正确线程内实例化 WSClient，确保其内部 Lock 绑定到 new_loop
            # 3. Instantiate WSClient in the correct thread to ensure its internal Lock binds to new_loop
            from lark_oapi.core.enum import LogLevel

            self.ws_client = WSClient(
                app_id=self.app_id,
                app_secret=self.app_secret,
                event_handler=self.event_handler,
                log_level=LogLevel.WARNING,  # 屏蔽 ping/pong 刷屏 / Suppress ping/pong spam
            )

            logger.info(
                "SDK 事件 Loop 绑定成功，正在建立飞书长连接..."
            )  # SDK event Loop binding succeeded, establishing Feishu long connection
            self.ws_client.start()
        except Exception as e:
            logger.error(f"❌ 飞书长连接线程崩溃: {e}")
            self.is_running = False
            self.startup_error = str(e)
            # Notify dashboard via log bridge so user sees the failure
            try:
                from dashboard.src.dashboard_ws import broadcast_event
                import asyncio as _aio

                loop = self.main_loop
                if loop and loop.is_running():
                    _aio.run_coroutine_threadsafe(
                        broadcast_event(
                            "log",
                            {
                                "level": "ERROR",
                                "logger": "FeishuChannel",
                                "message": f"❌ 飞书通道启动失败: {e}。请在 Setup 中检查 FEISHU_APP_ID 和 FEISHU_APP_SECRET 配置。",
                                "ts": time.time(),
                            },
                        ),
                        loop,
                    )
            except Exception:
                pass

    async def stop(self):
        self.is_running = False

    def _do_recv_message_v2(self, data: P2ImMessageReceiveV1) -> None:
        """适配 SDK 2.2.3 回调签名的标准处理器"""  # Standard handler adapted for SDK 2.2.3 callback signature
        msg_event = data.event.message
        sender_id = data.event.sender.sender_id.open_id
        message_id = msg_event.message_id
        create_time_ms = int(msg_event.create_time)

        # --- [V3 持久化幂等性过滤] ---
        # --- [V3 Persistent idempotency filtering] ---
        from utils.security import state_guard

        # 1. 物理 ID 校验 (即使重启也能挡住重播)
        # 1. Physical ID check (can block replay even after restart)
        if state_guard.is_message_seen(message_id):
            logger.debug(f"⏭️  [Deduplicate] 跳过已处理的消息 ID (物理命中): {message_id}")
            return

        # 2. 过期消息过滤 (忽略超过 30 秒的历史积压)
        # 2. Expired message filter (ignore backlogged messages older than 30 seconds)
        now_ms = int(time.time() * 1000)
        if (now_ms - create_time_ms) > 30000:
            logger.warning(
                f"⏰ [Filter] 丢弃陈旧消息 (ID: {message_id}, 延迟: {(now_ms - create_time_ms) / 1000:.1f}s)"
            )
            self.processed_msg_ids[message_id] = True
            return

        # 3. 解析文本用于内容校验
        # 3. Parse text for content verification
        text = ""
        try:
            content_json = json.loads(msg_event.content)
            if msg_event.message_type == "text":
                text = content_json.get("text", "")
            elif msg_event.message_type == "post":
                sections = content_json.get("content", [])
                text_parts = []
                for section in sections:
                    for element in section:
                        if element.get("tag") == "text":
                            text_parts.append(element.get("text", ""))
                text = "".join(text_parts).strip()
        except Exception:
            pass

        if text:
            # 4. 内容指纹校验 (sender_id + text_hash)
            # 4. Content fingerprint check (sender_id + text_hash)
            content_hash = hashlib.md5(text.encode("utf-8")).hexdigest()
            fingerprint = f"{sender_id}:{content_hash}"
            if fingerprint in self.processed_fingerprints:
                logger.warning(f"🛡️  [Deduplicate] 检测到重复内容请求，已拦截 (Sender: {sender_id})")
                return
            self.processed_fingerprints[fingerprint] = True

        # 5. 任务正式承接，写入持久化记录 (跨重启去重核心)
        # 5. Task officially accepted, write persistent record (cross-restart dedup core)
        state_guard.mark_message_seen(message_id)
        # ---------------------------

        logger.info(f"📩 收到飞书消息: type={msg_event.message_type}, sender={sender_id}")

        try:
            # 标准化消息封装
            # Standardized message packaging
            # 兼容处理：飞书 SDK 对象转换字典
            # Compatibility: Feishu SDK object to dict conversion
            try:
                raw_dict = data.to_dict() if hasattr(data, "to_dict") else vars(data)
            except Exception:
                raw_dict = {"event_id": msg_event.message_id}  # 兜底策略

            inbound_msg = InboundMessage(
                channel_id=self.channel_id,
                sender_id=sender_id,
                session_id=f"feishu_{sender_id}",
                text=text,
                raw_data=raw_dict,
            )

            # 引入真正的消息中枢 (agents.router)
            # Import the real message hub (agents.router)
            from agents.router import Router

            # 定义结果回调，用于捕捉子线程异常
            # Define result callback for capturing sub-thread exceptions
            def _on_route_done(fut):
                try:
                    fut.result()
                    logger.info(f"✅ [Dispatcher] 路由任务成功完成 (Sender: {sender_id})")
                except Exception as e:
                    logger.error(f"❌ [CRITICAL] 路由执行失败: {e}", exc_info=True)

            # 跨线程投递并挂载监控
            # Cross-thread delivery with monitoring
            future = asyncio.run_coroutine_threadsafe(
                Router.get_instance().handle_inbound(inbound_msg, self), self.main_loop
            )
            future.add_done_callback(_on_route_done)

        except Exception as e:
            logger.error(f"❌ 飞书消息预处理失败: {e}")

    async def send_message(self, to: str, text: str, **kwargs):
        """发送简单文本消息给飞书用户 (支持网络重试)"""
        # 飞书不支持纯空消息，如果是空字符串且没有扩展内容，则直接跳过
        # Feishu doesn't support empty messages, skip if empty string and no extended content
        if not text.strip() and not kwargs.get("is_tool"):
            return

        content = json.dumps({"text": text})

        # 如果是工具调用且文本为空，发送一个简洁的提示
        # If tool call with empty text, send a brief hint
        if kwargs.get("is_tool"):
            tool_data = kwargs.get("tool_data", {})
            tool_name = tool_data.get("tool", "unknown")
            # 仅发送极简提示，不干扰最终答案
            # Only send minimal hint, don't interfere with final answer
            content = json.dumps({"text": f"🔍 Rooster 正在使用工具: `{tool_name}`..."})

        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(CreateMessageRequestBody.builder().receive_id(to).msg_type("text").content(content).build())
            .build()
        )

        # --- [DAY 4 网络韧性增强] ---
        max_retries = 3
        last_err = None
        for attempt in range(max_retries):
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.lark_client.im.v1.message.create, request)
                return
            except Exception as e:
                last_err = e
                wait_sec = (attempt + 1) * 1.5
                logger.warning(
                    f"⚠️ [Feishu] 消息发送抖动 (Attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait_sec}s..."
                )
                await asyncio.sleep(wait_sec)

        if last_err:
            logger.error(f"❌ [Feishu] 消息发送彻底失败: {last_err}")

    async def send_card(self, to: str, card_content: Dict[str, Any], **kwargs):
        content = json.dumps(card_content)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder().receive_id(to).msg_type("interactive").content(content).build()
            )
            .build()
        )
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.lark_client.im.v1.message.create, request)

    async def send_file(self, to: str, file_path: str, **kwargs):
        """发送本地文件到飞书对话框"""
        if not os.path.exists(file_path):
            logger.error(f"❌ 找不到文件: {file_path}")
            return False

        file_size = os.path.getsize(file_path) / (1024 * 1024)
        if file_size > 10:
            logger.warning(f"⚠️ 文件太大 ({file_size:.1f}MB)，跳过飞书推送。")
            return False

        try:
            file_name = os.path.basename(file_path)
            with open(file_path, "rb") as f:
                import lark_oapi.api.im.v1 as im

                upload_request = (
                    im.CreateFileRequest.builder()
                    .request_body(
                        im.CreateFileRequestBody.builder().file_type("stream").file_name(file_name).file(f).build()
                    )
                    .build()
                )

                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(None, self.lark_client.im.v1.file.create, upload_request)

                if not response.success():
                    logger.error(f"❌ 飞书物理文件上传失败: {response.msg}")
                    return False

                file_key = response.data.file_key

            msg_request = (
                im.CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    im.CreateMessageRequestBody.builder()
                    .receive_id(to)
                    .msg_type("file")
                    .content(json.dumps({"file_key": file_key}))
                    .build()
                )
                .build()
            )

            await loop.run_in_executor(None, self.lark_client.im.v1.message.create, msg_request)
            logger.info(f"✅ 文件已分发至飞书: {file_name}")
            return True
        except Exception as e:
            logger.error(f"❌ 飞书发送文件异常: {e}")
            return False

    async def send_image(self, to: str, image_path: str, **kwargs):
        """[Premium] 发送本地图片到飞书并内联显示"""
        if not os.path.exists(image_path):
            return False
        try:
            with open(image_path, "rb") as f:
                import lark_oapi.api.im.v1 as im

                upload_request = (
                    im.CreateImageRequest.builder()
                    .request_body(im.CreateImageRequestBody.builder().image_type("message").image(f).build())
                    .build()
                )

                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(None, self.lark_client.im.v1.image.create, upload_request)

                if not response.success():
                    return False
                image_key = response.data.image_key

            msg_request = (
                im.CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    im.CreateMessageRequestBody.builder()
                    .receive_id(to)
                    .msg_type("image")
                    .content(json.dumps({"image_key": image_key}))
                    .build()
                )
                .build()
            )
            await loop.run_in_executor(None, self.lark_client.im.v1.message.create, msg_request)
            return True
        except Exception as e:
            logger.error(f"❌ 飞书发送图片异常: {e}")
            return False

    async def send_post(self, to: str, title: str, content_list: list, **kwargs):
        """[Premium] 发送飞书富文本消息"""
        try:
            import lark_oapi.api.im.v1 as im

            post_content = {"zh_cn": {"title": title, "content": content_list}}
            request = (
                im.CreateMessageRequest.builder()
                .receive_id_type("open_id")
                .request_body(
                    im.CreateMessageRequestBody.builder()
                    .receive_id(to)
                    .msg_type("post")
                    .content(json.dumps(post_content))
                    .build()
                )
                .build()
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.lark_client.im.v1.message.create, request)
            return True
        except Exception as e:
            logger.error(f"❌ 飞书富文本发送失败: {e}")
            return False

    def standardize_message(self, raw_data: Dict[str, Any]) -> Optional[InboundMessage]:
        return None

    def _handle_message_read(self, data: Any) -> None:
        """占位符：处理已读回执，消除 SDK 报错"""
        pass
