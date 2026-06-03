# src/agents/router.py
"""
[Rooster V15 任务路由中枢]
L1 硬规则门闸 → SkillIndex → Reframer(可选) → Strategist.decide() → MissionRunner
"""

import asyncio
import json
import logging
import os
import time
from typing import Any

from agents.llm_client import LLMClient
from agents.runners.mission_runner import MissionRunner
from agents.prompt_builder import PromptBuilder
from agents.skill_index import get_skill_index
from gateway.event_handler import AgentEventHandler
from gateway.metrics import metrics
from memory.manager import MemoryManager
from toolset.registry import global_tool_registry
from utils.config import settings
from utils.logging_context import reset_mission_id, set_mission_id

logger = logging.getLogger(__name__)


class Router:
    """V15 请求分拣器 — L1 硬路由 + Strategist 语义主权。"""

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

        self.mission_runner = MissionRunner(
            llm_client=self.llm_client,
            tool_registry=self.tool_registry,
            event_handler=self.event_handler,
            memory_manager=self.memory_manager,
            prompt_builder=self.prompt_builder,
            orchestrator=self.orchestrator,
        )

    async def close(self):
        await self.llm_client.close()

    async def handle_inbound(self, msg: Any, channel: Any, parent_event_handler=None):
        """处理所有入口指令：L1 门闸 → 路由 → 执行。"""
        _mission_token = set_mission_id(getattr(msg, "session_id", "router"))
        try:
            await self._handle_inbound_inner(msg, channel, parent_event_handler)
        finally:
            reset_mission_id(_mission_token)

    async def _handle_inbound_inner(self, msg: Any, channel: Any, parent_event_handler=None):
        """V15 路由主流程。"""
        from evolution.engine import EvolutionEngine

        # 进化引擎（本地模型）
        try:
            from models.factory import ModelFactory
            _evo_client = ModelFactory.get_client("local")
        except Exception:
            _evo_client = self.llm_client
        evolution_engine = EvolutionEngine(llm_client=_evo_client)

        # 1. 安全检查
        try:
            from utils.security.advanced_guard import AdvancedGuard
            jb_report = AdvancedGuard.scan_user_message(msg.text)
            if jb_report.should_block:
                logger.warning(f"[AdvancedGuard] 越狱尝试被阻断: {jb_report.threats[0].evidence!r}")
                await channel.send_message(to=msg.sender_id, text=jb_report.to_user_message())
                return
            if jb_report.has_threats:
                logger.warning(f"[AdvancedGuard] 越狱线索（记录并继续）: {jb_report.threats[0].evidence!r}")
        except Exception as _ag_err:
            logger.debug(f"[AdvancedGuard] jailbreak check skipped: {_ag_err}")

        # 2. L1 硬规则门闸（< 5ms，纯代码）
        _l1_start = time.time()
        flags = self._l1_gate(msg.text)
        _l1_duration = time.time() - _l1_start

        metrics.observe_v15_l1_gate(flags["target"], _l1_duration)
        logger.info(f"[L1 Gate] target={flags['target']} duration={_l1_duration:.4f}s text={msg.text[:60]}")

        # 3. BLOCK
        if flags["target"] == "block":
            logger.warning(f"安全策略拦截: {msg.text}")
            await channel.send_message(
                to=msg.sender_id,
                text="⚠️ **[安全警示]** 您的请求包含敏感内容，已被系统拦截。",
            )
            return

        # 4. SCHEDULE
        if flags["target"] == "schedule":
            logger.info("判定为定时任务 (SCHEDULE Mode)")
            await self._handle_schedule(msg, channel)
            return

        # 5. 动态事件处理器
        dynamic_event_handler = self._build_event_handler(msg, channel, parent_event_handler)

        # 6. flag:reframe → Reframer 前置
        original_text = msg.text
        if flags.get("reframe"):
            from agents.reframer import Reframer
            reframe_mode = getattr(settings, "REFRAMER_MODEL_MODE", "local")
            reframe_name = getattr(settings, "REFRAMER_MODEL_NAME", "")
            reframe_llm = LLMClient(provider=reframe_mode, model=reframe_name)
            reframer = Reframer(reframe_llm)
            original_text = await reframer.reframe(msg.text, session_id=msg.session_id)
            logger.info(f"[Reframer] 重构后: {original_text[:80]}")

            # CLARIFICATION_NEEDED 处理
            _CLAR_PREFIX = "__CLARIFICATION_NEEDED__:"
            if original_text.startswith(_CLAR_PREFIX):
                try:
                    payload = json.loads(original_text[len(_CLAR_PREFIX):])
                    question = payload.get("question", "请问您想要哪个版本？")
                    options = payload.get("options", [])
                except Exception:
                    question = original_text[len(_CLAR_PREFIX):]
                    options = []
                lines = [f"❓ **需要确认一下：**\n\n{question}"]
                if options:
                    lines.append("\n**请从以下选项中选择：**")
                    for i, opt in enumerate(options, 1):
                        lines.append(f"  **{i}.** {opt}")
                    lines.append("\n请回复选项序号（如 `1`、`2`）或直接输入您想要的具体描述。")
                await channel.send_message(to=msg.sender_id, text="\n".join(lines))

                # Emit require_user_input lifecycle event for dashboard confirmCard popup
                display_options = list(options) if options else []
                if not any("其他" in opt or "other" in opt.lower() for opt in display_options):
                    display_options.append("其他（自定义输入）")
                try:
                    await dynamic_event_handler.emit_lifecycle(
                        session_key=msg.session_id,
                        client_run_id="clarify_" + msg.session_id,
                        status="require_user_input",
                        title="❓ 需要确认一下",
                        message=question,
                        options=display_options,
                        inputMode=True,
                    )
                except Exception as e:
                    logger.debug(f"[Path B] emit require_user_input failed: {e}")
                return

        # 7. SkillIndex 查询（L2，~20ms）
        try:
            skill_idx = get_skill_index(threshold=settings.SKILL_INDEX_THRESHOLD)
            skill_hint = skill_idx.query(original_text)
            if skill_hint:
                logger.info(f"[SkillIndex] hint={skill_hint.hint_skill} confidence={skill_hint.confidence}")
                metrics.observe_v15_skill_hint(skill_hint.hint_skill)
            skill_hint_dict = skill_hint.to_dict() if skill_hint else None
        except Exception as e:
            logger.debug(f"[SkillIndex] 查询失败（降级为无 hint）: {e}")
            skill_hint_dict = None

        # 8. PASS_TO_PLANNER → MissionRunner.run_with_decision()
        await self.mission_runner.run_with_decision(
            msg=msg,
            channel=channel,
            original_text=original_text,
            dynamic_event_handler=dynamic_event_handler,
            skill_hint=skill_hint_dict,
        )
        self._fire_and_forget(
            evolution_engine.on_turn_complete(msg.session_id, msg.text, "Mission Completed", [])
        )

    def _build_event_handler(self, msg: Any, channel: Any, parent_event_handler=None) -> AgentEventHandler:
        """构建动态事件处理器（任务进度推送）。
        parent_event_handler: 来自 process_run 的事件处理器，拥有 Gateway WS 广播能力。
        生命周期事件（如 require_user_input）通过它转发到前端弹窗。
        """
        async def channel_broadcast(event_dict: dict):
            stream = event_dict.get("stream")
            if stream == "assistant":
                status = event_dict["data"].get("status")
                text = event_dict["data"].get("text", "")
                if status == "done" and text:
                    await channel.send_message(to=msg.sender_id, text=text)
            elif stream == "lifecycle" and parent_event_handler:
                # Forward lifecycle events to Gateway WS client (for confirmCard popup)
                await parent_event_handler.emit(
                    run_id=event_dict.get("run_id", ""),
                    session_id=event_dict.get("session_id", msg.session_id),
                    stream="lifecycle",
                    data=event_dict.get("data", {}),
                )

        return AgentEventHandler(broadcast_callback=channel_broadcast)

    def _l1_gate(self, text: str) -> dict:
        """L1 硬规则门闸（< 5ms，纯代码，无 LLM）。
        Returns: {"target": "block"|"schedule"|"pass_to_planner", "reframe": bool}
        """
        t = text.lower().strip()

        # 安全词表 → BLOCK
        _BLOCK_KW = ["rm -rf /", "format c:", "del /f /s /q", "shutdown /s", "sudo rm -rf /"]
        if any(kw in t for kw in _BLOCK_KW):
            return {"target": "block", "reframe": False}

        # /slash 命令 → 直接分流
        if text.startswith("/schedule") or text.startswith("/定时"):
            return {"target": "schedule", "reframe": False}

        # 定时词表 → SCHEDULE
        _SCHEDULE_KW = ["每天", "每周", "每小时", "每分钟", "定时", "自动提醒", "提醒我",
                         "schedule", "every day", "every hour", "every week", "remind me at"]
        if any(kw in t for kw in _SCHEDULE_KW):
            return {"target": "schedule", "reframe": False}

        # 下载词表 → flag:reframe
        _DOWNLOAD_KW = ["下载", "download", "install", "安装", "迅雷", "磁力", "torrent", "bt下载",
                         "fetch", "retrieve", "save file", "get file"]
        if any(kw in t for kw in _DOWNLOAD_KW):
            return {"target": "pass_to_planner", "reframe": True}

        # 其他 → PASS_TO_PLANNER
        return {"target": "pass_to_planner", "reframe": False}

    @staticmethod
    def _fire_and_forget(coro):
        """启动后台任务，异常记录到日志。"""
        task = asyncio.create_task(coro)
        task.add_done_callback(
            lambda t: (
                logger.error(f"Background task failed: {t.exception()}")
                if not t.cancelled() and t.exception()
                else None
            )
        )

    async def _handle_schedule(self, msg: Any, channel: Any):
        """将自然语言定时任务解析后写入 .rooster/schedules.json。"""
        import re
        import uuid
        import datetime

        text = msg.text
        schedule_id = str(uuid.uuid4())[:8]

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
                logger.error(f"schedules.json 解析失败，已重置: {e}")
                schedules = []
        schedules.append(entry)
        tmp_path = schedules_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(schedules, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, schedules_path)
        except Exception as e:
            logger.error(f"schedules.json 写入失败: {e}")
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
