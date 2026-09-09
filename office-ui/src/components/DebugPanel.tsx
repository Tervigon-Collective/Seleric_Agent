import { useOffice } from "../store";

export function DebugPanel() {
  const debug = useOffice((s) => s.debugMode);
  const last = useOffice((s) => s.lastEvent);
  const missionId = useOffice((s) => s.missionId);
  const lastSeq = useOffice((s) => s.lastSeq);
  const conn = useOffice((s) => s.conn);
  const providerMode = useOffice((s) => s.providerMode);
  const seen = useOffice((s) => s.seenSeq.size);
  const traceUrl = useOffice((s) => s.traceUrl);
  const traceRequestId = useOffice((s) => s.traceRequestId);

  if (!debug) return null;
  return (
    <div className="debug">
      <div><b>mission</b> {missionId ?? "—"}</div>
      <div><b>provider</b> {providerMode} · <b>conn</b> {conn}</div>
      <div><b>lastSeq</b> {lastSeq} · <b>seen</b> {seen}</div>
      {traceRequestId && <div><b>request_id</b> {traceRequestId}</div>}
      {traceUrl && (
        <div>
          <a href={traceUrl} target="_blank" rel="noreferrer" style={{ color: "var(--st-working)" }}>
            open LangSmith trace ↗
          </a>
        </div>
      )}
      <hr style={{ borderColor: "var(--line)" }} />
      <div><b>last event</b></div>
      <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>
        {last ? JSON.stringify(last, null, 2) : "—"}
      </pre>
    </div>
  );
}
