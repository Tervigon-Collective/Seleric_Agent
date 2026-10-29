import { useMemo, useState } from "react";
import type { AlertRule, DomainView, MetricView } from "../../api/monitoring";
import { isDirty, useMonitoringStore, type DraftMetric } from "../../stores/monitoring";
import { thresholdUnit } from "./format";

const POLARITY: { value: string; label: string }[] = [
  { value: "", label: "Use the catalogue" },
  { value: "up", label: "Up is bad" },
  { value: "down", label: "Down is bad" },
  { value: "none", label: "Neither, watch only" },
];

function RuleEditor({ domainId, m, unit }: { domainId: string; m: DraftMetric; unit: string }) {
  const update = useMonitoringStore((s) => s.updateMetric);
  const kind = m.alert?.on ?? "anomaly";
  const threshold = m.alert?.on === "worse_by" ? m.alert.threshold : 10;
  const set = (alert: AlertRule | null) => update(domainId, m.metric_id, { alert });
  return (
    <div className="mon-rule">
      <select
        aria-label={`When to alert for ${m.metric_id}`}
        value={kind}
        onChange={(e) => set(e.target.value === "worse_by" ? { on: "worse_by", threshold } : null)}
      >
        <option value="anomaly">Alert when outside its normal range the bad way</option>
        <option value="worse_by">Alert when worse than expected by</option>
      </select>
      {kind === "worse_by" && (
        <label className="mon-threshold">
          <input
            type="number" min={0.1} max={1000} step={0.5} value={threshold}
            aria-label={`Alert threshold for ${m.metric_id}`}
            onChange={(e) => set({ on: "worse_by", threshold: Number(e.target.value) })}
          />
          <span>{unit}</span>
        </label>
      )}
    </div>
  );
}

function SelectedRow({ domain, m, live }: { domain: DomainView; m: DraftMetric; live: MetricView | undefined }) {
  const update = useMonitoringStore((s) => s.updateMetric);
  const toggle = useMonitoringStore((s) => s.toggleMetric);
  const candidate = useMonitoringStore((s) => s.catalogue[domain.id]?.find((c) => c.id === m.metric_id));
  const unit = thresholdUnit(live?.shape, live?.unit ?? candidate?.unit ?? null);
  const polaritySet = m.direction_bad ?? (live?.polarity_known || candidate?.direction_bad ? "catalogue" : null);
  return (
    <li className="mon-row">
      <div className="mon-row-top">
        <input
          className="mon-label" value={m.label} maxLength={80}
          placeholder={live?.name || candidate?.name || m.metric_id}
          aria-label={`Name for ${m.metric_id}`}
          onChange={(e) => update(domain.id, m.metric_id, { label: e.target.value })}
        />
        <code className="mon-id">{m.metric_id}</code>
        <button className="icon-btn" aria-label={`Stop monitoring ${m.metric_id}`} title="Remove"
          onClick={() => toggle(domain.id, m.metric_id, false)}>×</button>
      </div>
      <div className="mon-controls">
      <RuleEditor domainId={domain.id} m={m} unit={unit} />
      <label className="mon-polarity">
        <span>Direction</span>
        <select
          aria-label={`Bad direction for ${m.metric_id}`}
          value={m.direction_bad ?? ""}
          onChange={(e) => update(domain.id, m.metric_id, { direction_bad: (e.target.value || null) as DraftMetric["direction_bad"] })}
        >{POLARITY.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}</select>
      </label>
      <label className="mon-headline">
        <input
          type="checkbox" checked={m.headline}
          aria-label={`Include ${m.metric_id} in the business snapshot`}
          onChange={(e) => update(domain.id, m.metric_id, { headline: e.target.checked })}
        />
        <span>In business snapshot</span>
      </label>
      </div>
      {!polaritySet && <em className="mon-warn">The catalogue has no direction for this metric; it is watched but never alerted unless you set one.</em>}
    </li>
  );
}

