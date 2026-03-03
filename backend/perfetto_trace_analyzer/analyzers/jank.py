"""Jank (frame drop) detection and analysis module."""

from __future__ import annotations

import logging
from typing import Any

from ..base_analyzer import BaseAnalyzer
from ..models import CategoryReport

logger = logging.getLogger(__name__)

_DEFAULT_TARGET_FRAME_TIME_MS = 16.67
_DEFAULT_SEVERE_JANK_CONSECUTIVE = 3

# ─── atrace fallback SQL ──────────────────────────────────────

# 用 draw-VRI[*] slices 作为帧边界（atrace 主线程帧标记）
_SQL_ATRACE_DRAW_FRAMES = """\
SELECT
    s.id AS frame_id,
    s.ts AS actual_ts,
    s.dur AS actual_dur,
    s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    t.name AS thread_name,
    t.tid,
    t.utid,
    p.name AS process_name,
    p.upid,
    p.pid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE s.name LIKE 'draw-VRI[%]'
  AND s.dur > 0
ORDER BY s.ts
"""

# traversal slices（主线程 measure+layout+draw 整体，比 draw-VRI 更全面）
_SQL_ATRACE_TRAVERSAL_FRAMES = """\
SELECT
    s.id AS frame_id,
    s.ts AS actual_ts,
    s.dur AS actual_dur,
    s.dur / 1e6 AS dur_ms,
    t.name AS thread_name,
    t.tid,
    t.utid,
    p.name AS process_name,
    p.upid,
    p.pid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE s.name = 'traversal'
  AND s.dur > 0
ORDER BY s.ts
"""

# atrace 模式下的调用栈（主线程 + RenderThread，按进程过滤）
_SQL_ATRACE_CALL_STACK = """\
SELECT
    s.id AS slice_id,
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    s.depth, s.parent_id,
    t.name AS thread_name, t.tid,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (t.name = p.name OR t.name = 'RenderThread'
       OR t.name LIKE 'Binder:%'
       OR t.name LIKE 'hwuiTask%')
  AND s.dur > 0
ORDER BY s.ts
"""

# ─── Prompt ──────────────────────────────────────────────────

