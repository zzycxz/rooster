"""
Stream event protocol and buffer for real-time WebSocket push.

StreamEvent defines the wire format. StreamBuffer collects deltas
and flushes them to connected Dashboard clients.
"""

import asyncio
import time
import logging
from enum import Enum
from typing import Any, Dict, List
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class StreamEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    THINK_DELTA = "think_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SUBTASK_START = "subtask_start"
    SUBTASK_COMPLETE = "subtask_complete"
    DONE = "done"
    ERROR = "error"


class StreamEvent(BaseModel):
    """Wire-format event for streaming responses."""

    type: StreamEventType
    run_id: str
    session_id: str
    seq: int
    ts: float = Field(default_factory=time.time)
    data: Dict[str, Any] = {}


class StreamBuffer:
    """
    Collects StreamEvents and pushes them to Dashboard WebSocket clients.

    Usage:
        buf = StreamBuffer(run_id="abc", session_id="s1")
        await buf.push_text_delta("Hello ")
        await buf.push_text_delta("world")
        await buf.flush()  # push all buffered events
    """

    def __init__(self, run_id: str, session_id: str):
        self.run_id = run_id
        self.session_id = session_id
        self._seq = 0
        self._buffer: List[StreamEvent] = []
        self._text_acc = ""  # accumulate text deltas
        self._flush_lock = asyncio.Lock()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def push_text_delta(self, text: str):
        event = StreamEvent(
            type=StreamEventType.TEXT_DELTA,
            run_id=self.run_id,
            session_id=self.session_id,
            seq=self._next_seq(),
            data={"text": text},
        )
        self._text_acc += text
        self._buffer.append(event)

    async def push_think_delta(self, text: str):
        event = StreamEvent(
            type=StreamEventType.THINK_DELTA,
            run_id=self.run_id,
            session_id=self.session_id,
            seq=self._next_seq(),
            data={"text": text},
        )
        self._buffer.append(event)

    async def push_tool_call(self, tool_name: str, args: dict):
        event = StreamEvent(
            type=StreamEventType.TOOL_CALL,
            run_id=self.run_id,
            session_id=self.session_id,
            seq=self._next_seq(),
            data={"tool": tool_name, "args": args},
        )
        self._buffer.append(event)
        await self._flush()

    async def push_tool_result(self, tool_name: str, result: str):
        event = StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            run_id=self.run_id,
            session_id=self.session_id,
            seq=self._next_seq(),
            data={"tool": tool_name, "result": result[:2000]},
        )
        self._buffer.append(event)
        await self._flush()

    async def push_subtask_start(self, subtask_id: str, total: int, current: int):
        event = StreamEvent(
            type=StreamEventType.SUBTASK_START,
            run_id=self.run_id,
            session_id=self.session_id,
            seq=self._next_seq(),
            data={"subtask_id": subtask_id, "total": total, "current": current},
        )
        self._buffer.append(event)
        await self._flush()

    async def push_subtask_complete(self, subtask_id: str, status: str):
        event = StreamEvent(
            type=StreamEventType.SUBTASK_COMPLETE,
            run_id=self.run_id,
            session_id=self.session_id,
            seq=self._next_seq(),
            data={"subtask_id": subtask_id, "status": status},
        )
        self._buffer.append(event)
        await self._flush()

    async def push_done(self, final_text: str = ""):
        event = StreamEvent(
            type=StreamEventType.DONE,
            run_id=self.run_id,
            session_id=self.session_id,
            seq=self._next_seq(),
            data={"text": final_text},
        )
        self._buffer.append(event)
        await self._flush()

    async def push_error(self, message: str):
        event = StreamEvent(
            type=StreamEventType.ERROR,
            run_id=self.run_id,
            session_id=self.session_id,
            seq=self._next_seq(),
            data={"message": message},
        )
        self._buffer.append(event)
        await self._flush()

    async def _flush(self):
        """Push all buffered events to Dashboard WebSocket and clear buffer."""
        async with self._flush_lock:
            if not self._buffer:
                return
            try:
                from dashboard.src.dashboard_ws import broadcast_event

                for event in self._buffer:
                    await broadcast_event("stream", event.model_dump())
                self._buffer.clear()
            except Exception as e:
                logger.debug(f"StreamBuffer flush failed: {e}")

    @property
    def accumulated_text(self) -> str:
        return self._text_acc
