import {
  ActionBarPrimitive,
  AuiIf,
  MessagePrimitive,
  ThreadPrimitive,
  type DataMessagePartProps,
} from "@assistant-ui/react";
import { useRef, useEffect, useState } from "react";
import type { MessagePart } from "../api/contracts";
import { useConversationStore } from "../stores/conversation";
import { useShellStore } from "../stores/shell";
import { ArrowDownIcon, CheckIcon, CopyIcon } from "./icons";
import { MessagePartRenderer } from "./MessagePartRenderer";
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

const parts = {
  Text: ({ text }: { text: string }) => <SafeContent text={text} />,
  data: {
    by_name: {
      "seleric-part": SelericPart,
      "seleric-sources": SelericSources,
      "seleric-response-time": SelericResponseTime,
    },
  },
};

const SUGGESTION_GROUPS: { label: string; questions: string[] }[] = [
  { label: "Marketing", questions: [
    "Which campaigns contributed the most revenue last week?",
    "Why did ROAS decline compared with last week?",
  ] },
  { label: "Sales & revenue", questions: [
    "Where did our sales come from yesterday?",
    "Compare marketing spend, revenue, and profitability.",
  ] },
  { label: "Products & conversion", questions: [
    "Which products are losing money after ads and returns?",
    "What changed in checkout conversion over the last 7 days?",
  ] },
];

function SuggestionList() {
  const submit = useConversationStore((s) => s.submit);
  return (
    <div role="list" aria-label="Suggested starting questions" style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 560 }}>
      {SUGGESTION_GROUPS.map((group) => (
        <div key={group.label} role="listitem">
          <p style={{ margin: "0 0 8px", fontSize: 11, fontWeight: 650, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-faint)" }}>
            {group.label}
          </p>
          <div className="empty-suggestions" style={{ justifyContent: "center" }}>
            {group.questions.map((question) => (
              <button key={question} onClick={() => void submit(question)}>
                {question}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

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
  const openInspector = useShellStore((state) => state.toggleDetails);
  const detailsOpen = useShellStore((state) => state.detailsOpen);
  return (
    <div className="run-status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{progress ?? "Working on it…"}</span>
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
  return (
    <div className="thread-context" style={{ marginBottom: 20 }}>
      <h2 style={{ margin: 0, fontSize: 22, fontWeight: 650, letterSpacing: "-0.02em" }}>
        {thread.title || "Untitled conversation"}
      </h2>
      {count > 0 && (
        <p style={{ margin: "4px 0 0", fontSize: 12.5, color: "var(--text-dim)" }}>
          {Math.ceil(count / 2)} exchange{Math.ceil(count / 2) === 1 ? "" : "s"} · evidence stays attached below
        </p>
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
              <SuggestionList />
            </div>
          )}
          {threadId && (
            <ThreadPrimitive.Empty>
              <div className="empty-state">
                <h1>What should we investigate?</h1>
                <p>Ask a question about your metrics and Seleric will investigate.</p>
                <SuggestionList />
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
