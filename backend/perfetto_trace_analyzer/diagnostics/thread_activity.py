"""Thread-state and scheduling diagnostic capabilities."""

from __future__ import annotations

from ..metrics.scheduling import (
    build_thread_cpu_scheduling_aggs,
    summarize_sched_slices_window as _summarize_sched_slices_window,
)
from ..metrics.threads import (
    build_thread_state_aggs,
    summarize_thread_states_window as _summarize_thread_states_window,
)


def get_thread_state_aggs(sql_results: dict[str, object]) -> dict[str, float]:
    return build_thread_state_aggs(sql_results)


def get_thread_cpu_scheduling_aggs(
    sql_results: dict[str, object],
    min_dur_ms: float = 1.0,
) -> dict[str, float]:
    return build_thread_cpu_scheduling_aggs(sql_results, min_dur_ms=min_dur_ms)


def summarize_thread_states_window(
    f_ts: int,
    f_end: int,
    f_upid: int | None,
    sorted_thread_states: list[dict] | None = None,
    ts_array: list[int] | None = None,
) -> list[dict]:
    return _summarize_thread_states_window(
        f_ts,
        f_end,
        f_upid,
        sorted_thread_states=sorted_thread_states,
        ts_array=ts_array,
    )


def summarize_sched_slices_window(
    f_ts: int,
    f_end: int,
    f_upid: int | None,
    sorted_sched: list[dict] | None = None,
    ts_array: list[int] | None = None,
) -> dict[str, float]:
    return _summarize_sched_slices_window(
        f_ts,
        f_end,
        f_upid,
        sorted_sched=sorted_sched,
        ts_array=ts_array,
    )