_PROMPT_TEMPLATE = """你是一个 Android 性能分析专家（精通 SurfaceFlinger 渲染管线）。

## Android 渲染管线
```
主线程: Choreographer#doFrame → measure → layout → draw → RenderThread 同步
RenderThread: DrawFrame → flush commands → eglSwapBuffers → GPU 执行
SurfaceFlinger: 合成各 Layer → Display 输出
```
每帧预算：16.67ms（60fps）/ 11.11ms（90fps）/ 8.33ms（120fps）

### jank_type 词汇表
| jank_type | 含义 |
|-----------|------|
| None | 正常帧 |
| App Deadline Missed | 应用主线程或 RenderThread 超期 |
| SurfaceFlinger CPU Deadline Missed | SF 合成线程 CPU 超期 |
| SurfaceFlinger GPU Deadline Missed | SF GPU 合成超期 |
| Buffer Stuffing | BufferQueue 积压，App 提交太快 |
| Prediction Error | 帧预测误差 |
| Display HAL | Display HAL 层延迟 |

### 常见根因
- 主线程 CPU 热点：measure/layout/draw 耗时函数
- RenderThread GPU 瓶颈：eglSwapBuffers 耗时长
- Binder 阻塞：主线程同步等待 system_server
- 锁竞争：主线程等待 synchronized 锁
- 主线程 I/O：直接读写文件/数据库
- GC 压力：Stop-The-World 暂停
- CPU 限频（Thermal Throttling）：散热导致降频
- 线程调度延迟：Runnable 状态时间长说明 CPU 资源不足
- IO Wait：线程在等待磁盘/网络 I/O

---

## 硬件性能上下文
{hardware_context}

---

## 统计概览
{statistics_overview}

---

## 目标应用包名
{package_name}

---

## 最严重的 Top-N 卡顿帧（按超时时间排序）
{top_jank_frames_text}

---

## 分析策略
1. 根据 jank_type 分布判断 App 侧 vs SF 侧
2. 结合硬件判断严重性：旗舰机（>3GHz）超 16.67ms 是代码问题；低端机（<2GHz）超 25ms 才需关注；CPU 均频 < 最高频 60% 疑似限频
3. 使用 `query_trace(sql, limit)` 工具深挖（最多 5 次）

### query_trace 常用 SQL
```sql
-- 帧内所有 slice（替换 ts/dur 为实际值）
SELECT s.name, s.dur/1e6 dur_ms, t.name thread
FROM slice s JOIN thread_track tt ON s.track_id=tt.id JOIN thread t ON tt.utid=t.utid
WHERE s.ts BETWEEN {{ts}} AND {{ts}}+{{dur}} ORDER BY s.dur DESC LIMIT 50

-- Binder 调用
SELECT name, dur/1e6 dur_ms FROM slice WHERE name LIKE 'binder%' AND ts BETWEEN {{ts}} AND {{ts}}+{{dur}}

-- GC 事件
SELECT name, dur/1e6 dur_ms FROM slice WHERE name LIKE '%GC%' ORDER BY dur DESC LIMIT 20

-- 线程状态（等锁/IO）
SELECT state, io_wait, dur/1e6 dur_ms FROM thread_state WHERE utid={{utid}} AND ts BETWEEN {{ts}} AND {{ts}}+{{dur}} ORDER BY dur DESC LIMIT 20
```

---

## 输出格式（严格 JSON）
```json
{{
  "frame_analyses": [
    {{
      "frame_id": 123,
      "ts": 1234567890,
      "flow_description": "详细的帧生命周期描述（见下方要求）",
      "bottleneck_function": "RecyclerView.onMeasure",
      "bottleneck_reason": "过多子 View 测量",
      "root_cause_category": "过度测量",
      "severity": "high",
      "side": "app",
      "evidence_sql": [
        {{
          "label": "layout 阶段耗时",
          "sql": "SELECT name, dur/1e6 AS dur_ms FROM slice WHERE name LIKE '%onLayout%' AND ts BETWEEN 1234567890 AND 1234567890+18200000 ORDER BY dur DESC LIMIT 5",
          "conclusion": "RecyclerView.onLayout 耗时 8.3ms，占帧时间 45%"
        }}
      ]
    }}
  ],
  "jank_cause_clusters": [
    {{
      "cause": "RecyclerView 过度测量",
      "description": "多帧中占 60%+ 帧时间",
      "count": 8,
      "severity": "high",
      "suggestion": "使用 setHasFixedSize(true)"
    }}
  ],
  "bottleneck_type": "cpu",
  "summary": "一句话总结",
  "app_jank_summary": "App 端摘要",
  "sf_jank_summary": "SF 端摘要",
  "hardware_assessment": "基于硬件的评估",
  "issues": [{{"severity": "high", "description": "...", "evidence": "...", "category": "app"}}],
  "suggestions": ["优化建议1"],
  "score": 45,
  "user_impact_assessment": "用户感知影响"
}}
```
bottleneck_type 只能是 "cpu" | "gpu" | "buffer_contention" | "mixed"
score 是 0-100 整数，结合硬件性能综合判断

### flow_description 要求
flow_description 必须是一段完整的帧生命周期叙述，按时间顺序串联整个渲染管线的关键阶段，包含具体耗时数据。格式要求：
- 按时间顺序描述帧经过的每个阶段：Choreographer → measure → layout → draw → sync → RenderThread → GPU → SurfaceFlinger → Display
- 每个阶段标注实际耗时（从 call tree 中提取）
- 明确指出哪个阶段是瓶颈、为什么慢
- 如果涉及跨线程/跨进程，说明等待和切换过程
- 如果有 buffer 竞争、Binder 阻塞、GC 等异常事件，在对应时间点插入说明
- 结合线程调度状态分析：如果线程有大量 Runnable 时间说明 CPU 资源竞争，Sleeping 说明等锁，IO Wait 说明 I/O 阻塞
- 结合帧期间 CPU 频率分析：如果频率明显低于最高频率，说明可能存在限频/降频
- 长度 150-300 字，不要太短

示例：
"Choreographer#doFrame 触发后，measure 阶段耗时 2.1ms（正常），layout 阶段因 RecyclerView 包含 42 个子 View 重新布局耗时 8.3ms（超标），draw 阶段 1.2ms。同步到 RenderThread 后，DrawFrame 耗时 3.5ms，其中 eglSwapBuffers 等待 GPU 完成耗时 2.8ms。总帧时间 18.2ms，超出 16.67ms 预算 1.5ms。瓶颈在 layout 阶段的 RecyclerView.onLayout，建议使用 setHasFixedSize 或减少嵌套层级。"

### evidence_sql 要求
每个 frame_analysis 必须附带 evidence_sql 数组，为你的关键结论提供可验证的 SQL 查询。要求：
- 每条 evidence 包含 label（结论标签）、sql（Perfetto SQL 查询）、conclusion（你从数据中得出的结论）
- sql 中用实际的 ts 和 dur 值（从帧数据中获取），不要用占位符
- 至少提供 1-3 条 evidence，覆盖：瓶颈函数耗时、线程调度状态、CPU 频率等关键结论
- SQL 必须是合法的 Perfetto trace_processor SQL，可直接在 Perfetto UI 中执行验证
"""

