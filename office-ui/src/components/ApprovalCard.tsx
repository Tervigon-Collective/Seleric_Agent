import { useState } from "react";
import { decideApproval, type ApprovalStatus } from "../api/phase7";

export function ApprovalCard({ value }: { value: Record<string, unknown> }) {
  const id = String(value.approval_id ?? "");
  const [state, setState] = useState(String(value.status ?? "REQUESTED") as ApprovalStatus);
  const [busy, setBusy] = useState(false);
  const decide = (decision: "APPROVED" | "REJECTED") => {
    setBusy(true);
    void decideApproval(id, decision).then((approval) => setState(approval.status)).finally(() => setBusy(false));
  };
  return <aside className="part-card approval" aria-labelledby={`approval-${id}`}>
    <strong id={`approval-${id}`}>Approval required</strong>
    <span>{String(value.summary ?? value.action ?? "")}</span>
    <small>Required role: {String(value.required_role ?? "approver")} · {value.dry_run !== false ? "Dry run" : "Write enabled"}</small>
    <div className="approval-actions" role="group" aria-label="Approval decision">
      <button disabled={busy || state !== "REQUESTED"} onClick={() => decide("APPROVED")}>Approve</button>
      <button disabled={busy || state !== "REQUESTED"} onClick={() => decide("REJECTED")}>Reject</button>
      <span role="status" aria-live="polite">{state}</span>
    </div>
  </aside>;
}
