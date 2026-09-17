import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { conversationsApi } from "../api/conversations";
import { Composer } from "../components/Composer";
import { Transcript } from "../components/Transcript";
import { SelericAssistantRuntimeProvider } from "../providers/SelericAssistantRuntime";
import { useConversationStore } from "../stores/conversation";

vi.mock("../api/runEvents", () => ({
  subscribeToRunEvents: vi.fn(() => vi.fn()),
}));

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  useConversationStore.getState().reset();
  useConversationStore.setState({
    demoMode: false,
    selectedThreadId: "thread-1",
    threads: [],
    messages: {
      "thread-1": [{
        id: "message-1",
        thread_id: "thread-1",
        workspace_id: "workspace-1",
        user_id: "user-1",
        role: "ASSISTANT",
        parts: [{ type: "TABLE", content: [{ metric: "CAC", value: 42 }] }],
        run_id: "run-old",
        parent_message_id: null,
        created_at: "2026-09-17T00:00:00.000Z",
      }],
    },
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

const renderConversation = () => {
  act(() => root.render(
    <SelericAssistantRuntimeProvider>
      <Transcript />
      <Composer />
    </SelericAssistantRuntimeProvider>,
  ));
};

describe("assistant-ui Seleric adapter", () => {
  it("renders assistant-ui thread/message primitives with Seleric typed parts", () => {
    renderConversation();
    expect(container.querySelector(".thread-root")).toBeTruthy();
    expect(container.querySelector('[data-message-id="message-1"]')).toBeTruthy();
    expect(container.querySelector("table")?.textContent).toContain("CAC42");
    expect(container.querySelector('textarea[name="input"]')).toBeTruthy();
  });

  it("submits the assistant-ui composer through the Seleric conversation API", async () => {
    const submitMessage = vi.spyOn(conversationsApi, "submitMessage").mockResolvedValue({
      message_id: "message-new",
      run_id: "run-new",
      mission_id: "mission-new",
    });
    renderConversation();

    const input = container.querySelector('textarea[aria-label="Message"]') as HTMLTextAreaElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
    act(() => {
      setter?.call(input, "Investigate CAC");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
      (container.querySelector('button[aria-label="Send message"]') as HTMLButtonElement).click();
      await Promise.resolve();
    });

    expect(submitMessage).toHaveBeenCalledWith("thread-1", expect.objectContaining({
      parts: [expect.objectContaining({ type: "TEXT", content: "Investigate CAC" })],
      parent_message_id: "message-1",
    }));
  });
});
