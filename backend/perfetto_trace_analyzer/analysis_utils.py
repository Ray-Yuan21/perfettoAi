"""Shared analysis utilities used by multiple analyzers.

This module keeps call tree helpers local and re-exports compatibility wrappers
for the analyzer-facing diagnostics/ layer.
"""

from __future__ import annotations

from typing import Any

from .diagnostics import (
    describe_hardware_context as build_hardware_context,
    summarize_cpu_freq_window,
    summarize_sched_slices_window,
    summarize_thread_states_window,
)
from .signals.windowing import find_overlapping_events, prepare_sorted_index


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
    """Compatibility wrapper for windowed thread-state summaries."""
    return summarize_thread_states_window(
        f_ts,
        f_end,
        f_upid,
        sorted_thread_states=sorted_thread_states,
        ts_array=ts_array,
    )


# ─── CPU Frequency Summarization ─────────────────────────────


def summarize_cpu_freq(
    f_ts: int, f_end: int,
    sorted_cpu_freq: list[dict] | None = None,
    ts_array: list[int] | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for windowed CPU-frequency summaries."""
    return summarize_cpu_freq_window(
        f_ts,
        f_end,
        sorted_cpu_freq=sorted_cpu_freq,
        ts_array=ts_array,
    )


def summarize_sched_slices(
    f_ts: int, f_end: int, f_upid: int | None,
    sorted_sched: list[dict] | None = None,
    ts_array: list[int] | None = None,
) -> dict[str, float]:
    """Compatibility wrapper for windowed scheduling summaries."""
    return summarize_sched_slices_window(
        f_ts,
        f_end,
        f_upid,
        sorted_sched=sorted_sched,
        ts_array=ts_array,
    )
