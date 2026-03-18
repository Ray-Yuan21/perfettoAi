# Example Traces

This directory contains small sample Perfetto trace files for quickly trying perfetto-ai without capturing your own trace.

## Files

| File | Size | Scenario |
|------|------|----------|
| `jank_sample.perfetto-trace` | ~5 MB | Scrolling with heavy jank — multiple dropped frames |
| `startup_sample.perfetto-trace` | ~3 MB | App cold start — ContentProvider and class loading bottlenecks |
| `anr_sample.perfetto-trace` | ~4 MB | ANR triggered by Binder blocking on main thread |

## How to use

```bash
# Upload via CLI
curl -F "file=@examples/jank_sample.perfetto-trace" \
  http://localhost:8000/api/traces/upload

# Or drag-drop any of these files into the web UI at http://localhost:5173
```

## Capturing your own trace

```bash
# On device (requires adb)
adb shell perfetto \
  -c - --txt \
  -o /data/misc/perfetto-traces/trace \
<<EOF
buffers: { size_kb: 63488 fill_policy: RING_BUFFER }
data_sources: { config { name: "linux.ftrace" ftrace_config {
  ftrace_events: "sched/sched_switch"
  ftrace_events: "sched/sched_wakeup"
  ftrace_events: "power/cpu_frequency"
  atrace_categories: "gfx" "view" "wm" "am" "binder_driver"
}}}
data_sources: { config { name: "android.surfaceflinger.frametimeline" }}
data_sources: { config { name: "android.gpu.memory" }}
duration_ms: 10000
EOF

adb pull /data/misc/perfetto-traces/trace ./my_trace.perfetto-trace
```

Then upload `my_trace.perfetto-trace` to perfetto-ai.
