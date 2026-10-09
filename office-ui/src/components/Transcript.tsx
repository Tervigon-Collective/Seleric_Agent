import {
  ActionBarPrimitive,
  AuiIf,
  MessagePrimitive,
  ThreadPrimitive,
  type DataMessagePartProps,
} from "@assistant-ui/react";
import { useRef, useEffect, useMemo, useState } from "react";
import type { MessagePart } from "../api/contracts";
import { useOffice } from "../store";
import { useConversationStore } from "../stores/conversation";
import { useShellStore } from "../stores/shell";
import { deriveRunPhases } from "../api/runPhases";
import { ArrowDownIcon, CheckIcon, CopyIcon, ThumbDownIcon, ThumbUpIcon } from "./icons";
import { conversationsApi } from "../api/conversations";
import { MessagePartRenderer } from "./MessagePartRenderer";
import { PromptRegistry } from "./PromptRegistry";
import { SafeContent } from "./SafeContent";

function SelericPart({ data }: DataMessagePartProps) {
  return <MessagePartRenderer part={data} />;
}

function SelericSources({ data }: DataMessagePartProps) {
  const sources = data as MessagePart[];
  return (
    <details className="message-sources">
      <summary>Sources ({sources.length})</summary>
      <div className="message-sources-list">
        {sources.map((part, index) => <MessagePartRenderer key={index} part={part} />)}
      </div>
    </details>
  );
}

export function formatResponseTime(ms: number): string {
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function SelericResponseTime({ data }: DataMessagePartProps) {
  const { elapsedMs } = data as { elapsedMs: number };
  return <p className="message-response-time">Responded in {formatResponseTime(elapsedMs)}</p>;
}

type FeedbackData = { messageId: string; threadId: string; runId: string | null; final: boolean };

export function SelericFeedback({ data }: DataMessagePartProps) {
  const { messageId, threadId, runId, final } = data as FeedbackData;
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!final) return;
    let alive = true;
    conversationsApi.getFeedback(threadId, messageId)
      .then((existing) => { if (alive && existing) setVote(existing.rating); })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [final, threadId, messageId]);

  // Draft/streaming messages carry a transient id — no voting until final.
  if (!final) return null;

  const cast = (rating: "up" | "down", text?: string) => {
    setSaving(true);
    setFailed(false);
    conversationsApi.submitFeedback(threadId, messageId, { rating, note: text ?? "", runId: runId ?? undefined })
      .then(() => {
        setVote(rating);
        if (rating === "up" || text !== undefined) setNoteOpen(false);
      })
      .catch(() => setFailed(true))
      .finally(() => setSaving(false));
  };

  return (
    <div className="message-feedback">
      <span className="feedback-prompt">Was this helpful?</span>
      <button
        type="button"
        className={vote === "up" ? "active" : ""}
        aria-label="Mark answer helpful"
        aria-pressed={vote === "up"}
        disabled={saving}
        onClick={() => cast("up")}
      ><ThumbUpIcon size={13} /></button>
      <button
        type="button"
        className={vote === "down" ? "active" : ""}
        aria-label="Report a problem with this answer"
        aria-pressed={vote === "down"}
        disabled={saving}
        onClick={() => (vote === "down" ? setNoteOpen((open) => !open) : cast("down"))}
      ><ThumbDownIcon size={13} /></button>
      {vote === "down" && !noteOpen && (
        <button type="button" className="feedback-note-toggle" onClick={() => setNoteOpen(true)}>
          Add detail
        </button>
      )}
      {failed && <span className="feedback-error" role="alert">Couldn’t save — retry.</span>}
      {noteOpen && (
        <form
          className="feedback-note"
          onSubmit={(event) => {
            event.preventDefault();
            cast("down", note.trim());
          }}
        >
          <label className="sr-only" htmlFor={`feedback-note-${messageId}`}>What was wrong?</label>
          <input
            id={`feedback-note-${messageId}`}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="What was wrong? (optional)"
            maxLength={2000}
          />
          <button type="submit" disabled={saving}>Send</button>
        </form>
      )}
    </div>
  );
}

