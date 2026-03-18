"""Helper functions for jank frame analysis.

Contains frame classification, atrace conversion, top jank frame enrichment,
bottleneck detection, and percentile calculation.
"""

from __future__ import annotations

import logging
from typing import Any

from ...analysis_utils import (
    build_call_tree as _build_call_tree,
    call_tree_to_text as _call_tree_to_text,
    prepare_sorted_index as _prepare_sorted_index,
    find_overlapping_events as _find_overlapping_events,
    summarize_thread_states as _summarize_thread_states,
    summarize_cpu_freq as _summarize_cpu_freq,
    summarize_sched_slices as _summarize_sched_slices,
)

logger = logging.getLogger(__name__)


# ─── atrace conversion ───────────────────────────────────────

def atrace_frames_to_unified(
    draw_frames: list[dict],
    traversal_frames: list[dict],
    target_ms: float,
) -> list[dict]:
    """Convert atrace draw-VRI / traversal slices to unified frame dicts.

    Matches each draw-VRI frame with the nearest preceding traversal slice
    from the same process (tid).  If no draw-VRI frames exist, falls back
    to traversal-only.  Output dicts mimic actual_frame_timeline_slice rows
    so all downstream statistics/LLM code can be reused unchanged.
    """
    primary = draw_frames if draw_frames else traversal_frames

    result = []
    for f in primary:
        dur_ms = f.get("dur_ms", 0) or (f.get("actual_dur", 0) / 1e6)
        overrun = max(0.0, dur_ms - target_ms)
        result.append({
            "frame_id": f.get("frame_id"),
            "actual_ts": f.get("actual_ts", f.get("ts", 0)),
            "actual_dur": f.get("actual_dur", f.get("dur", 0)),
            "overrun_ms": round(overrun, 2),
            "jank_type": "App Deadline Missed" if dur_ms > target_ms else "None",
            "present_type": "Late Present" if dur_ms > target_ms else "On-time Present",
            "layer_name": f.get("slice_name", ""),
            "process_name": f.get("process_name", ""),
            "upid": f.get("upid"),
            "pid": f.get("pid"),
        })
    return result


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


# ─── Frame classification ────────────────────────────────────

def is_real_jank(f: dict) -> bool:
    """Determine if a frame is a real jank frame.

    Rules:
    - present_type == "Dropped Frame" → always real jank
    - present_type == "Late Present" → real jank
    - present_type == "On-time Present" → not jank
    - jank_type != None with other present_type → real jank
    """
    jank_type = f.get("jank_type") or "None"
    present_type = f.get("present_type") or ""

    if present_type == "Dropped Frame":
        return True
    if present_type == "Late Present":
        return True
    if present_type == "On-time Present":
        return False
    if jank_type != "None":
        return True
    return False


def jank_severity(f: dict) -> str:
    """Classify jank severity based on present_type and overrun."""
    present_type = f.get("present_type") or ""
    overrun = f.get("overrun_ms", 0)
    if present_type == "Dropped Frame":
        return "high"
    if overrun > 16.67:
        return "high"
    if present_type == "Late Present":
        return "medium"
    return "low"


# ─── Percentile ──────────────────────────────────────────────

