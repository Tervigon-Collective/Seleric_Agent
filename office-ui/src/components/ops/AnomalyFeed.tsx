import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { DomainView, MetricStatus } from "../../api/monitoring";
import { collectAnomalies, type Anomaly } from "./anomalies";
import { Evidence } from "./Evidence";
import { formatMove, formatValue } from "./format";
import { investigate } from "./investigate";

const TABS: { status: MetricStatus; label: string; empty: string }[] = [
  { status: "alert", label: "Needs attention", empty: "Nothing is outside its normal range the wrong way." },
  { status: "watch", label: "Watching", empty: "Nothing unusual among metrics that have no bad direction set." },
  { status: "good", label: "Better", empty: "Nothing is outside its normal range the good way." },
];

/** One shared column template so the header and every row line up. */
const COLS = "grid grid-cols-[minmax(0,2fr)_8rem_8rem_minmax(0,1.6fr)_6.5rem] items-center gap-x-4 max-md:grid-cols-[minmax(0,1fr)_auto]";

function Row({ a }: { a: Anomaly }) {
  const { metric: m, domain } = a;
  const j = m.judgement;
  const title = m.label || m.name || m.metric_id;
  return (
    <Collapsible id={`feed-${a.id}`} data-slot="ops-row" data-status={m.status} className="scroll-mt-3 border-b">
      <div className={`${COLS} px-2 py-2.5`}>
        <CollapsibleTrigger className="min-w-0 cursor-pointer text-left" aria-label={`${title}, show evidence`}>
          <strong className="block text-sm font-medium break-words">{title}</strong>
          <span className="text-xs text-muted-foreground">{domain.title}</span>
        </CollapsibleTrigger>
        <span className="text-right text-sm tabular-nums max-md:text-left">{formatValue(m.value, m.unit, m.display)}</span>
        <span className="text-right text-sm text-muted-foreground tabular-nums max-md:hidden">{formatValue(j?.expected, m.unit, m.display)}</span>
        <span className="text-sm text-muted-foreground max-md:col-span-1 max-md:row-start-2">{formatMove(j?.worse_by, j?.unit, m.display) || j?.reason}</span>
        <Button type="button" variant="outline" size="sm" className="justify-self-end max-md:row-span-2 max-md:row-start-1 max-md:col-start-2" aria-label={`Investigate ${title}`} onClick={() => void investigate(a)}>Investigate</Button>
      </div>
      <CollapsibleContent><Evidence metric={m} /></CollapsibleContent>
    </Collapsible>
  );
}

/** Metrics outside their normal range, worst first, as one aligned list. Investigate asks Seleric; a row opens to the evidence behind its verdict. */
export function AnomalyFeed({ domains }: { domains: DomainView[] }) {
  const [tab, setTab] = useState<MetricStatus>("alert");
  const all = collectAnomalies(domains);
  const count = (s: MetricStatus) => all.filter((a) => a.metric.status === s).length;
  const current = TABS.find((t) => t.status === tab) ?? TABS[0];
  const rows = all.filter((a) => a.metric.status === tab);
  return (
    <section data-slot="ops-feed" aria-label="Anomalies" className="flex flex-col gap-3">
      <ToggleGroup type="single" variant="outline" size="sm" aria-label="Show" value={tab} onValueChange={(value) => value && setTab(value as MetricStatus)} className="w-fit">
        {TABS.map((t) => (
          <ToggleGroupItem key={t.status} value={t.status}>
            {t.label} <span className="tabular-nums text-muted-foreground">{count(t.status)}</span>
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      {rows.length === 0 ? <p className="m-0 py-2 text-sm text-muted-foreground">{current.empty}</p> : (
        <div>
          <div className={`${COLS} border-b px-2 pb-1.5 text-xs text-muted-foreground max-md:hidden`} aria-hidden="true">
            <span>Metric</span><span className="text-right">Value</span><span className="text-right">Expected</span><span>Change</span><span />
          </div>
          {rows.map((a) => <Row key={a.id} a={a} />)}
        </div>
      )}
      <p className="m-0 max-w-prose text-xs text-muted-foreground">Worst first. Each metric is compared with the median of its same weekday over the last 4 weeks; normal range is 3 spreads either side.</p>
    </section>
  );
}
