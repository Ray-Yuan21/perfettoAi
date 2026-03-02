"""Binder IPC slow call analysis module."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..base_analyzer import BaseAnalyzer
from ..models import CategoryReport

logger = logging.getLogger(__name__)

# ─── SQL Templates ────────────────────────────────────────────

_SQL_BINDER_SLOW_CALLS = """\
SELECT
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    s.id AS slice_id, s.depth, s.parent_id,
    t.name AS thread_name, t.tid,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (s.name LIKE 'binder transaction%'
       OR s.name LIKE 'binder reply%'
       OR s.name LIKE 'binder async%')
  AND s.dur > 1000000
ORDER BY s.dur DESC LIMIT 200
"""

_SQL_BINDER_BY_INTERFACE = """\
SELECT
    s.name AS slice_name,
    COUNT(*) AS call_count,
    AVG(s.dur) / 1e6 AS avg_dur_ms,
    MAX(s.dur) / 1e6 AS max_dur_ms,
    SUM(s.dur) / 1e6 AS total_dur_ms,
    p.name AS process_name
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (s.name LIKE 'binder transaction%'
       OR s.name LIKE 'binder reply%'
       OR s.name LIKE 'binder async%')
  AND s.dur > 0
GROUP BY s.name, p.name
ORDER BY total_dur_ms DESC LIMIT 50
"""

_SQL_BINDER_BLOCKING_MAIN = """\
SELECT
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    s.id AS slice_id,
    t.name AS thread_name, t.tid,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE t.name = 'main'
  AND (s.name LIKE 'binder transaction%'
       OR s.name LIKE 'binder reply%')
  AND s.dur > 500000
ORDER BY s.dur DESC LIMIT 100
"""

# ─── Prompt ──────────────────────────────────────────────────

_PROMPT_TEMPLATE = """\
你是一个 Android Binder IPC 性能分析专家。

## Binder 基础知识
- Binder 是 Android 跨进程通信（IPC）机制
- 同步 Binder 调用会阻塞调用线程直到对端返回
- 主线程上的同步 Binder 调用是卡顿和 ANR 的常见原因
- 常见慢 Binder 接口：IActivityManager、IPackageManager、IWindowManager、ISurfaceComposer

## 分析维度
- 哪些 Binder 接口调用最慢
- 主线程同步 Binder 调用是否导致卡顿
- 是 caller 慢（发起方准备数据慢）还是 callee 慢（服务端处理慢）
- Binder 线程池是否饱和

## 硬件性能上下文
{hardware_context}

## 统计概览
{statistics_json}

## 慢 Binder 调用详情
{slow_calls_json}

## 按接口聚合
{by_interface_json}

## 主线程 Binder 阻塞
{blocking_main_json}

## 目标应用包名
{package_name}

## 分析策略
1. 找出最慢的 Binder 接口
2. 分析主线程同步 Binder 调用对用户体验的影响
3. 判断是 caller 慢还是 callee 慢
4. 评估 Binder 调用频率是否过高
5. 使用 `query_trace(sql, limit)` 工具深挖（最多 5 次）

## 输出格式（严格 JSON）
```json
{{
  "slow_binder_count": 15,
  "top_slow_interfaces": [
    {{
      "interface": "binder transaction 12345",
      "avg_dur_ms": 25.3,
      "max_dur_ms": 120.5,
      "call_count": 8,
      "process": "com.example.app"
    }}
  ],
  "main_thread_binder_blocking_ms": 450,
  "main_thread_binder_count": 12,
  "bottleneck_side": "callee",
  "hardware_assessment": "基于硬件的评估",
  "evidence_sql": [
    {{
      "label": "最慢 Binder 调用",
      "sql": "SELECT name, dur/1e6 AS dur_ms FROM slice WHERE name LIKE 'binder%' ORDER BY dur DESC LIMIT 10",
      "conclusion": "IActivityManager.getRunningAppProcesses 最慢，耗时 120ms"
    }}
  ],
  "issues": [{{"severity": "high", "description": "...", "category": "binder"}}],
  "suggestions": ["将 IActivityManager 调用移到后台线程"],
  "score": 60
}}
```
score 是 0-100 整数，100 表示 Binder 使用健康
"""

# ─── Analyzer ─────────────────────────────────────────────────

class BinderAnalyzer(BaseAnalyzer):
    """Analyzes Binder IPC slow calls and cross-process bottlenecks."""

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self._slow_threshold_ms: float = cfg.get("slow_call_threshold_ms", 50)

    @property
    def name(self) -> str:
        return "binder"

    @property
    def sql_templates(self) -> dict[str, str]:
        return {
            "binder_slow_calls": _SQL_BINDER_SLOW_CALLS,
            "binder_by_interface": _SQL_BINDER_BY_INTERFACE,
            "binder_blocking_main": _SQL_BINDER_BLOCKING_MAIN,
        }

    @property
    def prompt_template(self) -> str:
        return _PROMPT_TEMPLATE

    def analyze(self, tp: Any, llm_client: Any) -> CategoryReport:
        sql_results = self._execute_queries(tp)

        slow_calls = sql_results.get("binder_slow_calls", [])
        blocking_main = sql_results.get("binder_blocking_main", [])
        if not slow_calls and not blocking_main:
            return CategoryReport(
                analyzer_name=self.name,
                status="insufficient_data",
                sql_results=sql_results,
                statistics={"slow_binder_count": 0, "main_thread_binder_count": 0},
                score=100,
            )

        statistics = self._compute_statistics(sql_results)
        prompt = self._build_binder_prompt(statistics, sql_results)
        llm_response = self._call_llm(llm_client, prompt, tp=tp)
        return self._build_report_from_llm(llm_response, sql_results, statistics)

    def _compute_statistics(self, sql_results: dict[str, Any]) -> dict[str, Any]:
        slow_calls = sql_results.get("binder_slow_calls", [])
        by_interface = sql_results.get("binder_by_interface", [])
        blocking_main = sql_results.get("binder_blocking_main", [])

        main_blocking_total_ms = sum(b.get("dur_ms", 0) for b in blocking_main)

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
            "slow_binder_count": len(slow_calls),
            "max_binder_dur_ms": round(max((s.get("dur_ms", 0) for s in slow_calls), default=0), 1),
            "interface_count": len(by_interface),
            "main_thread_binder_count": len(blocking_main),
            "main_thread_binder_blocking_ms": round(main_blocking_total_ms, 1),
            "cpu_freq_stats": cpu_stats,
        }

    def _build_binder_prompt(
        self, statistics: dict[str, Any], sql_results: dict[str, Any]
    ) -> str:
        hardware_context = _build_hardware_context(statistics)
        return self.prompt_template.format(
            hardware_context=hardware_context,
            statistics_json=json.dumps(statistics, indent=2, ensure_ascii=False, default=str),
            slow_calls_json=json.dumps(
                sql_results.get("binder_slow_calls", [])[:50],
                indent=2, ensure_ascii=False, default=str,
            ),
            by_interface_json=json.dumps(
                sql_results.get("binder_by_interface", []),
                indent=2, ensure_ascii=False, default=str,
            ),
            blocking_main_json=json.dumps(
                sql_results.get("binder_blocking_main", [])[:50],
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
