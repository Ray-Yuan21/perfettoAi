"""LLM prompt template for jank analysis."""

PROMPT_TEMPLATE = """你是一个 Android 性能分析专家（精通 SurfaceFlinger 渲染管线）。

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

## 进程级卡顿分布（Top 10）
{jank_by_process_text}

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

-- 跨进程 Binder 追踪（如果发现 Binder 调用耗时，务必用这个查对端！）
SELECT s2.name AS reply_name, s2.dur/1e6 AS reply_dur_ms, t2.name AS reply_thread, p2.name AS reply_process
FROM slice s1
JOIN flow f ON s1.id = f.slice_out
JOIN slice s2 ON s2.id = f.slice_in
JOIN thread_track tt2 ON s2.track_id = tt2.id JOIN thread t2 ON tt2.utid = t2.utid JOIN process p2 ON t2.upid = p2.upid
WHERE s1.id = {{binder_slice_id}}

-- 锁竞争分析 (Monitor Contention) - 找出谁拿着锁
SELECT s.name, s.dur/1e6 AS wait_dur_ms, EXTRACT_ARG(s.arg_set_id, 'monitor_contention.blocking_thread_name') AS blocking_thread, EXTRACT_ARG(s.arg_set_id, 'monitor_contention.blocking_tid') AS blocking_tid, EXTRACT_ARG(s.arg_set_id, 'monitor_contention.short_method_name') AS method
FROM slice s JOIN thread_track tt ON s.track_id = tt.id
WHERE s.name LIKE 'monitor contention%' AND s.ts BETWEEN {{ts}} AND {{ts}}+{{dur}} AND tt.utid = {{utid}}

-- GC 事件
SELECT name, dur/1e6 dur_ms FROM slice WHERE name LIKE '%GC%' ORDER BY dur DESC LIMIT 20

-- 线程具体状态耗时统计 (计算这段时间内真正跑在 CPU 上的时间 vs 阻塞时间)
SELECT state, SUM(dur)/1e6 AS total_dur_ms FROM thread_state WHERE utid={{utid}} AND ts BETWEEN {{ts}} AND {{ts}}+{{dur}} GROUP BY state ORDER BY total_dur_ms DESC

-- 线程状态明细（辨别是被抢占还是等锁/IO，带 waker_thread 追杀死锁源头！）
SELECT ts.state, ts.io_wait, ts.dur/1e6 AS dur_ms, wt.name AS waker_thread FROM thread_state ts LEFT JOIN thread wt ON ts.waker_utid = wt.utid WHERE ts.utid={{utid}} AND ts.ts BETWEEN {{ts}} AND {{ts}}+{{dur}} ORDER BY ts.dur DESC LIMIT 20
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
- **特别注意跨进程通信：如果瓶颈是 Binder，必须指出对端进程是什么，对端卡在什么操作上**
- **特别注意锁竞争：如果是 Monitor Contention，指出被哪个后台线程占用了锁**
- 如果涉及跨线程/跨进程，说明等待和切换过程
- 如果有 buffer 竞争、Binder 阻塞、GC 等异常事件，在对应时间点插入说明
- **特别注意 CPU 真实消耗**：如果发现 `Running` 状态总耗时远小于墙钟时间，说明卡顿是因为等锁/等资源/被排挤；如果满负荷 `Running`，说明是死循环或过重计算。
- **特别注意大小核与调度**：如果线程长期跑在【小核心】上，或者运行时频率极低，说明这是热温控降频(Thermal Throttling)或者优先级错乱，这不是应用代码的错！
- 结合线程调度状态分析：大量 Runnable 说明 CPU 资源竞争，Sleeping 等锁时结合 `waker_thread` 查是谁唤醒它的，IO Wait 说明 I/O 阻塞
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
