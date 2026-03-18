"""Analyzer-facing diagnostic capabilities.

This package is the semantic layer analyzers should depend on directly.
Lower-level signals/ and metrics/ modules remain internal building blocks.
"""

from .cpu_behavior import (
    describe_hardware_context,
    get_cpu_capacity_clusters,
    get_cpu_frequency_stats,
    get_gpu_frequency_stats,
    summarize_cpu_freq_window,
)
from .frame_timing import (
    atrace_frames_to_unified,
    classify_jank_frames,
    detect_severe_jank,
    get_unified_frame_timeline,
    is_real_jank,
    jank_severity,
    percentile,
    summarize_frame_timeline,
)
from .frame_window import (
    build_top_jank_frames,
    find_bottleneck,
)
from .thread_activity import (
    get_thread_cpu_scheduling_aggs,
    get_thread_state_aggs,
    summarize_sched_slices_window,
    summarize_thread_states_window,
)

__all__ = [
    "describe_hardware_context",
    "get_cpu_capacity_clusters",
    "get_cpu_frequency_stats",
    "get_gpu_frequency_stats",
    "get_thread_cpu_scheduling_aggs",
    "get_thread_state_aggs",
    "atrace_frames_to_unified",
    "build_top_jank_frames",
    "classify_jank_frames",
    "detect_severe_jank",
    "find_bottleneck",
    "get_unified_frame_timeline",
    "is_real_jank",
    "jank_severity",
    "percentile",
    "summarize_frame_timeline",
    "summarize_cpu_freq_window",
    "summarize_sched_slices_window",
    "summarize_thread_states_window",
]
