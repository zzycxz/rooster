import asyncio
import os
import uuid
import re
import json
import logging
from typing import List, Dict, Any, Optional, Callable
from pydantic import BaseModel, Field

from agents.prompt_builder import PromptBuilder, SystemPromptParams
from agents.llm_client import LLMClient
from agents.tool_dispatch import (
    extract_tool_calls_native,
    extract_tool_calls,
    execute_orchestrated_tool,
)
from gateway.event_handler import AgentEventHandler
from memory.manager import MemoryManager
from utils.audit import audit_manager
from utils.config import settings
from memory.visual_context import VisualContextBuffer
from models.vision_strategy import UIACache
from utils.exceptions import EscalateSignal

executor_logger = logging.getLogger(__name__)


async def _stream_with_chunk_timeout(generator, chunk_timeout: float):
    """Wait for each chunk from the generator with a timeout."""
    try:
        iterator = generator.__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(iterator.__anext__(), timeout=chunk_timeout)
                yield chunk
            except StopAsyncIteration:
                break
    except asyncio.TimeoutError:
        executor_logger.warning(f"⚠️ Stream chunk timeout after {chunk_timeout}s")
        raise


class PromptBleedFilter:
    """Intercepts LLM hallucination (prompt bleeding) before it reaches the UI."""
    def __init__(self):
        self.buffer = ""
        self.halted = False
        self.markers = ["【系统严格指令", "【系统提示】", "任务指令："]

    def process(self, chunk: str) -> str:
        if self.halted or not chunk:
            return ""
        self.buffer += chunk
        
        for m in self.markers:
            if m in self.buffer:
                self.halted = True
                idx = self.buffer.find(m)
                to_emit = self.buffer[:idx]
                self.buffer = ""
                return to_emit
                
        hold_len = 0
        for m in self.markers:
            for i in range(1, len(m)):
                if self.buffer.endswith(m[:i]):
                    hold_len = max(hold_len, i)
                    
        if hold_len == 0:
            to_emit = self.buffer
            self.buffer = ""
            return to_emit
        elif hold_len < len(self.buffer):
            to_emit = self.buffer[:-hold_len]
            self.buffer = self.buffer[-hold_len:]
            return to_emit
        else:
            return ""

    def flush(self) -> str:
        if self.halted:
            return ""
        to_emit = self.buffer
        self.buffer = ""
        return to_emit


class PromptBleedFilter:
    """Intercepts LLM hallucination (prompt bleeding) before it reaches the UI."""
    def __init__(self):
        self.buffer = ""
        self.halted = False
        self.markers = ["【系统严格指令", "【系统提示】", "任务指令："]

    def process(self, chunk: str) -> str:
        if self.halted or not chunk:
            return ""
        self.buffer += chunk
        
        for m in self.markers:
            if m in self.buffer:
                self.halted = True
                idx = self.buffer.find(m)
                to_emit = self.buffer[:idx]
                self.buffer = ""
                return to_emit
                
        hold_len = 0
        for m in self.markers:
            for i in range(1, len(m)):
                if self.buffer.endswith(m[:i]):
                    hold_len = max(hold_len, i)
                    
        if hold_len == 0:
            to_emit = self.buffer
            self.buffer = ""
            return to_emit
        elif hold_len < len(self.buffer):
            to_emit = self.buffer[:-hold_len]
            self.buffer = self.buffer[-hold_len:]
            return to_emit
        else:
            return ""

    def flush(self) -> str:
        if self.halted:
            return ""
        to_emit = self.buffer
        self.buffer = ""
        return to_emit


class AgentRunConfig(BaseModel):
    """Run configuration for a single agent turn."""

    session_id: str
    session_key: str
    agent_id: str
    prompt: str
    workspace_dir: str
    model: str = Field(
        default_factory=lambda: getattr(settings, "EXECUTOR_MODEL_NAME", None) or getattr(settings, "LOCAL_MODEL", "")
    )
    history: List[Dict[str, Any]] = []
    tool_registry: Optional[Any] = Field(default=None, exclude=True)
    max_steps: int = Field(default_factory=lambda: settings.AGENT_MAX_STEPS)
    allowed_paths: List[str] = []
    group_id: Optional[str] = None
    is_leaf: bool = False
    images: List[str] = []  # base64-encoded images for vision tasks
    policy_override: Optional[Any] = Field(
        None, exclude=True, description="SANDBOXED 子代理的权限策略覆盖，非 None 时替换全局 PermissionPolicy"
    )
    blackboard: Optional[Any] = Field(
        None, exclude=True, description="Per-mission 共享协调黑板（MissionBlackboard 实例），由 MissionRunner 注入"
    )
    spawn_depth: int = Field(default=0, description="Recursion depth limit for spawned subagents")

    @classmethod
    def for_subtask(cls, msg, session, subtask, tool_registry, group_id: str, allowed_paths=None) -> "AgentRunConfig":
        window = int(getattr(settings, "SESSION_HISTORY_WINDOW", 20))
        history = []
        for m in session.history[-window:]:
            if getattr(m, "images", None):
                vision_content = [{"type": "text", "text": m.content}]
                for b64 in m.images:
                    data_url = b64 if b64.startswith("data:") else f"data:image/png;base64,{b64}"
                    vision_content.append({"type": "image_url", "image_url": {"url": data_url}})
                history.append({"role": m.role, "content": vision_content})
            else:
                history.append({"role": m.role, "content": m.content})

        return cls(
            session_id=msg.session_id,
            session_key=msg.session_id,
            agent_id=f"executor_{subtask.id}",
            prompt=subtask.instruction,
            model=settings.EXECUTOR_MODEL_NAME,
            workspace_dir=os.path.abspath("."),
            tool_registry=tool_registry,
            allowed_paths=allowed_paths or [str(p) for p in settings.ALLOWED_PATHS],
            group_id=group_id,
            history=history,
        )


