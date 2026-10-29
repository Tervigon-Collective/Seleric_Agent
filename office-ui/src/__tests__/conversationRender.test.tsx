import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import App from "../App";
import { SafeContent } from "../components/SafeContent";
import { DetailPanel } from "../components/DetailPanel";
import { useOffice } from "../store";
import { useConversationStore } from "../stores/conversation";
import { useShellStore } from "../stores/shell";

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

describe("conversation shell rendering and accessibility", () => {
  it("exposes navigation, transcript, details, and a labeled composer", () => {
    act(() => root.render(<App />));
    expect(container.querySelector("header")).toBeTruthy();
    expect(container.querySelector('[aria-label="Conversations"]')).toBeTruthy();
    expect(container.querySelector('[aria-label="Conversation transcript"]')).toBeTruthy();
    expect(container.querySelector('[aria-label="Conversation details"]')).toBeTruthy();
    expect(container.querySelector('textarea[aria-label="Message"]')).toBeTruthy();
    expect(container.querySelector('button[aria-label="Send message"]')).toBeTruthy();
  });

  it("renders unsafe markup as text and safe links as anchors", () => {
    act(() => root.render(<SafeContent text={'<img src=x onerror=alert(1)> https://example.com'} />));
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img");
    expect(container.querySelector("a")?.getAttribute("rel")).toBe("noreferrer");
  });

  it("does not show stale run events when no conversation is selected", () => {
    useConversationStore.setState({ selectedThreadId: null, threads: [] });
    useShellStore.setState({ detailsOpen: true, detailTab: "Debug" });
    useOffice.getState().reset();
    useOffice.getState().ingestEvent({
      eventId: "stale-event",
      seq: 1,
      timestamp: "2026-09-17T10:00:00Z",
      missionId: "old-mission",
      eventType: "run_completed",
      summary: "Stale activity",
    });
    act(() => root.render(<App />));
    expect(container.textContent).toContain("Run a question to see loop timing");
    expect(container.textContent).not.toContain("Stale activity");
  });

  it("keeps Debug, Causal, and Memory and supports arrow-key tab navigation", async () => {
    useConversationStore.setState({
      selectedThreadId: "t1",
      threads: [{
        id: "t1", workspace_id: "w", owner_user_id: "u", project_id: null,
        title: "Research", status: "ACTIVE", metadata: {}, created_at: "now", updated_at: "now",
      }],
      messages: { t1: [] },
    });
    useShellStore.setState({ detailsOpen: true, detailTab: "Debug" });
    act(() => root.render(<DetailPanel />));
    const labels = [...container.querySelectorAll('[role="tab"]')].map((node) => node.textContent);
    expect(labels).toEqual(["Debug", "Causal", "Memory"]);
    const debug = container.querySelector('[role="tab"][aria-selected="true"]') as HTMLButtonElement;
    // Radix tabs move focus (and activate) on the next tick, and activate a pressed tab on mousedown.
    act(() => debug.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("Causal");
    const memory = [...container.querySelectorAll('[role="tab"]')].find((node) => node.textContent === "Memory") as HTMLButtonElement;
    act(() => memory.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, button: 0, ctrlKey: false })));
    expect(container.textContent).toContain("Saved memories");
  });

  it("opens the Debug tab by default in the slim inspector", () => {
    useConversationStore.setState({
      selectedThreadId: "t1",
      threads: [{
        id: "t1", workspace_id: "w", owner_user_id: "u", project_id: null,
        title: "Sales", status: "ACTIVE", metadata: {},
        created_at: "now", updated_at: "now",
      }],
      messages: { t1: [] },
    });
    useShellStore.setState({ detailsOpen: true, detailTab: "Debug" });
    act(() => root.render(<DetailPanel />));
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("Debug");
    expect(container.textContent).toMatch(/Debug|Run a question|loop timing|Loading trace/);
  });

  it("provides functional conversation and detail panel controls", () => {
    act(() => root.render(<App />));
    const sidebarToggle = container.querySelector('[aria-label="Toggle conversations"]') as HTMLButtonElement;
    const detailsToggle = container.querySelector('[aria-label="Toggle conversation details"]') as HTMLButtonElement;
    expect(sidebarToggle.getAttribute("aria-expanded")).toBe("true");
    expect(detailsToggle.getAttribute("aria-expanded")).toBe("true");
    act(() => sidebarToggle.click());
    expect(container.querySelector('[aria-label="Conversations"]')).toBeNull();
    act(() => detailsToggle.click());
    expect(container.querySelector('[aria-label="Conversation details"]')).toBeNull();
  });
});
