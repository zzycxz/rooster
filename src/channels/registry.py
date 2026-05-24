import logging
from typing import Dict, Optional
from .base import BaseChannel

logger = logging.getLogger(__name__)


class ChannelRegistry:
    """
    Rooster 多平台通道注册表。
    负责管理各种 Bot 实例（Telegram, Discord, CLI 等）的生命周期与路由。
    """

    # Multi-platform channel registry. Manages lifecycle and routing of various Bot instances

    _instance: Optional["ChannelRegistry"] = None

    def __init__(self):
        self._channels: Dict[str, BaseChannel] = {}

    @classmethod
    def get_instance(cls) -> "ChannelRegistry":
        """单例模式获取注册表实例"""  # Singleton pattern to get registry instance
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, channel: BaseChannel):
        """注册一个新的通道实例"""  # Register a new channel instance
        if channel.channel_id in self._channels:
            logger.warning(f"Channel {channel.channel_id} already registered. Overwriting...")

        self._channels[channel.channel_id] = channel
        logger.info(f"Registered Channel: {channel.channel_id}")

    def get_channel(self, channel_id: str) -> Optional[BaseChannel]:
        """根据 ID 检索通道实例"""  # Retrieve channel instance by ID
        return self._channels.get(channel_id)

    async def start_all(self):
        """启动所有已注册的通道"""  # Start all registered channels
        for channel_id, channel in self._channels.items():
            if not channel.is_running:
                logger.info(f"Starting channel: {channel_id}")
                await channel.start()
                channel.is_running = True

    async def stop_all(self):
        """停止所有已注册的通道并清理资源"""  # Stop all registered channels and clean up resources
        for channel_id, channel in self._channels.items():
            if channel.is_running:
                logger.info(f"Stopping channel: {channel_id}")
                await channel.stop()
                channel.is_running = False
