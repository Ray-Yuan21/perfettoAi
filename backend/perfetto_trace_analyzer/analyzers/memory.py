"""Memory usage analysis module."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..base_analyzer import BaseAnalyzer
from ..models import CategoryReport

logger = logging.getLogger(__name__)

# ─── SQL Templates ────────────────────────────────────────────

_SQL_MEMORY_TREND = """\
SELECT
    c.ts,
    c.value,
    ct.name AS counter_name,
    p.name AS process_name, p.upid
FROM counter c
JOIN process_counter_track ct ON c.track_id = ct.id
JOIN process p ON ct.upid = p.upid
WHERE ct.name IN ('mem.rss', 'mem.rss.anon', 'mem.rss.file', 'mem.swap', 'oom_score_adj')
ORDER BY p.upid, ct.name, c.ts
"""

_SQL_LARGE_ALLOCATIONS = """\
SELECT
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    t.name AS thread_name,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (s.name LIKE '%alloc%'
       OR s.name LIKE '%mmap%'
       OR s.name LIKE '%HeapTaskDaemon%'
       OR s.name LIKE '%AllocSpace%')
  AND s.dur > 1000000
ORDER BY s.dur DESC LIMIT 100
"""

_SQL_GC_SUMMARY = """\
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
ORDER BY s.dur DESC LIMIT 200
"""

# ─── Prompt ──────────────────────────────────────────────────

_PROMPT_TEMPLATE = """\
你是一个 Android 内存性能分析专家。

## 内存分析要点
- RSS (Resident Set Size): 进程实际占用物理内存
- PSS (Proportional Set Size): 按共享比例分摊的内存
- GC (Garbage Collection): STW (Stop-The-World) 暂停会导致帧卡顿
- OOM 阈值: 通常 256-512MB，接近时系统会 kill 进程

## 内存泄漏信号
- RSS 持续单调增长（无回落）
- GC 频率随时间增加但内存不降
- oom_score_adj 持续升高

## 硬件性能上下文
{hardware_context}

## 统计概览
{statistics_json}

## 内存趋势数据
{memory_trend_json}

## GC 事件
{gc_summary_json}

## 大内存分配
{large_alloc_json}

## 目标应用包名
{package_name}

## 分析策略
1. 判断内存趋势（increasing/stable/decreasing）
2. 计算 GC 频率和 STW 暂停总时间
3. 评估 GC 对帧率的影响（STW > 5ms 会导致丢帧）
4. 检查内存峰值是否接近 OOM 阈值
5. 使用 `query_trace(sql, limit)` 工具深挖（最多 5 次）

