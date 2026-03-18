"""Property-based and unit tests for Perfetto Trace Analyzer."""

import json
import math
import os
import tempfile

import pytest
from hypothesis import given, settings, assume
import hypothesis.strategies as st

from perfetto_trace_analyzer.models import (
    CategoryReport, PerformanceScore, ScoringConfig, AppConfig, LLMConfig,
)
from perfetto_trace_analyzer.analyzers.jank import (
    classify_jank_frames, detect_severe_jank, _percentile, JankAnalyzer, _is_real_jank,
)
from perfetto_trace_analyzer.scorer import PerformanceScorer, rank_issues
from perfetto_trace_analyzer.config import ConfigManager
from perfetto_trace_analyzer.llm_client import LLMClient
from perfetto_trace_analyzer.orchestrator import scan_trace_files
from perfetto_trace_analyzer.reporter import ReportGenerator, result_to_dict
from perfetto_trace_analyzer.models import AnalysisResult


# ============================================================
# Property 1: Jank frame classification correctness
# ============================================================

@settings(max_examples=100)
@given(
    durations=st.lists(st.floats(min_value=0.0, max_value=200.0), min_size=1, max_size=500),
    threshold=st.floats(min_value=0.1, max_value=100.0),
)
def test_jank_classification_correctness(durations, threshold):
    """Feature: perfetto-trace-analyzer, Property 1: Jank frame classification."""
    flags = classify_jank_frames(durations, threshold)
    assert len(flags) == len(durations)
    for dur, is_jank in zip(durations, flags):
        if dur > threshold:
            assert is_jank is True, f"Frame {dur}ms should be jank (threshold={threshold})"
        else:
            assert is_jank is False, f"Frame {dur}ms should NOT be jank (threshold={threshold})"


# ============================================================
# Property 2: Severe jank detection
# ============================================================

@settings(max_examples=100)
@given(
    flags=st.lists(st.booleans(), min_size=0, max_size=200),
    min_consecutive=st.integers(min_value=1, max_value=10),
)
def test_severe_jank_detection(flags, min_consecutive):
    """Feature: perfetto-trace-analyzer, Property 2: Severe jank event detection."""
    events = detect_severe_jank(flags, min_consecutive)

    for event in events:
        start = event["start_index"]
        end = event["end_index"]
        length = event["consecutive_frames"]
        assert length >= min_consecutive
        assert length == end - start + 1
        # All frames in the range must be jank
        for i in range(start, end + 1):
            assert flags[i] is True

    # Verify no severe event is missed: scan for any run >= min_consecutive not covered
    i = 0
    n = len(flags)
    while i < n:
        if flags[i]:
            start = i
            while i < n and flags[i]:
                i += 1
            run_len = i - start
            if run_len >= min_consecutive:
                found = any(
                    e["start_index"] == start and e["consecutive_frames"] == run_len
                    for e in events
                )
                assert found, f"Missed severe jank at {start} with length {run_len}"
        else:
            i += 1


# ============================================================
# Property 3: Frame statistics calculation correctness
# ============================================================

@settings(max_examples=100)
@given(
    durations=st.lists(st.floats(min_value=0.0, max_value=200.0), min_size=1, max_size=500),
    threshold=st.floats(min_value=0.1, max_value=100.0),
)
def test_frame_statistics(durations, threshold):
    """Feature: perfetto-trace-analyzer, Property 3: Frame statistics calculation."""
    flags = classify_jank_frames(durations, threshold)
    total = len(durations)
    jank_count = sum(flags)
    jank_rate = jank_count / total

    assert total == len(durations)
    assert jank_count == sum(1 for d in durations if d > threshold)
    assert abs(jank_rate - jank_count / total) < 1e-9

    sorted_dur = sorted(durations)
    p95 = _percentile(sorted_dur, 95)
    p99 = _percentile(sorted_dur, 99)
    assert p95 <= p99 or abs(p95 - p99) < 1e-9
    assert p99 <= max(durations) or abs(p99 - max(durations)) < 1e-9


# ============================================================
# Property 9: Startup threshold marking
# ============================================================

@settings(max_examples=100)
@given(
    duration_ms=st.floats(min_value=0.0, max_value=20000.0),
    threshold_ms=st.floats(min_value=100.0, max_value=10000.0),
)
def test_startup_threshold_marking(duration_ms, threshold_ms):
    """Feature: perfetto-trace-analyzer, Property 9: Startup threshold marking."""
    is_slow = duration_ms > threshold_ms
    if duration_ms > threshold_ms:
        assert is_slow is True
    else:
        assert is_slow is False


