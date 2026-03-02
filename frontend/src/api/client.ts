import type { Trace, TraceResult, UploadResponse, AnalysisStatus, LLMSettings } from "./types";

const API_BASE = import.meta.env.DEV ? "http://localhost:8000" : "";

export async function fetchTraces(): Promise<Trace[]> {
  const resp = await fetch(`${API_BASE}/api/traces`);
  const data = await resp.json();
  return data.traces;
}

export async function fetchTraceResult(traceId: string): Promise<TraceResult> {
  const resp = await fetch(`${API_BASE}/api/traces/${traceId}`);
  return resp.json();
}

/** URL for the Perfetto UI iframe.
 *  Dev: points to backend root (separate server, Perfetto proxy at /).
 *  Prod: points to /perfetto-ui/ to avoid conflicting with the React app at /. */
export function getPerfettoUrl(traceId: string): string {
  // Perfetto requires an absolute URL (uses new URL() internally)
  const origin = import.meta.env.DEV ? "http://localhost:8000" : window.location.origin;
  const traceFileUrl = `${origin}/api/traces/${traceId}/file`;
  const perfettoBase = import.meta.env.DEV ? API_BASE : "/perfetto-ui";
  return `${perfettoBase}/#!/viewer?url=${encodeURIComponent(traceFileUrl)}`;
}

/** Send jump command via backend API (broadcasts to Perfetto UI via WebSocket bridge). */
export async function jumpToTimestamp(
  ts: number,
  dur: number,
  processName?: string,
  upid?: number,
  jankCategory?: string,
  sliceType?: "frame" | "slice",
): Promise<void> {
  await fetch(`${API_BASE}/api/jump`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ts,
      dur,
      process_name: processName,
      upid,
      jank_category: jankCategory,
      slice_type: sliceType || "frame",
    }),
  });
}

/** Upload a trace file to the backend for analysis. */
export async function uploadTrace(file: File, analyzers?: string[]): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const url = analyzers?.length
    ? `${API_BASE}/api/traces/upload?analyzers=${analyzers.join(",")}`
    : `${API_BASE}/api/traces/upload`;
  const resp = await fetch(url, {
    method: "POST",
    body: formData,
  });
  if (!resp.ok) throw new Error(`Upload failed: ${resp.statusText}`);
  return resp.json();
}

/** Query the analysis status of a trace. */
export async function fetchAnalysisStatus(traceId: string): Promise<AnalysisStatus> {
  const resp = await fetch(`${API_BASE}/api/traces/${traceId}/status`);
  return resp.json();
}

/** Fetch current LLM settings. */
export async function fetchSettings(): Promise<LLMSettings> {
  const resp = await fetch(`${API_BASE}/api/settings`);
  return resp.json();
}

/** Update LLM settings. */
export async function updateSettings(settings: Partial<LLMSettings> & { api_key?: string }): Promise<void> {
  await fetch(`${API_BASE}/api/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
}

/** Fetch available models from the LLM API. */
export async function fetchModels(): Promise<{ models: string[]; current: string; error?: string }> {
  const resp = await fetch(`${API_BASE}/api/models`);
  return resp.json();
}
