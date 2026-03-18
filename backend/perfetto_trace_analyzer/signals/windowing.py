"""Windowing helpers for time-based signal extraction."""

from __future__ import annotations

from bisect import bisect_left, bisect_right


def prepare_sorted_index(
    events: list[dict],
    ts_key: str = "ts",
) -> tuple[list[dict], list[int]]:
    """Sort events by timestamp and return an index for windowed lookup."""
    sorted_events = sorted(events, key=lambda event: event.get(ts_key, 0))
    ts_array = [event.get(ts_key, 0) for event in sorted_events]
    return sorted_events, ts_array


def find_overlapping_events(
    sorted_events: list[dict],
    ts_array: list[int],
    f_ts: int,
    f_end: int,
    upid: int | None = None,
    upid_key: str = "upid",
    overlap: bool = False,
) -> list[dict]:
    """Find events inside a time window, optionally filtered by process."""
    if not sorted_events:
        return []

    if overlap:
        right = bisect_left(ts_array, f_end)
        candidates = []
        for i in range(right):
            event = sorted_events[i]
            start_ts = event.get("ts", 0)
            end_ts = start_ts + (event.get("dur", 0) or 0)
            if end_ts > f_ts:
                if upid is None or event.get(upid_key) == upid:
                    candidates.append(event)
        return candidates

    left = bisect_left(ts_array, f_ts)
    right = bisect_right(ts_array, f_end)
    if upid is None:
        return sorted_events[left:right]
    return [event for event in sorted_events[left:right] if event.get(upid_key) == upid]
