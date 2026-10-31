import { Skeleton } from "@/components/ui/skeleton";
import {
  ActionBarPrimitive,
  AuiIf,
  MessagePrimitive,
  ThreadPrimitive,
  useAui,
  useAuiState,
  type DataMessagePartProps,
} from "@assistant-ui/react";
import { useRef, useEffect, useMemo, useState } from "react";
import { GlobeIcon } from "lucide-react";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Button, buttonVariants } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "cn";
import { Message, MessageContent, MessageHeader } from "@/components/ui/message";
import { Spinner } from "@/components/ui/spinner";
import { Trace, TraceCollapsible, TraceCollapsibleContent, TraceCollapsibleTrigger, TraceIcon, TraceLine, TraceTitle } from "@/components/ui/trace";
import type { MessagePart } from "../api/contracts";
import { useOffice } from "../store";
import { useConversationStore } from "../stores/conversation";
import { useShellStore } from "../stores/shell";
import { deriveRunPhases } from "../api/runPhases";
import { ArrowDownIcon, ArrowRightIcon, CheckIcon, CopyIcon, ThumbDownIcon, ThumbUpIcon } from "./icons";
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
    <Trace data-part="message-sources">
      <TraceCollapsible>
        <TraceCollapsibleTrigger>
          <TraceLine>
            <TraceIcon><GlobeIcon /></TraceIcon>
            <TraceTitle>Used {sources.length} {sources.length === 1 ? "source" : "sources"}</TraceTitle>
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          {sources.map((part, index) => <MessagePartRenderer key={index} part={part} />)}
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  );
}

export function SelericContext({ data }: DataMessagePartProps) {
  const text = (data as { text?: unknown }).text;
  if (typeof text !== "string" || !text.trim()) return null;
  return <p data-slot="message-context" className="mt-2 text-xs tabular-nums text-muted-foreground">{text}</p>;
}

export function SelericNext({ data }: DataMessagePartProps) {
  const text = (data as { text?: unknown }).text;
  const aui = useAui();
  const running = useAuiState((s) => s.thread.isRunning);
  const [previous, setPrevious] = useState<string | null>(null);
  if (typeof text !== "string" || !text.trim()) return null;
  // Suggestions fill the composer instead of sending, so the user decides; Undo restores what was there.
  const fill = () => {
    setPrevious(aui.composer().getState().text);
    aui.composer().setText(text);
    requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message"]')?.focus());
  };
  const undo = () => { aui.composer().setText(previous ?? ""); setPrevious(null); };
  return (
    <div className="flex flex-col items-start gap-1" data-slot="message-next">
      <span className="text-xs text-muted-foreground">Suggested next</span>
      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" variant="outline" size="sm" className="h-auto whitespace-normal rounded-full py-1.5 text-left" disabled={running} onClick={fill}>
          <span>{text}</span><ArrowRightIcon size={14} />
        </Button>
        {previous !== null && <Button type="button" variant="ghost" size="sm" className="pop-in text-brand" onClick={undo}>Undo</Button>}
      </div>
    </div>
  );
}

