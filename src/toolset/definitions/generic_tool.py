from typing import Type
from pydantic import BaseModel, Field
from toolset.base import BaseTool


class GenericToolArgs(BaseModel):
    instruction: str = Field(description="The original user instruction or task description to execute.")
    query: str = Field(
        "",
        description="Alternative: a search query or question to answer.",
    )


class GenericTool(BaseTool):
    """
    兜底工具：当 Strategist 无法将任务映射到具体工具时，将原始指令交给
    Executor 的 ReAct 循环处理。LLM 会在后续步骤中自主选择正确的工具。

    触发场景:
    1. Strategist 规划失败（超时、JSON 解析失败、异常）→ FAILSAFE SubTask
    2. Strategist 输出了未注册的 tool 名 → 降级为 generic_tool
    3. SINGLE_STEP 模式 → 直接执行用户指令
    """

    name: str = "generic_tool"
    kit: str = "Core"
    description: str = (
        "Fallback tool when no specific tool matches the task. "
        "Passes the instruction to the execution engine, which will choose the right tool in subsequent steps.\n"
        "Requires: instruction (the original task description).\n"
        "Only use when: you are unsure which tool to call. The system will auto-route.\n"
        "NOT for: tasks where you CAN identify the right tool (always prefer the specific tool)."
    )
    domain: str = "craft"
    args_schema: Type[BaseModel] = GenericToolArgs
    risk_level: str = "low"

    async def run(self, **kwargs) -> str:
        instruction = kwargs.get("instruction", "") or kwargs.get("query", "")
        if not instruction:
            return "No instruction provided. Please specify what you need."
        # 不做任何执行——直接返回 instruction 让 ReAct 循环的 LLM 自主处理
        # LLM 会在下一步看到这个返回值，然后自主调用正确的工具
        return (
            f"[GenericTool] Received task: {instruction}\n"
            "Please analyze this task and use the appropriate tool to complete it."
        )
