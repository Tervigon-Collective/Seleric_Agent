import { create } from "zustand";
import { ApiError } from "../api/http";
import {
  monitoringApi,
  type AlertRule,
  type Candidate,
  type ConfigMetric,
  type DomainView,
  type MonitoringState,
  type SaveBody,
} from "../api/monitoring";

export interface DraftMetric {
  metric_id: string;
  label: string;
  alert: AlertRule | null;
  direction_bad: "up" | "down" | "none" | null;
  /** Carried into the business snapshot that answers “how is the business doing”. */
  headline: boolean;
}

export interface Draft {
  enabled: boolean;
  metrics: DraftMetric[];
}

type Busy = "saving" | "refreshing" | "resetting";

interface MonitoringStore {
  state: MonitoringState | null;
  loading: boolean;
  error: string | null;
  drafts: Record<string, Draft>;
  catalogue: Record<string, Candidate[]>;
  busy: Record<string, Busy | undefined>;
  notice: Record<string, { kind: "ok" | "error"; text: string } | undefined>;
  load: () => Promise<void>;
  loadCatalogue: (domain: string) => Promise<void>;
  toggleMetric: (domain: string, metricId: string, on: boolean) => void;
  updateMetric: (domain: string, metricId: string, patch: Partial<DraftMetric>) => void;
  setEnabled: (domain: string, enabled: boolean) => void;
  discard: (domain: string) => void;
  save: (domain: string) => Promise<void>;
  reset: (domain: string) => Promise<void>;
  refresh: (domain: string) => Promise<void>;
}

export const draftFromConfig = (metrics: ConfigMetric[], enabled: boolean): Draft => ({
  enabled,
  metrics: metrics.map((m) => ({
    metric_id: m.metric_id,
    label: m.label ?? "",
    alert: m.alert,
    direction_bad: m.direction_bad,
    headline: m.headline,
  })),
});

export function toBody(draft: Draft, version: number): SaveBody {
  return {
    enabled: draft.enabled,
    expected_version: version,
    metrics: draft.metrics.map((m) => ({
      metric_id: m.metric_id,
      ...(m.label.trim() ? { label: m.label.trim() } : {}),
      ...(m.alert ? { alert: m.alert } : {}),
      ...(m.direction_bad ? { direction_bad: m.direction_bad } : {}),
      headline: m.headline,
    })),
  };
}

export function isDirty(domain: DomainView, draft: Draft | undefined): boolean {
  if (!draft) return false;
  return JSON.stringify(draft) !== JSON.stringify(draftFromConfig(domain.metrics, domain.enabled));
}

const message = (e: unknown) => (e instanceof ApiError ? e.detail : e instanceof Error ? e.message : "Request failed");

function replaceDomain(state: MonitoringState | null, next: DomainView): MonitoringState | null {
  return state && { ...state, domains: state.domains.map((d) => (d.id === next.id ? next : d)) };
}

export const useMonitoringStore = create<MonitoringStore>((set, get) => {
  const setBusy = (domain: string, busy: Busy | undefined) =>
    set((s) => ({ busy: { ...s.busy, [domain]: busy } }));
  const setNotice = (domain: string, notice: MonitoringStore["notice"][string]) =>
    set((s) => ({ notice: { ...s.notice, [domain]: notice } }));
  const adopt = (next: DomainView) =>
    set((s) => ({
      state: replaceDomain(s.state, next),
      drafts: { ...s.drafts, [next.id]: draftFromConfig(next.metrics, next.enabled) },
    }));
  const editDraft = (domain: string, edit: (d: Draft) => Draft) =>
    set((s) => {
      const current = s.drafts[domain];
      return current ? { drafts: { ...s.drafts, [domain]: edit(current) } } : {};
    });

  return {
    state: null,
    loading: false,
    error: null,
    drafts: {},
    catalogue: {},
    busy: {},
    notice: {},

    load: async () => {
      set({ loading: true, error: null });
      try {
        const state = await monitoringApi.state();
        set((s) => {
          const drafts = { ...s.drafts };
          // A domain the user is editing keeps its draft; untouched ones follow the server.
          for (const d of state.domains) {
            if (!drafts[d.id] || !isDirty(s.state?.domains.find((x) => x.id === d.id) ?? d, drafts[d.id])) {
              drafts[d.id] = draftFromConfig(d.metrics, d.enabled);
            }
          }
          return { state, drafts, loading: false };
        });
      } catch (e) {
        set({ loading: false, error: message(e) });
      }
    },

    loadCatalogue: async (domain) => {
      if (get().catalogue[domain]) return;
      try {
        const { metrics } = await monitoringApi.catalogue(domain);
        set((s) => ({ catalogue: { ...s.catalogue, [domain]: metrics } }));
      } catch (e) {
        setNotice(domain, { kind: "error", text: message(e) });
      }
    },

    toggleMetric: (domain, metricId, on) =>
      editDraft(domain, (d) => {
        const has = d.metrics.some((m) => m.metric_id === metricId);
        if (on && !has) {
          const cap = get().state?.max_metrics_per_domain ?? 40;
          if (d.metrics.length >= cap) return d;
          return { ...d, metrics: [...d.metrics, { metric_id: metricId, label: "", alert: null, direction_bad: null, headline: false }] };
        }
        return !on && has ? { ...d, metrics: d.metrics.filter((m) => m.metric_id !== metricId) } : d;
      }),

    updateMetric: (domain, metricId, patch) =>
      editDraft(domain, (d) => ({ ...d, metrics: d.metrics.map((m) => (m.metric_id === metricId ? { ...m, ...patch } : m)) })),

    setEnabled: (domain, enabled) => editDraft(domain, (d) => ({ ...d, enabled })),

    discard: (domain) => {
      const d = get().state?.domains.find((x) => x.id === domain);
      if (d) adopt(d);
      setNotice(domain, undefined);
    },

    save: async (domain) => {
      const view = get().state?.domains.find((d) => d.id === domain);
      const draft = get().drafts[domain];
      if (!view || !draft) return;
      setBusy(domain, "saving");
      setNotice(domain, undefined);
      try {
        adopt(await monitoringApi.save(domain, toBody(draft, view.version)));
        setBusy(domain, "refreshing");
        // Resolve now so the new selection shows real numbers instead of waiting for the next scheduled run.
        try {
          adopt(await monitoringApi.refresh(domain));
          setNotice(domain, { kind: "ok", text: "Saved. Numbers are up to date." });
        } catch (e) {
          setNotice(domain, { kind: "ok", text: `Saved. The next scheduled run will fill in the numbers (${message(e)}).` });
        }
      } catch (e) {
        setNotice(domain, { kind: "error", text: message(e) });
      } finally {
        setBusy(domain, undefined);
      }
    },

    reset: async (domain) => {
      setBusy(domain, "resetting");
      try {
        adopt(await monitoringApi.reset(domain));
        setNotice(domain, { kind: "ok", text: "Back to the default selection." });
      } catch (e) {
        setNotice(domain, { kind: "error", text: message(e) });
      } finally {
        setBusy(domain, undefined);
      }
    },

    refresh: async (domain) => {
      setBusy(domain, "refreshing");
      setNotice(domain, undefined);
      try {
        const next = await monitoringApi.refresh(domain);
        set((s) => ({ state: replaceDomain(s.state, next) }));
      } catch (e) {
        setNotice(domain, { kind: "error", text: message(e) });
      } finally {
        setBusy(domain, undefined);
      }
    },
  };
});
