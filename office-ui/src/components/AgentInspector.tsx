import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useMemo } from "react";
import { useOffice } from "../store";
import { SPEC_BY_ID } from "../office/agents";
import { STATUS_TOKEN } from "../office/stateMachine";
import { XIcon } from "./icons";
import type { OfficeAgent, SwarmUIEvent } from "../types";

const TOKEN_VAR: Record<string, string> = {
  neutral: "var(--st-neutral)", working: "var(--st-working)", thinking: "var(--st-thinking)",
  waiting: "var(--st-waiting)", failed: "var(--st-failed)", done: "var(--st-done)", collab: "var(--st-collab)",
};

export function AgentInspector() {
  const selectedId = useOffice((s) => s.selectedAgentId);
  const agent = useOffice((s) => (selectedId ? s.agents[selectedId] : undefined));
  const timeline = useOffice((s) => s.timeline);
  const handoffs = useOffice((s) => s.handoffs);
  const artifacts = useOffice((s) => s.artifacts);
  const finalResponse = useOffice((s) => s.finalResponse);
  const debug = useOffice((s) => s.debugMode);
  const select = useOffice((s) => s.select);
  const parallel = useOffice((s) => (selectedId ? s.parallelTasks[selectedId] ?? [] : []));

  const mine = useMemo(
    () => timeline.filter((e) => e.agentId === selectedId).slice(-30).reverse(),
    [timeline, selectedId],
  );
  if (!agent || !selectedId) return null;

  const spec = SPEC_BY_ID[agent.agentId];
  const token = STATUS_TOKEN[agent.status];
  const proc = processFor(agent, timeline, artifacts);

  return (
    <aside data-slot="inspector" className="absolute inset-y-0 right-0 z-[12] w-[380px] overflow-y-auto border-l bg-card p-4 shadow-lg max-lg:w-full" aria-label={`${agent.name} inspector`}>
      <Button variant="ghost" size="icon-sm" className="absolute top-3 right-3" aria-label="Close inspector" onClick={() => select(null)}><XIcon /></Button>
      <h2 className="m-0 text-[15px] font-medium">{spec?.glyph} {agent.name}</h2>
      <Badge className="text-[var(--on-accent)]" style={{ background: TOKEN_VAR[token] }}>{agent.status.replace(/_/g, " ")}</Badge>

      <section className="mt-4">
        <h3 className="mb-1.5 text-xs tracking-wide text-muted-foreground uppercase">Overview</h3>
        <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm [&>span]:text-muted-foreground">
          <span>Role</span><div>{agent.role}{agent.domain ? ` · ${agent.domain}` : ""}</div>
          <span>Mission lead</span><div>{agent.missionLead ? "yes" : "no"}</div>
          {agent.lastEventAt && (<><span>Last event</span><div>{new Date(agent.lastEventAt).toLocaleTimeString()}</div></>)}
          {agent.error && (<><span>Blocked</span><div style={{ color: "var(--st-failed)" }}>{agent.error}</div></>)}
        </div>
      </section>

      <section className="mt-4">
        <h3 className="mb-1.5 text-xs tracking-wide text-muted-foreground uppercase">Current task</h3>
        <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm [&>span]:text-muted-foreground">
          {agent.subquestion && (<><span>Sub-question</span><div>{agent.subquestion}</div></>)}
          {agent.task && (<><span>Task</span><div>{agent.task}</div></>)}
          {agent.currentAction && (<><span>Action</span><div>{agent.currentAction}</div></>)}
          {agent.currentTool && (<><span>Tool</span><div>{agent.currentTool}</div></>)}
          {agent.currentHypothesis && (<><span>Hypothesis</span><div>{agent.currentHypothesis}</div></>)}
          {agent.progress && (<><span>Progress</span><div>{agent.progress.current}/{agent.progress.total} {agent.progress.label}</div></>)}
          {!agent.currentAction && !agent.task && !agent.subquestion && <div className="text-muted-foreground">Idle at home desk.</div>}
        </div>
        {parallel.length > 0 && (
          <div className="mt-2 flex flex-wrap items-center gap-1">
            <div className="w-full text-xs text-muted-foreground">Parallel helpers</div>
            {parallel.map((t, i) => (
              <Badge key={`${t.label}-${i}`} variant="outline">{t.label} · {t.status}</Badge>
            ))}
          </div>
        )}
      </section>

      <section className="mt-4">
        <h3 className="mb-1.5 text-xs tracking-wide text-muted-foreground uppercase">Process</h3>
        {proc.map((p) => (
          <div className="flex items-baseline gap-2 py-0.5 text-sm" key={p.label}>
            <span style={{ color: p.state === "done" ? "var(--st-done)" : p.state === "active" ? "var(--st-working)" : "var(--text-dim)" }}>
              {p.state === "done" ? "✓" : p.state === "active" ? "●" : "○"}
            </span>
            <span style={{ fontWeight: p.state === "active" ? 600 : 400 }}>{p.label}</span>
          </div>
        ))}
      </section>

      {(handoffs.length > 0 || agent.agentId === "coordinator") && agent.role === "coordinator" && (
        <section className="mt-4">
          <h3 className="mb-1.5 text-xs tracking-wide text-muted-foreground uppercase">Leadership / dependencies</h3>
          {handoffs.length === 0 && <div className="text-xs text-muted-foreground">No handoffs yet.</div>}
          {handoffs.map((h, i) => (
            <div key={i} className="flex items-baseline gap-2 py-0.5 text-sm">
              <span>↦</span>
              <span>{lbl(h.from)} → <b>{lbl(h.to)}</b>{h.reason ? ` — ${h.reason}` : ""}</span>
            </div>
          ))}
        </section>
      )}

      <section className="mt-4">
        <h3 className="mb-1.5 text-xs tracking-wide text-muted-foreground uppercase">Activity ({mine.length})</h3>
        <div className="max-h-56 overflow-y-auto">
          {mine.map((e) => (
            <div key={e.eventId} className="flex items-baseline gap-2 py-0.5 text-sm">
              <span className="tabular-nums text-muted-foreground">
                {new Date(e.timestamp).toLocaleTimeString().slice(0, 8)}
              </span>
              <span><span className="font-mono text-[11px]">{e.eventType}</span> {e.summary}</span>
            </div>
          ))}
          {mine.length === 0 && <div className="text-xs text-muted-foreground">No events for this agent yet.</div>}
        </div>
      </section>

      {(agent.agentId === "coordinator" || agent.agentId === "seleric_agent") && finalResponse && (
        <section className="mt-4">
          <h3 className="mb-1.5 text-xs tracking-wide text-muted-foreground uppercase">Final answer</h3>
          <div className="rounded-md border bg-muted p-3 text-sm whitespace-pre-wrap">{finalResponse}</div>
        </section>
      )}

      {debug && (
        <section className="mt-4">
          <h3 className="mb-1.5 text-xs tracking-wide text-muted-foreground uppercase">Debug — raw agent view-model</h3>
          <pre className="m-0 font-mono text-[11px] whitespace-pre-wrap">
            {JSON.stringify(agent, null, 2)}
          </pre>
        </section>
      )}
    </aside>
  );
}

