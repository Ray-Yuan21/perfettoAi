"""CPU- and GPU-related metric builders."""

from __future__ import annotations

from typing import Any

from ..signals.cpu import (
    get_cpu_capacity_cluster_rows,
    get_cpu_freq_rows,
    get_gpu_freq_rows,
)
from ..signals.windowing import find_overlapping_events


def build_cpu_frequency_stats(sql_results: dict[str, object]) -> list[dict[str, Any]]:
    rows = get_cpu_freq_rows(sql_results)
    if not rows:
        return []
    return [
        {
            "cpu": row.get("cpu"),
            "avg_freq_mhz": round((row.get("avg_freq_khz") or 0) / 1000, 0),
            "max_freq_mhz": round((row.get("max_freq_khz") or 0) / 1000, 0),
            "min_freq_mhz": round((row.get("min_freq_khz") or 0) / 1000, 0),
        }
        for row in rows
    ]


def build_gpu_frequency_stats(sql_results: dict[str, object]) -> dict[str, Any] | None:
    rows = get_gpu_freq_rows(sql_results)
    if not rows:
        return None
    row = rows[0]
    if (row.get("max_freq_hz") or 0) <= 0:
        return None
    return {
        "avg_freq_mhz": round((row.get("avg_freq_hz") or 0) / 1000, 1),
        "max_freq_mhz": round((row.get("max_freq_hz") or 0) / 1000, 1),
    }


def build_cpu_capacity_clusters(sql_results: dict[str, object]) -> list[dict[str, Any]]:
    return get_cpu_capacity_cluster_rows(sql_results)


def summarize_cpu_freq_window(
    f_ts: int,
    f_end: int,
    sorted_cpu_freq: list[dict] | None = None,
    ts_array: list[int] | None = None,
) -> dict[str, Any]:
    """Summarize CPU frequency events inside a window."""
    if not sorted_cpu_freq or not ts_array:
        return {}

    relevant = find_overlapping_events(
        sorted_cpu_freq,
        ts_array,
        f_ts,
        f_end,
        overlap=False,
    )
    if not relevant:
        return {}

    by_cpu: dict[int, list[float]] = {}
    for event in relevant:
        cpu = event.get("cpu", 0)
        freq = event.get("freq_khz", 0)
        by_cpu.setdefault(cpu, []).append(freq)

    summary: dict[str, Any] = {}
    for cpu, freqs in sorted(by_cpu.items()):
        summary[f"cpu{cpu}_avg_mhz"] = round(sum(freqs) / len(freqs) / 1000, 0)
        summary[f"cpu{cpu}_min_mhz"] = round(min(freqs) / 1000, 0)
        summary[f"cpu{cpu}_max_mhz"] = round(max(freqs) / 1000, 0)
    return summary


def build_hardware_context(statistics: dict[str, Any]) -> str:
    """Build a stable hardware summary string for prompts."""
    lines: list[str] = []
    cpu_stats = statistics.get("cpu_freq_stats", [])
    cpu_clusters = statistics.get("cpu_capacity_clusters", [])

    overall_max = 0
    if cpu_clusters:
        lines.append("【CPU 集群拓扑设计 (核心算力)】")
        for cluster in cpu_clusters:
            cpu_num = cluster.get("cpu", 0)
            max_khz = cluster.get("max_freq_khz") or 0
            mhz = int(max_khz / 1000)
            overall_max = max(overall_max, mhz)
            tier_name = "小核 (Little)"
            if mhz > 2700:
                tier_name = "超大核 (Prime)"
            elif mhz > 2000:
                tier_name = "大核 (Big)"
            lines.append(f"- CPU {cpu_num}: 最高 {mhz} MHz [{tier_name}]")

    if cpu_stats:
        if overall_max == 0:
            max_freqs = [cpu.get("max_freq_mhz", 0) for cpu in cpu_stats]
            overall_max = max(max_freqs) if max_freqs else 0
        tier = (
            "旗舰机（≥3GHz）"
            if overall_max >= 3000
            else "中端机（2-3GHz）"
            if overall_max >= 2000
            else "低端机（<2GHz）"
        )
        lines.append(f"\n【综合设备档次】: {tier}，最高频率 {overall_max:.0f}MHz")

        big_cores = [cpu for cpu in cpu_stats if cpu.get("max_freq_mhz", 0) >= overall_max * 0.99]
        if big_cores and overall_max > 0:
            avg_big = sum(cpu.get("avg_freq_mhz", 0) for cpu in big_cores) / len(big_cores)
            if avg_big / overall_max < 0.6:
                lines.append(
                    f"⚠️ 疑似散热限频：大核均频 {avg_big:.0f}MHz，仅为最高频率的 {avg_big / overall_max * 100:.0f}%"
                )

    if lines:
        return "\n".join(lines)
    return "CPU 频率数据不可用"
