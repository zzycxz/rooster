"""
Rooster 记忆管理器 v3.0
集成：结构化存储 + Markdown后端 + 真实嵌入 + SQLite索引 + 分块检索
      + 会话索引 + 文件监听 + 压缩前刷写 + 衰减 + 去重 + 审计
"""

import hashlib
import json
import logging
import re
import asyncio
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path

from .models import MemoryFact, MemoryFactType
from .backends import JSONFileBackend
from .semantic_search import SemanticMemorySearch
from .dedup import MemoryDeduplicator, MemoryAuditor

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    Rooster 记忆管理器 v3.0 (LTM 长期记忆核心)

    v3 新增：
    - MarkdownBackend（人类可读、git 友好）
    - 真实嵌入（OpenAI 兼容 API，自动降级到 n-gram）
    - SQLite 索引（FTS5 BM25 + numpy 向量余弦相似度）
    - 文本分块（400 token 块 + 80 token 重叠）
    - 会话 transcript 索引（可搜索历史对话）
    - 文件变更监听（自动重索引）
    - 压缩前刷写（上下文接近上限时自动蒸馏）
    """

    def __init__(
        self,
        storage_path: str = ".rooster/project_memory.json",
        llm_client=None,
        model: str = "",
        # --- v3 新参数 ---
        backend_type: Optional[str] = None,  # "json" | "markdown" | None(自动)
        embedder=None,  # EmbeddingProvider 实例
        enable_session_index: bool = False,
        enable_file_watcher: bool = False,
    ):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.llm_client = llm_client
        self.model = model
        self._last_flush_hash: Optional[str] = None
        self._background_tasks: set = set()
        self._housekeeping_running: bool = False

        # ─── 1. 后端选择 ───
        if backend_type is None:
            from utils.config import settings

            backend_type = settings.MEMORY_BACKEND_TYPE
        self._backend_type = backend_type

        if backend_type == "markdown":
            from .backends import MarkdownBackend

            base_dir = str(self.storage_path.parent)
            self.backend = MarkdownBackend(base_dir=base_dir)
        else:
            self.backend = JSONFileBackend(str(self.storage_path))

        # ─── 2. 嵌入提供者 ───
        self._embedder = embedder
        if self._embedder is None:
            try:
                from .embeddings import create_embedder
                from utils.config import settings

                self._embedder = create_embedder(
                    provider=settings.EMBEDDING_PROVIDER,
                    base_url=settings.EMBEDDING_URL,
                    api_key=settings.EMBEDDING_KEY,
                    model=settings.EMBEDDING_MODEL,
                    local_model=settings.EMBEDDING_LOCAL_MODEL,
                )
            except Exception as e:
                logger.warning(f"嵌入提供者创建失败: {e}")

        # ─── 3. SQLite 索引 ───
        self._index = None
        try:
            from .indexer import SQLiteIndex

            db_path = str(self.storage_path.parent / "memory_index.db")
            self._index = SQLiteIndex(db_path=db_path)
        except Exception as e:
            logger.warning(f"SQLite 索引创建失败: {e}")

        # ─── 4. 检索引擎 ───
        use_new_path = self._embedder is not None and self._index is not None
        self.search = SemanticMemorySearch(
            bm25_weight=0.5,
            vector_weight=0.3,
            decay_weight=0.2,
            embedder=self._embedder if use_new_path else None,
            index=self._index if use_new_path else None,
        )

        # ─── 5. 去重 + 审计（使用本地模型，记忆数据不出本机）───
        # ─── 5. Dedup + audit (use local model, memory data stays local) ───
        _hk_client = llm_client
        _hk_model = model
        try:
            from models.factory import ModelFactory
            from utils.config import settings as _s

            _hk_client = ModelFactory.get_client("local")
            _hk_model = _s.LOCAL_MODEL or model
        except Exception:
            pass  # 本地模型不可用则用传入的 client / Fallback to passed-in client
        self.deduplicator = MemoryDeduplicator(_hk_client, _hk_model)
        self.auditor = MemoryAuditor(_hk_client, _hk_model)

        # ─── 6. 会话索引 ───
        self._session_indexer = None
        if enable_session_index and self._index and self._embedder:
            try:
                from .session_index import SessionIndexer

                sessions_dir = str(self.storage_path.parent / "sessions")
                self._session_indexer = SessionIndexer(
                    sessions_dir=sessions_dir,
                    index=self._index,
                    embedder=self._embedder,
                )
            except Exception as e:
                logger.warning(f"会话索引器创建失败: {e}")

        # ─── 7. 文件监听 ───
        self._watcher = None
        if enable_file_watcher:
            try:
                from .watcher import MemoryFileWatcher

                watch_paths = []
                if backend_type == "markdown":
                    base = self.storage_path.parent
                    watch_paths.append(str(base / "MEMORY.md"))
                    watch_paths.append(str(base / "memory" / "daily"))
                self._watcher = MemoryFileWatcher(
                    watch_paths=watch_paths,
                    callback=self._on_files_changed,
                )
            except Exception as e:
                logger.warning(f"文件监听器创建失败: {e}")

        # ─── 8. 初始化 ───
        # 首次 v3 启动：自动迁移 JSON → Markdown
        if backend_type == "markdown":
            try:
                from .migrate import auto_migrate_if_needed

                auto_migrate_if_needed(
                    json_path=str(self.storage_path),
                    base_dir=str(self.storage_path.parent),
                    backend_type=backend_type,
                )
                self.backend.reload()  # 重新加载迁移后的数据
            except Exception as e:
                logger.warning(f"JSON→Markdown 迁移失败: {e}")

        self._migrate_legacy_facts()
        self._rebuild_index()  # 同步构建 BM25（快）

    # ─── 旧格式兼容 ──────────────────────────────────────────

    def _migrate_legacy_facts(self):
        """将旧的字符串事实迁移到 MemoryFact 结构"""
        if not self.storage_path.exists():
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        if not isinstance(data, dict):
            return

        legacy_facts = data.get("project_facts", [])
        if not legacy_facts:
            return

        existing_ids = {f.fact_id for f in self.backend.facts}
        migrated = 0
        for i, fact_str in enumerate(legacy_facts):
            fid = f"legacy_{i}"
            if fid in existing_ids:
                continue
            mf = MemoryFact(
                fact_id=fid,
                fact_type=MemoryFactType.RESEARCH_FINDING,
                content=fact_str,
                source_agent="system",
                weight=0.8,
            )
            self.backend.add_fact(mf)
            migrated += 1

        if migrated > 0:
            logger.info(f"迁移了 {migrated} 条旧格式事实到结构化存储")

        try:
            data.pop("project_facts", None)
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # ─── 索引构建 ──────────────────────────────────────────

    def _rebuild_index(self):
        """构建检索索引（同步兼容旧路径）。"""
        facts = self.backend.get_all_facts()
        self.search.fit(facts)

    async def _rebuild_index_async(self):
        """异步构建索引（新路径：分块 + 嵌入 + SQLite）。"""
        facts = self.backend.get_all_facts()
        if hasattr(self.search, "fit_async"):
            await self.search.fit_async(facts)
        else:
            self.search.fit(facts)

    async def _on_files_changed(self, changed_files: List[str]):
        """文件变更回调：重索引。"""
        logger.debug(f"文件变更，重索引: {changed_files}")
        self.backend._load()
        await self._rebuild_index_async()

    async def start_watcher(self):
        """启动文件监听（需手动调用）。"""
        if self._watcher:
            await self._watcher.start()

    async def stop_watcher(self):
        """停止文件监听。"""
        if self._watcher:
            await self._watcher.stop()

    # ─── 后台任务管理 ──────────────────────────────────

    def _fire_background(self, coro):
        """创建后台 Task 并保持强引用，防止 GC 提前回收。"""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    # ─── 核心 API：写入 ──────────────────────────────────

    def update_fact(
        self,
        content: str,
        fact_type: MemoryFactType = MemoryFactType.RESEARCH_FINDING,
        source_agent: str = "system",
        confidence: float = 1.0,
        tags: Optional[List[str]] = None,
        locked: bool = False,
        mission_id: Optional[str] = None,
        subtask_id: Optional[str] = None,
        evidence_path: Optional[str] = None,
    ):
        """记录一条结构化事实"""
        import uuid

        now = datetime.now()
        fact = MemoryFact(
            fact_id=f"{source_agent}_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}",
            fact_type=fact_type,
            content=content,
            source_agent=source_agent,
            mission_id=mission_id,
            subtask_id=subtask_id,
            evidence_path=evidence_path,
            confidence=confidence,
            created_at=now,
            tags=tags or [],
            locked=locked,
            weight=1.0,
        )
        self.backend.add_fact(fact)
        self._rebuild_index()
        logger.debug(f"记录事实: [{fact_type.value}] {content[:60]}...")

    def record_artifact(self, path: str, description: str):
        """记录生成的文件成果"""
        self.update_fact(
            content=f"生成了文件: {path} — {description}",
            fact_type=MemoryFactType.ARTIFACT_CREATED,
            evidence_path=path,
            confidence=1.0,
        )

    def record_failure(self, content: str, source_agent: str = "system"):
        """记录失败教训"""
        self.update_fact(
            content=content,
            fact_type=MemoryFactType.FAILURE_RECORD,
            source_agent=source_agent,
            confidence=1.0,
        )

    def record_preference(self, content: str):
        """记录用户偏好"""
        self.update_fact(
            content=content,
            fact_type=MemoryFactType.USER_PREFERENCE,
            source_agent="user",
            confidence=1.0,
            locked=True,
        )

    def record_decision(self, content: str, source_agent: str = "system"):
        """记录决策"""
        self.update_fact(
            content=content,
            fact_type=MemoryFactType.DECISION_LOG,
            source_agent=source_agent,
        )

    # ─── 核心 API：读取 ──────────────────────────────────

    def get_summary_for_prompt(
        self,
        query: Optional[str] = None,
        max_chars: int = 2000,
        top_k: int = 15,
    ) -> str:
        """为 System Prompt 生成记忆浓缩背景，支持语义召回"""
        all_facts = self.backend.get_all_facts()
        if not all_facts:
            return ""

        lines = ["# 长期记忆 (LTM Context):"]

        if query:
            relevant = self.search.retrieve(query, top_k=top_k)
        else:
            relevant = self.backend.get_by_priority(limit=top_k)

        if relevant:
            lines.append("## 关键事实:")
            for f in relevant:
                lock_tag = " [锁定]" if f.locked else ""
                lines.append(f"- [{f.fact_type.value}]{lock_tag} {f.content}")

        full_text = "\n".join(lines)
        if len(full_text) > max_chars:
            return full_text[:max_chars] + "\n... (记忆已截断) ..."
        return full_text

    # ─── 核心 API：衰减维护 ──────────────────────────────

    def apply_decay(self):
        """对所有未锁定的事实执行衰减。"""
        from .semantic_search import _decay_weight

        now = datetime.now()
        for fact in self.backend.get_all_facts():
            if fact.locked:
                continue
            time_factor = _decay_weight(fact, now)
            new_weight = max(0.05, fact.weight * time_factor)
            if abs(new_weight - fact.weight) > 0.01:
                self.backend.update_fact(fact.fact_id, weight=new_weight)
        self._rebuild_index()
        logger.debug("记忆衰减已执行")

    def lock_fact(self, fact_id: str):
        self.backend.update_fact(fact_id, locked=True)

    def unlock_fact(self, fact_id: str):
        self.backend.update_fact(fact_id, locked=False)

    # ─── 核心 API：智能去重 ──────────────────────────────

    async def deduplicate(self):
        facts = self.backend.get_all_facts()
        if len(facts) < 2:
            return
        deduplicated = await self.deduplicator.deduplicate(facts)
        if len(deduplicated) < len(facts):
            removed_ids = {f.fact_id for f in facts} - {f.fact_id for f in deduplicated}
            for fid in removed_ids:
                self.backend.remove_fact(fid)
            for f in deduplicated:
                if f.fact_id.startswith("merged_"):
                    self.backend.add_fact(f)
            await self._rebuild_index_async()
            logger.info(f"智能去重完成：{len(facts)} → {len(deduplicated)} 条")

    # ─── 核心 API：质量审计 ──────────────────────────────

    async def audit(self) -> Dict:
        facts = self.backend.get_all_facts()
        report = await self.auditor.audit(facts)
        for fid in report.get("stale", []):
            self.backend.remove_fact(fid)
        for fid in report.get("low_quality", []):
            self.backend.update_fact(fid, weight=0.1)
        for fid in report.get("lock_suggested", []):
            self.backend.update_fact(fid, locked=True)
        await self._rebuild_index_async()
        return report

    # ─── 核心 API：对话蒸馏 ──────────────────────────────

    @staticmethod
    def _sanitize_for_memory_extraction(content: str) -> str:
        """剥离用户消息中的伪系统指令注入模式，防止蒸馏污染 LTM。"""
        sanitized = content
        # 移除伪角色标签注入：[SYSTEM]、[ADMIN]、[INSTRUCTION] 等
        sanitized = re.sub(
            r"\[(?:SYSTEM|ADMIN|INSTRUCTION|OVERRIDE|IGNORE|IMPORTANT)\]",
            "[USER]",
            sanitized,
            flags=re.IGNORECASE,
        )
        # 移除常见 prompt injection 前缀
        injection_patterns = [
            r"ignore\s+(?:previous|above|all)\s+instructions?",
            r"forget\s+(?:everything|all|previous)",
            r"you\s+are\s+now\s+(?:a\s+)?(?:different|new)",
            r"new\s+(?:system\s+)?(?:instruction|prompt|rule)",
            r"(?:disregard|disobey|override)\s+(?:the\s+)?(?:above|previous|system)",
        ]
        for pat in injection_patterns:
            sanitized = re.sub(pat, "[redacted]", sanitized, flags=re.IGNORECASE)
        return sanitized

    async def distill_history(self, llm_client, model: str, history: List[Dict[str, str]]):
        if len(history) < 3:
            return

        context_str = ""
        for m in history[-10:]:
            role = m.get("role", "user")
            raw = m.get("content", "")[:500]
            if role == "user":
                raw = self._sanitize_for_memory_extraction(raw)
            context_str += f"{role}: {raw}\n"

        prompt = (
            "### 指令:\n"
            "你是记忆整理专家。从对话中提取关键事实，每条事实用以下格式：\n"
            "[类型] 内容\n"
            "类型可选：TOOL_RESULT / ARTIFACT_CREATED / DECISION_LOG / RESEARCH_FINDING / "
            "ENV_OBSERVATION / USER_PREFERENCE / FAILURE_RECORD\n"
            "要求：\n"
            "1. 每条一行，严禁废话\n"
            "2. 提及文件时使用绝对路径\n"
            "3. 最多提取 5 条\n\n"
            f"### 对话历史:\n{context_str}"
        )

        try:
            if hasattr(llm_client, "chat_non_stream"):
                resp = await llm_client.chat_non_stream(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                )
                result = resp.content.strip()
            elif hasattr(llm_client, "chat_stream"):
                result = ""
                async for delta in llm_client.chat_stream(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                ):
                    if delta.content:
                        result += delta.content
                result = result.strip()
            else:
                logger.warning("[MemoryManager] distill_history: LLM client has no chat method, skipping distillation")
                return

            if not result:
                return

            type_map = {t.value.upper(): t for t in MemoryFactType}
            count = 0
            for line in result.split("\n"):
                line = line.strip("- *").strip()
                if len(line) < 5:
                    continue
                match = re.match(r"\[(\w+)\]\s*(.*)", line)
                if match:
                    type_str = match.group(1).upper()
                    content = match.group(2).strip()
                    fact_type = type_map.get(type_str, MemoryFactType.RESEARCH_FINDING)
                else:
                    content = line
                    fact_type = MemoryFactType.RESEARCH_FINDING

                existing = self.search.retrieve(content, top_k=1, touch=False)
                if existing and existing[0].content == content:
                    continue

                self.update_fact(
                    content=content,
                    fact_type=fact_type,
                    source_agent="distillation",
                    confidence=0.8,
                )
                count += 1

            logger.info(f"对话蒸馏完成，提取了 {count} 条新事实")

            if len(self.backend.get_all_facts()) > 30:

                async def _run_audit():
                    try:
                        await self.audit()
                    except Exception as e:
                        logger.error(f"后台审计失败: {e}")

                self._fire_background(_run_audit())

        except Exception as e:
            logger.error(f"对话蒸馏失败: {e}")

    # ─── v3 新 API：压缩前刷写 ──────────────────────────────

    async def flush_before_compaction(self, session_id: str, history: List[Dict[str, str]]):
        """
        压缩前自动刷写：上下文接近上限时，将近期对话蒸馏为持久记忆。
        使用内容 hash 去重，避免重复刷写。
        """
        if len(history) < 3:
            return

        # 内容 hash 去重
        recent = json.dumps(history[-6:], ensure_ascii=False)
        content_hash = hashlib.md5(recent.encode()).hexdigest()
        if content_hash == self._last_flush_hash:
            return
        self._last_flush_hash = content_hash

        logger.info(f"触发压缩前刷写: session={session_id}, {len(history)} 条消息")
        await self.distill_history(self.llm_client, self.model, history)

    # ─── v3 新 API：定期维护（多阈值触发）──────────────────────

    # Housekeeping thresholds – all checked on every call, each has its own cooldown.
    _HK_FACT_DEDUPE = 30  # facts count → run dedup
    _HK_FACT_AUDIT = 50  # facts count → run audit + hard eviction
    _HK_FACT_CEIL = 60  # hard ceiling: evict lowest-weight facts above this
    _HK_DECAY_HOURS = 6  # hours since last decay → re-apply decay
    _HK_DECAY_FAST_HOURS = 1  # hours since last decay when facts > _HK_FACT_AUDIT
    _HK_FULL_HOURS = 24  # hours since last full housekeeping → force full run

    async def periodic_housekeeping(self) -> None:
        """
        多阈值定期维护，每次任务结束后后台触发（非阻塞）。

        触发条件（任一满足即执行对应动作）：
        1. 事实数 > 30 → 增量去重 (dedup)
        2. 事实数 > 50 → 质量审计 (audit) + 低质量删除
        3. 事实数 > 60 → 硬上限驱逐（强删最低权重直到 ≤ 60）
        4. 距上次衰减 > 6h（或 >50条时 >1h）→ 执行 apply_decay
        5. 距上次完整运行 > 24h → 强制执行全部步骤
        """
        if self._housekeeping_running:
            logger.debug("[HK] Skipping housekeeping: already running")
            return
        self._housekeeping_running = True
        try:
            await self._do_periodic_housekeeping()
        finally:
            self._housekeeping_running = False

    async def _do_periodic_housekeeping(self) -> None:
        """Internal housekeeping implementation; called only when not already running."""
        import time

        now_ts = time.time()

        # Read metadata from backend
        meta = getattr(self.backend, "_hk_meta", None)
        if meta is None:
            meta = {"last_decay": 0.0, "last_full": 0.0}
            self.backend._hk_meta = meta  # type: ignore[attr-defined]

        facts = self.backend.get_all_facts()
        count = len(facts)
        hours_since_decay = (now_ts - meta["last_decay"]) / 3600
        hours_since_full = (now_ts - meta["last_full"]) / 3600

        force_full = hours_since_full >= self._HK_FULL_HOURS
        ran_anything = False

        # ── 1. Decay ──────────────────────────────────────────────
        decay_threshold = self._HK_DECAY_FAST_HOURS if count > self._HK_FACT_AUDIT else self._HK_DECAY_HOURS
        if force_full or hours_since_decay >= decay_threshold:
            try:
                self.apply_decay()
                meta["last_decay"] = now_ts
                ran_anything = True
                logger.info(f"[HK] Decay applied ({count} facts, {hours_since_decay:.1f}h since last)")
            except Exception as e:
                logger.warning(f"[HK] Decay failed: {e}")

        # ── 2. Dedup ──────────────────────────────────────────────
        if force_full or count > self._HK_FACT_DEDUPE:
            try:
                before = len(self.backend.get_all_facts())
                await self.deduplicate()
                after = len(self.backend.get_all_facts())
                if before != after:
                    logger.info(f"[HK] Dedup: {before} → {after} facts")
                ran_anything = True
            except Exception as e:
                logger.warning(f"[HK] Dedup failed: {e}")

        # ── 3. Audit ──────────────────────────────────────────────
        if force_full or count > self._HK_FACT_AUDIT:
            try:
                report = await self.audit()
                logger.info(
                    f"[HK] Audit: quality={report.get('quality_score', 0):.2f}, "
                    f"stale={len(report.get('stale', []))}, "
                    f"low_quality={len(report.get('low_quality', []))}"
                )
                ran_anything = True
            except Exception as e:
                logger.warning(f"[HK] Audit failed: {e}")

        # ── 4. Hard ceiling eviction ──────────────────────────────
        facts_after = self.backend.get_all_facts()
        if len(facts_after) > self._HK_FACT_CEIL:
            unlocked = [f for f in facts_after if not f.locked]
            unlocked.sort(key=lambda f: f.weight)
            evict_count = len(facts_after) - self._HK_FACT_CEIL
            evicted = 0
            for f in unlocked[:evict_count]:
                self.backend.remove_fact(f.fact_id)
                evicted += 1
            if evicted:
                await self._rebuild_index_async()
                logger.info(
                    f"[HK] Hard eviction: removed {evicted} lowest-weight facts "
                    f"({len(facts_after)} → {len(facts_after) - evicted})"
                )
            ran_anything = True

        if force_full:
            meta["last_full"] = now_ts

        if ran_anything:
            logger.debug(f"[HK] Housekeeping complete. Facts now: {len(self.backend.get_all_facts())}")

    # ─── v3 新 API：会话索引 ──────────────────────────────

    async def index_sessions(self):
        """索引所有会话 transcript。"""
        if not self._session_indexer:
            logger.warning("会话索引器未启用")
            return
        await self._session_indexer.index_all()

    async def search_sessions(self, query: str, top_k: int = 10) -> List[Dict]:
        """搜索会话 transcript。"""
        if not self._session_indexer:
            return []
        return await self._session_indexer.search_sessions(query, top_k)

    # ─── v3 新 API：异步初始化 ──────────────────────────────

    async def initialize_async(self):
        """异步初始化（构建嵌入索引、启动监听器）。"""
        await self._rebuild_index_async()
        if self._watcher:
            await self._watcher.start()
        if self._session_indexer:
            await self._session_indexer.index_all()

    # ─── 统计信息 ──────────────────────────────────────────

    def stats(self) -> Dict:
        facts = self.backend.get_all_facts()
        by_type = {}
        for f in facts:
            by_type.setdefault(f.fact_type.value, 0)
            by_type[f.fact_type.value] += 1
        locked_count = len([f for f in facts if f.locked])
        avg_weight = sum(f.weight for f in facts) / len(facts) if facts else 0
        result = {
            "total": len(facts),
            "by_type": by_type,
            "locked": locked_count,
            "avg_weight": round(avg_weight, 3),
            "backend": self._backend_type,
            "embedding": getattr(self._embedder, "provider_id", "unknown") if self._embedder else "none",
            "index_chunks": self._index.chunk_count() if self._index else 0,
        }
        return result
