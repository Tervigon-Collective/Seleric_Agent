import { describe, expect, it } from "vitest";
import { CHART_TYPES, isSupportedChartType, normalizeChartType } from "../api/chartContract";
import { renderChartOption, type ChartSpec } from "../components/charts/ChartRenderer";

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

describe("chart option builders", () => {
  it.each(CHART_TYPES)("builds an option for %s", (chartType) => {
    const spec = specFor({ chart_type: chartType });
    expect(renderChartOption(spec)).not.toBeNull();
  });

  it("keeps a stacked bar stacked instead of flattening it", () => {
    const option = renderChartOption(
      specFor({
        chart_type: "stacked_bar",
        series: [
          { key: "meta", name: "meta", type: "bar", stack: "total", yAxisIndex: 0 },
          { key: "google", name: "google", type: "bar", stack: "total", yAxisIndex: 0 },
        ],
        data: [
          { time: "2026-10-02", meta: 300, google: 200 },
          { time: "2026-10-03", meta: 400, google: 100 },
        ],
      }),
    ) as { series: Array<Record<string, unknown>> };
    expect(option.series).toHaveLength(2);
    expect(option.series.every((s) => s.stack === "total")).toBe(true);
    expect(option.series.every((s) => s.type === "bar")).toBe(true);
  });

  it("leaves a grouped bar unstacked", () => {
    const option = renderChartOption(
      specFor({
        chart_type: "grouped_bar",
        series: [
          { key: "meta", name: "meta", type: "bar", yAxisIndex: 0 },
          { key: "google", name: "google", type: "bar", yAxisIndex: 0 },
        ],
      }),
    ) as { series: Array<Record<string, unknown>> };
    expect(option.series.every((s) => s.stack === undefined)).toBe(true);
  });

  it("renders a donut as a hollow pie and keeps the backend radius when given", () => {
    const option = renderChartOption(
      specFor({
        chart_type: "donut",
        series: [{ key: "value", name: "Revenue", type: "donut", radius: ["45%", "72%"] }],
        data: [{ name: "meta", value: 300 }],
      }),
    ) as { series: Array<Record<string, unknown>> };
    expect(option.series[0].type).toBe("pie");
    expect(option.series[0].radius).toEqual(["45%", "72%"]);
  });

  it("turns heatmap rows into index triples", () => {
    const option = renderChartOption(
      specFor({
        chart_type: "heatmap",
        series: [{ key: "value", name: "Revenue", type: "heatmap" }],
        xAxis: { type: "category", key: "x", data: ["d1", "d2"] },
        yAxis: [{ type: "category", key: "y", data: ["Revenue", "Orders"] }],
        data: [
          { x: "d1", y: "Revenue", value: 300 },
          { x: "d2", y: "Orders", value: 12 },
        ],
      }),
    ) as { series: Array<{ data: unknown[][] }> };
    expect(option.series[0].data).toEqual([
      [0, 0, 300],
      [1, 1, 12],
    ]);
  });

  it("returns null for a form it cannot render so the UI fails loudly", () => {
    expect(renderChartOption(specFor({ chart_type: "sparkline" }))).toBeNull();
  });

  it("switches a bar spec to lines without stacking", () => {
    const option = renderChartOption(
      specFor({
        chart_type: "stacked_bar",
        series: [
          { key: "meta", name: "meta", type: "bar", stack: "total", yAxisIndex: 0 },
          { key: "google", name: "google", type: "bar", stack: "total", yAxisIndex: 0 },
        ],
        data: [
          { time: "2026-10-02", meta: 300, google: 200 },
          { time: "2026-10-03", meta: 400, google: 100 },
        ],
      }),
      undefined,
      { mark: "line" },
    ) as { series: Array<Record<string, unknown>> };
    expect(option.series.every((s) => s.type === "line")).toBe(true);
    expect(option.series.every((s) => s.stack === undefined)).toBe(true);
  });

  it("keeps the backend stack when the mark override is bars", () => {
    const option = renderChartOption(
      specFor({
        chart_type: "stacked_bar",
        series: [
          { key: "meta", name: "meta", type: "bar", stack: "total", yAxisIndex: 0 },
          { key: "google", name: "google", type: "bar", stack: "total", yAxisIndex: 0 },
        ],
        data: [
          { time: "2026-10-02", meta: 300, google: 200 },
          { time: "2026-10-03", meta: 400, google: 100 },
        ],
      }),
      undefined,
      { mark: "bar" },
    ) as { series: Array<Record<string, unknown>> };
    expect(option.series.every((s) => s.type === "bar")).toBe(true);
    expect(option.series.every((s) => s.stack === "total")).toBe(true);
  });
});

describe("new forms", () => {
  const opt = (spec: ChartSpec) => renderChartOption(spec) as Record<string, any>;

  it("draws panels with one grid, axis and series each, so a count never shares a rupee scale", () => {
    const option = opt(specFor({
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
    expect(option.grid).toHaveLength(3);
    expect(option.yAxis).toHaveLength(3);
    expect(option.series.map((s: any) => [s.yAxisIndex, s.xAxisIndex])).toEqual([[0, 0], [1, 1], [2, 2]]);
    expect(option.legend.show).toBe(false);
  });

  it("lays a ranking out horizontally with the largest on top", () => {
    const option = opt(specFor({
      chart_type: "horizontal_bar", xAxis: { type: "category", key: "name" },
      series: [{ key: "value", name: "Spend", type: "bar", yAxisIndex: 0 }],
      data: [{ name: "A very long campaign name", value: 5 }, { name: "B", value: 3 }],
    }));
    expect(option.xAxis.type).toBe("value");
    expect(option.yAxis.type).toBe("category");
    expect(option.yAxis.inverse).toBe(true);
  });

  it("draws a combo as bars and a line on two axes, the second without gridlines", () => {
    const option = opt(specFor({
      chart_type: "combo",
      series: [{ key: "Spend", type: "bar", yAxisIndex: 0 }, { key: "ROAS", type: "line", yAxisIndex: 1 }],
      yAxis: [{ type: "value", name: "INR" }, { type: "value", name: "ratio", position: "right" }],
      data: [{ time: "a", Spend: 1, ROAS: 2 }, { time: "b", Spend: 2, ROAS: 3 }],
    }));
    expect(option.series.map((s: any) => [s.type, s.yAxisIndex])).toEqual([["bar", 0], ["line", 1]]);
    expect(option.yAxis[1].splitLine.show).toBe(false);
  });

  it("floats waterfall steps from the running total and ends on the total", () => {
    const option = opt(specFor({
      chart_type: "waterfall", xAxis: { type: "category", key: "name" },
      series: [{ key: "value", name: "Profit", type: "waterfall" }],
      data: [
        { name: "Net sales", value: 100, kind: "delta" },
        { name: "Costs", value: -30, kind: "delta" },
        { name: "Profit", value: 70, kind: "total" },
      ],
    }));
    const spans = option.series[0].data.map((d: any) => d.value.slice(1, 3));
    expect(spans).toEqual([[0, 100], [70, 100], [0, 70]]);
    expect(option.series[0].data[1].itemStyle.color).not.toBe(option.series[0].data[0].itemStyle.color);
  });

  it("draws a treemap from slice rows", () => {
    const option = opt(specFor({
      chart_type: "treemap", xAxis: {}, series: [{ key: "value", name: "Share", type: "treemap" }],
      data: [{ name: "a", value: 3 }, { name: "b", value: 1 }],
    }));
    expect(option.series[0].type).toBe("treemap");
    expect(option.series[0].data).toHaveLength(2);
  });
});
