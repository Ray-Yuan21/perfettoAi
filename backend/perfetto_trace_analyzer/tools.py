"""Perfetto UI tools for LLM function calling.

Framework-agnostic tool definitions that can be used by any LLM chat system.
Each tool has a JSON Schema for parameters and can be dispatched by name.

Usage in a chat agent::

    from perfetto_trace_analyzer.tools import registry

    # Get tool schemas (for LLM function calling / tool_use)
    schemas = registry.openai_schemas()   # OpenAI format
    schemas = registry.claude_schemas()   # Claude format

    # Dispatch a tool call from LLM response
    result = registry.call("perfetto_jump", ts=1234567890, dur=16000000)

    # Bind a TraceProcessorConnection for query_trace tool
    bound = registry.with_context(tp)
    result = bound.call_json("query_trace", {"sql": "SELECT ...", "limit": 50})
"""

from __future__ import annotations

import inspect
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any, Callable, get_type_hints

logger = logging.getLogger(__name__)

# ─── Tool Registry ───────────────────────────────────────────

# Python type → JSON Schema type
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


class _ToolDef:
    """A registered tool: function + metadata."""

    __slots__ = ("fn", "name", "description", "params_schema", "required")

    def __init__(self, fn: Callable, name: str, description: str,
                 params_schema: dict[str, Any], required: list[str]):
        self.fn = fn
        self.name = name
        self.description = description
        self.params_schema = params_schema
        self.required = required

    def __call__(self, **kwargs: Any) -> str:
        return self.fn(**kwargs)


class ToolRegistry:
    """Collects tools and exports schemas for LLM function calling."""

    def __init__(self, server_url: str = "http://localhost:8000"):
        self._tools: dict[str, _ToolDef] = {}
        self.server_url = server_url

    # ── decorator ──

    def tool(self, fn: Callable | None = None, *, name: str | None = None):
        """Register a function as a callable tool.

        The function's docstring becomes the tool description.
        Parameter types and defaults are inferred from type hints and signature.
        """
        def decorator(f: Callable) -> _ToolDef:
            tool_name = name or f.__name__
            description = (f.__doc__ or "").strip().split("\n\n")[0]  # first paragraph

            sig = inspect.signature(f)
            hints = get_type_hints(f)
            properties: dict[str, Any] = {}
            required: list[str] = []

            for pname, param in sig.parameters.items():
                if pname == "self":
                    continue
                ptype = hints.get(pname, str)
                prop: dict[str, Any] = {"type": _TYPE_MAP.get(ptype, "string")}

                # Extract per-param description from docstring Args section
                param_doc = _extract_param_doc(f.__doc__ or "", pname)
                if param_doc:
                    prop["description"] = param_doc

                if param.default is not inspect.Parameter.empty:
                    prop["default"] = param.default
                else:
                    required.append(pname)

                properties[pname] = prop

            schema = {"type": "object", "properties": properties, "required": required}
            td = _ToolDef(f, tool_name, description, schema, required)
            self._tools[tool_name] = td
            return td

        if fn is not None:
            return decorator(fn)
        return decorator

    # ── dispatch ──

    def call(self, name: str, **kwargs: Any) -> str:
        """Call a tool by name. Returns the string result."""
        td = self._tools.get(name)
        if not td:
            available = ", ".join(self._tools)
            return f"Unknown tool '{name}'. Available: {available}"
        return td.fn(**kwargs)

    def call_json(self, name: str, arguments: dict[str, Any] | str) -> str:
        """Call a tool from an LLM response (arguments may be JSON string)."""
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        return self.call(name, **arguments)

    # ── schema export ──

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    def openai_schemas(self) -> list[dict[str, Any]]:
        """Export tool definitions in OpenAI function calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": td.name,
                    "description": td.description,
                    "parameters": td.params_schema,
                },
            }
            for td in self._tools.values()
        ]

    def claude_schemas(self) -> list[dict[str, Any]]:
        """Export tool definitions in Claude / Anthropic tool_use format."""
        return [
            {
                "name": td.name,
                "description": td.description,
                "input_schema": td.params_schema,
            }
            for td in self._tools.values()
        ]

    def with_context(self, tp: Any) -> "BoundToolRegistry":
        """Return a BoundToolRegistry with tp injected for query_trace calls."""
        return BoundToolRegistry(self, tp)


class BoundToolRegistry:
    """A ToolRegistry bound to a specific TraceProcessorConnection for one analysis session.

    Created via registry.with_context(tp). Provides the same schema export methods
    as ToolRegistry, but query_trace calls are routed to the bound tp instance.
    """

    def __init__(self, base: "ToolRegistry", tp: Any):
        self._base = base
        self._tp = tp

    def call_json(self, name: str, arguments: dict[str, Any] | str) -> str:
        """Dispatch a tool call, injecting tp for query_trace."""
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if name == "query_trace":
            return _run_query_trace(self._tp, **arguments)
        return self._base.call_json(name, arguments)

    def openai_schemas(self) -> list[dict[str, Any]]:
        return self._base.openai_schemas()

    def claude_schemas(self) -> list[dict[str, Any]]:
        return self._base.claude_schemas()


def _extract_param_doc(docstring: str, param: str) -> str:
    """Extract a parameter's description from Google-style docstring."""
    in_args = False
    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("args:"):
            in_args = True
            continue
        if in_args:
            if stripped.startswith(f"{param}:") or stripped.startswith(f"{param} :"):
                return stripped.split(":", 1)[1].strip()
            if stripped and not stripped.startswith((" ", "\t")) and ":" in stripped:
                # Another section (Returns:, etc.)
                in_args = False
    return ""


