import { describe, expect, it } from "vitest";
import { buildFollowUp, buildPointContext } from "../api/drillContext";

const SERIES = [
  { key: "net_sales", name: "Net sales" },
  { key: "ad_spend", name: "Ad spend" },
];
const COLUMNS = ["time", "net_sales", "ad_spend"];
const ROW = { time: "2026-10-05", net_sales: 717245.58, ad_spend: 429000 };

describe("buildPointContext", () => {
  it("pins the dimension and every valued series, nothing more", () => {
    const ctx = buildPointContext({
      chartTitle: "7-day performance",
      dimensionKey: "time",
      row: ROW,
      series: SERIES,
      columns: COLUMNS,
    });
    expect(ctx).toEqual({
      chartTitle: "7-day performance",
      dimensionKey: "time",
      dimensionValue: "2026-10-05",
      metrics: [
        { key: "net_sales", label: "Net sales", value: 717245.58 },
        { key: "ad_spend", label: "Ad spend", value: 429000 },
      ],
    });
  });

  it("skips null values instead of inventing numbers", () => {
    const ctx = buildPointContext({
      chartTitle: "T",
      dimensionKey: "time",
      row: { time: "2026-10-05", net_sales: null, ad_spend: 429000 },
      series: SERIES,
      columns: COLUMNS,
    });
    expect(ctx.metrics).toEqual([{ key: "ad_spend", label: "Ad spend", value: 429000 }]);
  });

  it("falls back to row columns when no series vocabulary applies", () => {
    const ctx = buildPointContext({
      row: { campaign: "Diwali", spend: 12000 },
      series: [],
      columns: ["campaign", "spend"],
    });
    expect(ctx.dimensionKey).toBeNull();
    expect(ctx.metrics).toEqual([
      { key: "campaign", label: "campaign", value: "Diwali" },
      { key: "spend", label: "spend", value: 12000 },
    ]);
  });
});

describe("buildFollowUp", () => {
  const ctx = buildPointContext({
    chartTitle: "7-day performance",
    dimensionKey: "time",
    row: ROW,
    series: SERIES,
    columns: COLUMNS,
  });

  it("carries exact values into an ask follow-up", () => {
    const q = buildFollowUp("ask", ctx);
    expect(q).toContain("2026-10-05");
    expect(q).toContain("Net sales");
    expect(q).toContain("Ad spend");
    expect(q).toContain("7-day performance");
  });

  it("names the metrics in a previous-period comparison", () => {
    const q = buildFollowUp("compare", ctx);
    expect(q).toContain("previous equivalent period");
    expect(q).toContain("Net sales and Ad spend");
    expect(q).toContain("2026-10-05");
  });

  it("returns null when the context has no metrics", () => {
    expect(buildFollowUp("ask", { ...ctx, metrics: [] })).toBeNull();
    expect(buildFollowUp("compare", { ...ctx, metrics: [] })).toBeNull();
  });
});
