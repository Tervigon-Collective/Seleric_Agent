import { create } from "zustand";
import { conversationsApi, conversationScope } from "../api/conversations";
import type { ActivityEvent, MemoryItem, Message, Thread } from "../api/contracts";
import { ApiError } from "../api/http";
import { subscribeToRunEvents, subscribeToThreadEvents } from "../api/runEvents";
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
  progress: string | null;
  /** Words being spoken by voice that have no persisted user message yet. */
  voicePending: string | null;
  setVoicePending: (text: string | null) => void;
  followThread: (threadId: string) => Promise<void>;
  stopFollowingThread: () => void;
  loadThreads: () => Promise<void>;
  createThread: () => Promise<void>;
  selectThread: (id: string) => Promise<void>;
  clearSelection: () => void;
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
const TERMINAL_EVENTS = ["run.completed", "run.failed", "run.cancelled"];
let threadFollow: { threadId: string; stop: () => void; runId: string | null } | null = null;
const stopThreadFollow = () => {
  threadFollow?.stop();
  threadFollow = null;
};
let demoSubmission = 0;
let selectionGeneration = 0;
let submissionGeneration = 0;
const cancelledSubmissions = new Set<number>();
const textMessage = (threadId: string, role: "USER" | "ASSISTANT", text: string, id: string): Message => ({
  id, thread_id: threadId, workspace_id: "local", user_id: role === "USER" ? "me" : null,
  role, parts: [{ type: "TEXT", content: text }], run_id: null,
  parent_message_id: null, created_at: now(),
});
const optionalString = (value: unknown): string | undefined =>
  typeof value === "string" && value ? value : undefined;
