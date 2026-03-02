import type { Issue } from "../api/types";

interface Props {
  issues: Issue[];
}

export default function IssueList({ issues }: Props) {
  // Group by category
  const groups: Record<string, Issue[]> = {};
  issues.forEach((iss) => {
    const cat = iss.category || "other";
    (groups[cat] ??= []).push(iss);
  });

  const cats = Object.keys(groups);
  if (cats.length === 0) return null;

  return (
    <>
      {cats.map((cat) => (
        <div key={cat}>
          <div className="section-head">
            {cat}
            <span className="cnt">({groups[cat].length})</span>
          </div>
          {groups[cat].map((iss, i) => (
            <div key={i} className="issue-row">
              <span className={`sev sev-${iss.severity || "low"}`} />
              <div className="issue-text">{iss.description}</div>
            </div>
          ))}
        </div>
      ))}
    </>
  );
}