## 输出格式（严格 JSON）
```json
{{
  "peak_rss_mb": 320,
  "avg_rss_mb": 280,
  "gc_count": 45,
  "gc_stw_total_ms": 120,
  "gc_avg_pause_ms": 2.7,
  "memory_trend": "increasing",
  "leak_suspected": true,
  "leak_evidence": "RSS 从 200MB 持续增长到 320MB，无回落",
  "hardware_assessment": "基于硬件的评估",
  "evidence_sql": [
    {{
      "label": "内存增长趋势",
      "sql": "SELECT ts, value FROM counter JOIN process_counter_track ON ... WHERE name='mem.rss' ORDER BY ts",
      "conclusion": "RSS 在 30s 内从 200MB 增长到 320MB"
    }}
  ],
  "issues": [{{"severity": "high", "description": "...", "category": "memory"}}],
  "suggestions": ["使用 LeakCanary 检测泄漏源"],
  "score": 40
}}
```
memory_trend 只能是 "increasing" | "stable" | "decreasing"
score 是 0-100 整数，100 表示内存使用健康
"""

# ─── Analyzer ─────────────────────────────────────────────────

class MemoryAnalyzer(BaseAnalyzer):
    """Analyzes memory usage trends, GC pressure, and potential leaks."""

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self._gc_pressure_threshold: float = cfg.get("gc_pressure_threshold_per_sec", 2)

    @property
    def name(self) -> str:
        return "memory"

    @property
    def sql_templates(self) -> dict[str, str]:
        return {
            "memory_trend": _SQL_MEMORY_TREND,
            "large_allocations": _SQL_LARGE_ALLOCATIONS,
            "gc_summary": _SQL_GC_SUMMARY,
        }

    @property
    def prompt_template(self) -> str:
        return _PROMPT_TEMPLATE

    def analyze(self, tp: Any, llm_client: Any) -> CategoryReport:
        sql_results = self._execute_queries(tp)

        memory_data = sql_results.get("memory_trend", [])
        gc_data = sql_results.get("gc_summary", [])
        if not memory_data and not gc_data:
            return CategoryReport(
                analyzer_name=self.name,
                status="insufficient_data",
                sql_results=sql_results,
            )

        statistics = self._compute_statistics(sql_results)
        prompt = self._build_memory_prompt(statistics, sql_results)
        llm_response = self._call_llm(llm_client, prompt, tp=tp)
        return self._build_report_from_llm(llm_response, sql_results, statistics)

    def _compute_statistics(self, sql_results: dict[str, Any]) -> dict[str, Any]:
        memory_data = sql_results.get("memory_trend", [])
        gc_data = sql_results.get("gc_summary", [])

        # RSS stats per process
        rss_values: dict[int, list[float]] = {}
        for m in memory_data:
            if m.get("counter_name") == "mem.rss":
                upid = m.get("upid", 0)
                val_mb = (m.get("value") or 0) / (1024 * 1024)
                rss_values.setdefault(upid, []).append(val_mb)

        peak_rss = 0.0
        avg_rss = 0.0
        if rss_values:
            all_rss = [v for vs in rss_values.values() for v in vs]
            peak_rss = max(all_rss) if all_rss else 0
            avg_rss = sum(all_rss) / len(all_rss) if all_rss else 0

        # GC stats
        gc_count = len(gc_data)
        gc_total_ms = sum(g.get("dur_ms", 0) for g in gc_data)
        gc_max_ms = max((g.get("dur_ms", 0) for g in gc_data), default=0)

        # Trace duration for GC rate
        if gc_data and len(gc_data) >= 2:
            first_ts = min(g.get("ts", 0) for g in gc_data)
            last_ts = max(g.get("ts", 0) for g in gc_data)
            trace_dur_s = (last_ts - first_ts) / 1e9
            gc_per_sec = gc_count / trace_dur_s if trace_dur_s > 0 else 0
        else:
            gc_per_sec = 0

        # Memory trend detection
        trend = "stable"
        if rss_values:
            # Use the process with most data points
            main_upid = max(rss_values, key=lambda k: len(rss_values[k]))
            vals = rss_values[main_upid]
            if len(vals) >= 3:
                first_third = sum(vals[:len(vals)//3]) / (len(vals)//3)
                last_third = sum(vals[-len(vals)//3:]) / (len(vals)//3)
                change_pct = (last_third - first_third) / first_third * 100 if first_third > 0 else 0
                if change_pct > 10:
                    trend = "increasing"
                elif change_pct < -10:
                    trend = "decreasing"

        cpu_freq_rows = sql_results.get("cpu_freq", [])
        cpu_stats = []
        if cpu_freq_rows:
            cpu_stats = [
                {
                    "cpu": r.get("cpu"),
                    "avg_freq_mhz": round((r.get("avg_freq_khz") or 0) / 1000, 0),
                    "max_freq_mhz": round((r.get("max_freq_khz") or 0) / 1000, 0),
                }
                for r in cpu_freq_rows
            ]

        return {
            "peak_rss_mb": round(peak_rss, 1),
            "avg_rss_mb": round(avg_rss, 1),
            "gc_count": gc_count,
            "gc_stw_total_ms": round(gc_total_ms, 1),
            "gc_max_pause_ms": round(gc_max_ms, 1),
            "gc_per_second": round(gc_per_sec, 2),
            "memory_trend": trend,
            "large_allocation_count": len(sql_results.get("large_allocations", [])),
            "cpu_freq_stats": cpu_stats,
        }

    def _build_memory_prompt(
        self, statistics: dict[str, Any], sql_results: dict[str, Any]
    ) -> str:
        hardware_context = _build_hardware_context(statistics)
        return self.prompt_template.format(
            hardware_context=hardware_context,
            statistics_json=json.dumps(statistics, indent=2, ensure_ascii=False, default=str),
            memory_trend_json=json.dumps(
                sql_results.get("memory_trend", [])[:100],
                indent=2, ensure_ascii=False, default=str,
            ),
            gc_summary_json=json.dumps(
                sql_results.get("gc_summary", [])[:50],
                indent=2, ensure_ascii=False, default=str,
            ),
            large_alloc_json=json.dumps(
                sql_results.get("large_allocations", [])[:50],
                indent=2, ensure_ascii=False, default=str,
            ),
            package_name=statistics.get("package_name", "未指定"),
        )


def _build_hardware_context(statistics: dict[str, Any]) -> str:
    cpu_stats = statistics.get("cpu_freq_stats", [])
    if not cpu_stats:
        return "CPU 频率数据不可用"
    max_freqs = [c.get("max_freq_mhz", 0) for c in cpu_stats]
    overall_max = max(max_freqs) if max_freqs else 0
    tier = ("旗舰机（≥3GHz）" if overall_max >= 3000
            else "中端机（2-3GHz）" if overall_max >= 2000
            else "低端机（<2GHz）")
    return f"CPU 核心数: {len(cpu_stats)}，设备档次: {tier}，最高频率 {overall_max:.0f}MHz"
