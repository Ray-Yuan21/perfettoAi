"""Reverse proxy to ui.perfetto.dev."""

import os
import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .dependencies import get_services

PERFETTO_UI_ORIGIN = "https://ui.perfetto.dev"
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
services = get_services()

# JavaScript bridge injected into Perfetto UI HTML.
_BRIDGE_JS_PATH = os.path.join(_STATIC_DIR, "bridge.js")

def _load_bridge_script() -> str:
    """Load the JS bridge script from file and wrap in <script> tags."""
    try:
        with open(_BRIDGE_JS_PATH, "r", encoding="utf-8") as f:
            js = f.read()
        return f"\n<script>\n{js}\n</script>\n"
    except FileNotFoundError:
        return ""

_PERFETTO_BRIDGE_SCRIPT = _load_bridge_script()

router = APIRouter(tags=["proxy"])

if os.path.isdir(_STATIC_DIR):
    _assets_dir = os.path.join(_STATIC_DIR, "assets")
    if os.path.isdir(_assets_dir):
        # We can't mount this directly on APIRouter in the same way, but it's handled in server.py
        pass

@router.get("/")
async def serve_frontend(request: Request):
    index_path = os.path.join(_STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    # Fallback to serving Perfetto UI if frontend is not built
    return await perfetto_ui_root(request)

@router.get("/perfetto-ui")
@router.get("/perfetto-ui/")
async def perfetto_ui_root(request: Request):
    """Serve Perfetto UI root with bridge script injected (production mode)."""
    try:
        if services.http_client:
            resp = await services.http_client.get(
                PERFETTO_UI_ORIGIN + "/",
                headers={"Accept": "text/html", "Accept-Encoding": "identity"},
            )
        else:
            return Response("No HTTP client", status_code=500)
    except httpx.HTTPError as e:
        return Response(content=f"Proxy error: {e}", status_code=502)
    html = resp.content.decode("utf-8", errors="replace")
    # Inject <base href="/"> so relative asset paths resolve from root, not /perfetto-ui/
    html = html.replace("<head>", '<head><base href="/">', 1)
    html = html.replace("</body>", _PERFETTO_BRIDGE_SCRIPT + "</body>")
    return Response(content=html.encode("utf-8"), status_code=200,
                    headers={"content-type": "text/html; charset=utf-8"})

@router.api_route("/{path:path}", methods=["GET", "HEAD", "OPTIONS"])
async def perfetto_proxy(path: str, request: Request):
    """Reverse proxy requests to ui.perfetto.dev."""
    if not services.http_client:
        services.http_client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)

    # Block service worker to prevent caching
    if "service_worker" in path.lower() or "service-worker" in path.lower() or path.endswith("sw.js"):
        return Response(content="// no-op service worker", headers={"content-type": "application/javascript"})

    target_url = f"{PERFETTO_UI_ORIGIN}/{path}"
    query = str(request.url.query)
    if query:
        target_url += f"?{query}"

    try:
        resp = await services.http_client.get(
            target_url,
            headers={
                "Accept": request.headers.get("accept", "*/*"),
                "Accept-Encoding": "identity",
            },
        )
    except httpx.HTTPError as e:
        return Response(content=f"Proxy error: {e}", status_code=502)

    headers: dict[str, str] = {}
    for key in ("content-type", "cache-control", "etag", "last-modified"):
        if key in resp.headers:
            headers[key] = resp.headers[key]

    content = resp.content
    ct = resp.headers.get("content-type", "")

    # Inject bridge script into the root HTML page (dev mode: path == "")
    if path in ("", "perfetto-ui", "perfetto-ui/") and "text/html" in ct:
        html = content.decode("utf-8", errors="replace")
        html = html.replace("</body>", _PERFETTO_BRIDGE_SCRIPT + "</body>")
        content = html.encode("utf-8")
        headers["content-type"] = "text/html; charset=utf-8"

    return Response(
        content=content,
        status_code=resp.status_code,
        headers=headers,
    )
