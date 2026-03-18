"""CPU and GPU diagnostic capabilities."""

from __future__ import annotations

from typing import Any

from ..metrics.cpu import (
    build_cpu_capacity_clusters,
    build_cpu_frequency_stats,
    build_gpu_frequency_stats,
    build_hardware_context,
    summarize_cpu_freq_window as _summarize_cpu_freq_window,
)


def get_cpu_frequency_stats(sql_results: dict[str, object]) -> list[dict[str, Any]]:
    return build_cpu_frequency_stats(sql_results)


def get_gpu_frequency_stats(sql_results: dict[str, object]) -> dict[str, Any] | None:
    return build_gpu_frequency_stats(sql_results)


def get_cpu_capacity_clusters(sql_results: dict[str, object]) -> list[dict[str, Any]]:
    return build_cpu_capacity_clusters(sql_results)


def describe_hardware_context(statistics: dict[str, Any]) -> str:
    return build_hardware_context(statistics)


def summarize_cpu_freq_window(
    f_ts: int,
    f_end: int,
    sorted_cpu_freq: list[dict] | None = None,
    ts_array: list[int] | None = None,
) -> dict[str, Any]:
    return _summarize_cpu_freq_window(
        f_ts,
        f_end,
        sorted_cpu_freq=sorted_cpu_freq,
        ts_array=ts_array,
    )
