"""Core data models for Perfetto Trace Analyzer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMConfig:
    """LLM service configuration."""

    provider: str = "openai"
    api_endpoint: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4"
    api_key: str = ""
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout: int = 60
    max_retries: int = 1
    max_tool_turns: int = 5  # agentic loop max tool call rounds


@dataclass
class ScoringConfig:
    """Scoring weight configuration."""

    weights: dict[str, float] = field(default_factory=lambda: {
        "startup": 0.20,
        "jank": 0.20,
        "memory": 0.15,
        "cpu": 0.15,
        "anr": 0.15,
        "lock_contention": 0.05,
        "io": 0.05,
        "power": 0.025,
        "binder": 0.025,
    })


@dataclass
class AppConfig:
    """Application configuration."""

    llm: LLMConfig = field(default_factory=LLMConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    analyzers: dict[str, dict[str, Any]] = field(default_factory=dict)
    prompt_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Response from LLM service."""

    raw_text: str
    parsed_data: dict[str, Any] | None
    success: bool
    error: str | None = None


@dataclass
class CategoryReport:
    """Report for a single analysis category."""

    analyzer_name: str
    status: str  # "success" | "insufficient_data" | "llm_error" | "parse_error" | "error"
    sql_results: dict[str, Any] = field(default_factory=dict)
    statistics: dict[str, Any] = field(default_factory=dict)
    llm_insights: dict[str, Any] | None = None
    llm_raw_response: str | None = None
    issues: list[dict[str, Any]] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    score: float | None = None


@dataclass
class PerformanceScore:
    """Overall performance score."""

    overall: float  # 0-100
    category_scores: dict[str, float] = field(default_factory=dict)
    weights_used: dict[str, float] = field(default_factory=dict)
    ranked_issues: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AnalysisResult:
    """Complete analysis result for a trace file."""

    trace_path: str
    metadata: dict[str, Any] = field(default_factory=dict)
    category_reports: list[CategoryReport] = field(default_factory=list)
    overall_score: PerformanceScore | None = None
