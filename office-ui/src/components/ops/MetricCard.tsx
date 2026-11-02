import type { MetricView } from "../../api/monitoring";
import { MetricChart } from "./MetricChart";
import { metricAnchor } from "./anomalies";
import { formatMove, formatValue } from "./format";
import { StatusBadge } from "./status";

/** A small metric card: name and verdict, the value, a 28-day trend, and one line on what the verdict means. */
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
    <div data-slot="ops-card" data-status={metric.status} id={metricAnchor(domainId, metric.metric_id)} aria-label={title} className="flex scroll-mt-3 flex-col gap-0.5 rounded-lg border bg-background px-2.5 py-2">
      <span className="text-[12.5px] break-words text-muted-foreground" title={metric.metric_id}>{title}</span>
      <StatusBadge status={metric.status} />
      <div className="font-serif text-xl leading-tight font-normal tracking-tight tabular-nums">{formatValue(metric.value, metric.unit, metric.display)}</div>
      <MetricChart metric={metric} height={26} />
      {j && j.expected !== null && (
        <small className="text-[11.5px] text-muted-foreground">expected {formatValue(j.expected, metric.unit, metric.display)}{j.basis === "weekday" ? " for this weekday" : ""}</small>
      )}
      {note && <small className="text-[11.5px] text-muted-foreground">{note}</small>}
    </div>
  );
}
