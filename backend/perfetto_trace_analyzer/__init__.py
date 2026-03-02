"""Perfetto Trace Analyzer - LLM-driven automated Android performance analysis tool."""

__version__ = "0.1.0"

from .models import (
    AnalysisResult,
    AppConfig,
    CategoryReport,
    LLMConfig,
    LLMResponse,
    PerformanceScore,
    ScoringConfig,
)
from .base_analyzer import BaseAnalyzer
from .config import ConfigManager
from .llm_client import LLMClient
from .orchestrator import Orchestrator, scan_trace_files
from .registry import AnalyzerRegistry
from .reporter import ReportGenerator
from .scorer import PerformanceScorer
from .trace_processor import TraceProcessorConnection
