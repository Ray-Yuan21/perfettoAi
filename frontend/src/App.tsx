import { useCallback, useEffect, useRef, useState } from "react";
import type { JankFrame, TraceResult } from "./api/types";
import {
  fetchTraceResult,
  getPerfettoUrl,
  jumpToTimestamp,
  uploadTrace,
  fetchAnalysisStatus,
} from "./api/client";
import ScoreBar from "./components/ScoreBar";
import IssueList from "./components/IssueList";
import JankFrameList from "./components/JankFrameList";
import JankInsightsPanel from "./components/JankInsightsPanel";
import FrameDetailDrawer from "./components/FrameDetailDrawer";
import AnalyzerPanel from "./components/AnalyzerPanel";
import Splitter from "./components/Splitter";
import SettingsPanel from "./components/SettingsPanel";
import "./app.css";

type AnalysisState = "idle" | "uploading" | "analyzing" | "done" | "failed";
type TabId = "jank" | "startup" | "anr" | "memory" | "binder";

const ALL_ANALYZERS: { id: TabId; label: string; description: string }[] = [
  { id: "jank", label: "Jank", description: "帧丢失 / 卡顿分析" },
  { id: "startup", label: "Startup", description: "应用启动耗时" },
  { id: "anr", label: "ANR", description: "无响应检测" },
  { id: "memory", label: "Memory", description: "内存泄漏 / 占用" },
  { id: "binder", label: "Binder", description: "IPC 延迟" },
];

