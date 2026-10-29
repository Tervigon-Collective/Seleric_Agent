import { describe, expect, it } from "vitest";
import { CHART_TYPES, isSupportedChartType, normalizeChartType } from "../api/chartContract";
import { chartPlan, type ChartPlan, type ChartSpec } from "../components/charts/chartPlan";

const specFor = (overrides: Partial<ChartSpec>): ChartSpec => ({
  chart_type: "bar",
  title: "T",
  metrics: ["metric.revenue"],
  dimensions: [],
  series: [{ key: "value", name: "Revenue", type: "bar", yAxisIndex: 0 }],
  xAxis: { type: "category", key: "time" },
  yAxis: [{ type: "value", name: "inr" }],
  data: [{ time: "2026-10-02", value: 300 }, { time: "2026-10-03", value: 350 }],
  ...overrides,
});

/** The plan for a spec, narrowed to one kind so each test reads its own fields. */
function planOf<K extends ChartPlan["kind"]>(kind: K, spec: ChartSpec, opts?: Parameters<typeof chartPlan>[1]) {
  const plan = chartPlan(spec, opts);
  expect(plan?.kind).toBe(kind);
  return plan as Extract<ChartPlan, { kind: K }>;
}

const stackedSpec = () => specFor({
  chart_type: "stacked_bar",
  series: [
    { key: "meta", name: "meta", type: "bar", stack: "total", yAxisIndex: 0 },
    { key: "google", name: "google", type: "bar", stack: "total", yAxisIndex: 0 },
  ],
  data: [
    { time: "2026-10-02", meta: 300, google: 200 },
    { time: "2026-10-03", meta: 400, google: 100 },
  ],
});

describe("chart vocabulary normalization", () => {
  it("normalizes the spellings a stacked-bar request arrives in", () => {
    for (const spelling of ["stacked bar", "stackedBar", "stacked-bar", "stacked_bar", "stacked"]) {
      expect(normalizeChartType(spelling)).toBe("stacked_bar");
    }
  });

  it("normalizes aliases and leaves unknown spellings null", () => {
    expect(normalizeChartType("column")).toBe("bar");
    expect(normalizeChartType("Donut Chart")).toBe("donut");
    expect(normalizeChartType("sparkline")).toBeNull();
    expect(normalizeChartType("")).toBeNull();
    expect(normalizeChartType(undefined)).toBeNull();
  });

  it("keeps every published form renderable", () => {
    for (const chartType of CHART_TYPES) {
      expect(isSupportedChartType(chartType)).toBe(true);
    }
  });
});

describe("chart plan", () => {
  it.each(CHART_TYPES)("plans a drawing for %s", (chartType) => {
    expect(chartPlan(specFor({ chart_type: chartType }))).not.toBeNull();
  });

  it("keeps a stacked bar stacked instead of flattening it", () => {
    const plan = planOf("cartesian", stackedSpec());
    expect(plan.plots).toHaveLength(2);
    expect(plan.plots.every((p) => p.stack === "total" && p.kind === "bar")).toBe(true);
  });

  it("draws a stack over mixed units unstacked so values compare side by side", () => {
    const plan = planOf("cartesian", { ...stackedSpec(), yAxis: [{ type: "value", name: "INR" }, { type: "value", name: "count" }] });
    expect(plan.plots.every((p) => p.stack === undefined)).toBe(true);
  });

  it("leaves a grouped bar unstacked", () => {
    const plan = planOf("cartesian", specFor({
      chart_type: "grouped_bar",
      series: [
        { key: "meta", name: "meta", type: "bar", yAxisIndex: 0 },
        { key: "google", name: "google", type: "bar", yAxisIndex: 0 },
      ],
    }));
    expect(plan.plots.every((p) => p.stack === undefined)).toBe(true);
  });

  it("renders a donut hollow and keeps the backend radius when given", () => {
    const plan = planOf("slices", specFor({
      chart_type: "donut",
      series: [{ key: "value", name: "Revenue", type: "donut", radius: ["45%", "72%"] }],
      data: [{ name: "meta", value: 300 }],
    }));
    expect(plan.form).toBe("donut");
    expect(plan.radius).toEqual(["45%", "72%"]);
  });

  it("turns heatmap rows into index triples", () => {
    const plan = planOf("heatmap", specFor({
      chart_type: "heatmap",
      series: [{ key: "value", name: "Revenue", type: "heatmap" }],
      xAxis: { type: "category", key: "x", data: ["d1", "d2"] },
      yAxis: [{ type: "category", key: "y", data: ["Revenue", "Orders"] }],
      data: [
        { x: "d1", y: "Revenue", value: 300 },
        { x: "d2", y: "Orders", value: 12 },
      ],
    }));
    expect(plan.cells).toEqual([[0, 0, 300], [1, 1, 12]]);
  });

  it("returns null for a form it cannot render so the UI fails loudly", () => {
    expect(chartPlan(specFor({ chart_type: "sparkline" }))).toBeNull();
  });

  it("switches a bar spec to lines without stacking", () => {
    const plan = planOf("cartesian", stackedSpec(), { mark: "line" });
    expect(plan.plots.every((p) => p.kind === "line" && p.stack === undefined)).toBe(true);
  });

  it("keeps the backend stack when the mark override is bars", () => {
    const plan = planOf("cartesian", stackedSpec(), { mark: "bar" });
    expect(plan.plots.every((p) => p.kind === "bar" && p.stack === "total")).toBe(true);
  });
});

