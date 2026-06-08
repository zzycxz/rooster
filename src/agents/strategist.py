# src/agents/strategist.py
import asyncio
import json
import logging
from typing import List, Optional
from .llm_client import LLMClient
from .protocol import MissionPlan, PlanDecision, PlanMode, SubTask
from utils.config import settings
import re
import os
from .executor import _stream_with_chunk_timeout

logger = logging.getLogger(__name__)


def _extract_json_objects(text: str) -> list:
    """从可能格式不完美的文本中提取顶层 JSON 对象列表。
    使用字符串感知的括号匹配，避免 JSON 字符串值内的 {} 干扰解析。
    Extract top-level JSON objects from potentially imperfect text,
    using string-aware brace matching to ignore {} inside JSON string values."""
    objects = []
    i = 0
    n = len(text)

    while i < n:
        # 跳过非 { 字符，寻找对象起点
        if text[i] != "{":
            i += 1
            continue

        # 找到 { 开始，用字符串感知的方式找匹配的 }
        depth = 0
        start = i
        j = i
        in_string = False
        escape_next = False

        while j < n:
            c = text[j]

            if escape_next:
                escape_next = False
                j += 1
                continue

            if c == "\\" and in_string:
                escape_next = True
                j += 1
                continue

            if c == '"':
                in_string = not in_string
            elif not in_string:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        obj_str = text[start : j + 1]
                        try:
                            obj = json.loads(obj_str)
                            objects.append(obj)
                        except json.JSONDecodeError:
                            pass
                        i = j + 1
                        break
            j += 1
        else:
            # 没找到匹配的 }，跳过这个 {
            i += 1

    return objects


def _fast_tier_model() -> str:
    return getattr(settings, "MODEL_TIER_FAST", "") or settings.FAST_MODEL_NAME


