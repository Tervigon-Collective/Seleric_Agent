import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import App from "../App";
import { SafeContent } from "../components/SafeContent";
import { useConversationStore } from "../stores/conversation";
import { useShellStore } from "../stores/shell";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  useShellStore.getState().setWorkspace("conversation");
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
    expect(container.querySelector('[aria-label="Conversation threads"]')).toBeTruthy();
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
});
