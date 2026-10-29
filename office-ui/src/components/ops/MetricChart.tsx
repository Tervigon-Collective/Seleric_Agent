import type { MetricView } from "../../api/monitoring";

/** Last 28 days as a line; the latest day's normal range is the band at the right edge, with the latest point marked. */
export function MetricChart({ metric, height = 64 }: { metric: MetricView; height?: number }) {
  const points = metric.series;
  if (points.length < 2) return <div className="ops-chart-empty">No daily history yet</div>;
  const w = 240;
  const j = metric.judgement;
  const values = points.map((p) => p.value);
  const lo = Math.min(...values, j?.low ?? Infinity);
  const hi = Math.max(...values, j?.high ?? -Infinity);
  const span = hi - lo || 1;
  const x = (i: number) => 3 + (i * (w - 6)) / (points.length - 1);
  const y = (v: number) => 4 + (1 - (v - lo) / span) * (height - 8);
  const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  const band = j?.low !== null && j?.low !== undefined && j.high !== null ? { top: y(j.high), bottom: y(j.low) } : null;
  return (
    <svg
      className={`ops-chart status-${metric.status}`}
      viewBox={`0 0 ${w} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`${metric.label || metric.metric_id}, last ${points.length} days`}
    >
      {band && <rect className="band" x={w - 22} width={22} y={band.top} height={Math.max(band.bottom - band.top, 1)}><title>Normal range for the latest day</title></rect>}
      <polyline className="line" points={line} fill="none" vectorEffect="non-scaling-stroke" />
      <circle className="end" cx={x(points.length - 1)} cy={y(last.value)} r={3.5} />
    </svg>
  );
}
