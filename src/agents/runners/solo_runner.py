# src/agents/runners/solo_runner.py
"""单轮对话执行器（TALK / SOLO 模式）"""  # Single-round dialogue executor (TALK / SOLO mode)

import logging
from typing import Any

from agents.executor import AgentExecutor, AgentRunConfig
from agents.llm_client import LLMClient
from agents.prompt_builder import PromptBuilder
from gateway.event_handler import AgentEventHandler
from memory.manager import MemoryManager
from utils.config import settings

logger = logging.getLogger(__name__)


class SoloRunner:
    """处理 TALK/SOLO 模式的单轮对话请求。"""

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry,
        event_handler: AgentEventHandler,
        memory_manager: MemoryManager,
        prompt_builder: PromptBuilder,
        orchestrator=None,
    ):
        # [Fix] 显式为 SOLO 模式创建专属 LLM 客户端，确保其 Provider 与配置对齐
        # [Fix] Explicitly create dedicated LLM client for SOLO mode, ensuring Provider aligns with config
        solo_llm = LLMClient(
            provider=settings.SOLO_MODEL_MODE,
            model=settings.SOLO_MODEL_NAME,
            failover_order=settings.SOLO_FAILOVER_ORDER,
        )
        self.executor = AgentExecutor(
            event_handler=event_handler,
            llm_client=solo_llm,
            tool_registry=tool_registry,
            orchestrator=orchestrator,
            memory_manager=memory_manager,
            prompt_builder=prompt_builder,
        )

    async def run(self, msg: Any, channel: Any, event_handler: AgentEventHandler) -> str:
        """执行单轮对话，返回最终回复文本。"""
        from sessions.store import global_session_store

        # [Fix] 动态切换 EventBus，确保本次运行的事件能被 Router 捕获并分发
        # [Fix] Dynamically switch EventBus to ensure events from this run are captured and dispatched by Router
        self.executor.event_handler = event_handler

        session = global_session_store.get_or_create(msg.session_id)

        # 应用前端选择的 model override（session 级别）
        # Apply frontend-selected model override (session level)
        model_override = session.metadata.get("model_override", "").strip()
        if model_override:
            self.executor.llm_client = LLMClient(provider=model_override)
            logger.info(f"SOLO 模式使用 model override: {model_override}")
        elif settings.LOCAL_MODEL and settings.LOCAL_LIGHTWEIGHT_DOMAINS:
            msg_lower = msg.text.lower()
            if any(d.lower() in msg_lower for d in settings.LOCAL_LIGHTWEIGHT_DOMAINS):
                self.executor.llm_client = LLMClient(provider="local")
                logger.info("SOLO 自动路由到本地模型（轻量任务域匹配）")

        config = AgentRunConfig.for_solo(
            msg=msg,
            session=session,
            tool_registry=self.executor.tool_registry,
            images=session.metadata.pop("pending_images", []),
        )

        logger.info("SOLO 模式执行中...")
        final_content = await self.executor.run(config)
        return final_content or ""
