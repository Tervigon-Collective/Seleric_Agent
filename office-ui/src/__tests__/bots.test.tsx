import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { botsApi, type Bot, type BotActivity, type BotRun } from "../api/bots";
import { ActivityTimeline, BotsWorkspace, cronText, groupByRun, stepLabel, stepText, triggerText } from "../components/bots/BotsWorkspace";
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
  useBotsStore.setState({ bots: [], templates: [], readTools: [], selectedId: null, bot: null, runs: [], chat: [], activity: [], error: null, busy: false });
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

describe("bots page", () => {
  it("lists bots, opens the first, shows runs with merged triggers, and runs on demand", async () => {
    vi.spyOn(botsApi, "list").mockResolvedValue({ bots: [{ ...bot, last_run: run }] });
    vi.spyOn(botsApi, "templates").mockResolvedValue({ templates: [{ template: "marketing", name: "Marketing", role: "", tools: [] }], read_tools: ["meta_insights_live"] });
    vi.spyOn(botsApi, "get").mockResolvedValue(bot);
    vi.spyOn(botsApi, "runs").mockImplementation(async (_id, kind) => ({ runs: kind === "chat" ? [] : [run] }));
    vi.spyOn(botsApi, "activity").mockResolvedValue({ activity: [act_(1, "run_a", { kind: "trigger", summary: "Cron: schedule" })] });
    const start = vi.spyOn(botsApi, "run").mockResolvedValue({ run_id: "run_new", status: "queued" });

    await act(async () => root.render(<BotsWorkspace />));
    await act(async () => { await Promise.resolve(); });
    const text = container.textContent ?? "";
    expect(text).toContain("Paid media watch (Meta)");
    expect(text).toContain("All in order");
    expect(text).toContain("+ 1 more trigger while it waited");
    expect(text).toContain("instructions v2");
    expect(container.querySelector('[aria-label="Bot activity"]')?.textContent).toContain("Cron: schedule");

    const button = [...container.querySelectorAll("button")].find((b) => b.textContent === "Run now")!;
    await act(async () => { button.click(); });
    expect(start).toHaveBeenCalledWith("bot_1", "manual", "");
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
    const remove = vi.spyOn(botsApi, "remove").mockImplementation(async (id) => { listed = listed.filter((b) => b.bot_id !== id); });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    history.replaceState({}, "", "/?bots=1&bot=bot_2");

    await act(async () => root.render(<BotsWorkspace />));
    await act(async () => { await Promise.resolve(); });
    expect(useBotsStore.getState().selectedId).toBe("bot_2");

    const settings = [...container.querySelectorAll("button")].find((b) => b.textContent?.trim() === "Settings")!;
    await act(async () => { settings.click(); });
    const del = [...document.body.querySelectorAll("button")].find((b) => b.textContent === "Delete bot")!;
    await act(async () => { del.click(); });
    expect(remove).toHaveBeenCalledWith("bot_2");
    expect(useBotsStore.getState().bots.map((b) => b.bot_id)).toEqual(["bot_1", "bot_3"]);
    expect(useBotsStore.getState().selectedId).toBe("bot_3");
    expect(location.search).toContain("bot=bot_3");
    writeRoute({ workspace: "conversation" });
    expect(location.search).not.toContain("bot");
  });

  it("hides the activity panel with its close button and brings it back", async () => {
    vi.spyOn(botsApi, "list").mockResolvedValue({ bots: [bot] });
    vi.spyOn(botsApi, "templates").mockResolvedValue({ templates: [], read_tools: [] });
    vi.spyOn(botsApi, "get").mockResolvedValue(bot);
    vi.spyOn(botsApi, "runs").mockResolvedValue({ runs: [] });
    vi.spyOn(botsApi, "activity").mockResolvedValue({ activity: [] });
    await act(async () => root.render(<BotsWorkspace />));
    await act(async () => { await Promise.resolve(); });
    const panel = () => container.querySelector('[aria-label="Bot activity"]');
    expect(panel()).not.toBeNull();
    await act(async () => { (container.querySelector('[aria-label="Hide activity"]') as HTMLButtonElement).click(); });
    expect(panel()).toBeNull();
    await act(async () => { (container.querySelector('[aria-label="Show activity"]') as HTMLButtonElement).click(); });
    expect(panel()).not.toBeNull();
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