describe("new forms", () => {
  it("plans panels with one series each, so a count never shares a rupee scale", () => {
    const plan = planOf("panels", specFor({
      chart_type: "grouped_bar",
      panels: [
        { title: "Net sales", unit: "INR", series: ["Net sales"], mark: "bar" },
        { title: "Orders", unit: "count", series: ["Orders"], mark: "bar" },
        { title: "AOV", unit: "INR", series: ["AOV"], mark: "bar" },
      ],
      series: [
        { key: "Net sales", metric: "net_sales", type: "bar", yAxisIndex: 0 },
        { key: "Orders", metric: "orders", type: "bar", yAxisIndex: 1 },
        { key: "AOV", metric: "aov", type: "bar", yAxisIndex: 2 },
      ],
      data: [{ time: "2026-10-08", "Net sales": 290000, Orders: 151, AOV: 1900 }, { time: "2026-10-12", "Net sales": 258000, Orders: 136, AOV: 1898 }],
    }));
    expect(plan.panels.map((p) => p.plots.map((s) => s.key))).toEqual([["Net sales"], ["Orders"], ["AOV"]]);
  });

  it("lays a ranking out horizontally, in the order the rows arrive (largest first)", () => {
    const plan = planOf("cartesian", specFor({
      chart_type: "horizontal_bar", xAxis: { type: "category", key: "name" },
      series: [{ key: "value", name: "Spend", type: "bar", yAxisIndex: 0 }],
      data: [{ name: "A very long campaign name", value: 5 }, { name: "B", value: 3 }],
    }));
    expect(plan.horizontal).toBe(true);
    expect(plan.xKey).toBe("name");
  });

  it("draws a combo as bars and a line on two axes, the second without gridlines", () => {
    const plan = planOf("cartesian", specFor({
      chart_type: "combo",
      series: [{ key: "Spend", type: "bar", yAxisIndex: 0 }, { key: "ROAS", type: "line", yAxisIndex: 1 }],
      yAxis: [{ type: "value", name: "INR" }, { type: "value", name: "ratio", position: "right" }],
      data: [{ time: "a", Spend: 1, ROAS: 2 }, { time: "b", Spend: 2, ROAS: 3 }],
    }));
    expect(plan.plots.map((p) => [p.kind, p.axis])).toEqual([["bar", 0], ["line", 1]]);
    expect(plan.axes.map((a) => a.grid)).toEqual([true, false]);
  });

  it("floats waterfall steps from the running total and ends on the total", () => {
    const plan = planOf("waterfall", specFor({
      chart_type: "waterfall", xAxis: { type: "category", key: "name" },
      series: [{ key: "value", name: "Profit", type: "waterfall" }],
      data: [
        { name: "Net sales", value: 100, kind: "delta" },
        { name: "Costs", value: -30, kind: "delta" },
        { name: "Profit", value: 70, kind: "total" },
      ],
    }));
    expect(plan.steps.map((s) => [s.from, s.to])).toEqual([[0, 100], [70, 100], [0, 70]]);
    expect(plan.steps.map((s) => s.tone)).toEqual(["up", "down", "total"]);
  });

  it("draws a treemap from slice rows", () => {
    const plan = planOf("slices", specFor({
      chart_type: "treemap", xAxis: {}, series: [{ key: "value", name: "Share", type: "treemap" }],
      data: [{ name: "a", value: 3 }, { name: "b", value: 1 }],
    }));
    expect(plan.form).toBe("treemap");
    expect(plan.slices).toHaveLength(2);
  });

  it("states each causal driver's classification as data, not only as colour", () => {
    const plan = planOf("causal", specFor({
      chart_type: "causal_graph", series: [], data: [],
      nodes: [
        { id: "orders", name: "Orders", role: "outcome" },
        { id: "meta", name: "Meta spend", role: "driver", classification: "supported_cause", share_of_change: -0.46 },
      ],
      links: [{ source: "meta", target: "orders" }],
    }));
    expect(plan.outcome).toBe("Orders");
    expect(plan.drivers.map((d) => [d.id, d.classification])).toEqual([["meta", "supported_cause"]]);
  });
});
