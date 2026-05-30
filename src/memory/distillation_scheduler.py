"""Session distillation scheduler.

Periodically scans finished sessions and distills their conversation history
into long-term memory (LTM) using a cloud LLM.  Two trigger modes:

  - **Scheduled**: every ``DISTILLATION_INTERVAL`` seconds the scheduler wakes
    up, finds sessions that have been quiet for longer than
    ``DISTILLATION_QUIET_MINUTES``, and distills them.
  - **Manual**: ``distill_now(session_id)`` or ``distill_all()`` can be called
    at any time from the CLI or API.

Distillation reuses ``MemoryManager.distill_history()`` so the extraction
logic, dedup, and backend writes are all shared with the existing pipeline.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Set

from utils.config import settings

logger = logging.getLogger(__name__)


class DistillationScheduler:
    def __init__(self, memory_manager, session_store, llm_client, model: str = ""):
        self._memory_manager = memory_manager
        self._session_store = session_store
        self._llm_client = llm_client
        self._model = model or getattr(settings, "CLOUD_MODEL", "")

        self._interval = max(30, getattr(settings, "DISTILLATION_INTERVAL", 600))
        self._quiet_delta = timedelta(
            minutes=max(1, getattr(settings, "DISTILLATION_QUIET_MINUTES", 5))
        )

        self._inflight: Set[str] = set()
        self._wake = asyncio.Event()
        self._running = False

    # ── public API ──────────────────────────────────────────────

    async def start(self):
        if not getattr(settings, "DISTILLATION_ENABLED", True):
            logger.info("[DistillScheduler] 蒸馏调度器已禁用。")
            return

        logger.info(
            f"[DistillScheduler] 蒸馏调度器已启动 (间隔 {self._interval}s, 安静阈值 {self._quiet_delta})"
        )
        self._running = True
        await self._run_loop()

    async def stop(self):
        self._running = False
        self._wake.set()

    async def distill_now(self, session_id: str) -> bool:
        """手动蒸馏单个 session。返回是否成功。"""
        session = self._session_store.get_session(session_id)
        if not session:
            logger.warning(f"[DistillScheduler] session 不存在: {session_id}")
            return False
        await self._distill_session(session)
        return True

    async def distill_all(self) -> int:
        """手动蒸馏所有待处理 session。返回蒸馏数量。"""
        return await self._scan_and_distill(force=True)

    # ── internals ───────────────────────────────────────────────

    async def _run_loop(self):
        while self._running:
            try:
                self._wake.clear()
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

            if not self._running:
                break

            try:
                await self._scan_and_distill()
            except Exception as exc:
                logger.error(f"[DistillScheduler] 定时扫描失败: {exc}")

    async def _scan_and_distill(self, force: bool = False) -> int:
        now = datetime.now()
        count = 0

        for sid, session in list(self._session_store.list_sessions().items()):
            if sid in self._inflight:
                continue

            if force:
                needs = True
            else:
                if len(session.history) < 3:
                    continue
                quiet = now - session.updated_at.replace(tzinfo=None)
                if quiet < self._quiet_delta:
                    continue
                distilled = session.distilled_at
                needs = distilled is None or distilled.replace(tzinfo=None) < session.updated_at.replace(
                    tzinfo=None
                )

            if needs:
                await self._distill_session(session)
                count += 1

        return count

    async def _distill_session(self, session) -> None:
        sid = session.session_id
        if sid in self._inflight:
            return
        self._inflight.add(sid)

        try:
            history = [{"role": m.role, "content": m.content} for m in session.history]
            if len(history) < 3:
                return

            logger.info(f"[DistillScheduler] 开始蒸馏 session={sid} ({len(history)} 条消息)")
            await self._memory_manager.distill_history(
                llm_client=self._llm_client,
                model=self._model,
                history=history,
            )

            session.distilled_at = datetime.now()
            self._session_store.save_session(sid)
            logger.info(f"[DistillScheduler] 蒸馏完成 session={sid}")
        except Exception as exc:
            logger.error(f"[DistillScheduler] 蒸馏失败 session={sid}: {exc}")
        finally:
            self._inflight.discard(sid)
