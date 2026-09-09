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
  providerMode,
  onPickProvider,
  dark,
  onToggleTheme,
}: {
  missions: MissionRef[];
  missionId: string | null;
  onPickMission: (id: string) => void;
  providerMode: "demo" | "seleric";
  onPickProvider: (m: "demo" | "seleric") => void;
  dark: boolean;
  onToggleTheme: () => void;
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
    <header className="topbar">
      <span className="brand">SELERIC OFFICE</span>

      <select value={providerMode} onChange={(e) => onPickProvider(e.target.value as "demo" | "seleric")}>
        <option value="demo">Demo fixture</option>
        <option value="seleric">Live swarm</option>
      </select>

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
        cam: {followMode}
      </button>
      <button className="btn" onClick={toggleTimeline}>timeline</button>
      <button className="btn" onClick={toggleDebug}>debug</button>
      <button className="btn" onClick={onToggleTheme}>{dark ? "light" : "dark"}</button>

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