# ─── SQL Templates ────────────────────────────────────────────

# 帧时间线：jank_type 是 Perfetto 基于 deadline 的卡顿判定依据
_SQL_FRAME_TIMELINE = """\
SELECT
    af.id AS frame_id,
    af.ts AS actual_ts,
    af.dur AS actual_dur,
    ef.dur AS expected_dur,
    CASE WHEN ef.dur > 0 AND af.dur > ef.dur
         THEN (af.dur - ef.dur) / 1e6 ELSE 0 END AS overrun_ms,
    af.display_frame_token,
    af.jank_type,
    af.on_time_finish,
    af.layer_name,
    af.present_type,
    af.jank_severity_type,
    af.upid,
    p.name AS process_name,
    p.pid
FROM actual_frame_timeline_slice af
LEFT JOIN expected_frame_timeline_slice ef
    ON af.display_frame_token = ef.display_frame_token AND af.upid = ef.upid
LEFT JOIN process p ON af.upid = p.upid
ORDER BY af.ts
"""

# jank_type 分布统计
_SQL_JANK_TYPE_STATS = """\
SELECT jank_type, COUNT(*) AS cnt
FROM actual_frame_timeline_slice
GROUP BY jank_type ORDER BY cnt DESC
"""

# present_type 分布（On-time / Late / Dropped）
_SQL_PRESENT_TYPE_STATS = """\
SELECT present_type, COUNT(*) AS cnt
FROM actual_frame_timeline_slice
GROUP BY present_type ORDER BY cnt DESC
"""

# 按进程统计卡顿
_SQL_JANK_BY_PROCESS = """\
SELECT
    p.name AS process_name, p.pid,
    COUNT(*) AS total_frames,
    SUM(CASE WHEN af.present_type = 'Dropped Frame' THEN 1
             WHEN af.present_type = 'Late Present' THEN 1
             WHEN af.present_type = 'On-time Present' THEN 0
             WHEN af.jank_type != 'None' AND af.jank_type IS NOT NULL THEN 1
             ELSE 0 END) AS jank_frames,
    AVG(af.dur) / 1e6 AS avg_dur_ms,
    MAX(af.dur) / 1e6 AS max_dur_ms
FROM actual_frame_timeline_slice af
LEFT JOIN process p ON af.upid = p.upid
GROUP BY p.name, p.pid ORDER BY jank_frames DESC
"""

# 最慢的渲染帧（主线程 doFrame / RenderThread DrawFrame）
_SQL_SLOW_RENDERS = """\
SELECT
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    t.name AS thread_name, t.tid,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (s.name = 'Choreographer#doFrame' OR s.name = 'DrawFrame'
       OR s.name LIKE 'doFrame%' OR s.name = 'Draw')
  AND s.dur > 0
ORDER BY s.dur DESC LIMIT 30
"""

# 卡顿帧调用栈（主线程 + RenderThread + Binder）
_SQL_JANK_FRAME_CALL_STACK = """\
SELECT
    s.id AS slice_id,
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    s.name AS slice_name,
    s.depth, s.parent_id,
    t.name AS thread_name, t.tid,
    p.name AS process_name, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE (t.name IN ('main', 'RenderThread', 'surfaceflinger')
       OR t.name LIKE 'Binder:%'
       OR t.name LIKE 'hwuiTask%'
       OR t.name LIKE 'GPU completion%')
  AND s.dur > 0
ORDER BY s.ts
"""

# ─── atrace helpers ───────────────────────────────────────────

