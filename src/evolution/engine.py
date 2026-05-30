import asyncio
import logging
import os
from typing import List, Dict, Any

from .triggers import TriggerChecker
from .soul_writer import SoulWriter
from .user_writer import UserWriter
from .prompt_templates import USER_PREFERENCE_EXTRACTOR, CORRECTION_ANALYZER

logger = logging.getLogger(__name__)


class EvolutionEngine:
    """
    Rooster 进化引擎 (EvolutionEngine) 核心调度器。
    负责接收对话事件、触发逻辑判断、调用 LLM 分析并写回记忆文件。
    """

    # Rooster Evolution Engine core scheduler.
    # Receives conversation events, trigger logic judgment, calls LLM analysis and writes back memory files

    def __init__(self, rooster_root: str = ".rooster", llm_client: Any = None):
        self.trigger = TriggerChecker()
        self.soul_writer = SoulWriter(os.path.join(rooster_root, "SOUL.md"))
        self.user_writer = UserWriter(os.path.join(rooster_root, "USER.md"))
        self.llm_client = llm_client
        self.semaphore = asyncio.Semaphore(2)  # 最大并发进化任务数 / Max concurrent evolution task count
        self.turn_counter = 0
        self._background_tasks: set = set()

    def _fire_background(self, coro):
        """Create a background task and hold a strong reference to prevent GC collection."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def on_turn_complete(
        self, session_id: str, user_message: str, agent_message: str, full_history: List[Dict[str, str]]
    ) -> None:
        """
        每轮对话结束后调用（由 main.py 触发）。
        """
        # Called after each conversation turn (triggered by main.py)
        self.turn_counter += 1

        # 1. 快速检查触发信号
        # 1. Quick check for trigger signals
        signals = self.trigger.check_all(user_message)
        if not signals:
            return

        logger.info(f"🧬 [Evolution] 检测到进化信号: {signals}. 正在启动后台分析...")

        # 2. 分发异步进化任务
        # 2. Dispatch async evolution tasks
        for signal in signals:
            self._fire_background(self._process_evolution(signal, user_message, agent_message, full_history))

    async def _process_evolution(self, signal: str, user_msg: str, agent_msg: str, history: list):
        """具体分析与写回任务"""  # Specific analysis and write-back task
        async with self.semaphore:
            try:
                # 任务超时保护
                # Task timeout protection
                from utils.config import settings
                await asyncio.wait_for(self._run_task(signal, user_msg, agent_msg, history), timeout=getattr(settings, "EVOLUTION_TIMEOUT", 30.0))
            except asyncio.TimeoutError:
                logger.warning(f"⏳ [Evolution] 任务 {signal} 分析超时 ({getattr(settings, 'EVOLUTION_TIMEOUT', 30.0)}s)，已放弃。")
            except Exception as e:
                logger.error(f"❌ [Evolution] 任务 {signal} 执行失败: {str(e)}")

    async def _run_task(self, signal: str, user_msg: str, agent_msg: str, history: list):
        if not self.llm_client:
            logger.warning("⚠️ 进化引擎缺少 LLM 客户端，无法分析。")
            return

        from utils.config import settings

        # 隐私：进化分析使用传入的 llm_client（应为 local），用户消息不出本机
        # Privacy: evolution uses the passed-in llm_client (should be local), user messages stay local
        model_name = getattr(self.llm_client, "model", None) or settings.LOCAL_MODEL or "qwen3.5-4b"

        # 1. 选择 Prompt 与处理策略
        if signal == "CORRECTION":
            prompt = CORRECTION_ANALYZER
            target_fn = self.soul_writer.append_insight
            target_section = "## 核心行为原则"
            mode = "text"
        elif signal == "PREFERENCE":
            prompt = USER_PREFERENCE_EXTRACTOR
            target_fn = self.user_writer.update_field
            target_section = "## 偏好与习惯"  # 默认字段，JSON 中可覆盖 / Default field, JSON can override
            mode = "json"
        elif signal == "MILESTONE":
            prompt = USER_PREFERENCE_EXTRACTOR  # 复用提取器
            target_fn = self.user_writer.update_field
            target_section = "## 当前重点项目"
            mode = "json"
        else:
            return

        # 2. 调用 LLM 分析并提取洞察
        # 2. Call LLM to analyze and extract insights
        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": f"### 对话上下文 ###\n{self._format_history(history)}\n\n### 当前焦点 ###\n用户: {user_msg}\nAI: {agent_msg}",
            },
        ]

        try:
            response = await self.llm_client.chat_non_stream(messages=messages, model=model_name, temperature=0.3)
            raw_content = response.content.strip()
            if not raw_content:
                return

            # 3. 执行写回逻辑
            # 3. Execute write-back logic
            if mode == "text":
                if len(raw_content) > 5:
                    target_fn(target_section, raw_content)

            elif mode == "json":
                try:
                    # 尝试寻找并解析 JSON 块
                    import json

                    start = raw_content.find("{")
                    end = raw_content.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        data = json.loads(raw_content[start : end + 1])
                        field = data.get("field", target_section)
                        content = data.get("content", "")
                        if content:
                            target_fn(field, content, self.turn_counter)
                except Exception as je:
                    logger.warning(f"⚠️ [Evolution] JSON 解析失败: {je} | Raw: {raw_content[:100]}")

        except Exception as e:
            logger.error(f"❌ [Evolution] LLM 调用或写回失败: {e}")

    def _format_history(self, history: list, limit: int = 5) -> str:
        """格式化最近几轮历史，为进化提供背景"""  # Format recent turns of history for evolution context
        lines = []
        for m in history[-limit:]:
            role = "用户" if m["role"] == "user" else "AI"
            lines.append(f"{role}: {m['content'][:200]}...")
        return "\n".join(lines)
