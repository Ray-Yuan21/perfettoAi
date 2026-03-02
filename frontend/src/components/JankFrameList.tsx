import { useState } from "react";
import type { JankFrame, CallTreeNode } from "../api/types";

type JumpFn = (
  ts: number,
  dur: number,
  processName: string,
  upid: number,
  jankCategory?: string,
  sliceType?: "frame" | "slice"
) => void;

interface Props {
  frames: JankFrame[];
  onJump: JumpFn;
  onSelectFrame?: (frame: JankFrame) => void;
}

function TreeNode({
  node,
  depth,
  onJump,
  processName,
  upid,
  jankCategory,
}: {
  node: CallTreeNode;
  depth: number;
  onJump: JumpFn;
  processName: string;
  upid: number;
  jankCategory?: string;
}) {
  const [open, setOpen] = useState(depth < 2);
  const hasChildren = node.children.length > 0;
  const indent = depth * 16;
  const canJump = node.ts != null && node.dur != null;

  return (
    <>
      <div className="tree-row" style={{ paddingLeft: indent + 8 }}>
        <span
          className="tree-toggle"
          onClick={(e) => {
            e.stopPropagation();
            if (hasChildren) setOpen(!open);
          }}
        >
          {hasChildren ? (open ? "▼" : "▶") : "·"}
        </span>
        <span className="tree-name" title={node.name}>
          {node.name}
        </span>
        <span className="tree-dur">{node.dur_ms}ms</span>
        {node.self_ms > 0.5 && (
          <span className="tree-self">self {node.self_ms}ms</span>
        )}
        {node.thread !== "main" && node.thread && (
          <span className="tree-thread">{node.thread}</span>
        )}
        {canJump && (
          <span
            className="tree-jump"
            title="Jump to this slice in Perfetto"
            onClick={(e) => {
              e.stopPropagation();
              onJump(node.ts!, node.dur!, processName, upid, jankCategory, "slice");
            }}
          >
            ↗
          </span>
        )}
      </div>
      {open &&
        node.children.map((child, i) => (
          <TreeNode
            key={i}
            node={child}
            depth={depth + 1}
            onJump={onJump}
            processName={processName}
            upid={upid}
            jankCategory={jankCategory}
          />
        ))}
    </>
  );
}

export default function JankFrameList({ frames, onJump, onSelectFrame }: Props) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  if (frames.length === 0) return null;

  return (
    <>
      <div className="section-head">
        Jank Frames<span className="cnt">({frames.length})</span>
      </div>
      {frames.map((f, i) => {
        const type = f.jank_type || f.present_type || "";
        const proc = f.process_name
          ? f.process_name.split(":")[0].split(".").pop()
          : "";
        const displayMs =
          f.overrun_ms > 0 ? `+${f.overrun_ms}` : String(f.dur_ms);
        const isExpanded = expandedIdx === i;
        const hasCriticalPath = f.critical_path && f.critical_path.length > 0;
        const hasCallTree = f.call_tree && f.call_tree.length > 0;
        const hasAnalysis = !!f.analysis;
        const hasDetail = hasCriticalPath || hasCallTree || hasAnalysis;

        const rootCauseCategory = f.analysis?.root_cause_category || "";
        const bottleneck = f.analysis?.bottleneck_function || "";
        const severity = f.analysis?.severity || "";
        const side = f.analysis?.side || "";
        const fallbackCause = f.root_cause ? f.root_cause.split("(")[0].trim() : "";
        const displayCause = rootCauseCategory || fallbackCause;

        return (
          <div key={i} className={`frame-item ${isExpanded ? "expanded" : ""}`}>
            <div
              className="frame-row-enhanced"
              onClick={() =>
                onJump(f.ts, f.dur, f.process_name || "", f.upid || 0, f.jank_type, "frame")
              }
            >
              <div className="frame-row-top">
                <span className="frame-dur">{displayMs}ms</span>
                <span className="frame-type-short" title={type}>{type}</span>
                {side && (
                  <span className={`frame-side tag-${side}`}>
                    {side === "app" ? "App" : "SF"}
                  </span>
                )}
                {severity && (
                  <span className={`frame-severity tag-${severity}`}>{severity}</span>
                )}
                {hasDetail && (
                  <button
                    className="frame-expand"
                    title="Show details"
                    onClick={(e) => {
                      e.stopPropagation();
                      setExpandedIdx(isExpanded ? null : i);
                    }}
                  >
                    {isExpanded ? "▲" : "▼"}
                  </button>
                )}
                {onSelectFrame && hasAnalysis && (
                  <button
                    className="frame-detail-btn"
                    title="View frame analysis"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectFrame(f);
                    }}
                  >
                    ⋯
                  </button>
                )}
                <span className="frame-jump">&rarr;</span>
              </div>
              {(displayCause || bottleneck || proc) && (
                <div className="frame-row-bottom">
                  {displayCause && (
                    <span className="frame-cause" title={displayCause}>{displayCause}</span>
                  )}
                  {bottleneck && !displayCause && (
                    <span className="frame-bottleneck" title={bottleneck}>{bottleneck}</span>
                  )}
                  {proc && (
                    <span className="frame-proc" title={f.process_name}>{proc}</span>
                  )}
                </div>
              )}
            </div>

            {isExpanded && (
              <div className="frame-detail">
                {f.root_cause && (
                  <div className="root-cause">
                    <span className="root-cause-label">Root Cause:</span>
                    {f.root_cause}
                  </div>
                )}

                {hasCriticalPath && (
                  <div className="critical-path">
                    <div className="detail-label">Critical Path</div>
                    <div className="path-chain">
                      {f.critical_path!.map((node, j) => {
                        const isLeaf = j === f.critical_path!.length - 1;
                        const canJump = node.ts != null && node.dur != null;
                        return (
                          <span key={j} className="path-node">
                            {j > 0 && <span className="path-arrow">→</span>}
                            <span
                              className={`path-name ${isLeaf ? "path-leaf" : ""} ${canJump ? "path-clickable" : ""}`}
                              title={canJump ? `Click to jump · ${node.dur_ms}ms` : `${node.dur_ms}ms`}
                              onClick={
                                canJump
                                  ? () => onJump(node.ts!, node.dur!, f.process_name || "", f.upid || 0, f.jank_type, "slice")
                                  : undefined
                              }
                            >
                              {node.name}
                            </span>
                            <span className="path-dur">{node.dur_ms}ms</span>
                          </span>
                        );
                      })}
                    </div>
                  </div>
                )}

                {hasCallTree && (
                  <div className="call-tree">
                    <div className="detail-label">Call Tree</div>
                    {f.call_tree!.map((root, j) => (
                      <TreeNode
                        key={j}
                        node={root}
                        depth={0}
                        onJump={onJump}
                        processName={f.process_name || ""}
                        upid={f.upid || 0}
                        jankCategory={f.jank_type}
                      />
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}