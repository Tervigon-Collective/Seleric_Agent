import { Line, LineChart, ReferenceArea, XAxis, YAxis } from "recharts";
import { ChartContainer } from "@/components/ui/chart";
import type { MetricView } from "../../api/monitoring";

const STROKE: Record<string, string> = { alert: "var(--danger)", watch: "var(--warning)", good: "var(--success)" };

/** Last 28 days as a line; the latest day's normal range is the band at the right edge, with the latest point marked. */
export function MetricChart({ metric, height = 64 }: { metric: MetricView; height?: number }) {
  const points = metric.series;
  if (points.length < 2) return <p className="text-xs text-muted-foreground">No daily history yet</p>;
  const j = metric.judgement;
  const label = metric.label || metric.metric_id;
  const stroke = STROKE[metric.status] ?? "var(--chart-1)";
  const data = points.map((p, i) => ({ i, value: p.value }));
  const last = data.length - 1;
  const band = j?.low !== null && j?.low !== undefined && j.high !== null && j.high !== undefined ? { low: j.low, high: j.high } : null;
  return (
    <div role="img" aria-label={`${label}, last ${points.length} days`}>
      <ChartContainer config={{ value: { label, color: stroke } }} className="w-full" style={{ height }}>
        <LineChart data={data} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
          <XAxis dataKey="i" hide />
          <YAxis hide domain={[(min: number) => Math.min(min, band?.low ?? min), (max: number) => Math.max(max, band?.high ?? max)]} />
          {band && <ReferenceArea x1={Math.max(last - 1, 0)} x2={last} y1={band.low} y2={band.high} fill={stroke} fillOpacity={0.15} ifOverflow="extendDomain" />}
          <Line
            dataKey="value"
            type="monotone"
            stroke={stroke}
            strokeWidth={2}
            isAnimationActive={false}
            dot={(dot: any) => (dot.index === last
              ? <circle key={dot.key} cx={dot.cx} cy={dot.cy} r={3.5} fill={stroke} />
              : <g key={dot.key} />)}
          />
        </LineChart>
      </ChartContainer>
    </div>
  );
}
