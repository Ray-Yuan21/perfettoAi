"""Shared dependency access for backend services and routes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import httpx
from fastapi import WebSocket

from .state import StateManager
from .trace_processor import TraceProcessorPool

_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
_RESULTS_DIR = os.path.join(_UPLOAD_DIR, ".results")


class WebSocketHub:
    """Tracks connected Perfetto bridge clients."""

    def __init__(self):
        self._clients: set[WebSocket] = set()

    def register(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def discard(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    @property
    def size(self) -> int:
        return len(self._clients)

    async def broadcast_jump(self, payload: dict[str, object]) -> int:
        message = json.dumps(payload)
        disconnected: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self._clients.discard(ws)

        return len(self._clients)


@dataclass
class AppServices:
    """Container for runtime services shared across the app."""

    upload_dir: str
    results_dir: str
    state_manager: StateManager = field(init=False)
    trace_processor_pool: TraceProcessorPool = field(
        default_factory=lambda: TraceProcessorPool(max_size=3)
    )
    websocket_hub: WebSocketHub = field(default_factory=WebSocketHub)
    http_client: httpx.AsyncClient | None = None

    def __post_init__(self) -> None:
        self.state_manager = StateManager(data_dir=self.results_dir)


_services = AppServices(
    upload_dir=_UPLOAD_DIR,
    results_dir=_RESULTS_DIR,
)


def get_services() -> AppServices:
    """Return the singleton service container."""
    return _services
