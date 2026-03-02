"""Analyzer auto-discovery and registration."""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

from .base_analyzer import BaseAnalyzer

logger = logging.getLogger(__name__)


class AnalyzerRegistry:
    """Discovers and manages pluggable analyzer modules."""

    def __init__(self):
        self._analyzers: dict[str, BaseAnalyzer] = {}

    def auto_discover(self, analyzer_configs: dict[str, dict[str, Any]] | None = None) -> None:
        """Auto-discover all BaseAnalyzer subclasses in the analyzers package."""
        from . import analyzers as analyzers_pkg

        configs = analyzer_configs or {}

        for _importer, module_name, _is_pkg in pkgutil.iter_modules(analyzers_pkg.__path__):
            try:
                importlib.import_module(f".analyzers.{module_name}", package=__package__)
            except Exception as e:
                logger.warning("Failed to import analyzer module %s: %s", module_name, e)

        for cls in _all_subclasses(BaseAnalyzer):
            try:
                cfg = configs.get(cls.__name__.lower().replace("analyzer", ""), {})
                instance = cls(config=cfg) if cfg else cls()
                self.register(instance)
            except Exception as e:
                logger.warning("Failed to instantiate %s: %s", cls.__name__, e)

    def register(self, analyzer: BaseAnalyzer) -> None:
        """Register an analyzer instance."""
        self._analyzers[analyzer.name] = analyzer
        logger.debug("Registered analyzer: %s", analyzer.name)

    def get_analyzers(self, names: list[str] | None = None) -> list[BaseAnalyzer]:
        """Get specified analyzers or all registered analyzers."""
        if names is None:
            return list(self._analyzers.values())
        result = []
        for name in names:
            if name in self._analyzers:
                result.append(self._analyzers[name])
            else:
                logger.warning("Unknown analyzer: %s", name)
        return result

    @property
    def available_names(self) -> list[str]:
        return list(self._analyzers.keys())


def _all_subclasses(cls: type) -> set[type]:
    """Recursively find all concrete subclasses of a class."""
    result = set()
    for sub in cls.__subclasses__():
        if not getattr(sub, "__abstractmethods__", None):
            result.add(sub)
        result.update(_all_subclasses(sub))
    return result
