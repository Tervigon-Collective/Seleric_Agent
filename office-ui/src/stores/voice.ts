import { create } from "zustand";
import { Room, RoomEvent, Track } from "livekit-client";
import { mintVoiceToken } from "../api/voice";
import { useConversationStore } from "./conversation";

export type VoiceStatus = "idle" | "connecting" | "listening" | "speaking" | "error";

interface VoiceState {
  status: VoiceStatus;
  muted: boolean;
  error: string | null;
  /** Live captions for the voice screen: what you said, and what Seleric is saying. */
  captions: { user: string; assistant: string };
  /** Audio streams the voice screen analyses to animate the orb. */
  micStream: MediaStream | null;
  agentStream: MediaStream | null;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  toggleMute: () => Promise<void>;
}

let room: Room | null = null;
let audioEls: HTMLMediaElement[] = [];
let attempt = 0;

const isAgent = (identity: string) => identity.startsWith("agent-");

function detachAudio() {
  audioEls.forEach((el) => el.remove());
  audioEls = [];
}

export const useVoiceStore = create<VoiceState>((set, get) => ({
  status: "idle",
  muted: false,
  error: null,
  captions: { user: "", assistant: "" },
  micStream: null,
  agentStream: null,

  start: async () => {
    if (get().status !== "idle" && get().status !== "error") return;
    const generation = ++attempt;
    set({ status: "connecting", muted: false, error: null, captions: { user: "", assistant: "" } });
    const conversation = useConversationStore.getState();
    try {
      if (!conversation.selectedThreadId) await conversation.createThread();
      const threadId = useConversationStore.getState().selectedThreadId;
      if (!threadId) throw new Error("Unable to start a conversation for voice");
      const { url, token } = await mintVoiceToken(threadId);
      if (generation !== attempt) return;

      const next = new Room({ adaptiveStream: true, dynacast: true });
      next
        .on(RoomEvent.TrackSubscribed, (track) => {
          if (track.kind !== Track.Kind.Audio) return;
          set({ agentStream: track.mediaStream ?? null });
          const el = track.attach();
          el.style.display = "none";
          document.body.appendChild(el);
          audioEls.push(el);
        })
        .on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
          if (get().status === "connecting" || get().status === "error") return;
          set({ status: speakers.some((p) => isAgent(p.identity)) ? "speaking" : "listening" });
        })
        .on(RoomEvent.DataReceived, (payload) => {
          try {
            const data = JSON.parse(new TextDecoder().decode(payload)) as {
              type?: string; speaker?: string; text?: string;
            };
            if (data.type !== "transcript" || !data.text) return;
            const text = data.text;
            // The user's words also become a pending chat bubble; Seleric's
            // side reaches the chat as the persisted answer via the thread stream.
            if (data.speaker === "You") {
              useConversationStore.getState().setVoicePending(text);
              set((s) => ({ captions: { user: text, assistant: s.captions.user === text ? s.captions.assistant : "" } }));
            } else if (data.speaker === "Seleric") {
              set((s) => ({ captions: { ...s.captions, assistant: text } }));
            }
          } catch { /* ignore non-JSON data messages */ }
        })
        .on(RoomEvent.Disconnected, () => {
          if (room !== next) return;
          room = null;
          detachAudio();
          useConversationStore.getState().stopFollowingThread();
          set((s) => ({ status: s.status === "error" ? "error" : "idle", muted: false, micStream: null, agentStream: null }));
        });
      room = next;
      await next.connect(url, token);
      if (generation !== attempt || useConversationStore.getState().selectedThreadId !== threadId) {
        await next.disconnect();
        if (generation === attempt) set({ status: "idle" });
        return;
      }
      await next.localParticipant.setMicrophoneEnabled(true);
      set({ micStream: next.localParticipant.getTrackPublication(Track.Source.Microphone)?.track?.mediaStream ?? null });
      await useConversationStore.getState().followThread(threadId);
      set({ status: "listening" });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to start voice";
      const active = room;
      room = null;
      detachAudio();
      if (active) await active.disconnect().catch(() => undefined);
      if (generation === attempt) set({ status: "error", error: message });
    }
  },

  stop: async () => {
    attempt += 1;
    const active = room;
    room = null;
    detachAudio();
    useConversationStore.getState().stopFollowingThread();
    set({ status: "idle", muted: false, error: null, micStream: null, agentStream: null, captions: { user: "", assistant: "" } });
    if (active) await active.disconnect().catch(() => undefined);
  },

  toggleMute: async () => {
    if (!room) return;
    const muted = !get().muted;
    await room.localParticipant.setMicrophoneEnabled(!muted);
    set({ muted });
  },
}));

// One room per thread: leave voice when the user moves to another thread.
let lastThreadId = useConversationStore.getState().selectedThreadId;
useConversationStore.subscribe((state) => {
  if (state.selectedThreadId === lastThreadId) return;
  lastThreadId = state.selectedThreadId;
  const { status } = useVoiceStore.getState();
  if (status === "listening" || status === "speaking") void useVoiceStore.getState().stop();
});
