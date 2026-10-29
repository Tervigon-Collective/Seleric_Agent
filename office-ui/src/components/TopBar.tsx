import { useOffice, type ConnState } from "../store";
import type { MissionRef } from "../types";
import type { Workspace } from "../stores/shell";
import { WorkspaceSwitch } from "./ops/WorkspaceSwitch";

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
  dark,
  onToggleTheme,
  onSwitch,
}: {
  missions: MissionRef[];
  missionId: string | null;
  onPickMission: (id: string) => void;
  dark: boolean;
  onToggleTheme: () => void;
  onSwitch: (w: Workspace) => void;
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
    <header className="shell-header topbar">
      <button className="brand-button" onClick={() => onSwitch("conversation")} aria-label="Seleric home"><span className="brand-mark">S</span><strong>Seleric</strong></button>
      <WorkspaceSwitch current="office" onChange={onSwitch} />

      <select
        value={missionId ?? ""}
        onChange={(e) => onPickMission(e.target.value)}
        disabled={missions.length === 0}
        style={{ maxWidth: 260 }}
      >
        {missions.length === 0 && <option value="">No missions</option>}
        {missions.map((m) => (
          <option key={m.missionId} value={m.missionId}>
            {m.missionId} — {truncate(m.query, 40)}
          </option>
        ))}
      </select>

      <span className="mission-title" title={query}>{query}</span>
      <span className="spacer" />

      <span className="pill">{activeCount} active</span>
      <button className="btn" onClick={() => setFollow(cycleFollow(followMode))}>
        Camera: {followMode}
      </button>
      <button className="btn" onClick={toggleTimeline}>Timeline</button>
      <button className="btn" onClick={toggleDebug}>Debug</button>
      <button className="btn" onClick={onToggleTheme}>{dark ? "Light" : "Dark"}</button>

      <span className={`pill ${conn}`}>
        <span className="dot" /> {CONN_LABEL[conn]}
      </span>
    </header>
  );
}

function cycleFollow(m: "off" | "lead" | "activity"): "off" | "lead" | "activity" {
  return m === "off" ? "lead" : m === "lead" ? "activity" : "off";
}
function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
