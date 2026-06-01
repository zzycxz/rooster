# src/agents/runners/mission_runner.py
"""多步任务编排器（COMPLEX / DIRECT / REFRAME 模式）"""  # Multi-step task orchestrator (COMPLEX / DIRECT / REFRAME mode)

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from typing import Any, Dict, List, Optional, Set

from agents.auditor import Auditor
from agents.executor import AgentExecutor, AgentRunConfig
from agents.llm_client import LLMClient
from agents.mission_tactician import MissionTactician
from agents.protocol import MissionPlan, SubTask, AuditVerdictType, Report
from agents.strategist import Strategist
from gateway.event_handler import AgentEventHandler
from memory.manager import MemoryManager
from memory.models import MemoryFactType
from agents.prompt_builder import PromptBuilder
from sessions.store import global_session_store
from utils.audit.archiver import VaultArchiver
from utils.config import settings
from utils.security import state_guard
from utils.observation import summarize_dependency_observation

logger = logging.getLogger(__name__)

_CHECKPOINT_DIR = os.path.join(".rooster", "checkpoints")


class MissionRunner:
    """处理 COMPLEX 模式的多步任务编排。"""

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry,
        event_handler: AgentEventHandler,
        memory_manager: MemoryManager,
        prompt_builder: PromptBuilder,
        orchestrator=None,
    ):
        self.tool_registry = tool_registry
        self.event_handler = event_handler
        self.memory_manager = memory_manager
        self.prompt_builder = prompt_builder
        self.orchestrator = orchestrator
        self.tactician = MissionTactician()
        self._semaphore = asyncio.Semaphore(settings.MAX_PARALLEL_SUBTASKS)

        # 各角色 LLM 客户端
        # LLM clients per role
        self.strat_llm = LLMClient(provider=settings.STRATEGIST_MODEL_MODE, model=settings.STRATEGIST_MODEL_NAME)
        self.audit_llm = LLMClient(provider=settings.AUDITOR_MODEL_MODE, model=settings.AUDITOR_MODEL_NAME)
        self.exec_llm = LLMClient(provider=settings.EXECUTOR_MODEL_MODE, model=settings.EXECUTOR_MODEL_NAME)

        self.strategist = Strategist(self.strat_llm, memory_manager=self.memory_manager, tool_registry=self.tool_registry)
        self.auditor = Auditor(self.audit_llm)

    # ------------------------------------------------------------------
    # Checkpoint helpers — persist task progress so long tasks can resume
    # after a crash.  Scoped to COMPLEX mode only (MissionRunner).
    # Triggered by: CHECKPOINT_ENABLED=true (default false).
    # ------------------------------------------------------------------

    @staticmethod
    def _trim_retry_history(history: list, max_chars: int) -> list:
        """Return the most-recent suffix of *history* whose total content length
        fits within *max_chars*.  Prevents REMAND retry cycles from inflating the
        Executor context window unboundedly when tool outputs are large."""
        total = 0
        kept: list = []
        for msg in reversed(history):
            content = msg.get("content") or ""
            if isinstance(content, list):
                # vision messages: sum text parts
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            total += len(content)
            if total > max_chars:
                break
            kept.insert(0, msg)
        return kept

    def _checkpoint_path(self, session_id: str, goal: str) -> str:
        goal_hash = hashlib.md5(goal.encode()).hexdigest()[:8]
        os.makedirs(_CHECKPOINT_DIR, exist_ok=True)
        return os.path.join(_CHECKPOINT_DIR, f"{session_id[:16]}_{goal_hash}.json")

    def _save_checkpoint(
        self, session_id: str, plan: MissionPlan, completed_ids: Set[str], reports: Dict[str, Report],
        subtask_metrics: Optional[Dict[str, dict]] = None,
    ) -> None:
        if not getattr(settings, "CHECKPOINT_ENABLED", False):
            return
        try:
            path = self._checkpoint_path(session_id, plan.goal)
            data = {
                "task_id": plan.task_id,
                "goal": plan.goal,
                "original_goal": plan.original_goal,
                "replan_count": plan.replan_count,
                "subtasks": [st.model_dump() for st in plan.subtasks],
                "completed_task_ids": list(completed_ids),
                "executed_tasks": {k: v.model_dump(mode="json") for k, v in reports.items()},
                "blockers": plan.blockers,
                "deliverables": plan.deliverables,
                "feasibility_note": plan.feasibility_note,
                "subtask_metrics": subtask_metrics or {},
                "saved_at": time.time(),
            }
            # 原子写入：write-to-tmp → fsync → rename（进程崩溃时不会留下截断的 JSON）
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)  # 原子操作（Windows os.replace 也保证原子）
            logger.debug(f"Checkpoint saved: {path} ({len(completed_ids)} subtasks done)")
        except Exception as e:
            logger.warning(f"Checkpoint save failed: {e}")

    def _load_checkpoint(self, session_id: str, goal: str) -> Optional[dict]:
        if not getattr(settings, "CHECKPOINT_ENABLED", False):
            return None
        path = self._checkpoint_path(session_id, goal)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            # Expire checkpoints older than 24 hours
            if time.time() - data.get("saved_at", 0) > 86400:
                os.remove(path)
                # 清理可能残留的 .tmp 文件
                tmp_path = path + ".tmp"
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                logger.info(f"Checkpoint expired and removed: {path}")
                return None
            return data
        except json.JSONDecodeError as e:
            # checkpoint 文件损坏（可能是上次写入时崩溃），尝试从 .tmp 恢复
            logger.warning(f"Checkpoint corrupt ({e}), attempting .tmp recovery")
            tmp_path = path + ".tmp"
            if os.path.exists(tmp_path):
                try:
                    os.replace(tmp_path, path)
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    logger.info(f"Checkpoint recovered from .tmp: {path}")
                    return data
                except Exception:
                    pass
            # 恢复也失败，删除损坏文件
            for p in (path, tmp_path):
                if os.path.exists(p):
                    os.remove(p)
            return None

    def _write_heartbeat(self, session_id: str, task_id: str, running_subtask: str, completed: int, total: int) -> None:
        """[V12 B5.1] 写入监控心跳，供 guardian 守护线程轮询"""
        heartbeat_path = os.path.join(_CHECKPOINT_DIR, f"{session_id}.heartbeat")
        try:
            os.makedirs(_CHECKPOINT_DIR, exist_ok=True)
            with open(heartbeat_path, "w", encoding="utf-8") as f:
                json.dump({
                    "task_id": task_id,
                    "running_subtask": running_subtask,
                    "completed": completed,
                    "total": total,
                    "timestamp": time.time(),
                }, f)
        except Exception as e:
            logger.debug(f"Failed to write heartbeat: {e}")

    def _clear_checkpoint(self, session_id: str, goal: str) -> None:
        if not getattr(settings, "CHECKPOINT_ENABLED", False):
            return
        path = self._checkpoint_path(session_id, goal)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # User confirmation gate for requires_confirm subtasks
    # ------------------------------------------------------------------

    async def _request_user_confirmation(
        self,
        session_id: str,
        sender_id: str,
        channel: Any,
        st: SubTask,
        event_handler: Any = None,
    ) -> bool:
        """Ask user for confirmation. Returns True if confirmed, False on timeout/decline."""
        from gateway.run_manager import global_run_manager

        run_id = global_run_manager.session_to_run.get(session_id)
        run = global_run_manager.active_runs.get(run_id) if run_id else None

        preview = st.instruction[:200]
        await channel.send_message(
            to=sender_id,
            text=(
                f"⚠️ **[需要确认]** 子任务 `{st.id}` 需要人工确认后执行：\n"
                f"> {preview}\n\n"
                f"请回复 **确认** 继续执行，或发送其他消息取消。"
            ),
        )

        if event_handler:
            try:
                await event_handler.emit(
                    run_id="system_confirm",
                    session_id=session_id,
                    stream="lifecycle",
                    data={"status": "require_user_input", "subtask_id": st.id},
                )
            except Exception:
                pass

        if not run:
            logger.warning(f"No active run found for session {session_id} to block on confirmation.")
            return False

        run.status = "waiting_for_input"
        run.input_event.clear()
        run.input_data = None

        from utils.config import settings

        CONFIRM_TIMEOUT = getattr(settings, "WAIT_CONFIRM_TIMEOUT", 300)
        try:
            await asyncio.wait_for(run.input_event.wait(), timeout=CONFIRM_TIMEOUT)
            user_input = run.input_data or ""
            text_lower = user_input.strip().lower()
            confirmed = text_lower in ("确认", "confirm", "yes", "同意", "ok", "批准", "approve")

            if confirmed:
                await channel.send_message(to=sender_id, text=f"✅ 已确认，继续执行 `{st.id}`...")
                return True
            else:
                await channel.send_message(to=sender_id, text=f"❌ 子任务 `{st.id}` 已取消。")
                return False
        except asyncio.TimeoutError:
            await channel.send_message(
                to=sender_id,
                text=f"⏰ 确认超时（{CONFIRM_TIMEOUT}s），子任务 `{st.id}` 已跳过。",
            )
            return False
        finally:
            if run.status == "waiting_for_input":
                run.status = "running"
            run.input_event.clear()
            run.input_data = None

    async def run(
        self, msg: Any, channel: Any, reframed_text: str, dynamic_event_handler: AgentEventHandler, is_direct: bool = False
    ) -> None:
        """执行多步任务编排。"""  # Execute multi-step task orchestration
        archiver = VaultArchiver(settings.EVIDENCE_ROOT, msg.session_id)
        session = global_session_store.get_or_create(msg.session_id)

        # ------------------------------------------------------------------
        # Checkpoint restore: if a prior run was interrupted, pick up where
        # it left off instead of re-planning from scratch.
        # ------------------------------------------------------------------
        checkpoint = self._load_checkpoint(msg.session_id, reframed_text)

        if checkpoint:
            await channel.send_message(
                to=msg.sender_id,
                text=(
                    f"⏩ [断点续跑] 检测到上次未完成的任务，正在从第 "
                    f"{len(checkpoint['completed_task_ids'])}/{len(checkpoint['subtasks'])} 步恢复..."
                ),
            )
            current_mission_plan = MissionPlan(
                task_id=checkpoint["task_id"],
                goal=reframed_text,
                original_goal=checkpoint.get("original_goal", reframed_text),
                subtasks=[SubTask(**st) for st in checkpoint["subtasks"]],
                replan_count=checkpoint.get("replan_count", 0),
                blockers=checkpoint.get("blockers", []),
                deliverables=checkpoint.get("deliverables", []),
                feasibility_note=checkpoint.get("feasibility_note", ""),
            )
            subtask_list = current_mission_plan.subtasks
            executed_tasks: Dict[str, Report] = {k: Report(**v) for k, v in checkpoint["executed_tasks"].items()}
            completed_task_ids: Set[str] = set(checkpoint["completed_task_ids"])
            _subtask_metrics: Dict[str, dict] = checkpoint.get("subtask_metrics", {})
        else:
            # 1. 流式规划
            # 1. Streaming planning
            current_mission_plan = MissionPlan(
                task_id=f"T{int(time.time())}",
                goal=reframed_text,
                original_goal=reframed_text,
                subtasks=[],
            )
            subtask_list = []

            # Emit strategist_start lifecycle event for Dashboard pipeline
            await self.event_handler.emit_lifecycle(
                session_key=msg.session_id,
                client_run_id=current_mission_plan.task_id,
                status="strategist_start",
            )

            images = []
            if session.history and getattr(session.history[-1], "images", None):
                images = session.history[-1].images

            if is_direct:
                import uuid
                st = SubTask(
                    id=f"ST_{uuid.uuid4().hex[:6]}",
                    instruction=reframed_text,
                    domain="SYSTEM",   # generic domain — lets tool_router pick the right kit
                    tool="generic_tool",
                )
                async def mock_plan_stream():
                    yield st
                plan_iterator = mock_plan_stream()
            else:
                plan_iterator = self.strategist.plan_stream(reframed_text, images=images)

            async def _consume_plan():
                nonlocal subtask_list
                async for subtask in plan_iterator:
                    await channel.send_message(
                        to=msg.sender_id,
                        text=f"🧠 [规划中] {subtask.id}: {subtask.instruction}",
                    )
                    subtask_list.append(subtask)

            await asyncio.wait_for(
                _consume_plan(),
                timeout=getattr(settings, "STRATEGIST_LLM_TIMEOUT", 300),
            )

            # [V11] 从 plan_stream 流式输出的 full_content 中提取顶层元数据
            # 无需再调一次 plan()，省掉一次完整 LLM 调用
            if subtask_list and not is_direct:
                meta = getattr(self.strategist, "_last_plan_meta", None)
                if meta:
                    current_mission_plan.blockers = meta.get("blockers", [])
                    current_mission_plan.deliverables = meta.get("deliverables", [])
                    current_mission_plan.feasibility_note = meta.get("feasibility_note", "")
                else:
                    logger.warning("⚠️ plan_stream 未返回元数据，使用空默认值")

            current_mission_plan.subtasks = subtask_list
            executed_tasks: Dict[str, Report] = {}
            completed_task_ids: Set[str] = set()
            _subtask_metrics: Dict[str, dict] = {}

        # 展示阻塞项 / 可行性说明 / 交付物（仅首次规划时展示，checkpoint 恢复跳过）
        if not checkpoint:
            if current_mission_plan.blockers:
                blocker_text = "\n".join(
                    f"⚠️ {b.get('type', 'UNKNOWN')}: {b.get('description', '')} → {b.get('resolution', '请自行解决')}"
                    for b in current_mission_plan.blockers
                )
                await channel.send_message(to=msg.sender_id, text=f"🚨 前置阻塞项:\n{blocker_text}")
            if current_mission_plan.feasibility_note:
                await channel.send_message(
                    to=msg.sender_id, text=f"📊 可行性说明: {current_mission_plan.feasibility_note}"
                )
            if current_mission_plan.deliverables:
                deliverable_text = ", ".join(current_mission_plan.deliverables)
                await channel.send_message(to=msg.sender_id, text=f"📦 预期交付物: {deliverable_text}")

        total_tasks = len(current_mission_plan.subtasks)
        logger.info(f"规划完成，共 {total_tasks} 个子任务 (已完成: {len(completed_task_ids)})")

        # Create a shared blackboard for this mission — all subtask executors report into it.
        from agents.mission_blackboard import MissionBlackboard

        blackboard = MissionBlackboard(current_mission_plan.task_id)

        # 2. 并发任务调度
        # 2. Concurrent task scheduling
        running_tasks: Dict[str, asyncio.Task] = {}
        subtask_events: Dict[str, asyncio.Event] = {st.id: asyncio.Event() for st in current_mission_plan.subtasks}
        # 如果是从 checkpoint 恢复的，已完成的任务需要直接 set
        for cid in completed_task_ids:
            if cid in subtask_events:
                subtask_events[cid].set()
                
        round_artifacts: List[
            str
        ] = []  # 本轮产出的文件，重规划时清理 / Files produced this round, cleaned on replanning

        # 在所有子任务启动前，对会话历史进行一次性快照，防止并行子任务之间的上下文污染
        # （若 ST1 先完成，其结果会写入 session.history；如不隔离，ST2/ST3 会错误继承 ST1 的输出）
        # Before launching all subtasks, take a one-time snapshot of session history to prevent context pollution
        # between parallel subtasks (if ST1 finishes first, its result goes into session.history; without isolation, ST2/ST3 would incorrectly inherit ST1's output)
        _baseline_session_history = []
        for m in session.history[-20:]:
            raw_content = m.content or ""
            # --- 核心安全补丁: 防止全局历史中的长文本污染子任务 ---
            if len(raw_content) > 1500:
                raw_content = raw_content[:1500] + f"\n... [Context auto-truncated from {len(raw_content)} chars] ..."
            
            if getattr(m, "images", None):
                content = [{"type": "text", "text": raw_content}]
                for img in m.images:
                    content.append({"type": "image_url", "image_url": {"url": img}})
                _baseline_session_history.append({"role": m.role, "content": content})
            else:
                _baseline_session_history.append({"role": m.role, "content": raw_content})

        async def run_subtask(st: SubTask, current_idx: int):
            if st.owner == "USER":
                # USER 步骤不占用并发槽位
                return await _run_subtask_inner(st, current_idx)
            async with self._semaphore:  # 限制并行子任务数
                return await _run_subtask_inner(st, current_idx)

        async def _run_subtask_inner(st: SubTask, current_idx: int):
            from utils.config import settings
            
            self._write_heartbeat(
                session_id=msg.session_id,
                task_id=current_mission_plan.task_id,
                running_subtask=st.id,
                completed=len(completed_task_ids),
                total=total_tasks,
            )

            retry_limit = settings.AUDIT_MAX_REMAND_RETRY
            current_retry = 0
            previous_audit_cmd = ""
            _retry_history = []

            # USER 步骤路由：复用 requires_confirm 机制
            if st.owner == "USER":
                _st_start_time = time.time()
                await channel.send_message(
                    to=msg.sender_id,
                    text=f"⏸️ [{st.id}] 需要您操作: {st.instruction}",
                )
                confirmed = await self._request_user_confirmation(
                    msg.session_id, msg.sender_id, channel, st, dynamic_event_handler
                )
                if confirmed:
                    report = Report(
                        subtask_id=st.id,
                        status="SUCCESS",
                        observation="用户已确认完成操作",
                    )
                    executed_tasks[st.id] = report
                    completed_task_ids.add(st.id)
                    _subtask_metrics[st.id] = {
                        "duration_s": round(time.time() - _st_start_time, 1),
                        "retries": 0,
                    }
                    self._save_checkpoint(
                        msg.session_id, current_mission_plan,
                        completed_task_ids, executed_tasks,
                        subtask_metrics=_subtask_metrics,
                    )
                else:
                    # 超时或拒绝 → 标记 CANCELLED，防止调度器重新检测为 ready 导致死循环
                    executed_tasks[st.id] = Report(
                        subtask_id=st.id,
                        status="CANCELLED",
                        observation=f"USER 步骤 {st.id} 超时或未确认，已取消",
                    )
                    completed_task_ids.add(st.id)
                return

            # 等待依赖完成，最多等待 10 分钟防止永久阻塞
            # Wait for dependencies to complete, max 10 minutes to prevent permanent blocking
            _dep_wait_start = asyncio.get_running_loop().time()
            _DEP_WAIT_TIMEOUT = getattr(settings, "DEP_WAIT_TIMEOUT", 600)
            
            while not all(dep in executed_tasks for dep in st.depends_on):
                missing = [d for d in st.depends_on if d not in executed_tasks]
                wait_tasks = [subtask_events[d].wait() for d in missing if d in subtask_events]
                if not wait_tasks:
                    break
                
                try:
                    # 等待任意一个依赖完成，超时设为 20 秒用于进度播报
                    await asyncio.wait_for(
                        asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED),
                        timeout=20.0
                    )
                except asyncio.TimeoutError:
                    elapsed_s = int(asyncio.get_running_loop().time() - _dep_wait_start)
                    if elapsed_s > _DEP_WAIT_TIMEOUT:
                        raise Exception(
                            f"__ESCALATE__: [{st.id}] 依赖等待超时 ({_DEP_WAIT_TIMEOUT}s)，未就绪依赖: {missing}"
                        )
                    await channel.send_message(
                        to=msg.sender_id,
                        text=f"⏳ [{st.id}] 等待上游步骤 {missing} 完成（已等 {elapsed_s}s）...",
                    )

            # 计时从实际执行开始（不含依赖等待时间）
            _st_start_time = time.time()

            # 收集前置任务的结果
            # Collect results from preceding tasks
            dep_results = []
            cancelled_deps = []
            for dep_id in st.depends_on:
                if dep_id in executed_tasks:
                    dep_report = executed_tasks[dep_id]
                    if dep_report.status == "CANCELLED":
                        cancelled_deps.append(dep_id)
                    elif dep_report.observation:
                        # [V12] B2.5 Codex-style Dependency Observation Digest
                        digest = summarize_dependency_observation(
                            observation=dep_report.observation,
                            task_id=dep_id,
                            run_dir=getattr(settings, "WORKSPACE_DIR", ".rooster/runs")
                        )
                        dep_results.append(f"[{dep_id}]\n{digest.to_prompt_string()}")

            # [V11] 如果上游有被取消的步骤（如 USER 步骤超时），当前步骤也应取消
            if cancelled_deps:
                executed_tasks[st.id] = Report(
                    subtask_id=st.id,
                    status="CANCELLED",
                    observation=f"上游步骤 {cancelled_deps} 已取消，{st.id} 无法继续",
                )
                completed_task_ids.add(st.id)
                logger.warning(f"⚠️ [{st.id}] 因上游取消 {cancelled_deps} 而取消")
                return

            # 防抖动
            # Debounce
            await asyncio.sleep(random.uniform(0.2, 0.8))

            # [requires_confirm] 人工确认安全门 — Strategist 标记为高风险的子任务必须经用户确认才能执行
            # [requires_confirm] Human confirmation safety gate — subtasks marked high-risk by Strategist must be confirmed by user
            if st.requires_confirm:
                if not await self._request_user_confirmation(
                    msg.session_id, msg.sender_id, channel, st, dynamic_event_handler
                ):
                    executed_tasks[st.id] = Report(
                        subtask_id=st.id,
                        status="CANCELLED",
                        observation=f"子任务 {st.id} 未经用户确认，已取消 (requires_confirm=true)",  # Subtask not confirmed by user, cancelled
                        artifacts=[],
                        process_snapshots=[],
                    )
                    completed_task_ids.add(st.id)
                    await channel.send_message(
                        to=msg.sender_id,
                        text=f"⏭️ 跳过 `{st.id}` (未确认)，继续执行后续任务。",
                    )
                    return

            sub_agent_mode = getattr(st, "sub_agent_mode", "NORMAL").upper()

            while current_retry <= retry_limit:
                # RACE mode: early exit if a sibling already won
                if sub_agent_mode == "RACE" and state_guard.should_terminate(
                    st.id, group_id=current_mission_plan.task_id
                ):
                    logger.info(f"🏁 [{st.id}] 竞速信号已触发，终止当前任务")
                    executed_tasks[st.id] = Report(
                        subtask_id=st.id,
                        status="CANCELLED",
                        observation=f"子任务 {st.id} 竞速失败，已终止",
                        artifacts=[],
                        process_snapshots=[],
                    )
                    return

                state_guard.release_locks(st.id)

                refined = self.tactician.prepare_supplementary_prompt(msg.session_id, st.instruction)
                status_text = f"⚡ 正在执行 [{st.id}] ({current_idx}/{total_tasks}): {refined[:50]}..."
                if current_retry > 0:
                    status_text = f"🛡️ [补救 {current_retry}/{retry_limit}] {st.id}"

                await channel.send_message(to=msg.sender_id, text=f"--- {status_text} ---")

                # 发射子任务开始事件
                await self.event_handler.emit_subtask_start(
                    session_key=msg.session_id,
                    client_run_id=current_mission_plan.task_id,
                    subtask_id=st.id,
                    total=total_tasks,
                    current=current_idx,
                )

                # 使用执行前快照，并附加重试积累的历史（截断到 REMAND_HISTORY_MAX_CHARS 防止上下文爆炸）
                _max_retry_chars = getattr(settings, "REMAND_HISTORY_MAX_CHARS", 8000)
                _trimmed_retry = self._trim_retry_history(_retry_history, _max_retry_chars)
                history = list(_baseline_session_history) + _trimmed_retry

                # ----------------------------------------------------------------
                # [Sub-Agent Mode] 差异化调度 (must run BEFORE AgentRunConfig)
                # ISOLATED  : 迷宫型 — 克隆独立工具注册表，彻底防止状态/上下文污染
                # PARALLEL  : 并行型 — 共享注册表，与其他子任务完全并发
                # SANDBOXED : 沙箱型 — 激活 strict 权限策略，阻断高危工具
                # RACE      : 竞速型 — 首完成者取消兄弟任务
                # NORMAL    : 默认，共享注册表，无额外策略限制
                # ----------------------------------------------------------------

                if sub_agent_mode == "ISOLATED":
                    logger.info(f"🌀 [{st.id}] ISOLATED 模式：克隆独立工具注册表，防止上下文污染")
                    subtask_tool_registry = self.tool_registry.clone()
                else:
                    subtask_tool_registry = self.tool_registry

                subtask_policy = None
                if sub_agent_mode == "SANDBOXED":
                    from utils.permission_policy import make_sandboxed_policy

                    subtask_policy = make_sandboxed_policy()
                    logger.info(f"🔥 [{st.id}] SANDBOXED 模式：激活 strict 权限策略，高危工具已封锁")
                elif sub_agent_mode == "PARALLEL":
                    logger.info(f"⚡ [{st.id}] PARALLEL 模式：并发加速，与其他子任务同步执行")
                elif sub_agent_mode == "RACE":
                    logger.info(
                        f"🏁 [{st.id}] RACE 模式：竞速分组 '{getattr(st, 'race_group', '')}'，首完成者取消兄弟任务"
                    )

                # Broadcast subtask start to blackboard so peers know this slot is active
                await blackboard.update_progress(st.id, "running", step=0)

                config = AgentRunConfig(
                    session_id=msg.session_id,
                    session_key=msg.session_id,
                    agent_id=f"executor_{st.id}",
                    prompt=refined,
                    model=settings.EXECUTOR_MODEL_NAME,
                    workspace_dir=os.path.abspath("."),
                    tool_registry=subtask_tool_registry,
                    allowed_paths=[str(p) for p in settings.ALLOWED_PATHS],
                    group_id=current_mission_plan.task_id,
                    history=history,
                    policy_override=subtask_policy,
                    blackboard=blackboard,
                )

                # Each subtask gets its own LLMClient instance.
                # Sharing self.exec_llm across parallel subtasks is a race condition:
                # switch_provider() mutates shared state and corrupts the other subtask's provider.
                # [Ollama 本地模型接入] 自适应路由轻量任务域 (domain) 至本地模型
                is_local_domain = False
                if st.domain:
                    is_local_domain = any(
                        d.lower() == st.domain.lower() for d in getattr(settings, "OLLAMA_DOMAINS", [])
                    )

                subtask_provider = "local" if is_local_domain else settings.EXECUTOR_MODEL_MODE
                subtask_model = settings.LOCAL_MODEL if is_local_domain else settings.EXECUTOR_MODEL_NAME

                if is_local_domain:
                    logger.info(
                        f"⚡ [Ollama Local Router] 检测到子任务 {st.id} 职能域为 {st.domain}，自动路由至本地模型 (local)"
                    )

                subtask_executor = AgentExecutor(
                    event_handler=dynamic_event_handler,
                    llm_client=LLMClient(
                        provider=subtask_provider,
                        model=subtask_model,
                        failover_order=settings.LLM_FAILOVER_ORDER,
                    ),
                    tool_registry=subtask_tool_registry,
                    orchestrator=self.orchestrator,
                    memory_manager=self.memory_manager,
                    prompt_builder=self.prompt_builder,
                )

                # 合并前置任务结果和审计修正指令
                context_parts = []
                if dep_results:
                    context_parts.append("前置任务结果：\n" + "\n".join(dep_results))
                if previous_audit_cmd:
                    context_parts.append(f"审计官修正指令：\n{previous_audit_cmd}")
                # REMAND 重试：将上次执行的工具输出摘要注入 previous_observations，
                # 让 Executor 知道上次做了什么、哪里出错，才能定向修复。
                if current_retry > 0 and _retry_history:
                    tool_outputs = [
                        m.get("content", "")
                        for m in _retry_history
                        if m.get("role") == "tool" and m.get("content")
                    ]
                    if tool_outputs:
                        combined_tool_output = "\n---\n".join(
                            o[:500] for o in tool_outputs[-5:]  # 最多取最后 5 条，每条截断 500 字符
                        )
                        context_parts.append(f"上次执行工具输出（供纠错参考）：\n{combined_tool_output}")
                combined_context = "\n\n".join(context_parts) if context_parts else ""

                # [V10.0] DAG 叶节点判定：无下游依赖 → 叶节点
                is_leaf = not any(st.id in other.depends_on for other in current_mission_plan.subtasks)

                # [V10.1] 激活 SubTask.timeout 墙钟超时
                # 最小保底由 settings.SUBTASK_MIN_TIMEOUT 控制（默认 300s）
                # 可在 .env 中用 SUBTASK_MIN_TIMEOUT=600 等调大
                MIN_SUBTASK_TIMEOUT = settings.SUBTASK_MIN_TIMEOUT
                effective_timeout = max(st.timeout, MIN_SUBTASK_TIMEOUT) if st.timeout > 0 else 0
                if effective_timeout > st.timeout:
                    logger.warning(
                        f"⚠️ [{st.id}] Strategist 设定超时 {st.timeout}s 低于最小保底 {MIN_SUBTASK_TIMEOUT}s，"
                        f"已自动提升至 {effective_timeout}s。"
                    )
                if effective_timeout > 0:
                    try:
                        report = await asyncio.wait_for(
                            subtask_executor.execute_subtask(
                                st, config, previous_observations=combined_context, is_leaf=is_leaf
                            ),
                            timeout=effective_timeout,
                        )
                    except asyncio.TimeoutError:
                        report = Report(
                            subtask_id=st.id,
                            status="FAILED",
                            evidence={"error": f"SubTask timed out after {effective_timeout}s"},
                            failure_code="SUBTASK_TIMEOUT",
                            observation=f"子任务 {st.id} 超时 ({effective_timeout}s)",
                            provider_used=subtask_provider,
                        )
                else:
                    # timeout=0: 无超时限制
                    report = await subtask_executor.execute_subtask(
                        st, config, previous_observations=combined_context, is_leaf=is_leaf
                    )

                if len(config.history) > len(history):
                    _retry_history.extend(config.history[len(history):])

                # Propagate provider info (may differ from initial if failover occurred)
                if report and not report.provider_used:
                    report.provider_used = subtask_executor.llm_client.provider

                # --- [歧义拦截门] CONFIRM_REQUIRED 路由 (MISSION 模式) ---
                # 执行官在子任务执行过程中发现歧义时，返回 CONFIRM_REQUIRED 类型的 Report。
                # 任务编排器居中转发，晨2用户并挂起当前子任务，将用户回复注入指令后重试。
                if getattr(report, "type", "FINAL_REPORT") == "CONFIRM_REQUIRED":
                    question = report.evidence.get("question", "") or report.observation or ""
                    options = report.evidence.get("options", [])
                    clarification_text = self._format_subtask_clarification(st.id, question, options)
                    await channel.send_message(to=msg.sender_id, text=clarification_text)

                    user_answer = await self._wait_for_clarification(
                        session_id=msg.session_id,
                        sender_id=msg.sender_id,
                        channel=channel,
                        subtask_id=st.id,
                        timeout=getattr(settings, "WAIT_CONFIRM_TIMEOUT", 300),
                    )

                    if user_answer:
                        logger.info(f"[歧义拦截门] [{st.id}] 用户已澄清: {user_answer[:80]}")
                        # 将用户回复作为补充约束注入子任务指令，重新构造 SubTask
                        updated_instruction = (
                            f"{st.instruction}\n"
                            f"\n[\u7528\u6237\u6f84\u6e05] {user_answer}\n"
                            "\u8bf7严格按照用户确认的内容执行，不得再次猜测。"
                        )
                        st = st.model_copy(update={"instruction": updated_instruction})
                        current_retry += 1  # 此次重试不计入审计次数限制
                        await channel.send_message(
                            to=msg.sender_id,
                            text=f"⚡ [重新执行] [{st.id}] 根据您的选择继续执行...",
                        )
                        continue  # 重试当前子任务
                    else:
                        logger.warning(f"[歧义拦截门] [{st.id}] 用户澄清超时，子任务标记为 FAILED")
                        executed_tasks[st.id] = Report(
                            subtask_id=st.id,
                            status="FAILED",
                            observation=f"子任务 {st.id} 因歧义澄清超时而失败，未收到用户确认。",
                            failure_code="CLARIFICATION_TIMEOUT",
                        )
                        completed_task_ids.add(st.id)
                        return

                if report.status == "ESCALATE":
                    raise Exception(f"__ESCALATE__: {report.observation}")

                # [V10.1] 处理 on_failure=RETRY 策略
                if report.status == "RETRY" and current_retry < retry_limit:
                    current_retry += 1
                    await channel.send_message(
                        to=msg.sender_id,
                        text=f"🔄 [{st.id}] 执行失败，自动重试 ({current_retry}/{retry_limit})...",
                    )
                    continue

                # [V10.1] 处理 on_failure=ABORT 策略
                if report.status == "ABORT":
                    await channel.send_message(
                        to=msg.sender_id,
                        text=f"🛑 [{st.id}] 任务中止: {report.observation}",
                    )
                    raise Exception(f"__ABORT__: {report.observation}")

                # [V10.0] DAG 叶节点审计策略：
                # - 非叶节点（有下游依赖）：检查状态，失败则触发重规划
                # - 叶节点（无下游依赖）：调 LLM 审计

                if not is_leaf or is_direct:
                    if report.status == "FAILED" and not is_direct:
                        # 非叶节点失败：下游会拿到空数据，必须重规划
                        await channel.send_message(
                            to=msg.sender_id,
                            text=f"⚠️ [{st.id}] 上游失败，触发重规划: {report.observation}",
                        )
                        raise Exception(f"__ESCALATE__: 子任务 {st.id} 失败 ({report.observation})")
                    verdict = None
                    is_affirm = True
                else:
                    # 叶节点：调 LLM 审计
                    # Emit auditor_start lifecycle event for Dashboard pipeline
                    await self.event_handler.emit_lifecycle(
                        session_key=msg.session_id,
                        client_run_id=current_mission_plan.task_id,
                        status="auditor_start",
                    )
                    verdict = await self.auditor.review(report, st, is_leaf=True)
                    is_affirm = verdict is not None and verdict.verdict == AuditVerdictType.AFFIRM

                    # 广播审计判决事件到 Dashboard
                    if verdict is not None:
                        await self.event_handler.emit_audit_verdict(
                            session_key=msg.session_id,
                            client_run_id=current_mission_plan.task_id,
                            subtask_id=st.id,
                            verdict=verdict.verdict.value,
                            result_verdict=verdict.result_verdict,
                            reason=verdict.reason,
                            recommendation=verdict.recommendation,
                            findings=[f.get("summary", "") for f in (verdict.findings or [])],
                            command=verdict.command or "",
                        )

                    # 短路竞速判定
                    if verdict is not None and str(verdict.concurrency_action).upper() == "TERMINATE_SIBLINGS":
                        state_guard.set_terminate_signal(current_mission_plan.task_id, is_group=True)

                if is_affirm:
                    if report.observation and report.observation.strip():
                        await channel.send_message(
                            to=msg.sender_id,
                            text=f"💬 **完成小结 ({st.id})**:\n{report.observation}",
                        )

                    # 追踪本轮产出的文件（重规划时清理）
                    round_artifacts.extend(report.artifacts)

                    # 成果归档
                    for file_path in report.artifacts + report.process_snapshots:
                        if os.path.exists(file_path):
                            archived = archiver.archive_file(file_path)
                            if archived:
                                if hasattr(channel, "send_file") and channel.channel_id == "feishu":
                                    await channel.send_file(to=msg.sender_id, file_path=archived)
                                else:
                                    await channel.send_message(
                                        to=msg.sender_id,
                                        text=f"📦 [成果归档] {os.path.basename(archived)}",
                                    )

                    label = "审计通过" if is_leaf else "执行完成"
                    await channel.send_message(to=msg.sender_id, text=f"✅ [{label}] {st.id}")

                    # 发射子任务完成事件
                    await self.event_handler.emit_subtask_complete(
                        session_key=msg.session_id,
                        client_run_id=current_mission_plan.task_id,
                        subtask_id=st.id,
                        result_status="SUCCESS",
                        provider_used=report.provider_used or "",
                    )

                    # 产出文件在任务结案时统一 batch 写入，此处不再单独 record

                    executed_tasks[st.id] = report
                    # Record subtask metrics
                    _subtask_metrics[st.id] = {
                        "duration_s": round(time.time() - _st_start_time, 1),
                        "retries": current_retry,
                    }
                    # Persist progress so the task can resume if the process crashes
                    self._save_checkpoint(
                        msg.session_id,
                        current_mission_plan,
                        completed_task_ids | {st.id},
                        executed_tasks,
                        subtask_metrics=_subtask_metrics,
                    )

                    # Blackboard: mark done and broadcast key result for peer agents
                    await blackboard.update_progress(st.id, "done", step=0)
                    if report.observation:
                        # [V12 B4.2] 置信度判定 (Confidence Labeling)
                        _obs_lower = report.observation.lower()
                        status = "tentative" if any(kw in _obs_lower for kw in ["error", "failed", "fallback", "未找到", "妥协"]) else "confirmed"
                        
                        await blackboard.post_fact(
                            key=f"{st.id}_result",
                            value=report.observation[:600],
                            author=st.id,
                            status=status,
                        )

                    # RACE mode: first finisher cancels its race-group siblings
                    if sub_agent_mode == "RACE" and getattr(st, "race_group", ""):
                        won = await blackboard.declare_race_winner(st.race_group, st.id)
                        if won:
                            logger.info(f"🏁 [{st.id}] 赢得竞速组 '{st.race_group}'，取消兄弟任务")
                            # Set terminate signal so siblings check it at their next retry iteration
                            state_guard.set_terminate_signal(current_mission_plan.task_id, is_group=True)
                            for sibling in current_mission_plan.subtasks:
                                if sibling.id != st.id and getattr(sibling, "race_group", "") == st.race_group:
                                    if sibling.id in running_tasks:
                                        logger.info(f"🛑 取消竞速失败的兄弟任务: {sibling.id}")
                                        running_tasks[sibling.id].cancel()
                                    completed_task_ids.add(sibling.id)

                    state_guard.release_locks(st.id)
                    return

                elif verdict is not None and verdict.verdict == AuditVerdictType.REMAND and current_retry < retry_limit:
                    current_retry += 1
                    previous_audit_cmd = verdict.command
                    await channel.send_message(
                        to=msg.sender_id,
                        text=f"🔍 [审计纠错] {st.id}: {verdict.reason}",
                    )
                    continue
                else:
                    reason = verdict.reason if verdict else "叶节点审计未通过"
                    raise Exception(f"__ESCALATE__: 审计官拒绝放行 ({st.id})。原因: {reason}")

        # 调度循环
        try:
            task_counter = 0
            while True:
                # [Graceful Shutdown] 检测 SIGTERM/SIGINT 信号，保存 checkpoint 后干净退出
                from main import shutdown_requested
                if shutdown_requested:
                    logger.info("🛑 收到 shutdown 信号，保存 checkpoint 后退出")
                    self._save_checkpoint(
                        msg.session_id, current_mission_plan,
                        completed_task_ids, executed_tasks,
                        subtask_metrics=_subtask_metrics,
                    )
                    await channel.send_message(
                        to=msg.sender_id,
                        text="⏸️ [系统维护] 任务已暂停并保存进度，下次启动将自动恢复。",
                    )
                    # 取消所有运行中的任务
                    for t in running_tasks.values():
                        t.cancel()
                    break

                ready = [
                    st
                    for st in current_mission_plan.subtasks
                    if st.id not in completed_task_ids
                    and st.id not in running_tasks
                    and all(dep in completed_task_ids for dep in st.depends_on)
                ]

                if not ready and not running_tasks:
                    break

                for st in ready:
                    task_counter += 1
                    coro = run_subtask(st, task_counter)
                    running_tasks[st.id] = asyncio.create_task(coro)
                    logger.info(f"启动子任务: {st.id}")

                if running_tasks:
                    if state_guard.should_terminate("", group_id=current_mission_plan.task_id):
                        for t in running_tasks.values():
                            t.cancel()
                        break

                    done, _ = await asyncio.wait(
                        list(running_tasks.values()),
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=2.0,  # 2 秒轮询，兼顾响应速度和 CPU 效率
                    )

                    for done_task in done:
                        finished_id = next(tid for tid, t in running_tasks.items() if t == done_task)
                        del running_tasks[finished_id]
                        try:
                            await done_task
                            completed_task_ids.add(finished_id)
                        except asyncio.CancelledError:
                            logger.info(f"子任务 {finished_id} 已被成功取消。")
                            completed_task_ids.add(finished_id)  # 确保取消的任务标记为完成，防止下游挂起
                            if finished_id not in executed_tasks:
                                executed_tasks[finished_id] = Report(
                                    subtask_id=finished_id,
                                    status="CANCELLED",
                                    observation=f"子任务 {finished_id} 竞速被取消",
                                    artifacts=[],
                                    process_snapshots=[],
                                )
                        except Exception as e:
                            if "__ESCALATE__" in str(e):
                                blocker = str(e).replace("__ESCALATE__:", "").strip()
                                await channel.send_message(to=msg.sender_id, text=f"🚧 [战略重组] {blocker}")
                                for t in running_tasks.values():
                                    t.cancel()

                                if current_mission_plan.replan_count < current_mission_plan.max_replan:
                                    # 清理上一轮的废文件
                                    for f in round_artifacts:
                                        try:
                                            if os.path.exists(f):
                                                os.remove(f)
                                                logger.info(f"清理废文件: {f}")
                                        except Exception as cleanup_err:
                                            logger.warning(f"清理废文件失败: {f} - {cleanup_err}")
                                    round_artifacts.clear()

                                    current_mission_plan = await self.strategist.replan(
                                        current_mission_plan, blocker, list(completed_task_ids)
                                    )
                                    # Reset completed_task_ids for the new plan.
                                    # New plan may reuse IDs like ST1/ST2 — keeping old IDs would
                                    # cause the scheduler to skip those subtasks as already-done.
                                    new_ids = {st.id for st in current_mission_plan.subtasks}
                                    completed_task_ids -= new_ids
                                    running_tasks.clear()
                                    
                                    # [NEW PLAN] Re-initialize events for the new plan
                                    subtask_events.clear()
                                    for new_st in current_mission_plan.subtasks:
                                        subtask_events[new_st.id] = asyncio.Event()
                                    for cid in completed_task_ids:
                                        if cid in subtask_events:
                                            subtask_events[cid].set()
                                            
                                    await channel.send_message(to=msg.sender_id, text="🔄 [重构完毕] 执行新方案...")
                                    break
                                else:
                                    raise e
                            else:
                                raise e
                        finally:
                            if finished_id in subtask_events:
                                subtask_events[finished_id].set()
                else:
                    await asyncio.sleep(1.0)  # 无运行任务时 1 秒检查一次 ready

        except Exception as e:
            if running_tasks:
                for t in running_tasks.values():
                    t.cancel()
                await asyncio.gather(*running_tasks.values(), return_exceptions=True)
            logger.error(f"调度崩溃: {e}")

            error_msg = str(e)
            if "__ESCALATE__:" in error_msg:
                error_msg = error_msg.replace("__ESCALATE__:", "策略受阻 (由于资源缺失或审计未通过):")
            if "__ABORT__:" in error_msg:
                error_msg = error_msg.replace("__ABORT__:", "任务已中止:")

            await channel.send_message(to=msg.sender_id, text=f"❌ [执行失败] {error_msg}")

            # Emit 'failed' lifecycle event so Dashboard pipeline transitions to error state
            # instead of staying stuck in "running" forever
            await self.event_handler.emit_lifecycle(
                session_key=msg.session_id,
                client_run_id=current_mission_plan.task_id,
                status="failed",
            )

            # Keep the checkpoint so the user can resume after fixing the issue
            return

        if completed_task_ids:
            # 任务结束时一次性蒸馏记忆（避免子任务级别频繁蒸馏）
            # Emit all_subtasks_done lifecycle event for Dashboard pipeline
            await self.event_handler.emit_lifecycle(
                session_key=msg.session_id,
                client_run_id=current_mission_plan.task_id,
                status="all_subtasks_done",
            )

            # Auto-write key facts to LTM — batch write, single index rebuild
            _batch = []
            for tid in completed_task_ids:
                if tid in executed_tasks:
                    report = executed_tasks[tid]
                    if report.artifacts:
                        for art in report.artifacts:
                            _batch.append(
                                {
                                    "content": f"生成了文件: {art} — 任务 {tid} 产出",
                                    "fact_type": MemoryFactType.ARTIFACT_CREATED,
                                    "evidence_path": art,
                                    "confidence": 1.0,
                                }
                            )
                    if report.observation and report.observation.strip():
                        summary = report.observation[:200]
                        _template_words = ["执行成功", "任务完成", "子任务"]
                        if len(summary) > 50 and not any(w in summary for w in _template_words):
                            _batch.append(
                                {
                                    "content": f"[{tid}] 执行结果: {summary}",
                                    "fact_type": MemoryFactType.DECISION_LOG,
                                }
                            )
            if _batch:
                self.memory_manager.batch_update_facts(_batch)

            await channel.send_message(to=msg.sender_id, text="✅ **[任务结案]** 所有步骤已通过审计。")
            # Task succeeded — remove the checkpoint so it doesn't get replayed
            self._clear_checkpoint(msg.session_id, reframed_text)

        # Clean up MissionTactician state to prevent unbounded memory growth in long-running processes
        self.tactician.states.pop(current_mission_plan.task_id, None)

    # ----------------------------------------------------------------
    # Clarification Gate helpers (MISSION mode)
    # ----------------------------------------------------------------

    def _format_subtask_clarification(self, subtask_id: str, question: str, options: list) -> str:
        """将 CONFIRM_REQUIRED 信号格式化为任务级的用户问询消息。"""
        lines = [f"\u2753 **[子任务 `{subtask_id}` 需要您确认：]**\n\n{question}"]
        if options:
            lines.append("\n**请从以下选项中选择：**")
            for i, opt in enumerate(options, 1):
                lines.append(f"  **{i}.** {opt}")
            lines.append("\n请回复选项序号（如 `1`、`2`）或直接输入您想要的具体描述。")
        return "\n".join(lines)

    async def _wait_for_clarification(
        self,
        session_id: str,
        sender_id: str,
        channel: Any,
        subtask_id: str,
        timeout: int = getattr(settings, "WAIT_CONFIRM_TIMEOUT", 300),
    ) -> Optional[str]:
        """挂起当前子任务，等待用户回复澄清指令。

        timeout 内无回复返回 None，调用方应将子任务标记为 FAILED。
        """
        from gateway.run_manager import global_run_manager

        run_id = global_run_manager.session_to_run.get(session_id)
        run = global_run_manager.active_runs.get(run_id) if run_id else None

        if not run:
            logger.warning(f"No active run found for session {session_id} to block on clarification.")
            return None

        run.status = "waiting_for_input"
        run.input_event.clear()
        run.input_data = None

        try:
            await asyncio.wait_for(run.input_event.wait(), timeout=timeout)
            return str(run.input_data) if run.input_data else None
        except asyncio.TimeoutError:
            await channel.send_message(
                to=sender_id,
                text=(f"⏰ 等待用户输入超时（{timeout}s），子任务 `{subtask_id}` 已标记为失败。"),
            )
            return None
        finally:
            if run.status == "waiting_for_input":
                run.status = "running"
            run.input_event.clear()
            run.input_data = None
