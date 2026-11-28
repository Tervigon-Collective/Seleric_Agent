import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { botsApi, type Bot } from "../api/bots";
import { BotSettingsPage, buildCron, cronText, parseCron, SECTIONS } from "../components/bots/BotSettingsPage";
import { useBotsStore } from "../stores/bots";

const bot: Bot = {
  bot_id: "bot_1", brand_id: 20, name: "Marketing", template: "marketing", role: "Paid media watch (Meta)", enabled: true,
  instructions: "Watch our Meta ads.", instructions_version: 2, tools: [], run_tools: ["meta_insights_live"],
  limits: { max_scheduled_runs_per_day: 24 }, version: 3, updated_at: "2026-11-21T10:00:00Z", updated_by: "me",
  triggers: [], skills: [], next_run_at: null, last_run: null,
};

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  useBotsStore.setState({ bots: [bot], selectedId: "bot_1", bot, chat: [], readTools: [], busy: false, error: null });
  vi.spyOn(botsApi, "knowledge").mockResolvedValue({ documents: [], storage: "hindsight", max_mb: 20, types: [".pdf"] });
  vi.spyOn(botsApi, "skillLibrary").mockResolvedValue({ skills: [] });
});
afterEach(() => { act(() => root.unmount()); container.remove(); vi.restoreAllMocks(); });

const settle = async () => { await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); }); };
const button = (label: string) => [...container.querySelectorAll("button")].find((b) => b.textContent?.trim() === label)!;
const page = (section: (typeof SECTIONS)[number]["key"]) =>
  root.render(<BotSettingsPage bot={bot} section={section} onSection={() => {}} onBack={() => {}} onDeleted={() => {}} />);

describe("schedule cron helpers", () => {
  it("round-trips the shapes the form builds and keeps anything else as custom", () => {
    expect(parseCron("0 9 * * 1,5")).toEqual({ freq: "weekly", days: [1, 5], dom: 1, time: "09:00" });
    expect(buildCron(parseCron("30 18 15 * *"), "")).toBe("30 18 15 * *");
    expect(buildCron({ freq: "weekly", days: [5, 1, 1], dom: 1, time: "07:05" }, "")).toBe("5 7 * * 1,5");
    expect(parseCron("*/15 * * * *").freq).toBe("custom");
    expect(buildCron({ freq: "custom", days: [], dom: 1, time: "" }, " */15 * * * * ")).toBe("*/15 * * * *");
    expect(cronText("0 9 * * 1", "Asia/Kolkata")).toBe("Every Monday at 09:00 (Asia/Kolkata)");
    expect(cronText("0 9 * * *", "Asia/Kolkata")).toBe("Every day at 09:00 (Asia/Kolkata)");
  });
});

describe("bot settings page", () => {
  it("says what every section is for", async () => {
    await act(async () => page("knowledge"));
    const nav = container.querySelector('nav[aria-label="Settings sections"]')!.textContent ?? "";
    for (const s of SECTIONS) expect(nav).toContain(s.purpose);
    expect(container.textContent).toContain("Hindsight Cloud");
  });

  it("uploads each file as its own request with the share flag", async () => {
    const upload = vi.spyOn(botsApi, "upload").mockResolvedValue({ document_id: "doc_1", name: "a.pdf", status: "processing" });
    await act(async () => page("knowledge"));
    await settle();
    await act(async () => { (container.querySelector('[aria-label="Share with every bot of this brand"]') as HTMLElement).click(); });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const files = [new File(["x"], "a.pdf", { type: "application/pdf" }), new File(["y"], "b.pdf", { type: "application/pdf" })];
    Object.defineProperty(input, "files", { value: files, configurable: true });
    await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); });
    await settle();
    expect(upload.mock.calls.map((c) => [c[0], c[1].name, c[2]])).toEqual([["bot_1", "a.pdf", true], ["bot_1", "b.pdf", true]]);
  });

  it("adds a weekly schedule with its task from the form", async () => {
    const addTrigger = vi.fn().mockResolvedValue(true);
    useBotsStore.setState({ addTrigger });
    await act(async () => page("schedules"));
    await act(async () => { button("Add schedule").click(); });
    await settle();
    const task = container.querySelector("textarea") as HTMLTextAreaElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    await act(async () => { setter.call(task, "Email me last week's Meta report"); task.dispatchEvent(new Event("input", { bubbles: true })); });
    await act(async () => { button("Add schedule").click(); }); // the editor's save; the opener hides while editing
    expect(addTrigger).toHaveBeenCalledWith("cron", expect.objectContaining({ cron: "0 9 * * 1", timezone: "Asia/Kolkata", task: "Email me last week's Meta report" }));
  });

  it("runs a saved schedule from the list and the editor", async () => {
    const runTrigger = vi.fn().mockResolvedValue(undefined);
    const schedule = {
      trigger_id: "trg_1", kind: "cron" as const, enabled: true, next_run_at: null, last_fired_at: null, state: null,
      config: { cron: "0 9 * * *", timezone: "Asia/Kolkata", title: "Weekly Meta report", task: "Email me last week's Meta report" },
    };
    const withSchedule = { ...bot, triggers: [schedule] };
    useBotsStore.setState({ bot: withSchedule, runTrigger });
    await act(async () => {
      root.render(<BotSettingsPage bot={withSchedule} section="schedules" onSection={() => {}} onBack={() => {}} onDeleted={() => {}} />);
    });
    await act(async () => { button("Run now").click(); });
    expect(runTrigger).toHaveBeenCalledWith("trg_1");
    await act(async () => { button("Edit").click(); });
    await settle();
    await act(async () => { button("Run now").click(); });
    expect(runTrigger).toHaveBeenCalledTimes(2);
  });
});
