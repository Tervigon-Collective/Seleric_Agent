import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const handlers: Record<string, (...args: any[]) => void> = {};
const room = {
  on(event: string, cb: (...args: any[]) => void) { handlers[event] = cb; return room; },
  connect: vi.fn(async () => undefined),
  disconnect: vi.fn(async () => undefined),
  localParticipant: { setMicrophoneEnabled: vi.fn(async () => undefined), getTrackPublication: vi.fn(() => undefined) },
};
vi.mock("livekit-client", () => ({
  Room: function Room() { return room; },
  RoomEvent: {
    TrackSubscribed: "ts", ActiveSpeakersChanged: "as", DataReceived: "dr", Disconnected: "dc",
  },
  Track: { Kind: { Audio: "audio" }, Source: { Microphone: "microphone" } },
}));

import { ApiError, api } from "../api/http";
import { conversationsApi } from "../api/conversations";
import { useConversationStore } from "../stores/conversation";
import { useVoiceStore } from "../stores/voice";

const encode = (value: unknown) => new TextEncoder().encode(JSON.stringify(value));

describe("voice store", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useConversationStore.getState().reset();
    useConversationStore.setState({ selectedThreadId: "t1", messages: { t1: [] } });
    vi.spyOn(conversationsApi, "listThreadEvents").mockResolvedValue([]);
    vi.spyOn(conversationsApi, "listMessages").mockResolvedValue([]);
    vi.spyOn(api, "request").mockResolvedValue({ url: "wss://lk", token: "jwt", room: "r", thread_id: "t1" });
  });
  afterEach(async () => {
    await useVoiceStore.getState().stop();
    vi.restoreAllMocks();
  });

  it("connects to the selected thread's room and starts listening", async () => {
    await useVoiceStore.getState().start();
    expect(api.request).toHaveBeenCalledWith("/v1/voice/token", expect.objectContaining({ body: JSON.stringify({ thread_id: "t1" }) }));
    expect(room.connect).toHaveBeenCalledWith("wss://lk", "jwt");
    expect(room.localParticipant.setMicrophoneEnabled).toHaveBeenCalledWith(true);
    expect(useVoiceStore.getState().status).toBe("listening");
  });

  it("shows the user's spoken words as a pending chat message only", async () => {
    await useVoiceStore.getState().start();
    handlers.dr(encode({ type: "transcript", speaker: "Seleric", text: "Submitting query", is_final: true }));
    expect(useConversationStore.getState().voicePending).toBeNull();
    handlers.dr(encode({ type: "transcript", speaker: "You", text: "how are sales", is_final: false }));
    expect(useConversationStore.getState().voicePending).toBe("how are sales");
  });

  it("reports a disabled voice feature as an error", async () => {
    vi.spyOn(api, "request").mockRejectedValue(new ApiError(404, "voice is not enabled"));
    await useVoiceStore.getState().start();
    expect(useVoiceStore.getState().status).toBe("error");
    expect(useVoiceStore.getState().error).toMatch(/not enabled/);
  });

  it("disconnects when the user switches thread", async () => {
    await useVoiceStore.getState().start();
    useConversationStore.setState({ selectedThreadId: "t2" });
    await vi.waitFor(() => expect(useVoiceStore.getState().status).toBe("idle"));
    expect(room.disconnect).toHaveBeenCalled();
  });
});
