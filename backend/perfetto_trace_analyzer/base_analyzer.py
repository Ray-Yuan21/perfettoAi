"""Base class for pluggable analysis modules."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from .models import CategoryReport, LLMResponse

logger = logging.getLogger(__name__)

# ─── Common SQL queries shared by all analyzers ──────────────

COMMON_SQL_TEMPLATES: dict[str, str] = {
    # CPU frequency events (per-core frequency changes)
    "cpu_freq_events": """\
SELECT c.ts, c.value AS freq_khz, ct.cpu
FROM counter c
JOIN cpu_counter_track ct ON c.track_id = ct.id
WHERE ct.name = 'cpufreq'
ORDER BY c.ts
""",
    # Global CPU frequency stats (per-core avg/max/min)
    "cpu_freq": """\
SELECT cpu,
    AVG(value) AS avg_freq_khz,
    MAX(value) AS max_freq_khz,
    MIN(value) AS min_freq_khz
FROM counter
JOIN cpu_counter_track ON counter.track_id = cpu_counter_track.id
WHERE cpu_counter_track.name = 'cpufreq'
GROUP BY cpu ORDER BY cpu
""",
    # GPU frequency stats
    "gpu_freq": """\
SELECT
    AVG(value) AS avg_freq_hz,
    MAX(value) AS max_freq_hz
FROM counter
JOIN gpu_counter_track ON counter.track_id = gpu_counter_track.id
WHERE gpu_counter_track.name LIKE '%freq%' OR gpu_counter_track.name LIKE '%Freq%'
""",
    # Thread scheduling states (Running / Runnable / Sleeping / IO wait)
    "thread_state": """\
SELECT
    ts.ts, ts.dur, ts.dur / 1e6 AS dur_ms,
    ts.state, ts.io_wait,
    t.name AS thread_name, t.tid, t.utid,
    p.name AS process_name, p.upid
FROM thread_state ts
JOIN thread t ON ts.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (t.name IN ('main', 'RenderThread', 'surfaceflinger') OR t.name LIKE 'Binder:%')
  AND ts.dur > 500000
ORDER BY ts.ts
""",
    # Binder transactions (cross-process IPC)
    "binder_transactions": """\
SELECT
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    t.name AS thread_name, t.tid,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (s.name LIKE 'binder transaction%'
       OR s.name LIKE 'binder reply%'
       OR s.name LIKE 'binder async%')
  AND s.dur > 500000
ORDER BY s.dur DESC LIMIT 200
""",
    # GC events (Stop-The-World and concurrent)
    "gc_events": """\
SELECT
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    t.name AS thread_name,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE s.name LIKE '%GC%'
  AND s.dur > 0
ORDER BY s.dur DESC LIMIT 100
""",
    # GPU render slices (eglSwapBuffers, dequeueBuffer, GPU completion)
    "gpu_render_slices": """\
SELECT
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    t.name AS thread_name,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (s.name LIKE 'eglSwapBuffers%'
       OR s.name LIKE 'dequeueBuffer%'
       OR s.name LIKE 'queueBuffer%'
       OR s.name LIKE 'GPU completion%'
       OR s.name LIKE 'Texture upload%')
  AND s.dur > 500000
ORDER BY s.dur DESC LIMIT 200
""",
    # Memory counters (RSS/PSS per process)
    "memory_counters": """\
SELECT
    c.ts, c.value,
    ct.name AS counter_name,
    p.name AS process_name, p.upid
FROM counter c
JOIN process_counter_track ct ON c.track_id = ct.id
JOIN process p ON ct.upid = p.upid
WHERE ct.name IN ('mem.rss', 'mem.rss.anon', 'mem.rss.file',
                   'mem.swap', 'oom_score_adj')
ORDER BY c.ts
""",
    # CPU Hardware Topology / Capacity Clusters (Identifies Big/Little cores)
    "cpu_capacity_clusters": """\
SELECT
    cpu,
    MAX(value) AS max_freq_khz
FROM counter c
JOIN cpu_counter_track ct ON c.track_id = ct.id
WHERE ct.name = 'cpufreq'
GROUP BY cpu
ORDER BY max_freq_khz ASC
""",
    # Thread CPU Scheduling details (Which core did it run on, and at what frequency?)
    "thread_cpu_scheduling": """\
SELECT 
    s.ts, s.dur, s.dur / 1e6 AS dur_ms, 
    s.cpu, t.utid, t.name AS thread_name, p.upid
FROM sched_slice s
JOIN thread t ON s.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (t.name IN ('main', 'RenderThread', 'surfaceflinger') OR t.name LIKE 'Binder:%')
  AND s.dur > 100000
