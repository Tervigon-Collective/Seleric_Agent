/**
 * Truthful run-phase projection for the waiting state.
 *
 * The stepper below ("Understand → Retrieve → Answer") is derived only from
 * backend events already observed in the timeline — a phase is never marked
 * complete speculatively. Stage names come from `agent.stage` payloads
 * ("understand" → "prefetch" → "answer"); tool and answering events imply the
 * earlier phases even when a route skips explicit stage events.
 */

import type { SwarmUIEvent } from "../types";

export type PhaseState = "done" | "active" | "pending";

export interface RunPhase {
  key: "understand" | "retrieve" | "answer";
  label: string;
  state: PhaseState;
}

const TERMINAL = new Set(["run_completed", "run_failed", "run_cancelled", "answer_completed"]);

export function deriveRunPhases(timeline: SwarmUIEvent[]): RunPhase[] {
  const stages = new Set<string>();
  let sawTool = false;
  let sawAnswering = false;
  let sawTerminal = false;
  for (const event of timeline) {
    const type = typeof event.eventType === "string" ? event.eventType : "";
    const meta = event.metadata && typeof event.metadata === "object"
      ? event.metadata as Record<string, unknown>
      : {};
    if (type === "agent_stage" && typeof meta.stage === "string") stages.add(meta.stage);
    if (type === "agent_tool_started" || type === "agent_tool_completed" || type === "agent_tool_revising") {
      sawTool = true;
    }
    if (type === "agent_answering") sawAnswering = true;
    if (TERMINAL.has(type)) sawTerminal = true;
  }

  const understandDone = stages.has("prefetch") || stages.has("answer") || sawTool || sawAnswering || sawTerminal;
  const retrieveDone = stages.has("answer") || sawAnswering || sawTerminal;
  const answerDone = sawTerminal;

  const done: Record<RunPhase["key"], boolean> = {
    understand: understandDone,
    retrieve: retrieveDone,
    answer: answerDone,
  };
  const keys: RunPhase["key"][] = ["understand", "retrieve", "answer"];
  const labels: Record<RunPhase["key"], string> = {
    understand: "Understand",
    retrieve: "Retrieve",
    answer: "Answer",
  };
  const firstOpen = keys.find((key) => !done[key]);
  return keys.map((key) => ({
    key,
    label: labels[key],
    state: done[key] ? "done" : key === firstOpen ? "active" : "pending",
  }));
}
