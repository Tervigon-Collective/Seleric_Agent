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
    const initiated = await this.initiateAttachment(threadId, file);
    const url = /^https?:\/\//.test(initiated.upload.url)
      ? initiated.upload.url
      : `${this.http.baseUrl}${initiated.upload.url}`;
    const response = await this.http.fetchImpl(url, {
      method: initiated.upload.method,
      headers: this.http.headers({ "Content-Type": file.type || "application/octet-stream" }),
      body: file,
    });
    if (!response.ok) throw new Error(`Attachment upload failed (${response.status})`);
    return response.json() as Promise<Attachment>;
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
