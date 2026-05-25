"""
Dashboard WebSocket connection manager.

Maintains a pool of connected browser clients (/ws/dashboard) and provides
a broadcast() helper used by log_bridge and event_handler.
"""

import asyncio
import json
import logging
import time
from typing import Set

from fastapi import WebSocket
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)

_MAX_QUEUE_SIZE = 500


class DashboardManager:
    """Thread-safe broadcast pool for all connected Dashboard clients."""

    _instance: "DashboardManager | None" = None

    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()
        self._queue: asyncio.Queue | None = None
        self._queue_loop: asyncio.AbstractEventLoop | None = None
        self._broadcast_task: asyncio.Task | None = None

    def _get_queue(self) -> asyncio.Queue:
        """Return the queue, creating it on the current running loop if needed."""
        loop = asyncio.get_running_loop()
        if self._queue is None or self._queue_loop is not loop:
            self._queue = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
            self._queue_loop = loop
        return self._queue

    @classmethod
    def get_instance(cls) -> "DashboardManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def connect(self, websocket: WebSocket) -> None:
        self._clients.add(websocket)
        self._ensure_loop()
        logger.debug(f"Dashboard client connected ({len(self._clients)} total)")

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)
        logger.debug(f"Dashboard client disconnected ({len(self._clients)} remaining)")

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def _ensure_loop(self) -> None:
        """Start the background broadcast consumer if not running."""
        if self._broadcast_task is None or self._broadcast_task.done():
            self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    async def _broadcast_loop(self) -> None:
        """Consume events from the queue and broadcast to all clients."""
        queue = self._get_queue()
        while True:
            payload = await queue.get()
            if not self._clients:
                continue
            text = json.dumps(payload, ensure_ascii=False, default=str)
            stale: Set[WebSocket] = set()
            active_clients = list(self._clients)

            async def _send_to_client(ws: WebSocket):
                try:
                    if ws.application_state == WebSocketState.CONNECTED:
                        await ws.send_text(text)
                    else:
                        stale.add(ws)
                except Exception as exc:
                    logger.debug(f"Dashboard broadcast error: {exc}")
                    stale.add(ws)

            if active_clients:
                await asyncio.gather(*[_send_to_client(ws) for ws in active_clients])

            for ws in stale:
                self._clients.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        """Queue a JSON payload for broadcast. Drops if queue is full."""
        self._ensure_loop()
        try:
            self._get_queue().put_nowait(payload)
        except asyncio.QueueFull:
            logger.debug("Dashboard broadcast queue full, dropping event")


# Module-level singleton
dashboard_manager = DashboardManager.get_instance()


async def broadcast_event(event_type: str, data: dict) -> None:
    """Convenience wrapper: sends a typed event envelope to all dashboard clients."""
    await dashboard_manager.broadcast(
        {
            "type": event_type,
            "ts": time.time(),
            "data": data,
        }
    )
