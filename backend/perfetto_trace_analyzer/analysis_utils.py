"""Shared analysis utilities used by multiple analyzers.

Contains common building blocks:
- Call tree construction from flat slice lists
- Call tree text rendering for LLM prompts
- Thread state summarization
- CPU frequency/scheduling summarization
- Hardware context generation
"""

from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from typing import Any

logger = logging.getLogger(__name__)


# ─── Bisect-based event lookup ────────────────────────────────


def prepare_sorted_index(
    events: list[dict], ts_key: str = "ts",
) -> tuple[list[dict], list[int]]:
    """Sort events by ts_key and return (sorted_events, ts_array) for bisect."""
    sorted_events = sorted(events, key=lambda e: e.get(ts_key, 0))
    ts_array = [e.get(ts_key, 0) for e in sorted_events]
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
    """Find events within a time window using bisect.

    overlap=True:  s_ts < f_end AND s_end > f_ts  (catches cross-boundary slices)
    overlap=False: f_ts <= s_ts <= f_end            (containment)
    """
    if not sorted_events:
        return []

    if overlap:
        right = bisect_left(ts_array, f_end)
        candidates = []
        for i in range(right):
            e = sorted_events[i]
            s_ts = e.get("ts", 0)
            s_end = s_ts + (e.get("dur", 0) or 0)
            if s_end > f_ts:
                if upid is None or e.get(upid_key) == upid:
                    candidates.append(e)
        return candidates
    else:
        left = bisect_left(ts_array, f_ts)
        right = bisect_right(ts_array, f_end)
        if upid is None:
            return sorted_events[left:right]
        return [e for e in sorted_events[left:right] if e.get(upid_key) == upid]


# ─── Call Tree ────────────────────────────────────────────────


def build_call_tree(
    slices: list[dict],
    min_dur_ms: float = 0.5,
    max_children: int = 6,
    max_roots: int = 5,
) -> list[dict]:
    """Build a nested call tree from flat slice list using parent_id.

    Args:
        slices: Flat list of slice dicts with slice_id, parent_id, dur_ms, etc.
        min_dur_ms: Minimum duration to keep a node.
        max_children: Max children per node after pruning.
        max_roots: Max root nodes to return.
    """
    if not slices:
        return []
    by_id: dict[int, dict] = {}
    for s in slices:
        sid = s.get("slice_id")
        if sid is None:
            continue
        by_id[sid] = {
            "id": sid, "name": s.get("slice_name", ""),
            "dur_ms": round(s.get("dur_ms", 0), 2),
            "ts": s.get("ts"), "dur": s.get("dur", 0),
            "depth": s.get("depth", 0),
            "thread": s.get("thread_name", ""),
            "parent_id": s.get("parent_id"),
            "children": [], "self_ms": 0.0,
        }
    roots: list[dict] = []
    for node in by_id.values():
        pid = node["parent_id"]
        if pid is not None and pid in by_id:
            by_id[pid]["children"].append(node)
        else:
            roots.append(node)

    def _self_time(node: dict) -> None:
        node["self_ms"] = round(max(0.0, node["dur_ms"] - sum(c["dur_ms"] for c in node["children"])), 2)
        for c in node["children"]:
            _self_time(c)

    def _prune(node: dict) -> dict | None:
        if node["dur_ms"] < min_dur_ms:
            return None
        children = sorted(
            [pc for c in node["children"] for pc in [_prune(c)] if pc],
            key=lambda c: c["dur_ms"], reverse=True
        )[:max_children]
        node["children"] = children
        return node

    def _clean(node: dict) -> dict:
        return {
            "name": node["name"], "dur_ms": node["dur_ms"],
            "self_ms": node["self_ms"], "thread": node["thread"],
            "ts": node.get("ts"), "dur": node.get("dur", 0),
            "children": [_clean(c) for c in node["children"]],
        }

    # Ensure each thread has at least one root node
    thread_roots: dict[str, list[dict]] = {}
    for root in roots:
        _self_time(root)
        pr = _prune(root)
        if pr:
            t = pr.get("thread", "")
            thread_roots.setdefault(t, []).append(pr)

    # Pick the best root per thread, then fill remaining slots by dur
    selected: list[dict] = []
    remaining: list[dict] = []
    for t, t_roots in thread_roots.items():
        t_roots.sort(key=lambda r: r["dur_ms"], reverse=True)
        selected.append(t_roots[0])
        remaining.extend(t_roots[1:])

    remaining.sort(key=lambda r: r["dur_ms"], reverse=True)
    need = max_roots - len(selected)
    if need > 0:
        selected.extend(remaining[:need])

    selected.sort(key=lambda r: r["dur_ms"], reverse=True)
    return [_clean(r) for r in selected[:max_roots]]


def call_tree_to_text(call_tree: list[dict]) -> str:
    """Convert nested call tree to indented text for LLM prompts."""
    lines: list[str] = []

    def _render(nodes: list[dict], prefix: str = "", is_root: bool = True) -> None:
        for i, node in enumerate(nodes):
            is_last = i == len(nodes) - 1
            connector = "" if (is_root and not prefix) else ("└── " if is_last else "├── ")
            child_prefix = "" if (is_root and not prefix) else ("    " if is_last else "│   ")
            parts = [f"{prefix}{connector}{node['name']}  {node['dur_ms']}ms"]
            if node.get("self_ms", 0) > 0.5:
                parts.append(f"(self: {node['self_ms']}ms)")
            if node.get("thread") and node["thread"] not in ("main",):
                parts.append(f"[{node['thread']}]")
            if node.get("self_ms", 0) > 5.0 and not node.get("children"):
                parts.append("← 瓶颈")
            lines.append(" ".join(parts))
            if node.get("children"):
                _render(node["children"], prefix + child_prefix, is_root=False)

    _render(call_tree)
    return "\n".join(lines)