def _atrace_frames_to_unified(
    draw_frames: list[dict],
    traversal_frames: list[dict],
    target_ms: float,
) -> list[dict]:
    """Convert atrace draw-VRI / traversal slices to unified frame dicts.

    Matches each draw-VRI frame with the nearest preceding traversal slice
    from the same process (tid).  If no draw-VRI frames exist, falls back
    to traversal-only.  Output dicts mimic actual_frame_timeline_slice rows
    so all downstream statistics/LLM code can be reused unchanged.
    """
    # Build unified list: prefer draw-VRI as the primary frame marker;
    # traversal fills in measure+layout time that draw-VRI may not cover.
    if draw_frames:
        primary = draw_frames
    else:
        primary = traversal_frames

    result = []
    for f in primary:
        dur_ms = f.get("dur_ms", 0) or (f.get("actual_dur", 0) / 1e6)
        overrun = max(0.0, dur_ms - target_ms)
        result.append({
            "frame_id": f.get("frame_id"),
            "actual_ts": f.get("actual_ts", f.get("ts", 0)),
            "actual_dur": f.get("actual_dur", f.get("dur", 0)),
            "overrun_ms": round(overrun, 2),
            # atrace has no jank_type / present_type; synthesize from duration
            "jank_type": "App Deadline Missed" if dur_ms > target_ms else "None",
            "present_type": "Late Present" if dur_ms > target_ms else "On-time Present",
            "layer_name": f.get("slice_name", ""),
            "process_name": f.get("process_name", ""),
            "upid": f.get("upid"),
            "pid": f.get("pid"),
        })
    return result


# ─── Helpers ──────────────────────────────────────────────────

def _materialize_sql_results(sql_results: dict) -> None:
    """Convert QueryResultIterator values to list[dict] in-place.

    TraceProcessor query results are single-use iterators; materializing them
    allows multiple passes (statistics, prompt building, etc.) and .get() access.
    """
    for key, val in sql_results.items():
        if isinstance(val, list):
            continue
        try:
            col_names = val.column_names
            sql_results[key] = [
                {col: getattr(row, col) for col in col_names}
                for row in val
            ]
        except Exception:
            sql_results[key] = []


def _is_real_jank(f: dict) -> bool:
    """Determine if a frame is a real jank frame.

    Rules:
    - present_type == "Dropped Frame" → always real jank
    - present_type == "Late Present" → real jank
    - present_type == "On-time Present" → not jank (even if jank_type is flagged,
      e.g. OEM frame interpolation can compensate)
    - jank_type != None with other present_type → real jank
    """
    jank_type = f.get("jank_type") or "None"
    present_type = f.get("present_type") or ""

    # Dropped frame is always real jank
    if present_type == "Dropped Frame":
        return True
    # Late present is real jank
    if present_type == "Late Present":
        return True
    # On-time present → not real jank regardless of jank_type
    if present_type == "On-time Present":
        return False
    # jank_type flagged with other present_type → real jank
    if jank_type != "None":
        return True
    return False


def _jank_severity(f: dict) -> str:
    """Classify jank severity based on present_type and overrun."""
    present_type = f.get("present_type") or ""
    overrun = f.get("overrun_ms", 0)
    if present_type == "Dropped Frame":
        return "high"
    if overrun > 16.67:
        return "high"
    if present_type == "Late Present":
        return "medium"
    return "low"

def _build_call_tree(slices: list[dict], min_dur_ms: float = 0.5, max_children: int = 6) -> list[dict]:
    """Build a nested call tree from flat slice list using parent_id."""
    if not slices:
        return []
    by_id: dict[int, dict] = {}
    for s in slices:
        sid = s.get("slice_id")
        if sid is None:
            continue
        by_id[sid] = {
            "id": sid, "name": s.get("slice_name", ""),
            "dur_ms": round(s.get("dur_ms", 0), 2),
            "ts": s.get("ts"), "dur": s.get("dur", 0),
            "depth": s.get("depth", 0),
            "thread": s.get("thread_name", ""),
            "parent_id": s.get("parent_id"),
            "children": [], "self_ms": 0.0,
        }
    roots: list[dict] = []
    for node in by_id.values():
        pid = node["parent_id"]
        if pid is not None and pid in by_id:
            by_id[pid]["children"].append(node)
        else:
            roots.append(node)

    def _self_time(node: dict) -> None:
        node["self_ms"] = round(max(0.0, node["dur_ms"] - sum(c["dur_ms"] for c in node["children"])), 2)
        for c in node["children"]:
            _self_time(c)

    def _prune(node: dict) -> dict | None:
        if node["dur_ms"] < min_dur_ms:
            return None
        children = sorted(
            [pc for c in node["children"] for pc in [_prune(c)] if pc],
            key=lambda c: c["dur_ms"], reverse=True
        )[:max_children]
        node["children"] = children
        return node

    def _clean(node: dict) -> dict:
        return {
            "name": node["name"], "dur_ms": node["dur_ms"],
            "self_ms": node["self_ms"], "thread": node["thread"],
            "ts": node.get("ts"), "dur": node.get("dur", 0),
            "children": [_clean(c) for c in node["children"]],
        }

    result = []
    for root in roots:
        _self_time(root)
        pr = _prune(root)
        if pr:
            result.append(_clean(pr))
    result.sort(key=lambda r: r["dur_ms"], reverse=True)
    return result[:5]


