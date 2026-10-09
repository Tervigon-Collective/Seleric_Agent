/**
 * Structured drill-down context for chart/table follow-ups.
 *
 * When the user clicks a data point and asks about it, the follow-up question
 * carries the exact selected values (dimension, metric names, numbers) — not
 * vague keywords or screen text. The agent re-resolves these against live
 * evidence; nothing here is presented as verified data, only as the pointer
 * to what the user is looking at.
 */

export interface DrillMetric {
  key: string;
  label: string;
  value: unknown;
}

export interface DrillContext {
  chartTitle: string;
  dimensionKey: string | null;
  dimensionValue: string | null;
  metrics: DrillMetric[];
}

export interface DrillSeries {
  key: string;
  name?: string;
}

const fmt = (value: unknown): string => {
  if (typeof value === "number" && Number.isFinite(value)) {
    const abs = Math.abs(value);
    const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
    return value.toLocaleString("en-US", { maximumFractionDigits: digits });
  }
  return String(value ?? "—");
};

/**
 * Build the structured context for a selected row. `series` are the spec's
 * series descriptors (key + display name); only series present in the row
 * with non-null values are kept, so the context never invents numbers.
 */
export function buildPointContext(args: {
  chartTitle?: string;
  dimensionKey?: string | null;
  row: Record<string, unknown>;
  series: DrillSeries[];
  columns: string[];
}): DrillContext {
  const { row, series, columns } = args;
  const dimensionKey = args.dimensionKey && columns.includes(args.dimensionKey)
    ? args.dimensionKey
    : null;
  const metrics: DrillMetric[] = [];
  for (const s of series) {
    if (!(s.key in row)) continue;
    const value = row[s.key];
    if (value === null || value === undefined || value === "") continue;
    metrics.push({ key: s.key, label: s.name ?? s.key, value });
  }
  // Fallback for tables without a series vocabulary: every non-dimension
  // column with a value counts as a metric.
  if (!metrics.length) {
    for (const col of columns) {
      if (col === dimensionKey || !(col in row)) continue;
      const value = row[col];
      if (value === null || value === undefined || value === "") continue;
      metrics.push({ key: col, label: col, value });
    }
  }
  return {
    chartTitle: args.chartTitle?.trim() || "this analysis",
    dimensionKey,
    dimensionValue: dimensionKey ? String(row[dimensionKey] ?? "—") : null,
    metrics,
  };
}

const scopeLine = (ctx: DrillContext): string => {
  const bits: string[] = [];
  if (ctx.dimensionKey && ctx.dimensionValue) {
    bits.push(`${ctx.dimensionKey} = ${ctx.dimensionValue}`);
  }
  for (const m of ctx.metrics) {
    bits.push(`${m.label} = ${fmt(m.value)}`);
  }
  return bits.length ? ` (${bits.join(", ")})` : "";
};

export type FollowUpKind = "ask" | "compare";

/**
 * Render a follow-up question that pins the selected point with exact values.
 * Returns null when the context is empty (no metrics), so callers can hide
 * the actions instead of submitting a content-free question.
 */
export function buildFollowUp(kind: FollowUpKind, ctx: DrillContext): string | null {
  if (!ctx.metrics.length) return null;
  const scope = scopeLine(ctx);
  if (kind === "ask") {
    return `About "${ctx.chartTitle}"${scope}: what drove this result?`;
  }
  const names = ctx.metrics.map((m) => m.label).join(" and ");
  return `About "${ctx.chartTitle}"${scope}: compare ${names} with the previous equivalent period and explain what changed.`;
}
