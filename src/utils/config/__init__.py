"""
src/utils/config/__init__.py

Configuration system entry point.
- Loads .env / .env.local via dotenv
- Exposes ``settings`` singleton
"""

from dotenv import load_dotenv

# .env.local 优先（含敏感密钥），.env 为公共默认值
# .env.local takes priority (contains sensitive keys), .env is public defaults
load_dotenv(dotenv_path=".env.local", override=True)
load_dotenv(dotenv_path=".env", override=False)

# Composed Settings from sub-modules
from utils.config._settings import Settings as _Settings

# Global singleton
settings = _Settings()

__all__ = ["settings", "Settings"]
