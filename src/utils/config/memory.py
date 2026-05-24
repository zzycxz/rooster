"""Memory system configuration — backend, embeddings."""

from utils.config._base import (
    _env,
)


class MemoryConfig:
    MEMORY_BACKEND_TYPE: str = _env("MEMORY_BACKEND_TYPE", "json")

    # --- Embeddings ---
    EMBEDDING_PROVIDER: str = _env("EMBEDDING_PROVIDER", "auto")
    EMBEDDING_URL: str = _env("EMBEDDING_URL", "")
    EMBEDDING_KEY: str = _env("EMBEDDING_KEY", "")
    EMBEDDING_MODEL: str = _env("EMBEDDING_MODEL", "text-embedding-3-small")
    EMBEDDING_LOCAL_MODEL: str = _env("EMBEDDING_LOCAL_MODEL", "BAAI/bge-small-zh-v1.5")
