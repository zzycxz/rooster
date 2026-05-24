"""Hardware / vision configuration — model paths, rendering, action control, input, scroll."""

from utils.config._base import (
    _env,
    _env_int,
    _env_float,
    _env_bool,
)


class HardwareConfig:
    # --- Vision grounding ---
    VISION_MODEL_PATH: str = _env("VISION_MODEL_PATH", "resources/models/grounding/icon_detect/model.pt")
    VISION_CONFIDENCE: float = _env_float("VISION_CONFIDENCE", 0.45)
    VISION_DEDUPE_RADIUS: float = _env_float("VISION_DEDUPE_RADIUS", 30.0)

    # --- Rendering ---
    VISION_FONT_SIZE_BASE: int = _env_int("VISION_FONT_SIZE_BASE", 14)
    VISION_FONT_BASE_HEIGHT: int = _env_int("VISION_FONT_BASE_HEIGHT", 1080)
    VISION_FONT_SIZE_MIN: int = _env_int("VISION_FONT_SIZE_MIN", 14)
    VISION_LABEL_OFFSET_X: int = _env_int("VISION_LABEL_OFFSET_X", 12)
    VISION_LABEL_OFFSET_Y: int = _env_int("VISION_LABEL_OFFSET_Y", -12)

    # --- Action control ---
    ACTION_WAIT_MS: int = _env_int("ACTION_WAIT_MS", 500)
    ACTION_RETRY_MAX: int = _env_int("ACTION_RETRY_MAX", 2)
    ACTION_DRIFT_PX: int = _env_int("ACTION_DRIFT_PX", 2)
    ACTION_HASH_SIMILARITY: float = _env_float("ACTION_HASH_SIMILARITY", 0.98)

    # --- Input ---
    INPUT_USE_CLIPBOARD: bool = _env_bool("INPUT_USE_CLIPBOARD", True)
    INPUT_PRE_CLEAR: bool = _env_bool("INPUT_PRE_CLEAR", True)

    # --- Scroll ---
    SCROLL_DEFAULT_AMOUNT: int = _env_int("SCROLL_DEFAULT_AMOUNT", 600)
    SCROLL_OVERLAP_PX: int = _env_int("SCROLL_OVERLAP_PX", 150)
    SCROLL_WAIT_MS: int = _env_int("SCROLL_WAIT_MS", 300)

    # --- Visual audit ---
    AUDIT_VISUAL_SNAPSHOT: bool = _env_bool("AUDIT_VISUAL_SNAPSHOT", True)
    AUDIT_SNAPSHOT_DIR: str = _env("AUDIT_SNAPSHOT_DIR", ".rooster/audit/vision/")
    MEMORY_VISUAL_BUFFER_SIZE: int = _env_int("MEMORY_VISUAL_BUFFER_SIZE", 5)
