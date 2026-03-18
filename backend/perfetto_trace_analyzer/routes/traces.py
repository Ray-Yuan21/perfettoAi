"""Trace upload, listing, and analysis routes."""

import os

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import FileResponse

from ..services import AnalysisService, ResultService, TraceService

router = APIRouter(prefix="/api/traces", tags=["traces"])

trace_service = TraceService()
analysis_service = AnalysisService()
result_service = ResultService()


@router.get("")
async def list_traces():
    return {"traces": trace_service.list_traces()}


@router.post("/upload")
async def upload_trace(request: Request, file: UploadFile):
    """Upload a trace file, save it, and start async analysis."""
    analyzers_param = request.query_params.get("analyzers")
    analyzer_names = (
        [name.strip() for name in analyzers_param.split(",") if name.strip()]
        if analyzers_param
        else None
    )
    stored_trace = await trace_service.store_upload(file)
    analysis_service.schedule_analysis(
        stored_trace.trace_id,
        stored_trace.save_path,
        analyzer_names,
    )
    return {
        "trace_id": stored_trace.trace_id,
        "filename": stored_trace.filename,
    }


@router.get("/{trace_id}/status")
async def get_trace_status(trace_id: str):
    """Query the analysis status of a trace."""
    return result_service.get_status(trace_id)


@router.get("/{trace_id}")
async def get_trace_result(trace_id: str):
    return result_service.get_trace_result(trace_id)


@router.get("/{trace_id}/file")
async def serve_trace_file(trace_id: str):
    path = result_service.get_trace_file_path(trace_id)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Content-Disposition": f"attachment; filename={os.path.basename(path)}",
        },
    )
