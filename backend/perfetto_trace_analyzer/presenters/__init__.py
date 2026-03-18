"""Presentation-layer helpers for API responses."""

from .jank_presenter import build_jank_frames
from .trace_result_presenter import present_trace_result

__all__ = ["build_jank_frames", "present_trace_result"]
