import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
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
    <Card size="sm" data-slot="debug-panel" className="absolute bottom-[210px] left-3 z-[8] max-h-[40vh] w-80 overflow-auto font-mono text-xs shadow-lg"><CardContent className="flex flex-col gap-1.5">
      <div><b>mission</b> {missionId ?? "—"}</div>
      <div><b>provider</b> {providerMode} · <b>conn</b> {conn}</div>
      <div><b>lastSeq</b> {lastSeq} · <b>seen</b> {seen}</div>
      {traceRequestId && <div><b>request_id</b> {traceRequestId}</div>}
      {traceUrl && (
        <div>
          <a href={traceUrl} target="_blank" rel="noreferrer" className="text-primary underline">
            open Langfuse trace ↗
          </a>
        </div>
      )}
      <Separator />
      <LoopDebugConsole
        missionId={missionId}
        timeline={timeline}
        stats={loopDebug?.stats}
        grounding={loopDebug?.grounding}
        steps={loopDebug?.steps}
        live={status === "running" || status === "intake"}
      />
      <Separator />
      <div><b>last event</b></div>
      <pre className="m-0 whitespace-pre-wrap">
        {last ? JSON.stringify(last, null, 2) : "—"}
      </pre>
    </CardContent></Card>
  );
}
