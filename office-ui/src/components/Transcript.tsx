import {
  ActionBarPrimitive,
  AuiIf,
  MessagePrimitive,
  ThreadPrimitive,
  type DataMessagePartProps,
} from "@assistant-ui/react";
import type { MessagePart } from "../api/contracts";
import { useConversationStore } from "../stores/conversation";
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

function UserMessage() {
  return (
    <MessagePrimitive.Root className="message user" aria-label="user message">
      <div className="message-avatar" aria-hidden="true">Y</div>
      <div className="message-body"><header>You</header><MessagePrimitive.Parts components={parts} /></div>
    </MessagePrimitive.Root>
  );
}

function RunningLine() {
  const progress = useConversationStore((state) => state.progress);
  const cancelRun = useConversationStore((state) => state.cancelRun);
  return (
    <div className="running-row">
      <p className="running" aria-live="polite"><span className="pulse-dot" /> {progress ?? "Working on it…"}</p>
      <button type="button" className="running-cancel" onClick={() => void cancelRun()}>Cancel</button>
    </div>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="message assistant" aria-label="assistant message">
      <div className="message-avatar" aria-hidden="true">S</div>
      <div className="message-body">
        <header>Seleric</header>
        <MessagePrimitive.Parts components={parts} />
        <AuiIf condition={(state) => state.thread.isRunning}>
          <MessagePrimitive.If hasContent={false}>
            <RunningLine />
          </MessagePrimitive.If>
        </AuiIf>
        <AuiIf condition={(state) => !state.thread.isRunning}>
          <ActionBarPrimitive.Root className="message-actions">
            <ActionBarPrimitive.Reload aria-label="Retry response">Retry</ActionBarPrimitive.Reload>
          </ActionBarPrimitive.Root>
        </AuiIf>
      </div>
    </MessagePrimitive.Root>
  );
}

function SystemMessage() {
  return <MessagePrimitive.Root className="message system" aria-label="system message">
    <div className="message-body"><MessagePrimitive.Parts components={parts} /></div>
  </MessagePrimitive.Root>;
}

function VoicePendingMessage() {
  const text = useConversationStore((state) => state.voicePending);
  if (!text) return null;
  return (
    <div className="message user pending" aria-label="user message (speaking)" aria-live="polite">
      <div className="message-avatar" aria-hidden="true">Y</div>
      <div className="message-body"><header>You · voice</header><p>{text}</p></div>
    </div>
  );
}

export function Transcript() {
  const threadId = useConversationStore((state) => state.selectedThreadId);
  const loading = useConversationStore((state) => state.loading);
  return (
    <ThreadPrimitive.Root className="thread-root">
      <ThreadPrimitive.Viewport className="transcript" aria-label="Conversation transcript" aria-busy={loading}>
        {!threadId && <div className="empty-state"><h1>What can Seleric help with?</h1><p>Type a message below to start a conversation.</p></div>}
        {threadId && <ThreadPrimitive.Empty><div className="empty-state"><h1>What should we investigate?</h1><p>Ask a question about your metrics and Seleric will investigate.</p></div></ThreadPrimitive.Empty>}
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage, SystemMessage }} />
        <VoicePendingMessage />
        <ThreadPrimitive.ScrollToBottom aria-label="Scroll to latest message" className="scroll-latest">↓</ThreadPrimitive.ScrollToBottom>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
