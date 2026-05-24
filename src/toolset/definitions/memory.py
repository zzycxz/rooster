import asyncio
import logging
from pydantic import BaseModel, Field
from toolset.base import Tool, ToolResult
from memory.manager import MemoryManager

logger = logging.getLogger(__name__)


class MemoryAddFactArgs(BaseModel):
    fact: str = Field(..., description="要记录的长期事实内容，建议包含绝对时间戳")


class MemoryAddFactTool(Tool):
    name = "memory_add_fact"
    kit = "Memory"
    description = "【核心工具】手动向 Rooster 的长期记忆 (LTM) 中写入一条重要事实。用于持久化保存路径、配置、用户偏好或关键任务里程碑。"
    args_schema = MemoryAddFactArgs

    async def execute(self, args: MemoryAddFactArgs) -> ToolResult:
        try:
            # Get global MemoryManager instance (initialized in AgentExecutor)
            # Reconstruct here is safe since it points to the same disk file
            # 获取全局 MemoryManager 实例（在 AgentExecutor 中已初始化）
            # 这里我们通过单例模式或重新构造（由于它指向同一个磁盘文件，所以是安全的）
            manager = MemoryManager()
            await asyncio.to_thread(manager.update_fact, args.fact)
            return ToolResult.success(f"✅ 成功记录长期事实: {args.fact}")
        except Exception as e:
            return ToolResult.error(f"❌ 记录记忆失败: {str(e)}")