def percentile(sorted_values: list[float], p: float) -> float:
    """Compute the p-th percentile from a sorted list."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    k = (p / 100) * (n - 1)
    f = int(k)
    c = f + 1
    if c >= n:
        return sorted_values[-1]
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


# ─── Bottleneck detection ────────────────────────────────────

def find_bottleneck(call_tree: list[dict]) -> str:
    """Find the node with highest self_ms in the call tree, with clean name."""
    best: dict[str, Any] = {"self_ms": 0.0, "name": "", "thread": ""}

    def _walk(node: dict) -> None:
        if node.get("self_ms", 0) > best["self_ms"]:
            best["self_ms"] = node["self_ms"]
            best["name"] = node["name"]
            best["thread"] = node.get("thread", "")
        for c in node.get("children", []):
            _walk(c)

    for root in call_tree:
        _walk(root)

    if not best["name"]:
        return ""

    name = best["name"]
    if " - " in name:
        name = name.split(" - ")[0].strip()
    if name.startswith("Drawing") and len(name) > 10:
        name = "Drawing"
    if "(" in name and len(name) > 40:
        name = name[:name.index("(")].strip()

    return f"{name} ({best['self_ms']:.1f}ms self on {best['thread']})"


# ─── Top jank frame enrichment ────────────────────────────────

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
    """Build top-N jank frames with call trees, thread state, CPU freq, GC, SF, and lock contentions."""
    thread_states = thread_states or []
    cpu_freq_events = cpu_freq_events or []
    monitor_contentions = monitor_contentions or []
    gc_events = gc_events or []
    gpu_render_slices = gpu_render_slices or []
    binder_flows = binder_flows or []
    thread_cpu_scheduling = thread_cpu_scheduling or []

    # Pre-sort all lists for bisect lookup
    sorted_cs, ts_cs = _prepare_sorted_index(call_stacks)
    sorted_ts, ts_ts = _prepare_sorted_index(thread_states)
    sorted_cf, ts_cf = _prepare_sorted_index(cpu_freq_events)
    sorted_mc, ts_mc = _prepare_sorted_index(monitor_contentions)
    sorted_gc, ts_gc = _prepare_sorted_index(gc_events)
    sorted_gpu, ts_gpu = _prepare_sorted_index(gpu_render_slices)
    sorted_bf, ts_bf = _prepare_sorted_index(binder_flows, ts_key="caller_ts")
    sorted_sched, ts_sched = _prepare_sorted_index(thread_cpu_scheduling)

    jank_frames = sorted(
        [f for f in frames if is_real_jank(f)],
        key=lambda f: f.get("overrun_ms", 0), reverse=True
    )[:top_n]

    result = []
    for f in jank_frames:
        f_ts = f.get("actual_ts", 0)
        f_dur = f.get("actual_dur", 0)
        f_upid = f.get("upid")
        f_end = f_ts + f_dur

        frame_slices = _find_overlapping_events(
            sorted_cs, ts_cs, f_ts, f_end, upid=f_upid, overlap=True,
        )
        call_tree = _build_call_tree(frame_slices)
        root_cause = find_bottleneck(call_tree)

        frame_thread_states = _summarize_thread_states(
            f_ts, f_end, f_upid,
            sorted_thread_states=sorted_ts, ts_array=ts_ts,
        )

        frame_cpu_freq = _summarize_cpu_freq(
            f_ts, f_end,
            sorted_cpu_freq=sorted_cf, ts_array=ts_cf,
        )

        frame_contentions = _find_overlapping_events(
            sorted_mc, ts_mc, f_ts, f_end, upid=f_upid, overlap=True,
        )

        frame_gc = sorted(
            _find_overlapping_events(
                sorted_gc, ts_gc, f_ts, f_end, upid=f_upid, overlap=True,
            ),
            key=lambda x: x.get("dur_ms", 0), reverse=True,
        )

        frame_sf = sorted(
            _find_overlapping_events(
                sorted_gpu, ts_gpu, f_ts, f_end, overlap=True,
            ),
            key=lambda x: x.get("dur_ms", 0), reverse=True,
        )

        frame_binder = sorted(
            _find_overlapping_events(
                sorted_bf, ts_bf, f_ts, f_end, upid=f_upid,
                upid_key="caller_upid", overlap=False,
            ),
            key=lambda x: x.get("caller_dur_ms", 0), reverse=True,
        )

        frame_cpu_sched = _summarize_sched_slices(
            f_ts, f_end, f_upid, sorted_sched, ts_sched
        )

        result.append({
            "frame_id": f.get("frame_id"),
            "actual_ts": f_ts,
            "actual_dur": f_dur,
            "expected_dur": f.get("expected_dur"),
            "overrun_ms": round(f.get("overrun_ms", 0), 2),
            "jank_type": f.get("jank_type"),
            "present_type": f.get("present_type"),
            "layer_name": f.get("layer_name"),
            "process_name": f.get("process_name"),
            "call_tree": call_tree,
            "bottleneck": root_cause,
            "thread_states": frame_thread_states,
            "cpu_freq": frame_cpu_freq,
            "monitor_contentions": frame_contentions[:5],
            "gc_events": frame_gc[:3],
            "sf_gpu_render": frame_sf[:5],
            "binder_flows": frame_binder[:5],
            "thread_cpu_scheduling": frame_cpu_sched,
        })
    return result


# ─── Public helpers (used by tests) ──────────────────────────

def classify_jank_frames(durations_ms: list[float], target_frame_time_ms: float) -> list[bool]:
    """Classify each frame as jank (True) or not based on target frame time."""
    return [d > target_frame_time_ms for d in durations_ms]


def detect_severe_jank(jank_flags: list[bool], min_consecutive: int) -> list[dict[str, Any]]:
    """Detect severe jank events (consecutive jank frames >= min_consecutive)."""
    events: list[dict[str, Any]] = []
    i, n = 0, len(jank_flags)
    while i < n:
        if jank_flags[i]:
            start = i
            while i < n and jank_flags[i]:
                i += 1
            length = i - start
            if length >= min_consecutive:
                events.append({"start_index": start, "end_index": i - 1, "consecutive_frames": length})
        else:
            i += 1
    return events
