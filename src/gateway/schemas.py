from pydantic import BaseModel, Field
from typing import Any, Dict, Optional


class GatewayRequest(BaseModel):
    # 兼容 Rooster 真实协议字段
    # Compatible with the real Rooster protocol fields
    method: str = Field(..., description="请求方法, 例如 'chat.send'")  # Request method, e.g. 'chat.send'
    action: Optional[str] = Field(None, description="请求动作，兼容旧协议")  # Request action, backward-compatible
    params: Optional[Dict[str, Any]] = Field(default_factory=dict, description="请求参数内容")  # Request parameters
    payload: Optional[Dict[str, Any]] = Field(
        None, description="请求载荷，兼容旧协议"
    )  # Request payload, backward-compatible
    session_id: Optional[str] = Field(None, description="会话 ID")  # Session ID
    id: Optional[str] = Field(None, description="请求唯一标识，用于响应对齐")  # Unique request ID for response matching


class GatewayResponse(BaseModel):
    # 兼容 Rooster 真实响应字段
    # Compatible with the real Rooster response fields
    status: str = Field(..., description="响应状态: 'success' 或 'error'")  # Response status: 'success' or 'error'
    data: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="成功时的返回数据"
    )  # Return data on success
    error: Optional[Any] = Field(None, description="失败时的错误详情对象")  # Error details on failure
    id: Optional[str] = Field(None, description="对应请求的 ID")  # Corresponding request ID
