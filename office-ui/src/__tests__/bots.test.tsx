import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { botsApi, type Bot, type BotActivity, type BotRun } from "../api/bots";
import { ActivityTimeline, BotsWorkspace, bandScale, cronText, groupByRun, historyFor, inboxGroups, itemsOf, splitAnswer, stepLabel, stepText, triggerText } from "../components/bots/BotsWorkspace";
import { BotReports } from "../components/notifications/NotificationCenter";
import { useShellStore } from "../stores/shell";
import { readRoute, writeRoute } from "../routing";
import { useBotsStore } from "../stores/bots";

const bot: Bot = {
  bot_id: "bot_1", brand_id: 20, name: "Marketing", template: "marketing", role: "Paid media watch (Meta)", enabled: true,
  instructions: "Watch Meta.", instructions_version: 2, tools: ["meta_insights_live"],
  limits: { max_scheduled_runs_per_day: 24, max_tool_calls: 40, max_pending_proposals: 5 }, version: 3,
  updated_at: "2026-11-21T10:00:00Z", updated_by: "me",
  triggers: [{ trigger_id: "trg_1", kind: "cron", config: { cron: "0 9 * * *", timezone: "Asia/Kolkata" }, enabled: true,
    next_run_at: "2026-11-22T03:30:00Z", last_fired_at: null }],
  next_run_at: "2026-11-22T03:30:00Z", last_run: null,
};
const act_ = (id: number, run: string, over: Partial<BotActivity> = {}): BotActivity => ({
  id, run_id: run, at: "2026-11-21T10:00:00Z", kind: "tool", tool: "meta_insights_live", target: "object_id=act_1",
  scope: "meta_ads:read", decision: "allowed (service token)", outcome: "completed", summary: "{}", detail: {}, ...over,
});
const run: BotRun = {
  run_id: "run_a", bot_id: "bot_1", status: "completed", outcome: "no_action", trigger_kind: "cron", created_at: "2026-11-21T03:30:05Z",
  finished_at: "2026-11-21T03:31:00Z", reason: "schedule '0 9 * * *' (Asia/Kolkata), due 2026-11-21 09:00", query: "", answer: "All fine.\nOUTCOME: no_action — pacing normal",
  error: null, merged: [{ kind: "signal", reason: "critical finding F1" }], instructions_version: 2, mission_id: "MS3-x", tool_calls: 3, started_at: null,
};

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  useBotsStore.setState({ bots: [], templates: [], readTools: [], selectedId: null, bot: null, runs: [], chat: [], activity: [], inbox: [], inboxDays: 1, error: null, busy: false });
});
afterEach(() => { act(() => root.unmount()); container.remove(); vi.restoreAllMocks(); history.replaceState({}, "", "/"); });

describe("bots routing", () => {
  it("round-trips the bots workspace and leaves it cleanly", () => {
    writeRoute({ workspace: "bots" });
    expect(location.search).toContain("bots=1");
    expect(readRoute().workspace).toBe("bots");
    writeRoute({ workspace: "conversation" });
    expect(location.search).not.toContain("bots");
  });
});

