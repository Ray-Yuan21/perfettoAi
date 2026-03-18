"""Jank Analyzer Sub-package."""

from .analyzer import JankAnalyzer
from .frame_analysis import (
    classify_jank_frames,
    detect_severe_jank,
    is_real_jank as _is_real_jank,
    percentile as _percentile,
)

__all__ = [
    "JankAnalyzer",
    "classify_jank_frames",
    "detect_severe_jank",
    "_is_real_jank",
    "_percentile",
]
