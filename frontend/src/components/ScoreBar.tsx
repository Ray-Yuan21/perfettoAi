interface Props {
  score: number | null;
  jankRate: number;
  jankFrames: number;
  totalFrames: number;
  p95?: number;
  max?: number;
}

export default function ScoreBar({ score, jankRate, jankFrames, totalFrames, p95, max }: Props) {
  const cls = score === null ? "" : score >= 80 ? "high" : score >= 50 ? "mid" : "low";

  return (
    <div className="score-bar">
      <div className={`score-num ${cls}`}>{score ?? "-"}</div>
      <div className="score-meta">
        <div>
          Jank <b>{jankRate}%</b> ({jankFrames}/{totalFrames})
        </div>
        <div>
          P95 <b>{p95 ?? "-"}ms</b>&ensp;Max <b>{max ?? "-"}ms</b>
        </div>
      </div>
    </div>
  );
}
