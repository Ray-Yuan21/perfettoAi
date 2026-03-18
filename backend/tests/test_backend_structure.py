import threading
import time
from types import SimpleNamespace

from perfetto_trace_analyzer.models import AnalysisResult, CategoryReport
from perfetto_trace_analyzer.services.catalog_service import CatalogService
from perfetto_trace_analyzer.base_analyzer import BaseAnalyzer
from perfetto_trace_analyzer.diagnostics import (
    build_top_jank_frames,
    describe_hardware_context,
    get_unified_frame_timeline,
    get_cpu_capacity_clusters,
    get_cpu_frequency_stats,
    get_thread_cpu_scheduling_aggs,
    get_thread_state_aggs,
    summarize_frame_timeline,
)
from perfetto_trace_analyzer.presenters import present_trace_result
from perfetto_trace_analyzer.registry import AnalyzerRegistry
from perfetto_trace_analyzer.trace_processor import TraceProcessorConnection, TraceProcessorPool


def test_present_trace_result_keeps_api_shape():
    result = AnalysisResult(
        trace_path="demo.trace",
        metadata={"trace_file": "demo.trace"},
        category_reports=[
            CategoryReport(
                analyzer_name="jank",
                status="success",
                sql_results={
                    "frame_timeline": [
                        {
                            "display_frame_token": 1,
                            "upid": 42,
                            "actual_ts": 100,
                            "actual_dur": 21_000_000,
                            "overrun_ms": 4.3,
                            "present_type": "Dropped Frame",
                            "jank_type": "App Deadline Missed",
                            "layer_name": "com.demo/.MainActivity#42",
                            "process_name": "com.demo",
                            "pid": 123,
                            "frame_id": 7,
                        }
                    ]
                },
                statistics={
                    "top_jank_frames": [
                        {
                            "actual_ts": 100,
                            "call_tree": [{"name": "inflate"}],
                            "bottleneck": "inflate",
                        }
                    ]
                },
                llm_insights={
                    "frame_analyses": [
                        {
                            "frame_id": 7,
                            "flow_description": "UI thread blocked",
                            "bottleneck_function": "inflate",
                            "root_cause_category": "main_thread",
                            "severity": "high",
                        }
                    ]
                },
            )
        ],
    )

    payload = present_trace_result(result)

    assert "category_reports" in payload
    assert "jank_frames" in payload
    assert payload["jank_frames"][0]["jank_type"] == "App超时"
    assert payload["jank_frames"][0]["analysis"]["bottleneck_function"] == "inflate"


def test_registry_uses_analyzer_name_for_config(monkeypatch):
    class FakeAnalyzer:
        name = "demo"

        def __init__(self, config=None):
            self.config = config or {}

    fake_module = SimpleNamespace(FakeAnalyzer=FakeAnalyzer, __name__="fake_module")

    monkeypatch.setattr(
        "perfetto_trace_analyzer.registry.pkgutil.iter_modules",
        lambda _path: [(None, "fake_module", False)],
    )
    monkeypatch.setattr(
        "perfetto_trace_analyzer.registry.importlib.import_module",
        lambda *_args, **_kwargs: fake_module,
    )
    monkeypatch.setattr(
        "perfetto_trace_analyzer.registry._discover_module_analyzers",
        lambda _module: [FakeAnalyzer],
    )

    registry = AnalyzerRegistry()
    registry.auto_discover({"demo": {"threshold": 99}})

    analyzer = registry.get_analyzers(["demo"])[0]
    assert analyzer.config == {"threshold": 99}


def test_registry_accepts_package_reexported_analyzers():
    class PackageAnalyzer(BaseAnalyzer):
        @property
        def name(self) -> str:
            return "package_demo"

        @property
        def sql_templates(self) -> dict[str, str]:
            return {}

        @property
        def prompt_template(self) -> str:
            return ""

        def analyze(self, tp, llm_client):
            raise NotImplementedError

    PackageAnalyzer.__module__ = "fake_pkg.inner"

    fake_package = SimpleNamespace(PackageAnalyzer=PackageAnalyzer, __name__="fake_pkg")
    analyzers = __import__("perfetto_trace_analyzer.registry", fromlist=["_discover_module_analyzers"])
    discovered = analyzers._discover_module_analyzers(fake_package)
    assert discovered == [PackageAnalyzer]


def test_catalog_service_uses_registry_metadata(monkeypatch):
    class FakeRegistry:
        available_names = ["memory", "jank", "binder"]

        def auto_discover(self, _configs):
            return None

    monkeypatch.setattr(
        "perfetto_trace_analyzer.services.catalog_service.AnalyzerRegistry",
        lambda: FakeRegistry(),
    )

    service = CatalogService()
    analyzers = service.list_analyzers()

    assert [item["id"] for item in analyzers] == ["jank", "memory", "binder"]
    assert analyzers[0]["label"] == "Jank"


def test_trace_processor_pool_deduplicates_inflight_load(monkeypatch, tmp_path):
    trace_path = str(tmp_path / "demo.trace")
    pool = TraceProcessorPool(max_size=2)
    load_calls = 0
    load_lock = threading.Lock()

    def fake_load(self):
        nonlocal load_calls
        with load_lock:
            load_calls += 1
        time.sleep(0.05)
        self._tp = object()

    monkeypatch.setattr(TraceProcessorConnection, "load", fake_load)

    results = []

    def worker():
        results.append(pool.get_connection(trace_path))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert load_calls == 1
    assert len({id(conn) for conn in results}) == 1


