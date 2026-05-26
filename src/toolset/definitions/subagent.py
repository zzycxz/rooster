"""
src/toolset/definitions/subagent.py

[P1-3] SubAgent 隔离执行工具。

SubAgent 在独立 asyncio Task + 独立上下文中执行，
通过 JSON 文件与主 Agent 通信，完全隔离历史 Context 污染。

设计原则：
- 每个 SubAgent 持有自己的 session_history 副本（深拷贝）
- 超时由 args.timeout_seconds 控制
- 结果写入 .rooster/subagents/{agent_id}.json 持久化
"""

import asyncio
import json
import os
import uuid
import datetime
import logging
from typing import Type
from pydantic import BaseModel, Field
from toolset.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

_SUBAGENT_DIR = os.path.join(".rooster", "subagents")


def _subagent_result_path(agent_id: str) -> str:
    return os.path.join(_SUBAGENT_DIR, f"{agent_id}.json")


# --------------------------------------------------------------------------- #
# SubAgentSpawn
# --------------------------------------------------------------------------- #


class SubAgentSpawnArgs(BaseModel):
    task: str = Field(..., description="子 Agent 要完成的完整任务描述（独立 Context，不继承主对话历史）")
    timeout_seconds: int = Field(120, description="子任务最大执行时间（秒），超时后强制终止")
    model_hint: str = Field("", description="可选：指定子 Agent 使用的 LLM（如 'cloud', 'local'），空=继承主 Agent")
    wait_for_result: bool = Field(True, description="True=同步等待结果返回；False=后台异步执行，立即返回 agent_id")
    spawn_depth: int = Field(0, description="当前递归深度，由主 Agent 传递，0=顶层")


