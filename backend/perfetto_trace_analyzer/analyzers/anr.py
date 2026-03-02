"""ANR (Application Not Responding) detection and analysis module."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..base_analyzer import BaseAnalyzer
from ..models import CategoryReport

logger = logging.getLogger(__name__)

# ─── SQL Templates ────────────────────────────────────────────

_SQL_ANR_EVENTS = """\
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
WHERE (s.name LIKE '%ANR%'
       OR s.name LIKE '%Input dispatching timed out%'
       OR s.name LIKE '%executing service%'
       OR s.name LIKE '%Broadcast of%'
       OR s.name LIKE '%ContentProvider not responding%')
  AND s.dur > 0
ORDER BY s.ts
"""

_SQL_MAIN_THREAD_BLOCKING = """\
SELECT
    ts.ts, ts.dur, ts.dur / 1e6 AS dur_ms,
    ts.state, ts.io_wait,
    t.name AS thread_name, t.tid, t.utid,
    p.name AS process_name, p.upid
FROM thread_state ts
JOIN thread t ON ts.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE t.name = 'main'
  AND ts.state != 'R'
  AND ts.dur > 1000000000
ORDER BY ts.dur DESC LIMIT 50
"""

_SQL_BROADCAST_QUEUE = """\
SELECT
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    t.name AS thread_name,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (s.name LIKE '%BroadcastQueue%'
       OR s.name LIKE '%broadcastReceiveReg%'
       OR s.name LIKE '%Broadcast of%')
  AND s.dur > 1000000
ORDER BY s.dur DESC LIMIT 50
"""

# ─── Prompt ──────────────────────────────────────────────────

_PROMPT_TEMPLATE = """\
你是一个 Android ANR 分析专家。

## ANR 基础知识
ANR (Application Not Responding) 触发条件：
- InputDispatching: 主线程 5s 内未处理输入事件
- BroadcastQueue: 前台广播 10s / 后台广播 60s 未完成
- Service: 前台服务 20s / 后台服务 200s 未完成
- ContentProvider: 发布超时

## 常见 ANR 原因
- 主线程同步 Binder 调用阻塞
- 主线程死锁（synchronized 锁竞争）
- 主线程 IO 操作（数据库、文件、网络）
- CPU 密集计算在主线程
- 系统资源不足（CPU 被其他进程占满）

## 硬件性能上下文
{hardware_context}

## 统计概览
{statistics_json}

## ANR 事件
{anr_events_json}

## 主线程阻塞记录
{main_thread_blocking_json}

## 广播队列
{broadcast_queue_json}

## 目标应用包名
{package_name}

## 分析策略
1. 定位 ANR 时间窗口
2. 分析主线程在 ANR 窗口内的状态（Running/Sleeping/IO Wait/Uninterruptible）
3. 查看主线程调用栈，定位阻塞函数
4. 判断是 Binder 阻塞、死锁、IO、还是 CPU 密集
5. 使用 `query_trace(sql, limit)` 工具深挖（最多 5 次）

## 输出格式（严格 JSON）
```json
{{
  "anr_count": 2,
  "anr_events": [
    {{
      "ts": 1234567890,
      "type": "InputDispatching",
      "process": "com.example.app",
      "blocking_reason": "主线程同步 Binder 调用 IActivityManager.getRunningAppProcesses 耗时 6.2s",
      "blocking_thread_state": "Sleeping",
      "dur_ms": 6200
    }}
  ],
  "blocking_call_tree": "主线程调用栈描述",
  "hardware_assessment": "基于硬件的评估",
  "evidence_sql": [
    {{
      "label": "主线程阻塞状态",
      "sql": "SELECT state, dur/1e6 AS dur_ms FROM thread_state WHERE utid=123 AND ts BETWEEN ... ORDER BY dur DESC LIMIT 10",
      "conclusion": "主线程在 Sleeping 状态 6.2s，等待 Binder 返回"
    }}
  ],
  "issues": [{{"severity": "critical", "description": "...", "category": "anr"}}],
  "suggestions": ["将 Binder 调用移到后台线程"],
  "score": 30
}}
```
score 是 0-100 整数，100 表示无 ANR 风险
"""

# ─── Analyzer ─────────────────────────────────────────────────

class ANRAnalyzer(BaseAnalyzer):
    """Detects and analyzes ANR events and main thread blocking."""

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self._block_threshold_ms: float = cfg.get("main_thread_block_threshold_ms", 5000)

    @property
    def name(self) -> str:
        return "anr"

    @property
    def sql_templates(self) -> dict[str, str]:
        return {
            "anr_events": _SQL_ANR_EVENTS,
            "main_thread_blocking": _SQL_MAIN_THREAD_BLOCKING,
            "broadcast_queue": _SQL_BROADCAST_QUEUE,
        }

    @property
    def prompt_template(self) -> str:
        return _PROMPT_TEMPLATE

    def analyze(self, tp: Any, llm_client: Any) -> CategoryReport:
        sql_results = self._execute_queries(tp)

        anr_events = sql_results.get("anr_events", [])
        blocking = sql_results.get("main_thread_blocking", [])
        if not anr_events and not blocking:
            return CategoryReport(
                analyzer_name=self.name,
                status="insufficient_data",
                sql_results=sql_results,
                statistics={"anr_count": 0, "main_thread_long_blocks": 0},
                score=100,
            )

        statistics = self._compute_statistics(sql_results)
        prompt = self._build_anr_prompt(statistics, sql_results)
        llm_response = self._call_llm(llm_client, prompt, tp=tp)
        return self._build_report_from_llm(llm_response, sql_results, statistics)

    def _compute_statistics(self, sql_results: dict[str, Any]) -> dict[str, Any]:
        anr_events = sql_results.get("anr_events", [])
        blocking = sql_results.get("main_thread_blocking", [])
        broadcast = sql_results.get("broadcast_queue", [])

        # Classify blocking states
        sleeping_blocks = [b for b in blocking if b.get("state") == "S"]
        io_blocks = [b for b in blocking if b.get("state") == "D"]

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
            "anr_count": len(anr_events),
            "main_thread_long_blocks": len(blocking),
            "max_block_ms": round(max((b.get("dur_ms", 0) for b in blocking), default=0), 1),
            "sleeping_blocks": len(sleeping_blocks),
            "io_wait_blocks": len(io_blocks),
            "broadcast_slow_count": len(broadcast),
            "cpu_freq_stats": cpu_stats,
        }

    def _build_anr_prompt(
        self, statistics: dict[str, Any], sql_results: dict[str, Any]
    ) -> str:
        hardware_context = _build_hardware_context(statistics)
        return self.prompt_template.format(
            hardware_context=hardware_context,
            statistics_json=json.dumps(statistics, indent=2, ensure_ascii=False, default=str),
            anr_events_json=json.dumps(
                sql_results.get("anr_events", []),
                indent=2, ensure_ascii=False, default=str,
            ),
            main_thread_blocking_json=json.dumps(
                sql_results.get("main_thread_blocking", []),
                indent=2, ensure_ascii=False, default=str,
            ),
            broadcast_queue_json=json.dumps(
                sql_results.get("broadcast_queue", []),
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