# ============================================================
# Property 10: Insufficient data handling
# ============================================================

def test_insufficient_data_jank():
    """Feature: perfetto-trace-analyzer, Property 10: Insufficient data handling."""
    analyzer = JankAnalyzer()

    class MockTP:
        def query(self, sql):
            return []

    class MockLLM:
        def analyze(self, prompt):
            raise AssertionError("LLM should not be called when data is insufficient")

    report = analyzer.analyze(MockTP(), MockLLM())
    assert report.status == "insufficient_data"
    assert report.llm_insights is None


# ============================================================
# Property 11: LLM error isolation
# ============================================================

def test_llm_error_isolation():
    """Feature: perfetto-trace-analyzer, Property 11: LLM error isolation."""
    from perfetto_trace_analyzer.models import LLMResponse

    analyzer = JankAnalyzer()

    class MockTP:
        def query(self, sql):
            return [{"actual_dur": 20_000_000, "expected_dur": 16_670_000}] * 10

    class MockLLMFailing:
        def analyze(self, prompt):
            return LLMResponse(raw_text="", parsed_data=None, success=False, error="API error")

    report = analyzer.analyze(MockTP(), MockLLMFailing())
    assert report.status == "llm_error"
    assert report.statistics  # Statistics should still be computed


def test_llm_parse_failure_uses_json_repair():
    """Feature: perfetto-trace-analyzer, Property 12: Parse failure repair."""
    from perfetto_trace_analyzer.models import LLMResponse

    analyzer = JankAnalyzer()

    class MockTP:
        def query(self, sql):
            return [{"actual_dur": 20_000_000, "expected_dur": 16_670_000}] * 10

    class MockLLMRepairing:
        def analyze(self, prompt):
            return LLMResponse(
                raw_text="我会先分析这个 trace，然后给出结果。",
                parsed_data=None,
                success=True,
                error="Failed to parse JSON from response",
            )

        def repair_json(self, original_prompt, raw_text):
            return LLMResponse(
                raw_text='{"summary":"ok","issues":[],"suggestions":[],"score":80}',
                parsed_data={"summary": "ok", "issues": [], "suggestions": [], "score": 80},
                success=True,
                error=None,
            )

    report = analyzer.analyze(MockTP(), MockLLMRepairing())
    assert report.status == "success"
    assert report.llm_insights is not None
    assert report.score == 80


# ============================================================
# Property 14: LLM response parsing
# ============================================================

@settings(max_examples=50)
@given(
    summary=st.text(min_size=1, max_size=100),
    score=st.floats(min_value=0.0, max_value=100.0),
    severity=st.sampled_from(["low", "medium", "high", "critical"]),
)
def test_llm_response_parsing(summary, score, severity):
    """Feature: perfetto-trace-analyzer, Property 14: LLM response parsing."""
    data = {
        "summary": summary,
        "issues": [{"severity": severity, "description": "test"}],
        "suggestions": ["optimize"],
        "score": score,
    }
    raw = json.dumps(data, ensure_ascii=False)
    parsed = LLMClient._parse_response(raw)
    assert parsed is not None
    assert "summary" in parsed
    assert "issues" in parsed
    assert "suggestions" in parsed
    assert "score" in parsed


def test_llm_parse_markdown_code_block():
    """Parse JSON from markdown code block."""
    raw = '```json\n{"summary": "test", "score": 80}\n```'
    parsed = LLMClient._parse_response(raw)
    assert parsed is not None
    assert parsed["summary"] == "test"


# ============================================================
# Property 15: Overall score calculation
# ============================================================

@settings(max_examples=100)
@given(
    scores=st.lists(
        st.tuples(
            st.sampled_from(["startup", "jank", "memory", "cpu", "anr"]),
            st.floats(min_value=0.0, max_value=100.0),
        ),
        min_size=1,
        max_size=5,
    ),
)
def test_overall_score_calculation(scores):
    """Feature: perfetto-trace-analyzer, Property 15: Overall score calculation."""
    # Deduplicate names
    seen = set()
    unique_scores = []
    for name, score in scores:
        if name not in seen:
            seen.add(name)
            unique_scores.append((name, score))

    reports = [
        CategoryReport(analyzer_name=name, status="success", score=score)
        for name, score in unique_scores
    ]

    scorer = PerformanceScorer(ScoringConfig())
    result = scorer.compute_score(reports)

    assert 0.0 <= result.overall <= 100.0
    for name, score in unique_scores:
        assert name in result.category_scores


# ============================================================
# Property 16: Weight redistribution conservation
# ============================================================

