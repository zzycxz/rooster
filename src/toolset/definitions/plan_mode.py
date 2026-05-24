"""
src/toolset/definitions/plan_mode.py

[P2-2] Plan Mode 状态分离工具。

通过显式工具调用切换 Agent 的规划模式：
- plan_mode = True  → 只输出计划，等待用户确认，不执行工具
- plan_mode = False → 正常执行模式
"""

import logging
from typing import Type, Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# Global Plan Mode state (process-level singleton)
# 全局 Plan Mode 状态（进程级单例）
_PLAN_MODE_STATE: dict = {
    "active": False,
    "plan_content": "",
    "entered_at": None,
}


def is_plan_mode_active() -> bool:
    """Check whether Plan Mode is active (plan-only, no execution).
    供 Executor 检查当前是否处于 Plan Mode（只规划不执行）。"""
    return _PLAN_MODE_STATE["active"]


def get_current_plan() -> str:
    """Get the saved plan content under current Plan Mode.
    获取当前 Plan Mode 下保存的规划内容。"""
    return _PLAN_MODE_STATE.get("plan_content", "")


# --------------------------------------------------------------------------- #
# EnterPlanMode
# --------------------------------------------------------------------------- #


class EnterPlanModeArgs(BaseModel):
    plan: str = Field(..., description="你对当前任务的完整执行计划（步骤、工具、预期结果）")
    reason: str = Field("", description="进入 Plan Mode 的原因说明（可选）")


class EnterPlanModeTool(BaseTool):
    """
    进入 Plan Mode（规划确认模式）。
    调用此工具后，Agent 将输出完整计划并暂停执行，等待用户确认后再继续。
    适用场景：高风险操作前（批量删除文件、大规模改写代码等）先让用户审阅计划。
    """

    name = "enter_plan_mode"
    kit = "System"
    domain = "system"
    risk_level = "low"
    reversible = True
    fc_hidden: bool = True  # [Round 10] Use plan_mode(action="enter") instead
    description = (
        "Enter Planning Mode. The agent will output a full plan and pause execution "
        "until the user confirms. Use before high-risk or complex multi-step operations."
    )
    args_schema: Type[BaseModel] = EnterPlanModeArgs

    async def execute(self, args: EnterPlanModeArgs) -> ToolResult:
        import datetime

        _PLAN_MODE_STATE["active"] = True
        _PLAN_MODE_STATE["plan_content"] = args.plan
        _PLAN_MODE_STATE["entered_at"] = datetime.datetime.now().isoformat()
        logger.info("[PlanMode] Entering Plan Mode")
        logger.info("[PlanMode] 进入 Plan Mode")

        output = (
            "🗺️ **PLAN MODE ACTIVE** — Execution is PAUSED.\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{args.plan}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "Please review the plan above.\n"
            "Reply **'confirm'** to execute, or provide corrections to revise the plan.\n"
            "Call `exit_plan_mode` with execute=true when ready to proceed."
        )
        if args.reason:
            output = f"Reason: {args.reason}\n\n" + output
        return ToolResult.success(output)


# --------------------------------------------------------------------------- #
# ExitPlanMode
# --------------------------------------------------------------------------- #


class ExitPlanModeArgs(BaseModel):
    execute: bool = Field(True, description="True=确认执行计划，False=取消计划并退出 Plan Mode")
    feedback: str = Field("", description="用户对计划的修改意见（可选，仅在 execute=False 时有效）")


