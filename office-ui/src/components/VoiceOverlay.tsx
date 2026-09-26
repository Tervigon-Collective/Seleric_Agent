import { useEffect, useRef } from "react";
import { useConversationStore } from "../stores/conversation";
import { useVoiceStore } from "../stores/voice";

/** Drives `--level` (0..1) on `target` from the loudness of the given streams. */
function useAudioLevel(target: React.RefObject<HTMLElement>, streams: Array<MediaStream | null>, muted: boolean) {
  const [mic, agent] = streams;
  useEffect(() => {
    const AudioCtx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const analysers = [mic, agent].map((stream, index) => {
      if (!stream || stream.getAudioTracks().length === 0) return null;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      ctx.createMediaStreamSource(stream).connect(analyser);
      return { analyser, data: new Uint8Array(analyser.fftSize), isMic: index === 0 };
    });
    let frame = 0;
    let smoothed = 0;
    const tick = () => {
      let peak = 0;
      for (const item of analysers) {
        if (!item || (item.isMic && muted)) continue;
        item.analyser.getByteTimeDomainData(item.data);
        let sum = 0;
        for (const v of item.data) sum += ((v - 128) / 128) ** 2;
        peak = Math.max(peak, Math.min(1, Math.sqrt(sum / item.data.length) * 4));
      }
      smoothed += (peak - smoothed) * (peak > smoothed ? 0.5 : 0.12);
      target.current?.style.setProperty("--level", smoothed.toFixed(3));
      frame = requestAnimationFrame(tick);
    };
    void ctx.resume().catch(() => undefined);
    tick();
    return () => {
      cancelAnimationFrame(frame);
      void ctx.close().catch(() => undefined);
    };
  }, [mic, agent, muted, target]);
}

export function VoiceOverlay() {
  const status = useVoiceStore((s) => s.status);
  const muted = useVoiceStore((s) => s.muted);
  const captions = useVoiceStore((s) => s.captions);
  const micStream = useVoiceStore((s) => s.micStream);
  const agentStream = useVoiceStore((s) => s.agentStream);
  const stop = useVoiceStore((s) => s.stop);
  const toggleMute = useVoiceStore((s) => s.toggleMute);
  const working = useConversationStore((s) => s.submitting);
  const progress = useConversationStore((s) => s.progress);
  const screen = useRef<HTMLDivElement>(null);
  const open = status === "connecting" || status === "listening" || status === "speaking";

  useAudioLevel(screen, [micStream, agentStream], muted);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") void stop(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, stop]);
  if (!open) return null;

  const phase = status === "connecting" ? "connecting"
    : status === "speaking" ? "speaking"
    : working ? "thinking"
    : muted ? "muted"
    : "listening";
  const label = {
    connecting: "Connecting…",
    speaking: "Speaking",
    thinking: progress ?? "Thinking…",
    muted: "Microphone muted",
    listening: "Listening",
  }[phase];

  return (
    <div ref={screen} className={`voice-screen ${phase}`} role="dialog" aria-modal="true" aria-label="Voice conversation">
      <button type="button" className="voice-close" aria-label="End voice conversation" onClick={() => void stop()}>✕</button>
      <div className="voice-stage">
        <div className="voice-orb" aria-hidden="true">
          <span className="ring r1" /><span className="ring r2" /><span className="ring r3" />
          <span className="core" />
        </div>
        <p className="voice-phase" role="status" aria-live="polite">{label}</p>
        <div className="voice-captions" aria-live="polite">
          <p className="cap-user">{captions.user}</p>
          <p className="cap-assistant">{captions.assistant}</p>
        </div>
      </div>
      <div className="voice-actions">
        <button
          type="button"
          className={`voice-action ${muted ? "on" : ""}`}
          aria-pressed={muted}
          aria-label={muted ? "Unmute microphone" : "Mute microphone"}
          onClick={() => void toggleMute()}
        >{muted ? "🔇" : "🎙"}<span>{muted ? "Unmute" : "Mute"}</span></button>
        <button type="button" className="voice-action end" aria-label="End voice conversation" onClick={() => void stop()}>
          ⏻<span>End</span>
        </button>
      </div>
      <small className="voice-hint">Just talk, interrupt any time · Esc to end</small>
    </div>
  );
}