ORDER BY s.ts
""",
}

class BaseAnalyzer(ABC):
    """Abstract base class for all analyzer modules."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name of this analyzer."""
        ...

    @property
    @abstractmethod
    def sql_templates(self) -> dict[str, str]:
        """Pre-defined SQL query templates. Key=query name, value=SQL statement."""
        ...

    @property
    @abstractmethod
    def prompt_template(self) -> str:
        """LLM prompt template for this analysis category."""
        ...

    @abstractmethod
    def analyze(self, tp: Any, llm_client: Any) -> CategoryReport:
        """Execute full analysis: SQL queries -> statistics -> LLM interpretation."""
        ...

    def _execute_queries(self, tp: Any) -> dict[str, Any]:
        """Execute all pre-defined SQL queries (common + analyzer-specific) and return results."""
        all_templates = {**COMMON_SQL_TEMPLATES, **self.sql_templates}
        results: dict[str, Any] = {}
        for query_name, sql in all_templates.items():
            try:
                result = tp.query(sql)
                results[query_name] = result
            except Exception as e:
                logger.warning("SQL query '%s' failed in %s: %s", query_name, self.name, e)
                results[query_name] = []
        return results

    def _compute_statistics(self, sql_results: dict[str, Any]) -> dict[str, Any]:
        """Compute basic statistics from SQL results. Subclasses should override."""
        return {}

    def _build_llm_prompt(
        self,
        statistics: dict[str, Any],
        sql_results: dict[str, Any],
        thresholds: dict[str, Any] | None = None,
        package_name: str | None = None,
    ) -> str:
        """Combine prompt template with data for LLM analysis."""
        import json

        return self.prompt_template.format(
            category_name=self.name,
            statistics_json=json.dumps(statistics, indent=2, ensure_ascii=False, default=str),
            sql_results_json=json.dumps(
                _truncate_for_llm(sql_results), indent=2, ensure_ascii=False, default=str
            ),
            thresholds_json=json.dumps(thresholds or {}, indent=2, ensure_ascii=False),
            package_name=package_name or "未指定",
        )

    def _call_llm(
        self,
        llm_client: Any,
        prompt: str,
        tp: Any = None,
    ) -> LLMResponse:
        """Call LLM with optional agentic tool loop.

        If tp is provided, uses analyze_with_tools() so the LLM can call
        query_trace() to investigate the trace further. Falls back to simple
        analyze() when tp is None (e.g. in unit tests with mock LLM clients).
        """
        if tp is not None and hasattr(llm_client, "analyze_with_tools"):
            from perfetto_trace_analyzer.tools import registry
            bound = registry.with_context(tp)
            response = llm_client.analyze_with_tools(prompt, bound)
        else:
            response = llm_client.analyze(prompt)

        # First try a repair pass that forces the raw answer back into JSON.
        if response.success and response.parsed_data is None:
            logger.info("LLM response parse failed for %s, attempting JSON repair...", self.name)
            if hasattr(llm_client, "repair_json") and response.raw_text:
                repaired = llm_client.repair_json(prompt, response.raw_text)
                if repaired.parsed_data is not None:
                    return repaired

        # Retry once on parse failure (Property 12)
        if response.success and response.parsed_data is None:
            logger.info("LLM response parse failed for %s, retrying...", self.name)
            if tp is not None and hasattr(llm_client, "analyze_with_tools"):
                from perfetto_trace_analyzer.tools import registry
                bound = registry.with_context(tp)
                response = llm_client.analyze_with_tools(prompt, bound)
            else:
                response = llm_client.analyze(prompt)

            if response.success and response.parsed_data is None:
                logger.info("LLM retry parse failed for %s, attempting final JSON repair...", self.name)
                if hasattr(llm_client, "repair_json") and response.raw_text:
                    repaired = llm_client.repair_json(prompt, response.raw_text)
                    if repaired.parsed_data is not None:
                        return repaired

        return response

    def _build_report_from_llm(
        self,
        llm_response: LLMResponse,
        sql_results: dict[str, Any],
        statistics: dict[str, Any],
    ) -> CategoryReport:
        """Build CategoryReport from LLM response."""
        if not llm_response.success:
            return CategoryReport(
                analyzer_name=self.name,
                status="llm_error",
                sql_results=sql_results,
                statistics=statistics,
                llm_raw_response=llm_response.raw_text if llm_response.raw_text else None,
            )

        if llm_response.parsed_data is None:
            return CategoryReport(
                analyzer_name=self.name,
                status="parse_error",
                sql_results=sql_results,
                statistics=statistics,
                llm_raw_response=llm_response.raw_text,
            )

        data = llm_response.parsed_data
        return CategoryReport(
            analyzer_name=self.name,
            status="success",
            sql_results=sql_results,
            statistics=statistics,
            llm_insights=data,
            llm_raw_response=llm_response.raw_text,
            issues=data.get("issues", []),
            suggestions=data.get("suggestions", []),
            score=data.get("score"),
        )


def _truncate_for_llm(data: dict[str, Any], max_rows: int = 200) -> dict[str, Any]:
    """Truncate large SQL result sets to avoid exceeding LLM context limits."""
    truncated = {}
    for key, value in data.items():
        if isinstance(value, list) and len(value) > max_rows:
            truncated[key] = value[:max_rows]
            truncated[f"{key}_truncated_note"] = (
                f"Showing {max_rows} of {len(value)} rows"
            )
        else:
            truncated[key] = value
    return truncated
