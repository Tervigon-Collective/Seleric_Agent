import { api, type HttpClient } from "./http";

/** Loop debug payload mirrored from test_agent's ``done`` / ``trace.stats``. */
export interface LoopDebugStats {
  total_ms?: number | null;
  elapsed_ms?: number | null;
  ttft_ms?: number | null;
  answer_ttft_ms?: number | null;
  llm_calls?: number | null;
  tool_calls?: number | null;
  revisions?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  cached_tokens?: number | null;
  errors?: unknown;
  llm_steps?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface LoopGrounding {
  numbers?: number | null;
  measured?: number | null;
  derived?: unknown[];
  unmatched?: unknown[];
  misbound?: unknown[];
  placeholders?: unknown[];
  problems?: unknown[];
  [key: string]: unknown;
}

export interface MissionTraceResponse {
  mission_id: string;
  status?: string | null;
  trace: {
    stats?: LoopDebugStats | null;
    grounding?: LoopGrounding | null;
    steps?: Array<Record<string, unknown>> | null;
    [key: string]: unknown;
  };
  events?: unknown[];
  /** Diagnosis DoWhy DAGs for the inspector Causal tab (not shown in chat). */
  causal_graphs?: Array<Record<string, unknown>>;
}

export class TraceApi {
  constructor(private readonly http: HttpClient = api) {}

  get(missionId: string): Promise<MissionTraceResponse> {
    return this.http.request(`/v1/missions/${encodeURIComponent(missionId)}/trace`);
  }
}

export const traceApi = new TraceApi();
