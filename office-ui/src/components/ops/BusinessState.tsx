import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { Item, ItemDescription } from "@/components/ui/item";
import { cn } from "cn";
import type { DomainView } from "../../api/monitoring";
import { headlineMetrics } from "./anomalies";
import { ago, formatValue } from "./format";
import { STATUS_ACCENT } from "./status";

const DOT: Record<string, string> = { alert: "bg-destructive", watch: "bg-[var(--warning)]", ok: "bg-[var(--success)]" };

/** The state the agent answers "how is the business doing" from: verdict totals, data day, headline metrics, and anything that makes the numbers unreliable. */
export function BusinessState({ domains }: { domains: DomainView[] }) {
  const live = domains.filter((d) => d.enabled && d.snapshot);
  const metrics = live.flatMap((d) => d.snapshot?.metrics ?? []);
  const n = (s: string) => metrics.filter((m) => m.status === s).length;
  const stale = live.filter((d) => d.snapshot?.stale);
  const partial = live.filter((d) => d.snapshot && d.snapshot.status !== "OK");
  const noVerdict = n("no_data");
  const days = [...new Set(live.map((d) => d.snapshot!.as_of))].sort();
  const oldest = Math.max(0, ...live.map((d) => d.snapshot!.age_s));
  const headline = headlineMetrics(domains);
  const alerts = n("alert");
  return (
    <Card data-slot="ops-state" aria-label="Business state">
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
          <span className={cn("mt-1.5 size-2.5 shrink-0 rounded-full", DOT[alerts ? "alert" : n("watch") ? "watch" : "ok"])} aria-hidden />
          <div className="min-w-0 flex-[1_1_260px]">
            <h3 className="m-0 text-xl font-semibold tracking-tight">{alerts ? `${alerts} ${alerts === 1 ? "metric needs" : "metrics need"} attention` : "No metric needs attention"}</h3>
            <p className="m-0 text-sm text-muted-foreground">
              {n("watch")} watching · {n("good")} better than usual · {n("ok")} normal{noVerdict ? ` · ${noVerdict} without a verdict yet` : ""}
              {" · "}{metrics.length} metrics in {live.length} {live.length === 1 ? "domain" : "domains"}
            </p>
          </div>
          {live.length > 0 && (
            <span className="text-xs text-muted-foreground md:ml-auto">Data to {days.length === 1 ? days[0] : `${days[0]} – ${days[days.length - 1]}`} · oldest check {ago(oldest)}</span>
          )}
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
          <div data-slot="ops-headline" aria-label="Headline metrics" className="grid grid-cols-2 gap-2 sm:grid-cols-[repeat(auto-fill,minmax(150px,1fr))]">
            {headline.map(({ id, domain, metric: m }) => (
              <a key={id} href={`#${id}`} className="min-w-0 text-inherit no-underline">
                <Item variant="outline" size="sm" className={cn("h-full flex-col items-start gap-0.5", STATUS_ACCENT[m.status])}>
                  <ItemDescription className="w-full truncate">{m.label || m.name || m.metric_id}</ItemDescription>
                  <b className="text-xl font-medium tracking-tight tabular-nums">{formatValue(m.value, m.unit, m.display)}</b>
                  <small className="text-xs text-muted-foreground">{domain.title}</small>
                </Item>
              </a>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
