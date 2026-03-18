"""App startup performance analyzer."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..base_analyzer import BaseAnalyzer
from ..models import CategoryReport
from ..analysis_utils import (
    build_call_tree as _build_call_tree,
    build_hardware_context as _build_hardware_context,
)

logger = logging.getLogger(__name__)

# ─── SQL Templates ────────────────────────────────────────────

_SQL_STARTUP_BOUNDARY = """\
SELECT
    MIN(s.ts) AS start_ts,
    MAX(s.ts + s.dur) AS end_ts,
    p.name AS process_name, p.upid, t.utid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (s.name LIKE 'bindApplication%'
       OR s.name = 'Choreographer#doFrame'
       OR s.name LIKE 'launching:%'
       OR s.name = 'DrawFrame'
       OR s.name LIKE 'reportFullyDrawn%')
  AND s.dur > 0
  AND t.is_main_thread = 1
GROUP BY p.upid, t.utid
ORDER BY (MAX(s.ts + s.dur) - MIN(s.ts)) DESC
LIMIT 1
"""

_SQL_STARTUP_CALL_STACK = """\
SELECT
    s.id AS slice_id,
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    s.depth, s.parent_id,
    t.name AS thread_name, t.tid, t.utid,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE tt.utid = {utid}
  AND s.ts >= {start_ts}
  AND s.ts <= {end_ts}
  AND s.dur > 500000 -- 只关注 0.5ms 以上的函数
ORDER BY s.ts
"""

# 这些沿用 base_analyzer 的 common SQL，只需要传递 utid 和 start/end 即可

# ─── Prompt ──────────────────────────────────────────────────

_PROMPT_TEMPLATE = """\
你是一个 Android 极致启动性能优化专家。

## App 启动本质
冷启动从 `Process.start` 开始，经历 `bindApplication` (初始化 Application) → `ContentProvider.onCreate` → `Activity.onCreate/onStart/onResume` → `Choreographer#doFrame` (首帧测绘) 直至交送 GPU。
任何在这个时间窗内（主线程）的重体力代码、系统挂起、锁等待、或者被抢占资源，都会成倍放大启动时间！

## 硬件性能上下文
{hardware_context}

## 宏观耗时与环境 (Phase Breakdown)
{statistics_json}

## 发动机：启动期主线程极深调用树 (Call Tree)
下面是提取自 Perfetto 的主线程调用树（已经过滤掉细碎方法，保留了最耗时的骨干代码）。
【注意】：self_ms 是这个函数自己花掉的时间（排除了子函数的耗时）。如果 self_ms 极大，说明这行代码本身就是运算瓶颈！
```json
{startup_tree_json}
```

## 挂起侦探：锁竞争记录 (Monitor Contention)
{monitor_contention_json}

## 环境侦探：线程真实调度 (CPU 状态)
{thread_state_summary_json}

## 核心侦探：CPU 实际分配情况 (是否被系统降级)
{cpu_scheduling_json}

## 跨进程侦探：启动期 Binder 调用
{binder_transactions_json}

## 分析策略
1. **树根穿透**：观察上面的 Call Tree。找到哪一个节点的 `dur_ms` 和 `self_ms` 不成比例的大？是 `inflate` (XML 解析太庞大)、`installProvider` (流氓 SDK 在初始化)、还是 `measure` 阶段卡死？
2. **状态印证**：如果主线程 `Sleeping` 在总耗时占比过高，立刻去查【锁竞争记录】或者【Binder 记录】。如果 `Runnable` 过高，或者【CPU 实际分配情况】显示主线程极长时间只运行在弱算力的【小核】上，指责后台抢占算力或系统温控限频(Thermal Throttling)！
3. **精准指认**：不要泛泛而谈“优化 Application”，直接指出“是某个 Provider 耗时 500ms（由 Call Tree 某节点证实）”或“是被后台的 `pool-1-thread` 锁住了”。
4. 使用 `query_trace(sql, limit)` 工具深挖（需要时最多 5 次）

