"""Raw CPU-, GPU-, and scheduling-related signal accessors."""

from __future__ import annotations


def get_cpu_freq_event_rows(sql_results: dict[str, object]) -> list[dict]:
    return list(sql_results.get("cpu_freq_events", []) or [])


def get_cpu_freq_rows(sql_results: dict[str, object]) -> list[dict]:
    return list(sql_results.get("cpu_freq", []) or [])


def get_gpu_freq_rows(sql_results: dict[str, object]) -> list[dict]:
    return list(sql_results.get("gpu_freq", []) or [])


def get_cpu_capacity_cluster_rows(sql_results: dict[str, object]) -> list[dict]:
    return list(sql_results.get("cpu_capacity_clusters", []) or [])


def get_thread_cpu_scheduling_rows(sql_results: dict[str, object]) -> list[dict]:
    return list(sql_results.get("thread_cpu_scheduling", []) or [])
