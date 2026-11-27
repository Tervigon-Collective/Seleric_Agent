import { create } from "zustand";
import { ApiError } from "../api/http";
import {
  notificationsApi,
  type ActionPreview,
  type ActionResult,
  type Feed,
  type NotificationItem,
} from "../api/notifications";

/** The monitor ticks every 5 minutes; polling every 30 s shows a tick within half a minute of it landing. */
export const POLL_MS = 30_000;

export interface ActionFlow {
  item: NotificationItem;
  actionId: string;
  inputs: Record<string, string>;
  preview: ActionPreview | null;
  busy: "previewing" | "approving" | null;
  error: string | null;
  result: ActionResult | null;
}

interface NotificationsStore {
  feed: Feed | null;
  error: string | null;
  open: boolean;
  /** Finding ids already seen while the panel was open; the badge counts the rest. */
  seen: Record<string, true>;
  flow: ActionFlow | null;
  load: () => Promise<void>;
  startPolling: () => () => void;
  setOpen: (open: boolean) => void;
  label: (item: NotificationItem, label: NonNullable<NotificationItem["label"]>) => Promise<void>;
  startAction: (item: NotificationItem, actionId: string) => Promise<void>;
  setInput: (name: string, value: string) => void;
  repreview: () => Promise<void>;
  approve: () => Promise<void>;
  closeAction: () => void;
}

const message = (e: unknown) => (e instanceof ApiError ? e.detail : e instanceof Error ? e.message : "Request failed");

/** Open findings that need a person: critical and warning, not yet seen in the open panel. */
export function unseenCount(feed: Feed | null, seen: Record<string, true>): number {
  return (feed?.items ?? []).filter((i) => i.state !== "resolved" && i.severity !== "info" && !seen[i.finding_id]).length;
}

export const useNotificationsStore = create<NotificationsStore>((set, get) => {
  const markSeen = () => {
    const feed = get().feed;
    if (!feed) return;
    const seen = { ...get().seen };
    for (const i of feed.items) seen[i.finding_id] = true;
    set({ seen });
  };

  const preview = async (inputs: Record<string, string>) => {
    const flow = get().flow;
    if (!flow) return;
    set({ flow: { ...flow, inputs, busy: "previewing", error: null, preview: null } });
    try {
      const p = await notificationsApi.preview(flow.item.finding_id, flow.actionId, inputs);
      set((s) => (s.flow ? { flow: { ...s.flow, preview: p, busy: null } } : {}));
    } catch (e) {
      set((s) => (s.flow ? { flow: { ...s.flow, busy: null, error: message(e) } } : {}));
    }
  };

  return {
    feed: null,
    error: null,
    open: false,
    seen: {},
    flow: null,

    load: async () => {
      try {
        const feed = await notificationsApi.feed();
        set({ feed, error: null });
        if (get().open) markSeen();
      } catch (e) {
        set({ error: message(e) });
      }
    },

    startPolling: () => {
      void get().load();
      const tick = () => { if (document.visibilityState !== "hidden") void get().load(); };
      const id = window.setInterval(tick, POLL_MS);
      document.addEventListener("visibilitychange", tick);
      return () => { window.clearInterval(id); document.removeEventListener("visibilitychange", tick); };
    },

    setOpen: (open) => {
      set({ open });
      if (open) { markSeen(); void get().load(); }
    },

    label: async (item, label) => {
      const patch = (l: NotificationItem["label"]) => set((s) => ({
        feed: s.feed && { ...s.feed, items: s.feed.items.map((i) => (i.finding_id === item.finding_id ? { ...i, label: l } : i)) },
      }));
      const before = item.label;
      patch(label);
      try {
        await notificationsApi.label(item.finding_id, label);
      } catch (e) {
        patch(before);
        set({ error: message(e) });
      }
    },

    startAction: async (item, actionId) => {
      set({ flow: { item, actionId, inputs: {}, preview: null, busy: null, error: null, result: null } });
      await preview({});
      const p = get().flow?.preview;
      if (p?.kind === "write" && p.change) {
        const inputs: Record<string, string> = {};
        for (const name of item.actions.find((a) => a.id === actionId)?.inputs ?? []) {
          const v = p.change[name];
          if (typeof v === "string") inputs[name] = v;
        }
        set((s) => (s.flow ? { flow: { ...s.flow, inputs } } : {}));
      }
    },

    setInput: (name, value) => set((s) => (s.flow ? { flow: { ...s.flow, inputs: { ...s.flow.inputs, [name]: value }, preview: null } } : {})),

    repreview: async () => { const f = get().flow; if (f) await preview(f.inputs); },

    approve: async () => {
      const flow = get().flow;
      if (!flow || flow.preview?.kind !== "write") return;
      set({ flow: { ...flow, busy: "approving", error: null } });
      try {
        const result = await notificationsApi.approve(flow.preview.request_id);
        set((s) => (s.flow ? { flow: { ...s.flow, busy: null, result, error: result.status === "failed" ? result.error : null } } : {}));
        void get().load();
      } catch (e) {
        set((s) => (s.flow ? { flow: { ...s.flow, busy: null, error: message(e) } } : {}));
      }
    },

    closeAction: () => set({ flow: null }),
  };
});
