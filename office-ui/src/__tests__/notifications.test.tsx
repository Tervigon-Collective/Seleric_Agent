import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { notificationsApi, type Feed, type NotificationItem, type TenantStatus } from "../api/notifications";
import { ActionDialog, FindingCard, StatusStrip } from "../components/notifications/NotificationCenter";
import { unseenCount, useNotificationsStore } from "../stores/notifications";

const NOW = "2026-11-20T10:00:00Z";

const item = (over: Partial<NotificationItem> = {}): NotificationItem => ({
  finding_id: "live:abc", state: "open", severity: "critical", title: "ROAS below target / break-even", rule_id: "roas_below",
  tier: "performance", platform: "meta", summary: "3-day ROAS 0.43 is below break-even 2.20.",
  narration: "SnugSuit broad is returning ₹0.43 per rupee over 3 days, well under break-even 2.2.", narration_source: "llm",
  narrated_at: NOW, level: "adset", entity_id: "1", entity_name: "TH-477-SNUGSUIT-BROAD",
  path: ["TH-477-SNUGSUIT-PROSP-IN", "TH-477-SNUGSUIT-BROAD"], evidence: { roas_3d: 0.43, be_roas: 2.2 },
  actions: [{ id: "budget_down", title: "Lower budget", risk: "spend", inputs: [] }, { id: "open_agent", title: "Open in Marketing Agent", risk: "link", inputs: [] }],
  playbook_ref: "Meta Ads Playbook §7 Monitoring", impact_inr: 3400, confidence: 0.97, opened_at: "2026-11-20T09:40:00Z",
  updated_at: NOW, resolved_at: null, label: null, ...over,
});

const tenant = (over: Partial<TenantStatus> = {}): TenantStatus => ({
  tenant_id: "th-meta", name: "Tilting Heads (Meta)", brand_id: 20, platform: "meta", enabled: true, status: "ok", age_s: 120, tick_s: 300,
  pulse: { spend: 21000, purchases: 9, purchase_value: 24000, roas: 1.14, cpa: 2333, cpm: 600, ctr_link: 1.5, daily_budget: 36000,
    expected_spend_by_now: 15000, pace: 1.4, active: { campaign: 12, adset: 30, ad: 80 },
    guardrails: { be_roas_platform: 2.2, target_roas: 2.9, allowable_cpa: 1200, source: "auto", working: "", computed_at: null } },
  ...over,
});

const feed = (items: NotificationItem[], over: Partial<Feed> = {}): Feed => ({
  tenants: [tenant()], items, activity: [], config_error: null, actions_enabled: true, server_time: NOW, ...over,
});

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  useNotificationsStore.setState({ feed: null, error: null, open: false, seen: {}, flow: null });
});
afterEach(() => { act(() => root.unmount()); container.remove(); vi.restoreAllMocks(); });

describe("notification store", () => {
  it("counts unseen critical and warning findings, not info or resolved ones", () => {
    const f = feed([item(), item({ finding_id: "b", severity: "info" }), item({ finding_id: "c", state: "resolved" }), item({ finding_id: "d", severity: "warning" })]);
    expect(unseenCount(f, {})).toBe(2);
    expect(unseenCount(f, { "live:abc": true })).toBe(1);
  });

  it("marks everything seen when the panel opens", async () => {
    vi.spyOn(notificationsApi, "feed").mockResolvedValue(feed([item()]));
    useNotificationsStore.setState({ feed: feed([item()]) });
    useNotificationsStore.getState().setOpen(true);
    expect(useNotificationsStore.getState().seen["live:abc"]).toBe(true);
  });

  it("labels optimistically and rolls back on failure", async () => {
    useNotificationsStore.setState({ feed: feed([item()]) });
    vi.spyOn(notificationsApi, "label").mockRejectedValue(new Error("offline"));
    await useNotificationsStore.getState().label(item(), "noise");
    expect(useNotificationsStore.getState().feed?.items[0].label).toBeNull();
    expect(useNotificationsStore.getState().error).toBe("offline");
  });
});

describe("notification center", () => {
  it("shows the pulse against guardrails and how fresh the check is", () => {
    act(() => root.render(<StatusStrip tenant={tenant()} />));
    const text = container.textContent ?? "";
    expect(text).toContain("Live");
    expect(text).toContain("checked 2 min ago");
    expect(text).toContain("140% of pace");
    expect(text).toContain("target 2.90x");
    expect(text).toContain("max ₹1,200");
  });

  it("says when the monitor is failing", () => {
    act(() => root.render(<StatusStrip tenant={tenant({ status: "down", error: "MCP timeout", pulse: undefined })} />));
    expect(container.textContent).toContain("Monitor failing");
    expect(container.textContent).toContain("MCP timeout");
  });

  it("leads with the narration and disables write actions when they are switched off", () => {
    act(() => root.render(<FindingCard item={item()} now={NOW} actionsEnabled={false} />));
    expect(container.textContent).toContain("returning ₹0.43 per rupee");
    expect(container.textContent).toContain("About ₹3,400 at stake · 97% sure");
    const find = (t: string) => [...container.querySelectorAll("button")].find((b) => b.textContent === t);
    expect(find("Lower budget")?.disabled).toBe(true);
    act(() => find("More")!.click()); // the chat action sits under More when a write action leads
    expect(find("Ask Seleric")?.disabled).toBe(false);
  });

  it("previews a change, then approves exactly that request", async () => {
    vi.spyOn(notificationsApi, "preview").mockResolvedValue({
      kind: "write", request_id: "lar_1", action_id: "budget_down", title: "Lower budget", summary: "Lower the campaign daily budget from 1,000 to 800 INR",
      current: { daily_budget: 100000 }, change: { daily_budget: 80000 }, risk: "spend", checks: [{ tool: "meta_campaigns_update_budget", checked: true }],
      expires_at: NOW, enabled: true,
    });
    const approve = vi.spyOn(notificationsApi, "approve").mockResolvedValue({ request_id: "lar_1", status: "executed", error: null, summary: "" });
    vi.spyOn(notificationsApi, "feed").mockResolvedValue(feed([]));
    await act(async () => { await useNotificationsStore.getState().startAction(item(), "budget_down"); });
    act(() => root.render(<ActionDialog />));
    const dialog = document.body;
    expect(dialog.textContent).toContain("Lower the campaign daily budget from 1,000 to 800 INR");
    expect(dialog.textContent).toContain("80,000");
    const approveButton = [...dialog.querySelectorAll("button")].find((b) => b.textContent === "Approve")!;
    await act(async () => { approveButton.click(); });
    expect(approve).toHaveBeenCalledWith("lar_1");
    expect(dialog.textContent).toContain("Done.");
  });

  it("shows the MCP's refusal instead of an Approve button", async () => {
    const { ApiError } = await import("../api/http");
    vi.spyOn(notificationsApi, "preview").mockRejectedValue(new ApiError(422, "Seleric MCP would refuse this: created_only"));
    await act(async () => { await useNotificationsStore.getState().startAction(item(), "budget_down"); });
    act(() => root.render(<ActionDialog />));
    expect(document.body.textContent).toContain("created_only");
    expect([...document.body.querySelectorAll("button")].some((b) => b.textContent === "Approve")).toBe(false);
  });
});
