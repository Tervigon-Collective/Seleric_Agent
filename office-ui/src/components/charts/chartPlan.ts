/**
 * Pure chart planning. The backend sends one generic spec; this turns it into
 * what to draw (series kinds, stacking, axes, panels, waterfall spans, …)
 * with no rendering library involved, so the rules are testable on their own.
 */
import { normalizeChartType, type ChartType } from "../../api/chartContract";

export interface ChartSeries {
  key: string;
  name?: string;
  /** Metric id the series was drawn from (the backend keeps it so axes are assigned by metric, not by label). */
  metric?: string;
  type?: string;
  stack?: string;
  yAxisIndex?: number;
  areaStyle?: Record<string, unknown>;
  radius?: unknown[];
  values_key?: string;
}

export interface CausalGraphNode {
  id: string;
  name?: string;
  role?: "outcome" | "driver" | "confounder" | string;
  classification?: string | null;
  status?: string | null;
  share_of_change?: number | null;
}

export interface CausalGraphLink {
  source: string;
  target: string;
  kind?: "treatment" | "adjustment" | string;
  classification?: string | null;
}

/** Series of a different kind or size drawn in their own panel (own axis, shared x) instead of on twin axes. */
export interface ChartPanel {
  title: string;
  unit?: string;
  series: string[];
  mark?: "bar" | "line";
}

export interface ChartSpec {
  chart_type: string;
  panels?: ChartPanel[];
  title?: string;
  metrics: string[];
  dimensions: string[];
  series: ChartSeries[];
  xAxis?: Record<string, unknown> & { key?: string; type?: string; data?: string[] };
  yAxis?: Array<Record<string, unknown>>;
  radar?: { indicator: Array<{ name: string; max: number }> };
  data: Record<string, any>[];
  /** DoWhy DAG nodes/links when ``chart_type`` is ``causal_graph``. */
  nodes?: CausalGraphNode[];
  links?: CausalGraphLink[];
  warnings?: string[];
  downloadable_csv_url?: string;
}

export type CartesianMark = "bar" | "line";
export const MARK_SWITCHABLE: ReadonlySet<string> = new Set(["line", "bar", "stacked_bar", "grouped_bar"]);

type Row = Record<string, any>;

export function formatChartValue(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value ?? "");
  const abs = Math.abs(value);
  const fractionDigits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  // Indian grouping (22,53,608), as the answer text writes it; never shows float noise.
  return value.toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: fractionDigits });
}

export function formatAxisTick(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value ?? "");
  const abs = Math.abs(value);
  const trimmed = (n: number) => n.toLocaleString("en-IN", { maximumFractionDigits: 1 });
  if (abs >= 10_000_000) return `${trimmed(value / 10_000_000)}Cr`;
  if (abs >= 100_000) return `${trimmed(value / 100_000)}L`;
  if (abs >= 1_000) return `${trimmed(value / 1_000)}k`;
  return formatChartValue(value);
}

/** Distinct unit names across the spec's y axes (empty when single-unit). */
export function mixedUnitAxes(spec: ChartSpec): string[] {
  const names = (spec.yAxis ?? [])
    .map((a) => String((a as Record<string, unknown>).name ?? "").trim().toLowerCase())
    .filter(Boolean);
  return [...new Set(names)];
}

/** A stacked form over multiple units is misleading — render it unstacked. */
export function mustUnstack(spec: ChartSpec): boolean {
  if (spec.chart_type !== "stacked_bar") return false;
  if (!Array.isArray(spec.series) || spec.series.length < 2) return false;
  return mixedUnitAxes(spec).length > 1;
}

const PANEL_BLOCK_PX = 128;
const PANEL_TOP_PX = 30;
const PANEL_BOTTOM_PX = 56;

/** Pixel height a spec wants: panels and long rankings grow with their content, the rest use the default. */
export function chartHeightPx(spec: ChartSpec): number {
  const panels = spec.panels?.length ?? 0;
  if (panels > 1) return PANEL_TOP_PX + panels * PANEL_BLOCK_PX + PANEL_BOTTOM_PX;
  const rows = Array.isArray(spec.data) ? spec.data.length : 0;
  const type = normalizeChartType(spec.chart_type);
  if (type === "horizontal_bar") return Math.min(620, Math.max(260, 96 + rows * 28));
  if (type === "waterfall") return rows > 6 ? 380 : 320;
  return 300;
}

