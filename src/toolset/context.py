from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agents.llm_client import LLMClient
    from memory.manager import MemoryManager
    from agents.mission_blackboard import MissionBlackboard


@dataclass
class RoosterContext:
    """
    Rooster V14 工具调用的依赖注入上下文。
    包含了当前任务所需的所有依赖，避免在 Tool 实例化时硬编码和持有全局单例。
    """
    session_id: str
    task_id: str
    subtask_id: Optional[str] = None
    workspace_dir: str = ""

    # 核心组件注入
    memory_manager: Optional['MemoryManager'] = None
    llm_client: Optional['LLMClient'] = None
    blackboard: Optional['MissionBlackboard'] = None

    # 配置与安全
    config: Dict[str, Any] = field(default_factory=dict)
    security_policy: Dict[str, Any] = field(default_factory=dict)

    # 其他预留扩展
    extras: Dict[str, Any] = field(default_factory=dict)