const messageText = (message: Message | undefined): string =>
  message?.parts
    .filter((part) => part.type === "TEXT" && typeof part.content === "string")
    .map((part) => String(part.content))
    .join("\n")
    .trim() ?? "";
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
      ?? optionalString(event.payload.agent)
      ?? (optionalString(event.payload.route) === "v3" ? "coordinator" : undefined),
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
  progress: null,
  voicePending: null,

  setVoicePending: (voicePending) => set({ voicePending }),

  // Watches the whole thread so runs started outside this UI (voice) show up
  // live. Runs this UI submits itself keep their own stream; the follower only
  // adopts a run while nothing is in flight, then forwards it to applyRunEvent.
  followThread: async (threadId) => {
    stopThreadFollow();
    if (get().demoMode) return;
    let afterSequence = 0;
    try {
      const existing = await conversationsApi.listThreadEvents(threadId);
      afterSequence = Math.max(0, ...existing.map((event) => event.thread_sequence ?? 0));
    } catch { /* fall back to the full replay guard below */ }
    if (get().selectedThreadId !== threadId) return;
    const follow = { threadId, runId: null as string | null, stop: () => {} };
    follow.stop = subscribeToThreadEvents(threadId, {
      onEvent: (event) => {
        const state = get();
        if (state.selectedThreadId !== threadId || !event.run_id) return;
        if (event.run_id !== follow.runId) {
          if (state.submitting || TERMINAL_EVENTS.includes(event.event_type)) return;
          follow.runId = event.run_id;
          set({ currentRunId: event.run_id, submitting: true, progress: null, error: null });
          void conversationsApi.listMessages(threadId).then((messages) => {
            if (get().selectedThreadId !== threadId) return;
            set((s) => ({ messages: { ...s.messages, [threadId]: messages }, voicePending: null }));
          }).catch(() => undefined);
        }
        get().applyRunEvent(event);
      },
    }, { afterSequence });
    threadFollow = follow;
  },

  stopFollowingThread: () => {
    stopThreadFollow();
    set({ voicePending: null });
  },

  loadThreads: async () => {
    if (get().demoMode) return;
    set({ loading: true, error: null });
    try {
      const threads = await conversationsApi.listThreads();
      const selectedThreadId = get().selectedThreadId;
      const selectedExists = threads.some((thread) => thread.id === selectedThreadId);
      set({ threads, loading: false, selectedThreadId: selectedExists ? selectedThreadId : null });
      if (!selectedExists && threads[0]) {
        await get().selectThread(threads[0].id);
      } else if (!threads.length) {
        selectionGeneration += 1;
        submissionGeneration += 1;
        subscriptions.forEach((stop) => stop());
        subscriptions.clear();
        set({
          selectedThreadId: null,
          messages: {},
          submitting: false,
          currentRunId: null,
          memories: [],
          usedMemories: [],
          uploads: {},
          error: null,
        });
        useOffice.getState().reset();
      }
    } catch (error) {
      set({ loading: false, error: error instanceof Error ? error.message : "Unable to load threads" });
    }
  },

  createThread: async () => {
    useOffice.getState().reset();
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
    if (threadFollow && threadFollow.threadId !== id) stopThreadFollow();
    const generation = ++selectionGeneration;
    subscriptions.forEach((stop) => stop());
    subscriptions.clear();
    set({
      selectedThreadId: id,
      currentRunId: null,
      submitting: false,
      error: null,
    });
    useOffice.getState().reset();
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
      if (generation !== selectionGeneration || get().selectedThreadId !== id) return;
      const office = useOffice.getState();
      const runEvents = events.filter((event) => !latestRunId || event.run_id === latestRunId);
      const timeline = runEvents.map(toOfficeEvent);
      const latestUserMessage = [...messages]
        .reverse()
        .find((message) => message.role === "USER" && (!latestRunId || message.run_id === latestRunId));
      const terminal = [...runEvents].reverse().find((event) =>
        ["run.completed", "run.failed", "run.cancelled"].includes(event.event_type),
      );
      const artifactIds = new Map<string, Set<string>>();
      runEvents.forEach((event) => {
        const artifactType = optionalString(event.payload.artifact_type);
        if (!artifactType || !event.event_type.startsWith("artifact.")) return;
        const ids = artifactIds.get(artifactType) ?? new Set<string>();
        ids.add(optionalString(event.payload.artifact_id) ?? event.id);
        artifactIds.set(artifactType, ids);
      });
      const route = [...runEvents]
        .reverse()
        .map((event) => optionalString(event.payload.route))
        .find(Boolean) ?? null;
      const missionId = [...runEvents]
        .reverse()
        .map((event) => optionalString(event.payload.mission_id))
        .find(Boolean) ?? latestRunId ?? "conversation";
      office.hydrate({
        missionId,
        query: messageText(latestUserMessage),
        status: terminal?.event_type.replace("run.", "") ?? (latestRunId ? "running" : "idle"),
        route,
        stage: terminal?.event_type.replace("run.", "") ?? (
          runEvents[runEvents.length - 1]?.event_type.replaceAll(".", " ") ?? "intake"
        ),
        leadershipEpoch: 0,
        lastSeq: timeline[timeline.length - 1]?.seq ?? 0,
        agents: [],
        board: { steps: [] },
        handoffs: [],
        artifacts: Object.fromEntries(
          [...artifactIds.entries()].map(([type, ids]) => [type, ids.size]),
        ),
        unresolvedQuestions: [],
        limitations: [],
        timeline,
      });
      // Restore in-flight run state so the Cancel button (driven by
      // `submitting` -> `isRunning`) survives a page refresh. Without this,
      // a refresh drops `submitting`/`currentRunId` (in-memory only) and the
      // "Working on it…" row + composer Cancel never render even though the
      // backend run is still going.
      const stillRunning = Boolean(latestRunId && !terminal);
      if (stillRunning && latestRunId) {
        const runId = latestRunId;
        const lastProgress = [...runEvents].reverse().find(
          (event) => event.event_type.startsWith("agent.") && event.summary,
        )?.summary ?? null;
        subscriptions.get(id)?.();
        subscriptions.set(id, subscribeToRunEvents(runId, {
          onEvent: (event) => {
            const state = get();
            if (state.selectedThreadId === id && state.currentRunId === runId) {
              state.applyRunEvent(event);
            }
          },
          onError: () => {
            if (get().selectedThreadId === id && get().currentRunId === runId) {
              set({ error: "Event stream interrupted; reconnecting…" });
            }
          },
          onState: (state) => {
            if (
              state === "closed"
              && get().submitting
              && get().selectedThreadId === id
              && get().currentRunId === runId
            ) {
              set({
                submitting: false,
                currentRunId: null,
                error: "Run updates stopped before completion. You can send another prompt.",
              });
            }
          },
        }));
        set((s) => ({
          messages: { ...s.messages, [id]: messages },
          loading: false,
          submitting: true,
          currentRunId: runId,
          progress: lastProgress,
        }));
      } else {
        set((s) => ({ messages: { ...s.messages, [id]: messages }, loading: false, submitting: false, currentRunId: null, progress: null }));
      }
    } catch (error) {
      if (generation === selectionGeneration && get().selectedThreadId === id) {
        if (error instanceof ApiError && error.status === 404) {
          subscriptions.get(id)?.();
          subscriptions.delete(id);
          set((state) => {
            const messages = { ...state.messages };
            delete messages[id];
            return {
              threads: state.threads.filter((thread) => thread.id !== id),
              selectedThreadId: null,
              messages,
              loading: false,
              submitting: false,
              currentRunId: null,
              memories: [],
              usedMemories: [],
              error: null,
            };
          });
          useOffice.getState().reset();
          return;
        }
        set({ loading: false, error: error instanceof Error ? error.message : "Unable to load messages" });
      }
    }
  },

  clearSelection: () => {
    selectionGeneration += 1;
    submissionGeneration += 1;
    subscriptions.forEach((stop) => stop());
    subscriptions.clear();
    set({
      selectedThreadId: null,
      loading: false,
      submitting: false,
      currentRunId: null,
      memories: [],
      usedMemories: [],
      uploads: {},
      error: null,
    });
    useOffice.getState().reset();
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
      const wasSelected = get().selectedThreadId === id;
      if (!get().demoMode) await conversationsApi.archiveThread(id);
      const threads = get().threads.filter((thread) => thread.id !== id);
      const nextThreadId = wasSelected ? threads[0]?.id ?? null : get().selectedThreadId;
      if (wasSelected) {
        subscriptions.get(id)?.();
        subscriptions.delete(id);
        useOffice.getState().reset();
      }
      set({
        threads,
        selectedThreadId: nextThreadId,
        submitting: wasSelected ? false : get().submitting,
        currentRunId: wasSelected ? null : get().currentRunId,
      });
      if (wasSelected && nextThreadId) await get().selectThread(nextThreadId);
    } catch (error) {
      set({ error: error instanceof Error ? error.message : "Unable to archive thread" });
    }
  },

  setSearch: (search) => set({ search }),
  setDemoMode: (demoMode) => set(demoMode
    ? { demoMode, threads: [DEMO_THREAD], selectedThreadId: DEMO_THREAD.id, messages: { [DEMO_THREAD.id]: [DEMO_MESSAGE] }, memories: [DEMO_MEMORY], usedMemories: [DEMO_MEMORY], memoryOptedOut: false, error: null }
    : { demoMode, threads: [], selectedThreadId: null, messages: {}, memories: [], usedMemories: [], memoryOptedOut: false, error: null }),

  submit: async (text, attachmentIds = [], parentMessageId = null) => {
    const value = text.trim() || (attachmentIds.length ? "Attached files" : "");
    if (!value || get().submitting) return;
    const generation = ++submissionGeneration;
    let threadId = get().selectedThreadId;
    if (!threadId) {
      set({ submitting: true, error: null });
      try {
        const thread = await conversationsApi.createThread();
        threadId = thread.id;
        set((state) => ({
          threads: [thread, ...state.threads],
          selectedThreadId: thread.id,
          messages: { ...state.messages, [thread.id]: [] },
        }));
      } catch (error) {
        set({
          submitting: false,
          error: error instanceof Error ? error.message : "Unable to start conversation",
        });
        return;
      }
    }
    if (cancelledSubmissions.delete(generation)) {
      set({ submitting: false, currentRunId: null });
      return;
    }
    const optimistic = {
      ...textMessage(threadId, "USER", value, `optimistic_${Date.now()}`),
      parent_message_id: parentMessageId,
    };
    useOffice.getState().reset();
    useOffice.getState().hydrate({
      missionId: "pending",
      query: value,
      status: "running",
      stage: "intake",
      route: null,
      leadershipEpoch: 0,
      lastSeq: 0,
      agents: [],
      board: { steps: [] },
      handoffs: [],
      artifacts: {},
      unresolvedQuestions: [],
      limitations: [],
      timeline: [],
    });
    set((s) => ({
      submitting: true, error: null, progress: null,
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
      if (submission !== demoSubmission || cancelledSubmissions.delete(generation)) return;
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
        scope: conversationScope(),
        execution_mode: "development",
      });
      if (cancelledSubmissions.delete(generation)) {
        await conversationsApi.cancelRun(result.run_id).catch(() => undefined);
        if (generation === submissionGeneration) {
          set({ submitting: false, currentRunId: null });
        }
        return;
      }
      if (
        generation !== submissionGeneration
        || get().selectedThreadId !== threadId
      ) return;
      useOffice.getState().hydrate({
        missionId: result.mission_id,
        query: value,
        status: "running",
        stage: "intake",
        route: null,
        leadershipEpoch: 0,
        lastSeq: 0,
        agents: [],
        board: { steps: [] },
        handoffs: [],
        artifacts: {},
        unresolvedQuestions: [],
        limitations: [],
        timeline: [],
      });
      set((s) => ({
        currentRunId: result.run_id,
        messages: {
          ...s.messages,
          [threadId]: (s.messages[threadId] ?? []).map((message) =>
            message.id === optimistic.id
              ? { ...message, id: result.message_id, run_id: result.run_id }
              : message
          ),
        },
      }));
      subscriptions.get(threadId)?.();
      subscriptions.set(threadId, subscribeToRunEvents(result.run_id, {
        onEvent: (event) => {
          const state = get();
          if (state.selectedThreadId === threadId && state.currentRunId === result.run_id) {
            state.applyRunEvent(event);
          }
        },
        onError: () => {
          if (get().selectedThreadId === threadId && get().currentRunId === result.run_id) {
            set({ error: "Event stream interrupted; reconnecting…" });
          }
        },
        onState: (state) => {
          if (
            state === "closed"
            && get().submitting
            && get().selectedThreadId === threadId
            && get().currentRunId === result.run_id
          ) {
            set({
              submitting: false,
              currentRunId: null,
              error: "Run updates stopped before completion. You can send another prompt.",
            });
          }
        },
      }));
    } catch (error) {
      cancelledSubmissions.delete(generation);
      if (generation === submissionGeneration) {
        set({ submitting: false, error: error instanceof Error ? error.message : "Unable to submit message" });
      }
    }
  },

  cancelRun: async () => {
    const { currentRunId, demoMode, selectedThreadId } = get();
    if (!get().submitting) return;
    const generation = submissionGeneration;
    if (!currentRunId) cancelledSubmissions.add(generation);
    set({ submitting: false, currentRunId: null, progress: null });
    try {
      if (!demoMode && currentRunId) await conversationsApi.cancelRun(currentRunId);
      demoSubmission += 1;
      if (selectedThreadId) {
        subscriptions.get(selectedThreadId)?.();
        subscriptions.delete(selectedThreadId);
        if (!demoMode && currentRunId) {
          // The stream is closed, so pull the persisted state; otherwise the
          // pending reply just vanishes until the next reload.
          void conversationsApi.listMessages(selectedThreadId).then((messages) => {
            if (get().selectedThreadId !== selectedThreadId) return;
            set((s) => ({ messages: { ...s.messages, [selectedThreadId]: messages } }));
          }).catch(() => undefined);
        }
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        // The run already reached a terminal state server-side (it just
        // finished between render and click, or state was restored stale
        // after a refresh). Cancel is effectively satisfied: sync the
        // persisted messages instead of flashing "run is not cancellable".
        if (selectedThreadId && !demoMode) {
          void conversationsApi.listMessages(selectedThreadId).then((messages) => {
            if (get().selectedThreadId !== selectedThreadId) return;
            set((s) => ({ messages: { ...s.messages, [selectedThreadId]: messages } }));
          }).catch(() => undefined);
        }
        set({ progress: null, error: null });
        return;
      }
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
    let threadId = get().selectedThreadId;
    if (!threadId) {
      await get().createThread();
      threadId = get().selectedThreadId;
    }
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
    const state = get();
    if (
      event.thread_id !== state.selectedThreadId
      || (state.currentRunId && event.run_id !== state.currentRunId)
    ) return;
    // Live answer streaming: accumulate answer.delta into an optimistic
    // assistant message; answer.reset clears a revised-away draft. The full
    // message fetched on answer.completed is authoritative and replaces this.
    // Handled before ingestEvent so per-token deltas never spam the timeline.
    if (event.event_type === "answer.delta" || event.event_type === "answer.reset") {
      const threadId = event.thread_id;
      const messageId = optionalString(event.payload.message_id);
      if (!messageId) return;
      const reset = event.event_type === "answer.reset";
      const delta = reset ? "" : optionalString(event.payload.delta) ?? "";
      if (!reset && !delta) return;
      set((s) => {
        const list = s.messages[threadId] ?? [];
        const existing = list.find((m) => m.id === messageId);
        const nextText = reset ? "" : messageText(existing) + delta;
        const streamed: Message = {
          id: messageId,
          thread_id: threadId,
          workspace_id: existing?.workspace_id ?? "local",
          user_id: null,
          role: "ASSISTANT",
          parts: [{ type: "TEXT", content: nextText }],
          run_id: event.run_id ?? existing?.run_id ?? null,
          parent_message_id: existing?.parent_message_id ?? null,
          created_at: existing?.created_at ?? now(),
        };
        const nextList = existing
          ? list.map((m) => (m.id === messageId ? streamed : m))
          : [...list, streamed];
        return { messages: { ...s.messages, [threadId]: nextList } };
      });
      return;
    }
    useOffice.getState().ingestEvent(toOfficeEvent(event));
    if (event.event_type.startsWith("agent.") && event.summary) {
      set({ progress: event.summary });
    }
    const office = useOffice.getState();
    const incomingRoute = optionalString(event.payload.route);
    const streamedAnswer = optionalString(event.payload.final_response);
    const terminal = ["run.completed", "run.failed", "run.cancelled"].includes(event.event_type);
    if (event.event_type === "answer.completed" || terminal || incomingRoute) {
      const route = incomingRoute ?? office.route;
      const status = terminal ? event.event_type.replace("run.", "") : office.status;
      office.hydrate({
        missionId: optionalString(event.payload.mission_id) ?? office.missionId ?? event.run_id ?? "conversation",
        query: optionalString(event.payload.query) ?? office.query,
        status,
        route,
        stage: status || office.stage,
        leadershipEpoch: office.leadershipEpoch,
        lastSeq: event.sequence,
        agents: [],
        board: { steps: office.board },
        handoffs: office.handoffs,
        artifacts: office.artifacts,
        unresolvedQuestions: office.unresolvedQuestions,
        limitations: office.limitations,
        finalResponse: streamedAnswer ?? office.finalResponse,
        timeline: [],
      });
    }
    if (streamedAnswer && (event.event_type === "answer.completed" || terminal)) {
      const evidence = Array.isArray(event.payload.evidence) ? event.payload.evidence : [];
      const parts: Message["parts"] = [{ type: "TEXT", content: streamedAnswer }];
      for (const row of evidence) {
        if (!row || typeof row !== "object") continue;
        const source = row as Record<string, unknown>;
        const evidenceId = optionalString(source.evidence_id);
        if (!evidenceId) continue;
        const metric = optionalString(source.metric_or_fact) ?? "Evidence";
        parts.push({
          type: "SOURCE",
          content: {
            evidence_id: evidenceId,
            title: metric.replace(/^metric\./, "").replaceAll("_", " "),
            excerpt: source.value == null ? undefined : String(source.value),
          },
        });
      }
      const answer: Message = {
        id: optionalString(event.payload.message_id) ?? `answer_${event.run_id ?? event.id}`,
        thread_id: event.thread_id,
        workspace_id: event.workspace_id,
        user_id: null,
        role: "ASSISTANT",
        parts,
        run_id: event.run_id,
        parent_message_id: null,
        created_at: event.created_at,
      };
      set((s) => {
        const existing = s.messages[event.thread_id] ?? [];
        const index = existing.findIndex((message) =>
          message.id === answer.id
          || (message.run_id === answer.run_id && message.role === "ASSISTANT")
        );
        const next = index >= 0
          ? existing.map((message, i) => i === index ? { ...message, ...answer, parts } : message)
          : [...existing, answer];
        return { messages: { ...s.messages, [event.thread_id]: next }, error: null };
      });
    }
    if (event.event_type === "answer.completed" || terminal) {
      void conversationsApi.listMessages(event.thread_id).then((messages) => {
        const current = get();
        if (
          current.selectedThreadId !== event.thread_id
          || (current.currentRunId && event.run_id !== current.currentRunId)
        ) return;
        set((s) => ({ messages: { ...s.messages, [event.thread_id]: messages }, submitting: !terminal && s.submitting, error: null }));
      });
    }
    if (terminal) {
      subscriptions.get(event.thread_id)?.();
      subscriptions.delete(event.thread_id);
      if (get().selectedThreadId === event.thread_id) {
        set({ submitting: false, currentRunId: null, progress: null });
      }
    }
  },

  reset: () => {
    stopThreadFollow();
    demoSubmission += 1;
    selectionGeneration += 1;
    submissionGeneration += 1;
    cancelledSubmissions.clear();
    subscriptions.forEach((stop) => stop());
    subscriptions.clear();
    set({ threads: [], selectedThreadId: null, messages: {}, loading: false, submitting: false, uploads: {}, error: null, search: "", demoMode: false, memories: [], usedMemories: [], memoryOptedOut: false, currentRunId: null, progress: null, voicePending: null });
  },
}));
