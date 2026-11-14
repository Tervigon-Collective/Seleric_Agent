import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { ApprovalCard } from "../components/ApprovalCard";
import { decideApproval } from "../api/phase7";

vi.mock("../api/phase7", () => ({
  searchAll: vi.fn().mockResolvedValue([]),
  decideApproval: vi.fn().mockResolvedValue({ status: "APPROVED" }),
  getRunDiagnostics: vi.fn(),
}));

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
    new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }),
  ));
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("Phase 7 accessibility", () => {
  it("opens Cmd-K search with an accessible focused dialog", async () => {
    await act(async () => { root.render(<App />); });
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
    });
    // The dialog is a lazy chunk rendered into a portal: poll with act() so React can flush its Suspense resolution.
    await vi.waitFor(async () => {
      await act(async () => {});
      expect(document.querySelector('[role="dialog"]')).toBeTruthy();
    }, { timeout: 10000 });
    const dialog = document.querySelector('[role="dialog"]');
    const input = dialog?.querySelector("input");
    const labelledBy = dialog?.getAttribute("aria-labelledby") ?? "";
    expect(document.getElementById(labelledBy)?.textContent).toBe("Search conversations");
    expect(document.activeElement).toBe(input);
    expect(input?.getAttribute("aria-controls")).toBeTruthy();
  });

  it("exposes approval decisions as a labelled keyboard-operable group", async () => {
    await act(async () => {
      root.render(<ApprovalCard value={{
        approval_id: "approval-1", status: "REQUESTED", summary: "Update campaign",
        required_role: "approver", dry_run: true,
      }} />);
    });
    const group = container.querySelector('[role="group"][aria-label="Approval decision"]');
    const buttons = group?.querySelectorAll("button");
    expect(buttons).toHaveLength(2);
    await act(async () => { (buttons?.[0] as HTMLButtonElement).click(); await Promise.resolve(); });
    expect(group?.querySelector('[role="status"]')?.textContent).toBe("APPROVED");
  });

  it("keeps approvals actionable and reports decision failures", async () => {
    vi.mocked(decideApproval).mockRejectedValueOnce(new Error("Approval expired"));
    await act(async () => {
      root.render(<ApprovalCard value={{
        approval_id: "approval-2", status: "REQUESTED", summary: "Update campaign",
      }} />);
    });
    const approve = container.querySelector("button") as HTMLButtonElement;
    await act(async () => { approve.click(); await Promise.resolve(); });
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("Approval expired");
    expect(approve.disabled).toBe(false);
  });
});
