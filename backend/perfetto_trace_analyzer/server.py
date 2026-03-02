"""Web server for Perfetto Trace Analyzer.

Backend API server + Perfetto UI reverse proxy.
Frontend is a separate React app (see ../frontend/).

Routes (order matters – specific routes before catch-all):
  /api/traces         → list analyzed traces
  /api/traces/{id}    → trace analysis result JSON
  /api/traces/{id}/file → raw trace file download
  /api/jump           → trigger jump in Perfetto UI
  /ws/bridge          → WebSocket for Perfetto UI bridge
  /perfetto/{path}    → reverse proxy to ui.perfetto.dev
  /                   → serve frontend (production build)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import asyncio

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from .config import ConfigManager
from .models import AppConfig, AnalysisResult
from .orchestrator import Orchestrator, scan_trace_files
from .reporter import _result_to_dict, extract_jank_frames

logger = logging.getLogger(__name__)

PERFETTO_UI_ORIGIN = "https://ui.perfetto.dev"
_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")

app = FastAPI(title="Perfetto Trace Analyzer", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
_state: dict[str, Any] = {
    "config": None,
    "results": {},
    "trace_paths": {},
    "status": {},       # trace_id → "analyzing" | "done" | "failed"
    "progress": {},     # trace_id → progress message
}

# Connected WebSocket clients (Perfetto UI bridge instances)
_ws_clients: set[WebSocket] = set()

# Shared httpx client (created on startup)
_http_client: httpx.AsyncClient | None = None


@app.on_event("startup")
async def _startup():
    global _http_client
    _http_client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)

    # Auto-initialize on reload: if trace_path was set via env, re-analyze
    trace_path = os.environ.get("_PTA_TRACE_PATH")
    if trace_path and not _state["results"]:
        config_path = os.environ.get("_PTA_CONFIG")
        config = ConfigManager.load(config_path)
        create_app(config=config, trace_path=trace_path)


@app.on_event("shutdown")
async def _shutdown():
    global _http_client
    if _http_client:
        await _http_client.aclose()


def create_app(config: AppConfig | None = None, trace_path: str | None = None) -> FastAPI:
    _state["config"] = config or ConfigManager.load()
    if trace_path:
        trace_files = scan_trace_files(trace_path)
        orchestrator = Orchestrator(_state["config"])
        for tf in trace_files:
            logger.info("Pre-analyzing: %s", tf)
            result = orchestrator.analyze(tf)
            trace_id = _make_trace_id(tf)
            _state["results"][trace_id] = result
            _state["trace_paths"][trace_id] = tf
    return app


def _make_trace_id(path: str) -> str:
    return os.path.basename(path).replace(".", "_")


# ─── API Routes ───────────────────────────────────────────────

@app.get("/api/traces")
async def list_traces():
    traces = []
    for trace_id, result in _state["results"].items():
        score = result.overall_score.overall if result.overall_score else None
        traces.append({
            "id": trace_id,
            "filename": os.path.basename(result.trace_path),
            "score": score,
        })
    return {"traces": traces}


@app.post("/api/traces/upload")
async def upload_trace(request: Request, file: UploadFile):
    """Upload a trace file, save it, and start async analysis."""
    os.makedirs(_UPLOAD_DIR, exist_ok=True)
    trace_id = uuid.uuid4().hex[:12]
    filename = file.filename or f"trace_{trace_id}"
    save_path = os.path.join(_UPLOAD_DIR, f"{trace_id}_{filename}")

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    # Optional: comma-separated list of analyzers to run, e.g. "jank,startup"
    analyzers_param = request.query_params.get("analyzers")
    analyzer_names = [a.strip() for a in analyzers_param.split(",") if a.strip()] if analyzers_param else None

    _state["trace_paths"][trace_id] = save_path
    _state["status"][trace_id] = "analyzing"
    _state["progress"][trace_id] = "Starting analysis..."

    # Launch async analysis in background
    asyncio.create_task(_analyze_trace(trace_id, save_path, analyzer_names))

    return {"trace_id": trace_id, "filename": filename}


@app.get("/api/traces/{trace_id}/status")
async def get_trace_status(trace_id: str):
    """Query the analysis status of a trace."""
    status = _state["status"].get(trace_id)
    if status is None:
        # Check if it's a pre-loaded trace (already done)
        if trace_id in _state["results"]:
            return {"status": "done", "progress": "Analysis complete"}
        raise HTTPException(404, "Trace not found")
    return {
        "status": status,
        "progress": _state["progress"].get(trace_id, ""),
    }


async def _analyze_trace(trace_id: str, trace_path: str, analyzer_names: list[str] | None = None):
    """Background async analysis: SQL + LLM."""
    try:
        _state["progress"][trace_id] = "Loading config..."
        config = _state["config"] or ConfigManager.load()
        orchestrator = Orchestrator(config)

        _state["progress"][trace_id] = "Running SQL queries + LLM analysis..."
        result = await asyncio.to_thread(orchestrator.analyze, trace_path, analyzer_names)

        _state["results"][trace_id] = result
        _state["status"][trace_id] = "done"
        _state["progress"][trace_id] = "Analysis complete"
        logger.info("Analysis complete for trace %s", trace_id)
    except Exception as e:
        _state["status"][trace_id] = "failed"
        _state["progress"][trace_id] = f"Error: {e}"
        logger.error("Analysis failed for trace %s: %s", trace_id, e)


@app.get("/api/traces/{trace_id}")
async def get_trace_result(trace_id: str):
    result = _state["results"].get(trace_id)
    if not result:
        raise HTTPException(404, "Trace not found")
    data = _result_to_dict(result)

    # Extract and enrich jank frames (logic moved to reporter.py)
    jank_frames = extract_jank_frames(result)
    data["jank_frames"] = jank_frames

    return data


@app.get("/api/traces/{trace_id}/file")
async def serve_trace_file(trace_id: str):
    path = _state["trace_paths"].get(trace_id)
    if not path or not os.path.exists(path):
        raise HTTPException(404, "Trace file not found")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Content-Disposition": f"attachment; filename={os.path.basename(path)}",
        },
    )


# ─── WebSocket bridge + Jump API (for MCP / external tools) ──

@app.websocket("/ws/bridge")
async def ws_bridge(ws: WebSocket):
    """WebSocket endpoint for Perfetto UI bridge script."""
    await ws.accept()
    _ws_clients.add(ws)
    logger.info("Bridge WebSocket connected (%d total)", len(_ws_clients))
    try:
        while True:
            # Keep alive; bridge doesn't send data, but we need to detect disconnects
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)
        logger.info("Bridge WebSocket disconnected (%d total)", len(_ws_clients))


@app.post("/api/jump")
async def api_jump(request: Request):
    """Trigger a jump+select in all connected Perfetto UI instances.

    Body: {"ts": <nanoseconds>, "dur": <nanoseconds>, "slice_type": "frame"|"slice"}
    Called by MCP server or any external tool.
    """
    body = await request.json()
    ts = body.get("ts")
    dur = body.get("dur", 0)
    if ts is None:
        raise HTTPException(400, "Missing 'ts' field")

    msg = json.dumps({
        "type": "jump",
        "ts": ts,
        "dur": dur,
        "process_name": body.get("process_name", ""),
        "upid": body.get("upid", 0),
        "jank_category": body.get("jank_category", ""),
        "slice_type": body.get("slice_type", "frame"),
    })
    disconnected: list[WebSocket] = []
    for ws in _ws_clients:
        try:
            await ws.send_text(msg)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        _ws_clients.discard(ws)

    n = len(_ws_clients) - len(disconnected)
    return {"ok": True, "clients": n, "ts": ts, "dur": dur}


# ─── Settings API ─────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings():
    """Return current LLM configuration (key masked)."""
    config = _state["config"] or ConfigManager.load()
    llm = config.llm
    return {
        "provider": llm.provider,
        "api_endpoint": llm.api_endpoint,
        "model_name": llm.model_name,
        "api_key_set": bool(llm.api_key),
        "temperature": llm.temperature,
        "max_tokens": llm.max_tokens,
        "timeout": llm.timeout,
    }


@app.post("/api/settings")
async def update_settings(request: Request):
    """Update LLM configuration at runtime."""
    body = await request.json()
    config = _state["config"] or ConfigManager.load()
    llm = config.llm
    if "api_endpoint" in body:
        llm.api_endpoint = body["api_endpoint"]
    if "api_key" in body and body["api_key"]:
        llm.api_key = body["api_key"]
    if "model_name" in body:
        llm.model_name = body["model_name"]
    if "provider" in body:
        llm.provider = body["provider"]
    if "timeout" in body:
        llm.timeout = int(body["timeout"])
    _state["config"] = config
    return {"ok": True}


@app.get("/api/models")
async def list_models():
    """Query available models from the LLM API endpoint."""
    config = _state["config"] or ConfigManager.load()
    llm = config.llm
    url = f"{llm.api_endpoint.rstrip('/')}/models"
    headers = {
        "Authorization": f"Bearer {llm.api_key}",
        "x-api-key": llm.api_key,
    }
    try:
        resp = await _http_client.get(url, headers=headers, timeout=10.0)
        data = resp.json()
        models = [m.get("id", "") for m in data.get("data", [])]
        return {"models": sorted(models), "current": llm.model_name}
    except Exception as e:
        return {"models": [], "current": llm.model_name, "error": str(e)}


# ─── Static frontend serving (production build) ──────────────

if os.path.isdir(_STATIC_DIR):
    from fastapi.staticfiles import StaticFiles

    _assets_dir = os.path.join(_STATIC_DIR, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="static-assets")

    @app.get("/")
    async def serve_frontend():
        return FileResponse(os.path.join(_STATIC_DIR, "index.html"))

    @app.get("/perfetto-ui")
    @app.get("/perfetto-ui/")
    async def perfetto_ui_root(request: Request):
        """Serve Perfetto UI root with bridge script injected (production mode)."""
        try:
            resp = await _http_client.get(
                PERFETTO_UI_ORIGIN + "/",
                headers={"Accept": "text/html", "Accept-Encoding": "identity"},
            )
        except httpx.HTTPError as e:
            return Response(content=f"Proxy error: {e}", status_code=502)
        html = resp.content.decode("utf-8", errors="replace")
        # Inject <base href="/"> so relative asset paths resolve from root, not /perfetto-ui/
        html = html.replace("<head>", '<head><base href="/">', 1)
        html = html.replace("</body>", _PERFETTO_BRIDGE_SCRIPT + "</body>")
        return Response(content=html.encode("utf-8"), status_code=200,
                        headers={"content-type": "text/html; charset=utf-8"})


# ─── Perfetto UI Reverse Proxy (catch-all, MUST be last) ─────

@app.api_route("/{path:path}", methods=["GET", "HEAD", "OPTIONS"])
async def perfetto_proxy(path: str, request: Request):
    """Reverse proxy requests to ui.perfetto.dev."""
    global _http_client
    if not _http_client:
        _http_client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)

    # Block service worker to prevent caching
    if "service_worker" in path.lower() or "service-worker" in path.lower() or path.endswith("sw.js"):
        return Response(content="// no-op service worker", headers={"content-type": "application/javascript"})

    target_url = f"{PERFETTO_UI_ORIGIN}/{path}"
    query = str(request.url.query)
    if query:
        target_url += f"?{query}"

    try:
        resp = await _http_client.get(
            target_url,
            headers={
                "Accept": request.headers.get("accept", "*/*"),
                "Accept-Encoding": "identity",
            },
        )
    except httpx.HTTPError as e:
        return Response(content=f"Proxy error: {e}", status_code=502)

    headers: dict[str, str] = {}
    for key in ("content-type", "cache-control", "etag", "last-modified"):
        if key in resp.headers:
            headers[key] = resp.headers[key]

    content = resp.content
    ct = resp.headers.get("content-type", "")

    # Inject bridge script into the root HTML page (dev mode: path == "")
    if path in ("", "perfetto-ui", "perfetto-ui/") and "text/html" in ct:
        html = content.decode("utf-8", errors="replace")
        html = html.replace("</body>", _PERFETTO_BRIDGE_SCRIPT + "</body>")
        content = html.encode("utf-8")
        headers["content-type"] = "text/html; charset=utf-8"

    return Response(
        content=content,
        status_code=resp.status_code,
        headers=headers,
    )


# JavaScript bridge injected into Perfetto UI HTML.
# Listens for postMessage commands from our dashboard and uses
# Perfetto's internal APIs to scroll the timeline.
_PERFETTO_BRIDGE_SCRIPT = r"""
<script>
(function() {
  // Unregister any cached service workers
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.getRegistrations().then(function(regs) {
      regs.forEach(function(r) { r.unregister(); });
    });
  }

  // Track last jump note so we can replace it on each jump
  var _lastJumpNoteId = null;

  function getTrace() {
    var app = window.app;
    if (!app) return null;
    if (app._activeTrace) return app._activeTrace;
    if (app.trace && typeof app.trace !== 'function') return app.trace;
    if (typeof app.trace === 'function') {
      try { var t = app.trace(); if (t) return t; } catch(e) {}
    }
    return null;
  }

  window._perfettoJump = async function(ts, dur, processName, upid, jankCategory, sliceType) {
    var app = window.app;
    if (!app) { console.log('[Bridge] no app'); return; }
    var trace = getTrace();
    if (!trace || !trace.timeline) { console.log('[Bridge] no trace/timeline'); return; }

    var tl = trace.timeline;
    var viewDur = Math.max(dur * 10, 50000000);
    var viewStart = Math.max(0, ts - viewDur / 2);

    // 1. Set visible window (viewport zoom)
    try {
      var vw = tl._visibleWindow;
      var StartClass = vw.start.constructor;
      var WindowClass = vw.constructor;
      var newStart = new StartClass(BigInt(viewStart));
      var newWindow = new WindowClass(newStart, Number(viewDur));
      tl.setVisibleWindow(newWindow);
    } catch(e) {
      console.log('[Bridge] setVisibleWindow error:', e);
    }

    // 2. Create persistent span note (M-key style)
    try {
      var notes = trace.notes;
      if (_lastJumpNoteId !== null) {
        try { notes.removeNote(_lastJumpNoteId); } catch(e) {}
      }
      _lastJumpNoteId = notes.addSpanNote({
        start: BigInt(ts),
        end: BigInt(ts) + BigInt(dur),
        color: '#3b82f6'
      });
    } catch(e) {
      console.log('[Bridge] note error:', e);
    }

    // 3. Select the slice + scroll to it
    var selected = false;
    var verticalScrolled = false;
    sliceType = sliceType || 'frame';

    // Step A: Select the slice via SQL
    try {
      if (trace.engine) {
        var jankCat = jankCategory || '';
        var isSF = jankCat && (jankCat.indexOf('SF') >= 0 || jankCat.indexOf('sf') >= 0);
        var sql;
        var tableName;

        if (sliceType === 'slice') {
          // Call tree slice: search in regular slice table by ts and dur
          // Use range matching to handle potential precision issues with large integers
          tableName = 'slice';
          var tsTolerance = 1000; // 1 microsecond tolerance
          sql = "SELECT CAST(id AS TEXT) AS id_text, ts, dur, track_id FROM slice WHERE ABS(ts - " + ts + ") <= " + tsTolerance + " AND ABS(dur - " + dur + ") <= " + tsTolerance;
          sql += " ORDER BY ABS(ts - " + ts + ") + ABS(dur - " + dur + ") LIMIT 1";
          console.log('[Bridge] searching slice table for call tree item, ts=' + ts + ' dur=' + dur);
        } else if (isSF) {
          // SF frames: use time range overlap matching (ts may not match exactly)
          tableName = 'actual_frame_timeline_slice';
          sql = "SELECT CAST(id AS TEXT) AS id_text FROM actual_frame_timeline_slice WHERE ts <= " + ts + " AND (ts + dur) >= " + ts;
          if (upid) sql += " AND upid = " + upid;
          sql += " ORDER BY ABS(ts - " + ts + ") LIMIT 1";
        } else {
          // App frames: exact ts match
          tableName = 'actual_frame_timeline_slice';
          sql = "SELECT CAST(id AS TEXT) AS id_text FROM actual_frame_timeline_slice WHERE ts = " + ts;
          if (upid) sql += " AND upid = " + upid;
          sql += " LIMIT 1";
        }

        console.log('[Bridge] SQL:', sql);
        var result = await trace.engine.query(sql);
        var it = result.iter({id_text: 'str'});

        if (it.valid()) {
          var sliceId = parseInt(it.id_text, 10);
          var foundTs = it.ts !== undefined ? it.ts : ts;
          var foundDur = it.dur !== undefined ? it.dur : dur;
          var trackId = undefined;
          if (sliceType === 'slice') {
            try {
              trackId = it.get('track_id');
            } catch (e) {
              console.log('[Bridge] could not get track_id:', e.message);
            }
          }
          console.log('[Bridge] found slice id:', sliceId, 'in table:', tableName, 'track_id:', trackId);

          if (trace.selection && typeof trace.selection.selectSqlEvent === 'function') {
            trace.selection.selectSqlEvent(tableName, sliceId, {scrollToSelection: true});
            selected = true;
            console.log('[Bridge] selectSqlEvent OK');

            // For slice type, try to scroll to the specific track
            if (sliceType === 'slice' && trackId && trace.scrollHelper) {
              setTimeout(function() {
                try {
                  // Try to find and scroll to the track containing this slice
                  var trackUri = '/slice_' + trackId;
                  trace.scrollHelper.scrollTo({track: {uri: trackUri, expandGroup: true}});
                } catch(e) {
                  console.log('[Bridge] track scroll error:', e);
                }
              }, 50);
            }
          }
        } else {
          console.log('[Bridge] no slice found in', tableName, 'ts=' + ts + ' dur=' + dur);
        }
      }
    } catch(e) {
      console.log('[Bridge] select error:', e);
    }

    // Step B: Expand and scroll to the process group
    var trace2 = getTrace() || trace;

    if (upid) {
      var processUri = '/process_' + upid;

      // Expand the process group
      try {
        var ws = trace2.workspaces;
        var cws = ws.currentWorkspace || ws._currentWorkspace || ws.defaultWorkspace;
        if (cws && typeof cws.getTrackByUri === 'function') {
          var processNode = cws.getTrackByUri(processUri);
          if (processNode) {
            // Expand the process group
            if ('expanded' in processNode) processNode.expanded = true;
            if ('collapsed' in processNode) processNode.collapsed = false;
            if (typeof processNode.expand === 'function') processNode.expand();
            if (typeof processNode.toggle === 'function' && !processNode.expanded) processNode.toggle();

            // Expand parents too
            var p = processNode.parent;
            while (p) {
              if ('expanded' in p) p.expanded = true;
              if ('collapsed' in p) p.collapsed = false;
              if (typeof p.expand === 'function') p.expand();
              p = p.parent;
            }
          }
        }
      } catch(e) {
        console.log('[Bridge] expand error:', e);
      }

      // Redraw first, then scroll after UI updates
      app.raf.scheduleFullRedraw();
      setTimeout(function() {
        if (trace2.scrollHelper) {
          try {
            trace2.scrollHelper.scrollTo({track: {uri: processUri, expandGroup: true}});
          } catch(e) {}
        }
        app.raf.scheduleFullRedraw();
      }, 100);

      verticalScrolled = true;
    }

    if (!verticalScrolled) {
      console.log('[Bridge] vertical scroll failed. upid:', upid, 'process:', processName);
    }

    // 5. Force redraw
    app.raf.scheduleFullRedraw();
    try { app.raf.syncCanvasRedraw(); } catch(e) {}
    setTimeout(function() {
      app.raf.scheduleFullRedraw();
      try { app.raf.syncCanvasRedraw(); } catch(e) {}
    }, 100);
  };

  // ── WebSocket: receive jump commands from MCP / external tools ──
  function connectBridgeWS() {
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = proto + '//' + location.host + '/ws/bridge';
    var ws = new WebSocket(url);
    ws.onopen = function() { console.log('[Bridge] WS connected'); };
    ws.onmessage = function(evt) {
      try {
        var msg = JSON.parse(evt.data);
        if (msg.type === 'jump' && msg.ts !== undefined) {
          console.log('[Bridge] WS jump ts=' + msg.ts + ' dur=' + msg.dur + ' slice_type=' + (msg.slice_type || 'frame'));
          window._perfettoJump(msg.ts, msg.dur || 0, msg.process_name || '', msg.upid || 0, msg.jank_category || '', msg.slice_type || 'frame');
        }
      } catch(e) { console.log('[Bridge] WS message error:', e); }
    };
    ws.onclose = function() {
      console.log('[Bridge] WS disconnected, reconnecting in 3s');
      setTimeout(connectBridgeWS, 3000);
    };
    ws.onerror = function() { ws.close(); };
  }
  // Wait for page load before connecting WS
  if (document.readyState === 'complete') { connectBridgeWS(); }
  else { window.addEventListener('load', connectBridgeWS); }
})();
</script>
"""
