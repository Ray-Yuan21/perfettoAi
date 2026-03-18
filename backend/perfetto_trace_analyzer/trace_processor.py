"""Perfetto trace_processor connection management."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class TraceProcessorConnection:
    """Wraps Perfetto trace_processor for loading traces and executing SQL queries."""

    def __init__(self, trace_path: str):
        self.trace_path = trace_path
        self._tp = None

    def load(self) -> None:
        """Load the trace file and establish a queryable connection."""
        if not os.path.exists(self.trace_path):
            raise FileNotFoundError(f"Trace file not found: {self.trace_path}")

        try:
            from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

            config_kwargs = {}
            bin_path = os.environ.get("TRACE_PROCESSOR_SHELL_PATH")
            if bin_path and os.path.isfile(bin_path):
                config_kwargs["bin_path"] = bin_path

            self._tp = TraceProcessor(
                trace=self.trace_path,
                config=TraceProcessorConfig(**config_kwargs),
            )
        except Exception as e:
            error_msg = str(e).lower()
            if "proto" in error_msg or "format" in error_msg or "parse" in error_msg:
                raise ValueError(
                    f"Invalid Perfetto trace format: {self.trace_path}"
                ) from e
            raise

        logger.info("Loaded trace file: %s", self.trace_path)

    def query(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SQL query and return results as a list of dicts."""
        if self._tp is None:
            raise RuntimeError("Trace not loaded. Call load() first.")

        result = self._tp.query(sql)
        rows: list[dict[str, Any]] = []
        for row in result:
            rows.append({col: getattr(row, col) for col in result.column_names})
        return rows

    def get_metadata(self) -> dict[str, Any]:
        """Extract trace file metadata."""
        if self._tp is None:
            raise RuntimeError("Trace not loaded. Call load() first.")

        metadata: dict[str, Any] = {
            "trace_file": os.path.basename(self.trace_path),
            "trace_path": self.trace_path,
        }

        try:
            rows = self.query(
                "SELECT name, str_value, int_value FROM metadata"
            )
            for row in rows:
                name = row.get("name", "")
                value = row.get("str_value") or row.get("int_value")
                if name and value is not None:
                    metadata[name] = value
        except Exception as e:
            logger.warning("Failed to read metadata: %s", e)

        try:
            rows = self.query(
                "SELECT MIN(ts) as min_ts, MAX(ts) as max_ts FROM slice"
            )
            if rows:
                min_ts = rows[0].get("min_ts")
                max_ts = rows[0].get("max_ts")
                if min_ts is not None and max_ts is not None:
                    metadata["duration_ns"] = max_ts - min_ts
        except Exception as e:
            logger.warning("Failed to compute duration: %s", e)

        return metadata

    def close(self) -> None:
        """Close the trace processor connection."""
        if self._tp is not None:
            try:
                self._tp.close()
            except Exception:
                pass
            self._tp = None

    def __enter__(self):
        # We no longer auto-load here if we want the pool to control loading
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # We no longer auto-close here because the pool manages the lifecycle
        return False


class TraceProcessorPool:
    """Thread-safe LRU cache for TraceProcessor connections."""

    def __init__(self, max_size: int = 3):
        import collections
        import threading
        self.max_size = max_size
        self._pool: collections.OrderedDict[str, TraceProcessorConnection] = collections.OrderedDict()
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}

    def get_connection(self, trace_path: str) -> TraceProcessorConnection:
        """Get an existing connection from the pool, or create a new one."""
        while True:
            with self._lock:
                if trace_path in self._pool:
                    conn = self._pool.pop(trace_path)
                    self._pool[trace_path] = conn
                    logger.debug("Reusing pooled trace connection for: %s", trace_path)
                    return conn

                event = self._inflight.get(trace_path)
                if event is None:
                    import threading

                    event = threading.Event()
                    self._inflight[trace_path] = event
                    break

            event.wait()

        conn = TraceProcessorConnection(trace_path)
        load_error: Exception | None = None

        try:
            logger.info("Creating new trace connection for: %s", trace_path)
            conn.load()
        except Exception as exc:
            load_error = exc

        with self._lock:
            self._inflight.pop(trace_path, None)
            if load_error is None:
                if trace_path in self._pool:
                    existing = self._pool.pop(trace_path)
                    self._pool[trace_path] = existing
                    conn.close()
                    conn = existing
                else:
                    if len(self._pool) >= self.max_size:
                        oldest_path, oldest_conn = self._pool.popitem(last=False)
                        logger.info("Evicting trace connection from pool: %s", oldest_path)
                        try:
                            oldest_conn.close()
                        except Exception as e:
                            logger.warning("Error evicting connection %s: %s", oldest_path, e)
                    self._pool[trace_path] = conn

            event.set()

        if load_error is not None:
            raise load_error

        return conn
        
    def close_all(self):
        """Close all pooled connections."""
        with self._lock:
            for conn in self._pool.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._pool.clear()
