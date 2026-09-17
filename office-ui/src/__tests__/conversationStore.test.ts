import { beforeEach, describe, expect, it, vi } from "vitest";
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
});