export interface Plot {
  key: string;
  name: string;
  kind: "bar" | "line" | "area";
  /** Index of the y axis this series is read against. */
  axis: number;
  stack?: string;
  fillOpacity?: number;
}

export interface AxisPlan { index: number; name?: string; grid: boolean }

export interface WaterfallStep { label: string; from: number; to: number; value: number; tone: "up" | "down" | "total" }

export type ChartPlan =
  | { kind: "cartesian"; xKey: string; horizontal: boolean; plots: Plot[]; axes: AxisPlan[] }
  | { kind: "panels"; xKey: string; panels: Array<{ title: string; unit?: string; plots: Plot[] }> }
  | { kind: "waterfall"; steps: WaterfallStep[] }
  | { kind: "slices"; form: "pie" | "donut" | "funnel" | "treemap"; radius: [string, string]; slices: Array<{ name: string; value: number }> }
  | { kind: "radar"; indicators: Array<{ name: string; max: number }>; series: Array<{ key: string; name: string }> }
  | { kind: "scatter"; name: string; points: Array<{ x: number; y: number }> }
  | { kind: "heatmap"; xs: string[]; ys: string[]; cells: Array<[number, number, number]>; min: number; max: number }
  | { kind: "causal"; outcome: string | null; drivers: CausalGraphNode[]; links: CausalGraphLink[] };

function xKeyOf(spec: ChartSpec, fallback = "name"): string {
  return spec.xAxis?.key ?? fallback;
}

function cartesianPlots(spec: ChartSpec, mark?: CartesianMark): Plot[] {
  const unstack = mustUnstack(spec);
  return (spec.series ?? []).map((s) => {
    const type = mark ?? s.type ?? "bar";
    const isArea = spec.chart_type === "area" || !!s.areaStyle;
    const kind: Plot["kind"] = type === "bar" ? "bar" : isArea ? "area" : "line";
    return {
      key: s.key,
      name: s.name ?? s.key,
      kind,
      axis: s.yAxisIndex ?? 0,
      // The backend already emits `stack: "total"` for stacked forms; never restack.
      // A user line override never stacks (summed lines mislead), unless it is a forecast band.
      stack: mark === "line" && !s.areaStyle ? undefined : unstack ? undefined : s.stack,
      fillOpacity: kind === "area" ? Number((s.areaStyle as Record<string, unknown> | undefined)?.opacity ?? 0.12) : undefined,
    };
  });
}

function comboPlots(spec: ChartSpec): Plot[] {
  return (spec.series ?? []).map((s) => ({
    key: s.key, name: s.name ?? s.key, kind: s.type === "line" ? "line" : "bar", axis: s.yAxisIndex ?? 0,
  }));
}

function axesFor(spec: ChartSpec, plots: Plot[], form: string): AxisPlan[] {
  const used = [...new Set(plots.map((p) => p.axis))].sort((a, b) => a - b);
  return used.map((index) => ({
    index,
    name: (spec.yAxis?.[index] as Record<string, unknown> | undefined)?.name as string | undefined,
    // A combo's second axis draws no gridlines: one grid, read against the first axis.
    grid: !(form === "combo" && index > 0),
  }));
}

function panelsPlan(spec: ChartSpec): ChartPlan {
  const byKey = new Map((spec.series ?? []).map((s) => [s.key, s]));
  return {
    kind: "panels",
    xKey: xKeyOf(spec),
    panels: (spec.panels ?? []).map((panel) => ({
      title: panel.title,
      unit: panel.unit,
      plots: panel.series.flatMap((key) => {
        const s = byKey.get(key);
        return s ? [{ key, name: s.name ?? key, kind: (panel.mark ?? "bar") as Plot["kind"], axis: 0 }] : [];
      }),
    })),
  };
}

