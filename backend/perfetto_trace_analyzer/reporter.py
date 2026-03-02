"""Report generation in JSON and HTML formats."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .models import AnalysisResult

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")


class ReportGenerator:
    """Generates analysis reports in JSON and HTML formats."""

    def generate_json(self, result: AnalysisResult, output_path: str) -> None:
        """Write analysis result as JSON."""
        data = _result_to_dict(result)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        logger.info("JSON report written to %s", output_path)

    def generate_html(self, result: AnalysisResult, output_path: str) -> None:
        """Write analysis result as HTML with visual dashboard."""
        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(_TEMPLATE_DIR),
            autoescape=True,
        )
        template = env.get_template("report.html.j2")

        html = template.render(
            result=result,
            data=_result_to_dict(result),
            json_data=json.dumps(_result_to_dict(result), indent=2, ensure_ascii=False, default=str),
        )

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("HTML report written to %s", output_path)

    def generate_comparison(
        self,
        results: list[AnalysisResult],
        output_path: str,
        fmt: str = "json",
    ) -> None:
        """Generate a comparison report for multiple trace analyses."""
        comparison = {
            "type": "comparison",
            "traces": [],
        }
        for r in results:
            entry: dict[str, Any] = {
                "trace_path": r.trace_path,
                "metadata": r.metadata,
            }
            if r.overall_score:
                entry["overall_score"] = r.overall_score.overall
                entry["category_scores"] = r.overall_score.category_scores
            comparison["traces"].append(entry)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(comparison, f, indent=2, ensure_ascii=False, default=str)
        logger.info("Comparison report written to %s", output_path)


def _result_to_dict(result: AnalysisResult) -> dict[str, Any]:
    """Convert AnalysisResult to a JSON-serializable dict."""
    data: dict[str, Any] = {
        "metadata": result.metadata,
        "category_reports": [],
    }

    if result.overall_score:
        data["overall_score"] = {
            "score": result.overall_score.overall,
            "category_scores": result.overall_score.category_scores,
            "weights_used": result.overall_score.weights_used,
        }
        data["ranked_issues"] = result.overall_score.ranked_issues

    for report in result.category_reports:
        data["category_reports"].append({
            "analyzer_name": report.analyzer_name,
            "status": report.status,
            "statistics": report.statistics,
            "llm_insights": report.llm_insights,
            "llm_raw_response": report.llm_raw_response,
            "issues": report.issues,
            "suggestions": report.suggestions,
            "score": report.score,
        })

    return data


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


def extract_jank_frames(result: AnalysisResult) -> list[dict[str, Any]]:
    """Extract, process, and enrich jank frames from an AnalysisResult.

    Combines SQL frame_timeline data with call tree context and LLM frame analyses.
    """
    jank_frames: list[dict[str, Any]] = []

    jank_report = None
    for report in result.category_reports:
        if report.analyzer_name == "jank":
            jank_report = report
            break

    if not jank_report or not jank_report.sql_results:
        return jank_frames

    frames = jank_report.sql_results.get("frame_timeline", [])

    # Deduplicate: same display_frame_token + upid = same frame on different layers
    # Keep the record with highest overrun
    seen_frames: dict[str, dict] = {}

    for f in frames:
        jt = f.get("jank_type") or "None"
        pt = f.get("present_type", "")
        # Skip non-jank frames
        if jt == "None" and pt not in ("Dropped Frame", "Late Present"):
            continue
        # On-time present → not real jank (OEM interpolation can compensate)
        if pt == "On-time Present":
            continue

        overrun = f.get("overrun_ms", 0) or 0
        actual_dur = f.get("actual_dur", 0) or 0
        dur_ms = round(actual_dur / 1_000_000, 2) if actual_dur else 0

        if overrun <= 0 and dur_ms <= 0:
            continue
        # Skip Prediction Error with no overrun (not actionable)
        if overrun <= 0 and jt == "Prediction Error":
            continue

        # Dedup key: same frame token + same process
        token = f.get("display_frame_token")
        upid = f.get("upid")
        dedup_key = f"{token}:{upid}"

        if dedup_key in seen_frames:
            # Keep the one with higher overrun
            if overrun <= seen_frames[dedup_key].get("overrun_ms", 0):
                continue

        # Simplify jank_type labels
        jank_label = jt
        if jt == "None" and pt:
            jank_label = pt
        for key, val in _JANK_SHORT.items():
            if key in jank_label:
                jank_label = val
                break

        # Simplify layer_name: keep only last segment
        layer = f.get("layer_name", "") or ""
        short_layer = layer.split("/")[-1].split("#")[0] if layer else ""

        entry = {
            "ts": f.get("actual_ts"),
            "dur": actual_dur,
            "dur_ms": dur_ms,
            "overrun_ms": round(overrun, 2),
            "jank_type": jank_label,
            "present_type": pt,
            "layer_name": short_layer,
            "process_name": f.get("process_name"),
            "pid": f.get("pid"),
            "upid": f.get("upid"),
        }
        seen_frames[dedup_key] = entry

    jank_frames = list(seen_frames.values())

    # Sort by severity
    jank_frames.sort(
        key=lambda x: x.get("overrun_ms", 0) or x.get("dur_ms", 0),
        reverse=True,
    )

    # Attach call tree details from statistics
    if jank_report.statistics:
        details = jank_report.statistics.get("top_jank_frames", [])
        detail_by_ts: dict[int, dict] = {}
        for d in details:
            ts_key = d.get("actual_ts")
            if ts_key is not None:
                detail_by_ts[ts_key] = d

        for jf in jank_frames:
            detail = detail_by_ts.get(jf.get("ts"))
            if detail:
                jf["call_tree"] = detail.get("call_tree", [])
                jf["root_cause"] = detail.get("bottleneck", "")

    # Merge LLM frame_analyses if available
    if jank_report.llm_insights:
        frame_analyses = jank_report.llm_insights.get("frame_analyses", [])
        if frame_analyses:
            # Build lookup by frame_id and ts for LLM frame analyses
            analysis_by_frame_id: dict[int, dict] = {}
            analysis_by_ts: dict[int, dict] = {}
            for fa in frame_analyses:
                fa_fid = fa.get("frame_id")
                fa_ts = fa.get("ts")
                if fa_fid is not None:
                    analysis_by_frame_id[fa_fid] = fa
                if fa_ts is not None:
                    analysis_by_ts[fa_ts] = fa

            # Build frame_id lookup from SQL data
            frames_by_ts: dict[int, dict] = {}
            for f in frames:
                ts_key = f.get("actual_ts")
                if ts_key is not None:
                    frames_by_ts[ts_key] = f

            for jf in jank_frames:
                jf_ts = jf.get("ts")
                # Try frame_id match first (more reliable), then ts match
                sql_frame = frames_by_ts.get(jf_ts) if jf_ts else None
                frame_id = sql_frame.get("frame_id") if sql_frame else None
                fa = (
                    (analysis_by_frame_id.get(frame_id) if frame_id is not None else None)
                    or (analysis_by_ts.get(jf_ts) if jf_ts is not None else None)
                )
                if fa:
                    jf["analysis"] = {
                        "flow_description": fa.get("flow_description", ""),
                        "bottleneck_function": fa.get("bottleneck_function", ""),
                        "bottleneck_reason": fa.get("bottleneck_reason", ""),
                        "root_cause_category": fa.get("root_cause_category", ""),
                        "severity": fa.get("severity", ""),
                        "side": fa.get("side", ""),
                        "evidence_sql": fa.get("evidence_sql", []),
                    }

    return jank_frames