class AgentExecutor:
    """
    Agent execution engine — coordinates ReAct loop, tool dispatch, and report construction.
    Tool execution is delegated to agents.tool_dispatch.
    """

    def __init__(
        self,
        event_handler: AgentEventHandler,
        llm_client: LLMClient,
        *,
        tool_registry=None,
        orchestrator=None,
        memory_manager=None,
        prompt_builder=None,
    ):
        self.event_handler = event_handler
        self.llm_client = llm_client
        self.prompt_builder = prompt_builder or PromptBuilder(
            llm_client=llm_client, model=getattr(llm_client, "model", "")
        )
        self.orchestrator = orchestrator
        self.tool_registry = tool_registry
        self.memory_manager = memory_manager or MemoryManager()
        self.visual_buffer = VisualContextBuffer(settings.MEMORY_VISUAL_BUFFER_SIZE)
        self._uia_cache = UIACache(ttl=3.0)
        self._reflection_engine = None
        self._evolution_engine = None
        self._background_tasks: set = set()
        self._orchestrator_cache = {}

    def _fire_background(self, coro):
        """Create a background task and hold a strong reference to prevent GC collection."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    def _get_reflection_engine(self):
        if self._reflection_engine is None and self.tool_registry:
            from agents.reflection_engine import ReflectionEngine

            self._reflection_engine = ReflectionEngine(self.tool_registry)
        return self._reflection_engine

    def _get_evolution_engine(self):
        if self._evolution_engine is None:
            try:
                from evolution.engine import EvolutionEngine

                # 默认：进化引擎使用 mimo 模型
                try:
                    from models.factory import ModelFactory

                    _mimo_client = ModelFactory.get_client("mimo")
                    self._evolution_engine = EvolutionEngine(llm_client=_mimo_client)
                except Exception:
                    self._evolution_engine = EvolutionEngine(llm_client=self.llm_client)
            except Exception:
                pass
        return self._evolution_engine

    async def run(self, config: AgentRunConfig):
        """Execute a complete Agent Turn with ReAct loop."""
        run_id = str(uuid.uuid4())

        # 1. Initialize orchestrator (cached by session)
        if config.session_id not in self._orchestrator_cache:
            from agents.orchestrator import ToolOrchestrator

            self._orchestrator_cache[config.session_id] = ToolOrchestrator(
                workspace_dir=config.workspace_dir, allowed_paths=config.allowed_paths
            )
            # LRU cleanup to prevent memory leaks
            if len(self._orchestrator_cache) > 20:
                oldest_key = next(iter(self._orchestrator_cache))
                del self._orchestrator_cache[oldest_key]
        self.orchestrator = self._orchestrator_cache[config.session_id]
        session_history = config.history

        # --- FIX: Ensure prompt is in session_history to avoid losing it at step 2 ---
        # On step 1, separate the current prompt from history so compose_messages
        # can wrap it with a priority delimiter. On subsequent steps the prompt is
        # already part of history (as an assistant/tool exchange), so keep it inline.
        _current_user_input = ""
        _prompt_already_in_history = False
        if session_history and session_history[-1].get("role") == "user":
            if config.prompt and config.prompt.strip() in session_history[-1].get("content", ""):
                _prompt_already_in_history = True

        if not _prompt_already_in_history and config.prompt:
            _current_user_input = config.prompt

        # 2. Inject security guard and LLM capabilities into tool registry
        if config.tool_registry:
            if config.allowed_paths:
                from utils.security import PathGuard

                guard = PathGuard(config.allowed_paths)
                config.tool_registry.context["path_guard"] = guard

            config.tool_registry.context["llm_client"] = self.llm_client
            config.tool_registry.context["current_model"] = config.model
            config.tool_registry.context["session_id"] = config.session_id
            config.tool_registry.context["spawn_depth"] = config.spawn_depth

            # Only update context dictionary; no need to re-register tools entirely
            executor_logger.debug("Tool registry context updated.")

        # 3. Trigger audit worker cleanup
        audit_manager.trigger_cleanup()

        await self.event_handler.emit_lifecycle(session_key=config.session_key, client_run_id=run_id, status="running")

        step = 0
        _loop_exception = None

        # Stuck detection: track last N tool call signatures to detect loops
        _STUCK_THRESHOLD = getattr(settings, "AGENT_STUCK_THRESHOLD", 4)
        _recent_tool_calls: List[str] = []
        _stuck_break_count = 0

        # [Round 8] Track tool names used in previous steps for FC schema routing.
        # The router uses this list to keep recently-used tool schemas in scope even
        # when the current step's keywords no longer match their kit.
        _recently_used_tools: List[str] = []

        # Pre-compute full tool info for the system prompt (constant per run).
        # FC schemas are now computed per-step via the ToolRouter (see below).
        tools_info = config.tool_registry.get_all_tool_schemas() if config.tool_registry else None
        # Check if FC schemas are available so prompt can skip tool discovery instructions
        all_fc = config.tool_registry.get_all_fc_schemas() if config.tool_registry else []
        fc_tools_count = len(all_fc)

        # [V12 B3] Caching variables
        _cached_system_prompt = None
        _cached_ltm_hash = None
        _cached_fc_schemas = None
        _cached_recent_tools_hash = None

        while step < config.max_steps:
            step += 1

            # --- Blackboard: broadcast current step progress ---
            if config.blackboard:
                await config.blackboard.update_progress(
                    config.agent_id, "running", step=step, intent=config.prompt[:100]
                )

            # --- Phase 1: Pre-processing ---
            ltm_block = self.memory_manager.get_summary_for_prompt(query=config.prompt)

            # [V12 B3.1] System Prompt Caching
            import hashlib

            ltm_hash = hashlib.md5(ltm_block.encode()).hexdigest()[:8] if ltm_block else "empty"

            if _cached_system_prompt is None or ltm_hash != _cached_ltm_hash:
                params = SystemPromptParams(
                    agent_id=config.agent_id,
                    workspace_dir=config.workspace_dir,
                    tools_info=tools_info,
                    ltm_memory=ltm_block,
                    fc_tools_count=fc_tools_count,
                )
                system_prompt = self.prompt_builder.build_system_prompt(params)
                _cached_system_prompt = system_prompt
                _cached_ltm_hash = ltm_hash
            else:
                system_prompt = _cached_system_prompt

            context_limit = settings.AGENT_CONTEXT_LIMIT
            from utils.token_counter import count_message_tokens

            # Async compaction trigger at 0.6 threshold
            estimated_tokens = count_message_tokens(session_history)
            if estimated_tokens > context_limit * 0.6:
                try:
                    from memory.memory_compactor import schedule_memory_compaction

                    # 隐私：压缩对话历史使用本地模型，对话内容不出本机
                    # Expensive distillation runs off the executor hot path.
                    schedule_memory_compaction(self.memory_manager, config.session_id, session_history)
                except Exception as e:
                    executor_logger.warning(f"Compaction flush failed (degraded to pruning): {e}")

            # [V12 B2] 渐进式历史压缩 (Progressive History Compression)
            # 避免长任务末期突然发生硬裁剪导致失忆。每 10 步主动进行一次温和的中间层摘要。
            if step > 1 and step % 10 == 0 and len(session_history) > 12:
                # 前 2 条通常是初始指令，后 10 条是最近 5 步的高清上下文
                # 我们提炼中间的部分
                mid_msgs = session_history[2:-10]
                if mid_msgs:
                    summary = await self._summarize_mid_history(mid_msgs)
                    session_history = (
                        session_history[:2]
                        + [{"role": "user", "content": f"[系统提示：历史执行摘要]\n{summary}", "_internal": True}]
                        + session_history[-10:]
                    )

            session_history = await self._prune_history(session_history, max_total_tokens=context_limit)

            # --- Blackboard: inject shared context from peer agents ---
            # Inject at step 1 (initial context) and every 3 steps after (mid-execution awareness).
            # Only if there's actually something to share.
            # Inject BEFORE compose_messages so it appears before the user prompt, not after.
            blackboard_ctx = None
            if config.blackboard and (step == 1 or step % 3 == 0):
                shared_ctx = config.blackboard.get_context_snapshot(for_subtask=config.agent_id)
                if shared_ctx:
                    blackboard_ctx = shared_ctx

            messages = self.prompt_builder.compose_messages(
                system_prompt=system_prompt,
                history=session_history,
                user_input=_current_user_input,
                blackboard_context=blackboard_ctx,
            )

            # Only inject the delimiter on step 1; after that the prompt is
            # part of the ongoing ReAct exchange inside session_history.
            # Marked as _internal so it doesn't leak into the user-facing chat UI.
            if _current_user_input:
                session_history.append({"role": "user", "content": _current_user_input, "_internal": True})
                _current_user_input = ""

            # On the first step, if the request includes images, upgrade the user
            # message to OpenAI vision format: [{type:"text",...},{type:"image_url",...}]
            if step == 1 and config.images:
                # 隐私路由：检测图片是否含 PII，决定发原图还是描述 / Privacy routing
                _has_sensitive_images = False
                try:
                    from utils.privacy_router import get_privacy_router
                    from models.vision_analyzer import _quick_ocr

                    _router = get_privacy_router()
                    for b64 in config.images:
                        _ocr_text = ""
                        try:
                            _ocr_text = _quick_ocr(b64)
                        except Exception:
                            pass  # OCR 失败不卡用户 / OCR failure doesn't block
                        target, reason = _router.route_image(source_tool="executor_input", ocr_text=_ocr_text or None)
                        if target == "local":
                            _has_sensitive_images = True
                            executor_logger.info(f"[Privacy] 截图含敏感数据 ({reason})，不发原图")
                            break
                except Exception:
                    pass  # 路由失败不卡用户 / Router failure doesn't block

                for i in range(len(messages) - 1, -1, -1):
                    if messages[i]["role"] == "user":
                        text_content = messages[i]["content"]
                        if _has_sensitive_images:
                            # 含 PII：不注入 base64，仅附加提示 / Has PII: no base64, add hint
                            vision_content: List[Any] = [
                                {"type": "text", "text": text_content},
                                {"type": "text", "text": "(截图已因隐私保护脱敏，请基于上下文文字描述继续)"},
                            ]
                        else:
                            # 无 PII：正常注入 base64 / No PII: inject base64 normally
                            vision_content = [{"type": "text", "text": text_content}]
                            for b64 in config.images:
                                data_url = b64 if b64.startswith("data:") else f"data:image/png;base64,{b64}"
                                vision_content.append({"type": "image_url", "image_url": {"url": data_url}})
                        messages[i] = {"role": "user", "content": vision_content}
                        break

            audit_manager.log_step_detail(
                config.session_id, step, "prompt_full.md", json.dumps(messages, indent=2, ensure_ascii=False)
            )

            # --- [Round 8] Per-step FC schema routing ---
            # Select only the kit schemas relevant to this task context.
            # Falls back to full set when routing produces too few tools.
            if config.tool_registry:
                # [V12 B3.2] FC Schema Caching
                recent_tools_hash = "|".join(_recently_used_tools[-5:])
                if _cached_fc_schemas is None or recent_tools_hash != _cached_recent_tools_hash:
                    fc_schemas = config.tool_registry.get_fc_schemas_for_prompt(
                        prompt=config.prompt,
                        step=step,
                        recently_used=_recently_used_tools,
                    )
                    _cached_fc_schemas = fc_schemas
                    _cached_recent_tools_hash = recent_tools_hash
                else:
                    fc_schemas = _cached_fc_schemas
            else:
                fc_schemas = None

            # --- Phase 2: Model interaction with streaming ---
            full_content = ""
            full_reasoning_content = ""
            native_tool_calls = []
            try:
                # Add step info to the initial stream delta
                think_msg = f"\n> ⏳ **[执行回合 {step}]** 大脑思考中...\n" if step > 1 else ""
                await self.event_handler.emit_assistant_delta(
                    session_key=config.session_key, client_run_id=run_id, text=think_msg
                )

                _buffer = ""
                in_think = False
                bleed_filter = PromptBleedFilter()

                async def perform_chat():
                    nonlocal full_content, native_tool_calls, full_reasoning_content, _buffer, in_think, bleed_filter
                    chat_kwargs = {"model": config.model, "messages": messages}
                    if fc_schemas:
                        chat_kwargs["tools"] = fc_schemas
                        chat_kwargs["tool_choice"] = "auto"
                    async for delta in self.llm_client.chat_stream(**chat_kwargs):
                        if delta.reasoning_content:
                            full_reasoning_content += delta.reasoning_content
                            await self.event_handler.emit_think_delta(
                                session_key=config.session_key, client_run_id=run_id, text=delta.reasoning_content
                            )
                        if delta.tool_calls:
                            native_tool_calls = delta.tool_calls
                        elif delta.content:
                            _buffer += delta.content
                            while True:
                                if not in_think:
                                    start_idx = _buffer.find("<think>")
                                    if start_idx != -1:
                                        text_before = _buffer[:start_idx]
                                        if text_before:
                                            full_content += text_before
                                            await self.event_handler.emit_assistant_delta(
                                                session_key=config.session_key, client_run_id=run_id, text=text_before
                                            )
                                        in_think = True
                                        _buffer = _buffer[start_idx + 7 :]
                                    else:
                                        if len(_buffer) > 7:
                                            flush_len = len(_buffer) - 7
                                            text_to_flush = _buffer[:flush_len]
                                            full_content += text_to_flush
                                            safe_text = bleed_filter.process(text_to_flush)
                                            if safe_text:
                                                await self.event_handler.emit_assistant_delta(
                                                    session_key=config.session_key, client_run_id=run_id, text=safe_text
                                                )
                                            _buffer = _buffer[flush_len:]
                                        break
                                else:
                                    end_idx = _buffer.find("</think>")
                                    if end_idx != -1:
                                        think_text = _buffer[:end_idx]
                                        if think_text:
                                            full_reasoning_content += think_text
                                            await self.event_handler.emit_think_delta(
                                                session_key=config.session_key, client_run_id=run_id, text=think_text
                                            )
                                        in_think = False
                                        _buffer = _buffer[end_idx + 8 :]
                                    else:
                                        if len(_buffer) > 8:
                                            flush_len = len(_buffer) - 8
                                            think_to_flush = _buffer[:flush_len]
                                            full_reasoning_content += think_to_flush
                                            await self.event_handler.emit_think_delta(
                                                session_key=config.session_key,
                                                client_run_id=run_id,
                                                text=think_to_flush,
                                            )
                                            _buffer = _buffer[flush_len:]
                                        break

                    if _buffer:
                        if in_think:
                            full_reasoning_content += _buffer
                            await self.event_handler.emit_think_delta(
                                session_key=config.session_key, client_run_id=run_id, text=_buffer
                            )
                        else:
                            full_content += _buffer
                            await self.event_handler.emit_assistant_delta(
                                session_key=config.session_key, client_run_id=run_id, text=_buffer
                            )

                # --- Retry loop for network timeouts ---
                max_net_retries = 2
                _timeout_abort = False
                for net_retry in range(max_net_retries):
                    try:
                        if net_retry > 0:
                            await self.event_handler.emit_assistant_delta(
                                session_key=config.session_key,
                                client_run_id=run_id,
                                text="Thinking..." if step > 1 else "",
                            )
                            _buffer = ""
                            in_think = False
                            full_content = ""
                            full_reasoning_content = ""
                            native_tool_calls = []

                        await perform_chat()
                        final_flush = bleed_filter.flush()
                        if final_flush:
                            full_content += final_flush
                            await self.event_handler.emit_assistant_delta(
                                session_key=config.session_key, client_run_id=run_id, text=final_flush
                            )
                        break
                    except (asyncio.TimeoutError, TimeoutError) as e:
                        executor_logger.warning(
                            f"LLM Timeout (attempt {net_retry + 1}/{max_net_retries}). Retrying in 3s... {e}"
                        )
                        if net_retry == max_net_retries - 1:
                            fallback_msg_ui = "\n\n⚠️ [系统提示] 抱歉，大模型服务端接口响应持续超时。由于连续请求未收到回复，为防止死锁，本次任务已自动安全中止。请您检查网络或稍后重新下发指令。"
                            full_content = fallback_msg_ui + "\n[TASK_STATUS:FAILED]"
                            native_tool_calls = []
                            await self.event_handler.emit_assistant_delta(
                                session_key=config.session_key, client_run_id=run_id, text=fallback_msg_ui
                            )
                            _timeout_abort = True
                            break
                        await asyncio.sleep(3.0)

                executor_logger.debug(
                    f"Loop Step {step}: Sent {len(messages)} messages. Received {len(full_content)} chars."
                )
                executor_logger.info(f"Output: {len(full_content)} characters received")

                # Empty response retry
                if not full_content.strip() and not native_tool_calls:
                    empty_retry_max = 2
                    for empty_retry in range(empty_retry_max):
                        executor_logger.warning(f"Empty response, retry {empty_retry + 1}/{empty_retry_max}...")
                        await asyncio.sleep(2.0 * (empty_retry + 1))
                        full_content = ""
                        full_reasoning_content = ""
                        native_tool_calls = []
                        _buffer = ""
                        in_think = False
                        try:
                            await perform_chat()
                            final_flush = bleed_filter.flush()
                            if final_flush:
                                full_content += final_flush
                                await self.event_handler.emit_assistant_delta(
                                    session_key=config.session_key, client_run_id=run_id, text=final_flush
                                )
                        except Exception as e:
                            executor_logger.warning(f"Retry failed: {e}")
                        if full_content.strip():
                            break
                    if not full_content.strip():
                        executor_logger.error("LLM returned empty content after retries, aborting loop")
                        break

                # Strip thinking blocks
                if "<think" in full_content:
                    full_content = re.sub(r"<think.*?>.*?</think>", "", full_content, flags=re.DOTALL).strip()
                    
                # Strip prompt bleed
                bleed_markers = ["【系统严格指令", "【系统提示】", "任务指令："]
                for marker in bleed_markers:
                    if marker in full_content:
                        full_content = full_content[:full_content.find(marker)].strip()

                # Record history (FC protocol format)
                if native_tool_calls:
                    assistant_msg = {
                        "role": "assistant",
                        "content": full_content or None,
                        "tool_calls": native_tool_calls,
                    }
                    # MiMo thinking mode: reasoning_content 必须回传，即使为空也要保留字段
                    # MiMo thinking mode: reasoning_content must be echoed back, even if empty
                    assistant_msg["reasoning_content"] = full_reasoning_content or ""
                    session_history.append(assistant_msg)
                else:
                    assistant_msg = {"role": "assistant", "content": full_content}
                    if full_reasoning_content:
                        assistant_msg["reasoning_content"] = full_reasoning_content
                    session_history.append(assistant_msg)

                audit_manager.log_step_detail(config.session_id, step, "raw_llm_out.txt", full_content)

                # --- [歧义拦截门] CONFIRM_REQUIRED 检测 ---
                # 在执行任何工具之前，先检查 LLM 是否发出了歧义问询信号。
                # 若检测到，立即中断循环，把问题推送给用户，等待下一轮对话。
                _confirm_signal = self._extract_confirm_required(full_content)
                if _confirm_signal:
                    try:
                        # Ensure "Other" option exists for user free-text input
                        _confirm_options = list(_confirm_signal.get("options", []))
                        if not any("其他" in opt or "other" in opt.lower() for opt in _confirm_options):
                            _confirm_options.append("其他（自定义输入）")

                        executor_logger.error(f">>>>> [DEBUG] _confirm_signal detected! Options: {_confirm_options}")
                        _formatted_question = self._format_clarification_message(
                            _confirm_signal.get("question", ""),
                            _confirm_signal.get("options", []),
                        )
                        # 若 LLM 原始输出中已经包含了格式化的文字说明，则不重复发送 JSON 块
                        if not any(kw in full_content for kw in ["请选择", "请确认", "请问", "哪个", "哪一"]):
                            await self.event_handler.emit_assistant_delta(
                                session_key=config.session_key,
                                client_run_id=run_id,
                                text=_formatted_question,
                            )
                        # 触发前端弹窗 UI
                        executor_logger.error(">>>>> [DEBUG] Emitting require_user_input lifecycle event!")
                        await self.event_handler.emit_lifecycle(
                            session_key=config.session_key,
                            client_run_id=run_id,
                            status="require_user_input",
                            title="需要确认 (Confirmation Required)",
                            message=_confirm_signal.get("question", "请在下方选择或输入："),
                            options=_confirm_options,
                            inputMode=True
                        )
                        executor_logger.error(
                            f"[CONFIRM_REQUIRED] 歧义拦截门触发 (Step {step})，"
                            f"暂停执行，等待用户澄清：{_confirm_signal.get('question', '')[:80]}"
                        )
                    except Exception as confirm_exc:
                        executor_logger.error(f">>>>> [FATAL ERROR] Failed to emit require_user_input: {confirm_exc}", exc_info=True)
                    
                    break  # 不执行任何工具，退出 ReAct 循环，等待下一轮用户回复

                # --- Phase 3: Tool execution ---
                if native_tool_calls:
                    executor_logger.info(f"[FC] Native Function Calling: {len(native_tool_calls)} tool calls")
                    tool_calls = extract_tool_calls_native(native_tool_calls)
                else:
                    tool_calls = extract_tool_calls(full_content)

                # --- NEW: UI Feedback for micro-steps ---
                if tool_calls and self.event_handler:
                    tool_names = ", ".join([f"`{tc[0]}`" for tc in tool_calls])
                    ui_msg = f"\n> ⚙️ **[执行回合 {step}]** 正在调用底层工具: {tool_names}...\n\n"
                    # Emit to UI without creating a new message bubble (append to current assistant text)
                    await self.event_handler.emit_assistant_delta(
                        session_key=config.session_key, client_run_id=run_id, text=ui_msg
                    )

                # --- Stuck detection: break if same tool+args repeated consecutively ---
                if tool_calls:
                    _sig = "|".join(sorted(f"{n}:{json.dumps(a, sort_keys=True)[:120]}" for n, a in tool_calls))
                    _recent_tool_calls.append(_sig)
                    if len(_recent_tool_calls) > _STUCK_THRESHOLD:
                        _recent_tool_calls.pop(0)
                    if len(_recent_tool_calls) == _STUCK_THRESHOLD and len(set(_recent_tool_calls)) == 1:
                        _stuck_break_count += 1
                        if _stuck_break_count >= 3:
                            executor_logger.error(
                                f"[STUCK] Agent repeating identical tool calls "
                                f"{_STUCK_THRESHOLD * _stuck_break_count} times. Forcing ESCALATE."
                            )
                            raise EscalateSignal("智能体陷入死循环，连续重复调用相同工具失败，申请重规划。")
                        elif _stuck_break_count >= 2:
                            executor_logger.error(
                                f"[STUCK] Agent repeating identical tool calls "
                                f"{_STUCK_THRESHOLD * _stuck_break_count} times. Forcing exit."
                            )
                            session_history.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "【系统干预】你已经重复执行相同的操作 "
                                        f"{_STUCK_THRESHOLD * _stuck_break_count} 次。\n"
                                        "请立即停止调用工具！总结你尝试了什么以及为什么失败，然后给出你的最终答复。"
                                    ),
                                    "_internal": True,
                                }
                            )
                            tool_calls = []
                        else:
                            executor_logger.warning(
                                f"[STUCK] Detected repeated tool calls (round {_stuck_break_count}). "
                                "Injecting redirect prompt."
                            )
                            session_history.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "【系统警告】你似乎在重复执行相同的动作。\n"
                                        "请考虑换一种方式，或者现在就给出最终答复。"
                                    ),
                                    "_internal": True,
                                }
                            )
                    else:
                        if _stuck_break_count > 0:
                            _stuck_break_count = 0
                            _recent_tool_calls.clear()

                if not tool_calls:
                    executor_logger.info("No tool calls found. Breaking loop.")
                    # COMMIT synthesis for leaf nodes
                    if config.is_leaf and step > 1 and len(full_content.strip()) < 200 and not _timeout_abort:
                        executor_logger.info(
                            f"[COMMIT] Leaf task response too short ({len(full_content.strip())} chars). "
                            "Injecting synthesis pass."
                        )
                        synthesis_msg = (
                            "【系统提示】所有工具调用均已完成，结果如上所示。\n"
                            "请现在输出你的最终答复——直接且清晰地陈述结果，不要再调用任何工具。"
                        )
                        session_history.append({"role": "user", "content": synthesis_msg, "_internal": True})
                        synth_content = ""
                        synth_messages = self.prompt_builder.compose_messages(
                            system_prompt=system_prompt, history=session_history, user_input=""
                        )
                        try:
                            synth_bleed_filter = PromptBleedFilter()
                            async for delta in self.llm_client.chat_stream(model=config.model, messages=synth_messages):
                                if delta.content:
                                    safe_text = synth_bleed_filter.process(delta.content)
                                    synth_content += safe_text
                                    if safe_text:
                                        await self.event_handler.emit_assistant_delta(
                                            session_key=config.session_key, client_run_id=run_id, text=safe_text
                                        )
                            final_flush = synth_bleed_filter.flush()
                            if final_flush:
                                synth_content += final_flush
                                await self.event_handler.emit_assistant_delta(
                                    session_key=config.session_key, client_run_id=run_id, text=final_flush
                                )
                        except asyncio.TimeoutError:
                            executor_logger.warning("[COMMIT] Synthesis pass timed out (120s), using current content")
                        except Exception as e:
                            executor_logger.warning(f"[COMMIT] Synthesis pass failed: {e}")
                            
                        # Strip prompt bleed
                        for marker in ["【系统严格指令", "【系统提示】", "任务指令："]:
                            if marker in synth_content:
                                synth_content = synth_content[:synth_content.find(marker)].strip()
                                
                        if synth_content:
                            session_history.append({"role": "assistant", "content": synth_content})
                            executor_logger.info(f"[COMMIT] Synthesis complete: {len(synth_content)} chars")
                    break

                # Parallel tool execution via dispatch module
                # Each tool result is emitted immediately for Dashboard progress
                # 使用 index 标记保证结果顺序与 tool_calls 一致（FC 协议要求 tool_call_id 对应）
                async def _run_tool(idx: int, tool_name: str, args: dict) -> tuple:
                    """Execute a single tool, return (index, observation_string)."""
                    try:
                        obs = await execute_orchestrated_tool(
                            run_id,
                            config,
                            tool_name,
                            args,
                            step,
                            session_history,
                            orchestrator=self.orchestrator,
                            tool_registry=config.tool_registry,
                            event_handler=self.event_handler,
                            llm_client=self.llm_client,
                            uia_cache=self._uia_cache,
                            visual_buffer=self.visual_buffer,
                            memory_manager=self.memory_manager,
                            reflection_engine_getter=self._get_reflection_engine,
                            policy_override=config.policy_override,
                        )
                        # Emit individual tool result to Dashboard for real-time progress
                        if self.event_handler:
                            self._fire_background(
                                self.event_handler.emit_tool_response(
                                    session_key=config.session_key,
                                    client_run_id=run_id,
                                    tool_name=tool_name,
                                    response=obs[:200] if obs else "",
                                )
                            )
                        return (idx, obs)
                    except Exception as tool_exc:
                        # Individual tool failure does not crash other tools
                        executor_logger.warning(f"Tool execution failed: {tool_exc}")
                        return (idx, f"Tool execution error: {type(tool_exc).__name__}: {tool_exc}")

                indexed_results = await asyncio.gather(
                    *[_run_tool(i, name, args) for i, (name, args) in enumerate(tool_calls)]
                )
                # 按 index 排序，保证 observations[i] 对应 tool_calls[i]
                observations = [obs for _, obs in sorted(indexed_results, key=lambda x: x[0])]

                # [Round 8] Record which tools were called this step so the
                # ToolRouter can keep their kit schemas in scope next step.
                for tool_name, _ in tool_calls:
                    _recently_used_tools.append(tool_name)
                if len(_recently_used_tools) > 10:
                    _recently_used_tools = _recently_used_tools[-10:]

                # Tool output truncation
                SINGLE_TOOL_OUTPUT_LIMIT = settings.SINGLE_TOOL_OUTPUT_LIMIT
                truncated_obs = []
                for obs in observations:
                    if len(obs) > SINGLE_TOOL_OUTPUT_LIMIT:
                        executor_logger.info(f"Tool output truncated: {len(obs)} -> {SINGLE_TOOL_OUTPUT_LIMIT} chars")
                        suffix = f"\n... [Content truncated, original length {len(obs)} chars]"
                        truncated_obs.append(obs[: SINGLE_TOOL_OUTPUT_LIMIT - len(suffix)] + suffix)
                    else:
                        truncated_obs.append(obs)

                combined_obs = "\n".join(truncated_obs)

                # --- Blackboard: broadcast notable observations to peer agents ---
                # Only broadcast non-trivial, non-binary results (errors/successes with substance).
                if config.blackboard and combined_obs and len(combined_obs) > 80:
                    fact_key = f"{config.agent_id}_step{step}"
                    # [V12 B4.2] 置信度判定 (Confidence Labeling)
                    # 如果结果中包含失败、回退或正则表达式提取错误，则标为可疑 (tentative)
                    _obs_lower = combined_obs.lower()
                    status = (
                        "tentative"
                        if any(kw in _obs_lower for kw in ["error", "failed", "fallback", "未找到", "妥协"])
                        else "confirmed"
                    )

                    # Truncate to avoid blackboard bloat; peers only need the gist.
                    await config.blackboard.post_fact(
                        key=fact_key,
                        value=combined_obs[:600],
                        author=config.agent_id,
                        status=status,
                    )

                # Strip base64 image data — 所有 provider 都 strip，截图不发出本机
                if "[IMAGE_DATA:" in combined_obs:
                    combined_obs = re.sub(
                        r"\[IMAGE_DATA:.*?\]",
                        "(截图数据已脱敏 / Screenshot data redacted for privacy)",
                        combined_obs,
                        flags=re.DOTALL,
                    )

                # FC history format
                if native_tool_calls:
                    for i, obs in enumerate(truncated_obs):
                        tc_id = (
                            (native_tool_calls[i].get("id") or f"call_{i}")
                            if i < len(native_tool_calls)
                            else f"call_{i}"
                        )
                        session_history.append({"role": "tool", "tool_call_id": tc_id, "content": obs})
                else:
                    session_history.append({"role": "user", "content": combined_obs, "_internal": True})

                audit_manager.log_step_detail(config.session_id, step, "observation.txt", combined_obs)

                # Evolution engine callback (non-blocking)
                if settings.EVOLUTION_ENABLED:
                    evo = self._get_evolution_engine()
                    if evo:
                        self._fire_background(
                            evo.on_turn_complete(config.session_id, config.prompt, combined_obs[:500], [])
                        )

            except asyncio.CancelledError:
                executor_logger.error(f"Executor cancelled (Step {step}): SubTask timed out by mission_runner.")
                raise
            except Exception as e:
                executor_logger.error(f"Executor loop exception (Step {step}): {type(e).__name__}: {e}", exc_info=True)
                await self.event_handler.emit_error(
                    session_key=config.session_key,
                    client_run_id=run_id,
                    message=f"抱歉，系统在思考时遇到了小问题 (执行步骤 {step}). "
                    f"这通常是因为大模型接口超时或网络异常导致的，请稍后重试。\n\n"
                    f"技术细节: [{type(e).__name__}] {str(e)[:100]}",
                )
                _loop_exception = e
                break

        # Emergency final summary on max_steps
        if step >= config.max_steps:
            executor_logger.info(f"Reached max_steps ({config.max_steps}). Requesting emergency summary.")
            _task_hint = (config.prompt or "").split("\n\n任务指令：")[-1].strip()[:300]
            summary_prompt = (
                f"【系统紧急指令】已达到最大执行步数限制。请立即提供完整的最终答复。\n"
                f"绝不允许再调用任何工具。\n当前任务：{_task_hint}"
            )
            session_history.append({"role": "user", "content": summary_prompt, "_internal": True})
            system_prompt = self.prompt_builder.build_system_prompt(
                SystemPromptParams(agent_id=config.agent_id, workspace_dir=config.workspace_dir)
            )
            final_messages = self.prompt_builder.compose_messages(system_prompt, session_history, "")
            final_content = ""
            final_bleed_filter = PromptBleedFilter()
            try:
                async for delta in self.llm_client.chat_stream(model=config.model, messages=final_messages):
                    if delta.content:
                        safe_text = final_bleed_filter.process(delta.content)
                        final_content += safe_text
                        if safe_text:
                            await self.event_handler.emit_assistant_delta(
                                session_key=config.session_key, client_run_id=run_id, text=safe_text
                            )
                final_flush = final_bleed_filter.flush()
                if final_flush:
                    final_content += final_flush
                    await self.event_handler.emit_assistant_delta(
                        session_key=config.session_key, client_run_id=run_id, text=final_flush
                    )
            except asyncio.TimeoutError:
                executor_logger.warning("[EMERGENCY] Max-steps summary timed out (120s), using what we have")
            except Exception as e:
                executor_logger.warning(f"[EMERGENCY] Max-steps summary failed: {e}")
                
            # Strip prompt bleed
            for marker in ["【系统严格指令", "【系统提示】", "任务指令："]:
                if marker in final_content:
                    final_content = final_content[:final_content.find(marker)].strip()
                    
            if final_content:
                session_history.append({"role": "assistant", "content": final_content})

        # Done event
        # 修复: 当循环提前中断（如大模型报错）时，session_history[-1] 可能是 user 的 prompt。
        # 必须确保只有 assistant 角色才返回，避免将用户输入原样 Echo 给前端。
        last_content = ""
        if session_history and session_history[-1].get("role") == "assistant":
            last_content = session_history[-1].get("content") or ""

        await self.event_handler.emit_assistant_event(
            session_key=config.session_key,
            client_run_id=run_id,
            content=last_content,
            status="done",
        )

        # Blackboard: mark this agent as done
        if config.blackboard:
            await config.blackboard.update_progress(config.agent_id, "done", step=step)

        # Session history write-back
        try:
            from sessions.store import global_session_store

            session = global_session_store.get_or_create(config.session_id)
            for msg in session_history[len(session.history) :]:
                if msg.get("role") == "tool":
                    continue
                if msg.get("_internal"):
                    continue
                if isinstance(msg.get("content"), str):
                    session.add_message(msg["role"], msg["content"])
            global_session_store.save_session(config.session_id)
        except Exception as e:
            executor_logger.warning(f"Session history write-back failed: {e}")

        # Fire memory housekeeping in background — non-blocking, won't affect response latency.
        if self.memory_manager:
            self._fire_background(self.memory_manager.periodic_housekeeping())

        await self.event_handler.emit_lifecycle(session_key=config.session_key, client_run_id=run_id, status="done")

        if _loop_exception is not None:
            raise _loop_exception

        config.history = session_history
        return (session_history[-1].get("content") or "") if session_history else ""

    async def execute_subtask(
        self,
        subtask,
        config: AgentRunConfig,
        previous_observations: str = "",
        progress_callback: Optional[Callable] = None,
        is_leaf: bool = False,
    ) -> "Report":  # noqa: F821
        """Execute a single subtask and return a standardized Report."""
        from agents.protocol import Report
        import datetime
        from utils.system import sanitize_path_name

        config.history = list(config.history)

        # Inject phase info
        phase_lines = []
        if is_leaf:
            phase_lines.append(
                "【系统严格指令 - 交付阶段】\n"
                "这是当前任务的最终交付阶段。请在调用任何必要的工具后，直接且清晰地给出最终答案。\n"
                "重要：如果调用了多个工具，请优先参考“动作类工具”（如截图、点击、修改文件）的结果，即便“查询类工具”报错也不要掩盖动作的成功。\n"
                "在所有工具执行完毕后，你的最后一次回复【必须】包含实际结果——例如一个数字、一句话、一个文件路径等。\n"
                "警告：绝对不要输出无意义的套话，绝对不要反问用户寻求进一步指示。"
            )
        else:
            phase_lines.append(
                "【系统严格指令 - 执行阶段】\n"
                "这是一个中间执行步骤。请调用必要的工具执行操作，无需向用户进行总结或对话，系统会自动将结果传递给下游。"
            )

        # Resolve template variables
        import pathlib

        desktop_path = str(pathlib.Path.home() / "Desktop")
        workspace_path = os.path.abspath(config.workspace_dir or ".")
        output_dir = os.path.abspath(
            settings.OUTPUT_DIR
            if os.path.isabs(settings.OUTPUT_DIR)
            else os.path.join(workspace_path, settings.OUTPUT_DIR)
        )
        os.makedirs(output_dir, exist_ok=True)
        resolved_instruction = subtask.instruction.replace("{{desktop_path}}", desktop_path)
        resolved_instruction = resolved_instruction.replace("{{workspace}}", workspace_path)
        resolved_instruction = resolved_instruction.replace("{{output_dir}}", output_dir)

        prompt = "\n".join(phase_lines) + f"\n\n任务指令：{resolved_instruction}"
        if previous_observations:
            obs = previous_observations
            _MAX_PREV_OBS = 2000
            if len(obs) > _MAX_PREV_OBS:
                obs = obs[:_MAX_PREV_OBS] + f"\n... [上游输出截断，原长 {len(obs)} 字符]"
            prompt = f"{prompt}\n\n{obs}"

        config.prompt = prompt
        config.agent_id = f"executor_{subtask.id}"
        config.is_leaf = is_leaf

        if progress_callback:
            await progress_callback("start", subtask.id)

        try:
            initial_history_len = len(config.history)
            final_content = await self.run(config)
            session_history = config.history

            # Extract physical evidence
            safe_session_id = sanitize_path_name(config.session_id)
            evidence_dir = os.path.join(
                settings.ROOSTER_HOME, "evidence", datetime.datetime.now().strftime("%Y%m%d"), safe_session_id
            )
            os.makedirs(evidence_dir, exist_ok=True)

            found_snapshots = []
            found_artifacts = []
            seen_paths = set()

            for path_match in re.findall(r"\[RESULT_PATH:\s*(.+?)\]", final_content or ""):
                path = path_match.strip().strip('"').strip("'")
                if os.path.exists(path) and path not in seen_paths:
                    if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                        found_snapshots.append(path)
                    else:
                        found_artifacts.append(path)
                    seen_paths.add(path)

            for path_match in re.findall(r"\[IMAGE_SAVED:\s*(.+?)\]", final_content or ""):
                path = path_match.strip()
                if os.path.exists(path) and path not in seen_paths:
                    found_snapshots.append(path)
                    seen_paths.add(path)

            # Try structured JSON report
            try:
                json_match = re.search(r"(\{.*?\})", final_content or "", re.DOTALL)
                if json_match:
                    report_data = json.loads(json_match.group(1))
                    report_type = report_data.get("type", "FINAL_REPORT")
                    report_data.setdefault("subtask_id", subtask.id)

                    if report_type == "CONFIRM_REQUIRED":
                        return Report(**report_data)

                    if report_data.get("status") in ["REDIRECT", "BLOCKED"]:
                        report_data["type"] = "REPLAN_REQUEST"
                        report_data["inability_reason"] = f"Agent Signal: {report_data.get('status')}"
                        return Report(**report_data)

                    report_data["observation"] = final_content
                    report_data.setdefault("process_snapshots", found_snapshots)
                    report_data.setdefault("artifacts", found_artifacts)
                    return Report(**report_data)
            except Exception as e:
                executor_logger.debug(f"Structured Report construction failed, falling back: {e}")

            # Standard Report construction
            status = "SUCCESS"
            if "__ESCALATE_SIGNAL__" in (final_content or ""):
                status = "ESCALATE"
            else:
                # Prefer structured [TASK_STATUS:XXX] marker from LLM output
                status_match = re.search(r"\[TASK_STATUS:\s*(SUCCESS|FAILED|ESCALATE)\]", (final_content or ""))
                if status_match:
                    status = status_match.group(1)
                # Fallback: only match FAILED if it appears as a standalone declaration
                elif re.search(r"\b(?:TASK_FAILED|MISSION_FAILED)\b", (final_content or ""), re.IGNORECASE):
                    status = "FAILED"

            # ── Tool-level FAILED detection ──────────────────────────────────
            # 下载工具（movie_downloader, multimedia_download 等）在搜索失败时
            # 返回 "FAILED: no magnet link found for ..." 格式的字符串。
            # 此字符串仅存在于工具响应（role=tool）中，LLM 最终输出中可能只是
            # 复述了失败，但不会带上 [TASK_STATUS:FAILED] 标记。
            # 因此必须扫描本轮的工具输出，检测是否包含工具级别的 FAILED 信号。
            # Download tools (movie_downloader, multimedia_download, etc.) return
            # "FAILED: ..." strings when search fails. These strings only exist in
            # tool responses (role=tool), and the LLM's final answer may just restate
            # the failure without a [TASK_STATUS:FAILED] marker. We must scan tool
            # outputs for FAILED signals to properly set the report status.
            if status == "SUCCESS":
                for msg in session_history[initial_history_len:]:
                    if msg.get("role") == "tool":
                        content = msg.get("content", "") or ""
                        # Detect tool-level FAILED prefix (e.g. "FAILED: no magnet link found")
                        if content.strip().startswith("FAILED"):
                            executor_logger.warning(f"[Executor] Tool returned FAILED: {content[:120]}")
                            status = "FAILED"
                            break

            # Extract tool call traces from this round
            tool_call_trace = []
            for msg in session_history[initial_history_len:]:
                if "tool_calls" in msg and msg["tool_calls"]:
                    for tc in msg["tool_calls"]:
                        func = tc.get("function", {})
                        fname = func.get("name", "unknown")
                        fargs = func.get("arguments", "{}")
                        tool_call_trace.append(f"{fname}({fargs[:60]}...)")

                xml_tool_names = re.findall(r'<tool_response name="(\w+)">', msg.get("content", "") or "")
                for tname in xml_tool_names:
                    tool_call_trace.append(tname)
                xml_outputs = re.findall(
                    r'<tool_response name="\w+">\s*(.*?)</tool_response>', msg.get("content", "") or "", re.DOTALL
                )
                for out in xml_outputs:
                    tool_call_trace.append(f"-> {out[:200].strip()}")

            # Build evidence summary from tool outputs
            tool_outputs_for_summary = []
            for msg in session_history[initial_history_len:]:
                if msg.get("role") in ("user", "tool"):
                    content = msg.get("content", "") or ""
                    if msg.get("role") == "tool":
                        if content.strip():
                            tool_outputs_for_summary.append(content.strip()[:500])
                    else:
                        raw_outputs = re.findall(r"<tool_response[^>]*>\s*(.*?)</tool_response>", content, re.DOTALL)
                        if raw_outputs:
                            for out in raw_outputs:
                                tool_outputs_for_summary.append(out.strip()[:500])
                        elif content.strip() and "<" not in content:
                            tool_outputs_for_summary.append(content.strip()[:500])

            evidence_summary = f"子任务 {subtask.id} 执行完成。"
            if tool_outputs_for_summary:
                evidence_summary += " 工具执行结果:\n" + "\n---\n".join(tool_outputs_for_summary[:3])
            elif found_artifacts:
                evidence_summary += f" 产出文件: {', '.join(os.path.basename(a) for a in found_artifacts)}。"
            if found_snapshots:
                evidence_summary += f" 截图: {len(found_snapshots)} 张。"
            if not tool_call_trace and not tool_outputs_for_summary:
                evidence_summary += f" LLM 输出: {(final_content or '')[:300]}"

            # COMMIT observation fallback
            if is_leaf and tool_outputs_for_summary and len((final_content or "").strip()) < 200:
                observation_text = "\n\n".join(tool_outputs_for_summary[:3])
                executor_logger.info(
                    f"[COMMIT] Observation overridden with tool output "
                    f"(final_content was {len((final_content or '').strip())} chars)"
                )
            else:
                observation_text = self._clean_thought_chatter(final_content or "任务已完成，无文本输出。")

            report = Report(
                subtask_id=subtask.id,
                status=status,
                observation=observation_text,
                process_snapshots=found_snapshots,
                artifacts=found_artifacts,
                evidence={
                    "summary": evidence_summary,
                    "tool_call_trace": tool_call_trace,
                    "table_data": tool_outputs_for_summary[0][:500] if tool_outputs_for_summary else "",
                    "observation": observation_text,
                },
                evidence_path=found_artifacts[0]
                if found_artifacts
                else (found_snapshots[0] if found_snapshots else None),
            )

            if progress_callback:
                await progress_callback("complete", subtask.id, status)

            return report

        except Exception as e:
            executor_logger.error(f"Subtask {subtask.id} failed: {e}")

            if progress_callback:
                await progress_callback("error", subtask.id, str(e))

            failure_status = "FAILED"
            if subtask.on_failure == "REPLAN":
                failure_status = "ESCALATE"
            elif subtask.on_failure == "RETRY":
                failure_status = "RETRY"
            elif subtask.on_failure == "ABORT":
                failure_status = "ABORT"

            return Report(
                subtask_id=subtask.id,
                status=failure_status,
                evidence={"error": str(e)},
                failure_code="EXECUTOR_ERROR",
                observation=f"执行失败: {str(e)}",
                inability_reason=str(e) if subtask.on_failure == "REPLAN" else None,
            )

    # --- History and text utilities ---

    async def _prune_history(
        self, history: List[Dict[str, str]], max_total_tokens: int = 16000
    ) -> List[Dict[str, str]]:
        if not history:
            return []
        if len(history) <= 4:
            return history

        from utils.token_counter import count_message_tokens

        history_allowance = int(max_total_tokens * settings.CONTEXT_RATIO_HISTORY)
        current_tokens = count_message_tokens(history)
        if current_tokens <= history_allowance:
            return history

        pruned = []
        # Fallback character estimation for truncation if needed
        obs_cap_chars = int(max_total_tokens * settings.CONTEXT_RATIO_OBS * 3.5)
        for i, msg in enumerate(history):
            content = msg.get("content") or ""
            role = msg.get("role", "user")

            # Truncate both user (tool outputs) and assistant (thoughts/reasoning)
            if role in ("user", "assistant") and i > 0 and i < len(history) - 2:
                if "【视觉分析报告】" in content:
                    visual_cap = obs_cap_chars * 2
                    if len(content) > visual_cap:
                        content = (
                            content[:visual_cap]
                            + f"\n... [Visual report auto-truncated, original length {len(content)} chars] ..."
                        )
                elif len(content) > obs_cap_chars:
                    content = (
                        content[:obs_cap_chars]
                        + f"\n... [Content auto-truncated, original length {len(content)} chars] ..."
                    )

            entry = {k: v for k, v in msg.items()}
            entry["content"] = content

            # Also truncate reasoning_content for assistant
            if role == "assistant" and entry.get("reasoning_content"):
                r_content = entry["reasoning_content"]
                if len(r_content) > obs_cap_chars:
                    entry["reasoning_content"] = (
                        r_content[:obs_cap_chars]
                        + f"\n... [Reasoning auto-truncated, original length {len(r_content)} chars] ..."
                    )

            pruned.append(entry)

        total_tokens = count_message_tokens(pruned)
        if total_tokens > max_total_tokens and len(pruned) > 8:
            # V12: 退化情况下的硬裁剪（如果前面的渐进式压缩依然没能控制住）
            # 这种情况极少发生，因为中间层已经被摘要化了
            mid_msgs = pruned[1:-10]
            summary = await self._summarize_mid_history(mid_msgs) if mid_msgs else "中间对话已压缩以节约上下文"

            pruned = [pruned[0]] + pruned[-10:]
            pruned.insert(
                1,
                {
                    "role": "user",
                    "content": f"[系统提示：超限硬裁剪并生成摘要]\n{summary}",
                    "_internal": True,
                },
            )
        return pruned

    async def _summarize_mid_history(self, messages: List[Dict[str, str]]) -> str:
        """[V12 B2] 渐进式挤水：利用快模型或规则提取中间轮次摘要"""
        # 兜底规则提取
        rule_summary_lines = []
        for m in messages:
            content = m.get("content") or ""
            role = m.get("role", "unknown")
            if role == "tool" and len(content) > 200:
                content = content[:200] + "..."
            elif role == "assistant" and len(content) > 100:
                content = content[:100] + "..."
            rule_summary_lines.append(f"- [{role}] {content}")

        rule_summary = "\n".join(rule_summary_lines)
        if len(rule_summary) > 2000:
            rule_summary = rule_summary[:2000] + "...\n(truncated)"

        # 尝试调用小模型做快速摘要
        try:
            # 优先使用配置中的 FAST_MODEL，如果未配置则降级
            fast_provider = getattr(settings, "FAST_MODEL_PROVIDER", None)
            fast_model = getattr(settings, "FAST_MODEL_NAME", None)
            
            executor_logger.info("⏳ 触发 [B2] 渐进式中间层摘要 (Progressive History Compression)...")
            prompt = (
                "请将以下大模型的历史执行记录压缩为一段 500 字以内的执行摘要。\n"
                "你只需要提取核心逻辑，不要赘述废话。\n"
                "【必须保留】: 关键工具的调用结果、取得的核心数据、已确认的失败尝试。\n"
                "【可以省略】: 啰嗦的思考过程、重复循环的重试、毫无意义的文本截断提示。\n\n"
                f"原始记录：\n{rule_summary}"
            )
            
            if fast_provider and fast_model:
                from agents.llm_client import LLMClient
                fast_client = LLMClient(provider=fast_provider, model=fast_model, lightweight=True)
                resp = await fast_client.chat_non_stream(
                    messages=[{"role": "user", "content": prompt}]
                )
            elif hasattr(self.llm_client, "chat_non_stream"):
                # 安全退回：不要随意注入不兼容的 model_name
                resp = await self.llm_client.chat_non_stream(
                    messages=[{"role": "user", "content": prompt}]
                )
            else:
                resp = None

            if resp and resp.content:
                return resp.content[:600]
        except Exception as e:
            executor_logger.debug(f"LLM 渐进式摘要压缩失败，退化为规则提取: {e}")

        return rule_summary[:600]

    def _clean_thought_chatter(self, text: str) -> str:
        # <think> tags are handled during stream; remove fragile regex chatter cleaning
        return text.strip()

    # ----------------------------------------------------------------
    # Clarification Gate helpers
    # ----------------------------------------------------------------

    def _extract_confirm_required(self, content: str) -> Optional[Dict[str, Any]]:
        """从 LLM 输出中提取 CONFIRM_REQUIRED 信号块。

        LLM 可能在纯文本中夹杂一个 JSON 块，也可能直接输出纯 JSON。
        本方法使用贪心 JSON 扫描，而非严格的格式匹配，以提高鲁棒性。
        同时，当模型未输出 JSON，但输出特定的 markdown 选择题格式时（如包含选项或 FAILED 并询问），
        也能进行兜底提取。
        """
        if not content:
            return None
            
        # 兜底解析：如果输出中包含 "回复 A 或 B" 这种明确的选项或包含 Markdown 表格或列表
        if "CONFIRM_REQUIRED" not in content:
            if "请确认" in content or "请选择" in content or "A 或 B" in content or "哪一部" in content:
                # 尝试抓取 Markdown 表格或有序列表里的选项
                options = []
                lines = content.split("\n")
                for line in lines:
                    line_s = line.strip()
                    # 匹配表格
                    if line_s.startswith("|") and not "---" in line_s and not "选项" in line_s:
                        parts = [p.strip() for p in line_s.split("|") if p.strip()]
                        if len(parts) >= 2:
                            options.append(" ".join(parts[:3]))
                    # 匹配有序列表 (1. 选项A)
                    elif re.match(r"^\d+\.\s+", line_s):
                        # 提取选项内容，去掉前面的数字和点
                        opt_text = re.sub(r"^\d+\.\s*", "", line_s)
                        if len(opt_text) > 2 and len(opt_text) < 100:
                            options.append(opt_text)
                            
                if options:
                    return {
                        "type": "CONFIRM_REQUIRED",
                        "question": "检测到多个可能匹配的结果，请确认：",
                        "options": options
                    }
            return None

        try:
            # 先尝试从 ```json ... ``` 代码块中提取
            fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", content)
            candidates = fenced if fenced else [content]
            for candidate in candidates:
                # 在候选段中找所有 { ... } 块（贪心，从最外层括号开始）
                depth = 0
                start = -1
                for i, ch in enumerate(candidate):
                    if ch == "{":
                        if depth == 0:
                            start = i
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0 and start != -1:
                            blob = candidate[start : i + 1]
                            try:
                                data = json.loads(blob)
                                if (
                                    isinstance(data, dict)
                                    and data.get("type") == "CONFIRM_REQUIRED"
                                    and data.get("question")
                                ):
                                    return data
                            except json.JSONDecodeError:
                                pass
                            start = -1
        except Exception as exc:
            executor_logger.debug(f"[CONFIRM_REQUIRED] 信号提取失败 (忽略): {exc}")
        return None

    def _format_clarification_message(self, question: str, options: list) -> str:
        """将 CONFIRM_REQUIRED 信号格式化为用户友好的选项消息。

        格式设计原则：
        - 问题放在最前面，让用户一眼知道需要做什么
        - 选项编号清晰，用户回复数字即可
        - 末尾提示交互方式
        """
        lines = [f"❓ **需要您确认一下：**\n\n{question}"]
        if options:
            lines.append("\n**请从以下选项中选择：**")
            for i, opt in enumerate(options, 1):
                lines.append(f"  **{i}.** {opt}")
            lines.append("\n请回复选项序号（如 `1`、`2`）或直接输入您想要的具体描述。")
        return "\n".join(lines)
