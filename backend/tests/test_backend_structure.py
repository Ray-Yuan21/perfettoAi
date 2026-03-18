import threading
import time
from types import SimpleNamespace

from perfetto_trace_analyzer.models import AnalysisResult, CategoryReport
from perfetto_trace_analyzer.services.catalog_service import CatalogService
from perfetto_trace_analyzer.base_analyzer import BaseAnalyzer
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
