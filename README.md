# perfetto-ai

**LLM-enhanced Android performance analysis powered by [Perfetto](https://perfetto.dev)**

perfetto-ai analyzes Perfetto trace files to automatically detect performance issues — jank frames, app startup slowdowns, ANRs, memory pressure, and Binder IPC bottlenecks — and provides AI-driven root cause analysis with verifiable SQL evidence.

---

## Features

- **Jank Detection** — identifies dropped and late frames using `present_type`, handles OEM frame interpolation correctly
- **Startup Analysis** — cold/warm start breakdown across Application.onCreate → Activity.onCreate → first frame
- **ANR Detection** — pinpoints main thread blocking cause (Binder, lock, IO, CPU)
- **Memory Analysis** — RSS/PSS trends, GC pressure, OOM proximity
- **Binder IPC Analysis** — slow IPC calls, main-thread synchronous blocking by interface
- **Perfetto UI Integration** — click any jank frame to jump directly to that timestamp in the embedded Perfetto UI
- **Evidence SQL** — every AI conclusion includes runnable Perfetto SQL queries for manual verification
- **Runtime LLM Configuration** — configure API endpoint, key, and model from the UI Settings panel; model list auto-discovered

---

## Quick Start (Docker)

Single container, one command:

```bash
# 1. Configure LLM credentials
cp .env.example .env
# Edit .env:
#   LLM_API_KEY=your-api-key
#   LLM_API_ENDPOINT=https://api.openai.com/v1
#   LLM_MODEL_NAME=gpt-4

# 2. Build and start
docker compose up --build -d

# 3. Open browser
open http://localhost:8000
```

> LLM settings can also be changed at runtime via the **Settings** button in the UI header — no restart needed.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_API_KEY` | LLM service API key | _(empty)_ |
| `LLM_API_ENDPOINT` | LLM API base URL (OpenAI-compatible) | `https://api.openai.com/v1` |
| `LLM_MODEL_NAME` | Model name | `gpt-4` |
| `LLM_TIMEOUT` | LLM request timeout in seconds | `300` |

### Data Persistence

Uploaded trace files are stored in a Docker volume `uploads`, surviving container restarts.

```bash
# Stop
docker compose down

# Stop and remove uploaded traces
docker compose down -v
```

---

## Local Development

### Requirements

- Python 3.10+ with [conda](https://docs.conda.io/)
- Node.js 20+

### Backend

```bash
conda create -n perfetto python=3.11 -y
conda activate perfetto

cd backend
pip install -e ".[dev]"

cp config.yaml.example config.yaml
# Edit config.yaml — set llm.api_key

python run_server.py   # http://localhost:8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev     # http://localhost:5173, proxies API to localhost:8000
npm run build   # Production build → dist/
npm run lint    # ESLint
```

In development, run backend and frontend separately. The frontend Vite dev server proxies `/api` calls to `localhost:8000`.

### Tests

```bash
cd backend
conda run -n perfetto pytest tests/
conda run -n perfetto pytest tests/test_analyzer.py -k "test_name"  # Single test
```

---

## Usage

1. Open the app in a browser (`http://localhost:8000` for Docker, `http://localhost:5173` for dev)
2. Click **Settings** (header right) to configure LLM endpoint, API key, and model
   - The model dropdown auto-discovers available models from your API endpoint via `GET /models`
3. Drag and drop a `.perfetto-trace` / `.pb` / `.pftrace` file
4. Select analyzers to run (Jank, Startup, ANR, etc.) and click **Start**
5. Perfetto UI loads immediately; analysis runs in the background
6. Once complete, the left panel shows:
   - Performance score with p95/max frame times
   - AI-generated insights and ranked issue list
   - Jank frame list — click any frame to jump in Perfetto UI
   - Frame detail drawer with LLM root cause analysis and evidence SQL
7. Click **+ New** in the header to start another analysis without refreshing

---

## Architecture

```text
Browser
├─ React app
│  ├─ upload trace / choose analyzers
│  ├─ poll /api/traces/{id}/status
│  ├─ render analyzer tabs and results
│  └─ call /api/jump
└─ Perfetto UI iframe
   └─ receives jump commands over WebSocket

FastAPI backend
├─ routes/          API boundary
├─ services/        upload / analysis / result workflows
├─ orchestrator.py  analyzer coordination
├─ analyzers/       domain analyzers
├─ presenters/      frontend response shaping
└─ dependencies.py  shared runtime services
```

The backend is organized as a layered monolith. Routes stay thin, services own workflow orchestration, analyzers focus on domain logic, and presenters assemble frontend-friendly payloads such as `jank_frames`.

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/traces/upload` | POST | Upload trace file, start async analysis |
| `/api/analyzers` | GET | List currently available analyzers and metadata |
| `/api/traces/{id}/status` | GET | Poll analysis progress |
| `/api/traces/{id}` | GET | Get full analysis results |
| `/api/traces/{id}/file` | GET | Download raw trace file |
| `/api/jump` | POST | Jump to timestamp in Perfetto UI (via WebSocket bridge) |
| `/api/settings` | GET | Get current LLM configuration (key masked) |
| `/api/settings` | POST | Update LLM configuration at runtime |
| `/api/models` | GET | Auto-discover available models from LLM API |
| `/ws/bridge` | WS | WebSocket bridge to Perfetto UI |

### Project Structure

```
├── Dockerfile              # Multi-stage: Node frontend build → Python backend
├── docker-compose.yml      # Single container + uploads volume
├── .dockerignore           # Excludes node_modules, __pycache__, etc.
├── .env.example            # Environment variable template
├── backend/
│   ├── run_server.py       # Server entry point
│   ├── pyproject.toml      # Python dependencies
│   ├── config.yaml         # Local config (optional, overrides defaults)
│   └── perfetto_trace_analyzer/
│       ├── server.py           # FastAPI app wiring
│       ├── dependencies.py     # Shared runtime service container
│       ├── config.py           # YAML + env var config loading
│       ├── orchestrator.py     # Analysis workflow coordination
│       ├── registry.py         # Analyzer auto-discovery and registration
│       ├── trace_processor.py  # Perfetto trace_processor pool
│       ├── state.py            # In-memory + JSON-backed state cache
│       ├── models.py           # Shared dataclass models
│       ├── llm_client.py       # LLM API integration + JSON repair
│       ├── tools.py            # LLM tool definitions for agentic analysis
│       ├── reporter.py         # Generic JSON/HTML serialization
│       ├── scorer.py           # Performance scoring
│       ├── base_analyzer.py    # Abstract analyzer base (shared SQL templates)
│       ├── routes/             # API route modules
│       ├── services/           # Upload / analysis / result workflows
│       ├── presenters/         # Frontend-facing response shaping
│       ├── static/             # Injected Perfetto bridge script
│       └── analyzers/
│           ├── jank/           # Jank detection + per-frame LLM analysis
│           ├── startup.py      # App startup analysis
│           ├── anr.py          # ANR detection
│           ├── memory.py       # Memory analysis
│           └── binder.py       # Binder IPC analysis
└── frontend/
    └── src/
        ├── App.tsx         # Page-level composition
        ├── app.css
        ├── analyzers.ts    # Fallback analyzer metadata
        ├── api/
        │   ├── client.ts   # API client functions
        │   └── types.ts    # TypeScript type definitions
        ├── hooks/
        │   ├── useAnalyzerCatalog.ts
        │   └── useTraceAnalysis.ts
        ├── state/
        │   └── appReducer.ts
        └── components/
            ├── SettingsPanel.tsx      # LLM settings modal
            ├── ResultsPanel.tsx       # Left-side results area
            ├── PerfettoPanel.tsx      # Right-side Perfetto / upload area
            ├── AnalyzerPicker.tsx     # Analyzer selection before upload
            ├── ScoreBar.tsx           # Performance score display
            ├── IssueList.tsx          # Ranked issue list
            ├── JankFrameList.tsx      # Frame list with click-to-jump
            ├── JankInsightsPanel.tsx  # AI insights display
            ├── FrameDetailDrawer.tsx  # Frame detail side drawer
            ├── AnalyzerPanel.tsx      # Generic analyzer tab content
            └── Splitter.tsx           # Resizable panel splitter
```

For a fuller architecture walkthrough, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Analyzers

| Analyzer | What it detects |
|----------|----------------|
| **Jank** | Dropped/late frames, GPU/CPU bottleneck, SurfaceFlinger issues |
| **Startup** | Cold start phase breakdown, class loading, ContentProvider init |
| **ANR** | Main thread blocking, broadcast timeout, Binder deadlock |
| **Memory** | RSS growth trend, GC STW pauses, OOM proximity |
| **Binder** | Slow IPC by interface, main-thread synchronous blocking |

---

## Adding a New Analyzer

1. Create `backend/perfetto_trace_analyzer/analyzers/your_analyzer.py`
2. Inherit from `BaseAnalyzer`, implement `name`, `sql_templates`, `prompt_template`, `analyze()`
3. Common SQL queries (CPU freq, thread state, Binder, GC, etc.) are available via `COMMON_SQL_TEMPLATES`
4. Export it from `backend/perfetto_trace_analyzer/analyzers/__init__.py` so the registry can discover it
5. If it should appear in the upload UI, add metadata in `catalog_service.py`
6. Add backend tests and update README / README_CN when the analyzer becomes user-facing

---

## Contributing

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

## License

Apache 2.0 — same as [Perfetto](https://perfetto.dev). See [LICENSE](LICENSE).
