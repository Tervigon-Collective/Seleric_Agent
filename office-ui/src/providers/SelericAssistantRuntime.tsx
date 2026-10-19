import {
  AssistantRuntimeProvider,
  type AppendMessage,
  type AttachmentAdapter,
  type PendingAttachment,
  type ThreadMessageLike,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import type { PropsWithChildren } from "react";
import type { Message, MessagePart } from "../api/contracts";
import { ALLOWED_ATTACHMENT_TYPES, validateAttachment } from "../api/conversations";
import { useConversationStore } from "../stores/conversation";

const messageRole = (role: Message["role"]): ThreadMessageLike["role"] => {
  if (role === "USER") return "user";
  if (role === "SYSTEM") return "system";
  return "assistant";
};

const partText = (part: MessagePart) =>
  typeof part.content === "string" ? part.content : JSON.stringify(part.content);

// A footer line the model appends to every data answer, e.g.
// "Period: 2026-10-08..2026-10-08 · Currency: INR · Data as of 2026-10-09"
// (or "Fetched at <date>" when only query time is known). It is report
// metadata, not answer prose: strip it from the copied/voiced body and render
// it as a muted context line alongside Sources.
const CONTEXT_LINE =
  /^\s*(?:\*{0,2}Period:?\*{0,2}\s*.+?\s*[·|•]\s*)?(?:\*{0,2}Currency:?\*{0,2}\s*\S+\s*[·|•]\s*)?(?:\*{0,2}(?:Data as of|Fetched at):?\*{0,2}\s*.+)\s*\.?\s*$/i;
const CONTEXT_BARE =
  /^\s*\*{0,2}(?:Period|Currency|Data as of|Fetched at):?\*{0,2}\s+.+[·|•]?.*$/i;

function isContextLine(line: string): boolean {
  const t = line.trim();
  if (!t) return false;
  if (CONTEXT_LINE.test(t)) return true;
  // Full "Period: … · Currency: … · Data as of …" line always qualifies, even
  // if the strict shape above misses a variant separator.
  if (/period\s*:/i.test(t) && /currency\s*:/i.test(t)) return true;
  if (CONTEXT_BARE.test(t) && /(data as of|fetched at|currency|period)\s*:/i.test(t)) return true;
  return false;
}

/** Pull trailing report-metadata lines out of TEXT parts. Returns cleaned text + context. */
export function splitAnswerAndContext(raw: string): { answer: string; context: string | null } {
  const lines = raw.replace(/\r\n?/g, "\n").split("\n");
  const kept: string[] = [];
  const ctx: string[] = [];
  for (const line of lines) {
    if (isContextLine(line)) {
      const clean = line.trim().replace(/^[>•\-\*\+]\s*/, "").trim();
      if (clean) ctx.push(clean);
    } else {
      kept.push(line);
    }
  }
  // Trim trailing blank lines left behind after the footer is lifted.
  while (kept.length && !kept[kept.length - 1].trim()) kept.pop();
  const answer = kept.join("\n").trimEnd();
  return { answer, context: ctx.length ? ctx.join(" · ") : null };
}
const sourcesOf = (message: Message) => message.parts.filter((part) => part.type === "SOURCE");

// SOURCE parts collapse into one trailing disclosure so evidence rows don't
// crowd the answer text. Report metadata (Period / Currency / Data-as-of) is
// lifted out of the TEXT body into its own context part so it renders
// alongside Sources — never as answer prose and never in the copied text.
// A feedback part pins reader verdicts to the message.
const assistantContent = (message: Message) => {
  let context: string | null = null;
  const content = message.parts
    .filter((part) => part.type !== "SOURCE")
    .flatMap((part) => {
      if (part.type === "TEXT" && typeof part.content === "string") {
        const split = splitAnswerAndContext(part.content);
        if (split.context) context = context ? `${context} · ${split.context}` : split.context;
        if (!split.answer.trim()) return [];
        return [{ type: "data-seleric-part" as const, data: { ...part, content: split.answer } }];
      }
      return [{ type: "data-seleric-part" as const, data: part }];
    });
  const sources = sourcesOf(message);
  const withSources = sources.length
    ? [...content, { type: "data-seleric-sources" as const, data: sources }]
    : content;
  const withContext = context
    ? [...withSources, { type: "data-seleric-context" as const, data: { text: context } }]
    : withSources;
  const elapsedMs = responseTimeMs(message);
  const withFeedback = [
    ...withContext,
    {
      type: "data-seleric-feedback" as const,
      data: {
        messageId: message.id,
        threadId: message.thread_id,
        runId: message.run_id,
        final: Boolean(message.updated_at),
      },
    },
  ];
  return elapsedMs === null
    ? withFeedback
    : [...withFeedback, { type: "data-seleric-response-time" as const, data: { elapsedMs } }];
};

// Streaming drafts and in-flight placeholders carry no updated_at (or an empty
// body), so only persisted, answered messages get a response time.
const responseTimeMs = (message: Message): number | null => {
  if (!message.updated_at || !message.parts.length) return null;
  const elapsed = Date.parse(message.updated_at) - Date.parse(message.created_at);
  return Number.isFinite(elapsed) && elapsed > 0 ? elapsed : null;
};

export const convertSelericMessage = (message: Message): ThreadMessageLike => {
  const role = messageRole(message.role);
  // Assistant-only chrome (sources disclosure, context footer, verdict row,
  // response time) must never attach to user bubbles: every persisted message
  // carries updated_at, so gating on `final` alone is not enough.
  const textContent = [{ type: "text" as const, text: message.parts.map(partText).join("\n") }];
  return {
    id: message.id,
    role,
    createdAt: new Date(message.created_at),
    content: role === "assistant" ? assistantContent(message) : textContent,
    ...(role === "assistant"
      ? { status: { type: "complete" as const, reason: "stop" as const } }
      : {}),
    metadata: { custom: { runId: message.run_id, selericRole: message.role } },
  };
};

const messageText = (message: AppendMessage) =>
  message.content
    .filter((part): part is Extract<typeof part, { type: "text" }> => part.type === "text")
    .map((part) => part.text)
    .join("\n")
    .trim();

const attachmentIds = (message: AppendMessage) =>
  (message.attachments ?? []).flatMap((attachment) =>
    attachment.content.flatMap((part) => {
      if (part.type !== "data" || part.name !== "seleric-attachment") return [];
      const id = (part.data as { attachmentId?: unknown }).attachmentId;
      return typeof id === "string" ? [id] : [];
    }),
  );

const selericAttachmentAdapter: AttachmentAdapter = {
  accept: [...ALLOWED_ATTACHMENT_TYPES].join(","),
  async add({ file }): Promise<PendingAttachment> {
    validateAttachment(file);
    return {
      id: `${file.name}-${file.lastModified}-${file.size}`,
      type: "file",
      name: file.name,
      contentType: file.type,
      file,
      status: { type: "requires-action", reason: "composer-send" },
    };
  },
  async remove() {},
  async send(attachment) {
    const id = await useConversationStore.getState().uploadAttachment(attachment.file);
    if (!id) throw new Error(`Unable to upload ${attachment.name}`);
    return {
      ...attachment,
      id,
      status: { type: "complete" },
      content: [{
        type: "data",
        name: "seleric-attachment",
        data: { attachmentId: id, name: attachment.name },
      }],
    };
  },
};

export function SelericAssistantRuntimeProvider({ children }: PropsWithChildren) {
  const threadId = useConversationStore((state) => state.selectedThreadId);
  const messages = useConversationStore((state) =>
    threadId ? state.messages[threadId] ?? [] : [],
  );
  const loading = useConversationStore((state) => state.loading);
  const submitting = useConversationStore((state) => state.submitting);
  const submit = useConversationStore((state) => state.submit);
  const cancelRun = useConversationStore((state) => state.cancelRun);
  const retryFrom = useConversationStore((state) => state.retryFrom);

  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: convertSelericMessage,
    isLoading: loading,
    isRunning: submitting,
    isDisabled: false,
    onNew: async (message) => {
      await submit(messageText(message), attachmentIds(message), message.parentId);
    },
    onReload: async (parentId) => {
      await retryFrom(parentId);
    },
    onCancel: cancelRun,
    adapters: { attachments: selericAttachmentAdapter },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
