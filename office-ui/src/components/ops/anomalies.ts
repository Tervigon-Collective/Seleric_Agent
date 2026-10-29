import type { DomainView, MetricStatus, MetricView } from "../../api/monitoring";

/** One metric judged as off its normal range, with the domain it belongs to. The id is stable and shared with the metric card, so a later diagnosis step can link to it. */
export interface Anomaly { id: string; domain: DomainView; metric: MetricView }

export const metricAnchor = (domainId: string, metricId: string) => `ops-${domainId}-${metricId}`;

const RANK: Partial<Record<MetricStatus, number>> = { alert: 0, watch: 1, good: 2 };

/** Alerts first, then watched, then better-than-usual; within a group the furthest from normal (|z|) leads. */
export function collectAnomalies(domains: DomainView[]): Anomaly[] {
  const rows: Anomaly[] = [];
  for (const domain of domains) {
    for (const metric of domain.snapshot?.metrics ?? []) {
      if (RANK[metric.status] !== undefined) rows.push({ id: metricAnchor(domain.id, metric.metric_id), domain, metric });
    }
  }
  const spread = (a: Anomaly) => Math.abs(a.metric.judgement?.z ?? 0);
  return rows.sort((a, b) => (RANK[a.metric.status] ?? 9) - (RANK[b.metric.status] ?? 9) || spread(b) - spread(a));
}

/** The metrics the agent's business snapshot reads: the ones flagged `headline` in each domain's config. */
export function headlineMetrics(domains: DomainView[]): Anomaly[] {
  return domains.flatMap((domain) => {
    const flagged = new Set(domain.metrics.filter((m) => m.headline).map((m) => m.metric_id));
    return (domain.snapshot?.metrics ?? []).filter((m) => flagged.has(m.metric_id)).map((metric) => ({ id: metricAnchor(domain.id, metric.metric_id), domain, metric }));
  });
}
