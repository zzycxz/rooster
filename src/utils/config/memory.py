"""Memory system configuration — backend, embeddings."""

from utils.config._base import (
    _env,
    _env_bool,
    _env_int,
)


class MemoryConfig:
    MEMORY_BACKEND_TYPE: str = _env("MEMORY_BACKEND_TYPE", "json")
    MEMORY_COMPACTION_ENABLED: bool = _env_bool("MEMORY_COMPACTION_ENABLED", True)
    MEMORY_COMPACTION_MIN_HISTORY_ITEMS: int = _env_int("MEMORY_COMPACTION_MIN_HISTORY_ITEMS", 8)
    MEMORY_COMPACTION_MAX_HISTORY_ITEMS: int = _env_int("MEMORY_COMPACTION_MAX_HISTORY_ITEMS", 24)

    # --- Embeddings ---
    EMBEDDING_PROVIDER: str = _env("EMBEDDING_PROVIDER", "auto")
    EMBEDDING_URL: str = _env("EMBEDDING_URL", "")
    EMBEDDING_KEY: str = _env("EMBEDDING_KEY", "")
    EMBEDDING_MODEL: str = _env("EMBEDDING_MODEL", "text-embedding-3-small")
    EMBEDDING_LOCAL_MODEL: str = _env("EMBEDDING_LOCAL_MODEL", "BAAI/bge-small-zh-v1.5")

    # --- Distillation ---
    DISTILLATION_ENABLED: bool = _env_bool("DISTILLATION_ENABLED", True)
    DISTILLATION_INTERVAL: int = _env_int("DISTILLATION_INTERVAL", 600)
    DISTILLATION_QUIET_MINUTES: int = _env_int("DISTILLATION_QUIET_MINUTES", 5)
    DISTILLATION_MODEL: str = _env("DISTILLATION_MODEL", "")
