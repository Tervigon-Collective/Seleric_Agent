import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { conversationsApi } from "../api/conversations";
import * as runEvents from "../api/runEvents";
import { useOffice } from "../store";
import { useConversationStore } from "../stores/conversation";

const ev = (sequence: number, event_type: string, summary: string | null, run_id = "run-1", extra: object = {}) => ({
  id: `e${sequence}`, thread_id: "t1", workspace_id: "w", run_id, sequence, event_type,
  actor_type: null, actor_id: null, title: null, summary, evidence_ids: [],
  payload: { mission_id: "mission-1", ...extra }, metadata: {}, started_at: null, completed_at: null,
  duration_ms: null, created_at: "2026-10-27T10:00:00Z",
});
const msg = (id: string, thread_id: string, run_id: string) => ({
  id, thread_id, workspace_id: "w", user_id: "u", role: "USER", parts: [{ type: "TEXT", content: "q" }],
  run_id, parent_message_id: null, created_at: "2026-10-27T10:00:00Z",
}) as never;

describe("switching threads while a run is in flight", () => {
  beforeEach(() => {
    useConversationStore.getState().reset();
    useOffice.getState().reset();
    useConversationStore.setState({ demoMode: false, selectedThreadId: null, threads: [], messages: {} });
  });
  afterEach(() => vi.restoreAllMocks());

  it("keeps the running step when coming back", async () => {
    vi.spyOn(runEvents, "subscribeToRunEvents").mockReturnValue(() => {});
    vi.spyOn(conversationsApi, "listMessages").mockResolvedValue([msg("m1", "t1", "run-1")]);
    vi.spyOn(conversationsApi, "listThreadEvents").mockResolvedValue([
      ev(1, "run.started", null), ev(2, "agent.stage", "Reading your question"),
      ev(3, "agent.tool_started", "Fetching metric data"), ev(4, "agent.thinking_delta", "Thinking…", "run-1", { delta: "weighing " }), ev(5, "agent.thinking_delta", "Thinking…", "run-1", { delta: "it" }),
    ] as never);
    await useConversationStore.getState().selectThread("t1");
    const s = useConversationStore.getState();
    expect(s.submitting).toBe(true);
    expect(s.progress).toBe("Fetching metric data");
    expect(s.thinkingText).toBe("weighing it");
  });
});
