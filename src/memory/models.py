from enum import Enum
from dataclasses import dataclass
from pydantic import BaseModel, Field
from typing import Optional, Any, List
from datetime import datetime


class MemoryFactType(str, Enum):
    # 工具调用的物理结果（最高优先级）
    # Physical result of tool calls (highest priority)
    TOOL_RESULT = "tool_result"
    # 文件/工件的生成记录
    # File/artifact creation record
    ARTIFACT_CREATED = "artifact_created"
    # 任务阶段的决策与原因
    # Task-phase decisions and rationale
    DECISION_LOG = "decision_log"
    # 外部数据源的关键发现（搜索结果摘要）
    # Key findings from external data sources (search result summaries)
    RESEARCH_FINDING = "research_finding"
    # 系统/环境状态观测
    # System/environment state observation
    ENV_OBSERVATION = "env_observation"
    # 用户明确表达的偏好
    # Explicitly expressed user preferences
    USER_PREFERENCE = "user_preference"
    # 错误和失败记录（防止重蹈覆辙）
    # Error and failure records (prevent repeating mistakes)
    FAILURE_RECORD = "failure_record"


# 各类型的基准优先级（衰减时作为权重基础）
# Base priority per type (used as weight foundation during decay)
TYPE_PRIORITY: dict[MemoryFactType, float] = {
    MemoryFactType.USER_PREFERENCE: 1.0,  # 用户偏好永不衰减
    # User preferences never decay
    MemoryFactType.FAILURE_RECORD: 0.95,  # 失败教训极其重要
    # Failure lessons are critically important
    MemoryFactType.DECISION_LOG: 0.85,  # 决策记录重要
    # Decision logs are important
    MemoryFactType.ARTIFACT_CREATED: 0.80,  # 文件成果重要
    # Created artifacts are important
    MemoryFactType.TOOL_RESULT: 0.70,  # 工具结果中等
    # Tool results are moderate priority
    MemoryFactType.RESEARCH_FINDING: 0.60,  # 研究发现可衰减
    # Research findings are decayable
    MemoryFactType.ENV_OBSERVATION: 0.50,  # 环境观测最容易衰减
    # Environment observations decay most easily
}


class MemoryFact(BaseModel):
    fact_id: str  # 唯一ID，格式：{agent_role}_{timestamp}_{序号}
    # Unique ID, format: {agent_role}_{timestamp}_{seq}
    fact_type: MemoryFactType  # 类型标签
    # Type label
    content: str  # 事实内容（精简的自然语言）
    # Fact content (concise natural language)
    source_agent: str  # 来源 Agent 角色
    # Source agent role
    mission_id: Optional[str] = None  # 关联的 Mission ID
    # Associated Mission ID
    subtask_id: Optional[str] = None  # 关联的 SubTask ID
    # Associated SubTask ID
    evidence_path: Optional[str] = None  # 物理证据文件路径（用于验证）
    # Physical evidence file path (for verification)
    confidence: float = 1.0  # 置信度 0.0~1.0
    # Confidence 0.0~1.0
    created_at: datetime = Field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None  # 过期时间（None=永不过期）
    # Expiration time (None = never expires)
    tags: List[str] = Field(default_factory=list)  # 便于检索的标签
    # Tags for searchability
    raw_data: Optional[Any] = None  # 可选的结构化原始数据
    # Optional structured raw data

    # --- 衰减与访问追踪字段 ---
    access_count: int = 0  # 被召回次数
    # Number of times retrieved
    last_accessed: Optional[datetime] = None  # 最后一次被召回的时间
    # Last retrieval time
    weight: float = 1.0  # 动态权重 0.0~1.0（衰减后的有效分数）
    # Dynamic weight 0.0~1.0 (effective score after decay)
    locked: bool = False  # 锁定后不参与衰减（核心知识保护）
    # When locked, exempt from decay (core knowledge protection)

    def compute_effective_score(self, now: Optional[datetime] = None) -> float:
        """计算综合有效分 = 类型基准优先级 × 动态权重 × 置信度"""
        if now is None:
            now = datetime.now()
        base_priority = TYPE_PRIORITY.get(self.fact_type, 0.5)
        return base_priority * self.weight * self.confidence

    def touch(self):
        """记录一次访问（召回时调用）"""
        self.access_count += 1
        self.last_accessed = datetime.now()


@dataclass
class TextChunk:
    """文本分块，用于索引和嵌入。"""

    chunk_id: str  # SHA-256 of content (16 hex chars)
    content: str  # 分块文本
    # Chunk text
    source_path: str  # 来源文件路径或 fact_id
    # Source file path or fact_id
    start_line: int  # 起始行号 (0-based)
    # Start line number (0-based)
    end_line: int  # 结束行号 (0-based, exclusive)
    # End line number (0-based, exclusive)
    token_count: int  # 估算 token 数
    # Estimated token count
    fact_id: Optional[str] = None  # 关联的 MemoryFact ID
    # Associated MemoryFact ID
    created_at: Optional[str] = None  # ISO 格式时间
    # ISO format timestamp
