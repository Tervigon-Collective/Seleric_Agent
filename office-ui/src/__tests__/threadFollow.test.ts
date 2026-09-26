import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { conversationsApi } from "../api/conversations";
import * as runEvents from "../api/runEvents";
import type { ActivityEvent } from "../api/contracts";
import { useConversationStore } from "../stores/conversation";

const event = (type: string, runId: string, sequence: number): ActivityEvent => ({
  id: `e${sequence}`, thread_id: "t1", workspace_id: "w", run_id: runId, sequence,
  event_type: type, actor_type: null, actor_id: null, title: null, summary: "Fetching metric data",
  evidence_ids: [], payload: {}, metadata: {}, started_at: null, completed_at: null,
  duration_ms: null, created_at: "2026-09-26T10:00:00Z",
});

describe("following a thread for runs started elsewhere (voice)", () => {
  let emit: (e: ActivityEvent) => void = () => {};
  beforeEach(() => {
    useConversationStore.getState().reset();
    useConversationStore.setState({ selectedThreadId: "t1", messages: { t1: [] } });
    vi.spyOn(conversationsApi, "listMessages").mockResolvedValue([]);
    vi.spyOn(conversationsApi, "listThreadEvents").mockResolvedValue([
      { ...event("run.completed", "old", 3), thread_sequence: 9 },
    ]);
    vi.spyOn(runEvents, "subscribeToThreadEvents").mockImplementation((_id, handlers) => {
      emit = handlers.onEvent;
      return () => undefined;
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("starts the stream after the existing history", async () => {
    await useConversationStore.getState().followThread("t1");
    expect(runEvents.subscribeToThreadEvents).toHaveBeenCalledWith("t1", expect.anything(), { afterSequence: 9 });
  });

  it("adopts a new run, shows progress, and finishes on the terminal event", async () => {
    await useConversationStore.getState().followThread("t1");
    emit(event("agent.tool_started", "voice-run", 1));
    expect(useConversationStore.getState()).toMatchObject({ submitting: true, currentRunId: "voice-run", progress: "Fetching metric data" });
    emit(event("run.completed", "voice-run", 2));
    expect(useConversationStore.getState()).toMatchObject({ submitting: false, currentRunId: null });
  });

  it("ignores events while a typed submission is in flight", async () => {
    await useConversationStore.getState().followThread("t1");
    useConversationStore.setState({ submitting: true });
    emit(event("agent.tool_started", "typed-run", 1));
    expect(useConversationStore.getState().currentRunId).toBeNull();
  });
});
