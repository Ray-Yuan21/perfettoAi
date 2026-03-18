"""Frame-window diagnostic capabilities used by jank analysis."""

from __future__ import annotations

from typing import Any

from ..signals.windowing import find_overlapping_events, prepare_sorted_index
from .cpu_behavior import summarize_cpu_freq_window
from .thread_activity import summarize_sched_slices_window, summarize_thread_states_window
from .frame_timing import is_real_jank


def find_bottleneck(call_tree: list[dict]) -> str:
    """Find the highest self-time node from a call tree."""
    best: dict[str, Any] = {"self_ms": 0.0, "name": "", "thread": ""}

    def _walk(node: dict) -> None:
        if node.get("self_ms", 0) > best["self_ms"]:
            best["self_ms"] = node["self_ms"]
            best["name"] = node["name"]
            best["thread"] = node.get("thread", "")
        for child in node.get("children", []):
            _walk(child)

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


def build_top_jank_frames(
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
    """Build top jank-frame diagnostics from shared frame-window capabilities."""
    thread_states = thread_states or []
    cpu_freq_events = cpu_freq_events or []
    monitor_contentions = monitor_contentions or []
    gc_events = gc_events or []
    gpu_render_slices = gpu_render_slices or []
    binder_flows = binder_flows or []
    thread_cpu_scheduling = thread_cpu_scheduling or []

    sorted_cs, ts_cs = prepare_sorted_index(call_stacks)
    sorted_ts, ts_ts = prepare_sorted_index(thread_states)
    sorted_cf, ts_cf = prepare_sorted_index(cpu_freq_events)
    sorted_mc, ts_mc = prepare_sorted_index(monitor_contentions)
    sorted_gc, ts_gc = prepare_sorted_index(gc_events)
    sorted_gpu, ts_gpu = prepare_sorted_index(gpu_render_slices)
    sorted_bf, ts_bf = prepare_sorted_index(binder_flows, ts_key="caller_ts")
    sorted_sched, ts_sched = prepare_sorted_index(thread_cpu_scheduling)

    jank_frames = sorted(
        [frame for frame in frames if is_real_jank(frame)],
        key=lambda frame: frame.get("overrun_ms", 0),
        reverse=True,
    )[:top_n]

    result = []
    for frame in jank_frames:
        from ..analysis_utils import build_call_tree  # avoid import cycle

        f_ts = frame.get("actual_ts", 0)
        f_dur = frame.get("actual_dur", 0)
        f_upid = frame.get("upid")
        f_end = f_ts + f_dur

        frame_slices = find_overlapping_events(
            sorted_cs,
            ts_cs,
            f_ts,
            f_end,
            upid=f_upid,
            overlap=True,
        )
        call_tree = build_call_tree(frame_slices)

        frame_thread_states = summarize_thread_states_window(
            f_ts,
            f_end,
            f_upid,
            sorted_thread_states=sorted_ts,
            ts_array=ts_ts,
        )
        frame_cpu_freq = summarize_cpu_freq_window(
            f_ts,
            f_end,
            sorted_cpu_freq=sorted_cf,
            ts_array=ts_cf,
        )
        frame_cpu_sched = summarize_sched_slices_window(
            f_ts,
            f_end,
            f_upid,
            sorted_sched=sorted_sched,
            ts_array=ts_sched,
        )

        frame_contentions = find_overlapping_events(
            sorted_mc,
            ts_mc,
            f_ts,
            f_end,
            upid=f_upid,
            overlap=True,
        )
        frame_gc = sorted(
            find_overlapping_events(sorted_gc, ts_gc, f_ts, f_end, upid=f_upid, overlap=True),
            key=lambda row: row.get("dur_ms", 0),
            reverse=True,
        )
        frame_sf = sorted(
            find_overlapping_events(sorted_gpu, ts_gpu, f_ts, f_end, overlap=True),
            key=lambda row: row.get("dur_ms", 0),
            reverse=True,
        )
        frame_binder = sorted(
            find_overlapping_events(
                sorted_bf,
                ts_bf,
                f_ts,
                f_end,
                upid=f_upid,
                upid_key="caller_upid",
                overlap=False,
            ),
            key=lambda row: row.get("caller_dur_ms", 0),
            reverse=True,
        )

        result.append(
            {
                "frame_id": frame.get("frame_id"),
                "actual_ts": f_ts,
                "actual_dur": f_dur,
                "expected_dur": frame.get("expected_dur"),
                "overrun_ms": round(frame.get("overrun_ms", 0), 2),
                "jank_type": frame.get("jank_type"),
                "present_type": frame.get("present_type"),
                "layer_name": frame.get("layer_name"),
                "process_name": frame.get("process_name"),
                "call_tree": call_tree,
                "bottleneck": find_bottleneck(call_tree),
                "thread_states": frame_thread_states,
                "cpu_freq": frame_cpu_freq,
                "monitor_contentions": frame_contentions[:5],
                "gc_events": frame_gc[:3],
                "sf_gpu_render": frame_sf[:5],
                "binder_flows": frame_binder[:5],
                "thread_cpu_scheduling": frame_cpu_sched,
            }
        )
    return result
