"""Thread-safe state manager with optional JSON persistence.

Replaces the global mutable ``_state`` dict in ``server.py`` with a structured,
thread-safe class that can persist analysis results across server restarts.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict
from typing import Any

from .models import AppConfig, AnalysisResult, CategoryReport, PerformanceScore

logger = logging.getLogger(__name__)


class StateManager:
    """Manages trace analysis state with thread-safe access and disk persistence.

    On construction, ``data_dir`` is created (if provided) and any previously
    persisted results are loaded back into memory.
    """

    def __init__(self, data_dir: str | None = None):
        self._lock = threading.Lock()
        self._config: AppConfig | None = None
        self._results: dict[str, AnalysisResult] = {}
        self._trace_paths: dict[str, str] = {}
        self._status: dict[str, str] = {}        # "analyzing" | "done" | "failed"
        self._progress: dict[str, str] = {}

        self._data_dir = data_dir
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
            self._load_persisted()

    # ── Config ────────────────────────────────────────────────

    @property
    def config(self) -> AppConfig | None:
        return self._config

    @config.setter
    def config(self, value: AppConfig) -> None:
        self._config = value

    # ── Results ───────────────────────────────────────────────

    def set_result(self, trace_id: str, result: AnalysisResult, trace_path: str | None = None) -> None:
        with self._lock:
            self._results[trace_id] = result
            if trace_path:
                self._trace_paths[trace_id] = trace_path
            self._status[trace_id] = "done"
            self._progress[trace_id] = "Analysis complete"
        self._persist_result(trace_id, result)

    def get_result(self, trace_id: str) -> AnalysisResult | None:
        with self._lock:
            return self._results.get(trace_id)

    def get_trace_path(self, trace_id: str) -> str | None:
        with self._lock:
            return self._trace_paths.get(trace_id)

    def list_traces(self) -> list[dict[str, Any]]:
        with self._lock:
            traces = []
            for trace_id, result in self._results.items():
                score = result.overall_score.overall if result.overall_score else None
                traces.append({
                    "id": trace_id,
                    "filename": os.path.basename(result.trace_path),
                    "score": score,
                })
        traces.sort(key=lambda item: item["id"], reverse=True)
        return traces

    def has_results(self) -> bool:
        with self._lock:
            return bool(self._results)

    # ── Status / Progress ─────────────────────────────────────

    def set_status(self, trace_id: str, status: str, progress: str = "") -> None:
        with self._lock:
            self._status[trace_id] = status
            if progress:
                self._progress[trace_id] = progress

    def set_progress(self, trace_id: str, progress: str) -> None:
        with self._lock:
            self._progress[trace_id] = progress

    def get_status(self, trace_id: str) -> str | None:
        with self._lock:
            return self._status.get(trace_id)

    def get_progress(self, trace_id: str) -> str:
        with self._lock:
            return self._progress.get(trace_id, "")

    def set_trace_path(self, trace_id: str, path: str) -> None:
        with self._lock:
            self._trace_paths[trace_id] = path

    # ── Persistence ───────────────────────────────────────────

    def _result_file(self, trace_id: str) -> str | None:
        if not self._data_dir:
            return None
        return os.path.join(self._data_dir, f"{trace_id}.json")

    def _persist_result(self, trace_id: str, result: AnalysisResult) -> None:
        path = self._result_file(trace_id)
        if not path:
            return
        try:
            data = {
                "trace_id": trace_id,
                "trace_path": result.trace_path,
                "metadata": result.metadata,
                "overall_score": asdict(result.overall_score) if result.overall_score else None,
                "category_reports": [asdict(r) for r in result.category_reports],
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            logger.debug("Persisted result for %s → %s", trace_id, path)
        except Exception as e:
            logger.warning("Failed to persist result for %s: %s", trace_id, e)

    def _load_persisted(self) -> None:
        """Load previously persisted results from data_dir."""
        if not self._data_dir or not os.path.isdir(self._data_dir):
            return

        count = 0
        for fname in os.listdir(self._data_dir):
            if not fname.endswith(".json"):
                continue
            trace_id = fname[:-5]
            fpath = os.path.join(self._data_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Reconstruct AnalysisResult
                reports = []
                for r in data.get("category_reports", []):
                    reports.append(CategoryReport(**{
                        k: v for k, v in r.items()
                        if k in CategoryReport.__dataclass_fields__
                    }))

                score_data = data.get("overall_score")
                score = None
                if score_data:
                    score = PerformanceScore(**{
                        k: v for k, v in score_data.items()
                        if k in PerformanceScore.__dataclass_fields__
                    })

                result = AnalysisResult(
                    trace_path=data.get("trace_path", ""),
                    metadata=data.get("metadata", {}),
                    category_reports=reports,
                    overall_score=score,
                )
                self._results[trace_id] = result
                trace_path = data.get("trace_path", "")
                if trace_path:
                    self._trace_paths[trace_id] = trace_path
                self._status[trace_id] = "done"
                count += 1
            except Exception as e:
                logger.warning("Failed to load persisted result %s: %s", fname, e)

        if count:
            logger.info("Loaded %d persisted analysis results from %s", count, self._data_dir)
