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
        data = result_to_dict(result)
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
            data=result_to_dict(result),
            json_data=json.dumps(result_to_dict(result), indent=2, ensure_ascii=False, default=str),
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


def result_to_dict(result: AnalysisResult) -> dict[str, Any]:
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
