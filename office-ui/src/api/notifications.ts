import { api, type HttpClient } from "./http";

export type Severity = "critical" | "warning" | "info";
export type MonitorStatus = "ok" | "degraded" | "down" | "stale" | "never_run";

export interface Guardrails {
  be_roas_platform: number;
  target_roas: number;
  allowable_cpa: number;
  source: string;
  working: string;
  computed_at: string | null;
}

export interface Pulse {
  spend: number | null;
  purchases: number | null;
  purchase_value: number | null;
  roas: number | null;
  cpa: number | null;
  cpm: number | null;
  ctr_link: number | null;
  daily_budget: number | null;
  expected_spend_by_now: number | null;
  pace: number | null;
  active: { campaign: number; adset: number; ad: number };
  guardrails: Guardrails;
}

export interface TenantStatus {
  tenant_id: string;
  name: string;
  brand_id: number;
  platform: string;
  enabled: boolean;
  status: MonitorStatus;
  age_s: number | null;
  at?: string;
  tick_s?: number;
  pulse?: Pulse;
  findings?: Record<Severity, number>;
  partial_errors?: string[];
  rule_errors?: string[];
  config_error?: string | null;
  error?: string;
  consecutive_failures?: number;
}

export interface FindingAction {
  id: string;
  title: string;
  risk: "link" | "low" | "delivery" | "spend";
  inputs: string[];
}

export interface DiagnosisGates {
  confidence?: number | null;
  min_volume_ok?: boolean;
  learning?: boolean;
  window?: string | null;
}

export interface TimelineEvent {
  at: string;
  finding_id?: string | null;
  from_state: string | null;
  to_state: string;
  note: string;
  kind: string | null;
  actor: string | null;
  actor_type?: "system" | "user" | string | null;
  change_summary?: string | null;
  request_id?: string | null;
  action_id?: string | null;
  status?: string | null;
  entity_level?: string | null;
  entity_id?: string | null;
  severity?: Severity;
  title?: string | null;
  entity_name?: string | null;
  request_status?: string | null;
}

export interface NotificationItem {
  finding_id: string;
  state: "open" | "ongoing" | "resolved";
  severity: Severity;
  title: string;
  rule_id: string | null;
  tier: "performance" | "compliance" | "custom" | null;
  platform: string | null;
  summary: string;
  narration: string | null;
  narration_source: "llm" | "rule" | null;
  narrated_at: string | null;
  cause?: string | null;
  bullets?: string[];
  artifacts?: { kind: string; title: string; payload: Record<string, unknown>; source?: string }[];
  diagnosis_status?: "pending" | "ready" | "failed" | string | null;
  gates?: DiagnosisGates;
  recommended_action_id?: string | null;
  recommended_summary?: string | null;
  level: string | null;
  entity_id: string | null;
  entity_name: string | null;
  path: string[];
  evidence: Record<string, unknown>;
  actions: FindingAction[];
  playbook_ref: string | null;
  impact_inr: number | null;
  confidence: number | null;
  opened_at: string;
  updated_at: string;
  resolved_at: string | null;
  label: "useful" | "noise" | "wrong_action" | null;
}

export interface ActivityEntry {
  at: string;
  finding_id: string;
  from_state: string | null;
  to_state: string;
  note: string;
  severity: Severity;
  title: string | null;
  entity_name: string | null;
  kind: string | null;
}

export interface Feed {
  tenants: TenantStatus[];
  items: NotificationItem[];
  activity: ActivityEntry[];
  counts?: Record<Severity, number>;
  config_error: string | null;
  actions_enabled: boolean;
  server_time: string;
}

export type ActionPreview =
  | { kind: "link"; action_id: string; title: string; prompt: string }
  | {
      kind: "write"; request_id: string; action_id: string; title: string; summary: string;
      current: Record<string, unknown>; change: Record<string, unknown>; risk: FindingAction["risk"];
      checks: { tool: string; checked: boolean; note?: string }[]; expires_at: string; enabled: boolean;
    };

export interface ActionResult {
  request_id: string;
  status: "executed" | "failed";
  error: string | null;
  summary: string;
}

const enc = encodeURIComponent;

export class NotificationsApi {
  constructor(private readonly http: HttpClient = api) {}

  feed(): Promise<Feed> {
    return this.http.request("/v1/notifications");
  }
  events(findingId: string): Promise<{ finding_id: string; events: TimelineEvent[] }> {
    return this.http.request(`/v1/notifications/${enc(findingId)}/events`);
  }
  entityActivity(level: string, entityId: string, brandId?: number | null): Promise<{ level: string; entity_id: string; events: TimelineEvent[] }> {
    const q = brandId != null ? `?brand_id=${brandId}` : "";
    return this.http.request(`/v1/notifications/entities/${enc(level)}/${enc(entityId)}/activity${q}`);
  }
  label(findingId: string, label: NonNullable<NotificationItem["label"]>, note = ""): Promise<{ label: string }> {
    return this.http.request(`/v1/notifications/${enc(findingId)}/label`, { method: "POST", body: JSON.stringify({ label, note }) });
  }
  preview(findingId: string, actionId: string, inputs: Record<string, string> = {}): Promise<ActionPreview> {
    return this.http.request(`/v1/notifications/${enc(findingId)}/actions/${enc(actionId)}/preview`, {
      method: "POST", body: JSON.stringify({ inputs }),
    });
  }
  approve(requestId: string): Promise<ActionResult> {
    return this.http.request(`/v1/notifications/actions/${enc(requestId)}/approve`, { method: "POST" });
  }
}

