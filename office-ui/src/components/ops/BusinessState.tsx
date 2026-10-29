import type { DomainView } from "../../api/monitoring";
import { headlineMetrics } from "./anomalies";
import { ago, formatValue } from "./format";

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
    <section className="ops-state" aria-label="Business state">
      <div className="ops-verdict">
        <span className={`ops-dot ${alerts ? "status-alert" : n("watch") ? "status-watch" : "status-ok"}`} aria-hidden />
        <div>
          <h3>{alerts ? `${alerts} ${alerts === 1 ? "metric needs" : "metrics need"} attention` : "No metric needs attention"}</h3>
          <p>
            {n("watch")} watching · {n("good")} better than usual · {n("ok")} normal{noVerdict ? ` · ${noVerdict} without a verdict yet` : ""}
            {" · "}{metrics.length} metrics in {live.length} {live.length === 1 ? "domain" : "domains"}
          </p>
        </div>
        {live.length > 0 && (
          <span className="ops-asof">Data to {days.length === 1 ? days[0] : `${days[0]} – ${days[days.length - 1]}`} · oldest check {ago(oldest)}</span>
        )}
      </div>
      {(stale.length > 0 || partial.length > 0) && (
        <ul className="ops-warnings">
          {stale.length > 0 && <li>Out of date: {stale.map((d) => d.title).join(", ")}. Verdicts there may not reflect today.</li>}
          {partial.length > 0 && <li>Some metrics could not be read in: {partial.map((d) => d.title).join(", ")}.</li>}
        </ul>
      )}
      {headline.length > 0 && (
        <div className="ops-headline" aria-label="Headline metrics">
          {headline.map(({ id, domain, metric: m }) => (
            <a key={id} href={`#${id}`} className={`ops-hl status-${m.status}`}>
              <span>{m.label || m.name || m.metric_id}</span>
              <b>{formatValue(m.value, m.unit, m.display)}</b>
              <small>{domain.title}</small>
            </a>
          ))}
        </div>
      )}
    </section>
  );
}