function Picker({ domain }: { domain: DomainView }) {
  const [query, setQuery] = useState("");
  const candidates = useMonitoringStore((s) => s.catalogue[domain.id]);
  const draft = useMonitoringStore((s) => s.drafts[domain.id]);
  const cap = useMonitoringStore((s) => s.state?.max_metrics_per_domain ?? 40);
  const toggle = useMonitoringStore((s) => s.toggleMetric);
  const selected = useMemo(() => new Set(draft?.metrics.map((m) => m.metric_id)), [draft]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (candidates ?? []).filter((c) => !q || c.id.includes(q) || c.name.toLowerCase().includes(q)).slice(0, 80);
  }, [candidates, query]);
  if (!candidates) return <p className="ops-note">Loading the catalogue…</p>;
  const full = (draft?.metrics.length ?? 0) >= cap;
  return (
    <div className="mon-picker">
      <input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search metrics in this domain" aria-label={`Search ${domain.title} metrics`} />
      {full && <p className="ops-note">Limit of {cap} metrics reached: every metric is one query on each refresh. Remove one to add another.</p>}
      <ul>
        {shown.map((c) => (
          <li key={c.id}>
            <label>
              <input
                type="checkbox" checked={selected.has(c.id)} disabled={!selected.has(c.id) && full}
                onChange={(e) => toggle(domain.id, c.id, e.target.checked)}
              />
              <span className="mon-cand-name">{c.name}</span>
              <code>{c.id}</code>
              <span className="mon-cand-unit">{c.unit ?? ""}</span>
              {!c.direction_bad && <span className="mon-cand-flag" title="No direction in the catalogue">no direction</span>}
            </label>
          </li>
        ))}
      </ul>
      {candidates.length > shown.length && <p className="ops-note">Showing {shown.length} of {candidates.length}. Type to narrow.</p>}
    </div>
  );
}

function DomainSection({ domain }: { domain: DomainView }) {
  const draft = useMonitoringStore((s) => s.drafts[domain.id]);
  const busy = useMonitoringStore((s) => s.busy[domain.id]);
  const notice = useMonitoringStore((s) => s.notice[domain.id]);
  const { save, reset, discard, setEnabled, loadCatalogue } = useMonitoringStore.getState();
  const [adding, setAdding] = useState(false);
  const dirty = isDirty(domain, draft);
  const live = new Map(domain.snapshot?.metrics.map((m) => [m.metric_id, m]));
  if (!draft) return null;
  const working = !!busy;
  return (
    <section id={`mon-${domain.id}`} className="mon-domain" aria-label={domain.title}>
      <header>
        <div>
          <h3>{domain.title} <span className="ops-tag">{domain.custom ? "Customised" : "Default"}</span></h3>
          <p>{domain.description}</p>
        </div>
        <label className="mon-switch">
          <input type="checkbox" checked={draft.enabled} onChange={(e) => setEnabled(domain.id, e.target.checked)} />
          <span>Monitor this domain</span>
        </label>
      </header>
      <ul className="mon-list" aria-label={`Metrics monitored in ${domain.title}`}>
        {draft.metrics.map((m) => <SelectedRow key={m.metric_id} domain={domain} m={m} live={live.get(m.metric_id)} />)}
      </ul>
      {draft.metrics.length === 0 && <p className="ops-note">Nothing selected. Add at least one metric to see this domain on the dashboard.</p>}
      <div className="mon-add">
        <button className="btn" aria-expanded={adding} onClick={() => { setAdding(!adding); void loadCatalogue(domain.id); }}>
          {adding ? "Hide catalogue" : "Add metrics"}
        </button>
        {adding && <Picker domain={domain} />}
      </div>
      <footer>
        <button className="primary-btn" disabled={!dirty || working} onClick={() => void save(domain.id)}>
          {busy === "saving" ? "Saving…" : busy === "refreshing" ? "Fetching numbers…" : "Save and refresh"}
        </button>
        <button className="btn" disabled={!dirty || working} onClick={() => discard(domain.id)}>Discard changes</button>
        <button className="btn" disabled={!domain.custom || working} onClick={() => void reset(domain.id)}>Back to default</button>
        {dirty && <span className="ops-note">Unsaved changes</span>}
        {notice && <span className={notice.kind === "error" ? "ops-error" : "ops-note"} role={notice.kind === "error" ? "alert" : "status"}>{notice.text}</span>}
        {domain.updated_by && <span className="ops-note">Last saved by {domain.updated_by}</span>}
      </footer>
    </section>
  );
}

export function MonitoringConfig() {
  const state = useMonitoringStore((s) => s.state);
  const error = useMonitoringStore((s) => s.error);
  if (!state) return <div className="ops-empty">{error ? <p role="alert">{error}</p> : "Loading the configuration…"}</div>;
  return (
    <div className="mon-page">
      <p className="mon-intro">
        Pick what each domain watches and when to raise an alert. Changes apply to the dashboard and to the business snapshot that
        answers “how is the business doing”. Metrics are read from the live catalogue, up to {state.max_metrics_per_domain} per domain.
      </p>
      {state.domains.map((d) => <DomainSection key={d.id} domain={d} />)}
    </div>
  );
}
