import asyncio
import time
import logging
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pluggable event sink registry — Dashboard (or any consumer) registers here
# ---------------------------------------------------------------------------
_event_sinks: List[Callable] = []


def register_event_sink(fn: Callable):
    """Register an async callable(event_type: str, payload: dict) to receive agent events."""
    if fn not in _event_sinks:
        _event_sinks.append(fn)


def unregister_event_sink(fn: Callable):
    """Remove a previously registered event sink."""
    try:
        _event_sinks.remove(fn)
    except ValueError:
        pass


class AgentEvent(BaseModel):
    run_id: str
    session_id: str
    stream: str  # "lifecycle", "assistant", "error", "tool"
    seq: int
    ts: float = Field(default_factory=time.time)
    data: Dict[str, Any]
    subtask_id: Optional[str] = None  # Top-level subtask association for Dashboard grouping


class AgentEventHandler:
    def __init__(self, broadcast_callback):
        # broadcast_callback 是用于将消息发往 WebSocket 客户端的回调函数
        # broadcast_callback is the callback for sending messages to WebSocket clients
        self.broadcast = broadcast_callback
        self._run_seq: Dict[str, int] = {}
        self._background_tasks: set = set()

    async def emit(
        self, run_id: str, session_id: str, stream: str, data: Dict[str, Any], subtask_id: Optional[str] = None
    ):
        """发射一个 Agent 事件"""
        self._run_seq[run_id] = self._run_seq.get(run_id, 0) + 1
        seq = self._run_seq[run_id]

        event = AgentEvent(
            run_id=run_id,
            session_id=session_id,
            stream=stream,
            seq=seq,
            data=data,
            subtask_id=subtask_id or data.get("subtask_id"),
        )

        logger.debug(f"Emitting {stream} event (seq={seq}) for Run {run_id}")

        # Broadcast to the originating WebSocket client (fire-and-forget)
        try:
            await self.broadcast(event.model_dump())
        except Exception as _broadcast_err:
            logger.debug(f"broadcast failed (client likely disconnected): {_broadcast_err}")

        # Fan-out to all registered event sinks (e.g. Dashboard)
        for sink in _event_sinks:
            try:
                task = asyncio.create_task(sink("agent_event", event.model_dump()))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            except Exception as _sink_err:
                logger.debug(f"event sink failed: {_sink_err}")

    # ========== 扩展协议支持（供 Executor 调用）==========
    # ========== Extended protocol support (called by Executor) ==========
    async def emit_lifecycle(self, session_key: str, client_run_id: str, status: str):
        await self.emit(run_id=client_run_id, session_id=session_key, stream="lifecycle", data={"status": status})

    async def emit_assistant_delta(self, session_key: str, client_run_id: str, text: str):
        await self.emit(
            run_id=client_run_id, session_id=session_key, stream="assistant", data={"text": text, "status": "running"}
        )

    async def emit_assistant_event(self, session_key: str, client_run_id: str, content: str, status: str):
        await self.emit(
            run_id=client_run_id, session_id=session_key, stream="assistant", data={"text": content, "status": status}
        )

    async def emit_error(self, session_key: str, client_run_id: str, message: str):
        await self.emit(
            run_id=client_run_id, session_id=session_key, stream="error", data={"message": message, "status": "error"}
        )

    async def emit_tool_call(self, session_key: str, client_run_id: str, tool_name: str, args: Dict[str, Any]):
        await self.emit(
            run_id=client_run_id,
            session_id=session_key,
            stream="tool",
            data={"tool": tool_name, "args": args, "type": "call"},
        )

    async def emit_tool_response(self, session_key: str, client_run_id: str, tool_name: str, response: Any):
        await self.emit(
            run_id=client_run_id,
            session_id=session_key,
            stream="tool",
            data={"tool": tool_name, "response": response, "type": "response"},
        )

    async def emit_subtask_start(self, session_key: str, client_run_id: str, subtask_id: str, total: int, current: int):
        await self.emit(
            run_id=client_run_id,
            session_id=session_key,
            stream="lifecycle",
            data={
                "status": "subtask_start",
                "subtask_id": subtask_id,
                "total": total,
                "current": current,
            },
            subtask_id=subtask_id,
        )

    async def emit_subtask_complete(
        self, session_key: str, client_run_id: str, subtask_id: str, result_status: str, provider_used: str = ""
    ):
        data = {
            "status": "subtask_complete",
            "subtask_id": subtask_id,
            "result": result_status,
        }
        if provider_used:
            data["provider_used"] = provider_used
        await self.emit(
            run_id=client_run_id, session_id=session_key, stream="lifecycle", data=data, subtask_id=subtask_id
        )

    async def emit_audit_verdict(
        self,
        session_key: str,
        client_run_id: str,
        subtask_id: str,
        verdict: str,
        result_verdict: str,
        reason: str,
        recommendation: str = "",
        findings: list = None,
        command: str = "",
    ):
        await self.emit(
            run_id=client_run_id,
            session_id=session_key,
            stream="lifecycle",
            data={
                "status": "audit_verdict",
                "subtask_id": subtask_id,
                "verdict": verdict,
                "result_verdict": result_verdict,
                "reason": reason,
                "recommendation": recommendation,
                "findings": findings or [],
                "command": command,
            },
            subtask_id=subtask_id,
        )
