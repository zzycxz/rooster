# src/agents/router.py
"""
[Rooster 任务路由中枢]
瘦 Router：只负责分拣和路由，不再负责执行和审计。
"""
# [Rooster task routing hub]
# Thin Router: only responsible for sorting and routing, no longer handles execution and audit

import asyncio
import logging
import os
from typing import Any

from agents.llm_client import LLMClient
from agents.runners.solo_runner import SoloRunner
from agents.runners.mission_runner import MissionRunner
from agents.prompt_builder import PromptBuilder
from gateway.event_handler import AgentEventHandler
from memory.manager import MemoryManager
from toolset.registry import global_tool_registry
from utils.config import settings

logger = logging.getLogger(__name__)


class Router:
    """请求分拣器 — 不再负责执行。"""  # Request sorter — no longer responsible for execution

    _instance = None

    @classmethod
    def get_instance(cls, **kwargs):
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    def __init__(
        self,
        llm_client=None,
        tool_registry=None,
        orchestrator=None,
        event_handler=None,
        memory_manager=None,
        prompt_builder=None,
    ):
        self.llm_client = llm_client or LLMClient(provider="mimo", model=settings.MIMO_MODEL)
        self._triage_llm = LLMClient(provider=settings.ROUTER_MODEL_MODE, model=settings.ROUTER_MODEL_NAME)
        self.tool_registry = tool_registry or global_tool_registry
        self.memory_manager = memory_manager or MemoryManager()
        self.prompt_builder = prompt_builder or PromptBuilder()

        if orchestrator is None:
            try:
                from agents.orchestrator import ToolOrchestrator

                self.orchestrator = ToolOrchestrator(workspace_dir=os.path.abspath("."))
            except ImportError:
                self.orchestrator = None
        else:
            self.orchestrator = orchestrator

        async def dummy_broadcast(*args, **kwargs):
            pass

        self.event_handler = event_handler or AgentEventHandler(broadcast_callback=dummy_broadcast)

        # Short-circuit router (cached)
        from .short_circuit import ShortCircuitRouter

        self._short_circuit = ShortCircuitRouter()

        # 初始化 Runner
        # Initialize Runners
        self.solo_runner = SoloRunner(
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
            event_handler=self.event_handler,
            memory_manager=self.memory_manager,
            prompt_builder=self.prompt_builder,
            orchestrator=self.orchestrator,
        )
        self.mission_runner = MissionRunner(
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
            event_handler=self.event_handler,
            memory_manager=self.memory_manager,
            prompt_builder=self.prompt_builder,
            orchestrator=self.orchestrator,
        )

    async def handle_inbound(self, msg: Any, channel: Any):
        """处理所有入口指令：分拣 → 路由 → 进化。"""  # Handle all inbound commands: sort → route → evolve
        from evolution.engine import EvolutionEngine
        from agents.reframer import Reframer

        # 0. Confirmation interception — if a mission is waiting for user confirmation,
        #    route this message to the confirmation handler instead of starting a new mission.
        from agents.runners.mission_runner import get_pending_confirmation, resolve_confirmation

        pending = get_pending_confirmation(msg.session_id)
        if pending:
            confirmed = await resolve_confirmation(msg.session_id, msg.text)
            if confirmed:
                logger.info(f"用户确认子任务: {pending['subtask_id']}")
            else:
                logger.info(f"用户拒绝子任务: {pending['subtask_id']}")
            return

        # 隐私：进化引擎使用本地模型 / Privacy: evolution engine uses local model
        try:
            from models.factory import ModelFactory

            _evo_client = ModelFactory.get_client("local")
        except Exception:
            _evo_client = self.llm_client
        evolution_engine = EvolutionEngine(llm_client=_evo_client)

        # 1a. Advanced Security: jailbreak detection (default OFF, enabled via ADVANCED_SECURITY=true)
        try:
            from utils.security.advanced_guard import AdvancedGuard

            jb_report = AdvancedGuard.scan_user_message(msg.text)
            if jb_report.should_block:
                logger.warning(f"[AdvancedGuard] 越狱尝试被阻断: {jb_report.threats[0].evidence!r}")
                await channel.send_message(to=msg.sender_id, text=jb_report.to_user_message())
                return
            if jb_report.has_threats:
                logger.warning(f"[AdvancedGuard] 越狱线索（高/中级），记录并继续: {jb_report.threats[0].evidence!r}")
        except Exception as _ag_err:
            logger.debug(f"[AdvancedGuard] jailbreak check skipped (degraded): {_ag_err}")

        # 1b. Triage
        triage_state = await self._triage_via_llm(msg.text)

        # 动态事件处理器（支持流式打字机）
        # Dynamic event handler (supports streaming typewriter)
        async def channel_broadcast(event_dict: dict):
            if event_dict.get("stream") == "assistant":
                status = event_dict["data"].get("status")
                text = event_dict["data"].get("text", "")

                if status == "running" and triage_state == "[TALK]":
                    # 直答模式：实时流式输出
                    # Direct answer mode: real-time streaming output
                    await channel.send_message(to=msg.sender_id, text=text)

                elif status == "done":
                    if triage_state in ["[DIRECT]", "[REFRAME]"]:
                        logger.debug("复杂任务，文字报告已扣押，等待审计放行...")
                    elif triage_state == "[TALK]":
                        # 直答结束，补一个换行
                        # Direct answer ended, append a newline
                        await channel.send_message(to=msg.sender_id, text="\n")
                    else:
                        # 其他情况（防止漏掉）
                        # Other cases (prevent missing)
                        await channel.send_message(to=msg.sender_id, text=text)

        dynamic_event_handler = AgentEventHandler(broadcast_callback=channel_broadcast)

        # 2. BLOCK — 兜底：如果是下载类请求被误拦截，降级为 REFRAME
        # 2. BLOCK — fallback: if download request misjudged as BLOCK, degrade to REFRAME
        if triage_state == "[BLOCK]":
            _download_kw = ["下载", "download", "install", "安装", "迅雷", "磁力", "torrent", "bt下载"]
            if any(kw in msg.text.lower() for kw in _download_kw):
                logger.warning(f"下载请求被误判为 BLOCK，自动降级至 REFRAME: {msg.text}")
                triage_state = "[REFRAME]"
            else:
                logger.warning(f"安全策略拦截: {msg.text}")
                await channel.send_message(
                    to=msg.sender_id,
                    text="⚠️ **[安全警示]** 您的请求包含敏感内容，已被系统拦截。",
                )
                return

        # 3. TALK → SoloRunner
        if triage_state == "[TALK]":
            logger.info("判定为直答 (SOLO Mode)")  # Determined as direct answer (SOLO Mode)
            await self.solo_runner.run(msg, channel, dynamic_event_handler)
            self._fire_and_forget(
                evolution_engine.on_turn_complete(msg.session_id, msg.text, "Direct Response Sent", [])
            )
            return

        # 3b. SCHEDULE → 自然语言定时任务解析与持久化
        # 3b. SCHEDULE → natural language scheduled task parsing and persistence
        if triage_state == "[SCHEDULE]":
            logger.info("判定为定时任务 (SCHEDULE Mode)")  # Determined as scheduled task (SCHEDULE Mode)
            await self._handle_schedule(msg, channel)
            return

        # 4. DIRECT / REFRAME → MissionRunner
        logger.info(f"进入弹性任务模式 (Mode: {triage_state})")  # Entering flexible task mode

        # 意图重构
        # Intent reframing
        reframed_text = msg.text
        if triage_state == "[REFRAME]" or getattr(settings, "ENABLE_REFRAMER", False):
            reframe_mode = getattr(settings, "REFRAMER_MODEL_MODE", settings.ROUTER_MODEL_MODE)
            reframe_name = getattr(settings, "REFRAMER_MODEL_NAME", settings.ROUTER_MODEL_NAME)
            reframe_llm = LLMClient(provider=reframe_mode, model=reframe_name)
            reframer = Reframer(reframe_llm)
            reframed_text = await reframer.reframe(msg.text)
            logger.info(f"重构后任务: {reframed_text}")
        else:
            logger.info("任务清晰，跳过重构直达战略官。")

        # --- [歧义拦截] Reframer 的 CLARIFICATION_NEEDED 信号处理 ---
        # Reframer 判定实体存在多版本歧义时，返回带有 __CLARIFICATION_NEEDED__ 前缀的 payload。
        # Router 在此处截获，格式化为用户友好的问询消息发送，不进入 MissionRunner。
        # 用户的下一条回复将携带澄清信息，重新路由并透传给 Reframer/Executor 正常处理。
        _CLARIFICATION_PREFIX = "__CLARIFICATION_NEEDED__:"
        if reframed_text.startswith(_CLARIFICATION_PREFIX):
            import json as _json
            try:
                payload = _json.loads(reframed_text[len(_CLARIFICATION_PREFIX):])
                question = payload.get("question", "请问您想要哪个版本？")
                options = payload.get("options", [])
            except Exception:
                question = reframed_text[len(_CLARIFICATION_PREFIX):]
                options = []

            # 格式化问询消息
            lines = [f"❓ **需要确认一下：**\n\n{question}"]
            if options:
                lines.append("\n**请从以下选项中选择：**")
                for i, opt in enumerate(options, 1):
                    lines.append(f"  **{i}.** {opt}")
                lines.append("\n请回复选项序号（如 `1`、`2`）或直接输入您想要的具体描述。")
            clarification_msg = "\n".join(lines)

            await channel.send_message(to=msg.sender_id, text=clarification_msg)
            logger.info(f"[Router] 歧义拦截，已向用户发出问询，等待下一轮澄清回复。")
            return  # 不进入 MissionRunner，等待用户下一条消息

        # ⚡ 智能直通车 (Short-Circuit Execution) 拦截器
        if await self._short_circuit.try_handle(reframed_text, channel, msg.sender_id):
            return

        await self.mission_runner.run(msg, channel, reframed_text, dynamic_event_handler)
        self._fire_and_forget(evolution_engine.on_turn_complete(msg.session_id, msg.text, "Mission Completed", []))

    @staticmethod
    def _fire_and_forget(coro):
        """启动后台任务，异常记录到日志而非静默丢弃。"""  # Start background task, log exceptions instead of silently discarding
        task = asyncio.create_task(coro)
        task.add_done_callback(
            lambda t: logger.error(f"Background task failed: {t.exception()}") if t.exception() else None
        )

    async def _triage_via_llm(self, text: str) -> str:
        """三路智能分诊判定。"""  # Three-way intelligent triage determination
        if len(text) < 5 and any(
            k in text.lower()
            for k in [
                "hi",
                "你好",
                "在吗",
                "hello",
                "hey",
                "ok",
                "好的",
                "嗯",
                "谢谢",
            ]
        ):
            return "[TALK]"
        try:
            triage_llm = self._triage_llm
            prompt_path = os.path.join(os.path.dirname(__file__), "..", "prompts", "router_triage.md")
            if os.path.exists(prompt_path):
                with open(prompt_path, "r", encoding="utf-8") as f:
                    triage_prompt = f.read()
            else:
                triage_prompt = "输出 [TALK]/[DIRECT]/[REFRAME]/[BLOCK]。输入: {text}"

            if "{text}" in triage_prompt:
                user_content = triage_prompt.replace("{text}", text)
            else:
                user_content = f'{triage_prompt}\n\n用户输入："{text}"'

            response = await triage_llm.chat_non_stream(
                [{"role": "user", "content": user_content}],
                model=settings.ROUTER_MODEL_NAME,
            )
            verdict = response.content.upper()
            if "[TALK]" in verdict:
                return "[TALK]"
            if "[SCHEDULE]" in verdict:
                return "[SCHEDULE]"
            if "[REFRAME]" in verdict:
                return "[REFRAME]"
            if "[BLOCK]" in verdict:
                return "[BLOCK]"
            return "[DIRECT]"
        except Exception as e:
            logger.warning(f"分诊故障 ({e})，使用关键词兜底分诊。")
            return self._triage_by_keyword(text)

    _DOWNLOAD_KW = [
        "下载",
        "download",
        "install",
        "安装",
        "迅雷",
        "磁力",
        "torrent",
        "fetch",
        "retrieve",
        "save file",
        "get file",
    ]
    _SCHEDULE_KW = [
        "每天",
        "每周",
        "每小时",
        "每分钟",
        "定时",
        "自动提醒",
        "提醒我",
        "schedule",
        "every day",
        "every hour",
        "every week",
        "remind me at",
        "at 8am",
        "at 9am",
        "daily report",
        "weekly report",
    ]
    _TALK_KW = [
        "你好",
        "hi",
        "hello",
        "who are you",
        "你是谁",
        "在吗",
        "怎么了",
        "谢谢",
        "hey",
        "good morning",
        "good evening",
        "how are you",
        "thanks",
        "thank you",
        "what can you do",
        "help me understand",
        "是什么",
        "什么是",
        "解释",
        "explain",
        "what is",
        "what are",
        "翻译",
        "translate",
        "convert",
        "换算",
    ]
    _COMPLEX_KW = [
        "帮我",
        "帮我搜",
        "帮我查",
        "分析",
        "对比",
        "总结",
        "整理",
        "帮我写",
        "帮我做",
        "写一个",
        "生成",
        "批量",
        "search for",
        "find all",
        "analyze",
        "compare",
        "summarize",
        "write a",
        "create a",
        "build",
        "generate",
        "help me",
        "can you",
        "please",
        "how do i",
        "what is the best",
    ]

    def _triage_by_keyword(self, text: str) -> str:
        """LLM 不可用时的关键词兜底分诊。"""  # Keyword fallback triage when LLM is unavailable
        t = text.lower()
        if any(k in t for k in self._SCHEDULE_KW):
            return "[SCHEDULE]"
        if any(k in t for k in self._DOWNLOAD_KW):
            return "[REFRAME]"
        if any(k in t for k in self._TALK_KW):
            return "[TALK]"
        if any(k in t for k in self._COMPLEX_KW):
            return "[DIRECT]"
        return "[DIRECT]"

    async def _handle_schedule(self, msg: Any, channel: Any):
        """将自然语言定时任务解析后写入 .rooster/schedules.json。"""  # Parse natural language scheduled task and write to .rooster/schedules.json
        import json
        import re
        import uuid
        import datetime

        text = msg.text
        schedule_id = str(uuid.uuid4())[:8]

        # Simple NL extraction: look for time patterns
        time_patterns = [
            (r"每天\s*(\d{1,2})[点时:：](\d{0,2})", "daily"),
            (r"every day at\s*(\d{1,2}):?(\d{0,2})\s*(am|pm)?", "daily"),
            (r"每周(一|二|三|四|五|六|日|天)", "weekly"),
            (r"every week", "weekly"),
            (r"每小时", "hourly"),
            (r"every hour", "hourly"),
        ]

        freq = "daily"
        cron_time = "08:00"
        for pat, f in time_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                freq = f
                if f == "daily" and m.lastindex and m.lastindex >= 1:
                    hour = int(m.group(1))
                    minute = int(m.group(2)) if m.lastindex >= 2 and m.group(2) else 0
                    if f != "hourly":
                        # Handle am/pm if present
                        if m.lastindex >= 3 and m.group(3) and "pm" in m.group(3).lower() and hour < 12:
                            hour += 12
                        cron_time = f"{hour:02d}:{minute:02d}"
                break

        entry = {
            "id": schedule_id,
            "task": text,
            "session_id": msg.session_id,
            "frequency": freq,
            "time": cron_time,
            "created_at": datetime.datetime.now().isoformat(),
            "enabled": True,
        }

        schedules_path = os.path.join(".rooster", "schedules.json")
        os.makedirs(".rooster", exist_ok=True)
        schedules = []
        if os.path.exists(schedules_path):
            try:
                with open(schedules_path, "r", encoding="utf-8") as f:
                    schedules = json.load(f)
            except Exception as e:
                logger.error(f"❌ schedules.json 文件损坏，解析失败，定时任务列表已重置: {e}")
                schedules = []
        schedules.append(entry)
        tmp_path = schedules_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(schedules, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, schedules_path)
        except Exception as e:
            logger.error(f"❌ schedules.json 写入失败: {e}")
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            raise

        logger.info(f"定时任务已保存: {entry}")
        await channel.send_message(
            to=msg.sender_id,
            text=(
                f"✅ **[定时任务已注册]** ID: `{schedule_id}`\n"
                f"- 任务描述: {text}\n"
                f"- 执行频率: **{freq}**\n"
                f"- 执行时间: **{cron_time}**\n\n"
                f"📁 已写入 `.rooster/schedules.json`。\n"
                f"系统后台守护进程将在指定时间自动触发此任务。"
            ),
        )
