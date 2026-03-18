"""Trace upload and listing services."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from fastapi import UploadFile

from ..dependencies import AppServices, get_services


@dataclass
class StoredTrace:
    """Metadata for a newly uploaded trace."""

    trace_id: str
    filename: str
    save_path: str


class TraceService:
    """Handles trace file storage and trace metadata access."""

    def __init__(self, services: AppServices | None = None):
        self.services = services or get_services()

    def list_traces(self) -> list[dict[str, object]]:
        return self.services.state_manager.list_traces()

    async def store_upload(self, file: UploadFile) -> StoredTrace:
        os.makedirs(self.services.upload_dir, exist_ok=True)
        trace_id = uuid.uuid4().hex[:12]
        filename = file.filename or f"trace_{trace_id}"
        save_path = os.path.join(self.services.upload_dir, f"{trace_id}_{filename}")

        content = await file.read()
        with open(save_path, "wb") as handle:
            handle.write(content)

        self.services.state_manager.set_trace_path(trace_id, save_path)
        self.services.state_manager.set_status(trace_id, "analyzing", "Starting analysis...")
        return StoredTrace(trace_id=trace_id, filename=filename, save_path=save_path)
