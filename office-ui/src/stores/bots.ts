import { create } from "zustand";
import { ApiError } from "../api/http";
import { botsApi, type Bot, type BotActivity, type BotChanges, type BotRun, type BotSkill, type BotTemplate, type TriggerKind, type BotTrigger } from "../api/bots";

/** Bot runs take tens of seconds; polling every 10 s while a bot is open shows a run finishing promptly. */
export const POLL_MS = 10_000;

interface BotsStore {
  bots: Bot[];
  templates: BotTemplate[];
  readTools: string[];
  selectedId: string | null;
  bot: Bot | null;
  runs: BotRun[];
  chat: BotRun[];
  activity: BotActivity[];
  error: string | null;
  busy: boolean;
  /** A webhook's secret from the reply that created it; shown once, cleared when another bot opens. */
  newSecret: { triggerId: string; secret: string } | null;
  loadList: () => Promise<void>;
  select: (id: string | null) => Promise<void>;
  refresh: () => Promise<void>;
  create: (template: string, name?: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  save: (changes: BotChanges) => Promise<boolean>;
  addTrigger: (kind: TriggerKind, config: BotTrigger["config"]) => Promise<boolean>;
  setTrigger: (triggerId: string, enabled: boolean) => Promise<void>;
  deleteTrigger: (triggerId: string) => Promise<void>;
  runNow: (message?: string) => Promise<void>;
  ask: (message: string) => Promise<void>;
  saveSkill: (skill: Pick<BotSkill, "name" | "body" | "enabled"> & { skill_id?: string }) => Promise<boolean>;
  deleteSkill: (skillId: string) => Promise<void>;
}

/** The open bot lives in ?bot= so a reload or a shared link opens the same one. */
const urlBot = () => new URL(location.href).searchParams.get("bot");
function writeUrlBot(id: string | null) {
  const url = new URL(location.href);
  if (id) url.searchParams.set("bot", id); else url.searchParams.delete("bot");
  if (url.href !== location.href) history.replaceState(history.state, "", url);
}

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

export const useBotsStore = create<BotsStore>((set, get) => {
  /** Run an action; on failure keep the message for the page and report false. */
  async function attempt(fn: () => Promise<unknown>): Promise<boolean> {
    set({ busy: true, error: null });
    try {
      await fn();
      return true;
    } catch (e) {
      set({ error: message(e) });
      return false;
    } finally {
      set({ busy: false });
    }
  }

  return {
    bots: [], templates: [], readTools: [], selectedId: null, bot: null, runs: [], chat: [], activity: [],
    error: null, busy: false, newSecret: null,

    loadList: async () => {
      try {
        const [{ bots }, t] = await Promise.all([botsApi.list(), get().templates.length ? null : botsApi.templates()]);
        set({ bots, ...(t ? { templates: t.templates, readTools: t.read_tools } : {}) });
        const current = get().selectedId;
        // First open, or the open bot was deleted (here or elsewhere): open the one in the URL, else the first.
        if (current ? !bots.some((b) => b.bot_id === current) : bots.length) {
          const want = urlBot();
          await get().select((bots.find((b) => b.bot_id === (current ?? want)) ?? bots[0])?.bot_id ?? null);
        }
      } catch (e) { set({ error: message(e) }); }
    },

    select: async (id) => {
      set({ selectedId: id, bot: null, runs: [], chat: [], activity: [], error: null, newSecret: null });
      writeUrlBot(id);
      if (id) await get().refresh();
    },

    refresh: async () => {
      const id = get().selectedId;
      if (!id) return;
      try {
        const [bot, { runs }, { runs: chat }, { activity }] = await Promise.all([
          botsApi.get(id), botsApi.runs(id), botsApi.runs(id, "chat"), botsApi.activity(id)]);
        if (get().selectedId !== id) return; // the user moved to another bot while this loaded
        set({ bot, runs, chat: [...chat].reverse(), activity });
      } catch (e) { set({ error: message(e) }); }
    },

    create: async (template, name) => {
      let created: Bot | null = null;
      if (await attempt(async () => { created = await botsApi.create(template, name); })) {
        await get().loadList();
        await get().select((created as Bot | null)?.bot_id ?? null);
      }
    },

    remove: async (id) => {
      const { bots, selectedId } = get();
      if (!(await attempt(() => botsApi.remove(id)))) return;
      const rest = bots.filter((b) => b.bot_id !== id);
      set({ bots: rest });
      if (selectedId === id) {
        const at = bots.findIndex((b) => b.bot_id === id);
        await get().select(rest[Math.min(at, rest.length - 1)]?.bot_id ?? null); // the neighbour below, else above
      }
    },

    save: async (changes) => {
      const bot = get().bot;
      if (!bot) return false;
      const ok = await attempt(async () => { set({ bot: await botsApi.update(bot.bot_id, bot.version, changes) }); });
      if (ok) void get().loadList();
      else if (get().error?.includes("changed since")) void get().refresh(); // 409: reload so the next save carries the new version
      return ok;
    },

    addTrigger: async (kind, config) => {
      const id = get().selectedId;
      const ok = !!id && await attempt(async () => {
        const t = await botsApi.addTrigger(id, kind, config);
        if (t.secret) set({ newSecret: { triggerId: t.trigger_id, secret: t.secret } });
      });
      if (ok) await get().refresh();
      return ok;
    },

    setTrigger: async (triggerId, enabled) => {
      const id = get().selectedId;
      if (id && await attempt(() => botsApi.setTrigger(id, triggerId, enabled))) await get().refresh();
    },

    deleteTrigger: async (triggerId) => {
      const id = get().selectedId;
      if (id && await attempt(() => botsApi.deleteTrigger(id, triggerId))) await get().refresh();
    },

    runNow: async (text = "") => {
      const id = get().selectedId;
      if (id && await attempt(() => botsApi.run(id, "manual", text))) await get().refresh();
    },

    ask: async (text) => {
      const id = get().selectedId;
      if (id && text.trim() && await attempt(() => botsApi.run(id, "chat", text.trim()))) await get().refresh();
    },

    saveSkill: async (skill) => {
      const id = get().selectedId;
      const ok = !!id && await attempt(() => botsApi.saveSkill(id, skill));
      if (ok) await get().refresh();
      return ok;
    },

    deleteSkill: async (skillId) => {
      const id = get().selectedId;
      if (id && await attempt(() => botsApi.deleteSkill(id, skillId))) await get().refresh();
    },
  };
});
