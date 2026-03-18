"""Scheduling-related metric builders."""

from __future__ import annotations

from ..signals.cpu import get_thread_cpu_scheduling_rows
from ..signals.windowing import find_overlapping_events


def build_thread_cpu_scheduling_aggs(
    sql_results: dict[str, object],
    min_dur_ms: float = 1.0,
) -> dict[str, float]:
    rows = get_thread_cpu_scheduling_rows(sql_results)
    agg: dict[str, float] = {}
    for row in rows:
        cpu = row.get("cpu", "unk")
        dur_ms = row.get("dur_ms", 0.0)
        key = f"CPU_{cpu}"
        agg[key] = agg.get(key, 0.0) + dur_ms
    return {
        cpu: round(dur_ms, 1)
        for cpu, dur_ms in agg.items()
        if dur_ms > min_dur_ms
    }


def summarize_sched_slices_window(
    f_ts: int,
    f_end: int,
    f_upid: int | None,
    sorted_sched: list[dict] | None = None,
    ts_array: list[int] | None = None,
) -> dict[str, float]:
    """Aggregate per-CPU scheduling time inside a window."""
    if not sorted_sched or not ts_array:
        return {}

    relevant = find_overlapping_events(
        sorted_sched,
        ts_array,
        f_ts,
        f_end,
        upid=f_upid,
        overlap=True,
    )

    agg: dict[str, float] = {}
    for sched_slice in relevant:
        overlap_start = max(sched_slice.get("ts", 0), f_ts)
        overlap_end = min(sched_slice.get("ts", 0) + sched_slice.get("dur", 0), f_end)
        cpu = sched_slice.get("cpu", "unknown")
        agg[str(cpu)] = agg.get(str(cpu), 0.0) + (overlap_end - overlap_start) / 1e6
    return {f"CPU_{cpu}": round(dur_ms, 2) for cpu, dur_ms in agg.items() if dur_ms > 0.5}
