import { api } from "./http";

export type EmailStatus = "draft" | "sending" | "submitted" | "failed" | "unknown";
export interface EmailState {
  approval_id: string; version: number; status: EmailStatus; error: string;
  provider_message_id: string | null; to: string[]; subject: string;
}

export const getEmail = (id: string) =>
  api.request<EmailState>(`/v1/emails/${encodeURIComponent(id)}`);
export const approveEmail = (id: string, version: number) =>
  api.request<EmailState>(`/v1/emails/${encodeURIComponent(id)}/approve`, {
    method: "POST", body: JSON.stringify({ version }),
  });
