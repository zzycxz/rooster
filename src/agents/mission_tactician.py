import logging
from typing import Dict, Any, List
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MissionState:
    """持有任务的全局进度与局部记忆"""

    mission_id: str
    collected_data: Dict[str, Any] = field(default_factory=dict)
    missing_fields: List[Any] = field(default_factory=list)
    search_history: List[str] = field(default_factory=list)
    retry_count: int = 0


class MissionTactician:
    """
    任务战术员：独立于 Router 的任务状态协调层。
    负责跨审计周期的‘状态持有’与‘精准补遗清单’生成。
    """

    def __init__(self):
        self.states: Dict[str, MissionState] = {}

    def get_or_create_state(self, mission_id: str) -> MissionState:
        if mission_id not in self.states:
            self.states[mission_id] = MissionState(mission_id=mission_id)
        return self.states[mission_id]

    def prepare_supplementary_prompt(self, mission_id: str, base_instruction: str) -> str:
        """为 Executor 生成精准补遗指令，含搜索历史防重复和重试策略升级。"""
        state = self.get_or_create_state(mission_id)
        if not state.missing_fields and state.retry_count == 0:
            return base_instruction

        parts = [base_instruction]

        if state.missing_fields:
            fields_str = ", ".join([str(f) for f in state.missing_fields])
            parts.append(f"⚠️ 待补全: {fields_str}")

        # 注入搜索历史防止重复劳动
        # Inject search history to prevent redundant work
        if state.search_history:
            recent = state.search_history[-3:]
            parts.append(f"已尝试过以下方式（请勿重复）: {'; '.join(recent)}")

        # 连续重试时建议换路径
        # Suggest alternative approach on consecutive retries
        if state.retry_count >= 3:
            parts.append("⚡ 已多次重试同一方向失败，请考虑使用完全不同的工具或方法。")
        elif state.retry_count >= 2:
            parts.append("⚡ 注意：已重试多次，请审视当前策略是否正确。")

        return "\n".join(parts)
