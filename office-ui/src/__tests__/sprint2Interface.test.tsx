import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import App from "../App";
import { DetailPanel, groupActivityPhases } from "../components/DetailPanel";
import { ThreadSidebar, isEvalThread } from "../components/ThreadSidebar";
import type { SwarmUIEvent } from "../types";
import { useConversationStore } from "../stores/conversation";
import { useShellStore } from "../stores/shell";
import { useOffice } from "../store";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  useShellStore.getState().setWorkspace("conversation");
  useShellStore.setState({ sidebarOpen: true, detailsOpen: true, detailTab: "Activity" });
  useConversationStore.setState({ demoMode: true, error: null });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function thread(id: string, title: string | null) {
  return {
    id, workspace_id: "w", owner_user_id: "u", project_id: null,
    title, status: "ACTIVE" as const, metadata: {}, created_at: "now", updated_at: "now",
  };
}

function event(partial: Partial<SwarmUIEvent> & { eventId: string; seq: number }): SwarmUIEvent {
  return {
    timestamp: "2026-10-09T10:00:00Z",
    missionId: "m1",
    eventType: "task_started",
    ...partial,
  };
}

describe("eval thread separation", () => {
  it("classifies GOLDEN titles as evaluation runs", () => {
    expect(isEvalThread("GOLDEN Q18")).toBe(true);
    expect(isEvalThread("golden q3 daily series")).toBe(true);
    expect(isEvalThread("Why did ROAS decline?")).toBe(false);
    expect(isEvalThread(null)).toBe(false);
    expect(isEvalThread("Golden retriever trends")).toBe(false);
  });

  it("renders eval runs apart from the date-grouped history", () => {
    useConversationStore.setState({
      selectedThreadId: "u1",
      threads: [thread("u1", "Why did ROAS decline?"), thread("g18", "GOLDEN Q18")],
      messages: { u1: [], g18: [] },
    });
    act(() => root.render(<ThreadSidebar />));
    const nav = container.querySelector('[aria-label="Conversation list"]');
    expect(nav?.textContent).toContain("Why did ROAS decline?");
    // The eval thread must not appear as a regular date-grouped row.
    const dateGroups = [...container.querySelectorAll(".thread-group-label")].filter(
      (el) => el.tagName === "DIV",
    );
    expect(dateGroups.some((el) => el.textContent?.includes("GOLDEN"))).toBe(false);
    const evalGroup = container.querySelector(".thread-eval-group");
    expect(evalGroup?.textContent).toContain("Evaluation runs (1)");
    expect(evalGroup?.textContent).toContain("GOLDEN Q18");
  });
});

describe("activity phases", () => {
  it("collapses consecutive same-agent events into one phase, newest first", () => {
    const phases = groupActivityPhases([
      event({ eventId: "e1", seq: 1, agentId: "retriever_agent", eventType: "task_started", summary: "Fetching metrics" }),
      event({ eventId: "e2", seq: 2, agentId: "retriever_agent", eventType: "task_progress", summary: "Fetching metrics" }),
      event({ eventId: "e3", seq: 3, agentId: "retriever_agent", eventType: "task_progress", summary: "Metrics ready" }),
      event({ eventId: "e4", seq: 4, agentId: "coordinator", eventType: "task_started", summary: "Synthesizing answer" }),
    ], "swarm");
    expect(phases).toHaveLength(2);
    expect(phases[0].agent).toBe("coordinator");
    expect(phases[0].headline).toBe("Synthesizing answer");
    expect(phases[1].agent).toBe("retriever");
    expect(phases[1].count).toBe(3);
    // Consecutive duplicate summaries merge; seq numbers stay out of visible text.
    expect(phases[1].events.map((e) => e.summary)).toEqual(["Metrics ready", "Fetching metrics"]);
  });

  it("renders phases without raw sequence numbers in the panel", () => {
    useConversationStore.setState({
      selectedThreadId: "t1",
      threads: [thread("t1", "Sales")],
      messages: { t1: [] },
    });
    useOffice.getState().hydrate({
      missionId: "m1", query: "Sales", status: "working", route: "swarm", stage: "working",
      leadershipEpoch: 0, lastSeq: 2, agents: [], board: { steps: [] },
      handoffs: [], artifacts: {}, unresolvedQuestions: [], limitations: [],
      finalResponse: null, timeline: [],
    });
    for (const e of [
      event({ eventId: "e1", seq: 11, missionId: "m1", agentId: "retriever_agent", summary: "Fetching metrics" }),
      event({ eventId: "e2", seq: 12, missionId: "m1", agentId: "retriever_agent", summary: "Metrics ready" }),
    ]) {
      useOffice.getState().ingestEvent(e);
    }
    act(() => root.render(<DetailPanel />));
    expect(container.textContent).toContain("retriever");
    expect(container.textContent).toContain("2 steps");
    expect(container.textContent).not.toContain("#11");
    // Raw events stay expandable for diagnosis.
    expect(container.querySelector(".activity-phase ul")).toBeTruthy();
  });
});

describe("compact thread context", () => {
  it("shows a single-line context instead of a large duplicate heading", () => {
    useConversationStore.setState({
      selectedThreadId: "t1",
      threads: [thread("t1", "Why did ROAS decline?")],
      messages: { t1: [] },
    });
    act(() => root.render(<App />));
    const context = container.querySelector(".thread-context");
    expect(context?.textContent).toContain("Why did ROAS decline?");
    expect(context?.querySelector("h2")).toBeNull();
  });
});
