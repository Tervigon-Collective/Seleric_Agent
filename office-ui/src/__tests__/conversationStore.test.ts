import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { conversationsApi } from "../api/conversations";
import { ApiError } from "../api/http";
import { useOffice } from "../store";
import { useConversationStore } from "../stores/conversation";

describe("conversation store submit", () => {
  beforeEach(() => {
    useConversationStore.getState().reset();
    useOffice.getState().reset();
    useConversationStore.setState({
      demoMode: true,
      selectedThreadId: "t1",
      threads: [],
      messages: { t1: [] },
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("optimistically renders a user message and resolves the running placeholder", async () => {
    vi.useFakeTimers();
    const result = useConversationStore.getState().submit("  investigate CAC  ");
    expect(useConversationStore.getState().submitting).toBe(true);
    expect(useConversationStore.getState().messages.t1[0].parts[0].content).toBe("investigate CAC");
    await vi.advanceTimersByTimeAsync(400);
    await result;
    const state = useConversationStore.getState();
    expect(state.submitting).toBe(false);
    expect(state.messages.t1[state.messages.t1.length - 1]?.role).toBe("ASSISTANT");
    vi.useRealTimers();
  });

  it("forwards live run events to the activity timeline", () => {
    useConversationStore.getState().applyRunEvent({
      id: "event-1",
      thread_id: "t1",
      workspace_id: "w1",
      run_id: "run-1",
      sequence: 1,
      event_type: "agent.completed",
      actor_type: "agent",
      actor_id: null,
      title: null,
      summary: null,
      evidence_ids: [],
      payload: {
        mission_id: "mission-1",
        source_kind: "observed",
        agent: "performance_agent",
      },
      metadata: {},
      started_at: null,
      completed_at: null,
      duration_ms: null,
      created_at: "2026-09-17T10:00:00Z",
    });

    expect(useOffice.getState().timeline[0]).toMatchObject({
      eventType: "observed",
      agentId: "performance_agent",
      missionId: "mission-1",
    });
  });

  it("creates a conversation when the first message is sent", async () => {
    useConversationStore.setState({
      demoMode: false,
      selectedThreadId: null,
      threads: [],
      messages: {},
    });
    vi.spyOn(conversationsApi, "createThread").mockResolvedValue({
      id: "new-thread",
      workspace_id: "default",
      owner_user_id: "default",
      project_id: null,
      title: null,
      status: "ACTIVE",
      metadata: {},
      created_at: "2026-09-17T10:00:00Z",
      updated_at: "2026-09-17T10:00:00Z",
    });
    vi.spyOn(conversationsApi, "submitMessage").mockResolvedValue({
      message_id: "message-1",
      run_id: "run-1",
      mission_id: "mission-1",
    });

    await useConversationStore.getState().submit("Investigate checkout conversion");

    const state = useConversationStore.getState();
    expect(state.selectedThreadId).toBe("new-thread");
    expect(state.threads[0]?.title).toBe("Investigate checkout conversion");
    expect(state.messages["new-thread"][0]?.role).toBe("USER");
    expect(state.messages["new-thread"][0]?.id).toBe("message-1");
    expect(useOffice.getState().query).toBe("Investigate checkout conversion");
    useConversationStore.getState().reset();
  });

  it("clears a stale running state when no conversations remain", async () => {
    useConversationStore.setState({
      demoMode: false,
      selectedThreadId: "removed-thread",
      threads: [],
      messages: { "removed-thread": [] },
      submitting: true,
      currentRunId: "orphaned-run",
    });
    vi.spyOn(conversationsApi, "listThreads").mockResolvedValue([]);

    await useConversationStore.getState().loadThreads();

    const state = useConversationStore.getState();
    expect(state.selectedThreadId).toBeNull();
    expect(state.submitting).toBe(false);
    expect(state.currentRunId).toBeNull();
    expect(state.messages).toEqual({});
  });

  it("falls back to a clean new chat when a deep-linked thread is missing", async () => {
    useConversationStore.setState({
      demoMode: false,
      selectedThreadId: null,
      threads: [],
      messages: {},
    });
    vi.spyOn(conversationsApi, "listMessages").mockRejectedValue(
      new ApiError(404, "Thread not found"),
    );
    vi.spyOn(conversationsApi, "listThreadEvents").mockResolvedValue([]);

    await useConversationStore.getState().selectThread("missing-thread");

    const state = useConversationStore.getState();
    expect(state.selectedThreadId).toBeNull();
    expect(state.error).toBeNull();
    expect(state.loading).toBe(false);
    expect(state.submitting).toBe(false);
  });

  it("hydrates context and artifact summaries when selecting a conversation", async () => {
    useConversationStore.setState({
      demoMode: false,
      selectedThreadId: null,
      threads: [{
        id: "t1", workspace_id: "w", owner_user_id: "u", project_id: null,
        title: "Investigate CAC", status: "ACTIVE", metadata: {},
        created_at: "2026-09-17T10:00:00Z", updated_at: "2026-09-17T10:00:00Z",
      }],
      messages: {},
    });
    vi.spyOn(conversationsApi, "listMessages").mockResolvedValue([{
      id: "m1", thread_id: "t1", workspace_id: "w", user_id: "u", role: "USER",
      parts: [{ type: "TEXT", content: "Why did CAC rise?" }], run_id: "run-1",
      parent_message_id: null, created_at: "2026-09-17T10:00:00Z",
    }]);
    vi.spyOn(conversationsApi, "listThreadEvents").mockResolvedValue([
      {
        id: "e1", thread_id: "t1", workspace_id: "w", run_id: "run-1",
        sequence: 1, event_type: "mission.started", actor_type: null, actor_id: null,
        title: null, summary: null, evidence_ids: [],
        payload: { mission_id: "mission-1", route: "swarm" }, metadata: {},
        started_at: null, completed_at: null, duration_ms: null,
        created_at: "2026-09-17T10:00:01Z",
      },
      {
        id: "e2", thread_id: "t1", workspace_id: "w", run_id: "run-1",
        sequence: 2, event_type: "artifact.created", actor_type: null, actor_id: null,
        title: null, summary: null, evidence_ids: ["ev-1"],
        payload: { artifact_id: "artifact-1", artifact_type: "evidence" }, metadata: {},
        started_at: null, completed_at: null, duration_ms: null,
        created_at: "2026-09-17T10:00:02Z",
      },
    ]);

    await useConversationStore.getState().selectThread("t1");

    expect(useOffice.getState()).toMatchObject({
      missionId: "mission-1",
      query: "Why did CAC rise?",
      route: "swarm",
      artifacts: { evidence: 1 },
    });
  });

  it("cancels a run that is requested before submit returns its run id", async () => {
    useConversationStore.setState({
      demoMode: false,
      selectedThreadId: "t1",
      threads: [],
      messages: { t1: [] },
    });
    let resolveSubmit!: (value: { message_id: string; run_id: string; mission_id: string }) => void;
    vi.spyOn(conversationsApi, "submitMessage").mockReturnValue(new Promise((resolve) => {
      resolveSubmit = resolve;
    }));
    const cancel = vi.spyOn(conversationsApi, "cancelRun").mockResolvedValue({
      run_id: "run-late", status: "CANCELLED",
    });

    const submission = useConversationStore.getState().submit("Stop this");
    await useConversationStore.getState().cancelRun();
    resolveSubmit({ message_id: "m1", run_id: "run-late", mission_id: "mission-1" });
    await submission;

    expect(cancel).toHaveBeenCalledWith("run-late");
    expect(useConversationStore.getState().submitting).toBe(false);
    expect(useConversationStore.getState().currentRunId).toBeNull();
  });

  it("auto-creates a thread before uploading an attachment", async () => {
    useConversationStore.setState({
      demoMode: false, selectedThreadId: null, threads: [], messages: {},
    });
    vi.spyOn(conversationsApi, "createThread").mockResolvedValue({
      id: "attachment-thread", workspace_id: "w", owner_user_id: "u", project_id: null,
      title: null, status: "ACTIVE", metadata: {}, created_at: "now", updated_at: "now",
    });
    vi.spyOn(conversationsApi, "uploadAttachment").mockResolvedValue({
      id: "a1", filename: "note.txt", content_type: "text/plain", size_bytes: 1,
      checksum_sha256: "abc", status: "READY",
    });

    const id = await useConversationStore.getState().uploadAttachment(
      new File(["x"], "note.txt", { type: "text/plain" }),
    );

    expect(id).toBe("a1");
    expect(conversationsApi.uploadAttachment).toHaveBeenCalledWith(
      "attachment-thread",
      expect.any(File),
    );
  });

  it("ignores events belonging to another selected thread", () => {
    useConversationStore.getState().applyRunEvent({
      id: "stale", thread_id: "other", workspace_id: "w", run_id: "run-old",
      sequence: 2, event_type: "agent.completed", actor_type: null, actor_id: null,
      title: null, summary: "Wrong thread", evidence_ids: [], payload: {}, metadata: {},
      started_at: null, completed_at: null, duration_ms: null, created_at: "now",
    });
    expect(useOffice.getState().timeline).toHaveLength(0);
  });

  it("hydrates office route from answer.completed so Context stays in sync", () => {
    useConversationStore.setState({
      demoMode: false,
      selectedThreadId: "t1",
      currentRunId: "run-1",
      messages: { t1: [] },
    });
    vi.spyOn(conversationsApi, "listMessages").mockResolvedValue([]);
    useConversationStore.getState().applyRunEvent({
      id: "event-answer",
      thread_id: "t1",
      workspace_id: "w1",
      run_id: "run-1",
      sequence: 4,
      event_type: "answer.completed",
      actor_type: null,
      actor_id: null,
      title: null,
      summary: null,
      evidence_ids: [],
      payload: {
        mission_id: "MS-1",
        route: "lookup",
        query: "gross sale",
        final_response: "Gross sales: 1200",
        message_id: "assistant-1",
        evidence: [{ evidence_id: "ev-1", metric_or_fact: "metric.gross_sales", value: 1200 }],
      },
      metadata: {},
      started_at: null,
      completed_at: null,
      duration_ms: null,
      created_at: "2026-09-18T10:00:00Z",
    });
    expect(useOffice.getState()).toMatchObject({
      query: "gross sale",
      route: "lookup",
      missionId: "MS-1",
      finalResponse: "Gross sales: 1200",
    });
    const messages = useConversationStore.getState().messages.t1;
    expect(messages.at(-1)?.role).toBe("ASSISTANT");
    expect(messages.at(-1)?.parts[0]).toMatchObject({ type: "TEXT", content: "Gross sales: 1200" });
    expect(messages.at(-1)?.parts[1]?.type).toBe("SOURCE");
  });

  it("hydrates v3 route from run.started so Activity is not stuck on Swarm", () => {
    useConversationStore.setState({
      demoMode: false,
      selectedThreadId: "t1",
      currentRunId: "run-1",
      messages: { t1: [] },
    });
    useConversationStore.getState().applyRunEvent({
      id: "event-started",
      thread_id: "t1",
      workspace_id: "w1",
      run_id: "run-1",
      sequence: 1,
      event_type: "run.started",
      actor_type: null,
      actor_id: null,
      title: null,
      summary: "Run started",
      evidence_ids: [],
      payload: { mission_id: "MS3-1", route: "v3" },
      metadata: {},
      started_at: null,
      completed_at: null,
      duration_ms: null,
      created_at: "2026-09-19T06:00:00Z",
    });
    expect(useOffice.getState().route).toBe("v3");
    expect(useOffice.getState().timeline[0]?.agentId).toBe("coordinator");
  });
});
