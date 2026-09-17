import { api } from "./http";

export type SearchKind = "thread" | "message" | "memory" | "artifact" | "run";
export interface SearchResult {
  id: string; kind: SearchKind; title: string; snippet: string;
  thread_id: string | null; run_id: string | null; score: number;
  created_at: string; metadata: Record<string, unknown>;
}
export type ApprovalStatus =
  | "REQUESTED" | "APPROVED" | "REJECTED" | "EXPIRED"
  | "CANCELLED" | "EXECUTED" | "ROLLED_BACK";
export interface ApprovalRequest {
  id: string; action_type: string; action_preview: Record<string, unknown>;
  required_role: string; status: ApprovalStatus; dry_run: boolean;
  expires_at: string | null;
}

export const searchAll = (query: string) =>
  api.request<SearchResult[]>(`/v1/search?q=${encodeURIComponent(query)}`);
export const decideApproval = (id: string, decision: ApprovalStatus) =>
  api.request<ApprovalRequest>(`/v1/approvals/${encodeURIComponent(id)}/decision`, {
    method: "POST", body: JSON.stringify({ decision }),
  });
export const getRunDiagnostics = (runId: string) =>
  api.request<Record<string, unknown>>(
    `/v1/admin/runs/${encodeURIComponent(runId)}/diagnostics`,
  );
