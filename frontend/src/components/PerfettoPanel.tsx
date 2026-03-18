import type { AnalyzerOption, JankFrame } from "../api/types";
import type { AnalysisState, TabId } from "../state/appReducer";
import AnalyzerPicker from "./AnalyzerPicker";
import FrameDetailDrawer from "./FrameDetailDrawer";
import UploadZone from "./UploadZone";

interface Props {
  perfettoUrl: string | null;
  pendingFile: File | null;
  analyzers: AnalyzerOption[];
  selectedAnalyzers: TabId[];
  status: string;
  analysisState: AnalysisState;
  dragOver: boolean;
  selectedFrame: JankFrame | null;
  onToggleAnalyzer: (id: TabId) => void;
  onStartAnalysis: () => void;
  onCancelPendingFile: () => void;
  onDrop: (e: React.DragEvent) => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onFileSelected: (file: File) => void;
  onCloseFrame: () => void;
}

export default function PerfettoPanel({
  perfettoUrl,
  pendingFile,
  analyzers,
  selectedAnalyzers,
  status,
  analysisState,
  dragOver,
  selectedFrame,
  onToggleAnalyzer,
  onStartAnalysis,
  onCancelPendingFile,
  onDrop,
  onDragOver,
  onDragLeave,
  onFileSelected,
  onCloseFrame,
}: Props) {
  return (
    <div className="panel-right">
      <div className="perfetto-toolbar">
        <span className="toolbar-label">Perfetto UI</span>
        <span
          className={`perfetto-status ${
            status === "Jump failed" || analysisState === "failed" ? "error" : ""
          }`}
        >
          {status}
        </span>
      </div>
      {perfettoUrl ? (
        <iframe src={perfettoUrl} className="perfetto-frame" title="Perfetto UI" />
      ) : pendingFile ? (
        <AnalyzerPicker
          fileName={pendingFile.name}
          analyzers={analyzers}
          selectedAnalyzers={selectedAnalyzers}
          onToggle={onToggleAnalyzer}
          onStart={onStartAnalysis}
          onCancel={onCancelPendingFile}
        />
      ) : (
        <UploadZone
          analysisState={analysisState}
          dragOver={dragOver}
          onDrop={onDrop}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onFileSelected={onFileSelected}
        />
      )}
      <FrameDetailDrawer frame={selectedFrame} onClose={onCloseFrame} />
    </div>
  );
}
