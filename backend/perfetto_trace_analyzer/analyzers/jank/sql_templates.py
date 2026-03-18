"""SQL templates for jank frame analysis.

Contains all Perfetto SQL queries: frame timeline, jank stats, call stacks,
monitor contention, binder flows, and atrace fallback queries.
"""

# ─── atrace fallback SQL ──────────────────────────────────────

# 用 draw-VRI[*] slices 作为帧边界（atrace 主线程帧标记）
SQL_ATRACE_DRAW_FRAMES = """\
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
SQL_ATRACE_TRAVERSAL_FRAMES = """\
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
SQL_ATRACE_CALL_STACK = """\
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

# ─── Standard SQL Templates ──────────────────────────────────

# 帧时间线：jank_type 是 Perfetto 基于 deadline 的卡顿判定依据
SQL_FRAME_TIMELINE = """\
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
SQL_JANK_TYPE_STATS = """\
SELECT jank_type, COUNT(*) AS cnt
FROM actual_frame_timeline_slice
GROUP BY jank_type ORDER BY cnt DESC
"""

# present_type 分布（On-time / Late / Dropped）
SQL_PRESENT_TYPE_STATS = """\
SELECT present_type, COUNT(*) AS cnt
FROM actual_frame_timeline_slice
GROUP BY present_type ORDER BY cnt DESC
"""

# 按进程统计卡顿
SQL_JANK_BY_PROCESS = """\
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
SQL_SLOW_RENDERS = """\
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
SQL_JANK_FRAME_CALL_STACK = """\
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
WHERE (t.name IN ('main', 'RenderThread', 'surfaceflinger')
       OR t.name LIKE 'Binder:%'
       OR t.name LIKE 'hwuiTask%'
       OR t.name LIKE 'GPU completion%')
  AND s.dur > 0
ORDER BY s.ts
"""

# 预查帧内的 monitor contention (锁竞争)
SQL_MONITOR_CONTENTION = """\
SELECT
    s.ts, s.dur, s.dur / 1e6 AS dur_ms,
    EXTRACT_ARG(s.arg_set_id, 'monitor_contention.blocking_thread_name') AS blocking_thread,
    EXTRACT_ARG(s.arg_set_id, 'monitor_contention.short_method_name') AS blocked_method,
    t.name AS thread_name, t.tid, t.utid, p.upid
FROM slice s
JOIN thread_track tt ON s.track_id = tt.id
JOIN thread t ON tt.utid = t.utid
JOIN process p ON t.upid = p.upid
WHERE s.name LIKE 'monitor contention%'
  AND s.dur > 500000 -- 超过 0.5ms 的锁竞争才关注
ORDER BY s.ts
"""

# 跨进程 Binder flow（caller → callee，通过 flow 表关联）
SQL_BINDER_FLOW = """\
SELECT
    s1.ts AS caller_ts, s1.dur / 1e6 AS caller_dur_ms,
    s1.name AS caller_name,
    t1.name AS caller_thread,
    p1.name AS caller_process, p1.upid AS caller_upid,
    s2.dur / 1e6 AS callee_dur_ms,
    s2.name AS callee_name,
    t2.name AS callee_thread,
    p2.name AS callee_process
FROM slice s1
JOIN flow f ON s1.id = f.slice_out
JOIN slice s2 ON f.slice_in = s2.id
JOIN thread_track tt1 ON s1.track_id = tt1.id
JOIN thread t1 ON tt1.utid = t1.utid
JOIN process p1 ON t1.upid = p1.upid
JOIN thread_track tt2 ON s2.track_id = tt2.id
JOIN thread t2 ON tt2.utid = t2.utid
JOIN process p2 ON t2.upid = p2.upid
WHERE s1.name LIKE 'binder transaction%'
  AND s1.dur > 1000000
ORDER BY s1.dur DESC
LIMIT 500
"""
