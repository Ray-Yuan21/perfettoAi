"""Application service for reading state and shaping API responses."""

from __future__ import annotations

import os

from fastapi import HTTPException

from ..dependencies import AppServices, get_services
from ..presenters import present_trace_result


class ResultService:
    """Reads analysis state and converts it into API-friendly responses."""

    def __init__(self, services: AppServices | None = None):
        self.services = services or get_services()

    def get_status(self, trace_id: str) -> dict[str, str]:
        state_manager = self.services.state_manager
        status = state_manager.get_status(trace_id)
        if status is None:
            if state_manager.get_result(trace_id):
                return {"status": "done", "progress": "Analysis complete"}
            raise HTTPException(404, "Trace not found")
        return {
            "status": status,
            "progress": state_manager.get_progress(trace_id),
        }

    def get_trace_result(self, trace_id: str) -> dict[str, object]:
        result = self.services.state_manager.get_result(trace_id)
        if not result:
            raise HTTPException(404, "Trace not found")
        return present_trace_result(result)

    def get_trace_file_path(self, trace_id: str) -> str:
        path = self.services.state_manager.get_trace_path(trace_id)
        if not path or not os.path.exists(path):
            raise HTTPException(404, "Trace file not found")
        return path
