import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DomainView, MetricView, MonitoringState } from "../api/monitoring";
import { monitoringApi } from "../api/monitoring";
import { MonitoringConfig } from "../components/ops/MonitoringConfig";
import { OpsDashboard } from "../components/ops/OpsDashboard";
import { formatMove, formatValue, thresholdUnit } from "../components/ops/format";
import { draftFromConfig, isDirty, toBody, useMonitoringStore } from "../stores/monitoring";

const metric = (over: Partial<MetricView> = {}): MetricView => ({
  metric_id: "orders", name: "Orders", label: null, unit: "count", display: "count", shape: "additive", value: 40, observed_on: "2026-10-28", status: "alert",
  polarity_known: true, direction_bad: "down", rule: null, freshness: "CURRENT", quality_flags: [],
  series: Array.from({ length: 28 }, (_, i) => ({ ts: `2026-10-${String(i + 1).padStart(2, "0")}`, value: i === 27 ? 40 : 100 })),
  judgement: { status: "alert", shape: "additive", basis: "weekday", observed: 40, expected: 100, low: 80, high: 120, z: -9, direction: "down", is_anomaly: true, worse_by: 60, unit: "%", delta_pct: -60, reason: "outside its normal range the bad way", history_points: 27, observed_on: "2026-10-28" },
  ...over,
});

const domain = (over: Partial<DomainView> = {}): DomainView => ({
  id: "commerce", title: "Commerce", description: "Orders and sales", enabled: true, custom: false, version: 0,
  updated_at: null, updated_by: null, pending: false, counts: { alert: 1 },
  metrics: [{ metric_id: "orders", catalogue_metric: "orders", label: null, alert: null, direction_bad: null, headline: true, filters: {} }],
  snapshot: { status: "OK", as_of: "2026-10-28", computed_at: "2026-10-29T10:00:00Z", age_s: 120, stale: false, window: {}, signals: ["orders: outside its normal range the bad way"], metrics: [metric()] },
  ...over,
});

const stateWith = (d: DomainView): MonitoringState => ({ interval_s: 900, max_metrics_per_domain: 3, domains: [d] });

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  useMonitoringStore.setState({ state: null, drafts: {}, catalogue: {}, busy: {}, notice: {}, error: null, loading: false });
});
afterEach(() => { act(() => root.unmount()); container.remove(); vi.restoreAllMocks(); });

describe("formatting", () => {
  it("shows ratios as percent, rupees with Indian grouping, and never a percent for a signed move", () => {
    expect(formatValue(0.0207, "ratio")).toBe("2.07%");
    expect(formatValue(1234567, "INR")).toBe("₹12,34,567");
    expect(formatValue(-5150, "INR")).toBe("−₹5,150");
    expect(formatValue(null, "INR")).toBe("–");
    expect(formatValue(1.5276, "ratio", "multiple")).toBe("1.53x");
    expect(formatValue(0.0207, "ratio", "percent")).toBe("2.07%");
    expect(formatMove(3200, "abs", "currency")).toBe("₹3,200 worse than expected");
    expect(formatMove(0.01, "%")).toBe("as expected");
    expect(formatMove(-1.5, "pp")).toBe("1.5 pp better than expected");
    expect(thresholdUnit("ratio", null)).toBe("pp");
    expect(thresholdUnit("additive", "INR")).toBe("%");
  });
});

describe("monitoring store", () => {
  it("sends only the user's choices and tracks unsaved edits", () => {
    const d = domain();
    const draft = draftFromConfig(d.metrics, d.enabled);
    expect(isDirty(d, draft)).toBe(false);
    const edited = { ...draft, metrics: [{ ...draft.metrics[0], label: " Orders placed ", alert: { on: "worse_by" as const, threshold: 15 } }] };
    expect(isDirty(d, edited)).toBe(true);
    expect(toBody(edited, 4)).toEqual({
      enabled: true, expected_version: 4,
      metrics: [{ metric_id: "orders", label: "Orders placed", alert: { on: "worse_by", threshold: 15 }, headline: true }],
    });
  });

  it("stops adding metrics at the server's cap", () => {
    useMonitoringStore.setState({ state: stateWith(domain()), drafts: { commerce: draftFromConfig(domain().metrics, true) } });
    const { toggleMetric } = useMonitoringStore.getState();
    toggleMetric("commerce", "a", true); toggleMetric("commerce", "b", true); toggleMetric("commerce", "c", true);
    expect(useMonitoringStore.getState().drafts.commerce.metrics.map((m) => m.metric_id)).toEqual(["orders", "a", "b"]);
  });

  it("saves, then fetches fresh numbers, and surfaces a server refusal without losing the draft", async () => {
    const saved = domain({ custom: true, version: 1 });
    vi.spyOn(monitoringApi, "save").mockResolvedValue(saved);
    vi.spyOn(monitoringApi, "refresh").mockResolvedValue(saved);
    useMonitoringStore.setState({ state: stateWith(domain()), drafts: { commerce: draftFromConfig(domain().metrics, true) } });
    await useMonitoringStore.getState().save("commerce");
    expect(monitoringApi.save).toHaveBeenCalledWith("commerce", expect.objectContaining({ expected_version: 0 }));
    expect(useMonitoringStore.getState().state?.domains[0].version).toBe(1);

    const { ApiError } = await import("../api/http");
    vi.spyOn(monitoringApi, "save").mockRejectedValue(new ApiError(422, "ghost is not in the metric catalogue"));
    useMonitoringStore.getState().toggleMetric("commerce", "x", true);
    await useMonitoringStore.getState().save("commerce");
    expect(useMonitoringStore.getState().notice.commerce).toEqual({ kind: "error", text: "ghost is not in the metric catalogue" });
    expect(useMonitoringStore.getState().drafts.commerce.metrics.some((m) => m.metric_id === "x")).toBe(true);
  });
});

