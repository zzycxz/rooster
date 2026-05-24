"""Gateway authentication for Rooster.

Provides:
- APIKeyMiddleware: validates GATEWAY_API_KEY on HTTP endpoints
- HMACVerifier: validates webhook signatures
- RateLimiter: per-IP sliding window rate limiting
- WebSocket token validation

When GATEWAY_API_KEY is empty/not set, all auth checks are skipped (local dev mode).
"""

import hashlib
import hmac as hmac_mod
import logging
import os
import time
from collections import defaultdict
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# Paths that never require authentication
_PUBLIC_PATHS = {"/", "/docs", "/openapi.json", "/favicon.ico"}

# All env var names that hold secrets — used for log sanitization
_SECRET_ENV_KEYS = frozenset(
    {
        "ZHIPU_KEY",
        "EMBEDDING_KEY",
        "JIUTIAN_KEY",
        "MIMO_KEY",
        "TAVILY_API_KEY",
        "E2B_API_KEY",
        "FEISHU_APP_SECRET",
        "GATEWAY_API_KEY",
        "WEBHOOK_HMAC_SECRET",
        "CLOUD_KEY",
        "LOCAL_KEY",
    }
)


def _get_api_key() -> str:
    return os.getenv("GATEWAY_API_KEY", "").strip()


def _get_hmac_secret() -> str:
    return os.getenv("WEBHOOK_HMAC_SECRET", "").strip()


def _is_auth_enabled() -> bool:
    return bool(_get_api_key())


def mask_secret(value: str, visible: int = 4) -> str:
    """Mask a secret string, showing only the first `visible` chars.

    >>> mask_secret("sk-abc123xyz789")
    'sk-a...789'
    >>> mask_secret("short")
    'sh...t'
    >>> mask_secret("")
    ''
    """
    if not value:
        return ""
    if len(value) <= visible * 2:
        return value[0] + "..." + value[-1] if len(value) >= 2 else "*"
    return value[:visible] + "..." + value[-4:]


def sanitize_log_message(msg: str) -> str:
    """Replace any known secret values found in a log message with masked versions.

    Called by the log bridge before forwarding to Dashboard WebSocket.
    """
    for key in _SECRET_ENV_KEYS:
        val = os.getenv(key, "").strip()
        if val and val in msg:
            msg = msg.replace(val, mask_secret(val))
    return msg


class RateLimiter:
    """Sliding window rate limiter per client IP."""

    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_ip: str) -> bool:
        # Loopback IPs (localhost) are completely exempt from rate limiting to prevent blocking local development and UI updates
        if client_ip in ("127.0.0.1", "::1", "localhost"):
            return True
        now = time.time()
        cutoff = now - self.window_seconds
        timestamps = self._requests[client_ip]
        # Prune old entries
        self._requests[client_ip] = [t for t in timestamps if t > cutoff]
        if len(self._requests[client_ip]) >= self.max_requests:
            return False
        self._requests[client_ip].append(now)
        return True


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Validates API key on HTTP requests when GATEWAY_API_KEY is configured.

    Accepts key via:
    1. X-API-Key header (preferred)
    2. Authorization: Bearer <key> header

    Query parameter (?api_key=) is intentionally NOT supported to avoid
    leaking secrets into browser history, proxy logs, and referer headers.
    """

    def __init__(self, app, rate_limiter: Optional[RateLimiter] = None):
        super().__init__(app)
        self.rate_limiter = rate_limiter or RateLimiter()

    async def dispatch(self, request: Request, call_next):
        # 豁免公共路径、静态 UI 资产和 Dashboard 页面本身的鉴权（以允许浏览器在未携带 headers 的情况下加载前端）
        # Exempt public paths, static UI assets, and the Dashboard page itself from auth (allows browsers to load the frontend without headers)
        path = request.url.path
        if path in _PUBLIC_PATHS or path == "/dashboard" or path.startswith("/ui/"):
            return await call_next(request)

        # Localhost is fully exempt from auth (same-machine Dashboard access)
        client_ip = request.client.host if request.client else "unknown"
        if client_ip in ("127.0.0.1", "::1", "localhost"):
            from utils.config import settings

            if settings.GATEWAY_LOCALHOST_AUTH:
                return await call_next(request)

        # Rate limiting (always active)
        if not self.rate_limiter.is_allowed(client_ip):
            logger.warning("Rate limit exceeded for %s on %s", client_ip, request.url.path)
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

        # Auth check (only when configured)
        if not _is_auth_enabled():
            return await call_next(request)

        api_key = _get_api_key()

        # Check X-API-Key header
        provided = request.headers.get("X-API-Key", "")
        # Fallback: Authorization: Bearer <key>
        if not provided:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                provided = auth_header[7:]

        # Constant-time comparison to prevent timing attacks
        if not hmac_mod.compare_digest(provided, api_key):
            logger.warning("Auth failed for %s on %s", client_ip, request.url.path)
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})

        return await call_next(request)


class HMACVerifier:
    """Validates HMAC-SHA256 signatures on webhook payloads."""

    @staticmethod
    def verify(payload: bytes, signature: str) -> bool:
        secret = _get_hmac_secret()
        if not secret:
            return True  # No secret configured, skip verification

        expected = hmac_mod.new(
            secret.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

        return hmac_mod.compare_digest(f"sha256={expected}", signature)

    @staticmethod
    def is_configured() -> bool:
        return bool(_get_hmac_secret())