export function formatResponseTime(ms: number): string {
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function SelericResponseTime({ data }: DataMessagePartProps) {
  const { elapsedMs } = data as { elapsedMs: number };
  return <p data-slot="message-response-time" className="mt-2 text-xs tabular-nums text-muted-foreground">Responded in {formatResponseTime(elapsedMs)}</p>;
}

type FeedbackData = { messageId: string; threadId: string; runId: string | null; final: boolean };

export function SelericFeedback({ data }: DataMessagePartProps) {
  const { messageId, threadId, runId, final } = data as FeedbackData;
  const demoMode = useConversationStore((s) => s.demoMode);
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!final || demoMode) return;
    let alive = true;
    conversationsApi.getFeedback(threadId, messageId)
      .then((existing) => {
        if (!alive || !existing) return;
        setVote(existing.rating);
        if (existing.note) setNote(existing.note);
      })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [final, demoMode, threadId, messageId]);

  // Draft/streaming messages carry a transient id — no voting until final.
  if (!final) return null;

  const save = (rating: "up" | "down", text: string) => {
    // Demo mode has no backend thread: keep the verdict local so the buttons
    // still respond instead of flashing "Couldn't save".
    if (demoMode) {
      setSaved(true);
      return;
    }
    setSaving(true);
    setFailed(false);
    conversationsApi.submitFeedback(threadId, messageId, {
      rating, note: text, runId: runId ?? undefined,
    })
      .then(() => setSaved(true))
      .catch(() => setFailed(true))
      .finally(() => setSaving(false));
  };

  // Optimistic: paint the verdict immediately so a click always responds, then
  // persist in the background. Opening the comment box lets either verdict carry
  // a training note — not just a down vote.
  const cast = (rating: "up" | "down", text = note) => {
    setVote(rating);
    setSaved(false);
    setNoteOpen(true);
    save(rating, text);
  };

  const label = vote === "up" ? "What worked well?" : "What was wrong?";

  return (
    <div data-slot="message-feedback" className="mt-2 flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
      <span className="mr-1">Was this helpful?</span>
      <Button
        type="button"
        variant={vote === "up" ? "secondary" : "ghost"}
        size="icon-sm"
        aria-label="Mark answer helpful"
        aria-pressed={vote === "up"}
        onClick={() => (vote === "up" ? setNoteOpen((open) => !open) : cast("up"))}
      ><ThumbUpIcon /></Button>
      <Button
        type="button"
        variant={vote === "down" ? "secondary" : "ghost"}
        size="icon-sm"
        aria-label="Report a problem with this answer"
        aria-pressed={vote === "down"}
        onClick={() => (vote === "down" ? setNoteOpen((open) => !open) : cast("down"))}
      ><ThumbDownIcon /></Button>
      {failed && <span className="text-destructive" role="alert">Couldn’t save — retry.</span>}
      {saved && !noteOpen && <span role="status">Thanks — noted.</span>}
      {noteOpen && vote && (
        <form
          className="flex basis-full gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            setSaved(false);
            save(vote, note.trim());
          }}
        >
          <Label className="sr-only" htmlFor={`feedback-note-${messageId}`}>{label}</Label>
          <Input
            id={`feedback-note-${messageId}`}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder={`${label} (optional)`}
            maxLength={2000}
          />
          <Button type="submit" size="sm" disabled={saving}>Send</Button>
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
      "seleric-context": SelericContext,
      "seleric-next": SelericNext,
      "seleric-response-time": SelericResponseTime,
      "seleric-feedback": SelericFeedback,
    },
  },
};

/** Best-effort clipboard write: async API when available (secure contexts),
 *  legacy execCommand fallback for plain-http / older browsers. Never throws. */
async function writeToClipboard(text: string): Promise<boolean> {
  try {
    const clipboard = navigator.clipboard;
    if (clipboard?.writeText) {
      await clipboard.writeText(text);
      return true;
    }
  } catch { /* fall through to the legacy path */ }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-9999px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.focus();
    area.select();
    area.setSelectionRange(0, area.value.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

/** Answer-only text for Copy: prose + tables + code. Excludes Sources,
 *  report-metadata context, feedback row, response time and action labels. */
export function answerText(root: HTMLElement): string {
  const chunks: string[] = [];
  // Prose blocks (footer metadata already lives in the message-context line, so it is
  // excluded by construction).
  root.querySelectorAll(".safe-content").forEach((node) => {
    const text = (node as HTMLElement).innerText ?? node.textContent ?? "";
    if (text.trim()) chunks.push(text.trim());
  });
  // Analytical tables as tab-separated rows.
  root.querySelectorAll('[data-slot="table"], .md-table-wrap table').forEach((table) => {
    const rows: string[] = [];
    table.querySelectorAll("tr").forEach((tr) => {
      const cells = [...tr.querySelectorAll("th, td")].map((cell) =>
        (cell.textContent ?? "").trim().replace(/\s+/g, " "),
      );
      if (cells.some(Boolean)) rows.push(cells.join("\t"));
    });
    if (rows.length) chunks.push(rows.join("\n"));
  });
  root.querySelectorAll('[data-slot="part-code"]').forEach((node) => {
    const text = node.textContent ?? "";
    if (text.trim()) chunks.push(text.trim());
  });
  if (!chunks.length) {
    // Last resort: whole body minus known chrome rows.
    const clone = root.querySelector('[data-slot="message-content"]')?.cloneNode(true) as HTMLElement | undefined;
    clone?.querySelectorAll(
      '[data-slot="message-feedback"], [data-part="message-sources"], [data-slot="message-context"], [data-slot="message-response-time"], [data-slot="message-actions"], [data-part="thinking"], [data-slot="run-status"]',
    ).forEach((node) => node.remove());
    const fallback = clone?.innerText ?? clone?.textContent ?? "";
    if (fallback.trim()) chunks.push(fallback.trim());
  }
  return chunks.join("\n\n").trim();
}

function CopyButton() {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const flash = (ok: boolean) => {
    setCopied(ok);
    setFailed(!ok);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { setCopied(false); setFailed(false); }, 1600);
  };
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={failed ? "text-destructive" : undefined}
      aria-label={copied ? "Copied" : failed ? "Copy failed — select and copy manually" : "Copy response"}
      title="Copy response"
      onClick={(event) => {
        const root = (event.currentTarget as HTMLElement).closest('[data-slot="chat-message"]') as HTMLElement | null;
        if (!root) return;
        const text = answerText(root);
        if (!text) return;
        void writeToClipboard(text).then(flash);
      }}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
      {copied ? "Copied" : failed ? "Copy failed" : "Copy"}
    </Button>
  );
}