const parts = {
  Text: ({ text }: { text: string }) => <SafeContent text={text} />,
  data: {
    by_name: {
      "seleric-part": SelericPart,
      "seleric-sources": SelericSources,
      "seleric-response-time": SelericResponseTime,
      "seleric-feedback": SelericFeedback,
    },
  },
};

function CopyButton() {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  return (
    <button
      type="button"
      className={copied ? "copied" : ""}
      aria-label={copied ? "Copied" : "Copy response"}
      title="Copy response"
      onClick={(event) => {
        const root = (event.currentTarget as HTMLElement).closest(".message");
        const text = root?.querySelector(".message-body")?.textContent?.trim() ?? "";
        if (!text) return;
        void navigator.clipboard?.writeText(text).then(() => {
          setCopied(true);
          if (timer.current) window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => setCopied(false), 1600);
        }).catch(() => undefined);
      }}
    >
      {copied ? <CheckIcon size={12} /> : <CopyIcon size={12} />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function DetailsButton() {
  const openInspector = useShellStore((s) => s.openInspector);
  return (
    <button type="button" aria-label="Open evidence inspector" title="Evidence and run details" onClick={() => openInspector("Evidence")}>
      Details
    </button>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="message user" aria-label="user message">
      <div className="message-body">
        <div className="message-meta">You</div>
        <MessagePrimitive.Parts components={parts} />
      </div>
    </MessagePrimitive.Root>
  );
}

function RunningLine() {
  const progress = useConversationStore((state) => state.progress);
  const cancelRun = useConversationStore((state) => state.cancelRun);
  const timeline = useOffice((state) => state.timeline);
  const openInspector = useShellStore((state) => state.toggleDetails);
  const detailsOpen = useShellStore((state) => state.detailsOpen);
  const phases = useMemo(() => deriveRunPhases(timeline), [timeline]);
  return (
    <div className="run-status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <div className="run-status-body">
        <span>{progress ?? "Working on it…"}</span>
        <ol className="run-phases" aria-label="Run progress">
          {phases.map((phase) => (
            <li
              key={phase.key}
              className={`run-phase ${phase.state}`}
              aria-current={phase.state === "active" ? "step" : undefined}
            >
              <span className="run-phase-dot" aria-hidden="true" />
              <span className="run-phase-label">{phase.label}</span>
            </li>
          ))}
        </ol>
      </div>
      <button type="button" className="view-activity" onClick={() => { if (!detailsOpen) openInspector(); else useShellStore.getState().setDetailTab("Activity"); }}>
        View activity
      </button>
      <button type="button" onClick={() => void cancelRun()}>Stop</button>
    </div>
  );
}

/** Streams the model's reasoning live, in a collapsed-by-default block. */
function ThinkingBubble() {
  const thinkingText = useConversationStore((state) => state.thinkingText);
  const submitting = useConversationStore((state) => state.submitting);
  const bodyRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [thinkingText]);

  if (!thinkingText) return null;

  return (
    <details className="thinking-bubble" open={submitting} aria-label="Model thinking process">
      <summary className="thinking-bubble-summary">
        {submitting && <span className="pulse-dot" aria-hidden="true" />}
        {submitting ? "Thinking…" : "Thought process"}
      </summary>
      <pre
        ref={bodyRef}
        className="thinking-bubble-body"
        aria-live="polite"
        aria-label="Streaming thinking text"
      >
        {thinkingText}
      </pre>
    </details>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="message assistant" aria-label="assistant message">
      <div className="message-body">
        <header className="assistant-head"><span className="assistant-mark" aria-hidden="true">S</span> Seleric</header>
        <ThinkingBubble />
        <MessagePrimitive.Parts components={parts} />
        <AuiIf condition={(state) => state.thread.isRunning}>
          <MessagePrimitive.If hasContent={false}>
            <RunningLine />
          </MessagePrimitive.If>
        </AuiIf>
        <AuiIf condition={(state) => !state.thread.isRunning}>
          <ActionBarPrimitive.Root className="message-actions">
            <CopyButton />
            <DetailsButton />
            <ActionBarPrimitive.Reload aria-label="Retry response">Retry</ActionBarPrimitive.Reload>
          </ActionBarPrimitive.Root>
        </AuiIf>
      </div>
    </MessagePrimitive.Root>
  );
}

function SystemMessage() {
  return (
    <MessagePrimitive.Root className="message system" aria-label="system message">
      <div className="message-body"><MessagePrimitive.Parts components={parts} /></div>
    </MessagePrimitive.Root>
  );
}

function VoicePendingMessage() {
  const text = useConversationStore((state) => state.voicePending);
  if (!text) return null;
  return (
    <div className="message user pending" aria-label="user message (speaking)" aria-live="polite">
      <div className="message-body"><div className="message-meta">You · voice</div><p style={{ margin: 0 }}>{text}</p></div>
    </div>
  );
}

function ThreadContextBar() {
  const threadId = useConversationStore((s) => s.selectedThreadId);
  const thread = useConversationStore((s) => s.threads.find((item) => item.id === threadId));
  const count = useConversationStore((s) => (threadId ? s.messages[threadId]?.length ?? 0 : 0));
  if (!threadId || !thread) return null;
  // Compact context line: the question itself lives in the user bubble and the
  // compact title in the header — no large duplicate heading here.
  const exchanges = Math.ceil(count / 2);
  return (
    <div className="thread-context">
      <span className="thread-context-name" title={thread.title || "Untitled conversation"}>
        {thread.title || "Untitled conversation"}
      </span>
      {count > 0 && (
        <span className="thread-context-meta">
          {exchanges} exchange{exchanges === 1 ? "" : "s"} · evidence stays attached below
        </span>
      )}
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div aria-label="Loading conversation" role="status">
      <div className="skeleton-block skeleton-title" />
      <div className="skeleton-block skeleton-line" style={{ width: "38%" }} />
      <div className="skeleton-block skeleton-user" />
      <div className="skeleton-block skeleton-line" style={{ width: "92%" }} />
      <div className="skeleton-block skeleton-line" style={{ width: "78%" }} />
      <div className="skeleton-block skeleton-table" />
      <div className="skeleton-block skeleton-line" style={{ width: "64%" }} />
    </div>
  );
}

export function Transcript() {
  const threadId = useConversationStore((state) => state.selectedThreadId);
  const loading = useConversationStore((state) => state.loading);
  const cachedCount = useConversationStore((state) => (threadId ? state.messages[threadId]?.length ?? 0 : 0));
  const showSkeleton = loading && !!threadId && cachedCount === 0;
  return (
    <ThreadPrimitive.Root className="thread-root">
      <ThreadPrimitive.Viewport className="transcript" aria-label="Conversation transcript" aria-busy={loading}>
        {/* Key re-triggers the view-enter fade so browsing conversations feels fluid. */}
        <div className="transcript-inner view-enter" key={threadId ?? "empty"}>
          {showSkeleton && <LoadingSkeleton />}
          {!threadId && (
            <div className="empty-state">
              <h1>Where should we begin?</h1>
              <p>Ask about marketing, sales, products, or operations — Seleric investigates your metrics and shows its evidence.</p>
              <PromptRegistry />
            </div>
          )}
          {threadId && (
            <ThreadPrimitive.Empty>
              <div className="empty-state">
                <h1>What should we investigate?</h1>
                <p>Ask a question about your metrics and Seleric will investigate.</p>
                <PromptRegistry />
              </div>
            </ThreadPrimitive.Empty>
          )}
          {threadId && <ThreadContextBar />}
          <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage, SystemMessage }} />
          <VoicePendingMessage />
        </div>
        <ThreadPrimitive.ScrollToBottom aria-label="Scroll to latest message" className="scroll-latest">
          <ArrowDownIcon size={14} />
        </ThreadPrimitive.ScrollToBottom>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