describe("activity timeline", () => {
  it("groups by run, newest run first, steps oldest first", () => {
    const groups = groupByRun([act_(5, "run_b"), act_(2, "run_a", { kind: "trigger" }), act_(3, "run_a"), act_(4, "run_b", { kind: "trigger" })]);
    expect(groups.map((g) => g.runId)).toEqual(["run_b", "run_a"]);
    expect(groups[0].rows.map((r) => r.id)).toEqual([4, 5]);
  });

  it("shows denied and failed steps distinctly with scope and target", async () => {
    await act(async () => root.render(<ActivityTimeline activity={[
      act_(1, "run_a", { outcome: "denied", tool: "google_campaigns_get", scope: "google_ads:read", decision: "denied", target: "customer_id=9" }),
      act_(2, "run_a", { outcome: "failed", tool: "metrics_query", target: null }),
    ]} />));
    const text = container.textContent ?? "";
    expect(text).toContain("Google campaigns get · denied");
    expect(text).toContain("Not allowed: google_ads:read (denied)");
    expect(text).toContain("Metrics query · failed");
    expect(container.querySelector('[title*="customer_id=9"]')).not.toBeNull();
  });

  it("hides raw payloads of completed reads but keeps reasons and script results", () => {
    expect(stepText(act_(1, "r", { summary: '{"success":true,"data":[]}' }))).toBe("");
    expect(stepText(act_(1, "r", { outcome: "failed", summary: '{"error":"Provide account_id"}' }))).toContain("Provide account_id");
    expect(stepText(act_(1, "r", { tool: "run_python", summary: "sum spend | result={\"x\": 1}" }))).toContain("sum spend");
    expect(stepText(act_(1, "r", { kind: "trigger", summary: "Cron: schedule" }))).toBe("Cron: schedule");
  });

  it("describes triggers in words", () => {
    expect(triggerText(bot.triggers![0])).toBe("Every day at 09:00 (Asia/Kolkata)");
    expect(triggerText({ ...bot.triggers![0], kind: "signal", config: { detector_prefix: "live.meta.", severities: ["critical"] } }))
      .toBe("On monitoring alerts live.meta.* · critical");
    expect(triggerText({ ...bot.triggers![0], kind: "watch", config: { tool: "meta_campaigns_list", fields: ["effective_status"], interval_min: 15 } }))
      .toBe("When meta campaigns list changes: effective status · checked every 15 min");
    expect(triggerText({ ...bot.triggers![0], kind: "webhook", config: {} })).toBe("When another system calls its webhook");
    expect(cronText("30 7 * * 1", "UTC")).toBe("Schedule 30 7 * * 1 (UTC)");
  });

  it("splits the verdict off even when the fact-check note follows it", () => {
    expect(splitAnswer("**Calm.**\n\nOUTCOME: no_action — all inside range\n\n⚠ Not verified: 5,000"))
      .toEqual({ body: "**Calm.**\n\n\n⚠ Not verified: 5,000", reason: "All inside range" });
  });

  it("names memory and delivery steps in words", () => {
    expect(stepLabel(act_(1, "r", { kind: "memory", tool: "memory_recall" }))).toBe("Memory");
    expect(stepLabel(act_(1, "r", { kind: "delivery", tool: "notification_center" }))).toBe("Notified");
    expect(stepLabel(act_(1, "r", { kind: "answer", decision: "action_needed" }))).toBe("Answer: needs action");
  });
});

describe("notification center bot reports", () => {
  it("lists reported runs and opens the bot", async () => {
    const real = useBotsStore.getState().select;
    const select = vi.fn(async () => {});
    useBotsStore.setState({ select });
    await act(async () => root.render(<BotReports now="2026-11-21T10:05:00Z" reports={[{
      at: "2026-11-21T10:00:00Z", run_id: "run_a", bot_id: "bot_1", bot_name: "Marketing", outcome: "action_needed",
      trigger_kind: "signal", summary: "[Seleric bot] Marketing: action needed — Sale overspending", reason: "action needed" }]} />));
    expect(container.textContent).toContain("Marketing · action needed");
    await act(async () => { container.querySelector("button")!.click(); });
    expect(useShellStore.getState().workspace).toBe("bots");
    expect(select).toHaveBeenCalledWith("bot_1");
    useBotsStore.setState({ select: real });
  });
});

const action: BotRun = {
  ...run, run_id: "run_b", outcome: "action_needed", trigger_kind: "watch", created_at: "2026-11-21T09:36:00Z", bot_name: "Marketing",
  answer: "**Two budgets raised.**\nOUTCOME: action_needed — cap TH-149", merged: [],
  report: {
    version: 1, headline: "Two budgets raised.",
    eda_checks: [{ metric: "ad_spend", label: "Ad spend so far", unit: "inr", now: 19242, p10: 15568, p50: 20000, p90: 27755, read: "Normal" }],
    proposals: [
      { entity: "TH-149-SCRATCHLOUNGE-6OCT", field: "daily budget", current: 5000, proposed: 3000, condition: "only if short-window CPA > 7-day CPA", reason: "Budget raised today." },
      { entity: "DOG TOY CS-23NOV-NEW", field: "daily budget", current: 4000, proposed: 3000, condition: "", reason: "Results unproven." },
    ],
    movers: [{ entity: "TH-149-SCRATCHLOUNGE-6OCT", figure: "₹2,307", note: "Budget raised to ₹5,000" }],
    checks: [{ label: "Top-ad CTR", now: "−15 to −39%", normal: "", read: "High" }],
    unverified: ["the ₹5,000 budget figure"],
  },
};
const calm: BotRun = { ...run, bot_name: "Marketing" };

