"""WebSocket bridge and UI jump routes."""

import logging

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from ..dependencies import get_services

logger = logging.getLogger(__name__)
router = APIRouter(tags=["bridge"])
services = get_services()


@router.websocket("/ws/bridge")
async def ws_bridge(ws: WebSocket):
    """WebSocket endpoint for Perfetto UI bridge script."""
    await ws.accept()
    services.websocket_hub.register(ws)
    logger.info("Bridge WebSocket connected (%d total)", services.websocket_hub.size)
    try:
        while True:
            # Keep alive; bridge doesn't send data, but we need to detect disconnects
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        services.websocket_hub.discard(ws)
        logger.info(
            "Bridge WebSocket disconnected (%d total)",
            services.websocket_hub.size,
        )


@router.post("/api/jump")
async def api_jump(request: Request):
    """Trigger a jump+select in all connected Perfetto UI instances.

    Body: {"ts": <nanoseconds>, "dur": <nanoseconds>, "slice_type": "frame"|"slice"}
    Called by MCP server or any external tool.
    """
    body = await request.json()
    ts = body.get("ts")
    dur = body.get("dur", 0)
    if ts is None:
        raise HTTPException(400, "Missing 'ts' field")

    clients = await services.websocket_hub.broadcast_jump({
        "type": "jump",
        "ts": ts,
        "dur": dur,
        "process_name": body.get("process_name", ""),
        "upid": body.get("upid", 0),
        "jank_category": body.get("jank_category", ""),
        "slice_type": body.get("slice_type", "frame"),
    })
    return {"ok": True, "clients": clients, "ts": ts, "dur": dur}
