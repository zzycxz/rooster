"""Rooster Visual Gateway — FastAPI application entry point.

All route logic is delegated to domain-specific routers under gateway/routes/.
This file handles: app creation, middleware, shared state, route wiring, startup.
"""

import os
import asyncio
import logging
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
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


# UI directory — prefer built dist/, fall back to source src/
_ui_base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")
_ui_dir = os.path.join(_ui_base, "dist")
if not os.path.isdir(_ui_dir):
    _ui_dir = os.path.join(_ui_base, "src")  # dev fallback
if os.path.isdir(_ui_dir):
    app.mount("/ui", StaticFiles(directory=_ui_dir), name="ui")


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
# Dashboard HTML
# ---------------------------------------------------------------------------
@app.get("/dashboard", include_in_schema=False)
async def serve_dashboard():
    from fastapi import HTTPException
    from fastapi.responses import HTMLResponse

    html_path = os.path.join(_ui_dir, "dashboard.html")
    if not os.path.exists(html_path):
        raise HTTPException(status_code=404, detail="Dashboard not built yet.")

    # Inject token into HTML so the Dashboard JS can authenticate API calls
    # without requiring ?token=xxx in the URL
    gateway_key = os.getenv("GATEWAY_API_KEY", "").strip()
    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    if gateway_key:
        inject = (
            '<script>window.__ROOSTER_TOKEN__ = "'
            + gateway_key.replace("\\", "\\\\").replace('"', '\\"')
            + '";</script>'
        )
        html_content = html_content.replace("<head>", "<head>\n" + inject, 1)

    return HTMLResponse(
        content=html_content,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )


# ---------------------------------------------------------------------------
# Register domain routers
# ---------------------------------------------------------------------------
from .routes.config import router as config_router, handle_config_save, get_env_local_path
from .routes.skills import (
    router as skills_router,
    wire as wire_skills,
    fetch_cloud_skills_background,
)
from .routes.memory import router as memory_router, wire as wire_memory
from .routes.models import router as models_router, wire as wire_models
from .routes.system import router as system_router, wire as wire_system
from .routes.websockets import router as ws_router, wire as wire_ws

app.include_router(config_router)
app.include_router(skills_router)
app.include_router(memory_router)
app.include_router(models_router)
app.include_router(system_router)
app.include_router(ws_router)

# Wire shared state into routers
wire_skills(_skills_dir, get_skill_loader, invalidate_skill_loader)
wire_memory(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".rooster"))
wire_models(_hf_downloads, _hf_download_dir, get_env_local_path)
wire_system(get_skill_loader, get_env_local_path)
wire_ws(manager, channel_registry, _ui_dir, handle_config_save)


# ---------------------------------------------------------------------------
# Startup event
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def startup_warm_caches():
    get_skill_loader()
    asyncio.create_task(fetch_cloud_skills_background())
