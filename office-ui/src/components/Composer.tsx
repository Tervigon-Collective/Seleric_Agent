import {
  AuiIf,
  AttachmentPrimitive,
  ComposerPrimitive,
} from "@assistant-ui/react";

export function Composer() {
  return (
    <ComposerPrimitive.Root className="composer" aria-label="Message composer">
      <ComposerPrimitive.AddAttachment className="attach-btn" aria-label="Choose attachments">＋</ComposerPrimitive.AddAttachment>
      <ComposerPrimitive.Input
        placeholder="Ask Seleric…"
        aria-label="Message"
        rows={2}
      />
      <AuiIf condition={(state) => !state.thread.isRunning}>
        <ComposerPrimitive.Send className="send-btn" aria-label="Send message">↑</ComposerPrimitive.Send>
      </AuiIf>
      <AuiIf condition={(state) => state.thread.isRunning}>
        <ComposerPrimitive.Cancel className="send-btn cancel-btn" aria-label="Cancel run" title="Cancel run">■</ComposerPrimitive.Cancel>
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
      <small>Enter to send · Shift+Enter for a new line</small>
    </ComposerPrimitive.Root>
  );
}