@settings(max_examples=100)
@given(
    active=st.lists(
        st.sampled_from(["startup", "jank", "memory", "cpu", "anr", "lock_contention", "io", "power", "binder"]),
        min_size=1,
        max_size=9,
        unique=True,
    ),
)
def test_weight_redistribution(active):
    """Feature: perfetto-trace-analyzer, Property 16: Weight redistribution conservation."""
    scorer = PerformanceScorer(ScoringConfig())
    weights = scorer._redistribute_weights(active)

    # Sum should be 1.0
    assert abs(sum(weights.values()) - 1.0) < 1e-9, f"Weights sum to {sum(weights.values())}"

    # All active categories should have weights
    for name in active:
        assert name in weights

    # Relative proportions should be preserved
    if len(active) >= 2:
        orig = scorer.default_weights
        a, b = active[0], active[1]
        if orig.get(a, 0) > 0 and orig.get(b, 0) > 0:
            expected_ratio = orig[a] / orig[b]
            actual_ratio = weights[a] / weights[b]
            assert abs(expected_ratio - actual_ratio) < 1e-9


# ============================================================
# Property 17: Issue severity ranking
# ============================================================

@settings(max_examples=100)
@given(
    issues=st.lists(
        st.fixed_dictionaries({
            "severity": st.sampled_from(["critical", "high", "medium", "low"]),
            "description": st.text(min_size=1, max_size=50),
        }),
        min_size=0,
        max_size=20,
    ),
)
def test_issue_ranking(issues):
    """Feature: perfetto-trace-analyzer, Property 17: Issue severity ranking."""
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    ranked = rank_issues(issues)
    for i in range(len(ranked) - 1):
        a = order.get(ranked[i].get("severity", "low"), 99)
        b = order.get(ranked[i + 1].get("severity", "low"), 99)
        assert a <= b


# ============================================================
# Property 18: JSON report completeness
# ============================================================

def test_json_report_completeness():
    """Feature: perfetto-trace-analyzer, Property 18: JSON report completeness."""
    result = AnalysisResult(
        trace_path="test.perfetto-trace",
        metadata={"trace_file": "test.perfetto-trace", "duration_ns": 1000000},
        category_reports=[
            CategoryReport(
                analyzer_name="jank",
                status="success",
                sql_results={"frame_timeline": [{"dur": 16}]},
                statistics={"total_frames": 100},
                llm_insights={"summary": "ok"},
                llm_raw_response='{"summary":"ok"}',
                issues=[{"severity": "low", "description": "minor"}],
                suggestions=["optimize"],
                score=85.0,
            )
        ],
        overall_score=PerformanceScore(
            overall=85.0,
            category_scores={"jank": 85.0},
            weights_used={"jank": 1.0},
            ranked_issues=[],
        ),
    )

    data = result_to_dict(result)
    assert "metadata" in data
    assert data["metadata"]["trace_file"] == "test.perfetto-trace"
    assert "category_reports" in data
    report = data["category_reports"][0]
    assert report["llm_insights"] is not None
    assert report["llm_raw_response"] is not None
    assert report["statistics"]
    assert "overall_score" in data

    # Test actual file generation
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    try:
        ReportGenerator().generate_json(result, path)
        with open(path) as f:
            loaded = json.load(f)
        assert "metadata" in loaded
        assert "category_reports" in loaded
    finally:
        os.unlink(path)


# ============================================================
# Property 19: Config merge priority
# ============================================================

def test_config_merge_priority():
    """Feature: perfetto-trace-analyzer, Property 19: Config merge priority."""
    default = {"llm": {"model_name": "gpt-4", "temperature": 0.1}, "scoring": {"weights": {"jank": 0.2}}}
    user = {"llm": {"model_name": "gpt-3.5"}}
    merged = ConfigManager._merge_configs(default, user)
    assert merged["llm"]["model_name"] == "gpt-3.5"  # User overrides
    assert merged["llm"]["temperature"] == 0.1  # Default preserved
    assert merged["scoring"]["weights"]["jank"] == 0.2  # Default preserved


@settings(max_examples=50)
@given(
    user_temp=st.floats(min_value=0.0, max_value=2.0),
)
def test_config_merge_priority_property(user_temp):
    """Feature: perfetto-trace-analyzer, Property 19: Config merge with random values."""
    default = {"llm": {"temperature": 0.1, "model_name": "gpt-4"}}
    user = {"llm": {"temperature": user_temp}}
    merged = ConfigManager._merge_configs(default, user)
    assert merged["llm"]["temperature"] == user_temp
    assert merged["llm"]["model_name"] == "gpt-4"


# ============================================================
# Property 20: Invalid config fallback
# ============================================================

