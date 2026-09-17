import { useEffect, useRef, useState } from "react";
import { getRunDiagnostics } from "../api/phase7";

export function AdminDiagnostics({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [runId, setRunId] = useState("");
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) queueMicrotask(() => input.current?.focus()); }, [open]);
  if (!open) return null;
  return <div className="modal-backdrop">
    <section className="admin-diagnostics" role="dialog" aria-modal="true" aria-labelledby="diagnostics-title"
      onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
      <header><h2 id="diagnostics-title">Run diagnostics</h2>
        <button onClick={onClose} aria-label="Close diagnostics">×</button></header>
      <form onSubmit={(event) => {
        event.preventDefault(); setError(""); setData(null);
        void getRunDiagnostics(runId).then(setData).catch((reason: Error) => setError(reason.message));
      }}>
        <label>Run ID<input ref={input} required value={runId}
          onChange={(event) => setRunId(event.target.value)} /></label>
        <button className="primary-btn" type="submit">Inspect</button>
      </form>
      <p className="empty-note">Replay inspection is read-only and dry-run by default.</p>
      {error && <p role="alert" className="error-banner">{error}</p>}
      {data && <pre tabIndex={0}>{JSON.stringify(data, null, 2)}</pre>}
    </section>
  </div>;
}