class ExitPlanModeTool(BaseTool):
    """
    退出 Plan Mode。
    execute=True：确认执行，Agent 继续按计划运行工具。
    execute=False：取消计划，返回 Strategist 重新规划。
    """

    name = "exit_plan_mode"
    kit = "System"
    domain = "system"
    risk_level = "low"
    reversible = True
    fc_hidden: bool = True  # [Round 10] Use plan_mode(action="exit") instead
    description = (
        "Exit Planning Mode. Set execute=True to confirm and proceed, "
        "or execute=False to cancel and return to re-planning."
    )
    args_schema: Type[BaseModel] = ExitPlanModeArgs

    async def execute(self, args: ExitPlanModeArgs) -> ToolResult:
        _PLAN_MODE_STATE["active"] = False
        _PLAN_MODE_STATE["plan_content"] = ""

        if args.execute:
            logger.info("[PlanMode] Plan confirmed, resuming execution mode")
            logger.info("[PlanMode] 计划已确认，恢复执行模式")
            return ToolResult.success(
                "✅ Plan confirmed. Resuming execution mode.\n"
                "The agent will now proceed to execute the plan step by step."
            )
        else:
            logger.info("[PlanMode] Plan cancelled, returning to planning stage")
            logger.info("[PlanMode] 计划已取消，返回规划阶段")
            feedback_note = f"\nUser feedback: {args.feedback}" if args.feedback else ""
            return ToolResult.success(
                f"❌ Plan cancelled. Returning to re-planning.{feedback_note}\n"
                "Please revise the approach based on the feedback."
            )


# ---------------------------------------------------------------------------
# [Round 10] plan_mode — unified plan mode macro
# Replaces: enter_plan_mode, exit_plan_mode
# ---------------------------------------------------------------------------


class PlanModeArgs(BaseModel):
    action: str = Field(description="'enter' to activate planning mode, 'exit' to leave it")
    plan: Optional[str] = Field(default=None, description="[enter] Full execution plan text")
    reason: Optional[str] = Field(default=None, description="[enter] Why planning mode is needed")
    execute: Optional[bool] = Field(default=True, description="[exit] True=confirm and proceed, False=cancel")
    feedback: Optional[str] = Field(default=None, description="[exit] User feedback when cancelling")


class PlanModeTool(BaseTool):
    """[Round 10] Unified plan mode macro: enter or exit planning mode in one tool call."""

    name = "plan_mode"
    kit = "System"
    domain = "system"
    risk_level = "low"
    reversible = True
    description = (
        "Control Planning Mode. Use action='enter' with a plan to pause execution and present the plan "
        "for user review before running high-risk operations. Use action='exit' with execute=True to confirm "
        "and proceed, or execute=False to cancel."
    )
    args_schema: Type[BaseModel] = PlanModeArgs

    async def execute(self, args: PlanModeArgs) -> ToolResult:
        import datetime

        if args.action == "enter":
            plan = args.plan or ""
            _PLAN_MODE_STATE["active"] = True
            _PLAN_MODE_STATE["plan_content"] = plan
            _PLAN_MODE_STATE["entered_at"] = datetime.datetime.now().isoformat()
            logger.info("[PlanMode] Entering Plan Mode")
            logger.info("[PlanMode] 进入 Plan Mode")
            output = (
                "🗺️ **PLAN MODE ACTIVE** — Execution is PAUSED.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{plan}\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Please review the plan above.\n"
                "Reply **'confirm'** to execute, or provide corrections to revise the plan.\n"
                "Call plan_mode(action='exit', execute=True) when ready to proceed."
            )
            if args.reason:
                output = f"Reason: {args.reason}\n\n" + output
            return ToolResult.success(output)

        elif args.action == "exit":
            _PLAN_MODE_STATE["active"] = False
            _PLAN_MODE_STATE["plan_content"] = ""
            execute = args.execute if args.execute is not None else True
            if execute:
                logger.info("[PlanMode] Plan confirmed, resuming execution mode")
                logger.info("[PlanMode] 计划已确认，恢复执行模式")
                return ToolResult.success(
                    "✅ Plan confirmed. Resuming execution mode.\n"
                    "The agent will now proceed to execute the plan step by step."
                )
            else:
                logger.info("[PlanMode] Plan cancelled, returning to planning stage")
                logger.info("[PlanMode] 计划已取消，返回规划阶段")
                feedback_note = f"\nUser feedback: {args.feedback}" if args.feedback else ""
                return ToolResult.success(
                    f"❌ Plan cancelled. Returning to re-planning.{feedback_note}\n"
                    "Please revise the approach based on the feedback."
                )
        else:
            return ToolResult.error(f"Unknown action '{args.action}'. Valid: 'enter', 'exit'.")
