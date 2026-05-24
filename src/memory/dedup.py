"""智能去重与记忆质量审计模块。"""

import hashlib
import json
import logging
import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple

from .models import MemoryFact

logger = logging.getLogger(__name__)


class MemoryDeduplicator:
    """
    LLM 驱动的智能去重器：
    1. 基于语义相似度预筛选候选对
    2. 调用 LLM 判断是否重复/矛盾
    3. 合并为更高质量的单条事实
    """

    _STOPWORDS = frozenset(
        {
            "的",
            "了",
            "是",
            "在",
            "和",
            "有",
            "为",
            "与",
            "被",
            "把",
            "对",
            "从",
            "到",
            "也",
            "就",
            "都",
            "而",
            "及",
            "或",
            "a",
            "the",
            "is",
            "to",
        }
    )

    def __init__(self, llm_client=None, model: str = ""):
        self.llm_client = llm_client
        self.model = model
        self._token_cache: Dict[str, set] = {}

    def _tokenize_for_overlap(self, text: str) -> set:
        """中英文混合分词，结果按 content hash 缓存。"""
        cache_key = hashlib.md5(text.encode()).hexdigest()
        if cache_key in self._token_cache:
            return self._token_cache[cache_key]
        try:
            import jieba

            tokens = set(jieba.cut(text.lower()))
        except ImportError:
            tokens = set()
            buf = []
            for ch in text.lower():
                if "一" <= ch <= "鿿":
                    tokens.add(ch)
                    if buf:
                        tokens.add("".join(buf))
                        buf = []
                elif ch.isalnum():
                    buf.append(ch)
                else:
                    if buf:
                        tokens.add("".join(buf))
                        buf = []
            if buf:
                tokens.add("".join(buf))
        self._token_cache[cache_key] = tokens
        if len(self._token_cache) > 5000:
            oldest = list(self._token_cache.keys())[:1000]
            for k in oldest:
                del self._token_cache[k]
        return tokens

    def _content_overlap(self, a: str, b: str) -> float:
        """基于分词的重叠率（比字符级更适合中文）"""
        if not a or not b:
            return 0.0
        tokens_a = self._tokenize_for_overlap(a) - self._STOPWORDS
        tokens_b = self._tokenize_for_overlap(b) - self._STOPWORDS
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union) if union else 0.0

    def find_duplicate_candidates(
        self, facts: List[MemoryFact], threshold: float = 0.4
    ) -> List[Tuple[MemoryFact, MemoryFact, float]]:
        """找出可能重复的事实对（基于分词重叠率）"""
        candidates = []
        for i in range(len(facts)):
            for j in range(i + 1, len(facts)):
                overlap = self._content_overlap(facts[i].content, facts[j].content)
                if overlap >= threshold:
                    candidates.append((facts[i], facts[j], overlap))
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates

    def find_duplicate_candidates_incremental(
        self,
        new_facts: List[MemoryFact],
        existing_facts: List[MemoryFact],
        threshold: float = 0.4,
    ) -> List[Tuple[MemoryFact, MemoryFact, float]]:
        """增量去重：只对新增事实 vs 已有事实比较，O(n*m) 而非 O(n²)。"""
        candidates = []
        for new_f in new_facts:
            for old_f in existing_facts:
                overlap = self._content_overlap(new_f.content, old_f.content)
                if overlap >= threshold:
                    candidates.append((new_f, old_f, overlap))
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates

    async def deduplicate(self, facts: List[MemoryFact]) -> List[MemoryFact]:
        """
        完整去重流程：
        1. 快速预筛选候选对
        2. LLM 判断合并
        3. 返回去重后的事实列表
        """
        candidates = self.find_duplicate_candidates(facts)
        if not candidates:
            return facts

        to_remove: set[str] = set()
        merged_facts: List[MemoryFact] = []

        for fact_a, fact_b, overlap in candidates:
            if fact_a.fact_id in to_remove or fact_b.fact_id in to_remove:
                continue

            merged = await self._llm_merge(fact_a, fact_b)
            if merged:
                to_remove.add(fact_a.fact_id)
                to_remove.add(fact_b.fact_id)
                merged_facts.append(merged)

        result = [f for f in facts if f.fact_id not in to_remove]
        result.extend(merged_facts)
        return result

    def _make_merged_id(self, fact_a: MemoryFact, fact_b: MemoryFact) -> str:
        """生成定长哈希 ID，避免多轮合并后 ID 无限增长"""
        key = f"{min(fact_a.fact_id, fact_b.fact_id)}:{max(fact_a.fact_id, fact_b.fact_id)}"
        h = hashlib.md5(key.encode()).hexdigest()[:12]
        return f"merged_{h}"

    async def _llm_merge(self, fact_a: MemoryFact, fact_b: MemoryFact) -> Optional[MemoryFact]:
        """调用 LLM 合并两条重复事实"""
        if not self.llm_client:
            return None

        prompt = (
            "以下两条记忆事实可能重复或矛盾。请判断：\n"
            "1. 如果完全重复，返回合并后的一条精简事实。\n"
            "2. 如果有矛盾，返回更准确的那条。\n"
            "3. 如果其实不重复，返回 'NO_MERGE'。\n\n"
            f"事实A [{fact_a.fact_type.value}]: {fact_a.content}\n"
            f"事实B [{fact_b.fact_type.value}]: {fact_b.content}\n\n"
            "请只返回合并后的内容，或 'NO_MERGE'。"
        )

        try:
            result = await self._llm_call(prompt)

            if "NO_MERGE" in result or len(result) < 3:
                return None

            return MemoryFact(
                fact_id=self._make_merged_id(fact_a, fact_b),
                fact_type=fact_a.fact_type if fact_a.confidence >= fact_b.confidence else fact_b.fact_type,
                content=result,
                source_agent=fact_a.source_agent,
                mission_id=fact_a.mission_id or fact_b.mission_id,
                confidence=max(fact_a.confidence, fact_b.confidence),
                created_at=max(fact_a.created_at, fact_b.created_at)
                if isinstance(fact_a.created_at, datetime)
                else fact_a.created_at,
                tags=list(set(fact_a.tags + fact_b.tags)),
                access_count=fact_a.access_count + fact_b.access_count,
                weight=max(fact_a.weight, fact_b.weight),
                locked=fact_a.locked or fact_b.locked,
            )
        except Exception as e:
            logger.warning(f"LLM 合并失败: {e}")
            return None

    async def _llm_call(self, prompt: str) -> str:
        """统一 LLM 调用（支持 chat_non_stream 和 chat_stream）"""
        if hasattr(self.llm_client, "chat_non_stream"):
            resp = await self.llm_client.chat_non_stream(
                messages=[{"role": "user", "content": prompt}],
                model=self.model,
            )
            return resp.content.strip()
        elif hasattr(self.llm_client, "chat_stream"):
            result = ""
            async for delta in self.llm_client.chat_stream(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
            ):
                if delta.content:
                    result += delta.content
            return result.strip()
        return ""


