import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { VoiceDock } from "../components/VoiceOverlay";
import { useConversationStore } from "../stores/conversation";
import { useVoiceStore } from "../stores/voice";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("voice orb state", () => {
  it("shows thinking from the spoken query until the answer, then done, then listening", async () => {
    vi.useFakeTimers();
    useVoiceStore.setState({ status: "listening", muted: false, expanded: false });
    useConversationStore.setState({ submitting: false, voicePending: null, progress: null });
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => { root.render(<VoiceDock />); });
    const orb = () => host.querySelector("signal-orb")?.getAttribute("state");

    expect(orb()).toBe("listening");
    await act(async () => { useConversationStore.setState({ voicePending: "how are sales" }); });
    expect(orb()).toBe("thinking");
    await act(async () => { useConversationStore.setState({ voicePending: null, submitting: true }); });
    expect(orb()).toBe("thinking");
    await act(async () => { useConversationStore.setState({ submitting: false }); });
    expect(orb()).toBe("done");
    await act(async () => { vi.advanceTimersByTime(1500); });
    expect(orb()).toBe("listening");
    await act(async () => { useVoiceStore.setState({ status: "speaking" }); });
    expect(orb()).toBe("speaking");

    await act(async () => { root.unmount(); });
    host.remove();
    vi.useRealTimers();
  });
});
