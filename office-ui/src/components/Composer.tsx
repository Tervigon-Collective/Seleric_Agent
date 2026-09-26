import {
  AuiIf,
  AttachmentPrimitive,
  ComposerPrimitive,
} from "@assistant-ui/react";
import { useVoiceStore, type VoiceStatus } from "../stores/voice";

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
      >🎙</button>
    </div>
  );
}

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
      <VoiceControls />
      <small>Enter to send · Shift+Enter for a new line</small>
    </ComposerPrimitive.Root>
  );
}
