import { api, type HttpClient } from "./http";

export type TriggerKind = "cron" | "signal" | "watch" | "webhook";
export type RunKind = "manual" | "chat" | TriggerKind;
export type RunStatus = "queued" | "running" | "completed" | "partial" | "failed" | "skipped";
export type ActivityOutcome = "attempted" | "denied" | "failed" | "completed" | "skipped";

export interface BotTrigger {
  trigger_id: string;
  kind: TriggerKind;
  config: {
    cron?: string; timezone?: string; detector_prefix?: string; severities?: string[];
    tool?: string; args?: Record<string, unknown>; fields?: string[]; interval_min?: number;
  };
  enabled: boolean;
  next_run_at: string | null;
  last_fired_at: string | null;
  state?: { checked_at?: string; error?: string | null };
  /** Webhook only, and only in the reply that created it: shown once, never stored. */
  secret?: string;
}

export interface BotSkill {
  skill_id: string;
  name: string;
  body: string;
  enabled: boolean;
  author: string;
  updated_at: string;
}

export interface MemoryItem {
  id: string;
  text: string;
  date: string;
  fact_type: string | null;
  document_id: string | null;
  mentioned_at: string | null;
  tags: string[];
}

export type MemoryBank = "bot" | "shared";

export interface BotRunSummary {
  run_id: string;
  status: RunStatus;
  outcome: string | null;
  trigger_kind: RunKind;
  created_at: string;
  finished_at: string | null;
}

export interface Bot {
  bot_id: string;
  brand_id: number;
  name: string;
  template: string;
  role: string;
  enabled: boolean;
  instructions: string;
  instructions_version: number;
  tools: string[];
  limits: Record<string, number>;
  notify?: { email?: string[] };
  version: number;
  updated_at: string;
  updated_by: string;
  triggers?: BotTrigger[];
  skills?: BotSkill[];
  next_run_at?: string | null;
  last_run?: BotRunSummary | null;
}

/** D-253: the run's answer as data. eda_checks are computed in code from bot_eda; the rest is read out of the answer. */
export interface EdaCheck { metric: string; label: string; unit: "inr" | "count" | "pct"; now: number; p10: number; p50: number | null; p90: number; read: "Low" | "Normal" | "High" }
export interface Proposal { entity: string; field: string; current: number | null; proposed: number | null; condition: string; reason: string }
export interface Mover { entity: string; figure: string; note: string }
export interface AnswerCheck { label: string; now: string; normal: string; read: string }
export interface RunReport {
  version: number;
  eda_checks: EdaCheck[];
  headline?: string;
  proposals?: Proposal[];
  movers?: Mover[];
  checks?: AnswerCheck[];
  unverified?: string[];
  extract_failed?: boolean;
}

export interface BotRun extends BotRunSummary {
  bot_id: string;
  reason: string;
  query: string;
  answer: string | null;
  error: string | null;
  merged: { kind: string; reason: string; data?: string }[];
  instructions_version: number | null;
  mission_id: string | null;
  tool_calls: number;
  started_at: string | null;
  report?: RunReport | null;
  dismissed_at?: string | null;
  dismissed_by?: string | null;
  /** Inbox rows only. */
  bot_name?: string;
}

export interface BotActivity {
  id: number;
  run_id: string;
  at: string;
  kind: "trigger" | "tool" | "answer" | "note" | "memory" | "delivery";
  tool: string | null;
  target: string | null;
  scope: string | null;
  decision: string | null;
  outcome: ActivityOutcome;
  summary: string;
  detail: Record<string, unknown>;
}

export interface BotTemplate {
  template: string;
  name: string;
  role: string;
  tools: string[];
}

export type BotChanges = Partial<Pick<Bot, "name" | "role" | "enabled" | "instructions" | "tools" | "limits" | "notify">>;

const enc = encodeURIComponent;
const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export class BotsApi {
  constructor(private readonly http: HttpClient = api) {}
  list(): Promise<{ bots: Bot[] }> { return this.http.request("/v1/bots"); }
  templates(): Promise<{ templates: BotTemplate[]; read_tools: string[] }> { return this.http.request("/v1/bots/templates"); }
  create(template: string, name?: string): Promise<Bot> { return this.http.request("/v1/bots", json("POST", { template, name })); }
  get(id: string): Promise<Bot> { return this.http.request(`/v1/bots/${enc(id)}`); }
  remove(id: string): Promise<unknown> { return this.http.request(`/v1/bots/${enc(id)}`, { method: "DELETE" }); }
  update(id: string, expectedVersion: number, changes: BotChanges): Promise<Bot> {
    return this.http.request(`/v1/bots/${enc(id)}`, json("PATCH", { expected_version: expectedVersion, ...changes }));
  }
  addTrigger(id: string, kind: TriggerKind, config: BotTrigger["config"]): Promise<BotTrigger> {
    return this.http.request(`/v1/bots/${enc(id)}/triggers`, json("POST", { kind, config }));
  }
  setTrigger(id: string, triggerId: string, enabled: boolean): Promise<unknown> {
    return this.http.request(`/v1/bots/${enc(id)}/triggers/${enc(triggerId)}`, json("PATCH", { enabled }));
  }
  deleteTrigger(id: string, triggerId: string): Promise<unknown> {
    return this.http.request(`/v1/bots/${enc(id)}/triggers/${enc(triggerId)}`, { method: "DELETE" });
  }
  run(id: string, kind: "manual" | "chat", message = ""): Promise<{ run_id: string; status: string }> {
    return this.http.request(`/v1/bots/${enc(id)}/runs`, json("POST", { kind, message }));
  }
  runs(id: string, kind?: RunKind): Promise<{ runs: BotRun[] }> {
    return this.http.request(`/v1/bots/${enc(id)}/runs${kind ? `?kind=${kind}` : ""}`);
  }
  inbox(days = 1): Promise<{ runs: BotRun[] }> { return this.http.request(`/v1/bots/inbox?days=${days}`); }
  dismiss(runId: string, undo = false): Promise<{ run_id: string; dismissed_at: string | null }> {
    return this.http.request(`/v1/bots/runs/${enc(runId)}/dismiss`, json("POST", { undo }));
  }
  activity(id: string): Promise<{ activity: BotActivity[] }> { return this.http.request(`/v1/bots/${enc(id)}/activity`); }
  saveSkill(id: string, skill: { skill_id?: string; name: string; body: string; enabled: boolean }): Promise<{ skills: BotSkill[] }> {
    return this.http.request(`/v1/bots/${enc(id)}/skills`, json("PUT", skill));
  }
  deleteSkill(id: string, skillId: string): Promise<unknown> {
    return this.http.request(`/v1/bots/${enc(id)}/skills/${enc(skillId)}`, { method: "DELETE" });
  }
  memory(id: string, bank: MemoryBank, q = ""): Promise<{ bank: string; items: MemoryItem[]; total: number }> {
    return this.http.request(`/v1/bots/${enc(id)}/memory?bank=${bank}${q ? `&q=${enc(q)}` : ""}`);
  }
  addNote(id: string, bank: MemoryBank, text: string): Promise<{ document_id: string }> {
    return this.http.request(`/v1/bots/${enc(id)}/memory`, json("POST", { bank, text }));
  }
  forget(id: string, bank: MemoryBank, documentId: string): Promise<unknown> {
    return this.http.request(`/v1/bots/${enc(id)}/memory/${enc(documentId)}?bank=${bank}`, { method: "DELETE" });
  }
}

export const botsApi = new BotsApi();
