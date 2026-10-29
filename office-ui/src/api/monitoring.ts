import { api, type HttpClient } from "./http";

export type MetricStatus = "alert" | "watch" | "good" | "ok" | "no_data";
export type Shape = "additive" | "ratio" | "signed";

/** How a metric raises an alert: outside its normal range the bad way, or worse than expected by a set amount. */
export type AlertRule = { on: "anomaly" } | { on: "worse_by"; threshold: number };

export interface Judgement {
  status: MetricStatus;
  shape: Shape;
  basis: string;
  observed: number | null;
  expected: number | null;
  low: number | null;
  high: number | null;
  z: number | null;
  direction: string | null;
  is_anomaly: boolean;
  worse_by: number | null;
  unit: "%" | "pp" | "abs" | null;
  delta_pct: number | null;
  reason: string;
  history_points: number;
  observed_on: string | null;
}

export interface MetricView {
  metric_id: string;
  name: string | null;
  label: string | null;
  unit: string | null;
  /** How to write the value: a share as a percent, a multiple (MER, ROAS) as 1.5x. */
  display: "percent" | "multiple" | "currency" | "count" | "seconds" | null;
  shape: Shape | null;
  value: number | null;
  /** The day `value` belongs to. Differs from the domain's data day when the source had no row for that day. */
  observed_on: string | null;
  status: MetricStatus;
  polarity_known: boolean;
  direction_bad: "up" | "down" | null;
  rule: AlertRule | null;
  series: { ts: string; value: number }[];
  judgement: Judgement | null;
  freshness: string;
  quality_flags: string[];
}

export interface ConfigMetric {
  metric_id: string;
  catalogue_metric: string;
  label: string | null;
  alert: AlertRule | null;
  direction_bad: "up" | "down" | "none" | null;
  headline: boolean;
  filters: Record<string, string>;
}

export interface DomainView {
  id: string;
  title: string;
  description: string;
  enabled: boolean;
  custom: boolean;
  version: number;
  updated_at: string | null;
  updated_by: string | null;
  metrics: ConfigMetric[];
  pending: boolean;
  counts: Partial<Record<MetricStatus, number>>;
  snapshot: {
    status: "OK" | "DEGRADED" | "UNAVAILABLE";
    as_of: string;
    computed_at: string;
    age_s: number;
    stale: boolean;
    window: { start?: string; end?: string };
    signals: string[];
    metrics: MetricView[];
  } | null;
}

export interface MonitoringState {
  interval_s: number;
  max_metrics_per_domain: number;
  domains: DomainView[];
}

export interface Candidate {
  id: string;
  name: string;
  unit: string | null;
  domain: string;
  direction_bad: "up" | "down" | null;
  dimensions: string[];
}

/** What the page sends when saving a domain: the user's selection, not the shipped entry details. */
export interface SaveBody {
  enabled: boolean;
  metrics: { metric_id: string; label?: string; alert?: AlertRule; direction_bad?: "up" | "down" | "none"; headline?: boolean }[];
  expected_version?: number;
}

const enc = encodeURIComponent;

export class MonitoringApi {
  constructor(private readonly http: HttpClient = api) {}

  state(): Promise<MonitoringState> {
    return this.http.request("/v1/monitoring/state");
  }
  catalogue(domain: string): Promise<{ domain: string; metrics: Candidate[] }> {
    return this.http.request(`/v1/monitoring/catalogue/${enc(domain)}`);
  }
  save(domain: string, body: SaveBody): Promise<DomainView> {
    return this.http.request(`/v1/monitoring/config/${enc(domain)}`, { method: "PUT", body: JSON.stringify(body) });
  }
  reset(domain: string): Promise<DomainView> {
    return this.http.request(`/v1/monitoring/config/${enc(domain)}`, { method: "DELETE" });
  }
  refresh(domain: string): Promise<DomainView> {
    return this.http.request(`/v1/monitoring/refresh/${enc(domain)}`, { method: "POST" });
  }
}

export const monitoringApi = new MonitoringApi();
