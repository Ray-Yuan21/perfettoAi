import { useState } from "react";
import type { JankInsights } from "../api/types";

interface Props {
  insights: JankInsights | null;
}

const BOTTLENECK_LABELS: Record<string, string> = {
  cpu: "CPU 瓶颈",
  gpu: "GPU 瓶颈",
  buffer_contention: "缓冲区竞争",
  mixed: "混合瓶颈",
};

export default function JankInsightsPanel({ insights }: Props) {
  const [clustersOpen, setClustersOpen] = useState(true);

  if (!insights) return null;

  const { bottleneck_type, user_impact_assessment, hardware_assessment, jank_cause_clusters, app_jank_summary, sf_jank_summary } = insights;
  const hasContent = bottleneck_type || user_impact_assessment || hardware_assessment || jank_cause_clusters?.length || app_jank_summary || sf_jank_summary;
  if (!hasContent) return null;

  const sortedClusters = jank_cause_clusters ? [...jank_cause_clusters].sort((a, b) => b.count - a.count) : [];

  return (
    <div className="insights-panel">
      <div className="section-head">
        卡顿分析洞察
      </div>

      {bottleneck_type && (
        <div className="insights-row">
          <span className="insights-label">瓶颈类型</span>
          <span className={`bottleneck-badge bottleneck-${bottleneck_type}`}>
            {BOTTLENECK_LABELS[bottleneck_type] ?? bottleneck_type}
          </span>
        </div>
      )}

      {hardware_assessment && (
        <div className="insights-summary">
          <div className="insights-label">🖥️ 硬件与调度评估</div>
          <div className="insights-value">{hardware_assessment}</div>
        </div>
      )}

      {user_impact_assessment && (
        <div className="insights-row">
          <span className="insights-label">用户影响</span>
          <span className="insights-value">{user_impact_assessment}</span>
        </div>
      )}

      {sortedClusters.length > 0 && (
        <div className="insights-clusters">
          <div
            className="insights-clusters-head"
            onClick={() => setClustersOpen((v) => !v)}
          >
            <span className="insights-toggle">{clustersOpen ? "▾" : "▸"}</span>
            <span>卡顿原因聚类</span>
            <span className="cnt">({sortedClusters.length})</span>
          </div>
          {clustersOpen &&
            sortedClusters.map((c, i) => (
              <div key={i} className="cluster-row">
                <span className="cluster-count">{c.count}</span>
                <div>
                  <span className="cluster-cause">{c.cause}</span>
                  {c.severity && (
                    <span className={`cluster-severity cluster-severity-${c.severity}`}>
                      {c.severity}
                    </span>
                  )}
                  {c.description && (
                    <div className="cluster-description">{c.description}</div>
                  )}
                  {c.suggestion && (
                    <div className="cluster-suggestion">💡 {c.suggestion}</div>
                  )}
                </div>
              </div>
            ))}
        </div>
      )}

      {app_jank_summary && (
        <div className="insights-summary">
          <div className="insights-label">应用卡顿总结</div>
          <div className="insights-value">{app_jank_summary}</div>
        </div>
      )}

      {sf_jank_summary && (
        <div className="insights-summary">
          <div className="insights-label">SurfaceFlinger 卡顿总结</div>
          <div className="insights-value">{sf_jank_summary}</div>
        </div>
      )}
    </div>
  );
}
