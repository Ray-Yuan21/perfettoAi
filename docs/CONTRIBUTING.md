# Contributing to perfetto-ai

Thank you for your interest in contributing! This guide focuses on the most impactful contribution path: **adding a new Analyzer**.

---

## Development Setup

```bash
# Backend
conda create -n perfetto python=3.11 -y
conda activate perfetto
cd backend && pip install -e ".[dev]"

# Frontend
cd frontend && npm install
```

Run tests:
```bash
cd backend && conda run -n perfetto pytest tests/ -v
```

---

## Adding a New Analyzer

All analyzers follow the same pattern. Here is the complete checklist.

### 1. Create the analyzer file

```
backend/perfetto_trace_analyzer/analyzers/your_analyzer.py
```

Inherit from `BaseAnalyzer`:

```python
from ..base_analyzer import BaseAnalyzer
from ..models import CategoryReport

class YourAnalyzer(BaseAnalyzer):
    name = "your_analyzer"

    sql_templates = {
        # Your analyzer-specific queries
        "key_events": """
            SELECT ts, dur, name
            FROM slice
            WHERE name LIKE 'YourEvent%'
            LIMIT 200
        """,
        # Common queries (CPU freq, thread state, Binder, GC, GPU, memory)
        # are automatically available via COMMON_SQL_TEMPLATES in base_analyzer.py
    }

    prompt_template = """
    You are an Android performance expert. Analyze the following data:

    {key_events}

    Respond with JSON:
    {
      "summary": "...",
      "your_metric": 42,
      "issues": [...],
      "suggestions": [...],
      "evidence_sql": [
        {"label": "...", "sql": "SELECT ...", "conclusion": "..."}
      ]
    }
    """

    def analyze(self, trace_path: str) -> CategoryReport:
        # 1. Run SQL queries
        results = self._run_sql_queries(trace_path)

        # 2. Check for sufficient data
        if not results.get("key_events"):
            return self._insufficient_data_report()

        # 3. Call LLM
        llm_response = self._call_llm(results)

        # 4. Build and return CategoryReport
        return self._build_report(llm_response, statistics={...})
```

### 2. Register the analyzer

Add to `backend/perfetto_trace_analyzer/analyzers/__init__.py`:

```python
from .your_analyzer import YourAnalyzer
__all__ = [..., "YourAnalyzer"]
```

Add to `backend/perfetto_trace_analyzer/orchestrator.py`:

```python
from .analyzers import YourAnalyzer
# add YourAnalyzer() to the analyzers list
```

### 3. Add frontend types (if needed)

If your analyzer returns custom metrics, add types to `frontend/src/api/types.ts`.

### 4. Update AnalyzerPanel key metrics

In `frontend/src/components/AnalyzerPanel.tsx`, add a case in `buildKeyMetrics()`:

```ts
case "your_analyzer": {
  const myMetric = insights.your_metric ?? stats.your_metric;
  return myMetric != null
    ? [{ label: "My Metric", value: String(myMetric) }]
    : [];
}
```

### 5. Write tests

Add `backend/tests/test_your_analyzer.py`. At minimum test:
- `analyze()` returns `CategoryReport` with correct `analyzer_name`
- `analyze()` handles empty trace gracefully (returns `insufficient_data`)

### 6. Update documentation

- Add a row to the Analyzers table in `README.md` and `README_CN.md`
- Add an entry to `CHANGELOG.md`

---

## Common SQL Templates

The following queries are available to all analyzers via `COMMON_SQL_TEMPLATES` in `base_analyzer.py`. Use them in your `sql_templates` by referencing the key:

| Key | What it queries |
|-----|----------------|
| `cpu_freq` | CPU frequency per core over time |
| `gpu_freq` | GPU frequency counter |
| `thread_state` | Per-thread running/sleeping/blocked state |
| `binder_transactions` | Binder IPC call duration and interfaces |
| `gc_events` | GC start/stop events with STW duration |
| `gpu_render_stages` | GPU render stage slices |
| `memory_counters` | RSS/PSS memory counters per process |

---

## Pull Request Guidelines

- Keep PRs focused — one analyzer or one feature per PR
- Include test coverage for new code
- Run `npm run lint` and `pytest tests/` before submitting
- Fill in the PR template with a brief description and test plan
