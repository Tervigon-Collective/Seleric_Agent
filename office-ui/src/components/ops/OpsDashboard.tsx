import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useMonitoringStore } from "../../stores/monitoring";
import type { DomainView } from "../../api/monitoring";
import { useShellStore } from "../../stores/shell";
import { AnomalyFeed } from "./AnomalyFeed";
import { BusinessState } from "./BusinessState";
import { MetricCard } from "./MetricCard";
import { collectAnomalies } from "./anomalies";
import { ago, formatValue } from "./format";
import { investigate } from "./investigate";

function DomainRefresh({ domain }: { domain: DomainView }) {
  const busy = useMonitoringStore((s) => s.busy[domain.id]);
  const refresh = useMonitoringStore((s) => s.refresh);
  const snap = domain.snapshot;
  return (
    <div className="flex flex-wrap items-center gap-2 px-2.5 pb-2 text-xs text-muted-foreground">
      {snap && (
        <span className={snap.stale ? "font-medium text-[var(--warning)]" : undefined}>
          Data to {snap.as_of} · checked {ago(snap.age_s)}{snap.stale ? " · out of date" : ""}
        </span>
      )}
      {snap && snap.status !== "OK" && <Badge variant="secondary">Some metrics could not be read</Badge>}
      {domain.pending && <Badge variant="secondary">Selection changed, waiting for numbers</Badge>}
      <Button variant="outline" size="sm" className="ml-auto" onClick={() => void refresh(domain.id)} disabled={!!busy || !domain.enabled}>
        {busy === "refreshing" ? "Refreshing…" : "Refresh"}
      </Button>
    </div>
  );
}

