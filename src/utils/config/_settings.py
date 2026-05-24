"""
Composed Settings — flat facade that aggregates all config sub-modules.

Every attribute from ProvidersConfig, RuntimeConfig, HardwareConfig,
ChannelsConfig, and MemoryConfig is available directly on a Settings
instance, preserving full backward compatibility with the old single-file
config.py.
"""

from utils.config.providers import ProvidersConfig
from utils.config.runtime import RuntimeConfig
from utils.config.hardware import HardwareConfig
from utils.config.channels import ChannelsConfig
from utils.config.memory import MemoryConfig


class Settings(ProvidersConfig, RuntimeConfig, HardwareConfig, ChannelsConfig, MemoryConfig):
    """
    Rooster unified configuration.

    Merges all config sub-modules into a single flat class so that
    ``from utils.config import settings`` and ``settings.ZHIPU_KEY``
    continue to work without changes.
    """

    pass
