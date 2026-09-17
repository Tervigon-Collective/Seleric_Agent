import {
  ActionBarPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  type DataMessagePartProps,
} from "@assistant-ui/react";
import { useConversationStore } from "../stores/conversation";
import { MessagePartRenderer } from "./MessagePartRenderer";
import { SafeContent } from "./SafeContent";

function SelericPart({ data }: DataMessagePartProps) {
  return <MessagePartRenderer part={data} />;
}

const parts = {
  Text: ({ text }: { text: string }) => <SafeContent text={text} />,
  data: { by_name: { "seleric-part": SelericPart } },
};

function UserMessage() {
  return (
    <MessagePrimitive.Root className="message user" aria-label="user message">
      <div className="message-avatar" aria-hidden="true">Y</div>
      <div className="message-body"><header>You</header><MessagePrimitive.Parts components={parts} /></div>
    </MessagePrimitive.Root>
  );
}

function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="message assistant" aria-label="assistant message">
      <div className="message-avatar" aria-hidden="true">S</div>
      <div className="message-body">
        <header>Seleric</header>
        <MessagePrimitive.Parts components={parts} />
        <ActionBarPrimitive.Root className="message-actions">
          <ActionBarPrimitive.Reload aria-label="Retry response">Retry</ActionBarPrimitive.Reload>
        </ActionBarPrimitive.Root>
      </div>
    </MessagePrimitive.Root>
  );
}

function SystemMessage() {
  return <MessagePrimitive.Root className="message system" aria-label="system message">
    <div className="message-body"><MessagePrimitive.Parts components={parts} /></div>
  </MessagePrimitive.Root>;
}

export function Transcript() {
  const threadId = useConversationStore((state) => state.selectedThreadId);
  const loading = useConversationStore((state) => state.loading);
  return (
    <ThreadPrimitive.Root className="thread-root">
      <ThreadPrimitive.Viewport className="transcript" aria-label="Conversation transcript" aria-busy={loading}>
        {!threadId && <div className="empty-state"><h1>Start a conversation</h1><p>Create a thread to begin a Seleric mission.</p></div>}
        {threadId && <ThreadPrimitive.Empty><div className="empty-state"><h1>What should we investigate?</h1><p>Ask a question and the specialist swarm will coordinate a response.</p></div></ThreadPrimitive.Empty>}
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage, SystemMessage }} />
        <ThreadPrimitive.If running>
        <article className="message assistant running" aria-label="Assistant is working" aria-live="polite">
          <div className="message-avatar" aria-hidden="true">S</div>
          <div className="message-body"><header>Seleric</header><p><span className="pulse-dot" /> Coordinating specialists…</p></div>
        </article>
        </ThreadPrimitive.If>
        <ThreadPrimitive.ScrollToBottom aria-label="Scroll to latest message" className="scroll-latest">↓</ThreadPrimitive.ScrollToBottom>
      </ThreadPrimitive.Viewport>
    </ThreadPrimitive.Root>
  );
}
