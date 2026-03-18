"""High-level API presentation helpers."""

from __future__ import annotations

from typing import Any

from ..models import AnalysisResult
from ..reporter import result_to_dict
from .jank_presenter import build_jank_frames


def present_trace_result(result: AnalysisResult) -> dict[str, Any]:
    """Convert an analysis result into the API response payload."""
    data = result_to_dict(result)
    data["jank_frames"] = build_jank_frames(result)
    return data