def _call_tree_to_text(call_tree: list[dict]) -> str:
    """Convert nested call tree to indented text for LLM."""
    lines: list[str] = []

    def _render(nodes: list[dict], prefix: str = "", is_root: bool = True) -> None:
        for i, node in enumerate(nodes):
            is_last = i == len(nodes) - 1
            connector = "" if (is_root and not prefix) else ("└── " if is_last else "├── ")
            child_prefix = "" if (is_root and not prefix) else ("    " if is_last else "│   ")
            parts = [f"{prefix}{connector}{node['name']}  {node['dur_ms']}ms"]
            if node.get("self_ms", 0) > 0.5:
                parts.append(f"(self: {node['self_ms']}ms)")
            if node.get("thread") and node["thread"] not in ("main",):
                parts.append(f"[{node['thread']}]")
            if node.get("self_ms", 0) > 5.0 and not node.get("children"):
                parts.append("← 瓶颈")
            lines.append(" ".join(parts))
            if node.get("children"):
                _render(node["children"], prefix + child_prefix, is_root=False)

    _render(call_tree)
    return "\n".join(lines)


def _build_top_jank_frames_with_trees(
    frames: list[dict],
    call_stacks: list[dict],
    thread_states: list[dict] | None = None,
    cpu_freq_events: list[dict] | None = None,
    top_n: int = 20,
) -> list[dict]:
    """Build top-N jank frames with call trees, thread state, and CPU freq attached."""
    thread_states = thread_states or []
    cpu_freq_events = cpu_freq_events or []

    jank_frames = sorted(
        [f for f in frames if _is_real_jank(f)],
        key=lambda f: f.get("overrun_ms", 0), reverse=True
    )[:top_n]

    result = []
    for f in jank_frames:
        f_ts = f.get("actual_ts", 0)
        f_dur = f.get("actual_dur", 0)
        f_upid = f.get("upid")
        f_end = f_ts + f_dur

        frame_slices = [
            s for s in call_stacks
            if f_ts <= s.get("ts", 0) <= f_end
            and (f_upid is None or s.get("upid") == f_upid)
        ]
        call_tree = _build_call_tree(frame_slices)
        root_cause = _find_bottleneck(call_tree)

        # Thread scheduling states during this frame
        frame_thread_states = _summarize_thread_states(
            thread_states, f_ts, f_end, f_upid
        )

        # CPU frequency during this frame
        frame_cpu_freq = _summarize_cpu_freq(
            cpu_freq_events, f_ts, f_end
        )

        result.append({
            "frame_id": f.get("frame_id"),
            "actual_ts": f_ts,
            "actual_dur": f_dur,
            "overrun_ms": round(f.get("overrun_ms", 0), 2),
            "jank_type": f.get("jank_type"),
            "present_type": f.get("present_type"),
            "layer_name": f.get("layer_name"),
            "process_name": f.get("process_name"),
            "call_tree": call_tree,
            "bottleneck": root_cause,
            "thread_states": frame_thread_states,
            "cpu_freq": frame_cpu_freq,
        })
    return result


def _summarize_thread_states(
    thread_states: list[dict], f_ts: int, f_end: int, f_upid: int | None
) -> list[dict]:
    """Summarize thread scheduling states during a frame's time window."""
    relevant = [
        s for s in thread_states
        if s.get("ts", 0) < f_end
        and s.get("ts", 0) + s.get("dur", 0) > f_ts
        and (f_upid is None or s.get("upid") == f_upid)
    ]
    if not relevant:
        return []

    # Aggregate by thread + state
    agg: dict[str, float] = {}
    for s in relevant:
        thread = s.get("thread_name", "")
        state = s.get("state", "")
        io_wait = s.get("io_wait")
        label = state
        if state == "S":
            label = "Sleeping"
        elif state == "R":
            label = "Running"
        elif state == "R+":
            label = "Runnable"
        elif state == "D":
            label = "IO Wait" if io_wait else "Uninterruptible"
        key = f"{thread}:{label}"
        # Clip to frame window
        s_start = max(s.get("ts", 0), f_ts)
        s_end = min(s.get("ts", 0) + s.get("dur", 0), f_end)
        agg[key] = agg.get(key, 0) + (s_end - s_start) / 1e6

    # Return top entries sorted by duration
    entries = []
    for key, dur_ms in sorted(agg.items(), key=lambda x: -x[1])[:10]:
        thread, state = key.split(":", 1)
        entries.append({"thread": thread, "state": state, "dur_ms": round(dur_ms, 2)})
    return entries


