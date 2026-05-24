# src/memory/compactor.py
"""上下文压缩器 — 在 token 超限时自动摘要"""  # Context compactor -- auto-summarize when tokens exceed limit

import asyncio
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


class ContextCompactor:
    """在上下文超限时，调用 LLM 生成历史摘要以压缩 token。"""  # When context exceeds limit, call LLM to generate history summary for token compression

    def __init__(self, llm_client, model: str):
        self.llm_client = llm_client
        self.model = model

    async def compact(
        self, history: List[Dict[str, str]], max_tokens: int, preserve_last_n: int = 4
    ) -> List[Dict[str, str]]:
        """
        压缩历史记录：
        1. 保留最近 preserve_last_n 轮对话
        2. 更早的对话调用 LLM 生成摘要
        3. 用单条 [历史摘要] system 消息替代

        Compress history:
        1. Keep the most recent preserve_last_n rounds
        2. Call LLM to summarize older conversation
        3. Replace with a single [History Summary] system message
        """
        estimated = self._estimate_tokens(history)
        if estimated <= max_tokens:
            return history

        # 保留最近 N 轮（每轮 = 1 user + 1 assistant）
        keep_count = preserve_last_n * 2
        if len(history) <= keep_count:
            return history

        to_compact = history[:-keep_count]
        to_keep = history[-keep_count:]

        summary = await self._summarize(to_compact)
        if summary:
            compacted = [{"role": "system", "content": f"[历史摘要]\n{summary}"}] + to_keep
            logger.info(f"上下文压缩: {len(history)} 条 → {len(compacted)} 条 (摘要 {len(summary)} 字符)")
            return compacted

        return history

    def _estimate_tokens(self, history: List[Dict[str, str]]) -> int:
        """粗略估算 token 数（1 中文 ≈ 1.5 token, 1 英文词 ≈ 1.3 token）"""  # Rough token estimate (1 Chinese char ~1.5 token, 1 English word ~1.3 token)
        total_chars = sum(len(m.get("content", "")) for m in history)
        return int(total_chars * 1.5)

    async def compact_with_flush(
        self,
        history: List[Dict[str, str]],
        max_tokens: int,
        session_id: str,
        memory_manager=None,
        preserve_last_n: int = 4,
    ) -> List[Dict[str, str]]:
        """
        压缩前自动刷写：先将待截断的历史蒸馏到持久记忆，再执行压缩。
        避免压缩丢掉有价值的信息。

        Auto-flush before compaction: distill truncated history to persistent memory, then compress.
        Prevents valuable information loss during compression.
        """
        estimated = self._estimate_tokens(history)
        if estimated <= max_tokens:
            return history

        keep_count = preserve_last_n * 2
        if len(history) <= keep_count:
            return history

        to_compact = history[:-keep_count]

        # 压缩前刷写：将待截断部分蒸馏到持久记忆
        if memory_manager is not None:
            try:
                await memory_manager.flush_before_compaction(session_id, to_compact)
            except Exception as e:
                logger.warning(f"压缩前刷写失败（继续压缩）: {e}")

        # 正常压缩
        return await self.compact(history, max_tokens, preserve_last_n)

    async def _summarize(self, messages: List[Dict[str, str]]) -> str:
        """调用 LLM 生成对话摘要"""
        context = ""
        for m in messages:
            role = m.get("role", "unknown")
            content = m.get("content", "")[:500]
            context += f"{role}: {content}\n"

        prompt = (
            "请用 3-5 句话精简总结以下对话的关键信息，保留重要的事实、决策和结论。\n"
            "要求：只输出摘要，不要废话。\n\n"
            f"对话记录:\n{context}"
        )

        try:
            # 30s timeout — summarization hangs the entire executor loop if LLM stalls
            response = await asyncio.wait_for(
                self.llm_client.chat_non_stream(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.model,
                ),
                timeout=30.0,
            )
            return response.content.strip()
        except asyncio.TimeoutError:
            logger.warning("上下文摘要超时 (30s)，跳过压缩直接保留原始历史")
            return ""
        except Exception as e:
            logger.warning(f"上下文摘要生成失败: {e}")
            return ""
