import { Alert, AlertDescription } from "@/components/ui/alert";
import { cn } from "cn";
import type { DomainView, MetricStatus } from "../../api/monitoring";
import { headlineMetrics } from "./anomalies";
import { formatValue } from "./format";

const CHIP: Record<string, string> = {
  alert: "bg-[var(--danger-soft)] text-[var(--danger)] border-transparent",
  watch: "bg-[var(--warning-soft)] text-[var(--warning)] border-transparent",
  good: "bg-[var(--success-soft)] text-[var(--success)] border-transparent",
  ok: "text-muted-foreground",
};

/** The state the agent answers "how is the business doing" from: verdict totals, headline metrics, and anything that makes the numbers unreliable. */
export function BusinessState({ domains, onJump }: { domains: DomainView[]; onJump?: (id: string) => void }) {
  const live = domains.filter((d) => d.enabled && d.snapshot);
  const metrics = live.flatMap((d) => d.snapshot?.metrics ?? []);
  const n = (s: MetricStatus) => metrics.filter((m) => m.status === s).length;
  const stale = live.filter((d) => d.snapshot?.stale);
  const partial = live.filter((d) => d.snapshot && d.snapshot.status !== "OK");
  const noVerdict = n("no_data");
  const headline = headlineMetrics(domains);
  // Pick the column count (2 to 6) that leaves the fewest empty cells in the last row, so the tiles form an even block.
  const cols = [5, 6, 4, 3, 2].reduce((best, c) => (((c - (headline.length % c)) % c) < ((best - (headline.length % best)) % best) ? c : best), 5);
  const alerts = n("alert");
  const chips: [string, number, string][] = [
    ["alert", alerts, "need attention"], ["watch", n("watch"), "watching"], ["good", n("good"), "better"], ["ok", n("ok"), "normal"],
  ];
  return (
    <section data-slot="ops-state" aria-label="Business state" className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border bg-card px-3.5 py-2.5 shadow-[var(--shadow)]">
        <h3 className="m-0 font-serif text-lg font-normal tracking-tight">{alerts ? `${alerts} ${alerts === 1 ? "metric needs" : "metrics need"} attention` : "No metric needs attention"}</h3>
        <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
          {chips.filter(([k, c]) => c > 0 || k === "alert").map(([k, c, label]) => (
            <li key={k} className={cn("rounded-full border px-2.5 py-0.5 text-[13px]", CHIP[k])}><b className="font-semibold tabular-nums">{c}</b> {label}</li>
          ))}
          {noVerdict > 0 && <li className="rounded-full border px-2.5 py-0.5 text-[13px] text-muted-foreground"><b className="font-semibold tabular-nums">{noVerdict}</b> without a verdict yet</li>}
        </ul>
        <span className="text-xs text-muted-foreground md:ml-auto">{metrics.length} metrics in {live.length} {live.length === 1 ? "domain" : "domains"}</span>
      </div>
      {(stale.length > 0 || partial.length > 0) && (
        <Alert>
          <AlertDescription>
            {stale.length > 0 && <p className="m-0">Out of date: {stale.map((d) => d.title).join(", ")}. Verdicts there may not reflect today.</p>}
            {partial.length > 0 && <p className="m-0">Some metrics could not be read in: {partial.map((d) => d.title).join(", ")}.</p>}
          </AlertDescription>
        </Alert>
      )}
      {headline.length > 0 && (
        <div data-slot="ops-headline" aria-label="Headline metrics" style={{ "--cols": cols } as React.CSSProperties} className="stagger grid grid-cols-2 gap-2 md:grid-cols-[repeat(var(--cols),minmax(0,1fr))]">
          {headline.map(({ id, domain, metric: m }) => (
            <a key={id} href={`#${id}`} onClick={onJump ? (e) => { e.preventDefault(); onJump(id); } : undefined} className="min-w-0 text-inherit no-underline">
              <div className="flex h-full flex-col gap-px rounded-lg border bg-card px-3 py-2 transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-px hover:border-[var(--line-strong)] hover:shadow-[var(--shadow)]">
                <span className="truncate text-xs text-muted-foreground">{m.label || m.name || m.metric_id}</span>
                <b className={cn("font-serif text-[21px] leading-tight font-normal tracking-tight tabular-nums", m.status === "alert" && "text-destructive")}>{formatValue(m.value, m.unit, m.display)}</b>
                <small className="text-[11.5px] text-muted-foreground">{domain.title}</small>
              </div>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}