function mockBots(bots: Bot[] = [bot], inbox: BotRun[] = [action, calm]) {
  vi.spyOn(botsApi, "list").mockImplementation(async () => ({ bots }));
  vi.spyOn(botsApi, "templates").mockResolvedValue({ templates: [{ template: "marketing", name: "Marketing", role: "", tools: [] }], read_tools: [] });
  vi.spyOn(botsApi, "get").mockImplementation(async (id) => bots.find((b) => b.bot_id === id) ?? bot);
  vi.spyOn(botsApi, "runs").mockResolvedValue({ runs: [] });
  vi.spyOn(botsApi, "activity").mockResolvedValue({ activity: [act_(1, "run_b", { kind: "trigger", summary: "Campaign list changed" })] });
  return vi.spyOn(botsApi, "inbox").mockResolvedValue({ runs: inbox });
}
const button = (label: string) => [...document.body.querySelectorAll("button")].find((b) => b.textContent?.trim() === label || b.getAttribute("aria-label") === label)!;
const settle = async () => { await act(async () => { await Promise.resolve(); await Promise.resolve(); }); };

describe("bots inbox logic", () => {
  it("splits runs into decisions (one per proposal), dismissed flags and calm runs", () => {
    const g = inboxGroups([action, calm, { ...action, run_id: "run_c", dismissed_at: "2026-11-21T10:00:00Z" }]);
    expect(g.decide.map((i) => i.key)).toEqual(["run_b:0", "run_b:1"]);
    expect(g.earlier.map((i) => i.run.run_id)).toEqual(["run_c"]);
    expect(g.calm.map((r) => r.run_id)).toEqual(["run_a"]);
  });

  it("places the band, median tick and today's marker on one scale", () => {
    const s = bandScale({ now: 30, p10: 10, p50: 15, p90: 20 });
    expect(s.band[0]).toBeLessThan(s.tick!);
    expect(s.tick!).toBeLessThan(s.band[1]);
    expect(s.marker).toBeGreaterThan(s.band[1]);
    expect(s.marker).toBeLessThan(100);
  });

  it("follows the proposed campaign through other runs, and the bot's day when there is no campaign", () => {
    const later: BotRun = { ...action, run_id: "run_z", report: { version: 1, eda_checks: [], movers: [{ entity: "th-149-scratchlounge-6oct ", figure: "₹4,200", note: "Raised" }] } };
    const [first] = itemsOf([action]);
    const h = historyFor(first, [later, action]);
    expect(h.events.map((e) => e.title)).toEqual(["₹3,000 daily budget", "₹4,200", "₹2,307"]);
    const review = itemsOf([{ ...action, report: { version: 1, eda_checks: [], proposals: [{ entity: "X", field: "status", current: null, proposed: null, condition: "", reason: "Flips" }] } }])[0];
    expect(historyFor(review, []).events[0]).toMatchObject({ title: "Review status", note: "" });
    expect(historyFor(itemsOf([calm])[0], [action, calm]).events.map((e) => e.title)).toEqual(["Needs action", "All in order"]);
  });
});