def _summarize_cpu_freq(
    cpu_freq_events: list[dict], f_ts: int, f_end: int
) -> dict[str, Any]:
    """Summarize CPU frequency during a frame's time window."""
    relevant = [
        e for e in cpu_freq_events
        if f_ts <= e.get("ts", 0) <= f_end
    ]
    if not relevant:
        return {}

    # Group by CPU core
    by_cpu: dict[int, list[float]] = {}
    for e in relevant:
        cpu = e.get("cpu", 0)
        freq_mhz = (e.get("freq_khz", 0) or 0) / 1000
        by_cpu.setdefault(cpu, []).append(freq_mhz)

    cores = {}
    for cpu, freqs in sorted(by_cpu.items()):
        cores[f"core_{cpu}"] = {
            "avg_mhz": round(sum(freqs) / len(freqs), 0),
            "min_mhz": round(min(freqs), 0),
            "max_mhz": round(max(freqs), 0),
        }
    all_freqs = [f for fs in by_cpu.values() for f in fs]
    return {
        "avg_mhz": round(sum(all_freqs) / len(all_freqs), 0) if all_freqs else 0,
        "min_mhz": round(min(all_freqs), 0) if all_freqs else 0,
        "max_mhz": round(max(all_freqs), 0) if all_freqs else 0,
        "cores": cores,
    }


def _find_bottleneck(call_tree: list[dict]) -> str:
    """Find the node with highest self_ms in the call tree, with clean name."""
    best = {"self_ms": 0.0, "name": "", "thread": ""}

    def _walk(node: dict) -> None:
        if node.get("self_ms", 0) > best["self_ms"]:
            best["self_ms"] = node["self_ms"]
            best["name"] = node["name"]
            best["thread"] = node.get("thread", "")
        for c in node.get("children", []):
            _walk(c)

    for root in call_tree:
        _walk(root)

    if not best["name"]:
        return ""

    # Clean up raw internal names
    name = best["name"]
    # Truncate long names like "dequeueBuffer - VRI[Launcher]#78(BLAST Consumer)78"
    if " - " in name:
        name = name.split(" - ")[0].strip()
    # Truncate names with coords like "Drawing  0.00  0.00 1272.00 2800.00"
    if name.startswith("Drawing") and len(name) > 10:
        name = "Drawing"
    # Truncate names with parens like "Texture upload(47) 1280x608"
    if "(" in name and len(name) > 40:
        name = name[:name.index("(")].strip()

    return f"{name} ({best['self_ms']:.1f}ms self on {best['thread']})"


def _build_hardware_context(statistics: dict[str, Any]) -> str:
    """Build human-readable hardware context for the LLM prompt."""
    lines: list[str] = []
    cpu_stats = statistics.get("cpu_freq_stats", [])
    if cpu_stats:
        max_freqs = [c.get("max_freq_mhz", 0) for c in cpu_stats]
        overall_max = max(max_freqs) if max_freqs else 0
        tier = ("旗舰机（≥3GHz）" if overall_max >= 3000
                else "中端机（2-3GHz）" if overall_max >= 2000
                else "低端机（<2GHz）")
        lines.append(f"CPU 核心数: {len(cpu_stats)}，设备档次: {tier}，最高频率 {overall_max:.0f}MHz")
        for c in cpu_stats:
            lines.append(f"  core {c['cpu']}: avg={c.get('avg_freq_mhz',0):.0f} max={c.get('max_freq_mhz',0):.0f} min={c.get('min_freq_mhz',0):.0f} MHz")
        # Throttling detection: big cores = cores with max freq == overall_max
        big_cores = [c for c in cpu_stats if c.get("max_freq_mhz", 0) >= overall_max * 0.99]
        if big_cores and overall_max > 0:
            avg_big = sum(c.get("avg_freq_mhz", 0) for c in big_cores) / len(big_cores)
            if avg_big / overall_max < 0.6:
                lines.append(f"⚠️ 疑似散热限频：大核均频 {avg_big:.0f}MHz，仅为最高频率的 {avg_big/overall_max*100:.0f}%")
    else:
        lines.append("CPU 频率数据不可用")

    gpu = statistics.get("gpu_freq_stats")
    if gpu:
        lines.append(f"GPU 频率: avg={gpu.get('avg_freq_mhz',0):.0f}MHz max={gpu.get('max_freq_mhz',0):.0f}MHz")
    else:
        lines.append("GPU 频率数据不可用")
    return "\n".join(lines)


