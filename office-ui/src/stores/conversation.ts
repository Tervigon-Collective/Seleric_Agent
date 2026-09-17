import { create } from "zustand";
import { conversationsApi } from "../api/conversations";
import type { ActivityEvent, MemoryItem, Message, Thread } from "../api/contracts";
import { subscribeToRunEvents } from "../api/runEvents";
import { useOffice } from "../store";
import type { SwarmUIEvent } from "../types";

interface ConversationState {
  threads: Thread[];
  selectedThreadId: string | null;
  messages: Record<string, Message[]>;
  loading: boolean;
  submitting: boolean;
  uploads: Record<string, "uploading" | "ready" | "failed">;
  error: string | null;
  search: string;
  demoMode: boolean;
  memories: MemoryItem[];
  usedMemories: MemoryItem[];
  memoryOptedOut: boolean;
  currentRunId: string | null;
  loadThreads: () => Promise<void>;
  createThread: () => Promise<void>;
  selectThread: (id: string) => Promise<void>;
  renameThread: (id: string, title: string) => Promise<void>;
  archiveThread: (id: string) => Promise<void>;
  setSearch: (value: string) => void;
  setDemoMode: (demo: boolean) => void;
  submit: (text: string, attachmentIds?: string[], parentMessageId?: string | null) => Promise<void>;
  cancelRun: () => Promise<void>;
  retryFrom: (parentMessageId: string | null) => Promise<void>;
  uploadAttachment: (file: File) => Promise<string | null>;
  loadMemories: () => Promise<void>;
  addMemory: (content: string) => Promise<void>;
  editMemory: (id: string, content: string) => Promise<void>;
  pinMemory: (id: string, pinned: boolean) => Promise<void>;
  moveMemory: (id: string) => Promise<void>;
  archiveMemory: (id: string) => Promise<void>;
  deleteMemory: (id: string) => Promise<void>;
  setMemoryOptOut: (optedOut: boolean) => Promise<void>;
  applyRunEvent: (event: ActivityEvent) => void;
  reset: () => void;
}

const now = () => new Date().toISOString();
const DEMO_THREAD: Thread = {
  id: "thread_demo", workspace_id: "demo", owner_user_id: "demo", project_id: null,
  title: "Why has CAC increased?", status: "ACTIVE", metadata: {},
  created_at: now(), updated_at: now(),
};
const DEMO_MESSAGE: Message = {
  id: "message_demo", thread_id: DEMO_THREAD.id, workspace_id: "demo", user_id: "demo",
  role: "USER", parts: [{ type: "TEXT", content: "Why has CAC increased?" }],
  run_id: null, parent_message_id: null, created_at: now(),
};
const DEMO_MEMORY: MemoryItem = {
  id: "memory_demo", workspace_id: "demo", owner_user_id: "demo", project_id: null,
  thread_id: DEMO_THREAD.id, scope: "THREAD", type: "PREFERENCE", status: "ACTIVE",
  content: "Prefer concise answers with cited evidence.", normalized_content: "prefer concise answers with cited evidence",
  provenance: { source: "demo", message_id: DEMO_MESSAGE.id }, confidence: 1, salience: 0.8,
  source_run_id: null, source_message_ids: [DEMO_MESSAGE.id], source_evidence_ids: [],
  pinned: true, created_at: now(), updated_at: now(),
};

const subscriptions = new Map<string, () => void>();
let demoSubmission = 0;
const textMessage = (threadId: string, role: "USER" | "ASSISTANT", text: string, id: string): Message => ({
  id, thread_id: threadId, workspace_id: "local", user_id: role === "USER" ? "me" : null,
  role, parts: [{ type: "TEXT", content: text }], run_id: null,
  parent_message_id: null, created_at: now(),
});
const optionalString = (value: unknown): string | undefined =>
  typeof value === "string" && value ? value : undefined;
