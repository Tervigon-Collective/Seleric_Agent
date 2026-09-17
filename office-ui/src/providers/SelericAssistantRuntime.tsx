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
import { useConversationStore } from "../stores/conversation";

const messageRole = (role: Message["role"]): ThreadMessageLike["role"] => {
  if (role === "USER") return "user";
  if (role === "SYSTEM") return "system";
  return "assistant";
};

const partText = (part: MessagePart) =>
  typeof part.content === "string" ? part.content : JSON.stringify(part.content);

export const convertSelericMessage = (message: Message): ThreadMessageLike => {
  const role = messageRole(message.role);
  return {
    id: message.id,
    role,
    createdAt: new Date(message.created_at),
    content: role === "system"
      ? [{ type: "text", text: message.parts.map(partText).join("\n") }]
      : message.parts.map((part) => ({
          type: "data-seleric-part" as const,
          data: part,
        })),
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
  accept: "*",
  async add({ file }): Promise<PendingAttachment> {
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
    isDisabled: !threadId,
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
