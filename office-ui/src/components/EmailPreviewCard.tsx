import { useEffect, useState } from "react";
import { approveEmail, getEmail, type EmailState, type EmailStatus } from "../api/emails";
import { ApiError } from "../api/http";
import { SafeContent } from "./SafeContent";

interface Attachment { name: string; size_bytes: number; partial: boolean; note: string }

const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : []);
const kb = (bytes: number) => `${Math.max(1, Math.round(bytes / 1024))} KB`;
const message = (reason: unknown) => (reason instanceof Error ? reason.message : "Something went wrong");

export function EmailPreviewCard({ value }: { value: Record<string, unknown> }) {
  const id = String(value.approval_id ?? "");
  const version = Number(value.version ?? 1);
  const attachments = (Array.isArray(value.attachments) ? value.attachments : []) as Attachment[];
  const [live, setLive] = useState<EmailState | null>(null);
  const [expired, setExpired] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    setExpired(false);
    void getEmail(id)
      .then((state) => { if (current) setLive(state); })
      .catch((reason: unknown) => {
        if (!current) return;
        if (reason instanceof ApiError && reason.status === 404) setExpired(true);
      });
    return () => { current = false; };
  }, [id]);

  const status = (live?.status ?? String(value.status ?? "draft")) as EmailStatus;
  const newer = live !== null && live.version > version;
  const canApprove = !busy && !expired && !newer && (status === "draft" || status === "failed");
  const approve = () => {
    setBusy(true);
    setError(null);
    void approveEmail(id, version)
      .then((state) => setLive(state))
      .catch((reason: unknown) => {
        if (reason instanceof ApiError && reason.status === 404) setExpired(true);
        setError(message(reason));
      })
      .finally(() => setBusy(false));
  };

  return <aside className="part-card approval" aria-labelledby={`email-${id}`}>
    <strong id={`email-${id}`}>Email ready for your approval</strong>
    <small>From {String(value.sender ?? "")} · To {list(value.to).join(", ")}
      {list(value.cc).length > 0 && ` · Cc ${list(value.cc).join(", ")}`}</small>
    <span><b>{String(value.subject ?? "")}</b></span>
    <div className="email-body"><SafeContent text={String(value.body ?? "")} /></div>
    {attachments.map((a) => <small key={a.name}>Attachment: {a.name} ({kb(a.size_bytes)})</small>)}
    {attachments.filter((a) => a.partial).map((a) =>
      <small key={`${a.name}-partial`} role="alert">{a.note || "This attachment is partial."}</small>)}
    {newer && <small>A newer version of this email exists. Approve that one instead.</small>}
    {expired && <small>This draft has expired. Ask Seleric to draft it again.</small>}
    <div className="approval-actions" role="group" aria-label="Email approval">
      <button disabled={!canApprove} onClick={approve}>Approve &amp; send</button>
      <span role="status" aria-live="polite">{status}</span>
    </div>
    {status === "submitted" && <small>Submitted to the mail system. That means it was accepted for sending, not proof of delivery.</small>}
    {status === "unknown" && <small role="alert">The outcome is unknown: it may or may not have been sent. Check with the recipient before sending again.</small>}
    {status === "failed" && live?.error && <small className="approval-error" role="alert">Not sent: {live.error}</small>}
    {error && <small className="approval-error" role="alert">{error}</small>}
  </aside>;
}