class Strategist:
    """
    战略官 (Strategist)：
    职能：顶层设计、任务分解、蓝图制定。
    它是系统的"心"，负责将模糊的用户需求转化为结构化的任务执行计划 (MissionPlan)。
    """

    # Strategist: top-level design, task decomposition, blueprint formulation.
    # The system's 'heart', converting vague user needs into structured task execution plans (MissionPlan)

    # 延迟单例：首次使用时加载，后续复用
    # Lazy singleton: load on first use, reuse thereafter
    _skill_loader = None

    def __init__(self, llm_client: LLMClient, memory_manager=None, tool_registry=None):
        self.llm_client = llm_client
        self.memory_manager = memory_manager
        self._tool_registry = tool_registry
        if Strategist._skill_loader is None:
            try:
                from skills._loader import SkillLoader

                Strategist._skill_loader = SkillLoader()
            except Exception as e:
                logger.warning(f"⚠️ [Strategist] SkillLoader 初始化失败: {e}")

    def _get_skills_digest(self) -> str:
        digest_parts = []

        # 1. 注入系统原生工具（Native Tools）
        if self._tool_registry:
            try:
                native_tools = "## ⚙️ 原生系统工具 (Native Tools)\n"
                for t in self._tool_registry.get_all_tool_schemas():
                    name = t.get("function", {}).get("name", "")
                    desc = t.get("function", {}).get("description", "")
                    if name:
                        native_tools += f"- `{name}`: {desc}\n"
                digest_parts.append(native_tools)
            except Exception:
                pass

        # 2. 注入外部扩展技能（Custom Skills）
        if Strategist._skill_loader:
            try:
                custom_skills = Strategist._skill_loader.get_skills_digest()
                if custom_skills:
                    digest_parts.append(custom_skills)
            except Exception:
                pass

        return "\n\n".join(digest_parts)

    async def plan(self, user_request: str, max_tokens: int = 32768) -> MissionPlan:
        """
        [V3.0] 极速蓝图规划：接入 PromptManager。
        """
        logger.info(f"🧠 [Strategist] 极速规划启动: {user_request}")

        # --- [V2 Cognitive Upgrade: 五层 System Prompt 架构] ---
        from memory.soul_loader import SoulLoader
        from memory.manager import MemoryManager  # 使用 Manager 替代 Pool  # Use Manager instead of Pool

        # 实例化加载器
        # Instantiate loader
        soul_loader = SoulLoader(llm_client=self.llm_client, model=settings.STRATEGIST_MODEL_NAME)
        # 获取最相关的记忆召回 (语义搜索)
        # Get most relevant memory recall (semantic search)
        memory_manager = self.memory_manager or MemoryManager()
        ltm_context = await memory_manager.get_summary_for_prompt_async(query=user_request)

        # 组装五层 Prompt
        # Assemble five-layer Prompt
        # 使用 __file__ 构建绝对路径，避免因 CWD 不同导致 src/src/prompts 双层路径 bug
        _prompts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
        _strategist_prompt = (
            "strategist.md" if os.path.exists(os.path.join(_prompts_dir, "strategist.md")) else "base.md"
        )
        system_prompt = soul_loader.build_system_prompt(
            base_prompt_name=_strategist_prompt,
            ltm_context=ltm_context,
            skills_digest=self._get_skills_digest(),
        )
        # --- [End Upgrade] ---

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"User Request: {user_request}"},
        ]

        from pydantic import ValidationError

        response = None
        MAX_RETRIES = 2

        try:
            for attempt in range(MAX_RETRIES + 1):
                # Timeout guards against LLM hangs that would block MissionRunner indefinitely.
                response = await asyncio.wait_for(
                    self.llm_client.chat_non_stream(
                        messages=messages, model=settings.STRATEGIST_MODEL_NAME, temperature=0.1, max_tokens=max_tokens
                    ),
                    timeout=settings.STRATEGIST_LLM_TIMEOUT,
                )

                raw_plan = response.content

                # 健壮的 JSON 提取逻辑：支持 ```json 包裹和首尾大括号匹配
                # Robust JSON extraction: support ```json wrapping and first/last brace matching
                def extract_json(text: str) -> str:
                    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
                    if match:
                        return match.group(1)
                    match = re.search(r"(\{.*\})", text, re.DOTALL)
                    if match:
                        return match.group(1)
                    return text.strip()

                try:
                    clean_plan = extract_json(raw_plan)
                    plan_data = json.loads(clean_plan)

                    # 全自动语义补全
                    # Automatic semantic completion
                    if "edicts" in plan_data:
                        plan_data["subtasks"] = plan_data.pop("edicts")
                    if "subtasks" not in plan_data:
                        plan_data["subtasks"] = []

                    # v11.0 默认值补全（含 CCP 字段）
                    # v11.0 default value completion (including CCP fields)
                    plan_data.setdefault("os_context", "unknown")
                    plan_data.setdefault("autonomy", "AUTO")
                    plan_data.setdefault("blockers", [])
                    plan_data.setdefault("deliverables", [])
                    plan_data.setdefault("feasibility_note", "")

                    # v11.0: 收集已注册工具名用于校验
                    registered_tools = None
                    if self._tool_registry:
                        try:
                            registered_tools = set(self._tool_registry.list_tool_names())
                        except Exception:
                            pass

                    for task in plan_data["subtasks"]:
                        task.setdefault("on_failure", "RETRY")
                        task.setdefault("requires_confirm", False)
                        task.setdefault("owner", "AGENT")
                        task.setdefault("confidence", "HIGH")
                        task.setdefault("risk_note", "")
                        if task.get("domain") == "COMBAT":
                            task["domain"] = "UI"  # 自动迁移旧 Domain / Auto-migrate old Domain
                        task.pop(
                            "phase", None
                        )  # v10.0: phase 由 DAG 拓扑推导，忽略 LLM 输出 / v10.0: phase derived from DAG topology, ignore LLM output

                        # v11.0: tool 名校验 — 如果 tool 不在注册表中，降级为 generic_tool
                        if registered_tools and task.get("tool") not in registered_tools:
                            logger.warning(
                                f"⚠️ [Strategist] tool '{task.get('tool')}' 未注册，降级为 generic_tool (subtask {task.get('id')})"
                            )
                            task["tool"] = "generic_tool"

                    plan = MissionPlan(**plan_data)
                    logger.info(f"⚡ [Strategist] 规划秒开: {len(plan.subtasks)} 子任务已锁定。")
                    return plan

                except (json.JSONDecodeError, ValidationError) as parse_err:
                    if attempt < MAX_RETRIES:
                        err_msg = str(parse_err)
                        if isinstance(parse_err, ValidationError):
                            err_msg = "Pydantic Schema Error:\n" + "\n".join(
                                [f"- {err['loc']}: {err['msg']}" for err in parse_err.errors()]
                            )
                        logger.warning(
                            f"⚠️ [Strategist] 解析失败触发 LLM 自修复 (Attempt {attempt + 1}/{MAX_RETRIES}):\n{err_msg}"
                        )
                        messages.append({"role": "assistant", "content": raw_plan})
                        messages.append(
                            {
                                "role": "user",
                                "content": f"The JSON validation failed with these errors:\n{err_msg}\nPlease strictly follow the schema and output ONLY valid JSON.",
                            }
                        )
                        continue
                    else:
                        raise parse_err

        except asyncio.TimeoutError:
            logger.error(f"❌ [Strategist] plan() 超时 ({settings.STRATEGIST_LLM_TIMEOUT:.0f}s)，降级为单任务兜底方案")
            return MissionPlan(
                task_id="FAILSAFE",
                goal="降级任务处理",
                subtasks=[SubTask(id="FAILSAFE", instruction=user_request, domain="SYSTEM", tool="generic_tool")],
            )

        except Exception as e:
            # 终极解析尝试：寻找文本中的第一个 { 和最后一个 }
            # Ultimate parse attempt: find first { and last } in text
            if response and hasattr(response, "content") and response.content:
                try:
                    content = response.content
                    match = re.search(r"(\{.*\})", content, re.DOTALL)
                    if match:
                        plan_data = json.loads(match.group(1))
                        subtasks = []
                        for i, t in enumerate(plan_data.get("subtasks", [])):
                            instr = t.get("instruction", "")
                            subtasks.append(
                                SubTask(
                                    id=t.get("id", f"ST{i}"),
                                    instruction=instr,
                                    domain="UI" if t.get("domain") == "COMBAT" else t.get("domain", "SYSTEM"),
                                    tool=t.get("tool", "generic_tool"),
                                    depends_on=t.get("depends_on", []),
                                    on_failure=t.get("on_failure", "RETRY"),
                                    requires_confirm=t.get("requires_confirm", False),
                                    timeout=t.get("timeout", settings.SUBTASK_MIN_TIMEOUT),
                                    owner=t.get("owner", "AGENT"),
                                    confidence=t.get("confidence", "HIGH"),
                                    risk_note=t.get("risk_note", ""),
                                )
                            )
                        return MissionPlan(
                            task_id=plan_data.get("task_id", "ST-PLAN"),
                            os_context=plan_data.get("os_context", "unknown"),
                            goal=plan_data.get("goal", user_request),
                            autonomy=plan_data.get("autonomy", "AUTO"),
                            subtasks=subtasks,
                            blockers=plan_data.get("blockers", []),
                            deliverables=plan_data.get("deliverables", []),
                            feasibility_note=plan_data.get("feasibility_note", ""),
                        )
                except Exception as fallback_e:
                    logger.warning(f"❌ [Strategist] 降级解析也失败: {fallback_e}")

            logger.error(f"❌ [Strategist] 规划异常: {e}")
            return MissionPlan(
                task_id="FAILSAFE",
                goal="降级任务处理",
                subtasks=[SubTask(id="FAILSAFE", instruction=user_request, domain="SYSTEM", tool="generic_tool")],
            )

    async def plan_stream(self, user_request: str, images: Optional[List[str]] = None):
        """
        [V8.0] 流式产生子任务：严格对齐 Domain-Tool 映射。
        """
        logger.info(f"🧠 [Strategist] 流式规划启动: {user_request}")

        # --- [V2 Cognitive Upgrade: 五层 System Prompt 架构] ---
        from memory.soul_loader import SoulLoader
        from memory.manager import MemoryManager

        # 实例化加载器
        soul_loader = SoulLoader(llm_client=self.llm_client, model=settings.STRATEGIST_MODEL_NAME)
        # 获取最相关的记忆召回 (语义搜索)
        manager = self.memory_manager or MemoryManager()
        ltm_context = await manager.get_summary_for_prompt_async(query=user_request)

        # 组装五层 Prompt
        # 使用 __file__ 构建绝对路径，避免因 CWD 不同导致 src/src/prompts 双层路径 bug
        _prompts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
        _strategist_prompt = (
            "strategist.md" if os.path.exists(os.path.join(_prompts_dir, "strategist.md")) else "base.md"
        )
        system_prompt = soul_loader.build_system_prompt(
            base_prompt_name=_strategist_prompt,
            ltm_context=ltm_context,
            skills_digest=self._get_skills_digest(),
        )
        # --- [End Upgrade] ---

        user_content = [{"type": "text", "text": f"User Request: {user_request}"}]
        if images:
            for b64 in images:
                data_url = b64 if b64.startswith("data:") else f"data:image/png;base64,{b64}"
                user_content.append({"type": "image_url", "image_url": {"url": data_url}})

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content if images else f"User Request: {user_request}"},
        ]

        full_content = ""
        yielded_ids = set()
        # 预置元数据，流结束后从 full_content 解析填充
        self._last_plan_meta = {
            "blockers": [],
            "deliverables": [],
            "feasibility_note": "",
        }

        try:
            async for delta in _stream_with_chunk_timeout(
                self.llm_client.chat_stream(
                    messages=messages, model=settings.STRATEGIST_MODEL_NAME, temperature=0.1, max_tokens=32768
                ),
                chunk_timeout=getattr(settings, "LLM_STREAM_CHUNK_TIMEOUT", 30.0),
            ):
                if delta.content:
                    full_content += delta.content

                    # 字符串感知的 JSON 对象提取（替代旧版 depth counter）
                    # String-aware JSON object extraction (replaces legacy depth counter)
                    match = re.search(r'"subtasks"\s*:\s*(.*)', full_content, re.DOTALL | re.IGNORECASE)
                    if match:
                        subtasks_part = match.group(1)
                        for task_data in _extract_json_objects(subtasks_part):
                            t_id = task_data.get("id", f"ST{len(yielded_ids) + 1}")
                            instr = task_data.get("instruction", "").strip()

                            # 严格校验：ID 存在、未重复、且指令不是占位符
                            # Strict validation: ID exists, not duplicate, instruction not a placeholder
                            if t_id not in yielded_ids and instr and len(instr) > 5:
                                # [V10.0] phase 由 DAG 拓扑推导，忽略 LLM 输出
                                task_data.pop("phase", None)
                                task_data.update(
                                    {
                                        "id": t_id,
                                        "instruction": instr,
                                        "domain": "UI"
                                        if task_data.get("domain") == "COMBAT"
                                        else task_data.get("domain", "SYSTEM"),
                                        "tool": task_data.get("tool", "system_tool"),
                                        "depends_on": task_data.get("depends_on", []),
                                        "on_failure": task_data.get("on_failure", "RETRY"),
                                        "requires_confirm": task_data.get("requires_confirm", False),
                                        "timeout": task_data.get(
                                            "timeout", settings.SUBTASK_MIN_TIMEOUT
                                        ),
                                        "owner": task_data.get("owner", "AGENT"),
                                        "confidence": task_data.get("confidence", "HIGH"),
                                        "risk_note": task_data.get("risk_note", ""),
                                    }
                                )
                                yield SubTask(**task_data)
                                yielded_ids.add(t_id)
        except asyncio.TimeoutError:
            logger.error(
                f"❌ [Strategist] plan_stream() 流中断：超过 {getattr(settings, 'LLM_STREAM_CHUNK_TIMEOUT', 30):.0f}s 无新 chunk，降级 FAILSAFE"
            )
        except Exception as e:
            logger.error(f"❌ [Strategist] 流式规划异常: {e}")

        # 诊断日志：打印 LLM 原始返回的前 500 字符
        if not yielded_ids:
            logger.warning(f"🔍 [Strategist 诊断] yielded_ids 为空, full_content 长度={len(full_content)}")
            if full_content:
                logger.warning(f"🔍 [Strategist 诊断] LLM 原始返回前 500 字符:\n{full_content[:500]}")
            else:
                logger.warning("🔍 [Strategist 诊断] full_content 为空，LLM 可能未返回任何内容")

        # [DAY 5 Robust] 终极抢救机制：如果流式拆分完全失败，在流结束后使用字符串感知提取
        # [DAY 5 Robust] Ultimate rescue: if streaming split completely fails, use string-aware extraction
        if not yielded_ids and full_content:
            logger.info(f"🔍 [Strategist Fallback] 尝试解析全量文本 (长度: {len(full_content)})")
            try:
                # 步骤 1: 剥离 Markdown 代码块
                raw_text = re.sub(r"```json\n?|\n?```", "", full_content).strip()
                # 步骤 2: 先尝试完整 JSON 解析（如果整个回复就是一个 JSON 对象）
                # Step 2: Try full JSON parse first (if the entire response is a single JSON object)
                subtasks = []
                try:
                    plan_data = json.loads(raw_text)
                    subtasks = plan_data.get("subtasks", [])
                except (json.JSONDecodeError, ValueError):
                    # 步骤 3: 用字符串感知提取器从文本中找出所有子任务对象
                    # Step 3: Use string-aware extractor to find subtask objects in text
                    match = re.search(r'"subtasks"\s*:\s*(.*)', raw_text, re.DOTALL | re.IGNORECASE)
                    if match:
                        for obj in _extract_json_objects(match.group(1)):
                            if "id" in obj or "instruction" in obj:
                                subtasks.append(obj)
                    else:
                        # 最后手段：在整个文本中搜索所有 JSON 对象
                        for obj in _extract_json_objects(raw_text):
                            if "id" in obj or "instruction" in obj:
                                subtasks.append(obj)

                for i, t_obj in enumerate(subtasks):
                    t_id = t_obj.get("id", f"ST{i + 1}")
                    if t_id not in yielded_ids:
                        t_obj.pop("phase", None)  # v10.0: 忽略 LLM 输出的 phase
                        t_obj.update(
                            {
                                "id": t_id,
                                "instruction": t_obj.get("instruction", "").strip(),
                                "domain": "UI"
                                if t_obj.get("domain") == "COMBAT"
                                else t_obj.get("domain", "SYSTEM"),
                                "tool": t_obj.get("tool", "generic_tool"),
                                "on_failure": t_obj.get("on_failure", "RETRY"),
                                "requires_confirm": t_obj.get("requires_confirm", False),
                                "timeout": t_obj.get("timeout", settings.SUBTASK_MIN_TIMEOUT),
                                "owner": t_obj.get("owner", "AGENT"),
                                "confidence": t_obj.get("confidence", "HIGH"),
                                "risk_note": t_obj.get("risk_note", ""),
                            }
                        )
                        yield SubTask(**t_obj)
                        yielded_ids.add(t_id)

                if not yielded_ids:
                    logger.error(f"🚨 无法在回复中找到任何有效的子任务对象。Raw: {full_content[:200]}")
            except Exception as fe:
                logger.error(f"❌ [Strategist] 全量纠错失败: {fe}")

        # 如果所有手段均告失效，强制唤醒降级模式
        # If all methods fail, force degrade mode
        if not yielded_ids:
            logger.error("🚨 [Strategist] 任务蓝图严重破损！强制启用单步执行降级模式。")
            # 增加原始输出的回显，帮助用户定位是模型拒答还是解析逻辑 Bug
            # Add raw output echo to help user locate whether model refusal or parse logic bug
            summary = full_content.strip() if full_content else "LLM 无任何返回 (Empty Response)"
            logger.info(f"💾 [DEBUG] 原始输出快照: {summary[:500]}...")
            yield SubTask(id="FAILSAFE", instruction=user_request, domain="SYSTEM", tool="generic_tool")

        # --- [V11] 从 full_content 提取顶层元数据，避免调用者再调一次 plan() ---
        if full_content:
            try:
                raw_text = re.sub(r"```json\n?|\n?```", "", full_content).strip()
                # 优先尝试完整 JSON 解析
                plan_data = None
                try:
                    plan_data = json.loads(raw_text)
                except (json.JSONDecodeError, ValueError):
                    # 回退到字符串感知提取，寻找包含 blockers/deliverables 的对象
                    for obj in _extract_json_objects(raw_text):
                        if "blockers" in obj or "deliverables" in obj:
                            plan_data = obj
                            break
                if plan_data:
                    self._last_plan_meta = {
                        "blockers": plan_data.get("blockers", []),
                        "deliverables": plan_data.get("deliverables", []),
                        "feasibility_note": plan_data.get("feasibility_note", ""),
                    }
                    logger.info(
                        f"📋 [Strategist] 元数据提取成功: blockers={len(self._last_plan_meta['blockers'])}, deliverables={len(self._last_plan_meta['deliverables'])}"
                    )
            except Exception as meta_err:
                logger.warning(f"⚠️ [Strategist] 元数据提取失败，使用空默认值: {meta_err}")

    async def replan(self, current_plan: MissionPlan, roadblock_reason: str, completed_tasks: List[str]) -> MissionPlan:
        """
        [Dynamic Replanning]: 基于遇到的死胡同，动态重建剩余蓝图。
        核心护栏：原目标 (original_goal) 绝对不可篡改！
        """
        # [Dynamic Replanning]: Based on encountered dead ends, dynamically rebuild remaining blueprint.
        # Core guardrail: original_goal must NEVER be modified!
        logger.info(f"🚨 [Strategist] 触发紧急重规划 (Replan Count: {current_plan.replan_count + 1})")
        logger.info(f"📌 [Roadblock]: {roadblock_reason}")

        target_goal = current_plan.original_goal or current_plan.goal
        completed_str = ", ".join(completed_tasks) if completed_tasks else "无"

        # Record the current failure in replan_history
        if not hasattr(current_plan, "replan_history") or current_plan.replan_history is None:
            current_plan.replan_history = []

        current_plan.replan_history.append(
            {
                "replan_index": len(current_plan.replan_history) + 1,
                "roadblock": roadblock_reason,
                "failed_subtasks": [
                    {"id": t.id, "instruction": t.instruction, "tool": t.tool}
                    for t in current_plan.subtasks
                    if t.id not in completed_tasks
                ],
            }
        )

        replan_history_str = ""
        for item in current_plan.replan_history:
            replan_history_str += f"- 尝试 #{item['replan_index']}:\n"
            replan_history_str += f"  - 受阻原因 (Roadblock): {item['roadblock']}\n"
            replan_history_str += f"  - 失败步骤 (Failed subtasks): {item['failed_subtasks']}\n"

        # 提取残缺计划内容给 LLM 作为上下文
        # Extract incomplete plan content as context for LLM
        # [Optimization] 精简 Payload，防止触发 504 超时
        # [Optimization] Slim down Payload to prevent 504 timeout
        remaining_tasks = [
            {"id": t.id, "instr": t.instruction} for t in current_plan.subtasks if t.id not in completed_tasks
        ]

        from utils.system import prompt_manager

        system_prompt = prompt_manager.get_prompt(
            "replan",
            {
                "target_goal": target_goal,
                "roadblock_reason": roadblock_reason,
                "completed_str": completed_str,
                "remaining_tasks": remaining_tasks,
                "replan_history": replan_history_str,
            },
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "请立即进行战略重组并返回纯 JSON 蓝图。"},
        ]

        from pydantic import ValidationError

        response = None
        MAX_RETRIES = 2

        try:
            for attempt in range(MAX_RETRIES + 1):
                response = await asyncio.wait_for(
                    self.llm_client.chat_non_stream(
                        messages=messages, model=settings.STRATEGIST_MODEL_NAME, temperature=0.3, max_tokens=32768
                    ),
                    timeout=settings.STRATEGIST_LLM_TIMEOUT,
                )

                raw_content = response.content

                # --- [ROBUST JSON EXTRACTION] ---
                def extract_json(text):
                    # 方案 1: 正则寻找带 JSON 标签的块
                    # Option 1: regex find JSON-tagged block
                    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
                    if match:
                        return match.group(1)

                    # 方案 2: 正则寻找第一个 { 和最后一个 }
                    # Option 2: regex find first { and last }
                    match = re.search(r"(\{.*\})", text, re.DOTALL)
                    if match:
                        return match.group(1)

                    return text.strip()

                clean_json_str = extract_json(raw_content)

                try:
                    plan_data = json.loads(clean_json_str)
                except json.JSONDecodeError:
                    # 方案 3: 尝试暴力清洗常见干扰字符
                    # Option 3: brute-force clean common interference characters
                    fixed_str = clean_json_str.replace("'", '"').replace("True", "true").replace("False", "false")
                    plan_data = json.loads(fixed_str)

                new_subtasks_data = plan_data.get("subtasks", [])
                new_subtasks = []
                for i, t in enumerate(new_subtasks_data):
                    t_id = t.get("id", f"ST_R_{i + 1}")
                    t.pop("phase", None)  # v10.0: 忽略 LLM 输出的 phase
                    new_subtasks.append(
                        SubTask(
                            id=t_id,
                            instruction=t.get("instruction", ""),
                            domain="UI" if t.get("domain") == "COMBAT" else t.get("domain", "SYSTEM"),
                            tool=t.get("tool", "generic_tool"),
                            on_failure=t.get("on_failure", "RETRY"),
                            requires_confirm=t.get("requires_confirm", False),
                            timeout=t.get("timeout", settings.SUBTASK_MIN_TIMEOUT),
                            mode=t.get("mode", "ATOMIC"),
                            sub_agent_mode=t.get("sub_agent_mode", "NORMAL"),
                            race_group=t.get("race_group", ""),
                            owner=t.get("owner", "AGENT"),
                            confidence=t.get("confidence", "HIGH"),
                            risk_note=t.get("risk_note", ""),
                        )
                    )

                try:
                    plan = MissionPlan(
                        task_id=current_plan.task_id,
                        os_context=plan_data.get("os_context", current_plan.os_context),
                        goal=current_plan.goal,
                        original_goal=target_goal,
                        autonomy=plan_data.get("autonomy", current_plan.autonomy),
                        replan_count=current_plan.replan_count + 1,
                        max_replan=current_plan.max_replan,
                        replan_history=current_plan.replan_history,
                        subtasks=new_subtasks,
                    )
                    return plan
                except ValidationError as ve:
                    if attempt < MAX_RETRIES:
                        err_msg = "Pydantic Schema Error:\n" + "\n".join(
                            [f"- {err['loc']}: {err['msg']}" for err in ve.errors()]
                        )
                        logger.warning(
                            f"⚠️ [Strategist] replan 校验失败触发自修复 (Attempt {attempt + 1}/{MAX_RETRIES}):\n{err_msg}"
                        )
                        messages.append({"role": "assistant", "content": raw_content})
                        messages.append(
                            {
                                "role": "user",
                                "content": f"The JSON validation failed with these errors:\n{err_msg}\nPlease strictly follow the schema and output ONLY valid JSON.",
                            }
                        )
                        continue
                    else:
                        raise ve

        except asyncio.TimeoutError:
            logger.error("❌ [Strategist] replan() 超时 (120s)，原计划保持不变")
            raise Exception("重规划超时，维持原计划")

        except Exception as e:
            raw_snippet = (
                response.content[:100] if response is not None and hasattr(response, "content") else "<no response>"
            )
            logger.error(f"❌ [Strategist] 重规划崩盘 (Raw: {raw_snippet}...): {e}")
            # FAILSAFE: 降级为原始目标的单任务方案，而不是直接崩溃
            logger.warning("⚠️ [Strategist] replan FAILSAFE: 降级为单任务执行原始目标")
            return MissionPlan(
                task_id=current_plan.task_id,
                goal=current_plan.goal,
                original_goal=target_goal,
                os_context=current_plan.os_context,
                autonomy=current_plan.autonomy,
                replan_count=current_plan.replan_count + 1,
                max_replan=current_plan.max_replan,
                replan_history=current_plan.replan_history,
                subtasks=[
                    SubTask(
                        id="FAILSAFE",
                        instruction=target_goal,
                        domain="SYSTEM",
                        tool="generic_tool",
                        on_failure="RETRY",
                    )
                ],
            )

    # ──────────────────────────────────────────────────────────────────
    # V15: decide() — Strategist 语义主权入口
    # ──────────────────────────────────────────────────────────────────

    async def decide(
        self,
        user_request: str,
        skill_hint: Optional[dict] = None,
    ) -> PlanDecision:
        """
        V15 语义判断入口。
        调用 fast LLM 判断任务深度，返回 PlanDecision。
        不修改现有 plan/plan_stream/replan。

        Args:
            user_request: 用户原始请求
            skill_hint: SkillIndex 输出的 hint（可选）
        """
        logger.info(f"🎯 [Strategist.decide] 判断任务深度: {user_request[:80]}...")

        # 1. 调用 fast LLM 判断深度
        mode, model_tier = await self._judge_depth(user_request, skill_hint)

        # 2. DIRECT_REPLY: 返回 lazy 流式生成器
        if mode == PlanMode.DIRECT_REPLY:

            async def _reply_stream():
                try:
                    async for delta in _stream_with_chunk_timeout(
                        self.llm_client.chat_stream(
                            messages=[
                                {"role": "system", "content": "你是一个有帮助的助手。简洁、准确地回答用户问题。"},
                                {"role": "user", "content": user_request},
                            ],
                            model=_fast_tier_model(),
                            temperature=0.3,
                        ),
                        chunk_timeout=getattr(settings, "LLM_STREAM_CHUNK_TIMEOUT", 30.0),
                    ):
                        if delta.content:
                            yield delta.content
                except Exception as e:
                    logger.error(f"[DIRECT_REPLY] 流式生成异常: {e}")
                    yield f"\n[流式中断: {e}]"

            return PlanDecision(mode=PlanMode.DIRECT_REPLY, model_tier=model_tier, reply_stream=_reply_stream())

        # 3. CLARIFY: 返回澄清问题
        if mode == PlanMode.CLARIFY:
            return PlanDecision(
                mode=PlanMode.CLARIFY,
                model_tier=model_tier,
                clarify_question="抱歉，您的请求缺少必要信息。能否补充更多细节？",
            )

        # 4. SINGLE_STEP: 创建单子任务计划
        if mode == PlanMode.SINGLE_STEP:
            import uuid

            single_task = SubTask(
                id=f"ST_{uuid.uuid4().hex[:6]}",
                instruction=user_request,
                domain="SYSTEM",
                tool="generic_tool",
            )
            plan = MissionPlan(
                task_id=f"T{int(__import__('time').time())}",
                goal=user_request,
                subtasks=[single_task],
            )
            return PlanDecision(mode=PlanMode.SINGLE_STEP, model_tier=model_tier, plan=plan)

        # 5. DAG_PLAN: 复用现有 plan_stream()
        subtasks = []
        async for st in self.plan_stream(user_request):
            subtasks.append(st)
        plan = MissionPlan(
            task_id=f"T{int(__import__('time').time())}",
            goal=user_request,
            subtasks=subtasks,
        )
        return PlanDecision(mode=PlanMode.DAG_PLAN, model_tier=model_tier, plan=plan)

    async def _judge_depth(
        self,
        user_request: str,
        skill_hint: Optional[dict] = None,
    ) -> tuple:
        """
        调用 fast LLM 判断任务深度。
        Returns: (PlanMode, model_tier_str)
        """
        _prompts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")
        triage_prompt_path = os.path.join(_prompts_dir, "strategist_triage.md")

        try:
            with open(triage_prompt_path, encoding="utf-8") as f:
                system_prompt = f.read()
        except FileNotFoundError:
            logger.warning("[decide] strategist_triage.md not found, using inline prompt")
            system_prompt = '判断用户请求的执行深度，输出 JSON: {"mode": "direct_reply|single_step|dag_plan|clarify", "model_tier": "fast|standard|reasoning"}'

        # 注入 skill_hint 到 prompt
        hint_text = ""
        if skill_hint:
            hint_text = f"\n\n[能力索引提示] 最匹配的技能: {skill_hint.get('hint_skill', 'N/A')} (置信度: {skill_hint.get('confidence', 0)})"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_request + hint_text},
        ]

        try:
            response = await asyncio.wait_for(
                self.llm_client.chat_non_stream(
                    messages=messages,
                    model=_fast_tier_model(),
                    temperature=0.1,
                    max_tokens=256,
                ),
                timeout=15,  # 分诊必须快
            )

            raw = response.content.strip()
            # 提取 JSON
            match = re.search(r"\{[^}]+\}", raw)
            if match:
                data = json.loads(match.group())
                mode_str = data.get("mode", "single_step")
                tier_str = data.get("model_tier", "standard")

                mode_map = {
                    "direct_reply": PlanMode.DIRECT_REPLY,
                    "single_step": PlanMode.SINGLE_STEP,
                    "dag_plan": PlanMode.DAG_PLAN,
                    "clarify": PlanMode.CLARIFY,
                }
                mode = mode_map.get(mode_str, PlanMode.SINGLE_STEP)

                # model_tier 兜底校验
                if tier_str not in ("fast", "standard", "reasoning"):
                    tier_str = "standard"

                logger.info(f"🎯 [Strategist.decide] 判断结果: mode={mode.value}, tier={tier_str}")
                return mode, tier_str

        except asyncio.TimeoutError:
            logger.error("[Strategist.decide] 分诊超时 (15s)，降级为 SINGLE_STEP")
        except Exception as e:
            logger.error(f"[Strategist.decide] 分诊异常: {e}")

        # 兜底：SINGLE_STEP + standard
        return PlanMode.SINGLE_STEP, "standard"
