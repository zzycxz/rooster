# src/agents/strategist.py
import asyncio
import json
import logging
from typing import List, Optional
from .llm_client import LLMClient
from .protocol import MissionPlan, SubTask
from utils.config import settings
import re
import os

logger = logging.getLogger(__name__)


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

    def __init__(self, llm_client: LLMClient, memory_manager=None):
        self.llm_client = llm_client
        self.memory_manager = memory_manager
        if Strategist._skill_loader is None:
            try:
                from skills._loader import SkillLoader

                Strategist._skill_loader = SkillLoader()
            except Exception as e:
                logger.warning(f"⚠️ [Strategist] SkillLoader 初始化失败: {e}")

    def _get_skills_digest(self) -> str:
        if Strategist._skill_loader:
            try:
                return Strategist._skill_loader.get_skills_digest()
            except Exception:
                pass
        return ""

    async def plan(self, user_request: str) -> MissionPlan:
        """
        [V3.0] 极速蓝图规划：接入 PromptManager。
        """
        logger.info(f"🧠 [Strategist] 极速规划启动: {user_request}")

        # --- [V2 Cognitive Upgrade: 五层 System Prompt 架构] ---
        from memory.soul_loader import SoulLoader
        from memory.manager import MemoryManager  # 使用 Manager 替代 Pool  # Use Manager instead of Pool

        # 实例化加载器
        # Instantiate loader
        soul_loader = SoulLoader()
        # 获取最相关的记忆召回 (语义搜索)
        # Get most relevant memory recall (semantic search)
        memory_manager = self.memory_manager or MemoryManager()
        ltm_context = memory_manager.get_summary_for_prompt(query=user_request)

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

        try:
            # Timeout guards against LLM hangs that would block MissionRunner indefinitely.
            response = await asyncio.wait_for(
                self.llm_client.chat_non_stream(
                    messages=messages, model=settings.STRATEGIST_MODEL_NAME, temperature=0.1, max_tokens=32768
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

            clean_plan = extract_json(raw_plan)
            plan_data = json.loads(clean_plan)
            # 全自动语义补全
            # Automatic semantic completion
            if "edicts" in plan_data:
                plan_data["subtasks"] = plan_data.pop("edicts")
            if "subtasks" not in plan_data:
                plan_data["subtasks"] = []

            # v9.2 默认值补全
            # v9.2 default value completion
            plan_data.setdefault("os_context", "unknown")
            plan_data.setdefault("autonomy", "AUTO")
            for task in plan_data["subtasks"]:
                task.setdefault("on_failure", "RETRY")
                task.setdefault("requires_confirm", False)
                if task.get("domain") == "COMBAT":
                    task["domain"] = "UI"  # 自动迁移旧 Domain / Auto-migrate old Domain
                task.pop(
                    "phase", None
                )  # v10.0: phase 由 DAG 拓扑推导，忽略 LLM 输出 / v10.0: phase derived from DAG topology, ignore LLM output
            plan = MissionPlan(**plan_data)
            logger.info(f"⚡ [Strategist] 规划秒开: {len(plan.subtasks)} 子任务已锁定。")
            return plan

        except asyncio.TimeoutError:
            logger.error(f"❌ [Strategist] plan() 超时 ({settings.STRATEGIST_LLM_TIMEOUT:.0f}s)，降级为单任务兜底方案")
            return MissionPlan(
                task_id="FAILSAFE",
                goal="降级任务处理",
                subtasks=[SubTask(id="FAILSAFE", instruction=user_request, domain="SYSTEM", tool="system_fallback")],
            )

        except Exception as e:
            # 终极解析尝试：寻找文本中的第一个 { 和最后一个 }
            # Ultimate parse attempt: find first { and last } in text
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
                            )
                        )
                    return MissionPlan(
                        task_id=plan_data.get("task_id", "ST-PLAN"),
                        os_context=plan_data.get("os_context", "unknown"),
                        goal=plan_data.get("goal", user_request),
                        autonomy=plan_data.get("autonomy", "AUTO"),
                        subtasks=subtasks,
                    )
            except Exception:
                pass

            logger.error(f"❌ [Strategist] 规划异常: {e}")
            return MissionPlan(
                task_id="FAILSAFE",
                goal="降级任务处理",
                subtasks=[SubTask(id="FAILSAFE", instruction=user_request, domain="SYSTEM", tool="system_fallback")],
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
        soul_loader = SoulLoader()
        # 获取最相关的记忆召回 (语义搜索)
        memory_manager = MemoryManager()
        ltm_context = memory_manager.get_summary_for_prompt(query=user_request)

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

        try:
            async with asyncio.timeout(settings.STRATEGIST_LLM_TIMEOUT):
                async for delta in self.llm_client.chat_stream(
                    messages=messages, model=settings.STRATEGIST_MODEL_NAME, temperature=0.1, max_tokens=32768
                ):
                    if delta.content:
                        full_content += delta.content

                        # 使用正则灵活匹配 "subtasks" 的键名及后面的冒号（兼容所有空格抖动）
                        # Flexibly match "subtasks" key name and colon with regex (tolerant of whitespace jitter)
                        match = re.search(r'"subtasks"\s*:\s*(.*)', full_content, re.DOTALL | re.IGNORECASE)
                        if match:
                            try:
                                # 提取 subtasks 数组之后的部分
                                # Extract the part after subtasks array
                                subtasks_part = match.group(1)

                                # 查找完整的 JSON 对象 { ... }
                                # Find complete JSON object { ... }
                                depth = 0
                                start_idx = -1
                                for i, char in enumerate(subtasks_part):
                                    if char == "{":
                                        if depth == 0:
                                            start_idx = i
                                        depth += 1
                                    elif char == "}":
                                        depth -= 1
                                        if depth == 0 and start_idx != -1:
                                            obj_str = subtasks_part[start_idx : i + 1].strip()
                                            try:
                                                task_data = json.loads(obj_str)
                                                # --- Pydantic 宽容构造 [DAY 5 NEW] ---
                                                # --- Pydantic tolerant construction [DAY 5 NEW] ---
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
                                                            "requires_confirm": task_data.get(
                                                                "requires_confirm", False
                                                            ),
                                                            "timeout": task_data.get("timeout", 120),
                                                        }
                                                    )
                                                    yield SubTask(**task_data)
                                                    yielded_ids.add(t_id)
                                            except Exception as te:
                                                logger.debug(f"⚠️ [Strategist] 解析单个子任务失败: {te}")
                                                continue
                            except Exception:
                                continue
        except asyncio.TimeoutError:
            logger.error(f"❌ [Strategist] plan_stream() 超时 ({settings.STRATEGIST_LLM_TIMEOUT:.0f}s)，降级 FAILSAFE")
        except Exception as e:
            logger.error(f"❌ [Strategist] 流式规划异常: {e}")

        # 诊断日志：打印 LLM 原始返回的前 500 字符
        if not yielded_ids:
            logger.warning(f"🔍 [Strategist 诊断] yielded_ids 为空, full_content 长度={len(full_content)}")
            if full_content:
                logger.warning(f"🔍 [Strategist 诊断] LLM 原始返回前 500 字符:\n{full_content[:500]}")
            else:
                logger.warning("🔍 [Strategist 诊断] full_content 为空，LLM 可能未返回任何内容")

        # [DAY 5 Robust] 终极抢救机制：如果流式拆分完全失败，在流结束后使用全量正则进行静态强解析
        # [DAY 5 Robust] Ultimate rescue: if streaming split completely fails, use full regex static parse after stream ends
        if not yielded_ids and full_content:
            logger.info(f"🔍 [Strategist Fallback] 尝试解析全量文本 (长度: {len(full_content)})")
            try:
                # 步骤 1: 尝试剥离 Markdown
                # Step 1: Attempt to strip Markdown
                raw_text = re.sub(r"```json\n?|\n?```", "", full_content).strip()
                # 步骤 2: 暴力寻找第一个 { 和最后一个 }
                # Step 2: Brute-force find first { and last }
                match = re.search(r"(\{.*\})", raw_text, re.DOTALL)
                if match:
                    json_str = match.group(1)
                    plan_data = json.loads(json_str)
                    subtasks = plan_data.get("subtasks", [])
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
                                    "timeout": t_obj.get("timeout", 120),
                                }
                            )
                            yield SubTask(**t_obj)
                            yielded_ids.add(t_id)
                else:
                    logger.error(f"🚨 无法在回复中找到任何 JSON 块。Raw: {full_content[:200]}")
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
            yield SubTask(id="FAILSAFE", instruction=user_request, domain="SYSTEM", tool="system_fallback")

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

        response = None
        try:
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
                    )
                )

            return MissionPlan(
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
                        tool="system_fallback",
                        on_failure="RETRY",
                    )
                ],
            )
