"""Catalog and metadata services for analyzers."""

from __future__ import annotations

from ..config import ConfigManager
from ..dependencies import AppServices, get_services
from ..registry import AnalyzerRegistry

_ANALYZER_METADATA: dict[str, dict[str, str]] = {
    "jank": {"label": "Jank", "description": "帧丢失 / 卡顿分析"},
    "startup": {"label": "Startup", "description": "应用启动耗时"},
    "anr": {"label": "ANR", "description": "无响应检测"},
    "memory": {"label": "Memory", "description": "内存泄漏 / 占用"},
    "binder": {"label": "Binder", "description": "IPC 延迟"},
}
_PREFERRED_ORDER = ["jank", "startup", "anr", "memory", "binder"]


class CatalogService:
    """Returns stable analyzer metadata for the frontend."""

    def __init__(self, services: AppServices | None = None):
        self.services = services or get_services()

    def list_analyzers(self) -> list[dict[str, str]]:
        config = self.services.state_manager.config or ConfigManager.load()
        registry = AnalyzerRegistry()
        registry.auto_discover(config.analyzers)

        order_index = {name: idx for idx, name in enumerate(_PREFERRED_ORDER)}
        names = sorted(
            registry.available_names,
            key=lambda name: (order_index.get(name, len(_PREFERRED_ORDER)), name),
        )

        analyzers: list[dict[str, str]] = []
        for name in names:
            metadata = _ANALYZER_METADATA.get(name, {})
            analyzers.append(
                {
                    "id": name,
                    "label": metadata.get("label", name.title()),
                    "description": metadata.get("description", f"{name} analyzer"),
                }
            )
        return analyzers
