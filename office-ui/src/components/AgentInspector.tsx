import { useMemo } from "react";
import { useOffice } from "../store";
import { SPEC_BY_ID } from "../office/agents";
import { STATUS_TOKEN } from "../office/stateMachine";
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
    <aside className="inspector" aria-label={`${agent.name} inspector`}>
      <button className="btn close" onClick={() => select(null)}>✕</button>
      <h2>{spec?.glyph} {agent.name}</h2>
      <span className="status-tag" style={{ background: TOKEN_VAR[token] }}>{agent.status.replace(/_/g, " ")}</span>

      <section>
        <h3>Overview</h3>
        <div className="kv">
          <span>Role</span><div>{agent.role}{agent.domain ? ` · ${agent.domain}` : ""}</div>
          <span>Mission lead</span><div>{agent.missionLead ? "yes" : "no"}</div>
          {agent.lastEventAt && (<><span>Last event</span><div>{new Date(agent.lastEventAt).toLocaleTimeString()}</div></>)}
          {agent.error && (<><span>Blocked</span><div style={{ color: "var(--st-failed)" }}>{agent.error}</div></>)}
        </div>
      </section>

      <section>
        <h3>Current task</h3>
        <div className="kv">
          {agent.subquestion && (<><span>Sub-question</span><div>{agent.subquestion}</div></>)}
          {agent.task && (<><span>Task</span><div>{agent.task}</div></>)}
          {agent.currentAction && (<><span>Action</span><div>{agent.currentAction}</div></>)}
          {agent.currentTool && (<><span>Tool</span><div>{agent.currentTool}</div></>)}
          {agent.currentHypothesis && (<><span>Hypothesis</span><div>{agent.currentHypothesis}</div></>)}
          {agent.progress && (<><span>Progress</span><div>{agent.progress.current}/{agent.progress.total} {agent.progress.label}</div></>)}
          {!agent.currentAction && !agent.task && !agent.subquestion && <div style={{ color: "var(--text-dim)" }}>Idle at home desk.</div>}
        </div>
        {parallel.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <div style={{ fontSize: 11, color: "var(--text-dim)", marginBottom: 4 }}>Parallel helpers</div>
            {parallel.map((t, i) => (
              <span key={`${t.label}-${i}`} className="chip">{t.label} · {t.status}</span>
            ))}
          </div>
        )}
      </section>

      <section>
        <h3>Process</h3>
        {proc.map((p) => (
          <div className="process-step" key={p.label}>
            <span style={{ color: p.state === "done" ? "var(--st-done)" : p.state === "active" ? "var(--st-working)" : "var(--text-dim)" }}>
              {p.state === "done" ? "✓" : p.state === "active" ? "●" : "○"}
            </span>
            <span style={{ fontWeight: p.state === "active" ? 600 : 400 }}>{p.label}</span>
          </div>
        ))}
      </section>

      {(agent.role === "coordinator" || handoffs.length > 0) && (
        <section>
          <h3>Leadership / dependencies</h3>
          {handoffs.length === 0 && <div style={{ color: "var(--text-dim)", fontSize: 12 }}>No handoffs yet.</div>}
          {handoffs.map((h, i) => (
            <div key={i} className="process-step">
              <span>↦</span>
              <span>{lbl(h.from)} → <b>{lbl(h.to)}</b>{h.reason ? ` — ${h.reason}` : ""}</span>
            </div>
          ))}
        </section>
      )}

      <section>
        <h3>Activity ({mine.length})</h3>
        <div style={{ maxHeight: 220, overflowY: "auto" }}>
          {mine.map((e) => (
            <div key={e.eventId} className="process-step">
              <span className="tl-time" style={{ color: "var(--text-dim)", fontVariantNumeric: "tabular-nums" }}>
                {new Date(e.timestamp).toLocaleTimeString().slice(0, 8)}
              </span>
              <span><span className="ev-type" style={{ fontFamily: "var(--mono)", fontSize: 11 }}>{e.eventType}</span> {e.summary}</span>
            </div>
          ))}
          {mine.length === 0 && <div style={{ color: "var(--text-dim)", fontSize: 12 }}>No events for this agent yet.</div>}
        </div>
      </section>

      {agent.agentId === "coordinator" && finalResponse && (
        <section>
          <h3>Final answer</h3>
          <div className="final-answer">{finalResponse}</div>
        </section>
      )}

      {debug && (
        <section>
          <h3>Debug — raw agent view-model</h3>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 11, fontFamily: "var(--mono)" }}>
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
