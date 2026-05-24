"""Rooster 记忆系统 v3.0 — 结构化存储 + 真实嵌入 + SQLite 索引 + Markdown 后端 + 会话索引。"""

from .models import MemoryFact, MemoryFactType, TYPE_PRIORITY, TextChunk
from .backends import JSONFileBackend, MarkdownBackend, MemoryBackend
from .semantic_search import SemanticMemorySearch
from .dedup import MemoryDeduplicator, MemoryAuditor
from .manager import MemoryManager

__all__ = [
    "MemoryFact",
    "MemoryFactType",
    "TYPE_PRIORITY",
    "TextChunk",
    "MemoryBackend",
    "JSONFileBackend",
    "MarkdownBackend",
    "SemanticMemorySearch",
    "MemoryDeduplicator",
    "MemoryAuditor",
    "MemoryManager",
]
