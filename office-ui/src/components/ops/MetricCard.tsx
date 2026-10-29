import type { MetricView } from "../../api/monitoring";
import { MetricChart } from "./MetricChart";
import { metricAnchor } from "./anomalies";
import { STATUS_LABEL, formatMove, formatValue } from "./format";

export function MetricCard({ metric, domainId }: { metric: MetricView; domainId: string }) {
  const j = metric.judgement;
  const title = metric.label || metric.name || metric.metric_id;
  const note = metric.status === "no_data"
    ? j?.reason ?? "No daily history"
    : metric.status === "ok"
      ? j && j.low !== null && j.high !== null ? `Within normal range, ${formatValue(j.low, metric.unit, metric.display)} to ${formatValue(j.high, metric.unit, metric.display)}` : "Within normal range"
    : metric.polarity_known
      ? formatMove(j?.worse_by, j?.unit, metric.display)
      : "Direction not set in the catalogue, so this is watched, not alerted";
  return (
    <article className={`ops-card status-${metric.status}`} id={metricAnchor(domainId, metric.metric_id)} aria-label={title}>
      <header>
        <h4 title={metric.metric_id}>{title}</h4>
        <span className={`ops-pill status-${metric.status}`}>{STATUS_LABEL[metric.status]}</span>
      </header>
      <div className="ops-value">{formatValue(metric.value, metric.unit, metric.display)}</div>
      {j && j.expected !== null && (
        <div className="ops-expected">
          expected {formatValue(j.expected, metric.unit, metric.display)}
          {j.basis === "weekday" ? " for this weekday" : ""}
        </div>
      )}
      <MetricChart metric={metric} />
      {note && <p className="ops-note">{note}</p>}
    </article>
  );
}