# ─── HTTP helpers ─────────────────────────────────────────────

def _post_json(url: str, data: dict, timeout: int = 5) -> dict:
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _get_json(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


# ─── Global registry + tool definitions ──────────────────────

registry = ToolRegistry()


@registry.tool
def perfetto_jump(ts: int, dur: int = 0) -> str:
    """Jump to a timestamp in Perfetto UI with area selection (box highlight).

    Moves the viewport to center on the time range and creates an
    M-key style selection box highlighting the duration.

    Args:
        ts: Start timestamp in nanoseconds.
        dur: Duration in nanoseconds (0 for default window).

    Returns:
        Status message.
    """
    url = f"{registry.server_url}/api/jump"
    try:
        result = _post_json(url, {"ts": ts, "dur": dur})
        clients = result.get("clients", 0)
        if clients == 0:
            return "Jump sent but no Perfetto UI connected. Open the UI and load a trace first."
        return f"Jumped to {ts / 1e6:.1f}ms (dur: {dur / 1e6:.2f}ms) in {clients} UI instance(s)."
    except urllib.error.URLError as e:
        return f"Server unreachable ({registry.server_url}): {e}"


@registry.tool
def perfetto_list_traces() -> str:
    """List all loaded traces in the analyzer server.

    Returns:
        Trace list with id, filename, and score.
    """
    try:
        data = _get_json(f"{registry.server_url}/api/traces")
        traces = data.get("traces", [])
        if not traces:
            return "No traces loaded."
        lines = []
        for t in traces:
            score = t.get("score")
            s = f" (score: {score})" if score is not None else ""
            lines.append(f"  {t['id']}: {t['filename']}{s}")
        return f"Loaded traces ({len(traces)}):\n" + "\n".join(lines)
    except urllib.error.URLError as e:
        return f"Server unreachable: {e}"


@registry.tool
def perfetto_get_jank_frames(trace_id: str, top_n: int = 10) -> str:
    """Get the top N worst jank frames from a trace.

    Use the returned ts/dur values with perfetto_jump to navigate.

    Args:
        trace_id: Trace ID from perfetto_list_traces.
        top_n: Number of worst frames to return.

    Returns:
        Jank frames sorted by duration (worst first).
    """
    try:
        data = _get_json(f"{registry.server_url}/api/traces/{trace_id}")
        frames = data.get("jank_frames", [])
        if not frames:
            return "No jank frames found."
        top = frames[:top_n]
        lines = []
        for i, f in enumerate(top, 1):
            info = " | ".join(filter(None, [
                f.get("jank_type"), f.get("present_type"),
                f.get("layer_name"), f.get("process_name"),
            ]))
            lines.append(
                f"  {i}. ts={f['ts']} ({f['ts']/1e6:.1f}ms) "
                f"dur={f['dur']}ns ({f['dur_ms']}ms) — {info}"
            )
        return (
            f"Top {len(top)} jank frames (of {len(frames)} total):\n"
            + "\n".join(lines)
            + "\n\nUse perfetto_jump(ts, dur) to navigate to any frame."
        )
    except urllib.error.URLError as e:
        return f"Server unreachable: {e}"


# ─── query_trace: direct SQL access to TraceProcessorConnection ──────────────

_SQL_READONLY_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_MAX_LIMIT = 200


def _run_query_trace(tp: Any, sql: str, limit: int = 100) -> str:
    """Execute sql against tp, enforcing SELECT-only and row limit."""
    if not _SQL_READONLY_RE.match(sql):
        return "Error: only SELECT queries are allowed."
    limit = min(max(1, int(limit)), _MAX_LIMIT)
    # Inject LIMIT if not already present
    sql_stripped = sql.rstrip().rstrip(";")
    if not re.search(r"\bLIMIT\b", sql_stripped, re.IGNORECASE):
        sql_stripped = f"{sql_stripped} LIMIT {limit}"
    try:
        rows = tp.query(sql_stripped)
        if not rows:
            return "Query returned 0 rows."
        # Serialize to compact JSON
        result = json.dumps(rows[:limit], ensure_ascii=False, default=str)
        return f"{len(rows)} row(s):\n{result}"
    except Exception as e:
        return f"Query error: {e}"


@registry.tool
def query_trace(sql: str, limit: int = 100) -> str:
    """Execute a SQL query against the loaded Perfetto trace database.

    Use this tool to investigate specific performance issues in detail.
    Only SELECT queries are allowed. Results are capped to avoid context overflow.

    Useful queries:
    - Slice children: SELECT s.name, s.dur/1e6 as dur_ms FROM slice s WHERE s.parent_id=<id>
    - Binder calls: SELECT name, dur/1e6 as dur_ms FROM slice WHERE name LIKE 'binder%'
    - GC events: SELECT name, dur/1e6 as dur_ms FROM slice WHERE name LIKE '%GC%'
    - Thread state: SELECT state, dur/1e6 as dur_ms FROM thread_state WHERE utid=<utid>
    - Slices in time range: SELECT name, dur/1e6 FROM slice WHERE ts BETWEEN <ts> AND <ts+dur>

    Args:
        sql: SELECT query to execute against trace_processor.
        limit: Max rows to return (default 100, max 200).

    Returns:
        Query results as JSON rows, or an error message.
    """
    # This stub is replaced at runtime by BoundToolRegistry.call_json
    return "Error: query_trace requires a bound TraceProcessorConnection. Use registry.with_context(tp)."
