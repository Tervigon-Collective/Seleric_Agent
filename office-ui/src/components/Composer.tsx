import {
  AuiIf,
  AttachmentPrimitive,
  ComposerPrimitive,
  useAuiState,
} from "@assistant-ui/react";
import { useVoiceStore, type VoiceStatus } from "../stores/voice";
import { useEffect, useMemo, useRef, useState } from "react";
import { attachAutoCorrect, type AutoCorrectItem } from "../utils/autocorrect";
import { MicIcon, PaperclipIcon, SendIcon, StopIcon } from "./icons";

const VOICE_LABEL: Record<VoiceStatus, string> = {
  idle: "Talk to Seleric",
  connecting: "Connecting voice…",
  listening: "Voice conversation active",
  speaking: "Voice conversation active",
  error: "Voice unavailable — click to retry",
};

function VoiceControls() {
  const status = useVoiceStore((s) => s.status);
  const error = useVoiceStore((s) => s.error);
  const start = useVoiceStore((s) => s.start);
  const live = status === "listening" || status === "speaking";
  return (
    <div className="voice-controls">
      {status === "error" && error && <span className="voice-status" role="alert">{error}</span>}
      <button
        type="button"
        className={`voice-btn ${status}`}
        aria-label={VOICE_LABEL[status]}
        title={VOICE_LABEL[status]}
        disabled={status === "connecting" || live}
        onClick={() => void start()}
      ><MicIcon size={15} /></button>
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
        className="autocorrect-highlight"
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

export function Composer() {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const [corrections, setCorrections] = useState<AutoCorrectItem[]>([]);
  const text = useAuiState((s) => (s.composer.isEditing ? s.composer.text : ""));

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
    <div className="composer-wrap">
      <div className="composer-inner">
        <ComposerPrimitive.Root className="composer" aria-label="Message composer">
          <ComposerPrimitive.AddAttachment className="attach-btn" aria-label="Choose attachments" title="Attach a file">
            <PaperclipIcon size={16} />
          </ComposerPrimitive.AddAttachment>
          <div className="composer-input-wrapper">
            <div className="composer-backdrop" aria-hidden="true" ref={backdropRef}>
              {renderBackdrop(text, activeCorrections)}
            </div>
            <ComposerPrimitive.Input
              ref={inputRef}
              placeholder="Ask about sales, marketing, products…"
              aria-label="Message"
              rows={2}
            />
          </div>
          <AuiIf condition={(state) => !state.thread.isRunning}>
            <ComposerPrimitive.Send className="send-btn" aria-label="Send message" title="Send (Enter)">
              <SendIcon size={15} />
            </ComposerPrimitive.Send>
          </AuiIf>
          <AuiIf condition={(state) => state.thread.isRunning}>
            <ComposerPrimitive.Cancel className="send-btn cancel-btn" aria-label="Cancel run" title="Stop generating">
              <StopIcon size={13} />
            </ComposerPrimitive.Cancel>
          </AuiIf>
          <div className="upload-list" aria-live="polite">
            <ComposerPrimitive.Attachments>
              {({ attachment }) => (
                <AttachmentPrimitive.Root>
                  <AttachmentPrimitive.Name />
                  <span> · {attachment.status.type}</span>
                  <AttachmentPrimitive.Remove aria-label={`Remove ${attachment.name}`}>×</AttachmentPrimitive.Remove>
                </AttachmentPrimitive.Root>
              )}
            </ComposerPrimitive.Attachments>
          </div>
          <VoiceControls />
          <small className="hint">Enter to send · Shift+Enter for a new line</small>
        </ComposerPrimitive.Root>
      </div>
    </div>
  );
}