export default function App() {
  const [data, setData] = useState<TraceResult | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("jank");
  const [leftWidth, setLeftWidth] = useState(340);
  const [perfettoUrl, setPerfettoUrl] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [analysisState, setAnalysisState] = useState<AnalysisState>("idle");
  const [fileName, setFileName] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const [selectedFrame, setSelectedFrame] = useState<JankFrame | null>(null);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [selectedAnalyzers, setSelectedAnalyzers] = useState<TabId[]>(["jank"]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const resetAnalysis = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setPerfettoUrl(null);
    setData(null);
    setAnalysisState("idle");
    setStatus("");
    setFileName(null);
    setPendingFile(null);
    setSelectedFrame(null);
  }, []);

  const handleFile = useCallback(async (file: File, analyzers: TabId[]) => {
    setAnalysisState("uploading");
    setStatus("Uploading...");
    setData(null);
    setFileName(file.name);
    setPendingFile(null);

    try {
      const { trace_id } = await uploadTrace(file, analyzers.length ? analyzers : undefined);

      // Immediately load Perfetto with the uploaded file
      setPerfettoUrl(getPerfettoUrl(trace_id));
      setAnalysisState("analyzing");
      setStatus("Analyzing...");

      // Poll for analysis completion
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const s = await fetchAnalysisStatus(trace_id);
          if (s.status === "done") {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            const result = await fetchTraceResult(trace_id);
            setData(result);
            setAnalysisState("done");
            setStatus("Analysis complete");
          } else if (s.status === "failed") {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            setAnalysisState("failed");
            setStatus(s.progress || "Analysis failed");
          } else {
            setStatus(s.progress || "Analyzing...");
          }
        } catch {
          // Network error during polling — keep trying
        }
      }, 2000);
    } catch (e) {
      setAnalysisState("failed");
      setStatus(`Upload failed: ${e}`);
    }
  }, []);

  const jumpTo = useCallback(
    async (ts: number, dur: number, processName: string, upid: number, jankCategory?: string, sliceType?: "frame" | "slice") => {
      if (!perfettoUrl) return;
      try {
        await jumpToTimestamp(ts, dur, processName, upid, jankCategory, sliceType);
        setStatus(`${(ts / 1e6).toFixed(1)}ms`);
      } catch {
        setStatus("Jump failed");
      }
    },
    [perfettoUrl]
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const file = e.dataTransfer.files[0];
      if (file) setPendingFile(file);
    },
    []
  );

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const onDragLeave = useCallback(() => setDragOver(false), []);

  const jankReport = data?.category_reports?.find((r) => r.analyzer_name === "jank");
  const stats = (jankReport?.statistics ?? {}) as Record<string, number>;
  const insights = jankReport?.llm_insights ?? null;
  const showPanel = analysisState === "done" && data != null;

  // Available tabs — only show tabs with a corresponding report
  const availableTabs = ALL_ANALYZERS.filter(
    (t) => t.id === "jank" || data?.category_reports?.some((r) => r.analyzer_name === t.id)
  );
  const activeReport = data?.category_reports?.find((r) => r.analyzer_name === activeTab);

  const toggleAnalyzer = (id: TabId) => {
    setSelectedAnalyzers((prev) =>
      prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]
    );
  };

  return (
    <div className="app">
      <header className="header">
        <h1>Perfetto Analyzer</h1>
        {fileName && <span className="header-filename">{fileName}</span>}
        {fileName && (
          <button className="header-new-btn" onClick={resetAnalysis} title="New analysis">
            + New
          </button>
        )}
        {analysisState === "analyzing" && (
          <span className="header-analyzing">Analyzing...</span>
        )}
        <button
          className="header-settings-btn"
          onClick={() => setSettingsOpen(true)}
          title="LLM Settings"
        >
          Settings
        </button>
      </header>

      <div className="main-layout">
        {showPanel && (
          <div className="panel-left" style={{ width: leftWidth }}>
            <ScoreBar
              score={data.overall_score?.score ?? null}
              jankRate={stats.jank_rate_pct ?? 0}
              jankFrames={stats.jank_frames ?? 0}
              totalFrames={stats.total_frames ?? 0}
              p95={stats.p95_frame_time_ms}
              max={stats.max_frame_time_ms}
            />
            {availableTabs.length > 1 && (
              <div className="analyzer-tabs">
                {availableTabs.map((t) => (
                  <button
                    key={t.id}
                    className={`analyzer-tab ${activeTab === t.id ? "active" : ""}`}
                    onClick={() => setActiveTab(t.id)}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            )}
            <div className="panel-scroll">
              {activeTab === "jank" ? (
                <>
                  <JankInsightsPanel insights={insights} />
                  <IssueList issues={data.ranked_issues} />
                  <JankFrameList frames={data.jank_frames} onJump={jumpTo} onSelectFrame={setSelectedFrame} />
                </>
              ) : activeReport ? (
                <AnalyzerPanel report={activeReport} />
              ) : (
                <div className="analyzer-empty-tab">No data for this analyzer</div>
              )}
            </div>
          </div>
        )}

        {showPanel && <Splitter onResize={setLeftWidth} />}

        <div className="panel-right">
          <div className="perfetto-toolbar">
            <span className="toolbar-label">Perfetto UI</span>
            <span
              className={`perfetto-status ${status === "Jump failed" || analysisState === "failed" ? "error" : ""}`}
            >
              {status}
            </span>
          </div>
          {perfettoUrl ? (
            <iframe src={perfettoUrl} className="perfetto-frame" title="Perfetto UI" />
          ) : pendingFile ? (
            <div className="analyzer-picker">
              <div className="analyzer-picker-title">选择分析类型</div>
              <div className="analyzer-picker-file">{pendingFile.name}</div>
              <div className="analyzer-picker-list">
                {ALL_ANALYZERS.map((a) => (
                  <label key={a.id} className="analyzer-picker-item">
                    <input
                      type="checkbox"
                      checked={selectedAnalyzers.includes(a.id)}
                      onChange={() => toggleAnalyzer(a.id)}
                    />
                    <span className="analyzer-picker-label">{a.label}</span>
                    <span className="analyzer-picker-desc">{a.description}</span>
                  </label>
                ))}
              </div>
              <button
                className="analyzer-picker-start"
                disabled={selectedAnalyzers.length === 0}
                onClick={() => handleFile(pendingFile, selectedAnalyzers)}
              >
                开始分析
              </button>
              <button className="analyzer-picker-cancel" onClick={() => setPendingFile(null)}>
                取消
              </button>
            </div>
          ) : (
            <div
              className={`upload-zone ${dragOver ? "drag-over" : ""}`}
              onDrop={onDrop}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
            >
              <div className="upload-content">
                <div className="upload-icon">📂</div>
                <div className="upload-title">
                  {analysisState === "uploading"
                    ? "Uploading..."
                    : "Drop a Perfetto trace file here"}
                </div>
                <div className="upload-subtitle">
                  or{" "}
                  <label className="upload-link">
                    browse files
                    <input
                      type="file"
                      accept=".perfetto-trace,.pb,.pftrace"
                      hidden
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file) setPendingFile(file);
                      }}
                    />
                  </label>
                </div>
                <div className="upload-hint">
                  Supports .perfetto-trace, .pb, .pftrace
                </div>
              </div>
            </div>
          )}
          <FrameDetailDrawer frame={selectedFrame} onClose={() => setSelectedFrame(null)} />
        </div>
      </div>
      <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
