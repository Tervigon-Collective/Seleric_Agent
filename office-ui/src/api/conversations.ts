import type {
  ActivityEvent,
  Attachment,
  CreateThreadRequest,
  Message,
  MemoryItem,
  MemoryPreference,
  MemoryScope,
  MemoryType,
  SubmitMessageRequest,
  SubmitMessageResponse,
  Thread,
  InitiateAttachmentResponse,
} from "./contracts";
import { api, type HttpClient } from "./http";

const enc = encodeURIComponent;
export const MAX_ATTACHMENT_SIZE = 25 * 1024 * 1024;
export const ALLOWED_ATTACHMENT_TYPES = new Set([
  "application/json",
  "application/pdf",
  "text/csv",
  "text/plain",
  "image/jpeg",
  "image/png",
  "image/webp",
]);

export function validateAttachment(file: File): void {
  const type = (file.type || "application/octet-stream").split(";", 1)[0].toLowerCase();
  if (file.size > MAX_ATTACHMENT_SIZE) throw new Error("Attachments must be 25 MB or smaller");
  if (!ALLOWED_ATTACHMENT_TYPES.has(type)) throw new Error(`Attachment type is not supported: ${type}`);
}

export async function sha256(file: Blob): Promise<string> {
  const bytes = typeof file.arrayBuffer === "function"
    ? await file.arrayBuffer()
    : await new Promise<ArrayBuffer>((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(reader.error ?? new Error("Unable to read attachment"));
        reader.onload = () => resolve(reader.result as ArrayBuffer);
        reader.readAsArrayBuffer(file);
      });
  const digest = await crypto.subtle.digest("SHA-256", new Uint8Array(bytes));
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

export class ConversationApi {
  constructor(private readonly http: HttpClient = api) {}

  listThreads(limit = 50): Promise<Thread[]> {
    return this.http.request(`/v1/threads?limit=${limit}`);
  }
  createThread(body: CreateThreadRequest = {}): Promise<Thread> {
    return this.http.request("/v1/threads", { method: "POST", body: JSON.stringify(body) });
  }
  getThread(threadId: string): Promise<Thread> {
    return this.http.request(`/v1/threads/${enc(threadId)}`);
  }
  updateThread(threadId: string, body: { title: string }): Promise<Thread> {
    return this.http.request(`/v1/threads/${enc(threadId)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  }
  archiveThread(threadId: string): Promise<Thread> {
    return this.http.request(`/v1/threads/${enc(threadId)}/archive`, { method: "POST" });
  }
  listMessages(threadId: string, limit = 100): Promise<Message[]> {
    return this.http.request(`/v1/threads/${enc(threadId)}/messages?limit=${limit}`);
  }
  submitMessage(threadId: string, body: SubmitMessageRequest): Promise<SubmitMessageResponse> {
    return this.http.request(`/v1/threads/${enc(threadId)}/messages`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  }
  cancelRun(runId: string): Promise<{ run_id: string; status: string }> {
    return this.http.request(`/v1/runs/${enc(runId)}/cancel`, { method: "POST" });
  }
  initiateAttachment(threadId: string, file: File): Promise<InitiateAttachmentResponse> {
    validateAttachment(file);
    return this.http.request(`/v1/threads/${enc(threadId)}/attachments`, {
      method: "POST",
      body: JSON.stringify({
        filename: file.name,
        content_type: file.type || "application/octet-stream",
        size_bytes: file.size,
      }),
    });
  }
  async uploadAttachment(threadId: string, file: File): Promise<Attachment> {
    validateAttachment(file);
    const checksum = await sha256(file);
    const initiated = await this.initiateAttachment(threadId, file);
    const url = /^https?:\/\//.test(initiated.upload.url)
      ? initiated.upload.url
      : `${this.http.baseUrl}${initiated.upload.url}`;
    const apiOrigin = new URL(this.http.baseUrl || location.origin, location.origin).origin;
    const uploadOrigin = new URL(url, location.origin).origin;
    const headers = new Headers({ "Content-Type": file.type || "application/octet-stream" });
    if (uploadOrigin === apiOrigin) {
      const authorization = this.http.headers().get("Authorization");
      if (authorization) headers.set("Authorization", authorization);
    }
    const response = await this.http.fetchImpl(url, {
      method: initiated.upload.method,
      headers,
      body: file,
    });
    if (!response.ok) throw new Error(`Attachment upload failed (${response.status})`);
    const body = await response.text();
    if (body) {
      const attachment = JSON.parse(body) as Attachment;
      if (attachment.status !== "PENDING") return attachment;
    }
    return this.completeAttachment(initiated.attachment.id, checksum);
  }
  completeAttachment(attachmentId: string, checksumSha256: string): Promise<Attachment> {
    return this.http.request(`/v1/attachments/${enc(attachmentId)}/complete`, {
      method: "POST",
      body: JSON.stringify({ checksum_sha256: checksumSha256 }),
    });
  }
  listThreadEvents(threadId: string, afterSequence = 0): Promise<ActivityEvent[]> {
    return this.http.request(
      `/v1/threads/${enc(threadId)}/events?after_sequence=${afterSequence}`,
    );
  }
  listMemories(threadId?: string, includeInactive = true): Promise<MemoryItem[]> {
    const query = new URLSearchParams({ include_inactive: String(includeInactive) });
    if (threadId) query.set("thread_id", threadId);
    return this.http.request(`/v1/memories?${query}`);
  }
  createMemory(body: {
    content: string; scope: MemoryScope; type: MemoryType; thread_id?: string | null;
  }): Promise<MemoryItem> {
    return this.http.request("/v1/memories", { method: "POST", body: JSON.stringify(body) });
  }
  updateMemory(memoryId: string, body: { content: string }): Promise<MemoryItem> {
    return this.http.request(`/v1/memories/${enc(memoryId)}`, {
      method: "PATCH", body: JSON.stringify(body),
    });
  }
  pinMemory(memoryId: string, pinned: boolean): Promise<MemoryItem> {
    return this.http.request(`/v1/memories/${enc(memoryId)}/pin?pinned=${pinned}`, { method: "POST" });
  }
  moveMemory(memoryId: string, body: {
    scope: MemoryScope; thread_id?: string | null; project_id?: string | null;
  }): Promise<MemoryItem> {
    return this.http.request(`/v1/memories/${enc(memoryId)}/move`, {
      method: "POST", body: JSON.stringify(body),
    });
  }
  archiveMemory(memoryId: string): Promise<MemoryItem> {
    return this.http.request(`/v1/memories/${enc(memoryId)}/archive`, { method: "POST" });
  }
  deleteMemory(memoryId: string): Promise<{ memory_id: string; status: string }> {
    return this.http.request(`/v1/memories/${enc(memoryId)}`, { method: "DELETE" });
  }
  getMemoryPreference(): Promise<MemoryPreference> {
    return this.http.request("/v1/memories/preference");
  }
  setMemoryPreference(optedOut: boolean): Promise<MemoryPreference> {
    return this.http.request("/v1/memories/preference", {
      method: "PUT", body: JSON.stringify({ opted_out: optedOut }),
    });
  }
  listRunMemories(runId: string): Promise<MemoryItem[]> {
    return this.http.request(`/v1/runs/${enc(runId)}/memories`);
  }
}

export const conversationsApi = new ConversationApi();
