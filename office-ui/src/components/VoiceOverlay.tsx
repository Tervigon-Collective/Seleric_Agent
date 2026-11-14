import { Button } from "@/components/ui/button";
import { useEffect, useRef, useState } from "react";
import { ExpandIcon, ShrinkIcon, MicIcon, MicOffIcon, StopIcon, XIcon } from "./icons";
import { useConversationStore } from "../stores/conversation";
import { useVoiceStore } from "../stores/voice";

/** Drives `--level` (0..1) on `target` from the loudness of the given streams. */
function useAudioLevel(target: React.RefObject<HTMLElement>, streams: Array<MediaStream | null>, muted: boolean, enabled: boolean) {
  const [mic, agent] = streams;
  useEffect(() => {
    if (!enabled) return;
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
      target.current?.querySelector("signal-orb")?.setAttribute("level", smoothed.toFixed(3));
      frame = requestAnimationFrame(tick);
    };
    void ctx.resume().catch(() => undefined);
    tick();
    return () => {
      cancelAnimationFrame(frame);
      void ctx.close().catch(() => undefined);
    };
  }, [mic, agent, muted, enabled, target]);
}

/** Shared by the docked bar and the full screen: only the one matching `expanded` is live. */
function useVoiceView(expanded: boolean) {
  const status = useVoiceStore((s) => s.status);
  const muted = useVoiceStore((s) => s.muted);
  const micStream = useVoiceStore((s) => s.micStream);
  const agentStream = useVoiceStore((s) => s.agentStream);
  const isExpanded = useVoiceStore((s) => s.expanded);
  // voicePending covers the gap between the user's words and the run's first event.
  const working = useConversationStore((s) => s.submitting || s.voicePending !== null);
  const progress = useConversationStore((s) => s.progress);
  const root = useRef<HTMLDivElement>(null);
  const shown = (status === "connecting" || status === "listening" || status === "speaking") && isExpanded === expanded;

  useAudioLevel(root, [micStream, agentStream], muted, shown);
  const [finished, setFinished] = useState(false);
  const wasWorking = useRef(false);
  useEffect(() => {
    const justFinished = wasWorking.current && !working;
    wasWorking.current = working;
    if (!justFinished) return;
    setFinished(true);
    const timer = setTimeout(() => setFinished(false), 1400);
    return () => clearTimeout(timer);
  }, [working]);
  const phase = status === "connecting" ? "connecting"
    : status === "speaking" ? "speaking"
    : working ? "thinking"
    : muted ? "muted"
    : "listening";
  // The orb has no "searching" here: the run progress is free text, not a structured phase.
  const orb = phase === "connecting" || phase === "thinking" ? "thinking"
    : phase === "speaking" ? "speaking"
    : finished && !muted ? "done"
    : "listening";
  const label = {
    connecting: "Connecting…",
    speaking: "Speaking",
    thinking: progress ?? "Thinking…",
    muted: "Microphone muted",
    listening: "Listening",
  }[phase];
  return { root, shown, phase, orb, label, muted };
}

function Orb({ state }: { state: string }) {
  return <div className="voice-orb" aria-hidden="true"><signal-orb state={state} particles="4000" /></div>;
}

/** Docked bar: replaces the composer while voice is live, chat stays visible above it. */
export function VoiceDock() {
  const captions = useVoiceStore((s) => s.captions);
  const stop = useVoiceStore((s) => s.stop);
  const toggleMute = useVoiceStore((s) => s.toggleMute);
  const setExpanded = useVoiceStore((s) => s.setExpanded);
  const { root, shown, phase, orb, label, muted } = useVoiceView(false);
  const reading = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = reading.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [captions, shown]);
  if (!shown) return null;

  return (
    <div ref={root} className={`voice-dock ${phase}`} role="region" aria-label="Voice conversation">
      {captions.user && <p className="m-0 truncate px-3 pt-3 text-xs text-muted-foreground" title={captions.user}>{captions.user}</p>}
      <div ref={reading} className="max-h-32 overflow-y-auto px-3 pt-2 text-sm" aria-live="polite">
        {captions.assistant
          ? <p className="m-0">{captions.assistant}</p>
          : !captions.user && <p className="m-0 text-muted-foreground">Just talk — interrupt any time</p>}
      </div>
      <div className="flex items-center gap-2 p-3">
        <Orb state={orb} />
        <span className="flex-1 text-sm text-muted-foreground" role="status" aria-live="polite">{label}</span>
        <Button type="button" variant={muted ? "secondary" : "ghost"} size="icon-sm" aria-pressed={muted}
          aria-label={muted ? "Unmute microphone" : "Mute microphone"} title={muted ? "Unmute" : "Mute"}
          onClick={() => void toggleMute()}>{muted ? <MicOffIcon /> : <MicIcon />}</Button>
        <Button type="button" variant="ghost" size="icon-sm" aria-label="Expand to full screen" title="Full screen"
          onClick={() => setExpanded(true)}><ExpandIcon /></Button>
        <Button type="button" variant="destructive" size="sm" aria-label="End voice conversation"
          onClick={() => void stop()}><StopIcon /><span>End</span></Button>
      </div>
    </div>
  );
}

/** Full-screen takeover, reached from the dock's expand button. */
export function VoiceOverlay() {
  const captions = useVoiceStore((s) => s.captions);
  const stop = useVoiceStore((s) => s.stop);
  const toggleMute = useVoiceStore((s) => s.toggleMute);
  const setExpanded = useVoiceStore((s) => s.setExpanded);
  const { root: screen, shown: open, phase, orb, label, muted } = useVoiceView(true);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") void stop(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, stop]);
  if (!open) return null;

  return (
    <div ref={screen} className={`voice-screen ${phase}`} role="dialog" aria-modal="true" aria-label="Voice conversation">
      <div className="flex items-stretch justify-between self-stretch">
        <Button type="button" variant="ghost" size="icon" aria-label="Collapse to bar" title="Collapse" onClick={() => setExpanded(false)}><ShrinkIcon /></Button>
        <Button type="button" variant="ghost" size="icon" aria-label="End voice conversation" onClick={() => void stop()}><XIcon /></Button>
      </div>
      <div className="voice-stage">
        <Orb state={orb} />
        <p className="m-0 text-lg" role="status" aria-live="polite">{label}</p>
        <div className="flex flex-col gap-2 text-center" aria-live="polite">
          <p className="m-0 text-sm text-muted-foreground">{captions.user}</p>
          <p className="m-0 text-base">{captions.assistant}</p>
        </div>
      </div>
      <div className="flex gap-7">
        <Button
          type="button"
          variant={muted ? "default" : "outline"}
          className="size-16 flex-col gap-1 rounded-full text-[11px]"
          aria-pressed={muted}
          aria-label={muted ? "Unmute microphone" : "Mute microphone"}
          onClick={() => void toggleMute()}
        >{muted ? <MicOffIcon size={22} /> : <MicIcon size={22} />}<span>{muted ? "Unmute" : "Mute"}</span></Button>
        <Button type="button" variant="destructive" className="size-16 flex-col gap-1 rounded-full text-[11px]" aria-label="End voice conversation" onClick={() => void stop()}>
          <StopIcon size={20} /><span>End</span>
        </Button>
      </div>
      <small className="text-xs text-muted-foreground">Just talk, interrupt any time · Esc to end</small>
    </div>
  );
}