const toOfficeEvent = (event: ActivityEvent): SwarmUIEvent => {
  const sourceKind = optionalString(event.payload.source_kind);
  const eventType = (sourceKind ?? event.event_type).replaceAll(".", "_");
  const artifactId = optionalString(event.payload.artifact_id);
  return {
    eventId: event.id,
    seq: event.sequence,
    timestamp: event.created_at,
    missionId: optionalString(event.payload.mission_id) ?? event.run_id ?? "current-run",
    taskId: optionalString(event.payload.task_id),
    agentId:
      event.actor_id
      ?? optionalString(event.payload.agent_id)
      ?? optionalString(event.payload.agent),
    eventType,
    summary:
      event.summary
      ?? event.title
      ?? eventType.replaceAll("_", " "),
    artifactRefs: [...event.evidence_ids, ...(artifactId ? [artifactId] : [])],
    metadata: { ...event.metadata, ...event.payload, conversationEventType: event.event_type },
  };
};

export const useConversationStore = create<ConversationState>((set, get) => ({
  threads: [],
  selectedThreadId: null,
  messages: {},
  loading: false,
  submitting: false,
  uploads: {},
  error: null,
  search: "",
  demoMode: false,
  memories: [],
  usedMemories: [],
  memoryOptedOut: false,
  currentRunId: null,

  loadThreads: async () => {
    if (get().demoMode) return;
    set({ loading: true, error: null });
    try {
      const threads = await conversationsApi.listThreads();
      set({ threads, loading: false });
      if (!get().selectedThreadId && threads[0]) await get().selectThread(threads[0].id);
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : "Unable to load threads" });
    }
  },

  createThread: async () => {
    if (get().demoMode) {
      const id = `thread_demo_${Date.now()}`;
      const thread = { ...DEMO_THREAD, id, title: "New conversation", created_at: now(), updated_at: now() };
      set((s) => ({ threads: [thread, ...s.threads], selectedThreadId: id, messages: { ...s.messages, [id]: [] } }));
      return;
    }
    try {
      const thread = await conversationsApi.createThread();
      set((s) => ({ threads: [thread, ...s.threads], selectedThreadId: thread.id, messages: { ...s.messages, [thread.id]: [] } }));
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Unable to create thread" });
    }
  },

  selectThread: async (id) => {
    set({ selectedThreadId: id, error: null });
    if (get().demoMode) return;
    set({ loading: true });
    try {
      const [messages, events] = await Promise.all([
        conversationsApi.listMessages(id),
        conversationsApi.listThreadEvents(id),
      ]);
      const latestRunId = [...messages]
        .reverse()
        .find((message) => message.run_id)?.run_id;
      const office = useOffice.getState();
      office.reset();
      events
        .filter((event) => !latestRunId || event.run_id === latestRunId)
        .forEach((event) => office.ingestEvent(toOfficeEvent(event)));
      set((s) => ({ messages: { ...s.messages, [id]: messages }, loading: false }));
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : "Unable to load messages" });
    }
  },

  renameThread: async (id, value) => {
    const title = value.trim();
    if (!title) return;
    try {
      const existing = get().threads.find((item) => item.id === id);
      if (!existing) return;
      const thread = get().demoMode
        ? { ...existing, title, updated_at: now() }
        : await conversationsApi.updateThread(id, { title });
      set((state) => ({
        threads: state.threads.map((item) => item.id === id ? thread : item),
      }));
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Unable to rename conversation" });
    }
  },

  archiveThread: async (id) => {
    try {
      if (!get().demoMode) await conversationsApi.archiveThread(id);
      set((s) => {
        const threads = s.threads.filter((thread) => thread.id !== id);
        return { threads, selectedThreadId: s.selectedThreadId === id ? threads[0]?.id ?? null : s.selectedThreadId };
      });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Unable to archive thread" });
    }
  },

  setSearch: (search) => set({ search }),
  setDemoMode: (demoMode) => set(demoMode
    ? { demoMode, threads: [DEMO_THREAD], selectedThreadId: DEMO_THREAD.id, messages: { [DEMO_THREAD.id]: [DEMO_MESSAGE] }, memories: [DEMO_MEMORY], usedMemories: [DEMO_MEMORY], memoryOptedOut: false, error: null }
    : { demoMode, threads: [], selectedThreadId: null, messages: {}, memories: [], usedMemories: [], memoryOptedOut: false, error: null }),

  submit: async (text, attachmentIds = [], parentMessageId = null) => {
    const value = text.trim();
    const threadId = get().selectedThreadId;
    if (!value || !threadId || get().submitting) return;
    const optimistic = {
      ...textMessage(threadId, "USER", value, `optimistic_${Date.now()}`),
      parent_message_id: parentMessageId,
    };
    useOffice.getState().reset();
    set((s) => ({
      submitting: true, error: null,
      messages: { ...s.messages, [threadId]: [...(s.messages[threadId] ?? []), optimistic] },
      threads: s.threads.map((thread) =>
        thread.id === threadId && (!thread.title || thread.title === "Untitled conversation")
          ? { ...thread, title: value.replace(/\s+/g, " ").slice(0, 80), updated_at: now() }
          : thread
      ),
    }));
    if (get().demoMode) {
      const submission = ++demoSubmission;
      await new Promise((resolve) => setTimeout(resolve, 350));
      if (submission !== demoSubmission) return;
      const reply = textMessage(
        threadId, "ASSISTANT",
        "Demo mode is active. The office is replaying a representative swarm run; switch to Live to submit this request to Seleric.",
        `demo_reply_${Date.now()}`,
      );
      set((s) => ({ submitting: false, messages: { ...s.messages, [threadId]: [...(s.messages[threadId] ?? []), reply] } }));
      return;
    }
    try {
      const result = await conversationsApi.submitMessage(threadId, {
        parts: [{
          type: "TEXT",
          content: value,
          metadata: attachmentIds.length ? { attachment_ids: attachmentIds } : {},
        }],
        parent_message_id: parentMessageId,
        scope: { timezone: Intl.DateTimeFormat().resolvedOptions().timeZone },
        execution_mode: "production",
      });
      set({ currentRunId: result.run_id });
      subscriptions.get(threadId)?.();
      subscriptions.set(threadId, subscribeToRunEvents(result.run_id, {
        onEvent: get().applyRunEvent,
        onError: () => set({ error: "Event stream interrupted; reconnecting…" }),
        onState: (state) => {
          if (state === "closed" && get().submitting) {
            set({
              submitting: false,
              currentRunId: null,
              error: "Run updates stopped before completion. You can send another prompt.",
            });
          }
        },
      }));
    } catch (error) {
      set({ submitting: false, error: error instanceof Error ? error.message : "Unable to submit message" });
    }
  },

  cancelRun: async () => {
    const { currentRunId, demoMode, selectedThreadId } = get();
    if (!get().submitting) return;
    try {
      if (!demoMode && currentRunId) await conversationsApi.cancelRun(currentRunId);
      demoSubmission += 1;
      if (selectedThreadId) {
        subscriptions.get(selectedThreadId)?.();
        subscriptions.delete(selectedThreadId);
      }
      set({ submitting: false, currentRunId: null });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Unable to cancel run" });
    }
  },

  retryFrom: async (parentMessageId) => {
    const threadId = get().selectedThreadId;
    if (!threadId) return;
    const messages = get().messages[threadId] ?? [];
    const parentIndex = parentMessageId
      ? messages.findIndex((message) => message.id === parentMessageId)
      : messages.length - 1;
    const source = messages
      .slice(0, parentIndex + 1)
      .reverse()
      .find((message) => message.role === "USER");
    if (!source) return;
    const text = source.parts
      .filter((part) => part.type === "TEXT")
      .map((part) => typeof part.content === "string" ? part.content : JSON.stringify(part.content))
      .join("\n");
    const ids = source.parts.flatMap((part) => {
      const value = part.metadata?.attachment_ids;
      return Array.isArray(value) ? value.filter((id): id is string => typeof id === "string") : [];
    });
    await get().submit(text, ids, source.parent_message_id);
  },

  uploadAttachment: async (file) => {
    const threadId = get().selectedThreadId;
    if (!threadId) return null;
    set((state) => ({ uploads: { ...state.uploads, [file.name]: "uploading" }, error: null }));
    try {
      if (get().demoMode) {
        await new Promise((resolve) => setTimeout(resolve, 150));
        set((state) => ({ uploads: { ...state.uploads, [file.name]: "ready" } }));
        return `demo_attachment_${Date.now()}`;
      }
      const attachment = await conversationsApi.uploadAttachment(threadId, file);
      set((state) => ({ uploads: { ...state.uploads, [file.name]: "ready" } }));
      return attachment.id;
    } catch (error) {
      set((state) => ({
        uploads: { ...state.uploads, [file.name]: "failed" },
        error: error instanceof Error ? error.message : "Unable to upload attachment",
      }));
      return null;
    }
  },

  loadMemories: async () => {
    if (get().demoMode) return;
    const threadId = get().selectedThreadId ?? undefined;
    try {
      const [memories, preference] = await Promise.all([
        conversationsApi.listMemories(threadId),
        conversationsApi.getMemoryPreference(),
      ]);
      const currentRunId = get().currentRunId;
      const usedMemories = currentRunId
        ? await conversationsApi.listRunMemories(currentRunId).catch(() => [])
        : [];
      set({ memories, usedMemories, memoryOptedOut: preference.opted_out });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Unable to load memories" });
    }
  },
  addMemory: async (content) => {
    const threadId = get().selectedThreadId;
    if (get().demoMode) {
      set((s) => ({ memories: [{ ...DEMO_MEMORY, id: `memory_demo_${Date.now()}`, content, pinned: false }, ...s.memories] }));
      return;
    }
    const memory = await conversationsApi.createMemory({
      content, scope: threadId ? "THREAD" : "USER", type: "FACT", thread_id: threadId,
    });
    set((s) => ({ memories: [memory, ...s.memories] }));
  },
  editMemory: async (id, content) => {
    if (get().demoMode) {
      set((s) => ({ memories: s.memories.map((item) => item.id === id ? { ...item, content } : item) }));
      return;
    }
    const memory = await conversationsApi.updateMemory(id, { content });
    set((s) => ({ memories: s.memories.map((item) => item.id === id ? memory : item) }));
  },
  pinMemory: async (id, pinned) => {
    if (get().demoMode) {
      set((s) => ({ memories: s.memories.map((item) => item.id === id ? { ...item, pinned } : item) }));
      return;
    }
    const memory = await conversationsApi.pinMemory(id, pinned);
    set((s) => ({ memories: s.memories.map((item) => item.id === id ? memory : item) }));
  },
  moveMemory: async (id) => {
    const current = get().memories.find((item) => item.id === id);
    if (!current) return;
    const toThread = current.scope === "USER";
    const update = {
      scope: toThread ? "THREAD" as const : "USER" as const,
      thread_id: toThread ? get().selectedThreadId : null,
    };
    if (get().demoMode) {
      set((s) => ({ memories: s.memories.map((item) => item.id === id ? { ...item, ...update } : item) }));
      return;
    }
    const memory = await conversationsApi.moveMemory(id, update);
    set((s) => ({ memories: s.memories.map((item) => item.id === id ? memory : item) }));
  },
  archiveMemory: async (id) => {
    if (get().demoMode) {
      set((s) => ({ memories: s.memories.map((item) => item.id === id ? { ...item, status: "ARCHIVED" } : item) }));
      return;
    }
    const memory = await conversationsApi.archiveMemory(id);
    set((s) => ({ memories: s.memories.map((item) => item.id === id ? memory : item) }));
  },
  deleteMemory: async (id) => {
    if (!get().demoMode) await conversationsApi.deleteMemory(id);
    set((s) => ({ memories: s.memories.filter((item) => item.id !== id) }));
  },
  setMemoryOptOut: async (memoryOptedOut) => {
    if (!get().demoMode) await conversationsApi.setMemoryPreference(memoryOptedOut);
    set({ memoryOptedOut });
  },

  applyRunEvent: (event) => {
    useOffice.getState().ingestEvent(toOfficeEvent(event));
    const terminal = ["run.completed", "run.failed", "run.cancelled"].includes(event.event_type);
    if (event.event_type === "answer.completed" || terminal) {
      void conversationsApi.listMessages(event.thread_id).then((messages) => {
        set((s) => ({ messages: { ...s.messages, [event.thread_id]: messages }, submitting: !terminal && s.submitting, error: null }));
      });
    }
    if (terminal) {
      subscriptions.get(event.thread_id)?.();
      subscriptions.delete(event.thread_id);
      set({ submitting: false });
    }
  },

  reset: () => {
    demoSubmission += 1;
    subscriptions.forEach((stop) => stop());
    subscriptions.clear();
    set({ threads: [], selectedThreadId: null, messages: {}, loading: false, submitting: false, uploads: {}, error: null, search: "", demoMode: false, memories: [], usedMemories: [], memoryOptedOut: false, currentRunId: null });
  },
}));
