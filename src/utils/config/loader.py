"""
src/utils/config/loader.py

ConfigLoader — legacy YAML layer (removed).
All config now lives in .env / .env.local.
This module is kept for import compatibility.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ConfigLoader:
    """No-op loader. YAML configs have been removed; .env is the single source of truth."""

    def __init__(self, config_dir: Optional[str] = None):
        pass

    def load_all(self) -> None:
        pass

    def get(self, key: str, default: Any = None) -> Any:
        return default
