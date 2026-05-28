"""
Gateway security middleware — security headers, request body size limits, input validation.
"""

import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Allowed env keys for /api/config/save — block PATH, LD_PRELOAD, etc.
_ALLOWED_CONFIG_KEYS = frozenset(
    {
        "LOCAL_URL",
        "LOCAL_KEY",
        "LOCAL_MODEL",
        "CLOUD_URL",
        "CLOUD_KEY",
        "CLOUD_MODEL",
        "ZHIPU_URL",
        "ZHIPU_KEY",
        "ZHIPU_MODEL",
        "ZHIPU_GLM_URL",
        "ZHIPU_GLM_KEY",
        "ZHIPU_GLM_MODEL",
        "OPENAI_URL",
        "OPENAI_KEY",
        "OPENAI_MODEL",
        "ANTHROPIC_URL",
        "ANTHROPIC_KEY",
        "ANTHROPIC_MODEL",
        "KIMI_URL",
        "KIMI_KEY",
        "KIMI_MODEL",
        "QWEN_URL",
        "QWEN_KEY",
        "QWEN_MODEL",
        "JIUTIAN_URL",
        "JIUTIAN_KEY",
        "JIUTIAN_MODEL",
        "JIUTIAN_MODEL_FAST",
        "MIMO_URL",
        "MIMO_KEY",
        "MIMO_MODEL",
        "SEARXNG_URL",
        "ENABLE_SEARCH_DDG",
        "ENABLE_SEARCH_SEARXNG",
        "ENABLE_SEARCH_BAIDU",
        "ENABLE_SEARCH_TAVILY",
        "TAVILY_API_KEY",
        "GATEWAY_API_KEY",
        "WEBHOOK_HMAC_SECRET",
        "LOG_LEVEL",
        "AUDIT_STRICTNESS",
        "AGENT_MAX_STEPS",
        "AGENT_STUCK_THRESHOLD",
        "AUDIT_MAX_REMAND_RETRY",
        "CHECKPOINT_ENABLED",
        "ENABLE_TUNNEL",
        "WEBHOOK_ENABLED",
        "MCP_DYNAMIC_ENABLED",
        "OUTPUT_DIR",
        "EVOLUTION_ENABLED",
        "PERMISSION_POLICY",
        "BLOCKED_TOOLS",
        "ALLOWED_TOOLS",
        "CUSTOM_SECURITY_PATTERNS_JSON",
        "ALLOWED_PATHS",
        # --- New Multi-LLM Role Assignments ---
        "ROUTER_MODEL_MODE",
        "STRATEGIST_MODEL_MODE",
        "EXECUTOR_MODEL_MODE",
        "EXECUTOR_MODEL_NAME",
        "AUDITOR_MODEL_MODE",
        "SOLO_MODEL_MODE",
        # --- Failover & Retry ---
        "LLM_FAILOVER_ENABLED",
        "LLM_FAILOVER_ORDER",
        "LLM_FAILOVER_RETRY_MAX",
        "LLM_MIN_INTERVAL",
        # --- Feishu ---
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_USER_OPEN_ID",
        # --- Aria2 ---
        "ARIA2_RPC_URL",
        "ARIA2_TOKEN",
    }
)

_MAX_VALUE_LENGTH = 500
_MAX_BODY_SIZE = 1_000_000  # 1 MB


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all HTTP responses."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self' ws: wss:; "
            "font-src 'self' data:;"
        )
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with body exceeding _MAX_BODY_SIZE."""

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > _MAX_BODY_SIZE:
                return Response(
                    content='{"error":"Request body too large"}',
                    status_code=413,
                    media_type="application/json",
                )
        return await call_next(request)


def validate_config_keys(data: dict) -> list:
    """
    Validate config save payload.
    Returns list of rejected keys.
    """
    rejected = []
    for k in data:
        if k not in _ALLOWED_CONFIG_KEYS:
            rejected.append(k)
    return rejected


def validate_config_values(data: dict) -> list:
    """
    Validate config values for length limits and dangerous characters.
    Returns list of keys with oversized or unsafe values.
    """
    oversized = []
    for k, v in data.items():
        if isinstance(v, str) and (len(v) > _MAX_VALUE_LENGTH or "\n" in v or "\r" in v or "\x00" in v):
            oversized.append(k)
    return oversized
