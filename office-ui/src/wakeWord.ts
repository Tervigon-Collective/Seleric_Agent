import { useEffect } from "react";
import { useVoiceStore } from "./stores/voice";

// "Hey" alone wakes it: recognisers often mangle "Seleric", so the name is not required.
const WAKE = /\b(?:hey|hay)\b/i;
export const isWakePhrase = (text: string) => WAKE.test(text);

interface Recognition extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start(): void;
  stop(): void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: Event) => void) | null;
}

/**
 * Listens for "Hey" (as in "Hey Seleric") while voice is idle and opens the full-screen voice agent.
 * shortcut: browser SpeechRecognition (Chrome/Edge; Brave blocks it, needs network),
 * swap for an on-device keyword model (Porcupine/openWakeWord) if that is not enough.
 */
export function useWakeWord() {
  const idle = useVoiceStore((s) => s.status === "idle");
  useEffect(() => {
    const Ctor = (window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition });
    const Recognizer = Ctor.SpeechRecognition ?? Ctor.webkitSpeechRecognition;
    if (!idle || !Recognizer) return;
    const rec = new Recognizer();
    let active = true;
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = "en-US";
    rec.onresult = (e) => {
      const heard = Array.from(e.results, (r) => r[0].transcript).join(" ");
      console.debug("[wake] heard:", heard);
      if (!active || !isWakePhrase(heard)) return;
      active = false;
      rec.stop();
      void useVoiceStore.getState().start(true);
    };
    // The browser ends recognition after silence; keep listening until voice starts.
    rec.onend = () => { if (active) try { rec.start(); } catch { /* already running */ } };
    rec.onerror = (e) => console.warn("[wake] recognition error:", (e as unknown as { error?: string }).error);
    try { rec.start(); } catch { /* mic permission denied or unavailable */ active = false; }
    return () => { active = false; rec.onend = null; rec.stop(); };
  }, [idle]);
}
