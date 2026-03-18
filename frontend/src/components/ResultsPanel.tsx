import type { CategoryReport, JankFrame, TraceResult } from "../api/types";
import type { TabId } from "../state/appReducer";
import ScoreBar from "./ScoreBar";
import JankInsightsPanel from "./JankInsightsPanel";
import IssueList from "./IssueList";
import JankFrameList from "./JankFrameList";
import AnalyzerPanel from "./AnalyzerPanel";

interface TabOption {
  id: string;
  label: string;
}

interface Props {
  data: TraceResult;
  leftWidth: number;
  activeTab: TabId;
  availableTabs: TabOption[];
  onSelectTab: (tabId: TabId) => void;
  onJumpToFrame: (
    ts: number,
    dur: number,
    processName: string,
    upid: number,
    jankCategory?: string,
    sliceType?: "frame" | "slice"
  ) => Promise<void>;
  onSelectFrame: (frame: JankFrame) => void;
}

function getJankReport(data: TraceResult): CategoryReport | undefined {
  return data.category_reports.find((report) => report.analyzer_name === "jank");
}

export default function ResultsPanel({
  data,
  leftWidth,
  activeTab,
  availableTabs,
  onSelectTab,
  onJumpToFrame,
  onSelectFrame,
}: Props) {
  const jankReport = getJankReport(data);
  const stats = (jankReport?.statistics ?? {}) as Record<string, number>;
  const insights = jankReport?.llm_insights ?? null;
  const activeReport = data.category_reports.find(
    (report) => report.analyzer_name === activeTab
  );

  return (
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
          {availableTabs.map((tab) => (
            <button
              key={tab.id}
              className={`analyzer-tab ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => onSelectTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
      )}
      <div className="panel-scroll">
        {activeTab === "jank" ? (
          <>
            <JankInsightsPanel insights={insights} />
            <IssueList issues={data.ranked_issues ?? []} />
            <JankFrameList
              frames={data.jank_frames ?? []}
              onJump={onJumpToFrame}
              onSelectFrame={onSelectFrame}
            />
          </>
        ) : activeReport ? (
          <AnalyzerPanel report={activeReport} />
        ) : (
          <div className="analyzer-empty-tab">No data for this analyzer</div>
        )}
      </div>
    </div>
  );
}
