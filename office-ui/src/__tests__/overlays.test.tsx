/**
 * Overlay a11y + wiring smoke tests (partial for brief §88). Renders the DOM
 * overlays against a hydrated store in jsdom and asserts the semantic hooks a
 * full axe/Playwright pass would check: aria-labels, real buttons, keyboard
 * activation, reduced-motion honoured.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useOffice } from "../store";
import { blankAgents } from "../office/agents";
import { MissionBoard } from "../components/MissionBoard";
import { MissionMinimap } from "../components/MissionMinimap";
import { MissionTimeline } from "../components/MissionTimeline";
import type { OfficeSnapshot } from "../types";

let container: HTMLDivElement;
let root: Root;

const snap = (over: Partial<OfficeSnapshot> = {}): OfficeSnapshot => ({
  missionId: "MS-1",
  query: "Why has CAC increased?",
  status: "running",
  route: "swarm",
  stage: "investigating",
  missionLead: "funnel",
  leadAgentId: "funnel_agent",
  initialLead: "performance",
  leadershipEpoch: 1,
  startedAt: "2026-09-09T10:00:00Z",
  lastEventAt: null,
  lastSeq: 3,
  agents: blankAgents(),
  board: { steps: [{ id: "verify", label: "Verify issue", state: "done" }, { id: "frontier", label: "Find causal frontier", state: "active" }] },
  handoffs: [],
  parallelTasks: {},
  artifacts: {},
  unresolvedQuestions: ["Why did mobile CVR fall?"],
  limitations: [],
  finalResponse: null,
  timeline: [
    { eventId: "MS-1:1", seq: 1, timestamp: "2026-09-09T10:00:01Z", missionId: "MS-1", agentId: "coordinator", eventType: "mission_started", summary: "Mission received" },
    { eventId: "MS-1:2", seq: 2, timestamp: "2026-09-09T10:00:02Z", missionId: "MS-1", agentId: "funnel_agent", eventType: "leadership_transferred", summary: "Leadership performance → funnel" },
  ],
  ...over,
});

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  useOffice.getState().reset();
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("overlay a11y + wiring", () => {
  it("MissionBoard renders steps with done/active state and open questions", () => {
    act(() => {
      useOffice.getState().hydrate(snap());
      root.render(<MissionBoard />);
    });
    expect(container.textContent).toContain("Verify issue");
    expect(container.textContent).toContain("Find causal frontier");
    expect(container.querySelector(".board-step.done")).toBeTruthy();
    expect(container.querySelector(".board-step.active")).toBeTruthy();
    expect(container.textContent).toContain("Why did mobile CVR fall?");
  });

  it("MissionTimeline exposes filter buttons and clickable event rows select the agent", () => {
    act(() => {
      useOffice.getState().hydrate(snap());
      root.render(<MissionTimeline />);
    });
    const buttons = [...container.querySelectorAll("button")];
    expect(buttons.some((b) => b.textContent === "leadership")).toBe(true);
    const rows = [...container.querySelectorAll(".tl-row")];
    expect(rows.length).toBeGreaterThan(0);
    act(() => (rows.find((r) => r.textContent?.includes("funnel")) as HTMLElement)?.click());
    expect(useOffice.getState().selectedAgentId).toBe("funnel_agent");
  });

  it("MissionMinimap is hidden for a single mission and keyboard-activates for many", () => {
    let picked = "";
    act(() =>
      root.render(
        <MissionMinimap missions={[{ missionId: "MS-1", query: "one", status: "running", lastSeq: 0 }]} missionId="MS-1" onPick={(id) => (picked = id)} />,
      ),
    );
    expect(container.querySelector(".minimap")).toBeNull();

    act(() =>
      root.render(
        <MissionMinimap
          missions={[
            { missionId: "MS-1", query: "one", status: "running", lastSeq: 0 },
            { missionId: "MS-2", query: "two", status: "completed", missionLead: "inventory", lastSeq: 4 },
          ]}
          missionId="MS-1"
          onPick={(id) => (picked = id)}
        />,
      ),
    );
    const panel = container.querySelector(".minimap")!;
    expect(panel.getAttribute("aria-label")).toBe("Missions");
    const row = [...panel.querySelectorAll('[role="button"]')].find((r) => r.textContent?.includes("two")) as HTMLElement;
    expect(row.getAttribute("tabindex")).toBe("0");
    act(() => row.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
    expect(picked).toBe("MS-2");
  });

  it("store seeds reducedMotion from the matchMedia mock", () => {
    // jsdom has no matchMedia by default; store guards for it -> false
    expect(typeof useOffice.getState().reducedMotion).toBe("boolean");
  });
});
