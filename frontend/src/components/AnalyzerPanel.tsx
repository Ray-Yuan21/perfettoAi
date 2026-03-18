import { useState } from "react";
import { getAnalyzerLabel } from "../analyzers";
import type { CategoryReport } from "../api/types";

interface Props {
  report: CategoryReport;
}

interface EvidenceSQL {
  label: string;
  sql: string;
  conclusion: string;
}

interface KeyMetric {
  label: string;
  value: string;
  highlight?: boolean;
}

function buildKeyMetrics(
  name: string,
  stats: Record<string, unknown>,
  insights: Record<string, unknown>
): KeyMetric[] {
  switch (name) {
    case "startup": {
      const ms = insights.startup_time_ms ?? stats.startup_time_ms;
      const phase = (insights.bottleneck_phase ?? stats.bottleneck_phase) as string | undefined;
      const metrics: KeyMetric[] = [];
      if (ms != null) metrics.push({ label: "Startup Time", value: `${Number(ms).toFixed(0)} ms`, highlight: Number(ms) > 1000 });
      if (phase) metrics.push({ label: "Bottleneck Phase", value: phase });
      return metrics;
    }
    case "anr": {
      const count = insights.anr_count ?? stats.anr_count;
      const metrics: KeyMetric[] = [];
      if (count != null) metrics.push({ label: "ANR Count", value: String(count), highlight: Number(count) > 0 });
      return metrics;
    }
    case "memory": {
      const peak = insights.peak_rss_mb ?? stats.peak_rss_mb;
      const gc = insights.gc_count ?? stats.gc_count;
      const trend = (insights.memory_trend ?? stats.memory_trend) as string | undefined;
      const metrics: KeyMetric[] = [];
      if (peak != null) metrics.push({ label: "Peak RSS", value: `${Number(peak).toFixed(1)} MB` });
      if (gc != null) metrics.push({ label: "GC Count", value: String(gc), highlight: Number(gc) > 10 });
      if (trend) metrics.push({ label: "Trend", value: trend, highlight: trend === "increasing" });
      return metrics;
    }
    case "binder": {
      const slow = insights.slow_binder_count ?? stats.slow_binder_count;
      const blocking = insights.main_thread_binder_blocking_ms ?? stats.main_thread_binder_blocking_ms;
      const metrics: KeyMetric[] = [];
      if (slow != null) metrics.push({ label: "Slow Calls", value: String(slow), highlight: Number(slow) > 0 });
      if (blocking != null) metrics.push({ label: "Main Thread Blocked", value: `${Number(blocking).toFixed(1)} ms`, highlight: Number(blocking) > 100 });
      return metrics;
    }
    default:
      return [];
  }
}

function EvidenceBlock({ items }: { items: EvidenceSQL[] }) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  if (items.length === 0) return null;
  return (
    <div className="evidence-section">
      <div className="evidence-section-title">Evidence SQL ({items.length})</div>
      {items.map((ev, i) => (
        <div key={i} className="evidence-item">
          <div className="evidence-header" onClick={() => setOpen(o => ({ ...o, [i]: !o[i] }))}>
            <span className="evidence-toggle">{open[i] ? "▼" : "▶"}</span>
            <span className="evidence-label">{ev.label}</span>
          </div>
          {ev.conclusion && (
            <div className="evidence-conclusion">{ev.conclusion}</div>
          )}
          {open[i] && <pre className="evidence-sql">{ev.sql}</pre>}
        </div>
      ))}
    </div>
  );
}

export default function AnalyzerPanel({ report }: Props) {
  const [insightsOpen, setInsightsOpen] = useState(true);
  const [issuesOpen, setIssuesOpen] = useState(true);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);

  const insights = (report.llm_insights ?? {}) as Record<string, unknown>;
  const title = `${getAnalyzerLabel(report.analyzer_name)} Analysis`;
  const score = report.score;
  const scoreCls = score === null ? "" : score >= 80 ? "high" : score >= 50 ? "mid" : "low";

  const evidenceSql = (insights.evidence_sql ?? []) as EvidenceSQL[];
  const issues = report.issues ?? [];
  const suggestions = report.suggestions ?? [];
  const stats = report.statistics as Record<string, unknown>;
  const keyMetrics = buildKeyMetrics(report.analyzer_name, stats, insights);

  if (report.status === "insufficient_data") {
    return (
      <div className="analyzer-panel">
        <div className="analyzer-panel-head">{title}</div>
        <div className="analyzer-empty">No relevant data found in trace</div>
      </div>
    );
  }

  const summary = insights.summary as string | undefined;
  const bottleneckReason = (insights.bottleneck_reason ?? insights.blocking_reason) as string | undefined;

  return (
    <div className="analyzer-panel">
      <div className="analyzer-panel-head">
        <span>{title}</span>
        {score !== null && (
          <span className={`analyzer-score ${scoreCls}`}>{score}</span>
        )}
      </div>

      {keyMetrics.length > 0 && (
        <div className="analyzer-metrics">
          {keyMetrics.map((m, i) => (
            <div key={i} className="analyzer-metric">
              <span className="analyzer-metric-label">{m.label}</span>
              <span className={`analyzer-metric-value ${m.highlight ? "highlight" : ""}`}>{m.value}</span>
            </div>
          ))}
        </div>
      )}

      {(summary || bottleneckReason) && (
        <div className="analyzer-section">
          <div
            className="analyzer-section-head"
            onClick={() => setInsightsOpen(o => !o)}
          >
            <span className="insights-toggle">{insightsOpen ? "▼" : "▶"}</span>
            AI Insights
          </div>
          {insightsOpen && (
            <div className="analyzer-section-body">
              {summary && <div className="analyzer-summary">{summary}</div>}
              {bottleneckReason && !summary && (
                <div className="analyzer-summary">{bottleneckReason}</div>
              )}
              <EvidenceBlock items={evidenceSql} />
            </div>
          )}
        </div>
      )}

      {issues.length > 0 && (
        <div className="analyzer-section">
          <div
            className="analyzer-section-head"
            onClick={() => setIssuesOpen(o => !o)}
          >
            <span className="insights-toggle">{issuesOpen ? "▼" : "▶"}</span>
            Issues
            <span className="cnt"> {issues.length}</span>
          </div>
          {issuesOpen && (
            <div>
              {issues.map((issue, i) => (
                <div key={i} className="issue-row">
                  <span className={`sev sev-${issue.severity}`} />
                  <span className="issue-text">{issue.description}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {suggestions.length > 0 && (
        <div className="analyzer-section">
          <div
            className="analyzer-section-head"
            onClick={() => setSuggestionsOpen(o => !o)}
          >
            <span className="insights-toggle">{suggestionsOpen ? "▼" : "▶"}</span>
            Suggestions
            <span className="cnt"> {suggestions.length}</span>
          </div>
          {suggestionsOpen && (
            <div className="analyzer-section-body">
              {suggestions.map((s, i) => (
                <div key={i} className="analyzer-suggestion">{s}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
