import {
  AuiIf,
  AttachmentPrimitive,
  ComposerPrimitive,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "cn";
import { useVoiceStore, type VoiceStatus } from "../stores/voice";
import { useEffect, useMemo, useRef, useState } from "react";
import { enhanceQuery } from "../api/queryEnhance";
import { attachAutoCorrect, type AutoCorrectItem } from "../utils/autocorrect";
import { VoiceDock } from "./VoiceOverlay";
import { MicIcon, PaperclipIcon, PromptsIcon, SendIcon, SparklesIcon, StopIcon } from "./icons";

const VOICE_LABEL: Record<VoiceStatus, string> = {
  idle: "Talk to Seleric",
  connecting: "Connecting voice…",
  listening: "Voice conversation active",
  speaking: "Voice conversation active",
  error: "Voice unavailable — click to retry",
};

function focusComposer() {
  const input = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message"]');
  if (!input) return;
  input.focus();
  const end = input.value.length;
  input.setSelectionRange(end, end);
}

function EnhanceButton({ text }: { text: string }) {
  const aui = useAui();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const empty = !text.trim();

  const onEnhance = async () => {
    if (empty || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await enhanceQuery(text);
      const next = (result.enhanced || result.original).trim();
      if (next) {
        aui.composer.setText(next);
        requestAnimationFrame(() => focusComposer());
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unable to enhance query";
      setError(message);
      window.setTimeout(() => setError((cur) => (cur === message ? null : cur)), 4000);
    } finally {
      setBusy(false);
    }
  };

  const label = busy ? "Enhancing…" : empty ? "Type a question to enhance" : "Enhance query";
  return (
    <>
      {error && <span className="max-w-48 truncate text-xs text-destructive" role="alert">{error}</span>}
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={label}
        title={label}
        disabled={empty || busy}
        onClick={() => void onEnhance()}
      >
        {busy ? <Spinner /> : <SparklesIcon />}
      </Button>
    </>
  );
}

function VoiceControls({ text }: { text: string }) {
  const status = useVoiceStore((s) => s.status);
  const error = useVoiceStore((s) => s.error);
  const start = useVoiceStore((s) => s.start);
  const live = status === "listening" || status === "speaking";
  return (
    <div className="flex items-center gap-1">
      {status === "error" && error && <span className="max-w-48 truncate text-xs text-destructive" role="alert">{error}</span>}
      <EnhanceButton text={text} />
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label={VOICE_LABEL[status]}
        title={VOICE_LABEL[status]}
        disabled={status === "connecting" || live}
        onClick={() => void start()}
      ><MicIcon /></Button>
    </div>
  );
}

function renderBackdrop(text: string, corrections: AutoCorrectItem[]) {
  if (!text) return null;

  const valid = corrections
    .filter((c) => text.slice(c.start, c.end) === c.corrected)
    .sort((a, b) => a.start - b.start);

  if (valid.length === 0) {
    return <span>{text}</span>;
  }

  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;

  valid.forEach((item, idx) => {
    if (item.start > lastIndex) {
      nodes.push(
        <span key={`text-${idx}-${lastIndex}`}>
          {text.slice(lastIndex, item.start)}
        </span>
      );
    }
    nodes.push(
      <mark
        key={`correct-${item.id || idx}`}
        className="autocorrect-highlight rounded-sm bg-accent text-transparent"
        data-original={item.original}
        title={`Auto-corrected from "${item.original}"`}
      >
        {item.corrected}
      </mark>
    );
    lastIndex = item.end;
  });

  if (lastIndex < text.length) {
    nodes.push(
      <span key={`text-tail-${lastIndex}`}>
        {text.slice(lastIndex)}
      </span>
    );
  }

  if (text.endsWith("\n")) {
    nodes.push(<br key="trailing-br" />);
  }

  return nodes;
}

/** Shared by the textarea and its autocorrect overlay so the highlights line up with the text. */
const FIELD = "w-full px-1 py-1 font-[inherit] text-base leading-6 md:text-sm";

export function Composer() {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const [corrections, setCorrections] = useState<AutoCorrectItem[]>([]);
  const text = useAuiState((s) => (s.composer.isEditing ? s.composer.text : ""));
  const voiceLive = useVoiceStore((s) => s.status === "connecting" || s.status === "listening" || s.status === "speaking");

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;

    const handleAutoCorrect = (e: Event) => {
      const item = (e as CustomEvent<AutoCorrectItem>).detail;
      if (!item) return;
      setCorrections((prev) => {
        const filtered = prev.filter(
          (c) => !(c.start === item.start && c.end === item.end)
        );
        return [...filtered, item];
      });

      setTimeout(() => {
        setCorrections((prev) => prev.filter((c) => c.id !== item.id));
      }, 1500);
    };

    const handleAutoCorrectUndo = (e: Event) => {
      const item = (e as CustomEvent<AutoCorrectItem>).detail;
      if (!item) return;
      setCorrections((prev) => prev.filter((c) => c.id !== item.id));
    };

    const handleScroll = () => {
      if (backdropRef.current) {
        backdropRef.current.scrollTop = el.scrollTop;
      }
    };

    el.addEventListener("autocorrect", handleAutoCorrect);
    el.addEventListener("autocorrect-undo", handleAutoCorrectUndo);
    el.addEventListener("scroll", handleScroll);

    const detach = attachAutoCorrect(el);

    return () => {
      detach();
      el.removeEventListener("autocorrect", handleAutoCorrect);
      el.removeEventListener("autocorrect-undo", handleAutoCorrectUndo);
      el.removeEventListener("scroll", handleScroll);
    };
  }, []);

  const activeCorrections = useMemo(() => {
    if (!text) return [];
    return corrections.filter(
      (c) => text.slice(c.start, c.end) === c.corrected
    );
  }, [corrections, text]);

  return (
    <div className="shrink-0 bg-background px-6 pb-4 max-md:px-3 max-md:pb-[max(12px,env(safe-area-inset-bottom))]">
      <VoiceDock />
      {/* Hidden, not unmounted: keeps the draft and the autocorrect listeners alive. */}
      <div className="mx-auto max-w-[880px]" hidden={voiceLive}>
        <ComposerPrimitive.Root aria-label="Message composer" className="flex flex-col gap-1 rounded-lg border bg-background p-2 transition-shadow focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50">
          <div className="relative">
            <div className={cn(FIELD, "pointer-events-none absolute inset-0 overflow-hidden break-words whitespace-pre-wrap text-transparent")} aria-hidden="true" ref={backdropRef}>
              {renderBackdrop(text, activeCorrections)}
            </div>
            <ComposerPrimitive.Input
              ref={inputRef}
              placeholder="Ask about sales, marketing, products…"
              aria-label="Message"
              rows={2}
              className={cn(FIELD, "relative block max-h-36 min-h-11 resize-none border-0 bg-transparent outline-none placeholder:text-muted-foreground")}
            />
          </div>
          <div className="flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="Open prompt registry"
              title="Prompts"
              onClick={() => window.dispatchEvent(new Event("seleric:open-prompts"))}
            >
              <PromptsIcon />
            </Button>
            <ComposerPrimitive.AddAttachment className={buttonVariants({ variant: "ghost", size: "icon-sm" })} aria-label="Choose attachments" title="Attach a file">
              <PaperclipIcon />
            </ComposerPrimitive.AddAttachment>
            <small className="ml-1 min-w-0 truncate text-xs text-muted-foreground max-md:hidden">Enter to send · Shift+Enter for a new line</small>
            <span className="flex-1" />
            <VoiceControls text={text} />
            <AuiIf condition={(state) => !state.thread.isRunning}>
              <ComposerPrimitive.Send className={buttonVariants({ size: "icon-sm" })} aria-label="Send message" title="Send (Enter)">
                <SendIcon />
              </ComposerPrimitive.Send>
            </AuiIf>
            <AuiIf condition={(state) => state.thread.isRunning}>
              <ComposerPrimitive.Cancel className={buttonVariants({ variant: "destructive", size: "icon-sm" })} aria-label="Cancel run" title="Stop generating">
                <StopIcon />
              </ComposerPrimitive.Cancel>
            </AuiIf>
          </div>
          <div className="flex flex-wrap gap-1 empty:hidden" aria-live="polite">
            <ComposerPrimitive.Attachments>
              {({ attachment }) => (
                <AttachmentPrimitive.Root className="inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs">
                  <AttachmentPrimitive.Name />
                  <span> · {attachment.status.type}</span>
                  <AttachmentPrimitive.Remove className={buttonVariants({ variant: "ghost", size: "icon-xs" })} aria-label={`Remove ${attachment.name}`}>×</AttachmentPrimitive.Remove>
                </AttachmentPrimitive.Root>
              )}
            </ComposerPrimitive.Attachments>
          </div>
        </ComposerPrimitive.Root>
      </div>
    </div>
  );
}
