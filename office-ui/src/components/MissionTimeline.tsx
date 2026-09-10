import { useMemo, useState } from "react";
import { useOffice } from "../store";
import { SPEC_BY_ID } from "../office/agents";

const FILTERS = ["all", "mission", "leadership", "skeptic", "task", "artifact", "error"] as const;
type Filter = (typeof FILTERS)[number];

function familyOf(evType: string): Filter {
  if (evType.startsWith("mission")) return "mission";
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
    <div className={`timeline ${open ? "" : "collapsed"}`}>
      <div className="tl-head">
        <button className="btn" onClick={toggle}>{open ? "▾" : "▸"} Mission timeline</button>
        <span style={{ color: "var(--text-dim)" }}>{timeline.length} events</span>
        <span className="spacer" style={{ flex: 1 }} />
        {FILTERS.map((f) => (
          <button
            key={f}
            className="btn"
            style={{ borderColor: filter === f ? "var(--st-working)" : undefined }}
            onClick={() => setFilter(f)}
          >
            {f}
          </button>
        ))}
      </div>
      <div className="tl-body">
        {rows.map((e) => (
          <div
            className="tl-row"
            key={e.eventId}
            onClick={() => e.agentId && select(e.agentId)}
            style={{ cursor: e.agentId ? "pointer" : "default" }}
          >
            <span className="t">{new Date(e.timestamp).toLocaleTimeString().slice(0, 8)}</span>
            <span className="who">
              {e.agentId ? SPEC_BY_ID[e.agentId]?.name ?? e.agentId : "—"}{" "}
              <span className="ev-type">{e.eventType}</span>
            </span>
            <span>{e.summary}</span>
          </div>
        ))}
        {rows.length === 0 && (
          <div className="tl-row"><span /><span /><span style={{ color: "var(--text-dim)" }}>No events.</span></div>
        )}
      </div>
    </div>
  );
}
