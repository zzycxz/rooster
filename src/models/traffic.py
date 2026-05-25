"""Traffic control primitives for LLM calls.

This module is intentionally small and dependency-light.  The LLMClient already
owns provider failover and cooldown decisions; this layer only limits how many
requests may be in flight globally and per provider.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Tuple

from utils.config import settings

logger = logging.getLogger(__name__)


class LLMTrafficController:
    """Global + per-provider concurrency gates for model traffic."""

    def __init__(self) -> None:
        self._global: Tuple[int, asyncio.Semaphore] | None = None
        self._provider: Dict[str, Tuple[int, asyncio.Semaphore]] = {}
        self._lock = asyncio.Lock()

    async def _global_sem(self) -> asyncio.Semaphore:
        limit = max(1, int(getattr(settings, "LLM_GLOBAL_MAX_CONCURRENT", 6)))
        async with self._lock:
            if self._global is None or self._global[0] != limit:
                self._global = (limit, asyncio.Semaphore(limit))
            return self._global[1]

    async def _provider_sem(self, provider: str) -> asyncio.Semaphore:
        limits = getattr(settings, "LLM_PROVIDER_MAX_CONCURRENT", {})
        default_limit = max(1, int(getattr(settings, "LLM_PROVIDER_MAX_CONCURRENT_DEFAULT", 2)))
        limit = max(1, int(limits.get(provider, default_limit)))
        async with self._lock:
            current = self._provider.get(provider)
            if current is None or current[0] != limit:
                self._provider[provider] = (limit, asyncio.Semaphore(limit))
            return self._provider[provider][1]

    @asynccontextmanager
    async def slot(self, provider: str, *, purpose: str = "llm") -> AsyncIterator[None]:
        """Acquire a global and provider-specific traffic slot."""
        global_sem = await self._global_sem()
        provider_sem = await self._provider_sem(provider)

        await global_sem.acquire()
        try:
            await provider_sem.acquire()
            try:
                logger.debug("[Traffic] acquired %s slot for provider=%s", purpose, provider)
                yield
            finally:
                provider_sem.release()
                logger.debug("[Traffic] released %s provider slot for provider=%s", purpose, provider)
        finally:
            global_sem.release()
            logger.debug("[Traffic] released %s global slot for provider=%s", purpose, provider)


llm_traffic_controller = LLMTrafficController()
