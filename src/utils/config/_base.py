"""Configuration type helpers — shared parsing utilities for all config modules."""

import os
from typing import List


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int = 0) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid integer for environment variable {key}: {raw!r}") from exc


def _env_float(key: str, default: float = 0.0) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid float for environment variable {key}: {raw!r}") from exc


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    truthy = {"true", "1", "yes", "on"}
    falsy = {"false", "0", "no", "off"}
    if normalized in truthy:
        return True
    if normalized in falsy:
        return False
    raise ValueError(
        f"Invalid boolean for environment variable {key}: {raw!r}. "
        "Expected one of true/false/1/0/yes/no/on/off."
    )


def _env_list(key: str, default: str = "") -> List[str]:
    raw = os.getenv(key, default)
    return [m.strip() for m in raw.split(",") if m.strip()]


def _env_path(key: str, default: str = "") -> str:
    return os.getenv(key, default)
