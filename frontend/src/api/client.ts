import type { Trace, TraceResult, UploadResponse, AnalysisStatus, LLMSettings, AnalyzerOption } from "./types";

const API_BASE = import.meta.env.DEV ? "http://localhost:8000" : "";

/** Unified fetch wrapper with error handling. */
async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init);
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`API ${resp.status}: ${resp.statusText}${body ? ` – ${body.slice(0, 200)}` : ""}`);
  }
  return resp.json();
}

export async function fetchTraces(): Promise<Trace[]> {
  const data = await apiFetch<{ traces: Trace[] }>(`${API_BASE}/api/traces`);
  return data.traces;
}

export async function fetchAnalyzers(): Promise<AnalyzerOption[]> {
  const data = await apiFetch<{ analyzers: AnalyzerOption[] }>(`${API_BASE}/api/analyzers`);
  return data.analyzers;
}

export async function fetchTraceResult(traceId: string): Promise<TraceResult> {
  return apiFetch<TraceResult>(`${API_BASE}/api/traces/${traceId}`);
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
  await apiFetch(`${API_BASE}/api/jump`, {
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
  return apiFetch<UploadResponse>(url, {
    method: "POST",
    body: formData,
  });
}

/** Query the analysis status of a trace. */
export async function fetchAnalysisStatus(traceId: string): Promise<AnalysisStatus> {
  return apiFetch<AnalysisStatus>(`${API_BASE}/api/traces/${traceId}/status`);
}

/** Fetch current LLM settings. */
export async function fetchSettings(): Promise<LLMSettings> {
  return apiFetch<LLMSettings>(`${API_BASE}/api/settings`);
}

/** Update LLM settings. */
export async function updateSettings(settings: Partial<LLMSettings> & { api_key?: string }): Promise<void> {
  await apiFetch(`${API_BASE}/api/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
}

/** Fetch available models from the LLM API. */
export async function fetchModels(): Promise<{ models: string[]; current: string; error?: string }> {
  return apiFetch(`${API_BASE}/api/models`);
}
