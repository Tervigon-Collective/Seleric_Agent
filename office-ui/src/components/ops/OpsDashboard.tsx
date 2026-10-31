import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { useMonitoringStore } from "../../stores/monitoring";
import type { DomainView } from "../../api/monitoring";
import { useShellStore } from "../../stores/shell";
import { AnomalyFeed } from "./AnomalyFeed";
import { BusinessState } from "./BusinessState";
import { MetricCard } from "./MetricCard";
import { ago } from "./format";

function DomainHeader({ domain }: { domain: DomainView }) {
  const busy = useMonitoringStore((s) => s.busy[domain.id]);
  const refresh = useMonitoringStore((s) => s.refresh);
  const snap = domain.snapshot;
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
      <div>
        <h3 className="m-0 text-lg font-semibold tracking-tight">{domain.title}</h3>
        <p className="m-0 text-sm text-muted-foreground">{domain.description}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {snap && (
          <span className={snap.stale ? "font-medium text-[var(--warning)]" : undefined}>
            Data to {snap.as_of} · checked {ago(snap.age_s)}{snap.stale ? " · out of date" : ""}
          </span>
        )}
        {snap && snap.status !== "OK" && <Badge variant="secondary">Some metrics could not be read</Badge>}
        {domain.pending && <Badge variant="secondary">Selection changed, waiting for numbers</Badge>}
        <Button variant="outline" size="sm" onClick={() => void refresh(domain.id)} disabled={!!busy || !domain.enabled}>
          {busy === "refreshing" ? <><Spinner className="size-3.5" />Refreshing…</> : "Refresh"}
        </Button>
      </div>
    </header>
  );
}

export function OpsDashboard() {
  const state = useMonitoringStore((s) => s.state);
  const loading = useMonitoringStore((s) => s.loading);
  const error = useMonitoringStore((s) => s.error);
  const openOps = useShellStore((s) => s.openOps);
  const notice = useMonitoringStore((s) => s.notice);
  if (!state) {
    if (loading && !error) return <div className="flex flex-col gap-3" role="status" aria-busy="true" aria-label="Loading the latest numbers">
      <Skeleton className="h-28 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
    </div>;
    return <div className="py-8 text-muted-foreground">{error ? <p role="alert">{error}</p> : "No data yet."}</div>;
  }
  return (
    <div className="stagger flex flex-col gap-10">
      <BusinessState domains={state.domains} />
      <AnomalyFeed domains={state.domains} />
      {state.domains.map((d) => (
        <section key={d.id} id={`ops-${d.id}`} className="flex scroll-mt-3 flex-col gap-3" aria-label={d.title}>
          <DomainHeader domain={d} />
          {notice[d.id]?.kind === "error" && <Alert variant="destructive" role="alert"><AlertDescription>{notice[d.id]?.text}</AlertDescription></Alert>}
          {!d.enabled ? (
            <p className="text-sm text-muted-foreground">Monitoring for this domain is switched off. <Button variant="link" size="sm" onClick={() => openOps("monitoring")}>Change in Monitoring</Button></p>
          ) : !d.snapshot ? (
            <p className="text-sm text-muted-foreground">No numbers yet. They appear after the first scheduled run, or press Refresh.</p>
          ) : (
            <>
              {d.snapshot.signals.length > 0 && (
                <ul className="m-0 grid list-none gap-1 p-0 text-sm text-destructive">{d.snapshot.signals.map((s) => <li key={s}>{s}</li>)}</ul>
              )}
              <div className="stagger grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-4">
                {d.snapshot.metrics.map((m) => <MetricCard key={m.metric_id} metric={m} domainId={d.id} />)}
              </div>
              {d.snapshot.metrics.length === 0 && <p className="text-sm text-muted-foreground">No metrics selected. Choose some in Monitoring.</p>}
            </>
          )}
        </section>
      ))}
    </div>
  );
}
