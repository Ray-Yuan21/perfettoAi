"""Jank-specific presentation helpers."""

from __future__ import annotations

import logging
from typing import Any

from ..models import AnalysisResult

logger = logging.getLogger(__name__)

_JANK_SHORT = {
    "App Deadline Missed": "App超时",
    "SurfaceFlinger CPU Deadline Missed": "SF CPU超时",
    "SurfaceFlinger GPU Deadline Missed": "SF GPU超时",
    "Display HAL": "Display HAL",
    "Buffer Stuffing": "Buffer堆积",
    "Prediction Error": "预测误差",
    "Dropped Frame": "丢帧",
    "Late Present": "延迟呈现",
}


def build_jank_frames(result: AnalysisResult) -> list[dict[str, Any]]:
    """Build jank frame data tailored for the frontend."""
    jank_frames: list[dict[str, Any]] = []

    jank_report = next(
        (report for report in result.category_reports if report.analyzer_name == "jank"),
        None,
    )
    if not jank_report or not jank_report.sql_results:
        return jank_frames

    frames = jank_report.sql_results.get("frame_timeline", [])
    seen_frames: dict[str, dict[str, Any]] = {}

    for frame in frames:
        jank_type = frame.get("jank_type") or "None"
        present_type = frame.get("present_type", "")
        if jank_type == "None" and present_type not in ("Dropped Frame", "Late Present"):
            continue
        if present_type == "On-time Present":
            continue

        overrun_ms = frame.get("overrun_ms", 0) or 0
        actual_dur = frame.get("actual_dur", 0) or 0
        dur_ms = round(actual_dur / 1_000_000, 2) if actual_dur else 0
        if overrun_ms <= 0 and dur_ms <= 0:
            continue
        if overrun_ms <= 0 and jank_type == "Prediction Error":
            continue

        dedup_key = f"{frame.get('display_frame_token')}:{frame.get('upid')}"
        if dedup_key in seen_frames and overrun_ms <= seen_frames[dedup_key].get("overrun_ms", 0):
            continue

        label = jank_type if jank_type != "None" else present_type
        for long_name, short_name in _JANK_SHORT.items():
            if long_name in label:
                label = short_name
                break

        layer_name = frame.get("layer_name", "") or ""
        short_layer = layer_name.split("/")[-1].split("#")[0] if layer_name else ""

        seen_frames[dedup_key] = {
            "ts": frame.get("actual_ts"),
            "dur": actual_dur,
            "dur_ms": dur_ms,
            "overrun_ms": round(overrun_ms, 2),
            "jank_type": label,
            "present_type": present_type,
            "layer_name": short_layer,
            "process_name": frame.get("process_name"),
            "pid": frame.get("pid"),
            "upid": frame.get("upid"),
        }

    jank_frames = list(seen_frames.values())
    jank_frames.sort(
        key=lambda item: item.get("overrun_ms", 0) or item.get("dur_ms", 0),
        reverse=True,
    )

    _attach_statistics_details(jank_frames, jank_report.statistics or {})
    _attach_llm_analyses(jank_frames, frames, jank_report.llm_insights or {})
    return jank_frames


def _attach_statistics_details(
    jank_frames: list[dict[str, Any]],
    statistics: dict[str, Any],
) -> None:
    details = statistics.get("top_jank_frames", [])
    detail_by_ts: dict[int, dict[str, Any]] = {}
    for detail in details:
        ts_key = detail.get("actual_ts")
        if ts_key is not None:
            detail_by_ts[ts_key] = detail

    for frame in jank_frames:
        detail = detail_by_ts.get(frame.get("ts"))
        if not detail:
            continue
        frame["call_tree"] = detail.get("call_tree", [])
        frame["root_cause"] = detail.get("bottleneck", "")
        cpu_sched = detail.get("thread_cpu_scheduling")
        if cpu_sched:
            frame["cpu_scheduling"] = cpu_sched


def _attach_llm_analyses(
    jank_frames: list[dict[str, Any]],
    raw_frames: list[dict[str, Any]],
    llm_insights: dict[str, Any],
) -> None:
    frame_analyses = llm_insights.get("frame_analyses", [])
    logger.debug("frame_analyses count from LLM: %d", len(frame_analyses))
    if not frame_analyses:
        return

    analysis_by_frame_id: dict[int, dict[str, Any]] = {}
    analysis_by_ts: dict[int, dict[str, Any]] = {}
    for analysis in frame_analyses:
        frame_id = analysis.get("frame_id")
        ts = analysis.get("ts")
        if frame_id is not None:
            analysis_by_frame_id[frame_id] = analysis
        if ts is not None:
            analysis_by_ts[ts] = analysis

    frames_by_ts = {
        frame.get("actual_ts"): frame
        for frame in raw_frames
        if frame.get("actual_ts") is not None
    }

    match_count = 0
    for frame in jank_frames:
        ts = frame.get("ts")
        sql_frame = frames_by_ts.get(ts) if ts is not None else None
        frame_id = sql_frame.get("frame_id") if sql_frame else None
        analysis = (
            (analysis_by_frame_id.get(frame_id) if frame_id is not None else None)
            or (analysis_by_ts.get(ts) if ts is not None else None)
        )
        if not analysis:
            continue

        match_count += 1
        frame["analysis"] = {
            "flow_description": analysis.get("flow_description", ""),
            "bottleneck_function": analysis.get("bottleneck_function", ""),
            "bottleneck_reason": analysis.get("bottleneck_reason", ""),
            "root_cause_category": analysis.get("root_cause_category", ""),
            "severity": analysis.get("severity", ""),
            "side": analysis.get("side", ""),
            "evidence_sql": analysis.get("evidence_sql", []),
        }

    logger.debug("jank_frames count: %d, matched: %d", len(jank_frames), match_count)