def test_cpu_metrics_keep_existing_stat_shape():
    sql_results = {
        "cpu_freq": [
            {"cpu": 0, "avg_freq_khz": 1200000, "max_freq_khz": 1800000, "min_freq_khz": 800000},
            {"cpu": 7, "avg_freq_khz": 2400000, "max_freq_khz": 3000000, "min_freq_khz": 1800000},
        ],
        "cpu_capacity_clusters": [
            {"cpu": 0, "max_freq_khz": 1800000},
            {"cpu": 7, "max_freq_khz": 3000000},
        ],
    }

    cpu_stats = get_cpu_frequency_stats(sql_results)
    cpu_clusters = get_cpu_capacity_clusters(sql_results)
    hardware_context = describe_hardware_context(
        {
            "cpu_freq_stats": cpu_stats,
            "cpu_capacity_clusters": cpu_clusters,
        }
    )

    assert cpu_stats[0]["avg_freq_mhz"] == 1200
    assert cpu_stats[1]["max_freq_mhz"] == 3000
    assert cpu_clusters[1]["cpu"] == 7
    assert "旗舰机" in hardware_context


def test_thread_and_scheduling_metrics_keep_existing_stat_shape():
    sql_results = {
        "thread_state": [
            {"state": "R", "dur_ms": 8.2},
            {"state": "S", "dur_ms": 12.4},
            {"state": "S", "dur_ms": 2.1},
        ],
        "thread_cpu_scheduling": [
            {"cpu": 0, "dur_ms": 1.5},
            {"cpu": 0, "dur_ms": 2.0},
            {"cpu": 7, "dur_ms": 4.5},
        ],
    }

    thread_state_aggs = get_thread_state_aggs(sql_results)
    scheduling_aggs = get_thread_cpu_scheduling_aggs(sql_results)

    assert thread_state_aggs == {"R": 8.2, "S": 14.5}
    assert scheduling_aggs == {"CPU_0": 3.5, "CPU_7": 4.5}


def test_frame_timing_diagnostics_keep_existing_stat_shape():
    sql_results = {
        "frame_timeline": [
            {
                "frame_id": 1,
                "actual_ts": 0,
                "actual_dur": 20_000_000,
                "overrun_ms": 3.33,
                "present_type": "Late Present",
                "jank_type": "App Deadline Missed",
                "process_name": "com.demo",
            },
            {
                "frame_id": 2,
                "actual_ts": 20_000_000,
                "actual_dur": 15_000_000,
                "overrun_ms": 0,
                "present_type": "On-time Present",
                "jank_type": "None",
                "process_name": "com.demo",
            },
        ],
        "present_type_stats": [
            {"present_type": "Late Present", "cnt": 1},
            {"present_type": "On-time Present", "cnt": 1},
        ],
        "slow_renders": [{"dur_ms": 12.0}, {"dur_ms": 18.0}],
    }

    stats = summarize_frame_timeline(sql_results, target_frame_time_ms=16.67, severe_consecutive=2)

    assert stats["total_frames"] == 2
    assert stats["jank_frames"] == 1
    assert stats["late_present_frames"] == 1
    assert stats["p95_frame_time_ms"] >= 15.0


def test_frame_window_diagnostics_build_top_jank_frames():
    frames = [
        {
            "frame_id": 7,
            "actual_ts": 100,
            "actual_dur": 21_000_000,
            "expected_dur": 16_670_000,
            "overrun_ms": 4.3,
            "present_type": "Dropped Frame",
            "jank_type": "App Deadline Missed",
            "layer_name": "com.demo/.MainActivity#42",
            "process_name": "com.demo",
            "upid": 42,
        }
    ]
    top_frames = build_top_jank_frames(
        frames,
        call_stacks=[
            {
                "slice_id": 1,
                "ts": 100,
                "dur": 8_000_000,
                "dur_ms": 8.0,
                "slice_name": "inflate",
                "depth": 0,
                "parent_id": None,
                "thread_name": "main",
                "upid": 42,
            }
        ],
        thread_states=[
            {
                "ts": 100,
                "dur": 5_000_000,
                "thread_name": "main",
                "state": "Running",
                "io_wait": 0,
                "upid": 42,
            }
        ],
        cpu_freq_events=[{"ts": 100, "cpu": 0, "freq_khz": 1200000}],
        thread_cpu_scheduling=[{"ts": 100, "dur": 4_000_000, "cpu": 0, "upid": 42}],
        top_n=1,
    )

    assert top_frames[0]["bottleneck"].startswith("inflate")
    assert top_frames[0]["thread_states"][0]["thread"] == "main"
    assert top_frames[0]["thread_cpu_scheduling"]["CPU_0"] == 4.0


def test_frame_timing_diagnostics_fall_back_to_atrace_rows():
    sql_results = {
        "atrace_draw_frames": [
            {
                "frame_id": 9,
                "actual_ts": 123,
                "actual_dur": 20_000_000,
                "dur_ms": 20.0,
                "slice_name": "draw-VRI[0]",
                "process_name": "com.demo",
                "upid": 1,
                "pid": 2,
            }
        ],
        "atrace_traversal_frames": [],
        "atrace_call_stack": [{"slice_id": 1}],
    }

    frames = get_unified_frame_timeline(sql_results, target_frame_time_ms=16.67)

    assert frames[0]["present_type"] == "Late Present"
    assert sql_results["jank_frame_call_stack"] == sql_results["atrace_call_stack"]
