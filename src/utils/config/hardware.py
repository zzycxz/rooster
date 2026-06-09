"""Hardware / vision configuration — model paths, action control, visual buffer."""

from utils.config._base import (
    _env,
    _env_int,
    _env_float,
)


class HardwareConfig:
    # --- Vision grounding ---
    VISION_DEDUPE_RADIUS: float = _env_float("VISION_DEDUPE_RADIUS", 30.0)

    # --- Action control ---
    ACTION_WAIT_MS: int = _env_int("ACTION_WAIT_MS", 500)
    ACTION_HASH_SIMILARITY: float = _env_float("ACTION_HASH_SIMILARITY", 0.98)

    # --- Visual audit ---
    MEMORY_VISUAL_BUFFER_SIZE: int = _env_int("MEMORY_VISUAL_BUFFER_SIZE", 5)

    # --- Vision scan mode ---
    # low    = foreground window only, ElitePainter filtering (fastest, default)
    # medium = all visible windows, A+K only, type suppression + occlusion
    # high   = all visible windows, all categories A/N/K/U (most thorough)
    VISION_SCAN_MODE: str = _env("VISION_SCAN_MODE", "low")