function lbl(a?: string | null): string {
  return (a ?? "?").replace(/_agent$/, "");
}

interface Step { label: string; state: "pending" | "active" | "done"; }

function processFor(agent: OfficeAgent, timeline: SwarmUIEvent[], artifacts: Record<string, number>): Step[] {
  const kinds = new Set(timeline.map((e) => e.eventType));
  const mk = (label: string, done: boolean, active = false): Step => ({
    label, state: done ? "done" : active ? "active" : "pending",
  });

  switch (agent.agentId) {
    case "seleric_agent":
      return [
        mk("Understand the question", kinds.has("mission_started") || kinds.has("mission_created")),
        mk("Fetch metrics", (artifacts.evidence ?? 0) > 0, agent.currentTool === "query_metrics"),
        mk("Analyze", (artifacts.hypothesis ?? 0) > 0 || kinds.has("tool_completed")),
        mk("Diagnose cause", (artifacts.causal ?? 0) > 0),
        mk("Forecast impact", (artifacts.prediction ?? 0) > 0),
        mk("Validate & answer", kinds.has("mission_completed")),
      ];
    case "coordinator":
      return [
        mk("Normalize the query", kinds.has("mission_started")),
        mk("Decompose the problem", kinds.has("decomposition_created")),
        mk("Build the task plan", kinds.has("task_created")),
        mk("Route domain leadership", kinds.has("task_started")),
        mk("Refine on new evidence", kinds.has("decomposition_refined"), agent.status === "thinking"),
        mk("Plan remediation", kinds.has("remediation_created"), agent.status === "working" && kinds.has("skeptic_revise")),
        mk("Synthesize final answer", kinds.has("mission_completed")),
      ];
    case "diagnostic_agent":
      return [
        mk("Load anomaly", (artifacts.anomaly ?? 0) > 0),
        mk("Generate hypotheses", (artifacts.hypothesis ?? 0) > 0),
        mk("Rank hypotheses", (artifacts.hypothesis ?? 0) > 0),
        mk("Test strongest hypothesis", (artifacts.causal ?? 0) > 0, agent.status === "working"),
        mk("Causal validation", (artifacts.causal ?? 0) > 0),
        mk("Contradiction check", kinds.has("skeptic_pass")),
        mk("Report", kinds.has("skeptic_pass")),
      ];
    case "skeptic_agent":
      return [
        mk("Receive claim", kinds.has("skeptic_review_started")),
        mk("Check evidence coverage", kinds.has("skeptic_review_started"), agent.status === "reviewing"),
        mk("Look for contradictions", kinds.has("skeptic_revise") || kinds.has("skeptic_pass")),
        mk("Issue verdict", kinds.has("skeptic_pass") || kinds.has("skeptic_reject")),
      ];
    case "prediction_agent":
      return [
        mk("Resolve target + horizon", agent.status !== "idle"),
        mk("Check applicability / drift", (artifacts.prediction ?? 0) > 0),
        mk("Forecast", (artifacts.prediction ?? 0) > 0, agent.status === "working"),
        mk("Interval + report", (artifacts.prediction ?? 0) > 0),
      ];
    case "strategy_agent":
      return [
        mk("Frame the decision", agent.status !== "idle"),
        mk("Enumerate interventions", (artifacts.strategy ?? 0) > 0, agent.status === "working"),
        mk("Score mechanism fit", (artifacts.strategy ?? 0) > 0),
        mk("Recommend", (artifacts.strategy ?? 0) > 0),
      ];
    default:
      return [
        mk("Assigned", agent.status !== "idle" && agent.status !== "offline"),
        mk("Retrieve evidence", ["retrieving_evidence", "working", "completed"].includes(agent.status)),
        mk("Analyse metrics", ["working", "completed"].includes(agent.status), agent.status === "working"),
        mk("Report finding / handoff", agent.status === "completed" || agent.status === "handoff"),
      ];
  }
}
