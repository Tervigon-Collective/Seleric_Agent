import { Card, CardContent } from "@/components/ui/card";
import { useEffect, useState } from "react";
import { useOffice } from "../store";
import { SPEC_BY_ID } from "../office/agents";

export function MissionHeader() {
  const query = useOffice((s) => s.query);
  const status = useOffice((s) => s.status);
  const stage = useOffice((s) => s.stage);
  const leadAgentId = useOffice((s) => s.leadAgentId);
  const epoch = useOffice((s) => s.leadershipEpoch);
  const startedAt = useOffice((s) => s.startedAt);
  const agents = useOffice((s) => s.agents);

  const elapsed = useElapsed(startedAt, status);
  const activeCount = Object.values(agents).filter(
    (a) => a.status !== "idle" && a.status !== "offline",
  ).length;
  const leadName = leadAgentId ? SPEC_BY_ID[leadAgentId]?.name ?? leadAgentId : "—";

  if (!query) return null;

  return (
    <Card size="sm" data-slot="mission-header" className="absolute top-2 left-2 z-[4]">
      <CardContent className="flex gap-2 text-xs whitespace-nowrap text-muted-foreground">
        <span><b className="font-bold text-foreground">{status.toUpperCase()}</b></span>
        <span>{stage}</span>
        <span>{leadName}{epoch ? ` · e${epoch}` : ""}</span>
        <span>{activeCount} active</span>
        <span>{elapsed}</span>
      </CardContent>
    </Card>
  );
}

function useElapsed(startedAt: string | null, status: string): string {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (status === "completed" || status === "failed" || status === "blocked") return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [status]);
  if (!startedAt) return "00:00";
  const secs = Math.max(0, Math.floor((now - new Date(startedAt).getTime()) / 1000));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
