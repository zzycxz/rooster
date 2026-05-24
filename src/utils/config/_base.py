"""Configuration type helpers — shared parsing utilities for all config modules."""

import os
from typing import List


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _env_float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() == "true"


def _env_list(key: str, default: str = "") -> List[str]:
    raw = os.getenv(key, default)
    return [m.strip() for m in raw.split(",") if m.strip()]


def _env_path(key: str, default: str = "") -> str:
    return os.getenv(key, default)
