import { api, type HttpClient } from "./http";

export type TriggerKind = "cron" | "signal";
export type RunKind = "manual" | "chat" | "cron" | "signal";
export type RunStatus = "queued" | "running" | "completed" | "partial" | "failed" | "skipped";
export type ActivityOutcome = "attempted" | "denied" | "failed" | "completed" | "skipped";

export interface BotTrigger {
  trigger_id: string;
  kind: TriggerKind;
  config: { cron?: string; timezone?: string; detector_prefix?: string; severities?: string[] };
  enabled: boolean;
  next_run_at: string | null;
  last_fired_at: string | null;
}

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
  version: number;
  updated_at: string;
  updated_by: string;
  triggers?: BotTrigger[];
  next_run_at?: string | null;
  last_run?: BotRunSummary | null;
}

export interface BotRun extends BotRunSummary {
  bot_id: string;
  reason: string;
  query: string;
  answer: string | null;
  error: string | null;
  merged: { kind: string; reason: string }[];
  instructions_version: number | null;
  mission_id: string | null;
  tool_calls: number;
  started_at: string | null;
}

export interface BotActivity {
  id: number;
  run_id: string;
  at: string;
  kind: "trigger" | "tool" | "answer" | "note";
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

export type BotChanges = Partial<Pick<Bot, "name" | "role" | "enabled" | "instructions" | "tools" | "limits">>;

const enc = encodeURIComponent;
const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export class BotsApi {
  constructor(private readonly http: HttpClient = api) {}
  list(): Promise<{ bots: Bot[] }> { return this.http.request("/v1/bots"); }
  templates(): Promise<{ templates: BotTemplate[]; read_tools: string[] }> { return this.http.request("/v1/bots/templates"); }
  create(template: string, name?: string): Promise<Bot> { return this.http.request("/v1/bots", json("POST", { template, name })); }
  get(id: string): Promise<Bot> { return this.http.request(`/v1/bots/${enc(id)}`); }
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
  activity(id: string): Promise<{ activity: BotActivity[] }> { return this.http.request(`/v1/bots/${enc(id)}/activity`); }
}

export const botsApi = new BotsApi();
