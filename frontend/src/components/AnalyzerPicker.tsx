import type { AnalyzerOption } from "../api/types";
import type { TabId } from "../state/appReducer";

interface Props {
  fileName: string;
  analyzers: AnalyzerOption[];
  selectedAnalyzers: TabId[];
  onToggle: (id: TabId) => void;
  onStart: () => void;
  onCancel: () => void;
}

export default function AnalyzerPicker({
  fileName,
  analyzers,
  selectedAnalyzers,
  onToggle,
  onStart,
  onCancel,
}: Props) {
  return (
    <div className="analyzer-picker">
      <div className="analyzer-picker-title">选择分析类型</div>
      <div className="analyzer-picker-file">{fileName}</div>
      <div className="analyzer-picker-list">
        {analyzers.map((analyzer) => (
          <label key={analyzer.id} className="analyzer-picker-item">
            <input
              type="checkbox"
              checked={selectedAnalyzers.includes(analyzer.id)}
              onChange={() => onToggle(analyzer.id)}
            />
            <span className="analyzer-picker-label">{analyzer.label}</span>
            <span className="analyzer-picker-desc">{analyzer.description}</span>
          </label>
        ))}
      </div>
      <button
        className="analyzer-picker-start"
        disabled={selectedAnalyzers.length === 0}
        onClick={onStart}
      >
        开始分析
      </button>
      <button className="analyzer-picker-cancel" onClick={onCancel}>
        取消
      </button>
    </div>
  );
}
