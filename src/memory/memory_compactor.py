"""Background jobs for memory compaction.

Memory compaction keeps expensive memory distillation off the executor hot path. The
executor can keep moving with a pruned context while this worker distills older
events into long-term memory in the background.
"""

import asyncio
import logging
from typing import Dict, List, Set

from utils.config import settings

logger = logging.getLogger(__name__)

_background_tasks: Set[asyncio.Task] = set()
_inflight: Set[str] = set()


def _history_fingerprint(history: List[Dict[str, str]]) -> str:
    import hashlib
    import json

    payload = json.dumps(history[-10:], ensure_ascii=False, sort_keys=True)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def schedule_memory_compaction(memory_manager, session_id: str, history: List[Dict[str, str]]) -> None:
    """Schedule non-blocking compaction of older session history."""
    if not getattr(settings, "MEMORY_COMPACTION_ENABLED", True):
        return
    min_items = max(1, int(getattr(settings, "MEMORY_COMPACTION_MIN_HISTORY_ITEMS", 8)))
    if memory_manager is None or len(history) < min_items:
        return

    max_items = max(min_items, int(getattr(settings, "MEMORY_COMPACTION_MAX_HISTORY_ITEMS", 24)))
    snapshot = [dict(m) for m in history[-max_items:]]
    key = f"{session_id}:{_history_fingerprint(snapshot)}"
    if key in _inflight:
        return
    _inflight.add(key)

    async def _run() -> None:
        try:
            await memory_manager.flush_before_compaction(session_id, snapshot)
            logger.debug(
                "[MemoryCompactor] compaction complete for session=%s (%d messages)", session_id, len(snapshot)
            )
        except Exception as exc:
            logger.warning("[MemoryCompactor] compaction failed for session=%s: %s", session_id, exc)
        finally:
            _inflight.discard(key)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