class MemoryAuditor:
    """
    记忆质量审计器：
    1. 检测过时信息（与新事实矛盾的旧事实）
    2. 标记低质量条目（过短、无意义）
    3. 建议锁定高价值条目
    4. 生成质量报告
    """

    def __init__(self, llm_client=None, model: str = ""):
        self.llm_client = llm_client
        self.model = model

    async def audit(self, facts: List[MemoryFact]) -> Dict:
        """
        执行完整审计，返回审计报告。
        """
        report = {
            "stale": [],
            "low_quality": [],
            "lock_suggested": [],
            "quality_score": 0.0,
            "summary": "",
            "total": len(facts),
            "by_type": {},
        }

        if not facts:
            report["summary"] = "记忆库为空"
            return report

        for f in facts:
            report["by_type"].setdefault(f.fact_type.value, 0)
            report["by_type"][f.fact_type.value] += 1

        # 规则审计（无需 LLM）
        low_quality_set = set()
        for f in facts:
            if len(f.content.strip()) < 5:
                low_quality_set.add(f.fact_id)
            if f.weight < 0.2 and not f.locked:
                low_quality_set.add(f.fact_id)
            if f.access_count >= 3 and f.confidence >= 0.8 and not f.locked:
                report["lock_suggested"].append(f.fact_id)
        report["low_quality"] = list(low_quality_set)

        # LLM 深度审计（可选）
        if self.llm_client and len(facts) > 5:
            llm_report = await self._llm_audit(facts)
            report["stale"].extend(llm_report.get("stale", []))
            report["summary"] = llm_report.get("summary", "")
        else:
            report["summary"] = f"规则审计完成：{len(facts)} 条事实"

        high_quality = len([f for f in facts if f.weight >= 0.6 and f.confidence >= 0.7])
        report["quality_score"] = high_quality / len(facts) if facts else 0.0

        return report

    async def _llm_audit(self, facts: List[MemoryFact]) -> Dict:
        """LLM 深度审计：检测矛盾和过时信息（支持两种 LLM 客户端）"""
        if not self.llm_client:
            return {}

        facts_text = "\n".join([f"[{i}] [{f.fact_type.value}] {f.content}" for i, f in enumerate(facts[:30])])

        prompt = (
            "你是记忆质量审计员。请检查以下记忆事实列表：\n"
            "1. 找出已过时或被新事实覆盖的旧事实（返回其序号）\n"
            "2. 找出互相矛盾的事实对\n"
            "3. 给出整体质量评估（一句话）\n\n"
            f"事实列表：\n{facts_text}\n\n"
            '返回 JSON 格式：{"stale": [序号列表], "contradictions": [[序号A, 序号B]], "summary": "评估"}'
        )

        try:
            if hasattr(self.llm_client, "chat_non_stream"):
                resp = await self.llm_client.chat_non_stream(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.model,
                )
                result = resp.content.strip()
            elif hasattr(self.llm_client, "chat_stream"):
                result = ""
                async for delta in self.llm_client.chat_stream(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                ):
                    if delta.content:
                        result += delta.content
                result = result.strip()
            else:
                return {}

            json_match = re.search(r"\{.*\}", result, re.DOTALL)
            if not json_match:
                return {}

            data = json.loads(json_match.group())
            stale_ids = []
            for idx in data.get("stale", []):
                if isinstance(idx, int) and 0 <= idx < len(facts):
                    stale_ids.append(facts[idx].fact_id)

            return {
                "stale": stale_ids,
                "summary": data.get("summary", ""),
            }
        except Exception as e:
            logger.warning(f"LLM 审计失败: {e}")
            return {}