function DetailsButton() {
  const openInspector = useShellStore((s) => s.openInspector);
  return (
    <Button type="button" variant="ghost" size="sm" aria-label="Open inspector" title="Debug, causal, and memory" onClick={() => openInspector("Debug")}>
      Details
    </Button>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root data-slot="chat-message" data-role="user" className="mb-6 min-w-0 last:mb-2 animate-in fade-in slide-in-from-bottom-1 duration-200" aria-label="user message">
      <Message align="end">
        <MessageContent>
          <Bubble variant="muted">
            <BubbleContent><MessagePrimitive.Parts components={parts} /></BubbleContent>
          </Bubble>
        </MessageContent>
      </Message>
    </MessagePrimitive.Root>
  );
}

function RunningLine() {
  const progress = useConversationStore((state) => state.progress);
  const cancelRun = useConversationStore((state) => state.cancelRun);
  const timeline = useOffice((state) => state.timeline);
  const openInspector = useShellStore((state) => state.openInspector);
  const phases = useMemo(() => deriveRunPhases(timeline), [timeline]);
  return (
    <div data-slot="run-status" className="flex flex-wrap items-start justify-between gap-2" aria-live="polite">
      <Trace>
        <TraceCollapsible defaultOpen>
          <TraceCollapsibleTrigger>
            <TraceLine status="running">
              <TraceIcon><Spinner /></TraceIcon>
              <TraceTitle>{progress ?? "Working on it…"}</TraceTitle>
            </TraceLine>
          </TraceCollapsibleTrigger>
          <TraceCollapsibleContent>
            <Trace aria-label="Run progress">
              {phases.map((phase) => (
                <TraceLine
                  key={phase.key}
                  status={phase.state === "active" ? "running" : phase.state === "pending" ? "pending" : undefined}
                  aria-current={phase.state === "active" ? "step" : undefined}
                >
                  <TraceTitle>{phase.label}</TraceTitle>
                </TraceLine>
              ))}
            </Trace>
          </TraceCollapsibleContent>
        </TraceCollapsible>
      </Trace>
      <div className="flex gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={() => openInspector("Debug")}>View debug</Button>
        <Button type="button" variant="outline" size="sm" onClick={() => void cancelRun()}>Stop</Button>
      </div>
    </div>
  );
}

/** Streams the model's reasoning live, in a collapsed-by-default block. */
function ThinkingBubble() {
  const thinkingText = useConversationStore((state) => state.thinkingText);
  const submitting = useConversationStore((state) => state.submitting);
  const bodyRef = useRef<HTMLPreElement>(null);
  // Open while the model thinks and close when it finishes, unless the reader chose.
  const [chosen, setChosen] = useState<boolean>();

  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [thinkingText]);

  if (!thinkingText) return null;

  return (
    <Trace data-part="thinking" aria-label="Model thinking process">
      <TraceCollapsible open={chosen ?? submitting} onOpenChange={setChosen}>
        <TraceCollapsibleTrigger>
          <TraceLine status={submitting ? "running" : undefined}>
            <TraceTitle>{submitting ? "Thinking" : "Thought process"}</TraceTitle>
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <pre
            ref={bodyRef}
            className="m-0 max-h-56 overflow-y-auto font-mono text-xs break-words whitespace-pre-wrap text-muted-foreground"
            aria-live="polite"
            aria-label="Streaming thinking text"
          >
            {thinkingText}
          </pre>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root data-slot="chat-message" data-role="assistant" className="mb-6 min-w-0 last:mb-2 animate-in fade-in slide-in-from-bottom-1 duration-200" aria-label="assistant message">
      <Message>
        <MessageContent>
        <MessageHeader>Seleric</MessageHeader>
        <ThinkingBubble />
        <MessagePrimitive.Parts components={parts} />
        <AuiIf condition={(state) => state.thread.isRunning}>
          <MessagePrimitive.If hasContent={false}>
            <RunningLine />
          </MessagePrimitive.If>
        </AuiIf>
        <AuiIf condition={(state) => !state.thread.isRunning}>
          <ActionBarPrimitive.Root data-slot="message-actions" className="mt-2 flex items-center gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover/message:opacity-100 max-lg:opacity-100">
            <CopyButton />
            <DetailsButton />
            <ActionBarPrimitive.Reload aria-label="Retry response" className={buttonVariants({ variant: "ghost", size: "sm" })}>Retry</ActionBarPrimitive.Reload>
          </ActionBarPrimitive.Root>
        </AuiIf>
        </MessageContent>
      </Message>
    </MessagePrimitive.Root>
  );
}

