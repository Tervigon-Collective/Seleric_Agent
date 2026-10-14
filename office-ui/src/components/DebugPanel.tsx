import { useOffice } from "../store";
import { LoopDebugConsole } from "./LoopDebugConsole";

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
  const timeline = useOffice((s) => s.timeline);
  const status = useOffice((s) => s.status);
  const loopDebug = useOffice((s) => s.loopDebug);

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
            open Langfuse trace ↗
          </a>
        </div>
      )}
      <hr style={{ borderColor: "var(--line)" }} />
      <LoopDebugConsole
        missionId={missionId}
        timeline={timeline}
        stats={loopDebug?.stats}
        grounding={loopDebug?.grounding}
        steps={loopDebug?.steps}
        live={status === "running" || status === "intake"}
      />
      <hr style={{ borderColor: "var(--line)" }} />
      <div><b>last event</b></div>
      <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>
        {last ? JSON.stringify(last, null, 2) : "—"}
      </pre>
    </div>
  );
}
