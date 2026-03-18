"""Frame-timing diagnostic capabilities used by jank analysis."""

from __future__ import annotations

from typing import Any


def atrace_frames_to_unified(
    draw_frames: list[dict],
    traversal_frames: list[dict],
    target_ms: float,
) -> list[dict]:
    """Convert atrace draw/traversal slices to frame-timeline-like rows."""
    primary = draw_frames if draw_frames else traversal_frames

    result = []
    for frame in primary:
        dur_ms = frame.get("dur_ms", 0) or (frame.get("actual_dur", 0) / 1e6)
        overrun = max(0.0, dur_ms - target_ms)
        result.append(
            {
                "frame_id": frame.get("frame_id"),
                "actual_ts": frame.get("actual_ts", frame.get("ts", 0)),
                "actual_dur": frame.get("actual_dur", frame.get("dur", 0)),
                "overrun_ms": round(overrun, 2),
                "jank_type": "App Deadline Missed" if dur_ms > target_ms else "None",
                "present_type": "Late Present" if dur_ms > target_ms else "On-time Present",
                "layer_name": frame.get("slice_name", ""),
                "process_name": frame.get("process_name", ""),
                "upid": frame.get("upid"),
                "pid": frame.get("pid"),
            }
        )
    return result


def get_unified_frame_timeline(
    sql_results: dict[str, Any],
    target_frame_time_ms: float,
) -> list[dict]:
    """Return a normalized frame timeline, falling back to atrace data when needed."""
    frames = sql_results.get("frame_timeline", [])
    if frames:
        return frames

    draw_frames = sql_results.get("atrace_draw_frames", [])
    traversal_frames = sql_results.get("atrace_traversal_frames", [])
    if not draw_frames and not traversal_frames:
        return []

    frames = atrace_frames_to_unified(draw_frames, traversal_frames, target_frame_time_ms)
    sql_results["frame_timeline"] = frames
    sql_results["jank_frame_call_stack"] = sql_results.get("atrace_call_stack", [])
    return frames


def is_real_jank(frame: dict) -> bool:
    """Classify whether a frame should count as jank."""
    jank_type = frame.get("jank_type") or "None"
    present_type = frame.get("present_type") or ""

    if present_type == "Dropped Frame":
        return True
    if present_type == "Late Present":
        return True
    if present_type == "On-time Present":
        return False
    return jank_type != "None"


def jank_severity(frame: dict) -> str:
    """Classify jank severity from present type and overrun."""
    present_type = frame.get("present_type") or ""
    overrun = frame.get("overrun_ms", 0)
    if present_type == "Dropped Frame":
        return "high"
    if overrun > 16.67:
        return "high"
    if present_type == "Late Present":
        return "medium"
    return "low"


def percentile(sorted_values: list[float], p: float) -> float:
    """Compute the p-th percentile from a sorted list."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    k = (p / 100) * (n - 1)
    f = int(k)
    c = f + 1
    if c >= n:
        return sorted_values[-1]
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


def classify_jank_frames(durations_ms: list[float], target_frame_time_ms: float) -> list[bool]:
    """Classify each duration as jank based on a target frame time."""
    return [dur > target_frame_time_ms for dur in durations_ms]


def detect_severe_jank(jank_flags: list[bool], min_consecutive: int) -> list[dict[str, Any]]:
    """Find runs of consecutive jank frames."""
    events: list[dict[str, Any]] = []
    i, n = 0, len(jank_flags)
    while i < n:
        if jank_flags[i]:
            start = i
            while i < n and jank_flags[i]:
                i += 1
            length = i - start
            if length >= min_consecutive:
                events.append(
                    {
                        "start_index": start,
                        "end_index": i - 1,
                        "consecutive_frames": length,
                    }
                )
        else:
            i += 1
    return events


def summarize_frame_timeline(
    sql_results: dict[str, Any],
    target_frame_time_ms: float,
    severe_consecutive: int,
) -> dict[str, Any]:
    """Build stable frame-level statistics from frame timeline SQL results."""
    frames = sql_results.get("frame_timeline", [])
    if not frames:
        return {}

    total = len(frames)
    jank_count = sum(1 for frame in frames if is_real_jank(frame))
    jank_rate = jank_count / total if total else 0.0
    overruns = [frame.get("overrun_ms", 0) for frame in frames if (frame.get("overrun_ms") or 0) > 0]
    sorted_overruns = sorted(overruns)

    app_frames = [frame for frame in frames if "surfaceflinger" not in (frame.get("process_name") or "").lower()]
    sf_frames = [frame for frame in frames if "surfaceflinger" in (frame.get("process_name") or "").lower()]

    stats: dict[str, Any] = {
        "total_frames": total,
        "jank_frames": jank_count,
        "jank_rate_pct": round(jank_rate * 100, 2),
        "target_frame_time_ms": target_frame_time_ms,
        "max_overrun_ms": round(max(sorted_overruns), 2) if sorted_overruns else 0,
        "p95_overrun_ms": round(percentile(sorted_overruns, 95), 2) if sorted_overruns else 0,
        "app_total_frames": len(app_frames),
        "app_jank_frames": sum(1 for frame in app_frames if is_real_jank(frame)),
        "sf_total_frames": len(sf_frames),
        "sf_jank_frames": sum(1 for frame in sf_frames if is_real_jank(frame)),
    }

    if stats["app_total_frames"] > 0:
        stats["app_jank_rate_pct"] = round(stats["app_jank_frames"] / stats["app_total_frames"] * 100, 2)
    if stats["sf_total_frames"] > 0:
        stats["sf_jank_rate_pct"] = round(stats["sf_jank_frames"] / stats["sf_total_frames"] * 100, 2)

    present_map = {
        row.get("present_type", ""): row.get("cnt", 0)
        for row in sql_results.get("present_type_stats", [])
    }
    stats["dropped_frames"] = present_map.get("Dropped Frame", 0)
    stats["late_present_frames"] = present_map.get("Late Present", 0)

    frame_durs = sorted(
        frame.get("actual_dur", 0) / 1e6 for frame in frames if (frame.get("actual_dur") or 0) > 0
    )
    stats["p95_frame_time_ms"] = round(percentile(frame_durs, 95), 2) if frame_durs else 0
    stats["max_frame_time_ms"] = round(max(frame_durs), 2) if frame_durs else 0

    jank_flags = [is_real_jank(frame) for frame in frames]
    stats["severe_jank_events"] = len(detect_severe_jank(jank_flags, severe_consecutive))

    slow_renders = sql_results.get("slow_renders", [])
    if slow_renders:
        durs = [row.get("dur_ms", 0) for row in slow_renders if row.get("dur_ms")]
        stats["slow_render_p95_ms"] = round(percentile(sorted(durs), 95), 2) if durs else 0
        stats["slow_render_max_ms"] = round(max(durs), 2) if durs else 0

    return stats