/** The overview's two panels: the worst anomalies, and every domain at a glance. Both lead into their full tabs. */
function OverviewPanels({ domains, onTab, onJump }: { domains: DomainView[]; onTab: (tab: string) => void; onJump: (id: string) => void }) {
  const top = collectAnomalies(domains).filter((a) => a.metric.status === "alert").slice(0, 5);
  const total = collectAnomalies(domains).filter((a) => a.metric.status === "alert").length;
  return (
    <div className="@container">
      <div className="grid items-start gap-3 @3xl:grid-cols-2">
        <section data-slot="ops-top" aria-label="Top anomalies" className="flex min-w-0 flex-col rounded-xl border bg-card">
          <header className="flex items-center gap-2 border-b px-3 py-2">
            <h3 className="m-0 text-sm font-semibold">Needs attention</h3>
            <Button variant="link" size="sm" className="ml-auto h-auto p-0 text-xs" onClick={() => onTab("anomalies")}>{total > top.length ? `View all ${total}` : "View anomalies"}</Button>
          </header>
          {top.length === 0 ? <p className="m-0 px-3 py-4 text-sm text-muted-foreground">Nothing is outside its normal range the wrong way.</p> : (
            <ul className="m-0 flex list-none flex-col p-0">
              {top.map((a) => (
                <li key={a.id} className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-3 border-b px-3 py-2 last:border-b-0">
                  <span className="min-w-0"><b className="block truncate text-sm font-medium">{a.metric.label || a.metric.name || a.metric.metric_id}</b><small className="text-xs text-muted-foreground">{a.domain.title}</small></span>
                  <span className="text-right tabular-nums"><span className="block text-sm">{formatValue(a.metric.value, a.metric.unit, a.metric.display)}</span><small className="text-xs text-muted-foreground">expected {formatValue(a.metric.judgement?.expected, a.metric.unit, a.metric.display)}</small></span>
                  <Button type="button" variant="outline" size="sm" aria-label={`Investigate ${a.metric.label || a.metric.name || a.metric.metric_id}`} onClick={() => void investigate(a)}>Investigate</Button>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section data-slot="ops-glance" aria-label="Domains at a glance" className="flex min-w-0 flex-col rounded-xl border bg-card">
          <header className="flex items-center gap-2 border-b px-3 py-2">
            <h3 className="m-0 text-sm font-semibold">Domains</h3>
            <Button variant="link" size="sm" className="ml-auto h-auto p-0 text-xs" onClick={() => onTab("domains")}>View all {domains.length}</Button>
          </header>
          <ul className="m-0 flex list-none flex-col p-0">
            {domains.map((d) => {
              const alert = d.counts.alert ?? 0;
              const watch = d.counts.watch ?? 0;
              return (
                <li key={d.id} className="border-b last:border-b-0">
                  <button type="button" className="grid w-full cursor-pointer grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 px-3 py-1.5 text-left hover:bg-accent/50" onClick={() => onJump(`ops-${d.id}`)}>
                    <span className="min-w-0"><b className="block truncate text-sm font-medium">{d.title}</b><small className="text-xs text-muted-foreground">{d.snapshot ? `${d.snapshot.metrics.length} metrics · checked ${ago(d.snapshot.age_s)}` : d.enabled ? "no numbers yet" : "switched off"}</small></span>
                    <span className="flex gap-1.5 text-xs">
                      {alert > 0 && <span className="rounded-full bg-[var(--danger-soft)] px-2 py-0.5 font-medium text-[var(--danger)]">{alert} need attention</span>}
                      {watch > 0 && <span className="rounded-full bg-[var(--warning-soft)] px-2 py-0.5 font-medium text-[var(--warning)]">{watch} watching</span>}
                      {alert === 0 && watch === 0 && d.snapshot && <span className="rounded-full bg-muted px-2 py-0.5 text-muted-foreground">All normal</span>}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      </div>
    </div>
  );
}

/** Page title, data freshness, and the two page-level actions: pick metrics, refresh everything. */
function PageHeader({ domains }: { domains: DomainView[] }) {
  const openOps = useShellStore((s) => s.openOps);
  const askOpen = useShellStore((s) => s.askOpen);
  const setAskOpen = useShellStore((s) => s.setAskOpen);
  const refresh = useMonitoringStore((s) => s.refresh);
  const busy = useMonitoringStore((s) => s.busy);
  const live = domains.filter((d) => d.enabled && d.snapshot);
  const days = [...new Set(live.map((d) => d.snapshot!.as_of))].sort();
  const oldest = Math.max(0, ...live.map((d) => d.snapshot!.age_s));
  const refreshing = domains.some((d) => busy[d.id] === "refreshing");
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3 animate-in fade-in slide-in-from-bottom-1 duration-300">
      <div>
        <h2 className="m-0 font-serif text-2xl leading-tight font-normal tracking-tight">Monitoring</h2>
        {live.length > 0 && <p className="m-0 text-sm text-muted-foreground">Data to {days.length === 1 ? days[0] : `${days[0]} – ${days[days.length - 1]}`} · oldest check {ago(oldest)}</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button className="bg-brand text-[var(--on-accent)] hover:bg-brand-hover" aria-expanded={askOpen} onClick={() => setAskOpen(!askOpen)}>Ask Seleric</Button>
        <Button variant="outline" onClick={() => openOps("monitoring")}>Choose metrics</Button>
        <Button variant="outline" disabled={refreshing} onClick={() => domains.filter((d) => d.enabled).forEach((d) => void refresh(d.id))}>{refreshing ? "Refreshing…" : "Refresh"}</Button>
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
  const [tab, setTab] = useState("overview");
  const jump = (id: string) => {
    setTab("domains");
    requestAnimationFrame(() => { const el = document.getElementById(id); el?.closest("details")?.setAttribute("open", ""); el?.scrollIntoView({ behavior: "smooth", block: "center" }); });
  };
  if (!state) {
    if (loading && !error) return <div className="flex flex-col gap-3" role="status" aria-busy="true" aria-label="Loading the latest numbers">
      <Skeleton className="h-28 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
    </div>;
    return <div className="py-8 text-muted-foreground">{error ? <p role="alert">{error}</p> : "No data yet."}</div>;
  }
  return (
    <div className="stagger flex flex-col gap-4">
      <PageHeader domains={state.domains} />
      <Tabs value={tab} onValueChange={setTab} className="gap-3">
        <TabsList variant="line" className="h-auto w-full justify-start gap-1 rounded-none border-b p-0" aria-label="Monitoring sections">
          <TabsTrigger value="overview" className="flex-none rounded-none px-3 py-2 data-active:border-b-brand">Overview</TabsTrigger>
          <TabsTrigger value="anomalies" className="flex-none rounded-none px-3 py-2 data-active:border-b-brand">Anomalies <span className="tabular-nums text-muted-foreground">{state.domains.reduce((n, d) => n + (d.counts.alert ?? 0), 0)}</span></TabsTrigger>
          <TabsTrigger value="domains" className="flex-none rounded-none px-3 py-2 data-active:border-b-brand">Domains <span className="tabular-nums text-muted-foreground">{state.domains.length}</span></TabsTrigger>
        </TabsList>
        <TabsContent value="overview" forceMount className="flex flex-col gap-3 data-[state=inactive]:hidden">
          <BusinessState domains={state.domains} onJump={jump} />
          <OverviewPanels domains={state.domains} onTab={setTab} onJump={jump} />
        </TabsContent>
        <TabsContent value="anomalies" forceMount className="data-[state=inactive]:hidden">
          <AnomalyFeed domains={state.domains} />
        </TabsContent>
        <TabsContent value="domains" forceMount className="data-[state=inactive]:hidden">
      <section aria-label="All metrics by domain" className="flex min-w-0 flex-col gap-1.5">
        <p className="m-0 text-xs text-muted-foreground">Open a domain to see its metrics.</p>
        {state.domains.map((d) => {
          const needs = d.counts.alert ?? 0;
          return (
            <details key={d.id} id={`ops-${d.id}`} open={needs > 0} aria-label={d.title} className="group/dom scroll-mt-3 rounded-xl border bg-card">
              <summary className="flex cursor-pointer list-none items-center gap-2.5 px-3 py-2 [&::-webkit-details-marker]:hidden">
                <span className="font-serif text-[17px]">{d.title}</span>
                <span className="text-xs text-muted-foreground">{d.snapshot ? `${d.snapshot.metrics.length} metrics` : d.enabled ? "no numbers yet" : "switched off"}{needs > 0 ? ` · ${needs} need attention` : ""}</span>
                <span aria-hidden="true" className="mt-[-3px] ml-auto size-1.5 rotate-45 border-r-[1.5px] border-b-[1.5px] border-muted-foreground group-open/dom:-rotate-[135deg]" />
              </summary>
              <DomainRefresh domain={d} />
              {notice[d.id]?.kind === "error" && <Alert variant="destructive" role="alert" className="mx-2.5 mb-2 w-auto"><AlertDescription>{notice[d.id]?.text}</AlertDescription></Alert>}
              {!d.enabled ? (
                <p className="m-0 px-3 pb-3 text-sm text-muted-foreground">Monitoring for this domain is switched off. <Button variant="link" size="sm" onClick={() => openOps("monitoring")}>Change in Monitoring</Button></p>
              ) : !d.snapshot ? (
                <p className="m-0 px-3 pb-3 text-sm text-muted-foreground">No numbers yet. They appear after the first scheduled run, or press Refresh.</p>
              ) : (
                <>
                  {d.snapshot.signals.length > 0 && (
                    <ul className="m-0 grid list-none gap-1 px-3 pb-2 text-xs text-destructive">{d.snapshot.signals.map((x) => <li key={x}>{x}</li>)}</ul>
                  )}
                  <div className="grid grid-cols-[repeat(auto-fill,minmax(168px,1fr))] gap-2 px-2.5 pb-2.5">
                    {d.snapshot.metrics.map((m) => <MetricCard key={m.metric_id} metric={m} domainId={d.id} />)}
                  </div>
                  {d.snapshot.metrics.length === 0 && <p className="m-0 px-3 pb-3 text-sm text-muted-foreground">No metrics selected. Choose some in Monitoring.</p>}
                </>
              )}
            </details>
          );
        })}
      </section>
        </TabsContent>
      </Tabs>
    </div>
  );
}
