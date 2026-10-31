import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "cn";
import type { DomainView, MetricStatus } from "../../api/monitoring";
import { collectAnomalies, type Anomaly } from "./anomalies";
import { Evidence } from "./Evidence";
import { MetricChart } from "./MetricChart";
import { formatMove, formatValue } from "./format";
import { STATUS_ACCENT } from "./status";

const TABS: { status: MetricStatus; label: string; empty: string }[] = [
  { status: "alert", label: "Needs attention", empty: "Nothing is outside its normal range the wrong way." },
  { status: "watch", label: "Watching", empty: "Nothing unusual among metrics that have no bad direction set." },
  { status: "good", label: "Better than usual", empty: "Nothing is outside its normal range the good way." },
];

function Row({ a }: { a: Anomaly }) {
  const { metric: m, domain } = a;
  const j = m.judgement;
  const title = m.label || m.name || m.metric_id;
  return (
    <Collapsible id={`feed-${a.id}`} data-slot="ops-row" data-status={m.status} className={cn("scroll-mt-3 rounded-xl border bg-card transition-colors duration-150", STATUS_ACCENT[m.status])}>
      <CollapsibleTrigger className="grid w-full cursor-pointer grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1.4fr)_120px] items-center gap-x-4 gap-y-1 rounded-md p-3 text-left hover:bg-accent max-md:grid-cols-[minmax(0,1fr)_auto]">
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <strong className="text-sm font-medium break-words">{title}</strong>
          <Badge variant="outline">{domain.title}</Badge>
        </span>
        <span className="grid text-right md:text-left">
          <span className="text-sm font-semibold tabular-nums">{formatValue(m.value, m.unit, m.display)}</span>
          <span className="text-xs text-muted-foreground">expected {formatValue(j?.expected, m.unit, m.display)}</span>
        </span>
        <span className="text-sm text-muted-foreground max-md:col-span-full">{formatMove(j?.worse_by, j?.unit, m.display) || j?.reason}</span>
        <span className="max-md:col-span-full"><MetricChart metric={m} height={32} /></span>
      </CollapsibleTrigger>
      <CollapsibleContent><Evidence metric={m} /></CollapsibleContent>
    </Collapsible>
  );
}

/** Metrics outside their normal range, worst first. Each row opens to the evidence behind the verdict. */
export function AnomalyFeed({ domains }: { domains: DomainView[] }) {
  const [tab, setTab] = useState<MetricStatus>("alert");
  const all = collectAnomalies(domains);
  const count = (s: MetricStatus) => all.filter((a) => a.metric.status === s).length;
  const current = TABS.find((t) => t.status === tab) ?? TABS[0];
  const rows = all.filter((a) => a.metric.status === tab);
  return (
    <section data-slot="ops-feed" aria-label="Anomalies" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="m-0 text-lg font-semibold tracking-tight">Anomalies</h3>
        <ToggleGroup type="single" variant="outline" size="sm" className="flex-wrap" aria-label="Show" value={tab} onValueChange={(value) => value && setTab(value as MetricStatus)}>
          {TABS.map((t) => (
            <ToggleGroupItem key={t.status} value={t.status}>
              {t.label} <span className="tabular-nums text-muted-foreground">{count(t.status)}</span>
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      {rows.length === 0 ? <p className="text-sm text-muted-foreground">{current.empty}</p> : <div className="flex flex-col gap-2">{rows.map((a) => <Row key={a.id} a={a} />)}</div>}
      <p className="text-xs text-muted-foreground">How verdicts are made: each metric is compared with the median of its same weekday over the last 4 weeks, normal range is 3 spreads either side.</p>
    </section>
  );
}