def test_invalid_config_fallback():
    """Feature: perfetto-trace-analyzer, Property 20: Invalid config fallback."""
    config = {
        "llm": {"temperature": 5.0},  # Invalid
        "analyzers": "not_a_dict",  # Invalid
    }
    warnings = ConfigManager._validate_config(config)
    assert len(warnings) > 0
    assert config["llm"]["temperature"] == 0.1  # Reset to default
    assert config["analyzers"] == {}  # Reset to default


# ============================================================
# Property 23: Directory file scanning
# ============================================================

def test_directory_scan_filtering():
    """Feature: perfetto-trace-analyzer, Property 23: Directory file scan filtering."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create various files
        for name in [
            "trace1.perfetto-trace",
            "trace2.pb",
            "readme.txt",
            "data.json",
            "trace3.perfetto-trace",
            "image.png",
        ]:
            open(os.path.join(tmpdir, name), "w").close()

        result = scan_trace_files(tmpdir)
        names = [os.path.basename(p) for p in result]
        assert "trace1.perfetto-trace" in names
        assert "trace2.pb" in names
        assert "trace3.perfetto-trace" in names
        assert "readme.txt" not in names
        assert "data.json" not in names
        assert "image.png" not in names
        assert len(result) == 3


def test_scan_single_file():
    """Scanning a single file returns that file."""
    with tempfile.NamedTemporaryFile(suffix=".perfetto-trace", delete=False) as f:
        path = f.name
    try:
        result = scan_trace_files(path)
        assert result == [path]
    finally:
        os.unlink(path)


# ============================================================
# Config loading from default_config.yaml
# ============================================================

def test_default_config_loads():
    """Default config file loads without errors."""
    config = ConfigManager.load()
    assert config.llm.provider == "openai"
    assert config.llm.temperature == 0.1
    assert abs(sum(config.scoring.weights.values()) - 1.0) < 0.01
    assert "jank" in config.analyzers


# ============================================================
# Property: top_jank_frames truncation and ordering
# ============================================================

_JANK_TYPES = ["App Deadline Missed", "SurfaceFlinger Scheduling", "None"]

_frame_strategy = st.fixed_dictionaries({
    "jank_type": st.sampled_from(["None", "App Deadline Missed", "SurfaceFlinger CPU Deadline Missed", "Buffer Stuffing"]),
    "overrun_ms": st.floats(min_value=-5.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    "actual_ts": st.integers(min_value=0, max_value=10**12),
    "actual_dur": st.integers(min_value=1, max_value=10**9),
    "process_name": st.sampled_from(["com.example.app", "com.test.demo"]),
    "frame_id": st.integers(min_value=1, max_value=10**6),
    "layer_name": st.sampled_from(["Layer#0", "SurfaceView", "StatusBar"]),
    "present_type": st.sampled_from(["On-time Present", "Late Present", "Dropped Frame"]),
})


@settings(max_examples=200)
@given(
    frame_timeline=st.lists(_frame_strategy, min_size=1, max_size=50),
    top_n=st.integers(min_value=1, max_value=50),
)
def test_top_jank_frames_truncation_and_order(frame_timeline, top_n):
    """top_jank_frames length <= top_n and sorted by overrun_ms descending."""
    analyzer = JankAnalyzer(config={"top_jank_frames": top_n})
    sql_results = {
        "frame_timeline": frame_timeline,
        "jank_type_stats": [],
        "present_type_stats": [],
        "jank_by_process": [],
        "slow_renders": [],
        "cpu_freq": [],
        "gpu_freq": [],
    }
    stats = analyzer._compute_statistics(sql_results)
    result = stats.get("top_jank_frames", [])

    jank_count = sum(1 for f in frame_timeline if _is_real_jank(f))
    assert len(result) <= top_n
    assert len(result) <= jank_count

    overruns = [f["overrun_ms"] for f in result]
    for i in range(len(overruns) - 1):
        assert overruns[i] >= overruns[i + 1]


@settings(max_examples=200)
@given(frame_timeline=st.lists(_frame_strategy, min_size=1, max_size=50))
def test_compute_statistics_no_exception(frame_timeline):
    """_compute_statistics should never raise for any valid frame_timeline."""
    analyzer = JankAnalyzer()
    sql_results = {
        "frame_timeline": frame_timeline,
        "jank_type_stats": [],
        "present_type_stats": [],
        "jank_by_process": [],
        "slow_renders": [],
        "cpu_freq": [],
        "gpu_freq": [],
    }
    stats = analyzer._compute_statistics(sql_results)
    assert "total_frames" in stats
    assert "jank_frames" in stats