describe("bots page", () => {
  it("opens the first decision with its proposal, checks and movers, and runs on demand", async () => {
    mockBots();
    const start = vi.spyOn(botsApi, "run").mockResolvedValue({ run_id: "run_new", status: "queued" });
    await act(async () => root.render(<BotsWorkspace />));
    await settle();
    const text = container.textContent ?? "";
    expect(text).toContain("Needs you · 2");
    expect(text).toContain("TH-149-SCRATCHLOUNGE-6OCT");
    expect(text).toContain("₹3,000");
    expect(text).toContain("proposed daily budget");
    expect(text).toContain("normal ₹15,568–₹27,755");
    expect(text).toContain("Top-ad CTR");
    expect(text).toContain("What moved today");
    expect(text).toContain("Not checked against live data: the ₹5,000 budget figure");
    expect(text).toContain("1 run, nothing to do");
    expect(location.search).not.toContain("run=");
    await act(async () => { button("Run now").click(); });
    expect(start).toHaveBeenCalledWith("bot_1", "manual", "");
  });

  it("shows one bot per screen and switching bot shows only that bot's runs", async () => {
    const other: Bot = { ...bot, bot_id: "bot_2", name: "Marketing 2" };
    const theirs: BotRun = { ...calm, run_id: "run_o", bot_id: "bot_2", bot_name: "Marketing 2", outcome: "no_action" };
    mockBots([bot, other], [action, calm, theirs]);
    await act(async () => root.render(<BotsWorkspace />));
    await settle();
    expect(container.textContent).toContain("1 run, nothing to do");
    const pick = container.querySelector<HTMLSelectElement>('select[aria-label="Bot"]')!;
    await act(async () => { pick.value = "bot_2"; pick.dispatchEvent(new Event("change", { bubbles: true })); });
    await settle();
    expect(useBotsStore.getState().selectedId).toBe("bot_2");
    expect(container.textContent).toContain("Nothing needs you right now.");
    expect(container.textContent).not.toContain("TH-149-SCRATCHLOUNGE-6OCT");
  });

  it("dismisses a decision and brings it back", async () => {
    const inbox = mockBots();
    const dismiss = vi.spyOn(botsApi, "dismiss").mockResolvedValue({ run_id: "run_b", dismissed_at: "2026-11-21T10:00:00Z" });
    await act(async () => root.render(<BotsWorkspace />));
    await settle();
    inbox.mockResolvedValue({ runs: [{ ...action, dismissed_at: "2026-11-21T10:00:00Z" }, calm] });
    await act(async () => { button("Dismiss all 2").click(); });
    await settle();
    expect(dismiss).toHaveBeenCalledWith("run_b", false);
    expect(container.textContent).toContain("Nothing needs you right now.");
    expect(container.textContent).toContain("Dismissed");
  });

  it("opens the report with the run's steps and asks about the decision", async () => {
    mockBots();
    await act(async () => root.render(<BotsWorkspace />));
    await settle();
    await act(async () => { button("Open report").click(); });
    expect(document.body.querySelector('[aria-label="Bot activity"]')?.textContent).toContain("Campaign list changed");
    await act(async () => { button("Ask about this").click(); });
    const box = document.body.querySelector('textarea[aria-label="Message the bot"]') as HTMLTextAreaElement;
    expect(box.value).toContain("About TH-149-SCRATCHLOUNGE-6OCT");
  });

  it("opens the bot named in the URL, deletes it after confirming, then opens its neighbour", async () => {
    const second: Bot = { ...bot, bot_id: "bot_2", name: "Marketing 2" };
    const third: Bot = { ...bot, bot_id: "bot_3", name: "Marketing 3" };
    let listed = [bot, second, third];
    vi.spyOn(botsApi, "list").mockImplementation(async () => ({ bots: listed }));
    vi.spyOn(botsApi, "templates").mockResolvedValue({ templates: [], read_tools: [] });
    vi.spyOn(botsApi, "get").mockImplementation(async (id) => listed.find((b) => b.bot_id === id)!);
    vi.spyOn(botsApi, "runs").mockResolvedValue({ runs: [] });
    vi.spyOn(botsApi, "activity").mockResolvedValue({ activity: [] });
    vi.spyOn(botsApi, "inbox").mockResolvedValue({ runs: [] });
    const remove = vi.spyOn(botsApi, "remove").mockImplementation(async (id) => { listed = listed.filter((b) => b.bot_id !== id); });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    history.replaceState({}, "", "/?bots=1&bot=bot_2");

    await act(async () => root.render(<BotsWorkspace />));
    await settle();
    expect(useBotsStore.getState().selectedId).toBe("bot_2");

    await act(async () => { (container.querySelector('[aria-label="Bot settings"]') as HTMLButtonElement).click(); });
    await act(async () => { button("Delete bot").click(); });
    expect(remove).toHaveBeenCalledWith("bot_2");
    expect(useBotsStore.getState().bots.map((b) => b.bot_id)).toEqual(["bot_1", "bot_3"]);
    expect(useBotsStore.getState().selectedId).toBe("bot_3");
    expect(location.search).toContain("bot=bot_3");
    writeRoute({ workspace: "conversation" });
    expect(location.search).not.toContain("bot");
  });

  it("shows a version conflict and reloads the bot", async () => {
    const { ApiError } = await import("../api/http");
    vi.spyOn(botsApi, "update").mockRejectedValue(new ApiError(409, "bot changed since you opened it (version 4, you had 3)"));
    const get = vi.spyOn(botsApi, "get").mockResolvedValue({ ...bot, version: 4 });
    vi.spyOn(botsApi, "runs").mockResolvedValue({ runs: [] });
    vi.spyOn(botsApi, "activity").mockResolvedValue({ activity: [] });
    useBotsStore.setState({ bot, selectedId: "bot_1" });
    const ok = await useBotsStore.getState().save({ name: "X" });
    expect(ok).toBe(false);
    expect(useBotsStore.getState().error).toContain("changed since");
    await vi.waitFor(() => expect(get).toHaveBeenCalled());
  });
});
