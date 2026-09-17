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
  useShellStore.setState({ sidebarOpen: true, detailsOpen: true, detailTab: "Activity" });
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

  it("does not show activity when no conversation is selected", () => {
    useConversationStore.setState({ selectedThreadId: null, threads: [] });
    useOffice.getState().ingestEvent({
      eventId: "stale-event",
      seq: 1,
      timestamp: "2026-09-17T10:00:00Z",
      missionId: "old-mission",
      eventType: "run_completed",
      summary: "Stale activity",
    });
    act(() => root.render(<App />));
    expect(container.textContent).toContain("Select or start a conversation");
    expect(container.textContent).not.toContain("Stale activity");
  });

  it("renders thread sources and supports arrow-key tab navigation", () => {
    useConversationStore.setState({
      selectedThreadId: "t1",
      threads: [{
        id: "t1", workspace_id: "w", owner_user_id: "u", project_id: null,
        title: "Research", status: "ACTIVE", metadata: {}, created_at: "now", updated_at: "now",
      }],
      messages: {
        t1: [{
          id: "m1", thread_id: "t1", workspace_id: "w", user_id: null,
          role: "ASSISTANT", run_id: "r1", parent_message_id: null, created_at: "now",
          parts: [{ type: "SOURCE", content: {
            evidence_id: "EV-1", title: "Quarterly report",
            url: "https://example.com/report", excerpt: "Revenue increased.",
          } }],
        }],
      },
    });
    act(() => root.render(<DetailPanel />));
    const activity = container.querySelector('[role="tab"][aria-selected="true"]') as HTMLButtonElement;
    act(() => activity.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
    expect(container.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("Context");
    act(() => (container.querySelector("#detail-tab-sources") as HTMLButtonElement).click());
    expect(container.textContent).toContain("Quarterly report");
    expect(container.textContent).toContain("Revenue increased.");
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