class SubAgentSpawnTool(BaseTool):
    """
    启动一个隔离的 SubAgent 执行指定任务。
    SubAgent 拥有独立 Context，不会污染主 Agent 的对话历史。
    适用场景：并行子任务、高风险操作隔离、大规模数据处理。
    """

    name = "subagent_spawn"
    kit = "System"
    domain = "system"
    risk_level = "medium"
    reversible = False
    description = (
        "Spawn an isolated SubAgent to execute a specific task in its own context. "
        "The SubAgent runs independently and its output is returned as a structured result. "
        "Use for parallel subtasks, high-risk isolated operations, or large-scale processing."
    )
    args_schema: Type[BaseModel] = SubAgentSpawnArgs

    async def execute(self, args: SubAgentSpawnArgs) -> ToolResult:
        # --- Recursion depth guard ---
        from utils.config import settings

        # Always use depth from execution context (trust system, not LLM-supplied value)
        current_depth = self.context.get("spawn_depth", 0)
        args.spawn_depth = current_depth

        if args.spawn_depth >= settings.MAX_SUBAGENT_DEPTH:
            return ToolResult.error(
                f"❌ SubAgent 递归深度超限 (current={args.spawn_depth}, max={settings.MAX_SUBAGENT_DEPTH}). "
                f"无法继续创建子 Agent，请在当前层级完成任务。"
            )

        # Propagate incremented depth into SubAgent's context for nested calls
        self.context["spawn_depth"] = current_depth + 1

        agent_id = f"sub_{uuid.uuid4().hex[:8]}"
        os.makedirs(_SUBAGENT_DIR, exist_ok=True)

        # Initialize result file (PENDING state)
        # 初始化结果文件（PENDING 状态）
        result_data = {
            "agent_id": agent_id,
            "task": args.task,
            "status": "PENDING",
            "created_at": datetime.datetime.now().isoformat(),
            "result": None,
            "error": None,
        }
        with open(_subagent_result_path(agent_id), "w", encoding="utf-8") as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)

        logger.info(f"[SubAgent] 启动 {agent_id}: {args.task[:80]}")

        # Try actual execution (if LLM Client is injected into context)
        # 尝试实际执行（如果 LLM Client 已注入到 context）
        llm_client = self.context.get("llm_client")
        if llm_client is None:
            # When LLM not injected: record as PENDING, return agent_id for later query
            # LLM 未注入时：记录为 PENDING，返回 agent_id 供后续查询
            return ToolResult.success(
                f"⏳ SubAgent {agent_id} registered (no LLM context available for inline execution).\n"
                f"Task: {args.task[:200]}\n"
                f"Use `subagent_result` to check status."
            )

        # Inline execution (using independent coroutine + timeout)
        # 内联执行（使用独立协程 + timeout）
        async def _run_inline():
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a focused SubAgent. Complete the following task precisely and concisely. "
                        "Output your final answer directly. Do not ask clarifying questions."
                    ),
                },
                {"role": "user", "content": args.task},
            ]
            model = args.model_hint or self.context.get("current_model", "")
            output_text = ""
            async for delta in llm_client.chat_stream(model=model, messages=messages):
                if delta.content:
                    output_text += delta.content
            return output_text.strip()

        try:
            if args.wait_for_result:
                result_text = await asyncio.wait_for(_run_inline(), timeout=args.timeout_seconds)
                result_data.update(
                    {
                        "status": "DONE",
                        "result": result_text,
                        "completed_at": datetime.datetime.now().isoformat(),
                    }
                )
                with open(_subagent_result_path(agent_id), "w", encoding="utf-8") as f:
                    json.dump(result_data, f, ensure_ascii=False, indent=2)
                logger.info(f"[SubAgent] {agent_id} 完成，输出 {len(result_text)} 字符")
                return ToolResult.success(f"✅ SubAgent {agent_id} completed.\n\n--- Result ---\n{result_text}")
            else:
                # Background execution
                # 后台执行
                async def _background():
                    try:
                        result_text = await asyncio.wait_for(_run_inline(), timeout=args.timeout_seconds)
                        result_data.update(
                            {
                                "status": "DONE",
                                "result": result_text,
                                "completed_at": datetime.datetime.now().isoformat(),
                            }
                        )
                    except Exception as e:
                        result_data.update({"status": "FAILED", "error": str(e)})
                    with open(_subagent_result_path(agent_id), "w", encoding="utf-8") as f:
                        json.dump(result_data, f, ensure_ascii=False, indent=2)

                asyncio.create_task(_background())
                return ToolResult.success(
                    f"🚀 SubAgent {agent_id} started in background.\n"
                    f"Use `subagent_result` with agent_id='{agent_id}' to check status."
                )
        except asyncio.TimeoutError:
            result_data.update({"status": "FAILED", "error": "Timeout"})
            with open(_subagent_result_path(agent_id), "w", encoding="utf-8") as f:
                json.dump(result_data, f, ensure_ascii=False, indent=2)
            return ToolResult.error(f"⏰ SubAgent {agent_id} timed out after {args.timeout_seconds}s.")
        except Exception as e:
            result_data.update({"status": "FAILED", "error": str(e)})
            with open(_subagent_result_path(agent_id), "w", encoding="utf-8") as f:
                json.dump(result_data, f, ensure_ascii=False, indent=2)
            logger.error(f"[SubAgent] {agent_id} 执行失败: {e}")
            return ToolResult.error(f"❌ SubAgent {agent_id} failed: {e}")


# --------------------------------------------------------------------------- #
# SubAgentResult
# --------------------------------------------------------------------------- #


class SubAgentResultArgs(BaseModel):
    agent_id: str = Field(..., description="由 subagent_spawn 返回的 agent_id")


class SubAgentResultTool(BaseTool):
    """
    查询后台运行中的 SubAgent 的执行结果。
    """

    name = "subagent_result"
    kit = "System"
    domain = "system"
    risk_level = "low"
    reversible = True
    description = "Get the result of a SubAgent spawned in background mode."
    args_schema: Type[BaseModel] = SubAgentResultArgs

    async def execute(self, args: SubAgentResultArgs) -> ToolResult:
        path = _subagent_result_path(args.agent_id)
        if not os.path.exists(path):
            return ToolResult.error(f"❌ SubAgent '{args.agent_id}' not found.")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return ToolResult.error(f"❌ Failed to read SubAgent result: {e}")

        status = data.get("status", "UNKNOWN")
        if status == "PENDING":
            return ToolResult.success(f"⏳ SubAgent {args.agent_id} is still PENDING.")
        if status == "RUNNING":
            return ToolResult.success(f"⚙️ SubAgent {args.agent_id} is RUNNING...")
        if status == "DONE":
            return ToolResult.success(f"✅ SubAgent {args.agent_id} DONE.\n\n--- Result ---\n{data.get('result', '')}")
        if status == "FAILED":
            return ToolResult.error(f"❌ SubAgent {args.agent_id} FAILED: {data.get('error', 'unknown error')}")
        return ToolResult.success(f"SubAgent {args.agent_id} status: {status}")
