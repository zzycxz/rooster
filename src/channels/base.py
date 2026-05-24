import abc
import datetime
from typing import Any, Dict
from pydantic import BaseModel, Field


class InboundMessage(BaseModel):
    """标准化进站消息模型"""  # Standardized inbound message model

    sender_id: str = Field(description="发送者在原始平台上的唯一 ID")  # Sender's unique ID on original platform
    text: str = Field(description="消息文本内容")  # Message text content
    channel_id: str = Field(description="所属平台 ID (如 telegram, cli)")  # Platform ID
    session_id: str = Field(description="映射后的 Rooster Session ID")  # Mapped Rooster Session ID
    timestamp: datetime.datetime = Field(default_factory=datetime.datetime.now)
    raw_data: Dict[str, Any] = Field(
        default_factory=dict, description="保留原始平台的完整数据供特殊处理"
    )  # Preserve full original platform data


class BaseChannel(abc.ABC):
    """
    异步通道适配器基类。
    所有具体平台（CLI, Telegram, Discord）都必须继承此类。
    """

    # Async channel adapter base class. All concrete platforms must inherit from this class

    def __init__(self, channel_id: str):
        self.channel_id = channel_id
        self.is_running = False

    @abc.abstractmethod
    async def start(self):
        """启动通道监听或连接"""  # Start channel listening or connection
        pass

    @abc.abstractmethod
    async def stop(self):
        """停止通道并释放资源"""  # Stop channel and release resources
        pass

    @abc.abstractmethod
    async def send_message(self, to: str, text: str, **kwargs):
        """发送异步回复到指定目标"""  # Send async reply to specified target
        pass

    def standardize_message(self, raw_data: Any) -> InboundMessage:
        """
        子类具体实现：将平台私有格式转换为 InboundMessage。
        """
        # Subclass implementation: convert platform-specific format to InboundMessage
        raise NotImplementedError("Subclasses must implement standardize_message")
