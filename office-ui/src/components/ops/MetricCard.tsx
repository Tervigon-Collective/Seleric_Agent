import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "cn";
import type { MetricView } from "../../api/monitoring";
import { MetricChart } from "./MetricChart";
import { metricAnchor } from "./anomalies";
import { formatMove, formatValue } from "./format";
import { STATUS_ACCENT, StatusBadge } from "./status";

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
    <Card size="sm" data-slot="ops-card" data-status={metric.status} id={metricAnchor(domainId, metric.metric_id)} aria-label={title} className={cn("scroll-mt-3 transition-shadow duration-200 hover:shadow-md", STATUS_ACCENT[metric.status])}>
      <CardHeader>
        <CardTitle className="break-words" title={metric.metric_id}>{title}</CardTitle>
        <CardAction><StatusBadge status={metric.status} /></CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        <div className="text-2xl font-medium tracking-tight tabular-nums">{formatValue(metric.value, metric.unit, metric.display)}</div>
        {j && j.expected !== null && (
          <div className="text-xs text-muted-foreground">
            expected {formatValue(j.expected, metric.unit, metric.display)}
            {j.basis === "weekday" ? " for this weekday" : ""}
          </div>
        )}
        <MetricChart metric={metric} />
        {note && <p className="text-xs text-muted-foreground">{note}</p>}
      </CardContent>
    </Card>
  );
}
