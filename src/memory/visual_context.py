# rooster/src/memory/visual_context.py
import collections
from typing import Dict, Optional
from pydantic import BaseModel
from utils.vision import VisualObservation


class VisualStateRecord(BaseModel):
    timestamp: float
    nodeId: str
    id_map: Dict[str, dict]  # 简化的 ID 映射，用于快速比对
    # Simplified ID mapping for quick comparison
    screen_hash: str  # 画面语义指纹 (pHash)
    action_taken: Optional[str] = None


class VisualContextBuffer:
    """
    视觉感知缓冲区 (L1 瞬间记忆)
    维护每个节点最近 N 帧的状态，用于执行前后的验证比对。
    """

    def __init__(self, buffer_size: int = 5):
        # 按节点 ID 分组存储队列
        self.buffers: Dict[str, collections.deque] = {}
        self.buffer_size = buffer_size

    def push(self, node_id: str, obs: VisualObservation, action: str = None):
        """压入一帧观测记录"""
        if node_id not in self.buffers:
            self.buffers[node_id] = collections.deque(maxlen=self.buffer_size)

        # 将复杂的 Image 对象转化为哈希 (这里暂时使用简化的占位符，实际需实现 pHash)
        # Convert complex Image object to hash (simplified placeholder; real pHash needed)
        # TODO: 集成 perceptual_hash 算法
        fake_hash = str(hash(obs.screenshot.tobytes()))

        record = VisualStateRecord(
            timestamp=obs.timestamp,
            nodeId=node_id,
            id_map={k: v.dict() for k, v in obs.id_map.items()},
            screen_hash=fake_hash,
            action_taken=action,
        )
        self.buffers[node_id].append(record)

    def get_last(self, node_id: str) -> Optional[VisualStateRecord]:
        if node_id in self.buffers and self.buffers[node_id]:
            return self.buffers[node_id][-1]
        return None

    def calculate_delta(self, node_id: str) -> float:
        """
        计算最近两帧之间的变化量 (0.0=完全相同, 1.0=完全不同)
        """
        if node_id not in self.buffers or len(self.buffers[node_id]) < 2:
            return 1.0  # 如果没有前序帧，假设变化很大

        b = self.buffers[node_id]
        curr, prev = b[-1], b[-2]

        # 精确匹配（实际生产环境需使用 pHash 距离）
        if curr.screen_hash == prev.screen_hash:
            return 0.0
        return 0.5  # 占位
