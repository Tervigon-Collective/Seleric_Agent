import { act } from "react";
import type { DataMessagePartProps } from "@assistant-ui/react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { SelericFeedback } from "../components/Transcript";
import { conversationsApi } from "../api/conversations";
import { useConversationStore } from "../stores/conversation";
import { useShellStore } from "../stores/shell";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  useConversationStore.getState().reset();
  useShellStore.getState().setWorkspace("conversation");
  useShellStore.setState({ sidebarOpen: true, detailsOpen: false, detailTab: "Evidence" });
  useConversationStore.setState({ demoMode: true, error: null });
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

const feedbackProps = (data: { messageId: string; threadId: string; runId: string; final: boolean }) =>
  ({ data, type: "data-seleric-feedback", name: "seleric-feedback" }) as unknown as DataMessagePartProps;

const thread = (id: string, title: string | null) => ({
  id, workspace_id: "w", owner_user_id: "u", project_id: null,
  title, status: "ACTIVE" as const, metadata: {}, created_at: "now", updated_at: "now",
});

describe("answer feedback", () => {
  const data = { messageId: "m1", threadId: "t1", runId: "r1", final: true };

  it("renders nothing for streaming drafts with transient ids", () => {
    act(() => root.render(<SelericFeedback {...feedbackProps({ ...data, final: false })} />));
    expect(container.querySelector(".message-feedback")).toBeNull();
  });

  it("restores the saved vote and submits a new one", async () => {
    vi.spyOn(conversationsApi, "getFeedback").mockResolvedValue({
      id: "f1", workspace_id: "w", owner_user_id: "u", thread_id: "t1",
      message_id: "m1", run_id: "r1", rating: "up", note: "",
      supersedes_id: null, created_at: "now",
    });
    const submit = vi.spyOn(conversationsApi, "submitFeedback").mockResolvedValue({
      id: "f2", workspace_id: "w", owner_user_id: "u", thread_id: "t1",
      message_id: "m1", run_id: "r1", rating: "down", note: "",
      supersedes_id: "f1", created_at: "now",
    });
    act(() => root.render(<SelericFeedback {...feedbackProps(data)} />));
    await act(async () => undefined);
    const up = container.querySelector('[aria-label="Mark answer helpful"]') as HTMLButtonElement;
    expect(up.getAttribute("aria-pressed")).toBe("true");
    const down = container.querySelector(
      '[aria-label="Report a problem with this answer"]',
    ) as HTMLButtonElement;
    act(() => down.click());
    expect(submit).toHaveBeenCalledWith("t1", "m1", { rating: "down", note: "", runId: "r1" });
  });

  it("sends an optional note with a down vote", async () => {
    vi.spyOn(conversationsApi, "getFeedback").mockResolvedValue(null);
    const submit = vi.spyOn(conversationsApi, "submitFeedback").mockResolvedValue({
      id: "f2", workspace_id: "w", owner_user_id: "u", thread_id: "t1",
      message_id: "m1", run_id: "r1", rating: "down", note: "wrong metric",
      supersedes_id: null, created_at: "now",
    });
    act(() => root.render(<SelericFeedback {...feedbackProps(data)} />));
    await act(async () => undefined);
    // First down-vote records the bare verdict…
    act(() => (container.querySelector(
      '[aria-label="Report a problem with this answer"]',
    ) as HTMLButtonElement).click());
    expect(submit).toHaveBeenCalledTimes(1);
    await act(async () => undefined);
    // …then offers a detail field for the follow-up report.
    act(() => (container.querySelector(
      '[aria-label="Report a problem with this answer"]',
    ) as HTMLButtonElement).click());
    const input = container.querySelector(".feedback-note input") as HTMLInputElement;
    expect(input).toBeTruthy();
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
      setter?.call(input, "wrong metric");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    act(() => (container.querySelector(".feedback-note") as HTMLFormElement).requestSubmit());
    expect(submit).toHaveBeenLastCalledWith(
      "t1", "m1", { rating: "down", note: "wrong metric", runId: "r1" },
    );
  });
});

describe("sharing and keyboard polish", () => {
  it("offers copy-link only when a conversation is selected", () => {
    useConversationStore.setState({ selectedThreadId: null, threads: [] });
    act(() => root.render(<App />));
    expect(container.querySelector('[aria-label="Copy link to this conversation"]')).toBeNull();
    expect(container.querySelector('[aria-label="Link copied"]')).toBeNull();

    useConversationStore.setState({
      selectedThreadId: "t1",
      threads: [thread("t1", "Sales")],
      messages: { t1: [] },
    });
    act(() => root.render(<App />));
    expect(container.querySelector('[aria-label="Copy link to this conversation"]')).toBeTruthy();
  });

  it("Escape folds the inspector, then the sidebar", () => {
    useShellStore.setState({ sidebarOpen: true, detailsOpen: true });
    act(() => root.render(<App />));
    expect(container.querySelector('[aria-label="Conversation details"]')).toBeTruthy();
    act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(container.querySelector('[aria-label="Conversation details"]')).toBeNull();
    expect(container.querySelector('[aria-label="Conversations"]')).toBeTruthy();
    act(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(container.querySelector('[aria-label="Conversations"]')).toBeNull();
  });
});
