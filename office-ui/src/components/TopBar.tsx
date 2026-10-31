import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { useOffice, type ConnState } from "../store";
import type { MissionRef } from "../types";

const CONN_LABEL: Record<ConnState, string> = {
  idle: "Idle",
  connecting: "Connecting",
  live: "Live",
  reconnecting: "Reconnecting",
  closed: "Closed",
  error: "Error",
};

export function TopBar({
  missions,
  missionId,
  onPickMission,
}: {
  missions: MissionRef[];
  missionId: string | null;
  onPickMission: (id: string) => void;
}) {
  const conn = useOffice((s) => s.conn);
  const query = useOffice((s) => s.query);
  const agents = useOffice((s) => s.agents);
  const followMode = useOffice((s) => s.followMode);
  const setFollow = useOffice((s) => s.setFollow);
  const toggleTimeline = useOffice((s) => s.toggleTimeline);
  const toggleDebug = useOffice((s) => s.toggleDebug);

  const activeCount = Object.values(agents).filter(
    (a) => a.status !== "idle" && a.status !== "offline",
  ).length;

  return (
    <>

      <NativeSelect
        className="max-w-64"
        aria-label="Mission"
        value={missionId ?? ""}
        onChange={(e) => onPickMission(e.target.value)}
        disabled={missions.length === 0}
      >
        {missions.length === 0 && <NativeSelectOption value="">No missions</NativeSelectOption>}
        {missions.map((m) => (
          <NativeSelectOption key={m.missionId} value={m.missionId}>
            {m.missionId} — {truncate(m.query, 40)}
          </NativeSelectOption>
        ))}
      </NativeSelect>

      <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground max-lg:hidden" title={query}>{query}</span>
      <span className="flex-1 lg:hidden" />

      <Badge variant="secondary">{activeCount} active</Badge>
      <Button variant="outline" size="sm" onClick={() => setFollow(cycleFollow(followMode))}>
        Camera: {followMode}
      </Button>
      <Button variant="outline" size="sm" onClick={toggleTimeline}>Timeline</Button>
      <Button variant="outline" size="sm" onClick={toggleDebug}>Debug</Button>

      <Badge variant={conn === "live" ? "secondary" : "outline"}>{CONN_LABEL[conn]}</Badge>
    </>
  );
}

function cycleFollow(m: "off" | "lead" | "activity"): "off" | "lead" | "activity" {
  return m === "off" ? "lead" : m === "lead" ? "activity" : "off";
}
function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
