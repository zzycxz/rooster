"""Logging context helpers for request/mission correlation."""

from contextvars import ContextVar, Token
import logging


_mission_id_var: ContextVar[str] = ContextVar("mission_id", default="-")


class MissionContextFilter(logging.Filter):
    """Inject the current mission_id into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.mission_id = _mission_id_var.get()
        return True


def set_mission_id(mission_id: str) -> Token:
    return _mission_id_var.set(mission_id or "-")


def reset_mission_id(token: Token) -> None:
    _mission_id_var.reset(token)
