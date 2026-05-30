from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from datetime import datetime


class Message(BaseModel):
    role: str = Field(..., description="角色: user, assistant, system, tool")
    content: str = Field(..., description="消息内容")
    images: List[str] = Field(default_factory=list, description="附带的图片（Base64URL列表）")
    timestamp: datetime = Field(default_factory=datetime.now)


class Session(BaseModel):
    session_id: str = Field(..., description="唯一会话ID")
    history: List[Message] = Field(default_factory=list, description="对话历史记录")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="会话元数据，如平台、用户名等")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    distilled_at: Optional[datetime] = Field(default=None, description="上次蒸馏完成时间")

    def add_message(self, role: str, content: str, images: Optional[List[str]] = None):
        """追加一条消息并更新活跃时间"""
        self.history.append(Message(role=role, content=content, images=images or []))
        self.updated_at = datetime.now()
        # 简单的记忆长度限制，防止爆窗（可根据需求调整）
        # Simple history length limit to prevent context overflow (adjustable)
        if len(self.history) > 100:
            self.history = self.history[-100:]
