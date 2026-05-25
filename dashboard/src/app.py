"""Dashboard FastAPI sub-app — assembles all dashboard routes, WS, and static files."""

import os
import asyncio
import logging
from typing import Any, Callable, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def create_dashboard_app(
    skills_dir: str,
    rooster_dir: str,
    get_skill_loader: Callable,
    invalidate_skill_loader: Callable,
    get_env_local_path: Callable,
    handle_config_save: Callable,
    hf_downloads: Dict[str, Dict[str, Any]],
    hf_download_dir: str,
) -> FastAPI:
    """Create and return a fully configured Dashboard FastAPI sub-application."""
    app = FastAPI(title="Rooster Dashboard", docs_url=None, redoc_url=None)

    # ── Exception handlers ──────────────────────────────────────────────
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request, exc):
        logger.exception(f"Unhandled dashboard exception on {request.method} {request.url.path}")
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    # ── Register routers ────────────────────────────────────────────────
    from .routes.config import router as config_router
    from .routes.skills import router as skills_router, wire as wire_skills, fetch_cloud_skills_background
    from .routes.memory import router as memory_router, wire as wire_memory
    from .routes.models import router as models_router, wire as wire_models
    from .routes.system import router as system_router, wire as wire_system
    from .routes.scheduler import router as scheduler_router, wire as wire_scheduler
    from .ws import router as ws_router, wire as wire_ws

    app.include_router(config_router)
    app.include_router(skills_router)
    app.include_router(memory_router)
    app.include_router(models_router)
    app.include_router(system_router)
    app.include_router(scheduler_router)
    app.include_router(ws_router)

    # Wire shared state into routers
    wire_skills(skills_dir, get_skill_loader, invalidate_skill_loader)
    wire_memory(rooster_dir)
    wire_models(hf_downloads, hf_download_dir, get_env_local_path)
    wire_system(get_skill_loader, get_env_local_path)
    wire_scheduler(rooster_dir)
    wire_ws(handle_config_save)

    # ── UI directory resolution (used by /dashboard HTML endpoint) ──────
    _ui_base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")
    _ui_dir = os.path.join(_ui_base, "dist")
    if not os.path.isdir(_ui_dir):
        _ui_dir = os.path.join(_ui_base, "src")  # dev fallback

    # ── Dashboard HTML endpoint ─────────────────────────────────────────
    @app.get("/dashboard", include_in_schema=False)
    async def serve_dashboard():
        html_path = os.path.join(_ui_dir, "dashboard.html")
        if not os.path.exists(html_path):
            raise HTTPException(status_code=404, detail="Dashboard not built yet.")

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

    # ── Startup ─────────────────────────────────────────────────────────
    @app.on_event("startup")
    async def startup_warm_caches():
        get_skill_loader()
        asyncio.create_task(fetch_cloud_skills_background())

    return app