function SystemMessage() {
  return (
    <MessagePrimitive.Root data-slot="chat-message" data-role="system" className="mb-5 min-w-0 text-sm" aria-label="system message">
      <div><MessagePrimitive.Parts components={parts} /></div>
    </MessagePrimitive.Root>
  );
}

function VoicePendingMessage() {
  const text = useConversationStore((state) => state.voicePending);
  if (!text) return null;
  return (
    <div data-slot="chat-message" data-role="user" data-pending className="mb-5 min-w-0 opacity-65" aria-label="user message (speaking)" aria-live="polite">
      <Message align="end">
        <MessageContent>
          <Bubble variant="muted"><BubbleContent>{text}</BubbleContent></Bubble>
        </MessageContent>
      </Message>
    </div>
  );
}

function ThreadContextBar() {
  const threadId = useConversationStore((s) => s.selectedThreadId);
  const thread = useConversationStore((s) => s.threads.find((item) => item.id === threadId));
  const count = useConversationStore((s) => (threadId ? s.messages[threadId]?.length ?? 0 : 0));
  // Empty threads already show the welcome empty-state — don't echo "Untitled conversation".
  if (!threadId || !thread || count === 0) return null;
  const exchanges = Math.ceil(count / 2);
  return (
    <div data-slot="thread-context" className="mb-4 flex min-w-0 items-baseline gap-2">
      <span className="min-w-0 truncate text-sm font-medium text-muted-foreground" title={thread.title || "Untitled conversation"}>
        {thread.title || "Untitled conversation"}
      </span>
      <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
        {exchanges} exchange{exchanges === 1 ? "" : "s"} · evidence stays attached below
      </span>
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div aria-label="Loading conversation" role="status">
      <div className="flex flex-col gap-3">
        <Skeleton className="h-6 w-1/3" />
        <Skeleton className="h-10 w-2/5 self-end" />
        <Skeleton className="h-4 w-11/12" />
        <Skeleton className="h-4 w-4/5" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>
    </div>
  );
}

export function Transcript() {
  const threadId = useConversationStore((state) => state.selectedThreadId);
  const loading = useConversationStore((state) => state.loading);
  const cachedCount = useConversationStore((state) => (threadId ? state.messages[threadId]?.length ?? 0 : 0));
  const showSkeleton = loading && !!threadId && cachedCount === 0;
  return (
    <ThreadPrimitive.Root className="relative flex min-h-0 flex-1 flex-col">
      <ThreadPrimitive.Viewport className="min-h-0 flex-1 overflow-y-auto scroll-smooth px-6 pt-4 pb-4 max-md:px-3.5 max-md:pt-3" aria-label="Conversation transcript" aria-busy={loading}>
        {/* Key re-triggers the view-enter fade so browsing conversations feels fluid. */}
        <div className="mx-auto w-full max-w-[736px] pb-3 animate-in fade-in duration-200" key={threadId ?? "empty"}>
          {showSkeleton && <LoadingSkeleton />}
          {!threadId && (
            <Empty>
              <EmptyHeader>
                <EmptyTitle>Where should we begin?</EmptyTitle>
                <EmptyDescription>Ask about marketing, sales, products, or operations — Seleric investigates your metrics and shows its evidence.</EmptyDescription>
              </EmptyHeader>
              <EmptyContent className="max-w-xl"><PromptRegistry surface="front" /></EmptyContent>
            </Empty>
          )}
          {threadId && (
            <ThreadPrimitive.Empty>
              <Empty>
                <EmptyHeader>
                  <EmptyTitle>What should we investigate?</EmptyTitle>
                  <EmptyDescription>Ask a question about your metrics and Seleric will investigate.</EmptyDescription>
                </EmptyHeader>
                <EmptyContent className="max-w-xl"><PromptRegistry surface="front" /></EmptyContent>
              </Empty>
            </ThreadPrimitive.Empty>
          )}
          {threadId && <ThreadContextBar />}
          <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage, SystemMessage }} />
          <VoicePendingMessage />
        </div>
        <ThreadPrimitive.ScrollToBottom aria-label="Scroll to latest message" className={cn(buttonVariants({ variant: "outline", size: "icon-sm" }), "absolute right-5 bottom-3 rounded-full")}>
          <ArrowDownIcon />
        </ThreadPrimitive.ScrollToBottom>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
