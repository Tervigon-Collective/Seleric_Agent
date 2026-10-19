import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useOffice } from "../store";
import { asText, toOfficeEvent, useConversationStore } from "../stores/conversation";
import { groupActivityPhases } from "../components/DetailPanel";
import { AppErrorBoundary, PanelErrorBoundary } from "../components/ErrorBoundary";
import type { ActivityEvent } from "../api/contracts";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  useConversationStore.getState().reset();
  useOffice.getState().reset();
  useConversationStore.setState({
    demoMode: true,
    selectedThreadId: "t1",
    threads: [],
    messages: { t1: [] },
  });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const baseEvent = (overrides: Record<string, unknown> = {}): ActivityEvent => ({
  id: "e1",
  thread_id: "t1",
  workspace_id: "w1",
  run_id: "run-1",
  sequence: 1,
  event_type: "agent.stage",
  actor_type: null,
  actor_id: "retriever_agent",
  title: null,
  summary: "Fetching metrics",
  evidence_ids: [],
  payload: { mission_id: "mission-1" },
  metadata: {},
  started_at: null,
  completed_at: null,
  duration_ms: null,
  created_at: "2026-10-09T10:00:00Z",
  ...overrides,
} as ActivityEvent);

describe("untrusted event payloads never break state or rendering", () => {
  it("coerces a structured (object) summary to a fallback instead of passing it through", () => {
    const office = toOfficeEvent(baseEvent({ summary: { failed: true, reason: "boom" } }));
    expect(typeof office.summary).toBe("string");
    expect(office.summary).toBe("agent stage");
  });

  it("survives a null event_type, null payload, and null evidence list", () => {
    const office = toOfficeEvent(
      baseEvent({ event_type: null, payload: null, evidence_ids: null, summary: null, title: null }),
    );
    expect(office.eventType).toBe("unknown_event");
    expect(typeof office.summary).toBe("string");
    expect(office.artifactRefs).toEqual([]);
  });

  it("coerces a non-string actor id and a non-finite sequence to safe values", () => {
    const office = toOfficeEvent(baseEvent({ actor_id: 42, sequence: Number.NaN }));
    expect(office.agentId).toBe("42");
    expect(office.seq).toBe(0);
  });

  it("keeps progress renderable when an error path emits a structured summary", () => {
    const { applyRunEvent } = useConversationStore.getState();
    expect(() =>
      applyRunEvent(baseEvent({ summary: { error: "artifact is not evidence" } })),
    ).not.toThrow();
    // No object may reach React children: progress stays null, timeline holds text.
    expect(useConversationStore.getState().progress).toBeNull();
    const timeline = useOffice.getState().timeline;
    expect(timeline).toHaveLength(1);
    expect(typeof timeline[0].summary).toBe("string");
  });

  it("ignores a null event_type in the live handler instead of throwing", () => {
    const { applyRunEvent } = useConversationStore.getState();
    expect(() => applyRunEvent(baseEvent({ event_type: null }))).not.toThrow();
    expect(useOffice.getState().timeline[0].eventType).toBe("unknown_event");
  });

  it("groups junk timeline rows without throwing and without seq noise", () => {
    const phases = groupActivityPhases(
      [
        {
          eventId: "x1", seq: 1, timestamp: "", missionId: "m", agentId: 7,
          eventType: null, summary: { weird: true },
        },
        { eventId: "x2", seq: 2, timestamp: "", missionId: "m", summary: "ok" },
      ] as never,
      "swarm",
    );
    expect(phases.length).toBeGreaterThan(0);
    for (const phase of phases) {
      expect(typeof phase.headline).toBe("string");
      expect(phase.headline).not.toContain("#");
    }
  });

  it("asText only passes strings and finite numbers through", () => {
    expect(asText("hi")).toBe("hi");
    expect(asText(12)).toBe("12");
    expect(asText({})).toBeNull();
    expect(asText(null)).toBeNull();
    expect(asText(Number.NaN)).toBeNull();
  });
});

describe("error boundaries contain panel crashes", () => {
  const Boom = () => {
    throw new Error("malformed render");
  };

  it("panel boundary shows fallback while sibling icons stay mounted", () => {
    act(() => root.render(
      <div>
        <button aria-label=" intact control">
          <svg><line x1="4" y1="6" x2="20" y2="6" /></svg>
        </button>
        <PanelErrorBoundary name="test">
          <Boom />
        </PanelErrorBoundary>
      </div>,
    ));
    expect(container.querySelector(".panel-crash")).toBeTruthy();
    expect(container.querySelector("svg line")).toBeTruthy();
  });

  it("app boundary offers a reload instead of a blank page", () => {
    act(() => root.render(
      <AppErrorBoundary>
        <Boom />
      </AppErrorBoundary>,
    ));
    expect(container.querySelector(".app-crash")).toBeTruthy();
    expect(container.textContent).toContain("Reload Seleric");
  });
});
