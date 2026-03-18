"""Thread-state metric builders."""

from __future__ import annotations

from ..signals.threads import get_thread_state_rows
from ..signals.windowing import find_overlapping_events


def build_thread_state_aggs(sql_results: dict[str, object]) -> dict[str, float]:
    rows = get_thread_state_rows(sql_results)
    state_aggs: dict[str, float] = {}
    for row in rows:
        state = row.get("state", "Unknown")
        dur_ms = row.get("dur_ms", 0)
        state_aggs[state] = state_aggs.get(state, 0.0) + dur_ms
    return {state: round(dur_ms, 1) for state, dur_ms in state_aggs.items()}


def summarize_thread_states_window(
    f_ts: int,
    f_end: int,
    f_upid: int | None,
    sorted_thread_states: list[dict] | None = None,
    ts_array: list[int] | None = None,
) -> list[dict]:
    """Summarize thread states inside a time window."""
    if not sorted_thread_states or not ts_array:
        return []

    relevant = find_overlapping_events(
        sorted_thread_states,
        ts_array,
        f_ts,
        f_end,
        upid=f_upid,
        overlap=True,
    )
    if not relevant:
        return []

    agg: dict[str, float] = {}
    for state_row in relevant:
        thread = state_row.get("thread_name", "")
        state = state_row.get("state", "?")
        io_wait = state_row.get("io_wait")
        label = f"{thread}:{state}"
        if io_wait:
            label += ":io"
        overlap_start = max(state_row.get("ts", 0), f_ts)
        overlap_end = min(state_row.get("ts", 0) + (state_row.get("dur", 0) or 0), f_end)
        dur_ms = max(0, overlap_end - overlap_start) / 1e6
        agg[label] = agg.get(label, 0.0) + dur_ms

    result = []
    for label, dur_ms in sorted(agg.items(), key=lambda item: item[1], reverse=True):
        if dur_ms < 0.5:
            continue
        parts = label.split(":")
        result.append(
            {
                "thread": parts[0],
                "state": parts[1] if len(parts) > 1 else "?",
                "io_wait": len(parts) > 2,
                "dur_ms": round(dur_ms, 2),
            }
        )
    return result[:20]
