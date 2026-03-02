"""App startup performance analyzer."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..base_analyzer import BaseAnalyzer
from ..models import CategoryReport

logger = logging.getLogger(__name__)

# ─── SQL Templates ────────────────────────────────────────────

_SQL_STARTUP_SLICES = """\
SELECT
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    s.depth, s.parent_id, s.id AS slice_id,
    t.name AS thread_name, t.tid,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (s.name LIKE 'activityStart%'
       OR s.name LIKE 'activityResume%'
       OR s.name LIKE 'bindApplication%'
       OR s.name LIKE 'inflate%'
       OR s.name = 'Choreographer#doFrame'
       OR s.name LIKE 'performCreate%'
       OR s.name LIKE 'performStart%'
       OR s.name LIKE 'performResume%'
       OR s.name LIKE 'Application.onCreate%'
       OR s.name LIKE 'Activity.onCreate%'
       OR s.name = 'DrawFrame'
       OR s.name LIKE 'reportFullyDrawn%'
       OR s.name LIKE 'launching:%')
  AND s.dur > 0
ORDER BY s.ts
"""

_SQL_CONTENT_PROVIDER_INIT = """\
SELECT
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    t.name AS thread_name,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (s.name LIKE '%ContentProvider%'
       OR s.name LIKE '%contentprovider%'
       OR s.name LIKE 'provider:%')
  AND s.dur > 0
ORDER BY s.dur DESC LIMIT 50
"""

_SQL_CLASS_LOADING = """\
SELECT
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    t.name AS thread_name,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (s.name LIKE 'OpenDexFilesFromOat%'
       OR s.name LIKE 'LoadClass%'
       OR s.name LIKE 'VerifyClass%'
       OR s.name LIKE 'JIT compiling%')
  AND s.dur > 0
ORDER BY s.dur DESC LIMIT 100
"""

# ─── Prompt ──────────────────────────────────────────────────

_PROMPT_TEMPLATE = """\
你是一个 Android 启动性能分析专家。

## Android 启动阶段
```
Process.start → bindApplication → ContentProvider.onCreate → Application.onCreate
→ Activity.onCreate → Activity.onStart → Activity.onResume → 首帧渲染(Choreographer#doFrame → measure → layout → draw → RenderThread)
→ reportFullyDrawn
```

冷启动预算：
- 优秀: <1s
- 可接受: 1-2s
- 需优化: 2-5s
- 严重: >5s

## 硬件性能上下文
{hardware_context}

## 统计概览
{statistics_json}

## 启动相关 Slice 数据
{startup_slices_json}

## ContentProvider 初始化
{content_provider_json}

## 类加载事件
{class_loading_json}

## 目标应用包名
{package_name}

## 分析策略
1. 识别启动类型（冷启动/热启动/温启动）
2. 拆解各阶段耗时：bindApplication → Application.onCreate → Activity.onCreate → 首帧渲染
3. 定位最慢阶段及原因（Binder 阻塞、锁等待、IO、ContentProvider 初始化慢、类加载多）
4. 结合 CPU 频率判断是否因降频导致启动慢
5. 使用 `query_trace(sql, limit)` 工具深挖（最多 5 次）

