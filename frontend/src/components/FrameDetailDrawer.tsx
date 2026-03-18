import { useEffect, useState } from "react";
import type { JankFrame, EvidenceSQL } from "../api/types";

interface Props {
  frame: JankFrame | null;
  onClose: () => void;
}

function EvidenceItem({ ev }: { ev: EvidenceSQL }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="evidence-item">
      <div className="evidence-header" onClick={() => setOpen(!open)}>
        <span className="evidence-toggle">{open ? "▼" : "▶"}</span>
        <span className="evidence-label">{ev.label}</span>
      </div>
      <div className="evidence-conclusion">{ev.conclusion}</div>
      {open && (
        <pre className="evidence-sql">{ev.sql}</pre>
      )}
    </div>
  );
}

export default function FrameDetailDrawer({ frame, onClose }: Props) {
  useEffect(() => {
    if (!frame) return;
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [frame, onClose]);

  if (!frame || !frame.analysis) return null;

  const f = frame;
  const a = frame.analysis!;
  const type = f.jank_type || f.present_type || "";
  const side = a.side || "";
  const severity = a.severity || "";
  const evidences = a.evidence_sql || [];

  return (
    <div className="drawer-overlay" onClick={onClose}>
      <div className="drawer-panel" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-header">
          <div className="drawer-header-top">
            <span className="drawer-title">Frame Analysis</span>
            <button className="drawer-close" onClick={onClose} aria-label="Close">✕</button>
          </div>
          <div className="drawer-tags">
            <span className="drawer-tag tag-type">{type}</span>
            {side && <span className={`drawer-tag tag-${side}`}>{side === "app" ? "App" : "SF"}</span>}
            {severity && <span className={`drawer-tag tag-${severity}`}>{severity}</span>}
            <span className="drawer-tag tag-type">+{f.overrun_ms}ms</span>
          </div>
        </div>
        <div className="drawer-body">
          {/* Bottleneck */}
          {a.bottleneck_function && (
            <div className="drawer-kv">
              <span className="drawer-kv-label">Bottleneck</span>
              <span className="drawer-kv-value">
                {a.bottleneck_function}
                {a.bottleneck_reason && ` — ${a.bottleneck_reason}`}
              </span>
            </div>
          )}
          {a.root_cause_category && (
            <div className="drawer-kv">
              <span className="drawer-kv-label">Category</span>
              <span className={`frame-analysis-tag tag-${severity || "medium"}`}>
                {a.root_cause_category}
              </span>
            </div>
          )}

          {/* Flow Description */}
          {a.flow_description && (
            <>
              <div className="drawer-divider" />
              <p className="drawer-flow">{a.flow_description}</p>
            </>
          )}

          {/* CPU Core Scheduling Distribution */}
          {f.cpu_scheduling && Object.keys(f.cpu_scheduling).length > 0 && (
            <>
              <div className="drawer-divider" />
              <div className="drawer-kv">
                <span className="drawer-kv-label">🔧 核心调度分布</span>
              </div>
              <div className="cpu-sched-grid">
                {Object.entries(f.cpu_scheduling)
                  .sort(([, a], [, b]) => b - a)
                  .map(([core, ms]) => (
                    <div key={core} className="cpu-sched-item">
                      <span className="cpu-sched-core">{core}</span>
                      <div className="cpu-sched-bar-bg">
                        <div
                          className="cpu-sched-bar"
                          style={{
                            width: `${Math.min(100, (ms as number) / Math.max(...Object.values(f.cpu_scheduling!)) * 100)}%`,
                          }}
                        />
                      </div>
                      <span className="cpu-sched-ms">{(ms as number).toFixed(1)}ms</span>
                    </div>
                  ))}
              </div>
            </>
          )}

          {/* Evidence SQL */}
          {evidences.length > 0 && (
            <div className="evidence-section">
              <div className="evidence-section-title">SQL Evidence</div>
              {evidences.map((ev, i) => (
                <EvidenceItem key={i} ev={ev} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}