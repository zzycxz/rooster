"""
Python logging → Dashboard WebSocket bridge.

Install once after the asyncio event loop is running:

    from dashboard.src.log_bridge import install_log_bridge
    install_log_bridge(loop=asyncio.get_running_loop())

After that every log record from every logger is forwarded to all
connected Dashboard clients as {"type": "log", "ts": ..., "data": {...}}.
"""

import asyncio
import logging
import traceback
from typing import Optional


class DashboardLogHandler(logging.Handler):
    """Forwards Python log records to all connected Dashboard WebSocket clients."""

    def __init__(self, level: int = logging.DEBUG) -> None:
        super().__init__(level)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        try:
            from .dashboard_ws import broadcast_event
            from gateway.auth import sanitize_log_message

            raw_msg = self.format(record)
            safe_msg = sanitize_log_message(raw_msg)

            data: dict = {
                "level": record.levelname,
                "logger": record.name,
                "message": safe_msg,
                "ts": record.created,
            }
            if record.exc_info and record.exc_info[0] is not None:
                data["traceback"] = "".join(traceback.format_exception(*record.exc_info))

            loop = self._loop
            if loop is None:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return

            if loop.is_running():
                asyncio.run_coroutine_threadsafe(broadcast_event("log", data), loop)
        except Exception:
            pass  # Must never raise inside a logging handler


_handler: Optional[DashboardLogHandler] = None


def install_log_bridge(
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> DashboardLogHandler:
    """
    Attach DashboardLogHandler to the root logger (idempotent).
    Call once after the asyncio event loop has started.
    """
    global _handler
    if _handler is None:
        _handler = DashboardLogHandler(level=logging.DEBUG)
        _handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(name)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        if loop:
            _handler.set_loop(loop)
        logging.getLogger().addHandler(_handler)
    elif loop and _handler._loop is None:
        _handler.set_loop(loop)
    return _handler
