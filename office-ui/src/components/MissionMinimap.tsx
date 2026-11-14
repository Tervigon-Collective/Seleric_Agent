import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
    <Card size="sm" data-slot="minimap" aria-label="Missions" className="absolute right-2 bottom-9 z-[7] max-w-64 max-lg:hidden">
      <CardContent className="flex flex-col gap-0.5">
        <h2 className="m-0 text-xs tracking-wide text-muted-foreground uppercase">Missions ({missions.length})</h2>
        {missions.slice(0, 8).map((m) => {
          const lead = m.missionLead ? SPEC_BY_ID[`${m.missionLead}_agent`]?.name ?? m.missionLead : "—";
          return (
            <Button
              key={m.missionId}
              type="button"
              variant={m.missionId === missionId ? "secondary" : "ghost"}
              size="xs"
              className="w-full justify-start"
              onClick={() => onPick(m.missionId)}
              title={m.query}
            >
              <span className="size-2 shrink-0 rounded-full" style={{ background: STATUS_PIP[m.status] ?? "var(--st-neutral)" }} />
              <span className="min-w-0 flex-1 truncate text-left">{m.query || m.missionId}</span>
              <span className="shrink-0 text-muted-foreground">{lead}</span>
            </Button>
          );
        })}
      </CardContent>
    </Card>
  );
}