# ─── Thread State Summarization ───────────────────────────────


def summarize_thread_states(
    f_ts: int, f_end: int, f_upid: int | None,
    sorted_thread_states: list[dict] | None = None,
    ts_array: list[int] | None = None,
) -> list[dict]:
    """Summarize thread scheduling states during a time window."""
    if not sorted_thread_states or not ts_array:
        return []

    relevant = find_overlapping_events(
        sorted_thread_states, ts_array, f_ts, f_end, upid=f_upid, overlap=True,
    )
    if not relevant:
        return []

    # Aggregate by thread + state
    agg: dict[str, float] = {}
    for s in relevant:
        thread = s.get("thread_name", "")
        state = s.get("state", "?")
        io_wait = s.get("io_wait")
        label = f"{thread}:{state}"
        if io_wait:
            label += ":io"
        overlap_start = max(s.get("ts", 0), f_ts)
        overlap_end = min(s.get("ts", 0) + (s.get("dur", 0) or 0), f_end)
        dur_ms = max(0, (overlap_end - overlap_start)) / 1e6
        agg[label] = agg.get(label, 0.0) + dur_ms

    result = []
    for label, dur_ms in sorted(agg.items(), key=lambda x: x[1], reverse=True):
        if dur_ms < 0.5:
            continue
        parts = label.split(":")
        result.append({
            "thread": parts[0],
            "state": parts[1] if len(parts) > 1 else "?",
            "io_wait": len(parts) > 2,
            "dur_ms": round(dur_ms, 2),
        })
    return result[:20]


# ─── CPU Frequency Summarization ─────────────────────────────


def summarize_cpu_freq(
    f_ts: int, f_end: int,
    sorted_cpu_freq: list[dict] | None = None,
    ts_array: list[int] | None = None,
) -> dict[str, Any]:
    """Summarize CPU frequency events during a time window."""
    if not sorted_cpu_freq or not ts_array:
        return {}

    relevant = find_overlapping_events(
        sorted_cpu_freq, ts_array, f_ts, f_end, overlap=False,
    )
    if not relevant:
        return {}

    by_cpu: dict[int, list[float]] = {}
    for e in relevant:
        cpu = e.get("cpu", 0)
        freq = e.get("freq_khz", 0)
        by_cpu.setdefault(cpu, []).append(freq)

    result: dict[str, Any] = {}
    for cpu, freqs in sorted(by_cpu.items()):
        result[f"cpu{cpu}_avg_mhz"] = round(sum(freqs) / len(freqs) / 1000, 0)
        result[f"cpu{cpu}_min_mhz"] = round(min(freqs) / 1000, 0)
        result[f"cpu{cpu}_max_mhz"] = round(max(freqs) / 1000, 0)
    return result


def summarize_sched_slices(
    f_ts: int, f_end: int, f_upid: int | None,
    sorted_sched: list[dict] | None = None,
    ts_array: list[int] | None = None,
) -> dict[str, float]:
    """Aggregate total ms spent on each CPU core by threads in a time window."""
    if not sorted_sched or not ts_array:
        return {}

    relevant = find_overlapping_events(
        sorted_sched, ts_array, f_ts, f_end, upid=f_upid, overlap=True,
    )

    agg: dict[str, float] = {}
    for s in relevant:
        overlap_start = max(s.get("ts", 0), f_ts)
        overlap_end = min(s.get("ts", 0) + s.get("dur", 0), f_end)
        cpu = s.get("cpu", "unknown")
        agg[str(cpu)] = agg.get(str(cpu), 0.0) + (overlap_end - overlap_start) / 1e6
    return {f"CPU_{k}": round(v, 2) for k, v in agg.items() if v > 0.5}


# ─── Hardware Context ─────────────────────────────────────────


def build_hardware_context(statistics: dict[str, Any]) -> str:
    """Build hardware context string from CPU freq stats for LLM prompts."""
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
            max_freqs = [c.get("max_freq_mhz", 0) for c in cpu_stats]
            overall_max = max(max_freqs) if max_freqs else 0
        tier = ("旗舰机（≥3GHz）" if overall_max >= 3000
                else "中端机（2-3GHz）" if overall_max >= 2000
                else "低端机（<2GHz）")
        lines.append(f"\n【综合设备档次】: {tier}，最高频率 {overall_max:.0f}MHz")

        # Throttling detection
        big_cores = [c for c in cpu_stats if c.get("max_freq_mhz", 0) >= overall_max * 0.99]
        if big_cores and overall_max > 0:
            avg_big = sum(c.get("avg_freq_mhz", 0) for c in big_cores) / len(big_cores)
            if avg_big / overall_max < 0.6:
                lines.append(f"⚠️ 疑似散热限频：大核均频 {avg_big:.0f}MHz，仅为最高频率的 {avg_big/overall_max*100:.0f}%")

    return "\n".join(lines)
