import type { MetricView } from "../../api/monitoring";
import { MetricChart } from "./MetricChart";
import { formatMove, formatValue } from "./format";

const RULE = (m: MetricView) =>
  !m.rule ? "Outside its normal range the bad way (default)"
    : m.rule.on === "anomaly" ? "Outside its normal range the bad way"
      : `Worse than expected by ${m.rule.threshold}`;

/** Everything the verdict was based on, so a person (or a later diagnosis step) can check it without leaving the page. */
export function Evidence({ metric }: { metric: MetricView }) {
  const j = metric.judgement;
  const fmt = (v: number | null | undefined) => formatValue(v, metric.unit, metric.display);
  const rows: [string, string][] = [
    ["Observed", `${fmt(j?.observed ?? metric.value)} on ${j?.observed_on ?? metric.observed_on ?? "unknown day"}`],
    ["Expected", j && j.expected !== null ? `${fmt(j.expected)} (${j.basis === "weekday" ? "median of the same weekday, last 4 weeks" : j.basis === "trailing" ? "median of recent days" : "no baseline"})` : "–"],
    ["Normal range", j && j.low !== null && j.high !== null ? `${fmt(j.low)} to ${fmt(j.high)}` : "–"],
    ["Distance from normal", j?.z !== null && j?.z !== undefined ? `${j.z > 0 ? "+" : ""}${j.z.toFixed(1)} spreads (${j.direction})` : "–"],
    ["Against expected", formatMove(j?.worse_by, j?.unit, metric.display) || "–"],
    ["Bad direction", metric.direction_bad ?? "not set"],
    ["Alert rule", RULE(metric)],
    ["History used", j ? `${j.history_points} prior days` : "–"],
    ["Source", `${metric.freshness.toLowerCase()}${metric.quality_flags.length ? ` · ${metric.quality_flags.join(", ")}` : ""}`],
  ];
  return (
    <div className="flex flex-col gap-3 border-t p-3">
      <p className="m-0 text-sm">{j?.reason || "No verdict."}</p>
      <MetricChart metric={metric} height={96} />
      <dl className="m-0 grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-x-5 gap-y-2">
        {rows.map(([k, v]) => <div key={k}><dt className="text-xs tracking-wide text-muted-foreground uppercase">{k}</dt><dd className="m-0 text-sm tabular-nums break-words">{v}</dd></div>)}
      </dl>
      <p className="m-0 font-mono text-xs text-muted-foreground" title="Stable reference for this metric">{metric.metric_id}</p>
    </div>
  );
}
