"""会话 transcript 索引：将历史对话纳入可搜索记忆。"""

import json
import logging
from pathlib import Path
from typing import List, Dict

from .chunker import chunk_text
from .indexer import SQLiteIndex
from .embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)


class SessionIndexer:
    """
    会话 transcript 索引器。

    读取 .rooster/sessions/*.json，提取消息，分块嵌入后存入 SQLiteIndex。
    source 标记为 "session:{session_id}"，与记忆事实隔离。
    通过 metadata 表跟踪已索引的会话，支持增量更新。
    """

    def __init__(
        self,
        sessions_dir: str,
        index: SQLiteIndex,
        embedder: EmbeddingProvider,
    ):
        self.sessions_dir = Path(sessions_dir)
        self.index = index
        self.embedder = embedder

    def _list_sessions(self) -> List[Path]:
        if not self.sessions_dir.exists():
            return []
        return sorted(self.sessions_dir.glob("*.json"))

    def _load_session_messages(self, path: Path) -> List[Dict[str, str]]:
        """加载会话的消息列表。"""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                messages = data.get("history", data.get("messages", []))
            elif isinstance(data, list):
                messages = data
            else:
                return []
            # 只保留 user 和 assistant 消息
            return [m for m in messages if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
        except Exception as e:
            logger.warning(f"加载会话 {path.name} 失败: {e}")
            return []

    def _messages_to_text(self, messages: List[Dict[str, str]]) -> str:
        """将消息列表转为可索引的文本。"""
        lines = []
        for m in messages:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if not content:
                continue
            # 截断过长消息
            if len(content) > 1000:
                content = content[:1000] + "..."
            label = "User" if role == "user" else "Assistant"
            lines.append(f"{label}: {content}")
        return "\n".join(lines)

    def _get_indexed_count(self, session_id: str) -> int:
        """查询已索引的消息数。"""
        val = self.index.get_meta(f"session:{session_id}:msg_count")
        return int(val) if val else 0

    def _set_indexed_count(self, session_id: str, count: int):
        """记录已索引的消息数。"""
        self.index.set_meta(f"session:{session_id}:msg_count", str(count))

    async def index_session(self, session_id: str):
        """索引单个会话（增量：只处理新增消息）。"""
        session_file = self.sessions_dir / f"{session_id}.json"
        if not session_file.exists():
            return

        messages = self._load_session_messages(session_file)
        if not messages:
            return

        already_indexed = self._get_indexed_count(session_id)
        if len(messages) <= already_indexed:
            return  # 无新增

        # 只索引新增消息
        new_messages = messages[already_indexed:]
        text = self._messages_to_text(new_messages)
        if not text.strip():
            return

        source = f"session:{session_id}"
        # 删除旧的该会话分块（如果有），重新索引全部
        # （因为会话是连续对话，上下文关联性强，不适合只索引片段）
        if already_indexed > 0:
            full_text = self._messages_to_text(messages)
        else:
            full_text = text

        self.index.delete_by_source(source)
        chunks = chunk_text(full_text, source_path=source)
        if not chunks:
            return

        texts = [c.content for c in chunks]
        vectors = await self.embedder.embed(texts)
        self.index.upsert_chunks(chunks, vectors)

        self._set_indexed_count(session_id, len(messages))
        logger.debug(f"会话 {session_id} 索引完成: {len(messages)} 条消息 → {len(chunks)} 个分块")

    async def index_all(self):
        """扫描并索引所有会话。"""
        sessions = self._list_sessions()
        for path in sessions:
            session_id = path.stem
            try:
                await self.index_session(session_id)
            except Exception as e:
                logger.warning(f"索引会话 {session_id} 失败: {e}")
        logger.info(f"会话索引完成: {len(sessions)} 个会话")

    async def search_sessions(self, query: str, top_k: int = 10) -> List[Dict]:
        """
        搜索会话 transcript。
        返回 [{session_id, chunk_content, score, source}, ...]
        """
        # BM25 搜索
        bm25_results = self.index.search_bm25(query, top_k=top_k * 2)
        bm25_scores = {cid: score for cid, score in bm25_results}

        # 向量搜索
        try:
            query_vecs = await self.embedder.embed([query])
            query_vec = query_vecs[0] if query_vecs else None
        except Exception:
            query_vec = None

        vec_scores = {}
        if query_vec:
            vec_results = self.index.search_vector(query_vec, top_k=top_k * 2)
            vec_scores = {cid: score for cid, score in vec_results}

        # 合并
        all_cids = set(bm25_scores.keys()) | set(vec_scores.keys())
        results = []
        for cid in all_cids:
            chunk = self.index.get_chunk(cid)
            if not chunk or not chunk.source_path.startswith("session:"):
                continue
            score = 0.5 * bm25_scores.get(cid, 0) + 0.5 * vec_scores.get(cid, 0)
            session_id = chunk.source_path.replace("session:", "")
            results.append(
                {
                    "session_id": session_id,
                    "chunk_content": chunk.content[:300],
                    "score": score,
                    "source": "session",
                }
            )

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]
