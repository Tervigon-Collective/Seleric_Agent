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
});
