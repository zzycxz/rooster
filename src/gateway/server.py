"""Rooster Visual Gateway — FastAPI application entry point.

Handles: app creation, middleware, shared state, core route wiring (WS, webhook).
Dashboard routes are mounted separately by the dashboard sub-package at startup.
"""

import os
import asyncio
import logging
from typing import Any, Dict

from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException

from .connection_manager import ConnectionManager
from .auth import APIKeyMiddleware, RateLimiter
from .security import SecurityHeadersMiddleware, RequestSizeLimitMiddleware
from channels.registry import ChannelRegistry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(title="Rooster Visual Gateway")

_rate_limiter = RateLimiter(max_requests=100, window_seconds=60)
app.add_middleware(APIKeyMiddleware, rate_limiter=_rate_limiter)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
manager = ConnectionManager()
channel_registry = ChannelRegistry.get_instance()

# HF download state
_hf_downloads: Dict[str, Dict[str, Any]] = {}
_hf_download_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "models", "hf_cache")
os.makedirs(_hf_download_dir, exist_ok=True)

# Skill loader cache
_skills_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills")
_cached_skill_loader = None


def get_skill_loader():
    global _cached_skill_loader
    if _cached_skill_loader is None:
        from skills._loader import SkillLoader

        _cached_skill_loader = SkillLoader(skills_dir=_skills_dir)
    return _cached_skill_loader


def invalidate_skill_loader():
    global _cached_skill_loader
    _cached_skill_loader = None


# ---------------------------------------------------------------------------
# Global exception handler — prevent internal details from leaking to clients
# ---------------------------------------------------------------------------
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):
    logger.exception(f"Unhandled exception on {request.method} {request.url.path}")
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ---------------------------------------------------------------------------
# Register core routers (gateway WS, node WS, webhook)
# ---------------------------------------------------------------------------
from .routes.websockets import router as ws_router, wire as wire_ws

app.include_router(ws_router)

# Wire shared state into core WebSocket routes
wire_ws(manager, channel_registry)


# ---------------------------------------------------------------------------
# Mount Dashboard sub-package (optional — graceful skip if not installed)
# ---------------------------------------------------------------------------
def _mount_dashboard():
    """Attempt to mount the dashboard sub-package. No-op if not installed."""
    try:
        from dashboard.src.mount import mount_dashboard

        mount_dashboard(
            main_app=app,
            skills_dir=_skills_dir,
            rooster_dir=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".rooster"),
            get_skill_loader=get_skill_loader,
            invalidate_skill_loader=invalidate_skill_loader,
            hf_downloads=_hf_downloads,
            hf_download_dir=_hf_download_dir,
        )
        logger.info("Dashboard sub-package mounted")
    except ImportError:
        logger.info("Dashboard sub-package not installed, skipping web dashboard")
    except Exception as exc:
        logger.warning(f"Failed to mount dashboard: {exc}")


_mount_dashboard()


# ---------------------------------------------------------------------------
# Startup event
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup_warm_caches():
    get_skill_loader()
