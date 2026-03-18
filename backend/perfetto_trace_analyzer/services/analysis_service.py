"""Application service for trace analysis workflows."""

from __future__ import annotations

import asyncio
import logging
import os

from ..config import ConfigManager
from ..dependencies import AppServices, get_services
from ..models import AppConfig
from ..orchestrator import Orchestrator, scan_trace_files

logger = logging.getLogger(__name__)


class AnalysisService:
    """Runs trace analysis and updates shared application state."""

    def __init__(self, services: AppServices | None = None):
        self.services = services or get_services()

    def schedule_analysis(
        self,
        trace_id: str,
        trace_path: str,
        analyzer_names: list[str] | None = None,
    ) -> None:
        asyncio.create_task(self.analyze_trace(trace_id, trace_path, analyzer_names))

    async def analyze_trace(
        self,
        trace_id: str,
        trace_path: str,
        analyzer_names: list[str] | None = None,
    ) -> None:
        state_manager = self.services.state_manager
        try:
            state_manager.set_progress(trace_id, "Loading config...")
            config = state_manager.config or ConfigManager.load()
            orchestrator = self._build_orchestrator(config)

            state_manager.set_progress(trace_id, "Running SQL queries + LLM analysis...")
            result = await asyncio.to_thread(
                orchestrator.analyze,
                trace_path,
                analyzer_names,
            )

            state_manager.set_result(trace_id, result, trace_path=trace_path)
            logger.info("Analysis complete for trace %s", trace_id)
        except Exception as e:
            state_manager.set_status(trace_id, "failed", f"Error: {e}")
            logger.error("Analysis failed for trace %s: %s", trace_id, e)

    def preload_traces(self, config: AppConfig, trace_path: str) -> None:
        state_manager = self.services.state_manager
        orchestrator = self._build_orchestrator(config)
        for path in scan_trace_files(trace_path):
            logger.info("Pre-analyzing: %s", path)
            result = orchestrator.analyze(path)
            trace_id = os.path.basename(path).replace(".", "_")
            state_manager.set_result(trace_id, result, trace_path=path)

    def _build_orchestrator(self, config: AppConfig) -> Orchestrator:
        return Orchestrator(
            config,
            trace_processor_pool=self.services.trace_processor_pool,
        )
