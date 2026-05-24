"""SQLite 索引：FTS5 全文检索 + numpy 向量余弦相似度。"""

import logging
import re
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from .models import TextChunk

logger = logging.getLogger(__name__)


def _escape_fts5(query: str) -> str:
    """转义 FTS5 特殊字符，防止语法错误。"""
    # 移除 FTS5 运算符字符
    return re.sub(r'["*(){}[\]^~\\]', " ", query).strip()


def _escape_like(query: str) -> str:
    """转义 LIKE 通配符。"""
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SQLiteIndex:
    """
    SQLite 后端索引，三张表：
      chunks — 分块元数据
      embeddings — 向量 BLOB (numpy float32)
      fts_chunks — FTS5 虚拟表（BM25 全文检索）
    外加 metadata 表存配置（嵌入模型、维度等）。
    """

    def __init__(self, db_path: str = ".rooster/memory_index.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def close(self):
        """显式关闭 SQLite 连接，释放文件锁。"""
        try:
            self.conn.close()
        except Exception:
            pass

    def __del__(self):
        self.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _ensure_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                source_path TEXT NOT NULL,
                start_line INTEGER,
                end_line INTEGER,
                token_count INTEGER,
                fact_id TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS embeddings (
                chunk_id TEXT PRIMARY KEY,
                vector BLOB NOT NULL,
                FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
            );
        """)
        # FTS5 虚拟表（如果不存在）
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks "
                "USING fts5(content, chunk_id UNINDEXED, tokenize='unicode61 trigram')"
            )
        except sqlite3.OperationalError:
            # trigram tokenizer 不可用时回退到纯 unicode61
            try:
                self.conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS fts_chunks "
                    "USING fts5(content, chunk_id UNINDEXED, tokenize='unicode61')"
                )
            except sqlite3.OperationalError:
                logger.warning("FTS5 不可用，关键词检索降级")
        self.conn.commit()

    def get_meta(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES(?,?)",
            (key, value),
        )
        self.conn.commit()

    def upsert_chunks(self, chunks: List[TextChunk], vectors: List[List[float]]):
        """插入或更新分块及其向量（批量写入，单次事务提交）。"""
        if not chunks:
            return

        chunk_rows = [
            (c.chunk_id, c.content, c.source_path, c.start_line, c.end_line, c.token_count, c.fact_id, c.created_at)
            for c in chunks
        ]
        vec_rows = [(c.chunk_id, np.array(v, dtype=np.float32).tobytes()) for c, v in zip(chunks, vectors)]
        # FTS5 先批量删再批量插
        fts_ids = [(c.chunk_id,) for c in chunks]
        fts_rows = [(c.content, c.chunk_id) for c in chunks]

        self.conn.executemany(
            "INSERT OR REPLACE INTO chunks"
            "(chunk_id, content, source_path, start_line, end_line, "
            "token_count, fact_id, created_at) VALUES(?,?,?,?,?,?,?,?)",
            chunk_rows,
        )
        self.conn.executemany(
            "INSERT OR REPLACE INTO embeddings(chunk_id, vector) VALUES(?,?)",
            vec_rows,
        )
        try:
            self.conn.executemany("DELETE FROM fts_chunks WHERE chunk_id=?", fts_ids)
            self.conn.executemany("INSERT INTO fts_chunks(content, chunk_id) VALUES(?,?)", fts_rows)
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    def delete_by_source(self, source_path: str):
        """删除指定来源的所有分块。"""
        ids = [
            r[0]
            for r in self.conn.execute("SELECT chunk_id FROM chunks WHERE source_path=?", (source_path,)).fetchall()
        ]
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        self.conn.execute(f"DELETE FROM chunks WHERE chunk_id IN ({placeholders})", ids)
        self.conn.execute(f"DELETE FROM embeddings WHERE chunk_id IN ({placeholders})", ids)
        try:
            for cid in ids:
                self.conn.execute("DELETE FROM fts_chunks WHERE chunk_id=?", (cid,))
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    def delete_by_fact_id(self, fact_id: str):
        """删除关联到某 fact 的所有分块。"""
        ids = [r[0] for r in self.conn.execute("SELECT chunk_id FROM chunks WHERE fact_id=?", (fact_id,)).fetchall()]
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        self.conn.execute(f"DELETE FROM chunks WHERE chunk_id IN ({placeholders})", ids)
        self.conn.execute(f"DELETE FROM embeddings WHERE chunk_id IN ({placeholders})", ids)
        try:
            for cid in ids:
                self.conn.execute("DELETE FROM fts_chunks WHERE chunk_id=?", (cid,))
        except sqlite3.OperationalError:
            pass
        self.conn.commit()

    def clear(self):
        """清空所有索引数据。"""
        self.conn.executescript("""
            DELETE FROM chunks;
            DELETE FROM embeddings;
            DELETE FROM fts_chunks;
        """)
        self.conn.commit()

    def search_bm25(self, query: str, top_k: int = 20) -> List[Tuple[str, float]]:
        """FTS5 BM25 搜索。返回 [(chunk_id, score), ...]。"""
        escaped = _escape_fts5(query)
        if not escaped:
            return []
        try:
            rows = self.conn.execute(
                "SELECT chunk_id, bm25(fts_chunks) as rank "
                "FROM fts_chunks WHERE fts_chunks MATCH ? "
                "ORDER BY rank LIMIT ?",
                (escaped, top_k),
            ).fetchall()
            results = []
            for cid, rank in rows:
                score = 1.0 / (1.0 + abs(rank))
                results.append((cid, score))
            return results
        except sqlite3.OperationalError:
            # FTS5 不可用或查询语法错误，降级为 LIKE
            like_query = _escape_like(query)
            rows = self.conn.execute(
                "SELECT chunk_id FROM chunks WHERE content LIKE ? ESCAPE '\\' LIMIT ?",
                (f"%{like_query}%", top_k),
            ).fetchall()
            return [(r[0], 0.5) for r in rows]

    def search_vector(self, query_embedding: List[float], top_k: int = 20) -> List[Tuple[str, float]]:
        """暴力余弦相似度搜索（numpy 加速）。"""
        rows = self.conn.execute("SELECT chunk_id, vector FROM embeddings").fetchall()
        if not rows:
            return []

        q = np.array(query_embedding, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []

        q_dim = len(q)
        ids = []
        sims = []
        for cid, vec_blob in rows:
            vec = np.frombuffer(vec_blob, dtype=np.float32)
            if len(vec) != q_dim:
                logger.warning(f"向量维度不匹配: query={q_dim}, stored={len(vec)}, skip {cid}")
                continue
            v_norm = np.linalg.norm(vec)
            if v_norm == 0:
                continue
            sim = float(np.dot(q, vec) / (q_norm * v_norm))
            ids.append(cid)
            sims.append(sim)

        if not ids:
            return []
        indices = np.argsort(sims)[::-1][:top_k]
        return [(ids[i], sims[i]) for i in indices if sims[i] > 0]

    def get_chunk(self, chunk_id: str) -> Optional[TextChunk]:
        row = self.conn.execute(
            "SELECT chunk_id, content, source_path, start_line, end_line, "
            "token_count, fact_id, created_at FROM chunks WHERE chunk_id=?",
            (chunk_id,),
        ).fetchone()
        if not row:
            return None
        return TextChunk(
            chunk_id=row[0],
            content=row[1],
            source_path=row[2],
            start_line=row[3],
            end_line=row[4],
            token_count=row[5],
            fact_id=row[6],
            created_at=row[7],
        )

    def get_chunks_by_source(self, source_path: str) -> List[TextChunk]:
        rows = self.conn.execute(
            "SELECT chunk_id, content, source_path, start_line, end_line, "
            "token_count, fact_id, created_at FROM chunks WHERE source_path=?",
            (source_path,),
        ).fetchall()
        return [
            TextChunk(
                chunk_id=r[0],
                content=r[1],
                source_path=r[2],
                start_line=r[3],
                end_line=r[4],
                token_count=r[5],
                fact_id=r[6],
                created_at=r[7],
            )
            for r in rows
        ]

    def chunk_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