## 输出格式（严格 JSON）
```json
{{
  "startup_time_ms": 2350,
  "startup_type": "cold",
  "phase_breakdown": [
    {{"phase": "bindApplication", "dur_ms": 120, "note": "正常"}},
    {{"phase": "Application.onCreate", "dur_ms": 800, "note": "ContentProvider 初始化慢"}},
    {{"phase": "Activity.onCreate", "dur_ms": 600, "note": "inflate 复杂布局"}},
    {{"phase": "首帧渲染", "dur_ms": 350, "note": "正常"}}
  ],
  "bottleneck_phase": "Application.onCreate",
  "bottleneck_reason": "3 个 ContentProvider 串行初始化，总耗时 650ms",
  "hardware_assessment": "基于硬件的评估",
  "evidence_sql": [
    {{
      "label": "ContentProvider 初始化耗时",
      "sql": "SELECT name, dur/1e6 AS dur_ms FROM slice WHERE name LIKE '%ContentProvider%' ORDER BY dur DESC LIMIT 10",
      "conclusion": "FileProvider 初始化耗时 320ms"
    }}
  ],
  "issues": [{{"severity": "high", "description": "...", "category": "startup"}}],
  "suggestions": ["延迟初始化非必要 ContentProvider"],
  "score": 55
}}
```
score 是 0-100 整数，100 表示启动极快
"""

# ─── Analyzer ─────────────────────────────────────────────────

class StartupAnalyzer(BaseAnalyzer):
    """Analyzes app cold/warm/hot startup performance."""

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self._cold_threshold_ms: float = cfg.get("cold_start_threshold_ms", 5000)
        self._warm_threshold_ms: float = cfg.get("warm_start_threshold_ms", 2000)

    @property
    def name(self) -> str:
        return "startup"

    @property
    def sql_templates(self) -> dict[str, str]:
        return {
            "startup_slices": _SQL_STARTUP_SLICES,
            "content_provider_init": _SQL_CONTENT_PROVIDER_INIT,
            "class_loading": _SQL_CLASS_LOADING,
        }

    @property
    def prompt_template(self) -> str:
        return _PROMPT_TEMPLATE

    def analyze(self, tp: Any, llm_client: Any) -> CategoryReport:
        sql_results = self._execute_queries(tp)
        startup_slices = sql_results.get("startup_slices", [])
        if not startup_slices:
            return CategoryReport(
                analyzer_name=self.name,
                status="insufficient_data",
                sql_results=sql_results,
            )

        statistics = self._compute_statistics(sql_results)
        prompt = self._build_startup_prompt(statistics, sql_results)
        llm_response = self._call_llm(llm_client, prompt, tp=tp)
        return self._build_report_from_llm(llm_response, sql_results, statistics)

    def _compute_statistics(self, sql_results: dict[str, Any]) -> dict[str, Any]:
        slices = sql_results.get("startup_slices", [])
        if not slices:
            return {}

        # Find launching slice or estimate from first bindApplication to first doFrame
        launching = [s for s in slices if (s.get("slice_name") or "").startswith("launching:")]
        bind_app = [s for s in slices if "bindApplication" in (s.get("slice_name") or "")]
        first_frame = [s for s in slices if s.get("slice_name") in ("Choreographer#doFrame", "DrawFrame")]

        startup_ms = 0.0
        if launching:
            startup_ms = max(s.get("dur_ms", 0) for s in launching)
        elif bind_app and first_frame:
            start_ts = min(s.get("ts", 0) for s in bind_app)
            end_ts = max(s.get("ts", 0) + s.get("dur", 0) for s in first_frame[:5])
            startup_ms = (end_ts - start_ts) / 1e6

        # Phase durations
        phases: dict[str, float] = {}
        for s in slices:
            name = s.get("slice_name", "")
            dur = s.get("dur_ms", 0)
            if "bindApplication" in name:
                phases["bindApplication"] = phases.get("bindApplication", 0) + dur
            elif "Application.onCreate" in name:
                phases["Application.onCreate"] = phases.get("Application.onCreate", 0) + dur
            elif "performCreate" in name or "Activity.onCreate" in name:
                phases["Activity.onCreate"] = phases.get("Activity.onCreate", 0) + dur
            elif "inflate" in name:
                phases["inflate"] = phases.get("inflate", 0) + dur

        # ContentProvider stats
        cp = sql_results.get("content_provider_init", [])
        cp_total_ms = sum(s.get("dur_ms", 0) for s in cp)

        # Class loading stats
        cl = sql_results.get("class_loading", [])
        cl_total_ms = sum(s.get("dur_ms", 0) for s in cl)

        # CPU freq stats
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
            "estimated_startup_ms": round(startup_ms, 1),
            "phase_durations": {k: round(v, 1) for k, v in phases.items()},
            "content_provider_total_ms": round(cp_total_ms, 1),
            "content_provider_count": len(cp),
            "class_loading_total_ms": round(cl_total_ms, 1),
            "class_loading_count": len(cl),
            "startup_slice_count": len(sql_results.get("startup_slices", [])),
            "cpu_freq_stats": cpu_stats,
        }

    def _build_startup_prompt(
        self, statistics: dict[str, Any], sql_results: dict[str, Any]
    ) -> str:
        hardware_context = _build_hardware_context(statistics)
        return self.prompt_template.format(
            hardware_context=hardware_context,
            statistics_json=json.dumps(statistics, indent=2, ensure_ascii=False, default=str),
            startup_slices_json=json.dumps(
                sql_results.get("startup_slices", [])[:100],
                indent=2, ensure_ascii=False, default=str,
            ),
            content_provider_json=json.dumps(
                sql_results.get("content_provider_init", []),
                indent=2, ensure_ascii=False, default=str,
            ),
            class_loading_json=json.dumps(
                sql_results.get("class_loading", [])[:50],
                indent=2, ensure_ascii=False, default=str,
            ),
            package_name=statistics.get("package_name", "未指定"),
        )


def _build_hardware_context(statistics: dict[str, Any]) -> str:
    """Build hardware context string from CPU freq stats."""
    cpu_stats = statistics.get("cpu_freq_stats", [])
    if not cpu_stats:
        return "CPU 频率数据不可用"
    lines = []
    max_freqs = [c.get("max_freq_mhz", 0) for c in cpu_stats]
    overall_max = max(max_freqs) if max_freqs else 0
    tier = ("旗舰机（≥3GHz）" if overall_max >= 3000
            else "中端机（2-3GHz）" if overall_max >= 2000
            else "低端机（<2GHz）")
    lines.append(f"CPU 核心数: {len(cpu_stats)}，设备档次: {tier}，最高频率 {overall_max:.0f}MHz")
    return "\n".join(lines)
