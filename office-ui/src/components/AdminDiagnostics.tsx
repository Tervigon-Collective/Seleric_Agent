import { useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { getRunDiagnostics } from "../api/phase7";

export function AdminDiagnostics({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [runId, setRunId] = useState("");
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl" aria-describedby="diagnostics-note">
        <DialogHeader>
          <DialogTitle>Run diagnostics</DialogTitle>
          <DialogDescription id="diagnostics-note">Replay inspection is read-only and dry-run by default.</DialogDescription>
        </DialogHeader>
        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault(); setError(""); setData(null);
            void getRunDiagnostics(runId).then(setData).catch((reason: Error) => setError(reason.message));
          }}
        >
          <div className="flex flex-1 flex-col gap-1.5">
            <Label htmlFor="diagnostics-run-id">Run ID</Label>
            <Input id="diagnostics-run-id" required value={runId} onChange={(event) => setRunId(event.target.value)} />
          </div>
          <Button type="submit">Inspect</Button>
        </form>
        {error && <Alert variant="destructive" role="alert"><AlertDescription>{error}</AlertDescription></Alert>}
        {data && <pre tabIndex={0} className="max-h-[45vh] overflow-auto rounded-md border bg-muted p-3 font-mono text-xs">{JSON.stringify(data, null, 2)}</pre>}
      </DialogContent>
    </Dialog>
  );
}
