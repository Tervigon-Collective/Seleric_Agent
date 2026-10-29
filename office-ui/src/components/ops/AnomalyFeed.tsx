import { useState } from "react";
import type { MetricStatus } from "../../api/monitoring";
import { collectAnomalies, type Anomaly } from "./anomalies";
import { Evidence } from "./Evidence";
import { MetricChart } from "./MetricChart";
import { formatMove, formatValue } from "./format";
import type { DomainView } from "../../api/monitoring";

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
    <details className={`ops-row status-${m.status}`} id={`feed-${a.id}`}>
      <summary>
        <span className="ops-row-main">
          <strong>{title}</strong>
          <span className="ops-tag">{domain.title}</span>
        </span>
        <span className="ops-row-nums">
          <span className="ops-value-sm">{formatValue(m.value, m.unit, m.display)}</span>
          <span className="ops-expected">expected {formatValue(j?.expected, m.unit, m.display)}</span>
        </span>
        <span className="ops-row-move">{formatMove(j?.worse_by, j?.unit, m.display) || j?.reason}</span>
        <span className="ops-row-chart"><MetricChart metric={m} height={32} /></span>
      </summary>
      <Evidence metric={m} />
    </details>
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
    <section className="ops-feed" aria-label="Anomalies">
      <div className="ops-feed-head">
        <h3>Anomalies</h3>
        <div className="ops-chips" role="group" aria-label="Show">
          {TABS.map((t) => (
            <button key={t.status} className={`ops-chip ${tab === t.status ? "active" : ""}`} aria-pressed={tab === t.status} onClick={() => setTab(t.status)}>
              {t.label} <span>{count(t.status)}</span>
            </button>
          ))}
        </div>
      </div>
      {rows.length === 0 ? <p className="ops-note">{current.empty}</p> : <div className="ops-rows">{rows.map((a) => <Row key={a.id} a={a} />)}</div>}
      <p className="ops-note ops-foot">How verdicts are made: each metric is compared with the median of its same weekday over the last 4 weeks, normal range is 3 spreads either side.</p>
    </section>
  );
}