export const notificationsApi = new NotificationsApi();

// ------------------------------------------------------------------------------------------ live configuration

export interface JsonSchemaProp {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
}

export interface RuleSchema {
  id: string;
  title: string;
  description: string;
  tier: "performance" | "compliance";
  platforms: string[];
  levels: string[];
  cadence: "5m" | "1h" | "daily";
  actions: string[];
  playbook_ref: string;
  efficiency: boolean;
  rollup: boolean;
  params_schema: { properties?: Record<string, JsonSchemaProp> };
  params_defaults: Record<string, unknown>;
}

export interface FieldSchema {
  path: string;
  type: "number" | "string" | "string_list" | "bool";
  levels: string[];
  label: string;
  unit: string;
}

export interface LiveSchema {
  levels: string[];
  severities: Severity[];
  rules: RuleSchema[];
  actions: { platform: string; id: string; title: string; levels: string[]; risk: string }[];
  platforms: Record<string, { fields: FieldSchema[] }>;
  conditions: { operators: Record<string, string[]>; no_value_operators: string[]; max_conditions: number; refs: string[] };
}

export interface RuleSetting {
  enabled?: boolean;
  severity?: Severity | null;
  params?: Record<string, unknown>;
}

export interface Condition {
  field: string;
  op: string;
  value?: unknown;
}

export interface CustomRule {
  id: string;
  title: string;
  platform: string;
  level: string;
  match: "all" | "any";
  conditions: Condition[];
  severity: Severity;
  actions: string[];
  explain_hint: string;
  enabled: boolean;
}

export interface AttributionWindow {
  event_type: "CLICK_THROUGH" | "VIEW_THROUGH" | "ENGAGED_VIDEO_VIEW";
  window_days: number;
}

/** One brand / ad account the live monitor watches. */
export interface LiveTenant {
  tenant_id: string;
  name: string;
  brand_id: number;
  platform: string;
  account_id: string;
  timezone: string;
  enabled: boolean;
  structure_cache_ttl_min: number;
  exclude_entity_ids: string[];
  expectations: {
    pixel_id: string | null;
    conversion_event: string;
    display_domain: string | null;
    attribution: AttributionWindow[];
    compliance_name_pattern: string;
    label_from_name: string;
  };
  actions: {
    budget_step_pct: number;
    max_daily_budget_minor: number;
    min_daily_budget_minor: number;
  };
}

/** The whole document; the page edits the parts it draws and sends everything back. */
export interface LiveConfig {
  tenants: LiveTenant[];
  guardrails: {
    mode: "auto" | "manual";
    target_roas_multiple: number;
    manual: { be_roas_platform: number; target_roas: number; allowable_cpa: number };
    campaign_overrides: Record<string, unknown>[];
  };
  baselines: Record<string, unknown>;
  stats: { confidence: number } & Record<string, number>;
  rules: Record<string, RuleSetting>;
  custom_rules: CustomRule[];
  notify: { quiet_hours: { start: string; end: string } | null; cooldown_ticks: number; max_narrations_per_tick: number };
}

export interface LiveConfigView {
  config: LiveConfig;
  defaults: LiveConfig;
  custom: boolean;
  version: number;
  updated_at: string | null;
  updated_by: string | null;
  error: string | null;
}

export interface DryRun {
  tenants: { tenant_id: string; snapshot_at?: string; error?: string; counts?: Record<Severity, number>; by_rule?: Record<string, number>;
    findings?: { rule_id: string; title: string; severity: Severity; path: string[]; level: string; summary: string }[] }[];
}

export class LiveConfigApi {
  constructor(private readonly http: HttpClient = api) {}

  schema(): Promise<LiveSchema> {
    return this.http.request("/v1/monitoring/live/schema");
  }
  get(): Promise<LiveConfigView> {
    return this.http.request("/v1/monitoring/live/config");
  }
  save(config: LiveConfig, expectedVersion: number): Promise<LiveConfigView> {
    return this.http.request("/v1/monitoring/live/config", { method: "PUT", body: JSON.stringify({ config, expected_version: expectedVersion }) });
  }
  reset(): Promise<LiveConfigView> {
    return this.http.request("/v1/monitoring/live/config", { method: "DELETE" });
  }
  dryRun(config: LiveConfig): Promise<DryRun> {
    return this.http.request("/v1/monitoring/live/config/dry-run", { method: "POST", body: JSON.stringify({ config }) });
  }
}

export const liveConfigApi = new LiveConfigApi();