function waterfallPlan(spec: ChartSpec): ChartPlan {
  const rows: Row[] = Array.isArray(spec.data) ? spec.data : [];
  const xKey = xKeyOf(spec, Object.keys(rows[0] ?? {}).find((k) => k !== "value" && k !== "kind") ?? "name");
  let running = 0;
  return {
    kind: "waterfall",
    steps: rows.map((row) => {
      const value = Number(row.value) || 0;
      const kind = String(row.kind ?? "delta");
      const from = kind === "delta" ? running : 0;
      const to = kind === "delta" ? running + value : value;
      if (kind !== "total") running = kind === "delta" ? to : value;
      return {
        label: String(row[xKey] ?? ""), from: Math.min(from, to), to: Math.max(from, to), value,
        tone: kind !== "delta" ? "total" : value >= 0 ? "up" : "down",
      } as WaterfallStep;
    }),
  };
}

function radiusFor(spec: ChartSpec): [string, string] {
  const radius = spec.series?.[0]?.radius;
  return Array.isArray(radius) && radius.length === 2 ? (radius as [string, string]) : ["40%", "70%"];
}

function heatmapPlan(spec: ChartSpec): ChartPlan {
  // Heatmap rows are x/y/value against the axis categories; cells are [xIndex, yIndex, value].
  const xs = (spec.xAxis?.data as string[]) ?? [];
  const ys = ((spec.yAxis?.[0] as Record<string, unknown> | undefined)?.data as string[]) ?? [];
  const xi = new Map(xs.map((d, i) => [d, i]));
  const yi = new Map(ys.map((d, i) => [d, i]));
  const rows: Row[] = Array.isArray(spec.data) ? spec.data : [];
  const cells = rows.map((row) => [xi.get(row.x) ?? 0, yi.get(row.y) ?? 0, Number(row.value) || 0] as [number, number, number]);
  const values = cells.map((c) => c[2]);
  return { kind: "heatmap", xs, ys, cells, min: Math.min(...values, 0), max: Math.max(...values, 1) };
}

/** What to draw for a spec, or null for a form this UI cannot render (the caller then fails loudly). */
export function chartPlan(spec: ChartSpec, opts: { mark?: CartesianMark } = {}): ChartPlan | null {
  const type = normalizeChartType(spec?.chart_type) as ChartType | null;
  if (!type) return null;
  const rows: Row[] = Array.isArray(spec.data) ? spec.data : [];
  switch (type) {
    case "line": case "area": case "bar": case "stacked_bar": case "grouped_bar": case "combo": {
      if ((spec.panels?.length ?? 0) > 1) return panelsPlan(spec);
      const plots = type === "combo" ? comboPlots(spec) : cartesianPlots(spec, opts.mark);
      return { kind: "cartesian", xKey: xKeyOf(spec, "time"), horizontal: false, plots, axes: axesFor(spec, plots, type) };
    }
    case "horizontal_bar": {
      const plots = (spec.series ?? []).map((s) => ({ key: s.key, name: s.name ?? s.key, kind: "bar" as const, axis: 0 }));
      return { kind: "cartesian", xKey: "name", horizontal: true, plots, axes: axesFor(spec, plots, type) };
    }
    case "waterfall": return waterfallPlan(spec);
    case "pie": return { kind: "slices", form: "pie", radius: ["0%", "70%"], slices: rows.map((r) => ({ name: String(r.name), value: Number(r.value) || 0 })) };
    case "donut": case "funnel": case "treemap":
      return { kind: "slices", form: type, radius: radiusFor(spec), slices: rows.map((r) => ({ name: String(r.name), value: Number(r.value) || 0 })) };
    case "scatter":
      return { kind: "scatter", name: spec.series?.[0]?.name ?? spec.series?.[0]?.key ?? "Value", points: rows.map((r) => ({ x: Number(r.x), y: Number(r.y) })) };
    case "radar":
      return { kind: "radar", indicators: spec.radar?.indicator ?? [], series: (spec.series ?? []).map((s) => ({ key: s.key, name: s.name ?? s.key })) };
    case "heatmap": return heatmapPlan(spec);
    case "causal_graph": {
      const nodes = spec.nodes ?? [];
      return {
        kind: "causal",
        outcome: nodes.find((n) => n.role === "outcome")?.name ?? nodes.find((n) => n.role === "outcome")?.id ?? null,
        drivers: nodes.filter((n) => n.role !== "outcome"),
        links: spec.links ?? [],
      };
    }
    default: return null;
  }
}
