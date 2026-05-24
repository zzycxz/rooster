from enum import Enum
from typing import Any, Dict, Optional


class ErrorCodes(str, Enum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UNAVAILABLE = "UNAVAILABLE"


def error_shape(code: ErrorCodes, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """生成符合 Rooster 规范的错误响应结构"""
    return {"error": {"code": code.value, "message": message, "details": details}}
