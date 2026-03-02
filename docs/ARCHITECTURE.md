# Architecture

This document describes the internal architecture of perfetto-ai.

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser                                   │
│                                                                  │
│  ┌─────────────────────┐      ┌──────────────────────────────┐  │
│  │   React Frontend    │      │      Perfetto UI iframe       │  │
│  │                     │      │  (perfetto.dev reverse proxy) │  │
│  │  Left panel:        │      │                              │  │
│  │  • ScoreBar         │      │  ← injected WebSocket script  │  │
│  │  • Analyzer tabs    │◄────►│    receives jump commands     │  │
│  │  • JankFrameList    │      │                              │  │
│  │  • AnalyzerPanel    │      └──────────────────────────────┘  │
│  └────────┬────────────┘                                        │
└───────────┼─────────────────────────────────────────────────────┘
            │ HTTP / WebSocket
            ▼
┌───────────────────────────────────────────────────────────────┐
│                     FastAPI Backend                            │
│                                                               │
│  POST /api/traces/upload  →  store file, return trace_id      │
│  GET  /api/status/{id}    →  poll analysis progress           │
│  GET  /api/traces/{id}    →  full analysis JSON               │
│  POST /api/jump           →  broadcast timestamp via WS       │
│  GET  /perfetto/*         →  reverse proxy to perfetto.dev    │
│                                                               │
│  ┌────────────────────────────────────────────────────────┐   │
│  │                    Orchestrator                         │   │
│  │                                                        │   │
│  │  for each registered Analyzer:                        │   │
│  │    1. run SQL queries via trace_processor              │   │
│  │    2. call LLM with structured prompt                  │   │
│  │    3. collect CategoryReport                           │   │
│  │  → compute overall score                               │   │
│  │  → deduplicate issues                                  │   │
│  └────────────────────────────────────────────────────────┘   │
└───────────────────────────────────────────────────────────────┘
```

---

## Backend

### Key Files

| File | Responsibility |
|------|---------------|
| `server.py` | FastAPI routes, WebSocket bridge, Perfetto UI reverse proxy |
| `orchestrator.py` | Coordinates all analyzers, aggregates results |
| `base_analyzer.py` | Abstract base class; defines `COMMON_SQL_TEMPLATES` |
| `analyzers/jank.py` | Jank frame detection and per-frame LLM analysis |
| `analyzers/startup.py` | App startup phase breakdown |
| `analyzers/anr.py` | ANR event detection and blocking analysis |
| `analyzers/memory.py` | Memory trend and GC pressure analysis |
| `analyzers/binder.py` | Binder IPC slow call analysis |
| `llm_client.py` | LLM API integration (configurable provider) |
| `reporter.py` | Builds final JSON report; deduplicates frames |
| `scorer.py` | Computes per-analyzer and overall performance scores |
| `models.py` | Pydantic data models shared across the stack |
| `config.py` | YAML configuration loader |

### Analyzer Pattern

Every analyzer:
1. Inherits `BaseAnalyzer`
2. Declares `name`, `sql_templates`, `prompt_template`
3. Implements `analyze(trace_path) -> CategoryReport`
4. Can access `COMMON_SQL_TEMPLATES` (CPU freq, thread state, Binder, GC, GPU, memory) automatically

### Jank Classification

Jank determination uses `present_type`, not `jank_type`:
- `Dropped Frame` → jank
- `Late Present` → jank
- `On-time Present` → **never** jank (even if `jank_type` is set — handles OEM frame interpolation)

### Data Flow

```
upload → trace_processor opens .perfetto-trace
       → SQL queries execute synchronously
       → results serialized to string context
       → LLM prompt constructed per analyzer
       → LLM response parsed to structured JSON
       → CategoryReport assembled
       → overall_score computed
       → result stored in memory cache (keyed by trace_id)
```

---

## Frontend

### Key Files

| File | Responsibility |
|------|---------------|
| `App.tsx` | Main layout: upload zone, left panel, Perfetto iframe, WebSocket |
| `api/client.ts` | Backend API calls + `jumpToTimestamp()` |
| `api/types.ts` | TypeScript interfaces matching backend Pydantic models |
| `components/ScoreBar.tsx` | Overall performance score display |
| `components/JankFrameList.tsx` | Jank frame list with click-to-jump and inline expand |
| `components/JankInsightsPanel.tsx` | AI insights (clusters, bottleneck type, summary) |
| `components/AnalyzerPanel.tsx` | Generic panel for startup/ANR/memory/binder results |
| `components/FrameDetailDrawer.tsx` | Right-side drawer with full LLM analysis for a frame |
| `components/IssueList.tsx` | Ranked issue list with severity indicators |
| `components/Splitter.tsx` | Drag-to-resize splitter between left/right panels |

### State Machine

```
idle → uploading → analyzing (polling) → done
                                       ↘ failed
```

### WebSocket Jump Protocol

1. User clicks jank frame
2. Frontend calls `POST /api/jump` with `{ts, dur, process_name, upid}`
3. Backend broadcasts via WebSocket to all connected Perfetto iframe clients
4. Injected script in iframe receives message and calls Perfetto's navigation API

---

## Configuration

`backend/config.yaml` (not committed — use `config.yaml.example`):

```yaml
llm:
  provider: openai          # openai | anthropic | gemini
  api_key: "..."
  model: "gpt-4o"
  temperature: 0.2

server:
  host: "0.0.0.0"
  port: 8000
  upload_dir: "uploads/"

analysis:
  max_jank_frames: 50       # frames to send to LLM
  jank_threshold_ms: 16.67  # ms per frame at 60fps
```

---

## Adding a New Analyzer

See [CONTRIBUTING.md](CONTRIBUTING.md#adding-a-new-analyzer) for the full step-by-step guide.
