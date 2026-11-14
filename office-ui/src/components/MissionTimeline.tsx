import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { cn } from "cn";
import { useMemo, useState } from "react";
import { useOffice } from "../store";
import { SPEC_BY_ID } from "../office/agents";

const FILTERS = ["all", "mission", "tools", "leadership", "skeptic", "task", "artifact", "error"] as const;
type Filter = (typeof FILTERS)[number];

function familyOf(evType: string): Filter {
  if (evType.startsWith("mission")) return "mission";
  if (evType.startsWith("tool_") || evType === "agent_thinking" || evType === "answering") return "tools";
  if (evType.startsWith("leadership")) return "leadership";
  if (evType.startsWith("skeptic")) return "skeptic";
  if (evType.startsWith("task") || evType === "agent_started" || evType.startsWith("remediation")) return "task";
  if (evType.startsWith("artifact") || evType.startsWith("decomposition")) return "artifact";
  if (evType === "error" || evType.endsWith("_rejected")) return "error";
  return "all";
}

export function MissionTimeline() {
  const open = useOffice((s) => s.timelineOpen);
  const toggle = useOffice((s) => s.toggleTimeline);
  const timeline = useOffice((s) => s.timeline);
  const select = useOffice((s) => s.select);
  const [filter, setFilter] = useState<Filter>("all");

  const rows = useMemo(() => {
    const r = filter === "all" ? timeline : timeline.filter((e) => familyOf(e.eventType) === filter);
    return [...r].reverse();
  }, [timeline, filter]);

  return (
    <div data-slot="timeline" className={cn("z-[6] flex flex-col border-t bg-card", open ? "max-h-40" : "max-h-10")}>
      <div className="flex flex-none items-center gap-2.5 overflow-x-auto border-b px-3 py-1.5 text-xs">
        <Button variant="ghost" size="sm" onClick={toggle}>{open ? "▾" : "▸"} Mission timeline</Button>
        <span className="text-muted-foreground">{timeline.length} events</span>
        <span className="flex-1" />
        <ToggleGroup type="single" variant="outline" size="sm" aria-label="Event filter" value={filter} onValueChange={(value) => value && setFilter(value as Filter)}>
          {FILTERS.map((f) => <ToggleGroupItem key={f} value={f}>{f}</ToggleGroupItem>)}
        </ToggleGroup>
      </div>
      <div className="overflow-y-auto py-1">
        {rows.map((e) => (
          <div
            data-slot="tl-row"
            className={cn("grid grid-cols-[52px_1fr] items-baseline gap-x-2.5 px-3 py-1 text-xs hover:bg-accent md:grid-cols-[64px_130px_1fr]", e.agentId && "cursor-pointer")}
            key={e.eventId}
            onClick={() => e.agentId && select(e.agentId)}
          >
            <span className="tabular-nums text-muted-foreground">{new Date(e.timestamp).toLocaleTimeString().slice(0, 8)}</span>
            <span className="font-semibold">
              {e.agentId ? SPEC_BY_ID[e.agentId]?.name ?? e.agentId : "—"}{" "}
              <span className="font-mono text-[11px] font-normal text-muted-foreground">{e.eventType}</span>
            </span>
            <span className="col-start-2 md:col-start-auto">{e.summary}</span>
          </div>
        ))}
        {rows.length === 0 && (
          <div data-slot="tl-row" className="px-3 py-1 text-xs text-muted-foreground">No events.</div>
        )}
      </div>
    </div>
  );
}
