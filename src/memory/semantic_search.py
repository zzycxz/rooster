"""
混合语义检索层 v3：
- 新路径：SQLite 索引 + 真实嵌入 + BM25 FTS5 + 衰减评分
- 旧路径：内存 BM25 + n-gram 哈希 + 衰减评分（向后兼容）
"""

import asyncio
import hashlib
import logging
import math
import re
from datetime import datetime
from typing import List

from .models import MemoryFact, TYPE_PRIORITY

logger = logging.getLogger(__name__)


def _tokenize(text: str) -> List[str]:
    """中英文混合分词"""
    try:
        import jieba

        return list(jieba.cut(text.lower()))
    except ImportError:
        tokens = []
        for char in text.lower():
            if "一" <= char <= "鿿":
                tokens.append(char)
        english_parts = re.sub(r"[一-鿿]", " ", text.lower()).split()
        tokens.extend(english_parts)
        return tokens if tokens else text.lower().split()


# ─── 向量检索：字符 n-gram 哈希嵌入（旧路径 fallback）───────────

DIMENSION = 256


def _char_ngram_embedding(text: str, n: int = 3, dim: int = DIMENSION) -> List[float]:
    vec = [0.0] * dim
    text_lower = text.lower().strip()
    if len(text_lower) < n:
        text_lower = text_lower.ljust(n, " ")
    for i in range(len(text_lower) - n + 1):
        gram = text_lower[i : i + n]
        h = int(hashlib.md5(gram.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1 if (h // dim) % 2 == 0 else -1
        vec[idx] += sign
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ─── 衰减评分 ──────────────────────────────────────────────────


def _decay_weight(fact: MemoryFact, now: datetime) -> float:
    if fact.locked:
        return fact.weight

    ref_time = fact.last_accessed or fact.created_at
    if isinstance(ref_time, str):
        try:
            ref_time = datetime.fromisoformat(ref_time)
        except ValueError:
            ref_time = now

    days_elapsed = max((now - ref_time).total_seconds() / 86400, 0)
    half_life_days = 7.0
    time_decay = math.exp(-0.693 * days_elapsed / half_life_days)

    access_boost = min(math.log(1 + fact.access_count) * 0.2, 0.5)
    time_decay = min(time_decay + access_boost, 1.0)
    return time_decay


# ─── 混合检索引擎 ──────────────────────────────────────────────


class SemanticMemorySearch:
    """
    混合语义检索引擎 v3。

    新模式（传入 index + embedder）：
      SQLite FTS5 + 真实向量相似度 + 衰减评分

    旧模式（无参数构造）：
      内存 BM25 + n-gram 哈希 + 衰减评分
    """

    def __init__(
        self,
        bm25_weight: float = 0.5,
        vector_weight: float = 0.3,
        decay_weight: float = 0.2,
        # v3 新参数（可选）
        embedder=None,
        index=None,
    ):
        self.w_bm25 = bm25_weight
        self.w_vector = vector_weight
        self.w_decay = decay_weight

        self.facts: List[MemoryFact] = []

        # 新路径
        self._embedder = embedder
        self._index = index
        self._use_new_path = embedder is not None and index is not None

        # 旧路径
        self.bm25 = None
        self.embeddings: List[List[float]] = []

    def fit(self, facts: List[MemoryFact]):
        """构建索引（同步）。始终构建内存 BM25；新路径同时更新 SQLite。"""
        self.facts = facts
        self._fit_legacy(facts)
        if self._use_new_path:
            self._sync_rebuild_sqlite(facts)

    async def fit_async(self, facts: List[MemoryFact], batch_size: int = 64):
        """异步构建索引（新路径：分块 + 批量嵌入 + SQLite）。

        改进：先收集所有 chunk，再按 batch_size 一次性调用嵌入，
        避免逐条调用导致的 N 次单条嵌入开销。
        """
        self.facts = facts
        if not self._use_new_path:
            self._fit_legacy(facts)
            return

        from .chunker import chunk_text
        from .models import TextChunk

        # ── 1. 收集所有 chunk（仅分块，不嵌入）──
        all_chunks: List[TextChunk] = []
        indexed_ids: set = set()

        for fact in facts:
            try:
                chunks = chunk_text(
                    fact.content,
                    source_path=fact.fact_id,
                    fact_id=fact.fact_id,
                    created_at=fact.created_at.isoformat()
                    if isinstance(fact.created_at, datetime)
                    else str(fact.created_at),
                )
                if not chunks:
                    chunks = [
                        TextChunk(
                            chunk_id=f"fact_{fact.fact_id}",
                            content=fact.content,
                            source_path=fact.fact_id,
                            start_line=0,
                            end_line=0,
                            token_count=len(fact.content),
                            fact_id=fact.fact_id,
                            created_at=fact.created_at.isoformat()
                            if isinstance(fact.created_at, datetime)
                            else str(fact.created_at),
                        )
                    ]
                all_chunks.extend(chunks)
                indexed_ids.add(fact.fact_id)
            except Exception as e:
                logger.warning(f"分块事实 {fact.fact_id} 失败，跳过: {e}")

        # ── 2. 批量嵌入（一次 embed 调用处理 batch_size 条）──
        all_vectors: List[List[float]] = []
        texts = [c.content for c in all_chunks]
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                vecs = await self._embedder.embed(batch)
                all_vectors.extend(vecs)
            except Exception as e:
                logger.warning(f"批量嵌入 [{i}:{i + batch_size}] 失败，用零向量占位: {e}")
                dim = getattr(self._embedder, "dimension", 512)
                all_vectors.extend([[0.0] * dim] * len(batch))

        # ── 3. 一次性写入 SQLite ──
        if all_chunks and all_vectors:
            self._index.upsert_chunks(all_chunks, all_vectors)

        # ── 4. 清理已删除事实的残留分块 ──
        all_source_paths = {f.fact_id for f in facts}
        for row in self._index.conn.execute("SELECT DISTINCT source_path FROM chunks").fetchall():
            sp = row[0]
            if sp not in all_source_paths:
                self._index.delete_by_source(sp)

        logger.info(
            f"批量索引完成: {len(indexed_ids)}/{len(facts)} 条事实，"
            f"{len(all_chunks)} 个分块，{len(all_vectors)} 条向量"
            f" → SQLite 共 {self._index.chunk_count()} 块"
        )

    def _sync_rebuild_sqlite(self, facts: List[MemoryFact]):
        """同步初始化时跳过嵌入计算，仅标记待索引。异步路径由 fit_async() 完成。"""
        pass

    def _fit_legacy(self, facts: List[MemoryFact]):
        """旧路径：内存 BM25 + n-gram。"""
        if not facts:
            self.bm25 = None
            self.embeddings = []
            return
        try:
            from rank_bm25 import BM25Okapi

            tokenized_corpus = [_tokenize(f.content) for f in facts]
            self.bm25 = BM25Okapi(tokenized_corpus)
        except ImportError:
            self.bm25 = None
        self.embeddings = [_char_ngram_embedding(f.content) for f in facts]

    def _run_async(self, coro):
        """在同步上下文中运行异步协程。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            # 已在异步上下文中，用线程池运行
            import concurrent.futures

            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=60)
            except concurrent.futures.TimeoutError:
                logger.warning("异步索引操作超时 (60s)")
                raise
            finally:
                pool.shutdown(wait=False)
        else:
            return asyncio.run(coro)

    def retrieve(self, query: str, top_k: int = 15, touch: bool = True) -> List[MemoryFact]:
        """
        混合检索：BM25 + 向量 + 衰减，三路分数融合后排序。
        touch=True 时自动更新访问计数。
        """
        if not self.facts:
            return []

        if not query:
            decay_scores = self._decay_scores()
            paired = sorted(zip(self.facts, decay_scores), key=lambda x: x[1], reverse=True)
            return [f for f, _ in paired[:top_k]]

        if self._use_new_path:
            results = self._retrieve_new(query, top_k)
        else:
            results = self._retrieve_legacy(query, top_k)

        if touch:
            for fact in results:
                fact.touch()
        return results

    def _retrieve_new(self, query: str, top_k: int) -> List[MemoryFact]:
        """新路径：SQLite FTS5 + 向量 + 衰减。"""
        # 异步嵌入查询
        query_vec = None
        try:
            query_vecs = self._run_async(self._embedder.embed([query]))
            query_vec = query_vecs[0] if query_vecs else None
        except Exception as e:
            logger.warning(f"查询嵌入失败，降级到旧路径: {e}")
            return self._retrieve_legacy(query, top_k)

        # BM25
        bm25_results = self._index.search_bm25(query, top_k=top_k * 2)
        bm25_scores = {cid: score for cid, score in bm25_results}

        # 向量
        vec_scores = {}
        if query_vec:
            vec_results = self._index.search_vector(query_vec, top_k=top_k * 2)
            vec_scores = {cid: score for cid, score in vec_results}

        # 合并所有 chunk_id
        all_cids = set(bm25_scores.keys()) | set(vec_scores.keys())
        if not all_cids:
            return self._retrieve_legacy(query, top_k)

        # 衰减：按 fact_id 聚合
        fact_decay = {}
        for fact in self.facts:
            base = TYPE_PRIORITY.get(fact.fact_type, 0.5)
            decay = _decay_weight(fact, datetime.now())
            fact_decay[fact.fact_id] = base * decay * fact.confidence

        # chunk_id → fact_id 映射 + 融合分数
        fact_scores: dict[str, float] = {}
        for cid in all_cids:
            chunk = self._index.get_chunk(cid)
            if not chunk or not chunk.fact_id:
                continue
            fid = chunk.fact_id
            bm25_s = bm25_scores.get(cid, 0.0)
            vec_s = vec_scores.get(cid, 0.0)
            decay_s = fact_decay.get(fid, 0.5)
            score = self.w_bm25 * bm25_s + self.w_vector * vec_s + self.w_decay * decay_s
            fact_scores[fid] = max(fact_scores.get(fid, 0.0), score)

        # 排序取 top_k
        sorted_fids = sorted(fact_scores.keys(), key=lambda f: fact_scores[f], reverse=True)
        fact_map = {f.fact_id: f for f in self.facts}
        results = [fact_map[fid] for fid in sorted_fids[:top_k] if fid in fact_map]

        if not results:
            return self._retrieve_legacy(query, top_k)
        return results

    def _retrieve_legacy(self, query: str, top_k: int) -> List[MemoryFact]:
        """旧路径：内存 BM25 + n-gram + 衰减。"""
        bm25_s = self._bm25_scores(query)
        vec_s = self._vector_scores(query)
        decay_s = self._decay_scores()

        final_scores = []
        for i in range(len(self.facts)):
            score = self.w_bm25 * bm25_s[i] + self.w_vector * vec_s[i] + self.w_decay * decay_s[i]
            final_scores.append(score)

        paired = sorted(
            zip(self.facts, final_scores),
            key=lambda x: x[1],
            reverse=True,
        )
        results = [fact for fact, score in paired if score > 0][:top_k]
        if not results:
            results = [fact for fact, _ in paired[:top_k]]
        return results

    def _bm25_scores(self, query: str) -> List[float]:
        if not self.bm25 or not query:
            return [0.0] * len(self.facts)
        scores = self.bm25.get_scores(_tokenize(query))
        if hasattr(scores, "tolist"):
            scores = scores.tolist()
        max_s = max(scores) if scores else 1.0
        if max_s > 0:
            scores = [s / max_s for s in scores]
        return scores

    def _vector_scores(self, query: str) -> List[float]:
        if not query or not self.embeddings:
            return [0.0] * len(self.facts)
        q_emb = _char_ngram_embedding(query)
        return [_cosine_similarity(q_emb, emb) for emb in self.embeddings]

    def _decay_scores(self) -> List[float]:
        now = datetime.now()
        scores = []
        for fact in self.facts:
            base = TYPE_PRIORITY.get(fact.fact_type, 0.5)
            decay = _decay_weight(fact, now)
            scores.append(base * decay * fact.confidence)
        return scores

    def update_fact_weight(self, fact_id: str, new_weight: float):
        for fact in self.facts:
            if fact.fact_id == fact_id:
                fact.weight = max(0.0, min(1.0, new_weight))
                break
