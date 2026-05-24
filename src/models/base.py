from abc import ABC, abstractmethod
from typing import List, Dict, AsyncGenerator, Optional, Any
from pydantic import BaseModel


class LLMResponseDelta(BaseModel):
    content: str = ""
    role: Optional[str] = None
    finish_reason: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = (
        None  # 原生 Function Calling 返回的结构化工具调用  # Structured tool calls from native Function Calling
    )
    reasoning_content: Optional[str] = None  # MiMo thinking-mode internal scratchpad


class BaseModelClient(ABC):
    """
    所有 LLM 客户端的抽象基类。
    实现此接口以接入不同的模型供应商 (如本地 Ollama, 九天, DeepSeek 等)。

    Abstract base class for all LLM clients.
    Implement this interface to plug in different model providers (e.g. local Ollama, Jiutian, DeepSeek).
    """

    @abstractmethod
    async def chat_stream(
        self, model: str, messages: List[Dict[str, str]], **kwargs
    ) -> AsyncGenerator[LLMResponseDelta, None]:
        """流式对话接口"""  # Streaming chat interface
        pass

    @abstractmethod
    async def chat_non_stream(self, model: str, messages: List[Dict[str, str]], **kwargs) -> LLMResponseDelta:
        """非流式对话接口"""  # Non-streaming chat interface
        pass

    @abstractmethod
    async def close(self):
        """关闭连接"""  # Close connection
        pass