## 输出格式（严格 JSON）
```json
{{
  "startup_time_ms": 2350,
  "startup_type": "cold",
  "bottleneck_node": "ActivityThread.handleBindApplication",
  "bottleneck_reason": "主线程被名为 `RxCachedThreadScheduler` 的后台线程锁死长达 850ms，并且有长达 300ms 的 FileProvider IO 阻塞。",
  "flow_description": "启动耗时近 2.3s。第一阶段 bindApplication 耗时 1.1s（严重超时），追踪 Call Tree 发现 installProvider 占据大头。结合线程状态，主线程处于 Sleeping 长达 850ms，且锁竞争表实锤了主线程(tid=xx) 被 RxJava 线程池死死卡住。第二阶段抵达首帧时，由于首页 XML 层级过深，inflate 又是 450ms（被 self_ms 证明）。",
  "hardware_assessment": "基于硬件的评估",
  "evidence_sql": [
    {{
      "label": "证明锁竞争",
      "sql": "SELECT ...",
      "conclusion": "确实被 Rx 锁住"
    }}
  ],
  "issues": [{{"severity": "critical", "description": "...", "category": "startup"}}],
  "suggestions": ["将 RxJava 初始化改为 lazy", "用 AsyncLayoutInflater"],
  "score": 55
}}
```
score 是 0-100 整数，100 表示启动没有任何非必要阻塞，完美压榨了 CPU。
bottleneck_node 请直接写 CallTree 里对应的那个具体的 slice name。
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
    def prompt_template(self) -> str:
        return _PROMPT_TEMPLATE

    @property
    def sql_templates(self) -> dict[str, str]:
        return {
            "startup_boundary": _SQL_STARTUP_BOUNDARY,
            # class_loading and cp are nice to have, we keep them around but focus on tree
            "content_provider_init": _SQL_CONTENT_PROVIDER_INIT,
            "class_loading": _SQL_CLASS_LOADING,
        }

    def analyze(self, tp: Any, llm_client: Any) -> CategoryReport:
        # Step 1: Execute base queries (boundary and legacy flat ones)
        sql_results = self._execute_queries(tp)
        boundary = sql_results.get("startup_boundary", [])
        
        if not boundary:
            return CategoryReport(
                analyzer_name=self.name,
                status="insufficient_data",
                sql_results=sql_results,
            )
            
        b = boundary[0]
        start_ts = b.get("start_ts", 0)
        end_ts = b.get("end_ts", 0)
        utid = b.get("utid", 0)
        dur = end_ts - start_ts
        
        if dur <= 0:
             return CategoryReport(
                analyzer_name=self.name,
                status="insufficient_data",
                sql_results=sql_results,
            )

        # Step 2: Query the deep diagnostics (Call Tree, Thread State, Monitor Contention, Binder)
        tree_query = _SQL_STARTUP_CALL_STACK.format(utid=utid, start_ts=start_ts, end_ts=end_ts)
        try:
            tree_it = tp.query(tree_query)
            col_names = tree_it.column_names
            call_stack_rows = [{col: getattr(row, col) for col in col_names} for row in tree_it]
        except Exception as e:
            logger.error(f"Failed to query startup call stack: {e}")
            call_stack_rows = []
            
        sql_results["startup_call_stack"] = call_stack_rows
        
        # Pull Common SQLs from base_analyzer (using self.COMMON_SQL_TEMPLATES)
        from ..base_analyzer import COMMON_SQL_TEMPLATES
        
        diagnostics_queries = {
            "monitor_contention": COMMON_SQL_TEMPLATES["monitor_contention"],
            "thread_state": COMMON_SQL_TEMPLATES["thread_state"],
            "binder_transactions": COMMON_SQL_TEMPLATES["binder_transactions"], # Used the wrong key previously
            "thread_cpu_scheduling": COMMON_SQL_TEMPLATES["thread_cpu_scheduling"],
        }
        
        for k, q_template in diagnostics_queries.items():
            q = q_template.format(utid=utid, start_ts=start_ts, end_ts=end_ts)
            try:
                it = tp.query(q)
                cols = it.column_names
                sql_results[k] = [{col: getattr(row, col) for col in cols} for row in it]
            except Exception as e:
                logger.warning(f"Failed deep diagnostic query {k}: {e}")
                sql_results[k] = []

        statistics = self._compute_statistics(sql_results, start_ts, dur)
        statistics["package_name"] = b.get("process_name", "Unknown")
        
        prompt = self._build_startup_prompt(statistics, sql_results)
        llm_response = self._call_llm(llm_client, prompt, tp=tp)
        return self._build_report_from_llm(llm_response, sql_results, statistics)

    def _compute_statistics(self, sql_results: dict[str, Any], start_ts: int, dur: int) -> dict[str, Any]:
        startup_ms = dur / 1e6

        # Phase durations (Optional legacy calculation)
        cp = sql_results.get("content_provider_init", [])
        cp_total_ms = sum(s.get("dur_ms", 0) for s in cp)

        cl = sql_results.get("class_loading", [])
        cl_total_ms = sum(s.get("dur_ms", 0) for s in cl)

        # Thread State Aggregation (CPU true cost)
        states = sql_results.get("thread_state", [])
        state_aggs: dict[str, float] = {}
        for row in states:
             s = row.get("state", "Unknown")
             dur_ms = row.get("dur_ms", 0)
             state_aggs[s] = state_aggs.get(s, 0.0) + dur_ms

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
            
        cpu_clusters = sql_results.get("cpu_capacity_clusters", [])

        # Sched Slices grouping map
        sched = sql_results.get("thread_cpu_scheduling", [])
        sched_aggs: dict[str, float] = {}
        for r in sched:
            cpu = r.get("cpu", "unk")
            dur = r.get("dur_ms", 0.0)
            key = f"CPU_{cpu}"
            sched_aggs[key] = sched_aggs.get(key, 0.0) + dur

        return {
            "estimated_startup_ms": round(startup_ms, 1),
            "content_provider_total_ms": round(cp_total_ms, 1),
            "class_loading_total_ms": round(cl_total_ms, 1),
            "cpu_freq_stats": cpu_stats,
            "cpu_capacity_clusters": cpu_clusters,
            "thread_state_aggs": {k: round(v, 1) for k, v in state_aggs.items()},
            "thread_cpu_scheduling_aggs": {k: round(v, 1) for k, v in sched_aggs.items() if v > 1.0},
        }
        # debug logging after dict is constructed won't work, so log before return
        # (moved logging to _build_startup_prompt instead)

    def _build_startup_prompt(
        self, statistics: dict[str, Any], sql_results: dict[str, Any]
    ) -> str:
        hardware_context = _build_hardware_context(statistics)

        logger.debug("cpu_capacity_clusters: %s", statistics.get('cpu_capacity_clusters', [])[:3])
        logger.debug("thread_cpu_scheduling_aggs: %s", statistics.get('thread_cpu_scheduling_aggs', {}))
        logger.debug("hardware_context:\n%s", hardware_context)
        
        # Build Call Tree
        raw_stack = sql_results.get("startup_call_stack", [])
        # Provide depth limit similar to Jank tree (top 20 branches per node, 0.5ms filter already applied via SQL)
        call_tree = _build_call_tree(raw_stack, min_dur_ms=1.0, max_children=15)
        
        return self.prompt_template.format(
            hardware_context=hardware_context,
            statistics_json=json.dumps(statistics, indent=2, ensure_ascii=False, default=str),
            startup_tree_json=json.dumps(
                call_tree, indent=2, ensure_ascii=False, default=str
            ),
            monitor_contention_json=json.dumps(
                sql_results.get("monitor_contention", [])[:20], indent=2, ensure_ascii=False, default=str
            ),
            thread_state_summary_json=json.dumps(
                sql_results.get("thread_state", [])[:30], indent=2, ensure_ascii=False, default=str
            ),
            cpu_scheduling_json=json.dumps(
                statistics.get("thread_cpu_scheduling_aggs", {}), indent=2, ensure_ascii=False, default=str
            ),
            binder_transactions_json=json.dumps(
                sql_results.get("binder_transactions", [])[:20], indent=2, ensure_ascii=False, default=str
            ),
        )

