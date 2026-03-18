# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Perfetto AI is an LLM-enhanced Android performance analysis system. It analyzes Perfetto trace files to identify jank (frame drops) and provides AI-driven root cause analysis. The system combines a FastAPI backend with a React frontend, embedding Perfetto UI via iframe for visualization.

## Development Commands

### Environment Setup
All Python commands must use the `perfetto` conda environment:
```bash
conda run -n perfetto <command>
```

### Backend
```bash
cd backend
conda run -n perfetto python run_server.py          # Start server on http://localhost:8000
conda run -n perfetto pip install -e ".[dev]"       # Install with dev dependencies
conda run -n perfetto pytest tests/                 # Run tests
conda run -n perfetto pytest tests/test_analyzer.py -k "test_name"  # Run single test
```

### Frontend
```bash
cd frontend
npm install
npm run dev      # Dev server (Vite)
npm run build    # Production build
npm run lint     # ESLint
```

## Architecture

```
User uploads trace → Backend stores file → Perfetto UI loads via iframe
                  → Async analysis starts (SQL queries + LLM)
                  → Frontend polls /api/status/{trace_id}
                  → Results displayed in left panel + right drawer
                  → Click jank frame → WebSocket → Perfetto UI jumps to timestamp
```

### Backend (`backend/perfetto_trace_analyzer/`)
- `base_analyzer.py` - Abstract base class for all analyzers; defines `COMMON_SQL_TEMPLATES` (CPU freq, GPU freq, thread state, Binder, GC, GPU render, memory) shared by all analyzers
- `server.py` - FastAPI server: upload, status polling, jump API, WebSocket bridge, Perfetto UI reverse proxy
- `orchestrator.py` - Analysis workflow coordination
- `analyzers/jank.py` - Jank detection with LLM-enhanced root cause analysis; uses `_is_real_jank()` for accurate jank classification based on `present_type` (not just `jank_type`)
- `llm_client.py` - LLM API integration
- `reporter.py` - Report generation (JSON/HTML); deduplicates frames by `display_frame_token:upid`
- `scorer.py` - Performance scoring; deduplicates issues by `severity:description`
- `models.py` - Pydantic data models
- `config.py` - YAML configuration management

### Frontend (`frontend/src/`)
- `App.tsx` - Main component: drag-drop upload, iframe embedding, analysis panel, frame detail drawer
- `api/client.ts` - Backend API client with `jumpToTimestamp()` for Perfetto navigation
- `api/types.ts` - TypeScript types matching backend models
- `components/JankFrameList.tsx` - Jank frame list with click-to-jump and inline expand (critical path, call tree)
- `components/JankInsightsPanel.tsx` - AI analysis insights display
- `components/FrameDetailDrawer.tsx` - Right-side drawer showing LLM analysis (bottleneck, flow description, evidence SQL)

### Key Data Flow
1. Upload: `POST /api/traces/upload` → returns `trace_id`
2. Status: `GET /api/traces/{trace_id}/status` → polling until "done"
3. Results: `GET /api/traces/{trace_id}` → full analysis JSON
4. Jump: `POST /api/jump` → broadcasts via WebSocket → Perfetto UI navigates

## Key Concepts

- **Jank Frame**: Determined by `present_type` — `Dropped Frame` and `Late Present` are real jank; `On-time Present` is never jank regardless of `jank_type` (handles OEM frame interpolation)
- **Call Tree**: Stack trace extracted from trace, converted to text for LLM analysis
- **Evidence SQL**: Each LLM frame analysis includes verifiable Perfetto SQL queries that users can run to validate conclusions
- **Common SQL Templates**: Shared queries in `base_analyzer.py` (CPU/GPU freq, thread state, Binder, GC, GPU render, memory) available to all analyzers
- **WebSocket Bridge**: Injected script in Perfetto UI iframe receives jump commands

## Adding a New Analyzer

1. Create `backend/perfetto_trace_analyzer/analyzers/your_analyzer.py`
2. Inherit from `BaseAnalyzer`, implement `name`, `sql_templates`, `prompt_template`, `analyze()`
3. Common SQL queries (CPU freq, thread state, etc.) are automatically available via `COMMON_SQL_TEMPLATES`
4. Export it from `analyzers/__init__.py` if you want it discoverable in the catalog
