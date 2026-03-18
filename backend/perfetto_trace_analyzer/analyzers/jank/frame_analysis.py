"""Compatibility exports for jank frame analysis helpers.

The actual frame-level diagnostic capabilities now live in
``perfetto_trace_analyzer.diagnostics``. This module preserves the old import
surface for tests and any external callers.
"""

from __future__ import annotations

from typing import Any

from ...diagnostics import (
    atrace_frames_to_unified,
    build_top_jank_frames,
    classify_jank_frames,
    detect_severe_jank,
    find_bottleneck,
    is_real_jank,
    jank_severity,
    percentile,
)


# ─── SQL result materialization ───────────────────────────────

def materialize_sql_results(sql_results: dict) -> None:
    """Convert QueryResultIterator values to list[dict] in-place.

    TraceProcessor query results are single-use iterators; materializing them
    allows multiple passes (statistics, prompt building, etc.) and .get() access.
    """
    for key, val in sql_results.items():
        if isinstance(val, list):
            continue
        try:
            col_names = val.column_names
            sql_results[key] = [
                {col: getattr(row, col) for col in col_names}
                for row in val
            ]
        except Exception:
            sql_results[key] = []


def build_top_jank_frames_with_trees(
    frames: list[dict],
    call_stacks: list[dict],
    thread_states: list[dict] | None = None,
    cpu_freq_events: list[dict] | None = None,
    monitor_contentions: list[dict] | None = None,
    gc_events: list[dict] | None = None,
    gpu_render_slices: list[dict] | None = None,
    binder_flows: list[dict] | None = None,
    thread_cpu_scheduling: list[dict] | None = None,
    top_n: int = 20,
) -> list[dict]:
    """Compatibility wrapper for frame-window diagnostics."""
    return build_top_jank_frames(
        frames,
        call_stacks,
        thread_states=thread_states,
        cpu_freq_events=cpu_freq_events,
        monitor_contentions=monitor_contentions,
        gc_events=gc_events,
        gpu_render_slices=gpu_render_slices,
        binder_flows=binder_flows,
        thread_cpu_scheduling=thread_cpu_scheduling,
        top_n=top_n,
    )
