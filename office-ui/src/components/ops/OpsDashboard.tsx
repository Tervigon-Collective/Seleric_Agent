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
    <header className="ops-domain-head">
      <div>
        <h3>{domain.title}</h3>
        <p>{domain.description}</p>
      </div>
      <div className="ops-domain-meta">
        {snap && (
          <span className={snap.stale ? "ops-stale" : ""}>
            Data to {snap.as_of} · checked {ago(snap.age_s)}{snap.stale ? " · out of date" : ""}
          </span>
        )}
        {snap && snap.status !== "OK" && <span className="ops-pill status-watch">Some metrics could not be read</span>}
        {domain.pending && <span className="ops-pill status-watch">Selection changed, waiting for numbers</span>}
        <button className="btn" onClick={() => void refresh(domain.id)} disabled={!!busy || !domain.enabled}>
          {busy === "refreshing" ? "Refreshing…" : "Refresh"}
        </button>
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
    return <div className="ops-empty">{error ? <p role="alert">{error}</p> : loading ? "Loading the latest numbers…" : "No data yet."}</div>;
  }
  return (
    <div className="ops-dashboard">
      <BusinessState domains={state.domains} />
      <AnomalyFeed domains={state.domains} />
      {state.domains.map((d) => (
        <section key={d.id} id={`ops-${d.id}`} className="ops-domain" aria-label={d.title}>
          <DomainHeader domain={d} />
          {notice[d.id]?.kind === "error" && <p className="ops-error" role="alert">{notice[d.id]?.text}</p>}
          {!d.enabled ? (
            <p className="ops-note">Monitoring for this domain is switched off. <button className="link-btn" onClick={() => openOps("monitoring")}>Change in Monitoring</button></p>
          ) : !d.snapshot ? (
            <p className="ops-note">No numbers yet. They appear after the first scheduled run, or press Refresh.</p>
          ) : (
            <>
              {d.snapshot.signals.length > 0 && (
                <ul className="ops-signals">{d.snapshot.signals.map((s) => <li key={s}>{s}</li>)}</ul>
              )}
              <div className="ops-grid">
                {d.snapshot.metrics.map((m) => <MetricCard key={m.metric_id} metric={m} domainId={d.id} />)}
              </div>
              {d.snapshot.metrics.length === 0 && <p className="ops-note">No metrics selected. Choose some in Monitoring.</p>}
            </>
          )}
        </section>
      ))}
    </div>
  );
}
