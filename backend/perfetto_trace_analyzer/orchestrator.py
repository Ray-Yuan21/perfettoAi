"""Analysis orchestrator - coordinates the full analysis pipeline."""

from __future__ import annotations

import logging
import os
from typing import Any

from .config import ConfigManager
from .llm_client import LLMClient
from .models import AnalysisResult, AppConfig, CategoryReport
from .registry import AnalyzerRegistry
from .reporter import ReportGenerator
from .scorer import PerformanceScorer
from .trace_processor import TraceProcessorConnection

logger = logging.getLogger(__name__)


class Orchestrator:
    """Coordinates trace loading, analyzer execution, scoring, and report generation."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.registry = AnalyzerRegistry()
        self.llm_client = LLMClient(config.llm)
        self.scorer = PerformanceScorer(config.scoring)
        self.reporter = ReportGenerator()

        self.registry.auto_discover(config.analyzers)

    def analyze(
        self,
        trace_path: str,
        analyzer_names: list[str] | None = None,
        package_filter: str | None = None,
    ) -> AnalysisResult:
        """Run the full analysis pipeline on a single trace file."""
        tp = TraceProcessorConnection(trace_path)
        tp.load()

        try:
            metadata = tp.get_metadata()
            analyzers = self.registry.get_analyzers(analyzer_names)

            if not analyzers:
                logger.warning("No analyzers to run")
                return AnalysisResult(
                    trace_path=trace_path,
                    metadata=metadata,
                )

            reports: list[CategoryReport] = []
            for analyzer in analyzers:
                try:
                    logger.info("Running analyzer: %s", analyzer.name)
                    report = analyzer.analyze(tp, self.llm_client)
                    reports.append(report)
                except Exception as e:
                    logger.error(
                        "Analyzer %s failed with exception: %s", analyzer.name, e
                    )
                    reports.append(
                        CategoryReport(
                            analyzer_name=analyzer.name,
                            status="error",
                        )
                    )

            score = self.scorer.compute_score(reports)

            return AnalysisResult(
                trace_path=trace_path,
                metadata=metadata,
                category_reports=reports,
                overall_score=score,
            )
        finally:
            tp.close()

    def batch_analyze(
        self,
        trace_paths: list[str],
        analyzer_names: list[str] | None = None,
        package_filter: str | None = None,
    ) -> list[AnalysisResult]:
        """Run analysis on multiple trace files."""
        results: list[AnalysisResult] = []
        for path in trace_paths:
            logger.info("Analyzing: %s", path)
            try:
                result = self.analyze(path, analyzer_names, package_filter)
                results.append(result)
            except Exception as e:
                logger.error("Failed to analyze %s: %s", path, e)
                results.append(
                    AnalysisResult(trace_path=path, metadata={"error": str(e)})
                )
        return results


def scan_trace_files(path: str) -> list[str]:
    """Scan a directory for .perfetto-trace and .pb trace files."""
    valid_extensions = {".perfetto-trace", ".pb"}
    if os.path.isfile(path):
        return [path]

    trace_files: list[str] = []
    for entry in os.listdir(path):
        full = os.path.join(path, entry)
        if os.path.isfile(full):
            _, ext = os.path.splitext(entry)
            if ext in valid_extensions:
                trace_files.append(full)

    trace_files.sort()
    return trace_files