describe("screens", () => {
  it("dashboard lists what needs attention and draws each metric with its status", () => {
    useMonitoringStore.setState({ state: stateWith(domain()) });
    act(() => root.render(<OpsDashboard />));
    expect(container.querySelector(".ops-state")?.textContent).toContain("1 metric needs attention");
    expect(container.querySelector(".ops-feed .ops-row.status-alert")?.textContent).toContain("Orders");
    expect(container.querySelector(".ops-card.status-alert")).not.toBeNull();
    // The feed row and the card share one stable anchor per metric, so a diagnosis step can link to either.
    expect(container.querySelector("#ops-commerce-orders.ops-card")).not.toBeNull();
    expect(container.querySelector("#feed-ops-commerce-orders")).not.toBeNull();
    expect(container.querySelector(".ops-headline")?.textContent).toContain("Orders");
    expect(container.querySelector("svg.ops-chart")).not.toBeNull();
    expect(container.textContent).toContain("expected 100 for this weekday");
  });

  it("anomaly feed orders alerts by distance from normal and keeps better-than-usual apart", () => {
    const worse = metric({ metric_id: "ctr", name: "CTR", judgement: { ...metric().judgement!, z: -4 } });
    const better = metric({ metric_id: "aov", name: "AOV", status: "good", judgement: { ...metric().judgement!, status: "good", z: 5 } });
    const d = domain({ metrics: [], snapshot: { ...domain().snapshot!, metrics: [worse, better, metric()] } });
    useMonitoringStore.setState({ state: stateWith(d) });
    act(() => root.render(<OpsDashboard />));
    const names = () => [...container.querySelectorAll(".ops-row strong")].map((e) => e.textContent);
    expect(names()).toEqual(["Orders", "CTR"]);
    act(() => { (container.querySelectorAll(".ops-chip")[2] as HTMLButtonElement).click(); });
    expect(names()).toEqual(["AOV"]);
  });

  it("a normal metric reads as within range, never as better than expected", () => {
    const ok = metric({ status: "ok", judgement: { ...metric().judgement!, status: "ok", worse_by: -30 } });
    useMonitoringStore.setState({ state: stateWith(domain({ snapshot: { ...domain().snapshot!, metrics: [ok] } })) });
    act(() => root.render(<OpsDashboard />));
    expect(container.querySelector(".ops-card")?.textContent).toContain("Within normal range");
    expect(container.querySelector(".ops-card")?.textContent).not.toContain("better than expected");
  });

  it("dashboard says plainly when a domain has no numbers yet or is switched off", () => {
    useMonitoringStore.setState({ state: { ...stateWith(domain({ snapshot: null })), domains: [domain({ snapshot: null }), domain({ id: "finance", title: "Finance", enabled: false, snapshot: null })] } });
    act(() => root.render(<OpsDashboard />));
    expect(container.textContent).toContain("No numbers yet");
    expect(container.textContent).toContain("switched off");
  });

  it("monitoring page shows the selection per domain and enables Save only after an edit", () => {
    useMonitoringStore.setState({ state: stateWith(domain()), drafts: { commerce: draftFromConfig(domain().metrics, true) } });
    act(() => root.render(<MonitoringConfig />));
    const save = [...container.querySelectorAll("button")].find((b) => b.textContent?.includes("Save"))!;
    expect(save.disabled).toBe(true);
    expect(container.querySelector('[aria-label="Metrics monitored in Commerce"]')?.children.length).toBe(1);
    act(() => useMonitoringStore.getState().updateMetric("commerce", "orders", { label: "Orders placed" }));
    expect(save.disabled).toBe(false);
    expect(container.textContent).toContain("Unsaved changes");
  });
});
