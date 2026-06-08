import asyncio
import logging
from pydantic import BaseModel, Field
from toolset.base import Tool, ToolResult

logger = logging.getLogger(__name__)


class MemoryAddFactArgs(BaseModel):
    fact: str = Field(..., description="要记录的长期事实内容，建议包含绝对时间戳")


class MemoryAddFactTool(Tool):
    name = "memory_add_fact"
    kit = "Memory"
    description = "【核心工具】手动向 Rooster 的长期记忆 (LTM) 中写入一条重要事实。用于持久化保存路径、配置、用户偏好或关键任务里程碑。"
    args_schema = MemoryAddFactArgs

    async def execute(self, args: MemoryAddFactArgs, ctx=None) -> ToolResult:
        try:
            # 优先使用注入的 memory_manager，复用连接池和缓存
            # Prefer injected memory_manager to reuse connection pool and cache
            manager = None
            if ctx and hasattr(ctx, "memory_manager") and ctx.memory_manager:
                manager = ctx.memory_manager
            else:
                from memory.manager import MemoryManager
                manager = MemoryManager()

            await asyncio.wait_for(
                asyncio.to_thread(manager.update_fact, args.fact),
                timeout=30.0,
            )
            return ToolResult.success(f"✅ 成功记录长期事实: {args.fact}")
        except Exception as e:
            return ToolResult.error(f"❌ 记录记忆失败: {str(e)}")
