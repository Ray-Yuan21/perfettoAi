"""Jank (frame drop) detection and analysis module.

This module provides JankAnalyzer which detects and analyzes frame jank
in Android traces.  SQL templates, prompt template, and helper functions
have been split into separate files for clarity:

- jank_sql.py     — all Perfetto SQL queries
- jank_prompt.py  — LLM prompt template
- jank_helpers.py — frame classification, enrichment, bottleneck detection
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ...base_analyzer import BaseAnalyzer
from ...diagnostics import (
    build_top_jank_frames,
    describe_hardware_context as _describe_hardware_context,
    get_unified_frame_timeline,
    get_cpu_capacity_clusters,
    get_cpu_frequency_stats,
    get_gpu_frequency_stats,
    summarize_frame_timeline,
)
from ...models import CategoryReport
from ...analysis_utils import (
    call_tree_to_text as _call_tree_to_text,
)

from .sql_templates import (
    SQL_ATRACE_DRAW_FRAMES,
    SQL_ATRACE_TRAVERSAL_FRAMES,
    SQL_ATRACE_CALL_STACK,
    SQL_FRAME_TIMELINE,
    SQL_JANK_TYPE_STATS,
    SQL_PRESENT_TYPE_STATS,
    SQL_JANK_BY_PROCESS,
    SQL_SLOW_RENDERS,
    SQL_JANK_FRAME_CALL_STACK,
    SQL_MONITOR_CONTENTION,
    SQL_BINDER_FLOW,
)
from .prompt import PROMPT_TEMPLATE
from .frame_analysis import (
    materialize_sql_results,
    is_real_jank,
    percentile,
)

# Backward-compatible aliases (used by tests and external imports)
_is_real_jank = is_real_jank
_percentile = percentile

logger = logging.getLogger(__name__)

_DEFAULT_TARGET_FRAME_TIME_MS = 16.67
_DEFAULT_SEVERE_JANK_CONSECUTIVE = 3


class JankAnalyzer(BaseAnalyzer):
    """Detects and analyzes frame jank (App + SurfaceFlinger)."""

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self._target_frame_time_ms: float = cfg.get("target_frame_time_ms", _DEFAULT_TARGET_FRAME_TIME_MS)
        self._severe_consecutive: int = cfg.get("severe_jank_consecutive_frames", _DEFAULT_SEVERE_JANK_CONSECUTIVE)
        self._top_jank_frames: int = cfg.get("top_jank_frames", 20)

    @property
    def name(self) -> str:
        return "jank"

    @property
    def sql_templates(self) -> dict[str, str]:
        return {
            "frame_timeline": SQL_FRAME_TIMELINE,
            "jank_type_stats": SQL_JANK_TYPE_STATS,
            "present_type_stats": SQL_PRESENT_TYPE_STATS,
            "jank_by_process": SQL_JANK_BY_PROCESS,
            "slow_renders": SQL_SLOW_RENDERS,
            "jank_frame_call_stack": SQL_JANK_FRAME_CALL_STACK,
            "atrace_draw_frames": SQL_ATRACE_DRAW_FRAMES,
            "atrace_traversal_frames": SQL_ATRACE_TRAVERSAL_FRAMES,
            "atrace_call_stack": SQL_ATRACE_CALL_STACK,
            "monitor_contention": SQL_MONITOR_CONTENTION,
            "binder_flow": SQL_BINDER_FLOW,
        }

    @property
    def prompt_template(self) -> str:
        return PROMPT_TEMPLATE

    def analyze(self, tp: Any, llm_client: Any) -> CategoryReport:
        sql_results = self._execute_queries(tp)
        materialize_sql_results(sql_results)

        had_perfetto_frames = bool(sql_results.get("frame_timeline"))
        frames = get_unified_frame_timeline(sql_results, self._target_frame_time_ms)
        if frames and not had_perfetto_frames and sql_results.get("atrace_call_stack"):
            logger.info(
                "No Perfetto frame timeline data; switching to atrace mode "
                "(draw_frames=%d traversal=%d)",
                len(sql_results.get("atrace_draw_frames", [])),
                len(sql_results.get("atrace_traversal_frames", [])),
            )

        if not frames:
            return CategoryReport(analyzer_name=self.name, status="insufficient_data", sql_results=sql_results)

        statistics = self._compute_statistics(sql_results)
        prompt = self._build_llm_prompt(statistics, sql_results)
        llm_response = self._call_llm(llm_client, prompt, tp=tp)
        return self._build_report_from_llm(llm_response, sql_results, statistics)

    def _compute_statistics(self, sql_results: dict[str, Any]) -> dict[str, Any]:
        frames = sql_results.get("frame_timeline", [])
        if not frames:
            return {}

        stats = summarize_frame_timeline(
            sql_results,
            target_frame_time_ms=self._target_frame_time_ms,
            severe_consecutive=self._severe_consecutive,
        )

        stats["top_jank_frames"] = build_top_jank_frames(
            frames,
            sql_results.get("jank_frame_call_stack", []),
            thread_states=sql_results.get("thread_state", []),
            cpu_freq_events=sql_results.get("cpu_freq_events", []),
            monitor_contentions=sql_results.get("monitor_contention", []),
            gc_events=sql_results.get("gc_events", []),
            gpu_render_slices=sql_results.get("gpu_render_slices", []),
            binder_flows=sql_results.get("binder_flow", []),
            thread_cpu_scheduling=sql_results.get("thread_cpu_scheduling", []),
            top_n=self._top_jank_frames,
        )

        # Hardware stats
        stats["cpu_capacity_clusters"] = get_cpu_capacity_clusters(sql_results)
        logger.debug("cpu_capacity_clusters: %d records", len(stats["cpu_capacity_clusters"]))

        thread_cpu_sched = sql_results.get("thread_cpu_scheduling", [])
        logger.debug("thread_cpu_scheduling: %d raw sched_slice records", len(thread_cpu_sched))

        cpu_freq_stats = get_cpu_frequency_stats(sql_results)
        if cpu_freq_stats:
            stats["cpu_freq_stats"] = cpu_freq_stats

        gpu_freq_stats = get_gpu_frequency_stats(sql_results)
        if gpu_freq_stats:
            stats["gpu_freq_stats"] = gpu_freq_stats

        return stats

    def _build_llm_prompt(
        self,
        statistics: dict[str, Any],
        sql_results: dict[str, Any],
        thresholds: dict[str, Any] | None = None,
        package_name: str | None = None,
    ) -> str:
        stats_overview = {k: v for k, v in statistics.items() if k not in ("top_jank_frames", "cpu_freq_stats", "gpu_freq_stats")}
        hardware_context = _describe_hardware_context(statistics)
        logger.debug("hardware_context for LLM:\n%s", hardware_context)

        top_frames = statistics.get("top_jank_frames", [])
        for i, f in enumerate(top_frames[:3]):
            logger.debug("Frame %d (frame_id=%s): thread_cpu_scheduling=%s",
                         i + 1, f.get("frame_id"), f.get("thread_cpu_scheduling", {}))

        # Build per-process jank distribution summary (top 10)
        jank_by_process = sql_results.get("jank_by_process", [])
        proc_lines = []
        for p in jank_by_process[:10]:
            pname = p.get("process_name") or "unknown"
            total = p.get("total_frames", 0)
            jank = p.get("jank_frames", 0)
            avg_ms = p.get("avg_dur_ms", 0) or 0
            max_ms = p.get("max_dur_ms", 0) or 0
            rate = (jank / total * 100) if total > 0 else 0
            proc_lines.append(f"  {pname}: {jank}/{total} 帧卡顿 ({rate:.1f}%), avg={avg_ms:.1f}ms max={max_ms:.1f}ms")
        jank_by_process_text = "\n".join(proc_lines) if proc_lines else "(无数据)"

        top_frames = statistics.get("top_jank_frames", [])
        frame_lines = []
        for i, f in enumerate(top_frames, 1):
            actual_dur = f.get("actual_dur", 0) or 0
            dur_ms = actual_dur / 1e6
            expected_dur = f.get("expected_dur")
            overrun_ms = f.get("overrun_ms", 0)
            dur_part = f"dur={dur_ms:.2f}ms"
            if expected_dur:
                dur_part += f" expected={expected_dur / 1e6:.2f}ms"
            dur_part += f" overrun={overrun_ms}ms"
            header = (
                f"### 帧 {i}: frame_id={f.get('frame_id')} ts={f.get('actual_ts')} "
                f"{dur_part} jank_type={f.get('jank_type')} "
                f"present_type={f.get('present_type')} "
                f"process={f.get('process_name')} layer={f.get('layer_name')}"
            )
            call_tree = f.get("call_tree", [])
            tree_text = _call_tree_to_text(call_tree) if call_tree else "(无调用树数据)"
            bottleneck = f.get("bottleneck", "")
            parts = [header, f"调用树:\n{tree_text}"]
            if bottleneck:
                parts.append(f"初步瓶颈: {bottleneck}")

            # Thread scheduling states
            thread_states = f.get("thread_states", [])
            if thread_states:
                sched_lines = []
                for ts in thread_states:
                    sched_lines.append(f"  {ts['thread']}: {ts['state']} {ts['dur_ms']}ms")
                parts.append(f"线程调度状态:\n" + "\n".join(sched_lines))

            # CPU frequency during frame
            cpu_freq = f.get("cpu_freq", {})
            if cpu_freq:
                freq_line = f"帧期间 CPU 频率: avg={cpu_freq.get('avg_mhz', 0):.0f}MHz min={cpu_freq.get('min_mhz', 0):.0f}MHz max={cpu_freq.get('max_mhz', 0):.0f}MHz"
                parts.append(freq_line)

            # Thread CPU core scheduling residency during frame
            cpu_sched = f.get("thread_cpu_scheduling", {})
            if cpu_sched:
                sched_parts = [f"{k}: {v}ms" for k, v in cpu_sched.items()]
                parts.append(f"帧核心调度时长分布: " + ", ".join(sched_parts) + " (判断是否在小核上拖慢)")

            # Monitor (Lock) Contentions during frame
            contentions = f.get("monitor_contentions", [])
            if contentions:
                cont_lines = []
                for c in contentions:
                    cont_lines.append(f"  {c.get('dur_ms', 0):.1f}ms: 被 {c.get('blocking_thread', 'unknown')}(tid={c.get('blocking_tid', '')}) 占用的 '{c.get('blocked_method', 'unknown')}' 锁阻塞")
                parts.append(f"发现锁竞争 (Monitor Contention):\n" + "\n".join(cont_lines))

            # GC events during frame
            gc_events = f.get("gc_events", [])
            if gc_events:
                gc_lines = []
                for g in gc_events:
                    gc_lines.append(f"  {g.get('dur_ms', 0):.1f}ms: {g.get('slice_name', 'unknown')} ({g.get('thread_name', '')})")
                parts.append(f"发生 GC (垃圾回收) 事件:\n" + "\n".join(gc_lines))

            # SF/GPU rendering events during frame
            sf_events = f.get("sf_gpu_render", [])
            if sf_events:
                sf_lines = []
                for s in sf_events:
                    sf_lines.append(f"  {s.get('dur_ms', 0):.1f}ms: {s.get('slice_name', 'unknown')} ({s.get('process_name', '')})")
                parts.append(f"SF/GPU 渲染耗时切片 (如 eglSwapBuffers/dequeueBuffer 等):\n" + "\n".join(sf_lines))

            # Binder cross-process flows during frame
            binder_events = f.get("binder_flows", [])
            if binder_events:
                binder_lines = []
                for b in binder_events:
                    binder_lines.append(
                        f"  {b.get('caller_dur_ms', 0):.1f}ms: {b.get('caller_name', '')} "
                        f"({b.get('caller_thread', '')}@{b.get('caller_process', '')}) → "
                        f"{b.get('callee_name', '')} {b.get('callee_dur_ms', 0):.1f}ms "
                        f"({b.get('callee_thread', '')}@{b.get('callee_process', '')})"
                    )
                parts.append(f"跨进程 Binder 调用:\n" + "\n".join(binder_lines))

            frame_lines.append("\n".join(parts))

        return self.prompt_template.format(
            hardware_context=hardware_context,
            statistics_overview=json.dumps(stats_overview, indent=2, ensure_ascii=False, default=str),
            jank_by_process_text=jank_by_process_text,
            package_name=package_name or "未指定",
            top_jank_frames_text="\n\n".join(frame_lines) if frame_lines else "(无卡顿帧数据)",
        )
