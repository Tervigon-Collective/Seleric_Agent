/**
 * Mirror of the backend chart vocabulary
 * (`src/seleric_swarm/analytics/chart_vocabulary.py`).
 *
 * The backend publishes the same list at `GET /v1/office/chart-types`
 * (`loadChartVocabulary`) so drift between the two sides is detectable at
 * runtime instead of showing up as a blank chart frame.
 */

export const CHART_TYPES = [
  "line",
  "area",
  "bar",
  "stacked_bar",
  "grouped_bar",
  "pie",
  "donut",
  "funnel",
  "scatter",
  "radar",
  "heatmap",
] as const;

export type ChartType = (typeof CHART_TYPES)[number];

/** Every form the backend can emit. Keyed by canonical name. */
export const CHART_REGISTRY: Record<ChartType, { label: string; description: string }> = {
  line: { label: "Line", description: "Continuous change over time for one or more series." },
  area: { label: "Area", description: "Continuous change over time with filled magnitude." },
  bar: { label: "Bar", description: "Discrete comparison by period or category." },
  stacked_bar: {
    label: "Stacked bar",
    description: "Part-to-whole contribution of several series per period or category.",
  },
  grouped_bar: {
    label: "Grouped bar",
    description: "Side-by-side bars comparing several series per period or category.",
  },
  pie: { label: "Pie", description: "Shares of a whole across categories." },
  donut: { label: "Donut", description: "Shares of a whole with a hollow centre." },
  funnel: { label: "Funnel", description: "Sequential stage drop-off." },
  scatter: { label: "Scatter", description: "Relationship between two measures." },
  radar: { label: "Radar", description: "Several measures compared on a common radial scale." },
  heatmap: { label: "Heatmap", description: "Magnitude at the intersection of two dimensions." },
};

/** Accepted spellings -> canonical form. Mirrors the backend alias table. */
const CHART_ALIASES: Record<string, ChartType> = {
  line: "line",
  area: "area",
  bar: "bar",
  stacked_bar: "stacked_bar",
  grouped_bar: "grouped_bar",
  pie: "pie",
  donut: "donut",
  funnel: "funnel",
  scatter: "scatter",
  radar: "radar",
  heatmap: "heatmap",
  // bars
  stacked: "stacked_bar",
  stack: "stacked_bar",
  stackedbar: "stacked_bar",
  stacked_column: "stacked_bar",
  stackedcolumn: "stacked_bar",
  column: "bar",
  columns: "bar",
  column_chart: "bar",
  vertical_bar: "bar",
  grouped: "grouped_bar",
  group: "grouped_bar",
  groupedbar: "grouped_bar",
  clustered: "grouped_bar",
  clustered_bar: "grouped_bar",
  multi_bar: "grouped_bar",
  bar_chart: "bar",
  horizontal_bar: "bar",
  bar_over_time: "bar",
  // time series
  trend: "line",
  trendline: "line",
  time_series: "line",
  timeseries: "line",
  line_chart: "line",
  area_chart: "area",
  // composition
  pie_chart: "pie",
  donut_chart: "donut",
  doughnut: "donut",
  composition: "pie",
  share: "pie",
  shares: "pie",
  // stages
  funnel_chart: "funnel",
  stages: "funnel",
  conversion_funnel: "funnel",
  // correlation
  scatter_plot: "scatter",
  scatterplot: "scatter",
  bubble: "scatter",
  bubble_chart: "scatter",
  correlation: "scatter",
  // multivariate
  radar_chart: "radar",
  spider: "radar",
  spider_chart: "radar",
  kiviat: "radar",
  // density
  heat_map: "heatmap",
  heatmap_chart: "heatmap",
  density: "heatmap",
  matrix: "heatmap",
};

function aliasKey(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .split("_")
    .filter(Boolean)
    .join("_");
}

/** Resolve any accepted spelling to a canonical form, or null if unsupported. */
export function normalizeChartType(value: unknown): ChartType | null {
  if (typeof value !== "string" || !value.trim()) return null;
  return CHART_ALIASES[aliasKey(value)] ?? null;
}

export function isSupportedChartType(value: unknown): value is ChartType {
  return normalizeChartType(value) !== null;
}

export function chartTypeLabel(value: unknown): string {
  const normalized = normalizeChartType(value);
  return normalized ? CHART_REGISTRY[normalized].label : "unknown form";
}

/** Forms drawn on a category/value plane. */
export const CARTESIAN_CHART_TYPES: readonly ChartType[] = [
  "line",
  "area",
  "bar",
  "stacked_bar",
  "grouped_bar",
  "scatter",
];

/** Forms whose payload carries slice rows instead of cartesian rows. */
export const COMPOSITION_CHART_TYPES: readonly ChartType[] = ["pie", "donut", "funnel"];

export interface ChartTypeCatalogEntry {
  chart_type: string;
  label: string;
  description: string;
  family: string;
  stacking: string;
  axes: string;
  aliases: string[];
}

export interface ChartVocabulary {
  chart_types: ChartTypeCatalogEntry[];
  default_chart_type: string;
}

const VOCABULARY_URL = "/v1/office/chart-types";

/**
 * Fetch the backend vocabulary, used to confirm this module is in sync.
 * Resolves to null when the endpoint is unavailable; callers fall back to the
 * local mirror.
 */
export async function loadChartVocabulary(): Promise<ChartVocabulary | null> {
  try {
    const response = await fetch(VOCABULARY_URL, { headers: { Accept: "application/json" } });
    if (!response.ok) return null;
    const payload = (await response.json()) as ChartVocabulary;
    return Array.isArray(payload?.chart_types) ? payload : null;
  } catch {
    return null;
  }
}

/** Forms the backend publishes that this mirror cannot render. Empty when in sync. */
export function missingFromLocalVocabulary(vocabulary: ChartVocabulary | null): string[] {
  if (!vocabulary) return [];
  return vocabulary.chart_types
    .map((entry) => entry.chart_type)
    .filter((type) => !isSupportedChartType(type));
}

/** Forms this mirror knows that the backend does not publish. Empty when in sync. */
export function missingFromBackend(vocabulary: ChartVocabulary | null): string[] {
  if (!vocabulary) return [];
  const published = new Set(vocabulary.chart_types.map((entry) => entry.chart_type));
  return CHART_TYPES.filter((type) => !published.has(type));
}

let driftChecked: Promise<void> | null = null;

/**
 * Compare the backend's published vocabulary with this build's mirror.
 *
 * Runs once per page load. The local mirror stays authoritative for rendering
 * (a spec must draw synchronously), so a mismatch is reported as a console
 * warning rather than blocking anything — the backend test suite is the real
 * gate, and this makes the same drift visible in the browser console.
 */
export function useChartVocabularyDrift(): void {
  if (driftChecked) return;
  driftChecked = loadChartVocabulary().then((vocabulary) => {
    if (!vocabulary) return;
    const unrenderable = missingFromLocalVocabulary(vocabulary);
    const unbacked = missingFromBackend(vocabulary);
    if (unrenderable.length > 0 || unbacked.length > 0) {
      console.warn(
        "[chart vocabulary drift] backend and UI disagree.",
        { backendOnly: unrenderable, uiOnly: unbacked },
      );
    }
  });
}
