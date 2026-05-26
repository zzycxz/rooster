"""Dashboard mount entry point — plug the dashboard sub-app into the main gateway."""

import asyncio
import logging
import os
from typing import Callable, Dict, Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


def mount_dashboard(
    main_app: FastAPI,
    skills_dir: str,
    rooster_dir: str,
    get_skill_loader: Callable,
    invalidate_skill_loader: Callable,
    hf_downloads: Dict[str, Dict[str, Any]],
    hf_download_dir: str,
) -> None:
    """Mount the dashboard sub-application onto the main FastAPI app.

    This also registers the dashboard's broadcast_event as an event sink
    in the core engine's event_handler, so agent events flow to the dashboard.
    """
    from gateway.event_handler import register_event_sink
    from .dashboard_ws import broadcast_event
    from .log_bridge import install_log_bridge
    from .routes.config import handle_config_save, get_env_local_path
    from .app import create_dashboard_app

    # Register dashboard as an event sink for agent events.
    # Unregister first to ensure idempotency: if Guardian restarts the process and
    # mount_dashboard() is called again in the same interpreter session (e.g. via
    # uvicorn --reload or hot-module re-import), the global _event_sinks list would
    # accumulate duplicate references, causing every agent event to be broadcast N times.
    from gateway.event_handler import unregister_event_sink
    unregister_event_sink(broadcast_event)   # no-op if not yet registered
    register_event_sink(broadcast_event)

    # Create the dashboard sub-app
    dashboard_app = create_dashboard_app(
        skills_dir=skills_dir,
        rooster_dir=rooster_dir,
        get_skill_loader=get_skill_loader,
        invalidate_skill_loader=invalidate_skill_loader,
        get_env_local_path=get_env_local_path,
        handle_config_save=handle_config_save,
        hf_downloads=hf_downloads,
        hf_download_dir=hf_download_dir,
    )

    # Merge dashboard routes into main app (paths remain /api/*, /ws/dashboard, /dashboard)
    for route in dashboard_app.routes:
        main_app.routes.append(route)

    # Static files — mount separately on main app since app.mount needs top-level registration
    _ui_base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui")
    _ui_dir = os.path.join(_ui_base, "dist")
    if not os.path.isdir(_ui_dir):
        _ui_dir = os.path.join(_ui_base, "src")
    if os.path.isdir(_ui_dir):
        main_app.mount("/ui", StaticFiles(directory=_ui_dir), name="ui")

    # Install log bridge (forwards Python logs to dashboard WebSocket)
    try:
        install_log_bridge(loop=asyncio.get_event_loop())
    except RuntimeError:
        install_log_bridge()

    logger.info("Dashboard mounted successfully")
