import { SPEC_BY_ID } from "../office/agents";
import type { MissionRef } from "../types";

const STATUS_PIP: Record<string, string> = {
  running: "var(--st-working)",
  completed: "var(--st-done)",
  prototype_completed: "var(--st-done)",
  partial: "var(--st-waiting)",
  blocked: "var(--st-waiting)",
  failed: "var(--st-failed)",
  cancelled: "var(--st-neutral)",
};

/**
 * Multi-mission awareness (brief §31). Shows every mission the gateway knows
 * about with a status pip + current lead; clicking switches the office to it.
 * Hidden when there is only one.
 */
export function MissionMinimap({
  missions,
  missionId,
  onPick,
}: {
  missions: MissionRef[];
  missionId: string | null;
  onPick: (id: string) => void;
}) {
  if (missions.length < 2) return null;
  return (
    <div className="minimap" aria-label="Missions">
      <h2>Missions ({missions.length})</h2>
      {missions.slice(0, 8).map((m) => {
        const lead = m.missionLead ? SPEC_BY_ID[`${m.missionLead}_agent`]?.name ?? m.missionLead : "—";
        return (
          <div
            key={m.missionId}
            className={`mm-row ${m.missionId === missionId ? "active" : ""}`}
            role="button"
            tabIndex={0}
            onClick={() => onPick(m.missionId)}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onPick(m.missionId)}
            title={m.query}
          >
            <span className="pip" style={{ background: STATUS_PIP[m.status] ?? "var(--st-neutral)" }} />
            <span className="mm-q">{m.query || m.missionId}</span>
            <span className="mm-lead">{lead}</span>
          </div>
        );
      })}
    </div>
  );
}
