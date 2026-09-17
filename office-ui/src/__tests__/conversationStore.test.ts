import { beforeEach, describe, expect, it, vi } from "vitest";
import { useConversationStore } from "../stores/conversation";

describe("conversation store submit", () => {
  beforeEach(() => {
    useConversationStore.getState().reset();
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
});