def _percentile(sorted_values: list[float], p: float) -> float:
    """Compute the p-th percentile from a sorted list."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    k = (p / 100) * (n - 1)
    f = int(k)
    c = f + 1
    if c >= n:
        return sorted_values[-1]
    return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])


# ─── Analyzer ─────────────────────────────────────────────────

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
            "frame_timeline": _SQL_FRAME_TIMELINE,
            "jank_type_stats": _SQL_JANK_TYPE_STATS,
            "present_type_stats": _SQL_PRESENT_TYPE_STATS,
            "jank_by_process": _SQL_JANK_BY_PROCESS,
            "slow_renders": _SQL_SLOW_RENDERS,
            "jank_frame_call_stack": _SQL_JANK_FRAME_CALL_STACK,
            "atrace_draw_frames": _SQL_ATRACE_DRAW_FRAMES,
            "atrace_traversal_frames": _SQL_ATRACE_TRAVERSAL_FRAMES,
            "atrace_call_stack": _SQL_ATRACE_CALL_STACK,
        }

    @property
    def prompt_template(self) -> str:
        return _PROMPT_TEMPLATE

    def analyze(self, tp: Any, llm_client: Any) -> CategoryReport:
        sql_results = self._execute_queries(tp)

        # Convert QueryResultIterator objects to list[dict] so they can be
        # iterated multiple times and accessed with .get().
        _materialize_sql_results(sql_results)

        frames = sql_results.get("frame_timeline", [])

        # atrace fallback: actual_frame_timeline_slice is empty (no Perfetto FrameTimeline data)
        if not frames:
            draw_frames = sql_results.get("atrace_draw_frames", [])
            traversal_frames = sql_results.get("atrace_traversal_frames", [])
            if draw_frames or traversal_frames:
                logger.info(
                    "No Perfetto frame timeline data; switching to atrace mode "
                    "(draw_frames=%d traversal=%d)", len(draw_frames), len(traversal_frames)
                )
                frames = _atrace_frames_to_unified(
                    draw_frames, traversal_frames, self._target_frame_time_ms
                )
                sql_results["frame_timeline"] = frames
                # Use atrace call stack instead of Perfetto jank_frame_call_stack
                sql_results["jank_frame_call_stack"] = sql_results.get("atrace_call_stack", [])

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

        total = len(frames)
        jank_count = sum(1 for f in frames if _is_real_jank(f))
        jank_rate = jank_count / total if total > 0 else 0.0

        overruns = [f.get("overrun_ms", 0) for f in frames if (f.get("overrun_ms") or 0) > 0]
        sorted_overruns = sorted(overruns)

        # App vs SF split
        app_frames = [f for f in frames if "surfaceflinger" not in (f.get("process_name") or "").lower()]
        sf_frames = [f for f in frames if "surfaceflinger" in (f.get("process_name") or "").lower()]

        stats: dict[str, Any] = {
            "total_frames": total,
            "jank_frames": jank_count,
            "jank_rate_pct": round(jank_rate * 100, 2),
            "target_frame_time_ms": self._target_frame_time_ms,
            "max_overrun_ms": round(max(sorted_overruns), 2) if sorted_overruns else 0,
            "p95_overrun_ms": round(_percentile(sorted_overruns, 95), 2) if sorted_overruns else 0,
            "app_total_frames": len(app_frames),
            "app_jank_frames": sum(1 for f in app_frames if _is_real_jank(f)),
            "sf_total_frames": len(sf_frames),
            "sf_jank_frames": sum(1 for f in sf_frames if _is_real_jank(f)),
        }

        if stats["app_total_frames"] > 0:
            stats["app_jank_rate_pct"] = round(stats["app_jank_frames"] / stats["app_total_frames"] * 100, 2)
        if stats["sf_total_frames"] > 0:
            stats["sf_jank_rate_pct"] = round(stats["sf_jank_frames"] / stats["sf_total_frames"] * 100, 2)

        # present_type counts
        present_map = {r.get("present_type", ""): r.get("cnt", 0) for r in sql_results.get("present_type_stats", [])}
        stats["dropped_frames"] = present_map.get("Dropped Frame", 0)
        stats["late_present_frames"] = present_map.get("Late Present", 0)

        # Frame duration stats (for ScoreBar)
        frame_durs = sorted([f.get("actual_dur", 0) / 1e6 for f in frames if (f.get("actual_dur") or 0) > 0])
        stats["p95_frame_time_ms"] = round(_percentile(frame_durs, 95), 2) if frame_durs else 0
        stats["max_frame_time_ms"] = round(max(frame_durs), 2) if frame_durs else 0

        # Severe jank (consecutive)
        jank_flags = [_is_real_jank(f) for f in frames]
        stats["severe_jank_events"] = len(detect_severe_jank(jank_flags, self._severe_consecutive))

        # Top worst jank frames with call trees
        call_stacks = sql_results.get("jank_frame_call_stack", [])
        thread_states = sql_results.get("thread_state", [])
        cpu_freq_events = sql_results.get("cpu_freq_events", [])
        stats["top_jank_frames"] = _build_top_jank_frames_with_trees(
            frames, call_stacks, thread_states, cpu_freq_events,
            top_n=self._top_jank_frames
        )

        # Slow render slices summary
        slow_renders = sql_results.get("slow_renders", [])
        if slow_renders:
            durs = [s.get("dur_ms", 0) for s in slow_renders if s.get("dur_ms")]
            stats["slow_render_p95_ms"] = round(_percentile(sorted(durs), 95), 2) if durs else 0
            stats["slow_render_max_ms"] = round(max(durs), 2) if durs else 0

        # Hardware stats
        cpu_freq_rows = sql_results.get("cpu_freq", [])
        if cpu_freq_rows:
            stats["cpu_freq_stats"] = [
                {
                    "cpu": r.get("cpu"),
                    "avg_freq_mhz": round((r.get("avg_freq_khz") or 0) / 1000, 1),
                    "max_freq_mhz": round((r.get("max_freq_khz") or 0) / 1000, 1),
                    "min_freq_mhz": round((r.get("min_freq_khz") or 0) / 1000, 1),
                }
                for r in cpu_freq_rows
            ]

        gpu_freq_rows = sql_results.get("gpu_freq", [])
        if gpu_freq_rows and (gpu_freq_rows[0].get("max_freq_hz") or 0) > 0:
            r = gpu_freq_rows[0]
            # GPU freq counter is in kHz
            stats["gpu_freq_stats"] = {
                "avg_freq_mhz": round((r.get("avg_freq_hz") or 0) / 1000, 1),
                "max_freq_mhz": round((r.get("max_freq_hz") or 0) / 1000, 1),
            }

        return stats

    def _build_llm_prompt(
        self,
        statistics: dict[str, Any],
        sql_results: dict[str, Any],
        thresholds: dict[str, Any] | None = None,
        package_name: str | None = None,
    ) -> str:
        import json

        stats_overview = {k: v for k, v in statistics.items() if k not in ("top_jank_frames", "cpu_freq_stats", "gpu_freq_stats")}
        hardware_context = _build_hardware_context(statistics)

        top_frames = statistics.get("top_jank_frames", [])
        frame_lines = []
        for i, f in enumerate(top_frames, 1):
            header = (
                f"### 帧 {i}: frame_id={f.get('frame_id')} ts={f.get('actual_ts')} "
                f"overrun={f.get('overrun_ms')}ms jank_type={f.get('jank_type')} "
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

            frame_lines.append("\n".join(parts))

        return self.prompt_template.format(
            hardware_context=hardware_context,
            statistics_overview=json.dumps(stats_overview, indent=2, ensure_ascii=False, default=str),
            package_name=package_name or "未指定",
            top_jank_frames_text="\n\n".join(frame_lines) if frame_lines else "(无卡顿帧数据)",
        )


# ─── Public helpers (used by tests) ──────────────────────────

def classify_jank_frames(durations_ms: list[float], target_frame_time_ms: float) -> list[bool]:
    """Classify each frame as jank (True) or not based on target frame time."""
    return [d > target_frame_time_ms for d in durations_ms]


def detect_severe_jank(jank_flags: list[bool], min_consecutive: int) -> list[dict[str, Any]]:
    """Detect severe jank events (consecutive jank frames >= min_consecutive)."""
    events: list[dict[str, Any]] = []
    i, n = 0, len(jank_flags)
    while i < n:
        if jank_flags[i]:
            start = i
            while i < n and jank_flags[i]:
                i += 1
            length = i - start
            if length >= min_consecutive:
                events.append({"start_index": start, "end_index": i - 1, "consecutive_frames": length})
        else:
            i += 1
    return events
