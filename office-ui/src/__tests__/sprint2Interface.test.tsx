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
  useShellStore.setState({ sidebarOpen: true, detailsOpen: true, detailTab: "Debug" });
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
    const rows = [...container.querySelectorAll('[data-slot="thread-row"]')];
    const regular = rows.filter((row) => !row.closest('[data-slot="collapsible-content"]'));
    expect(regular.some((row) => row.textContent?.includes("GOLDEN"))).toBe(false);
    // Evaluation runs sit in their own collapsed group, opened on demand.
    const trigger = [...container.querySelectorAll("button")].find((b) => b.textContent?.includes("Evaluation runs (1)")) as HTMLButtonElement;
    expect(trigger).toBeTruthy();
    act(() => trigger.click());
    const evalRows = [...container.querySelectorAll('[data-slot="collapsible-content"] [data-slot="thread-row"]')];
    expect(evalRows.map((row) => row.textContent).join(" ")).toContain("GOLDEN Q18");
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

  it("keeps sequence numbers out of visible phase summaries", () => {
    const phases = groupActivityPhases([
      event({ eventId: "e1", seq: 11, missionId: "m1", agentId: "retriever_agent", summary: "Fetching metrics" }),
      event({ eventId: "e2", seq: 12, missionId: "m1", agentId: "retriever_agent", summary: "Metrics ready" }),
    ], "swarm");
    expect(phases).toHaveLength(1);
    expect(phases[0].agent).toBe("retriever");
    expect(phases[0].count).toBe(2);
    expect(phases[0].events.map((row) => row.summary)).toEqual(["Metrics ready", "Fetching metrics"]);
    expect(phases[0].events.every((row) => !row.summary.includes("#"))).toBe(true);
  });
});

describe("compact thread context", () => {
  it("shows a single-line context instead of a large duplicate heading", () => {
    useConversationStore.setState({
      selectedThreadId: "t1",
      threads: [thread("t1", "Why did ROAS decline?")],
      messages: {
        t1: [{
          id: "m1", thread_id: "t1", workspace_id: "w", user_id: "u", role: "USER",
          parts: [{ type: "TEXT", content: "Why did ROAS decline?" }],
          run_id: null, parent_message_id: null, created_at: "now",
        }],
      },
    });
    act(() => root.render(<App />));
    const context = container.querySelector('[data-slot="thread-context"]');
    expect(context?.textContent).toContain("Why did ROAS decline?");
    expect(context?.querySelector("h2")).toBeNull();
  });

  it("hides the context line on empty threads", () => {
    useConversationStore.setState({
      selectedThreadId: "t1",
      threads: [thread("t1", null)],
      messages: { t1: [] },
    });
    act(() => root.render(<App />));
    expect(container.querySelector('[data-slot="thread-context"]')).toBeNull();
  });
});
