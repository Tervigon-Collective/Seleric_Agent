import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Item, ItemActions, ItemContent, ItemDescription, ItemTitle } from "@/components/ui/item";
import { decideApproval, type ApprovalStatus } from "../api/phase7";

export function ApprovalCard({ value }: { value: Record<string, unknown> }) {
  const id = String(value.approval_id ?? "");
  const [state, setState] = useState(String(value.status ?? "REQUESTED") as ApprovalStatus);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setState(String(value.status ?? "REQUESTED") as ApprovalStatus);
    setError(null);
  }, [value.status, id]);
  const decide = (decision: "APPROVED" | "REJECTED") => {
    setBusy(true);
    setError(null);
    void decideApproval(id, decision)
      .then((approval) => setState(approval.status))
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Approval decision failed");
      })
      .finally(() => setBusy(false));
  };
  return (
    <Item variant="outline" size="sm" aria-labelledby={`approval-${id}`}>
      <ItemContent>
        <ItemTitle id={`approval-${id}`}>Approval required</ItemTitle>
        <ItemDescription>{String(value.summary ?? value.action ?? "")}</ItemDescription>
        <ItemDescription>Required role: {String(value.required_role ?? "approver")} · {value.dry_run !== false ? "Dry run" : "Write enabled"}</ItemDescription>
        {error && <small role="alert" className="text-destructive">{error}</small>}
      </ItemContent>
      <ItemActions role="group" aria-label="Approval decision">
        <Button size="sm" disabled={busy || state !== "REQUESTED"} onClick={() => decide("APPROVED")}>Approve</Button>
        <Button size="sm" variant="outline" disabled={busy || state !== "REQUESTED"} onClick={() => decide("REJECTED")}>Reject</Button>
        <span role="status" aria-live="polite" className="text-muted-foreground">{state}</span>
      </ItemActions>
    </Item>
  );
}
