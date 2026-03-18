import { useCallback, useEffect, useReducer } from "react";

import { jumpToTimestamp } from "./api/client";
import { appReducer, initialState, type TabId } from "./state/appReducer";
import Splitter from "./components/Splitter";
import SettingsPanel from "./components/SettingsPanel";
import PerfettoPanel from "./components/PerfettoPanel";
import ResultsPanel from "./components/ResultsPanel";
import useAnalyzerCatalog from "./hooks/useAnalyzerCatalog";
import useTraceAnalysis from "./hooks/useTraceAnalysis";
import "./app.css";

export default function App() {
  const [state, dispatch] = useReducer(appReducer, initialState);
  const analyzers = useAnalyzerCatalog();
  const { handleFile, resetAnalysis } = useTraceAnalysis(dispatch);

  // Destructure for easy access in render
  const {
    data,
    perfettoUrl,
    analysisState,
    status,
    fileName,
    pendingFile,
    selectedAnalyzers,
    dragOver,
    activeTab,
    leftWidth,
    settingsOpen,
    selectedFrame,
  } = state;

  const jumpTo = useCallback(
    async (ts: number, dur: number, processName: string, upid: number, jankCategory?: string, sliceType?: "frame" | "slice") => {
      if (!perfettoUrl) return;
      try {
        await jumpToTimestamp(ts, dur, processName, upid, jankCategory, sliceType);
        dispatch({ type: "SET_STATUS", payload: { status: `${(ts / 1e6).toFixed(1)}ms` } });
      } catch {
        dispatch({ type: "SET_STATUS", payload: { status: "Jump failed" } });
      }
    },
    [perfettoUrl]
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      dispatch({ type: "SET_DRAG_OVER", payload: false });
      const file = e.dataTransfer.files[0];
      if (file) dispatch({ type: "SET_PENDING_FILE", payload: file });
    },
    []
  );

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    dispatch({ type: "SET_DRAG_OVER", payload: true });
  }, []);

  const onDragLeave = useCallback(() => dispatch({ type: "SET_DRAG_OVER", payload: false }), []);

  const showPanel = analysisState === "done" && data != null;

  // Available tabs — only show analyzers that actually returned a report
  const availableTabs = analyzers.filter((t) =>
    data?.category_reports?.some((r) => r.analyzer_name === t.id)
  );

  useEffect(() => {
    if (!showPanel || availableTabs.length === 0) return;
    if (!availableTabs.some((t) => t.id === activeTab)) {
      dispatch({ type: "SET_ACTIVE_TAB", payload: availableTabs[0].id });
    }
  }, [showPanel, activeTab, availableTabs]);

  const toggleAnalyzer = (id: TabId) => {
    dispatch({ type: "TOGGLE_ANALYZER", payload: id });
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
          onClick={() => dispatch({ type: "SET_SETTINGS_OPEN", payload: true })}
          title="LLM Settings"
        >
          Settings
        </button>
      </header>

      <div className="main-layout">
        {showPanel && data && (
          <ResultsPanel
            data={data}
            leftWidth={leftWidth}
            activeTab={activeTab}
            availableTabs={availableTabs}
            onSelectTab={(tabId) =>
              dispatch({ type: "SET_ACTIVE_TAB", payload: tabId })
            }
            onJumpToFrame={jumpTo}
            onSelectFrame={(frame) =>
              dispatch({ type: "SET_SELECTED_FRAME", payload: frame })
            }
          />
        )}

        {showPanel && <Splitter onResize={(w) => dispatch({ type: "SET_LEFT_WIDTH", payload: w })} />}

        <PerfettoPanel
          perfettoUrl={perfettoUrl}
          pendingFile={pendingFile}
          analyzers={analyzers}
          selectedAnalyzers={selectedAnalyzers}
          status={status}
          analysisState={analysisState}
          dragOver={dragOver}
          selectedFrame={selectedFrame}
          onToggleAnalyzer={toggleAnalyzer}
          onStartAnalysis={() => {
            if (pendingFile) {
              void handleFile(pendingFile, selectedAnalyzers);
            }
          }}
          onCancelPendingFile={() =>
            dispatch({ type: "SET_PENDING_FILE", payload: null })
          }
          onDrop={onDrop}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onFileSelected={(file) =>
            dispatch({ type: "SET_PENDING_FILE", payload: file })
          }
          onCloseFrame={() =>
            dispatch({ type: "SET_SELECTED_FRAME", payload: null })
          }
        />
      </div>
      <SettingsPanel open={settingsOpen} onClose={() => dispatch({ type: "SET_SETTINGS_OPEN", payload: false })} />
    </div>
  );
}
