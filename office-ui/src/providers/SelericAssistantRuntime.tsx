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

// SOURCE parts collapse into one trailing disclosure so evidence rows don't
// crowd the answer text.
const assistantContent = (message: Message) => {
  const sources = message.parts.filter((part) => part.type === "SOURCE");
  const content = message.parts
    .filter((part) => part.type !== "SOURCE")
    .map((part) => ({ type: "data-seleric-part" as const, data: part }));
  const withSources = sources.length
    ? [...content, { type: "data-seleric-sources" as const, data: sources }]
    : content;
  const elapsedMs = responseTimeMs(message);
  return elapsedMs === null
    ? withSources
    : [...withSources, { type: "data-seleric-response-time" as const, data: { elapsedMs } }];
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
  return {
    id: message.id,
    role,
    createdAt: new Date(message.created_at),
    content: role === "system"
      ? [{ type: "text", text: message.parts.map(partText).join("\n") }]
      : assistantContent(message),
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
