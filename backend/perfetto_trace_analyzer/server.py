"""Web server for Perfetto Trace Analyzer.

Entrypoint that configures FastAPI, mounts routers, and manages application lifecycle.
"""

from __future__ import annotations

import logging
import os
import httpx

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import ConfigManager
from .dependencies import get_services
from .models import AppConfig
from .services.analysis_service import AnalysisService

# Import routers
from .routes.catalog import router as catalog_router
from .routes.traces import router as traces_router
from .routes.settings import router as settings_router
from .routes.bridge import router as bridge_router
from .proxy import router as proxy_router, _STATIC_DIR

logger = logging.getLogger(__name__)
services = get_services()
analysis_service = AnalysisService(services)

app = FastAPI(title="Perfetto Trace Analyzer", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include modules
app.include_router(traces_router)
app.include_router(settings_router)
app.include_router(bridge_router)
app.include_router(catalog_router)

# Mount static asset directory if it exists
_assets_dir = os.path.join(_STATIC_DIR, "assets")
if os.path.exists(_assets_dir):
    app.mount("/assets", StaticFiles(directory=_assets_dir), name="static-assets")

# Proxy router MUST be last as it contains the catch-all `/{path:path}`
app.include_router(proxy_router)


@app.on_event("startup")
async def _startup():
    services.http_client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)

    # Auto-initialize on reload: if trace_path was set via env, re-analyze
    trace_path = os.environ.get("_PTA_TRACE_PATH")
    if trace_path and not services.state_manager.has_results():
        config_path = os.environ.get("_PTA_CONFIG")
        config = ConfigManager.load(config_path)
        create_app(config=config, trace_path=trace_path)


@app.on_event("shutdown")
async def _shutdown():
    if services.http_client:
        await services.http_client.aclose()
        services.http_client = None
    services.trace_processor_pool.close_all()


def create_app(config: AppConfig | None = None, trace_path: str | None = None) -> FastAPI:
    services.state_manager.config = config or ConfigManager.load()
    if trace_path:
        analysis_service.preload_traces(services.state_manager.config, trace_path)
    return app
