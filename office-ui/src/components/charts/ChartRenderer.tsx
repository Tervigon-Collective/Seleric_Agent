import React, { useMemo, useCallback, useState, useRef, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import { useShellStore } from '../../stores/shell';
import { CheckIcon, CopyIcon, DownloadIcon, ExpandIcon, ShrinkIcon, ResetZoomIcon, ImageIcon } from '../icons';
import {
  normalizeChartType,
  type ChartType,
} from '../../api/chartContract';

export interface ChartSeries {
  key: string;
  name?: string;
  type?: string;
  stack?: string;
  yAxisIndex?: number;
  areaStyle?: Record<string, unknown>;
  radius?: unknown[];
  values_key?: string;
}

export interface ChartSpec {
  chart_type: string;
  title?: string;
  metrics: string[];
  dimensions: string[];
  series: ChartSeries[];
  xAxis?: Record<string, unknown> & { key?: string; type?: string; data?: string[] };
  yAxis?: Array<Record<string, unknown>>;
  radar?: { indicator: Array<{ name: string; max: number }> };
  data: Record<string, any>[];
  warnings?: string[];
  downloadable_csv_url?: string;
}

interface ChartRendererProps {
  spec: ChartSpec;
}

/** Restrained analytical palette led by the product chart accent. */
const PALETTE = [
  '#737CE7', '#2A9D8F', '#7FB069', '#E0A458',
  '#9B8AFB', '#94A3B8', '#C0C9D6',
];

/** Forms drawn as flat bars rather than as a continuous line. */
const FLAT_MARK_FORMS = new Set(['bar', 'stacked_bar', 'grouped_bar']);

export interface ChartThemeColors {
  text: string;
  dim: string;
  faint: string;
  line: string;
  lineStrong: string;
  panel: string;
  sidebar: string;
  accent: string;
}

const LIGHT_CHART_THEME: ChartThemeColors = {
  text: '#202125',
  dim: '#656971',
  faint: '#898d96',
  line: '#e9eaec',
  lineStrong: '#dcdde1',
  panel: '#ffffff',
  sidebar: '#f8f8f7',
  accent: '#5865d9',
};

const DARK_CHART_THEME: ChartThemeColors = {
  text: '#e8ebf0',
  dim: '#9aa4b2',
  faint: '#6b7686',
  line: '#232b36',
  lineStrong: '#303a47',
  panel: '#111418',
  sidebar: '#141920',
  accent: '#8e97f2',
};

function readCssVar(name: string, fallback: string): string {
  try {
    if (typeof document === 'undefined') return fallback;
    const value = getComputedStyle(document.documentElement).getPropertyValue(name)?.trim();
    return value || fallback;
  } catch {
    return fallback;
  }
}

/**
 * Resolve the CSS design tokens to concrete colors.
 *
 * ECharts renders on canvas and cannot resolve `var(--...)` references, so
 * passing them through leaves axis labels, grid lines and tooltips near-black
 * in the dark theme. Always resolve to a real color here.
 */
export function getChartThemeColors(): ChartThemeColors {
  const isDark =
    typeof document !== 'undefined' && document.documentElement?.dataset?.theme === 'dark';
  const fb = isDark ? DARK_CHART_THEME : LIGHT_CHART_THEME;
  if (typeof document === 'undefined') return fb;
  return {
    text: readCssVar('--text', fb.text),
    dim: readCssVar('--text-dim', fb.dim),
    faint: readCssVar('--text-faint', fb.faint),
    line: readCssVar('--line', fb.line),
    lineStrong: readCssVar('--line-strong', fb.lineStrong),
    panel: readCssVar('--panel', fb.panel),
    sidebar: readCssVar('--sidebar', fb.sidebar),
    accent: readCssVar('--accent', fb.accent),
  };
}

/** Live theme colors; re-resolves when `[data-theme]` flips. */
export function useChartThemeColors(): ChartThemeColors {
  const [colors, setColors] = useState<ChartThemeColors>(() => getChartThemeColors());
  useEffect(() => {
    const refresh = () => setColors(getChartThemeColors());
    refresh();
    const observer = new MutationObserver(refresh);
    try {
      observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['data-theme', 'class', 'style'],
      });
    } catch {
      /* non-DOM test env */
    }
    window.addEventListener('storage', refresh);
    return () => {
      observer.disconnect();
      window.removeEventListener('storage', refresh);
    };
  }, []);
  return colors;
}

/** Compact, human-readable numbers: 2,539.83 instead of 2539.831111111111. */
export function formatChartValue(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value ?? '');
  const abs = Math.abs(value);
  const fractionDigits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  // Trim trailing zeros (389.50 -> 389.5) without ever showing float noise.
  const text = value.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: fractionDigits,
  });
  return text;
}

function formatAxisTick(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return String(value ?? '');
  const abs = Math.abs(value);
  if (abs >= 1_000_000) {
    const trimmed = (value / 1_000_000).toLocaleString('en-US', { maximumFractionDigits: 1 });
    return `${trimmed}M`;
  }
  if (abs >= 10_000) {
    const trimmed = (value / 1_000).toLocaleString('en-US', { maximumFractionDigits: 0 });
    return `${trimmed}k`;
  }
  if (abs >= 1_000) {
    const trimmed = (value / 1_000).toLocaleString('en-US', { maximumFractionDigits: 1 });
    return `${trimmed}k`;
  }
  return formatChartValue(value);
}

function exportToCsv(data: any[], filename: string) {
  if (!data || data.length === 0) return;
  const headers = Array.from(new Set(data.flatMap(Object.keys)));
  const rows = data.map(row =>
    headers.map(header => {
      const val = row[header];
      if (val === null || val === undefined) return '""';
      const escaped = String(val).replace(/"/g, '""');
      return `"${escaped}"`;
    }).join(',')
  );
  const csvContent = [headers.join(','), ...rows].join('\n');
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.setAttribute('href', url);
  link.setAttribute('download', `${filename.toLowerCase().replace(/[^a-z0-9]+/g, '_')}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export function SelectionInsight({ columns, row, onClose }: {
  columns: string[]; row: Record<string, unknown>; onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const openInspector = useShellStore((s) => s.openInspector);
  const entries = columns.map((col) => [col, row?.[col]] as const);
  const copy = () => {
    const text = entries.map(([col, val]) => `${col}: ${String(val ?? "")}`).join("\n");
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    }).catch(() => undefined);
  };
  return (
    <div className="insight-panel" role="status" aria-label="Selected data point">
      <dl>
        {entries.map(([col, val]) => (
          <React.Fragment key={col}>
            <dt>{col}</dt>
            <dd>{String(val ?? "—")}</dd>
          </React.Fragment>
        ))}
      </dl>
      <div className="insight-actions">
        <button type="button" onClick={copy} aria-label="Copy selected values">
          {copied ? <CheckIcon size={12} /> : <CopyIcon size={12} />}
          {copied ? "Copied" : "Copy"}
        </button>
        <button type="button" onClick={() => openInspector("Evidence")}>Evidence</button>
        <button type="button" onClick={onClose} aria-label="Clear selection">Clear</button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Renderers. One per chart form, all driven by the generic spec the backend
// emits. An unknown form returns null so the caller fails loudly.
// --------------------------------------------------------------------------

type ChartOption = Record<string, unknown>;

interface RenderOpts {
  expanded?: boolean;
}

function baseOption(
  spec: ChartSpec,
  theme: ChartThemeColors,
  opts: RenderOpts = {},
): ChartOption {
  const expanded = opts.expanded === true;
  return {
    color: PALETTE,
    textStyle: { fontFamily: 'inherit', color: theme.text },
    title: {
      text: spec.title || 'Chart',
      left: 'left',
      textStyle: { color: theme.text, fontSize: expanded ? 14 : 13, fontWeight: 600, fontFamily: 'inherit' },
    },
    tooltip: {
      trigger: 'axis',
      confine: true,
      backgroundColor: theme.panel,
      borderColor: theme.lineStrong,
      borderWidth: 1,
      padding: [8, 12],
      textStyle: { color: theme.text, fontSize: 12, fontFamily: 'inherit' },
      axisPointer: { type: 'shadow', shadowStyle: { color: theme.line, opacity: 0.35 } },
      valueFormatter: (value: unknown) => formatChartValue(
        Array.isArray(value) ? value[value.length - 1] : value,
      ),
    },
    legend: {
      bottom: expanded ? 34 : 0,
      type: 'scroll',
      pageIconColor: theme.text,
      pageIconInactiveColor: theme.faint,
      pageTextStyle: { color: theme.dim },
      pageButtonItemGap: 6,
      textStyle: { color: theme.dim, fontSize: 11, overflow: 'truncate', width: 140, fontFamily: 'inherit' },
      icon: 'circle',
    },
    toolbox: expanded
      ? {
          show: true,
          top: 0,
          right: 0,
          itemSize: 15,
          itemGap: 10,
          iconStyle: { borderColor: theme.dim },
          emphasis: { iconStyle: { borderColor: theme.text } },
          feature: {
            saveAsImage: { show: true, title: 'Save as PNG', backgroundColor: theme.panel, name: (spec.title || 'chart').toLowerCase().replace(/[^a-z0-9]+/g, '_') },
            dataView: { show: true, title: 'Data view', readOnly: true, lang: [(spec.title || 'Data'), 'Close', 'Refresh'] },
            dataZoom: { show: true, title: { zoom: 'Zoom', back: 'Reset zoom' }, iconStyle: { borderColor: theme.dim } },
            restore: { show: true, title: 'Restore' },
          },
        }
      : undefined,
    backgroundColor: 'transparent',
    animationDuration: 300,
    animationEasing: 'cubicOut',
  };
}

/** Shared axes for every cartesian form. */
function withCartesianAxes(
  option: ChartOption,
  spec: ChartSpec,
  theme: ChartThemeColors,
  opts: RenderOpts = {},
): ChartOption {
  const expanded = opts.expanded === true;
  const xAxis = spec.xAxis ?? { type: 'category' };
  const yAxis = spec.yAxis && spec.yAxis.length > 0 ? spec.yAxis : [{ type: 'value' }];
  const rowCount = Array.isArray(spec.data) ? spec.data.length : 0;
  const crowded = rowCount > 12;
  option.xAxis = xAxis;
  option.yAxis = yAxis;
  if ((xAxis as Record<string, unknown>).type !== 'value') {
    option.xAxis = {
      ...xAxis,
      axisLine: { lineStyle: { color: theme.lineStrong } },
      axisTick: { show: false },
      axisLabel: {
        color: theme.dim,
        fontSize: 11,
        fontFamily: 'inherit',
        hideOverlap: true,
        rotate: expanded ? 0 : crowded ? 32 : 0,
        interval: 'auto',
      },
      axisPointer: { label: { backgroundColor: theme.sidebar, color: theme.text } },
    };
  } else {
    option.xAxis = {
      ...xAxis,
      axisLabel: {
        color: theme.dim,
        fontSize: 11,
        fontFamily: 'inherit',
        formatter: (v: unknown) => formatAxisTick(v),
      },
      splitLine: { lineStyle: { color: theme.line, type: 'dashed' } },
    };
  }
  (yAxis as Array<Record<string, unknown>>).forEach((axis) => {
    axis.splitLine = { lineStyle: { color: theme.line, type: 'dashed' } };
    axis.axisLabel = {
      ...(typeof axis.axisLabel === 'object' && axis.axisLabel !== null ? (axis.axisLabel as Record<string, unknown>) : {}),
      color: theme.dim,
      fontSize: 11,
      fontFamily: 'inherit',
      formatter: (v: unknown) => formatAxisTick(v),
    };
    if (axis.name && !axis.nameTextStyle) {
      axis.nameTextStyle = { color: theme.faint, fontSize: 11, fontFamily: 'inherit' };
    }
  });
  option.grid = {
    left: '3%',
    right: '4%',
    top: expanded ? '12%' : '15%',
    bottom: expanded ? '24%' : crowded ? '20%' : '14%',
    containLabel: true,
  };
  // Wheel / pinch zoom inline; slider + inside controls in the expanded modal.
  if (rowCount > 8) {
    option.dataZoom = expanded
      ? [
          {
            type: 'slider',
            show: true,
            bottom: 2,
            height: 26,
            borderColor: theme.line,
            backgroundColor: theme.sidebar,
            fillerColor: theme.accent,
            handleStyle: { color: theme.panel, borderColor: theme.lineStrong },
            moveHandleStyle: { color: theme.lineStrong },
            selectedDataBackground: { lineStyle: { color: theme.accent }, areaStyle: { color: theme.accent, opacity: 0.12 } },
            textStyle: { color: theme.faint, fontSize: 10, fontFamily: 'inherit' },
          },
          { type: 'inside', filterMode: 'filter' },
        ]
      : [{ type: 'inside', filterMode: 'filter' }];
  }
  return option;
}

/** Series drawn from a dataset: bar, line/area, stacked and grouped all land here. */
function cartesianSeries(spec: ChartSpec): Array<Record<string, unknown>> {
  const flatMarks = FLAT_MARK_FORMS.has(spec.chart_type);
  return (spec.series ?? []).map((s) => ({
    ...s,
    type: s.type ?? 'bar',
    name: s.name ?? s.key,
    encode: { x: spec.xAxis?.key, y: s.key },
    itemStyle: {
      borderRadius: flatMarks ? [3, 3, 0, 0] : 0,
      borderColor: flatMarks ? 'transparent' : undefined,
    },
    // The backend already emits `stack: "total"` for stacked forms; never restack.
    stack: s.stack,
    areaStyle: spec.chart_type === 'area' ? { opacity: 0.12 } : s.areaStyle,
    lineStyle: flatMarks ? undefined : { width: 2 },
    symbolSize: 5,
    smooth: !flatMarks,
    connectNulls: !flatMarks,
  }));
}

function renderCartesian(spec: ChartSpec, theme: ChartThemeColors, opts: RenderOpts = {}): ChartOption {
  const option = baseOption(spec, theme, opts);
  withCartesianAxes(option, spec, theme, opts);
  option.dataset = {
    dimensions: spec.xAxis?.key ? [spec.xAxis.key, ...(spec.series ?? []).map((s) => s.key)] : undefined,
    source: spec.data,
  };
  option.series = cartesianSeries(spec);
  return option;
}

function renderScatter(spec: ChartSpec, theme: ChartThemeColors, opts: RenderOpts = {}): ChartOption {
  const option = baseOption(spec, theme, opts);
  withCartesianAxes(option, spec, theme, opts);
  // Scatter carries explicit [x, y] pairs, so a dataset is not used.
  option.series = (spec.series ?? []).map((s) => ({
    ...s,
    type: 'scatter',
    name: s.name ?? s.key,
    data: (spec.data ?? []).map((row) => [row.x, row.y]),
    symbolSize: 8,
    itemStyle: { opacity: 0.8 },
  }));
  return option;
}

function renderSlices(spec: ChartSpec, chartType: ChartType, theme: ChartThemeColors, opts: RenderOpts = {}): ChartOption {
  const option = baseOption(spec, theme, opts);
  (option.tooltip as Record<string, unknown>).trigger = 'item';
  (option.tooltip as Record<string, unknown>).valueFormatter = (value: unknown) => formatChartValue(value);
  option.series = (spec.series ?? []).map((s) => ({
    ...s,
    type: chartType === 'donut' ? 'pie' : chartType,
    name: s.name ?? s.key,
    data: (spec.data ?? []).map((row) => ({ name: row.name, value: row.value })),
    label: { color: theme.dim, fontSize: 11, fontFamily: 'inherit', formatter: '{b}: {d}%' },
    labelLine: { lineStyle: { color: theme.lineStrong } },
    itemStyle: { borderRadius: 3, borderColor: theme.panel, borderWidth: 2 },
  }));
  const series = option.series as Array<Record<string, unknown>>;
  const main = series[0];
  if (main) {
    if (chartType === 'pie') {
      main.radius = ['0%', '70%'];
      main.center = ['50%', '48%'];
      main.label = { show: false };
      main.itemStyle = { borderRadius: 3, borderColor: theme.panel, borderWidth: 2 };
    }
    if (chartType === 'donut') {
      main.radius = seriesRadius(spec);
      main.center = ['50%', '48%'];
      main.label = { show: false };
      main.itemStyle = { borderRadius: 3, borderColor: theme.panel, borderWidth: 2 };
    }
    if (chartType === 'funnel') {
      main.left = '10%';
      main.width = '80%';
      main.label = { show: true, color: theme.dim, fontSize: 11, fontFamily: 'inherit' };
      main.gap = 4;
    }
  }
  return option;
}

/** The backend may publish a radius on the series for a hollow form. */
function seriesRadius(spec: ChartSpec): [string, string] {
  const radius = spec.series?.[0]?.radius;
  if (Array.isArray(radius) && radius.length === 2) return radius as [string, string];
  return ['40%', '70%'];
}

function renderRadar(spec: ChartSpec, theme: ChartThemeColors, opts: RenderOpts = {}): ChartOption {
  const option = baseOption(spec, theme, opts);
  const indicators = spec.radar?.indicator ?? [];
  option.radar = {
    indicator: indicators,
    axisName: { color: theme.dim, fontSize: 11, fontFamily: 'inherit' },
    axisLine: { lineStyle: { color: theme.lineStrong } },
    splitLine: { lineStyle: { color: theme.line } },
    splitArea: { show: false },
    radius: '68%',
    center: ['50%', '52%'],
  };
  option.series = (spec.series ?? []).map((s) => ({
    ...s,
    type: 'radar',
    name: s.name ?? s.key,
    data: [{ name: s.name ?? s.key, value: indicators.map((ind) => valueFor(spec, ind.name, s.key)) }],
    symbolSize: 4,
    lineStyle: { width: 2 },
  }));
  option.tooltip = { ...(option.tooltip as Record<string, unknown>), trigger: 'item', valueFormatter: (v: unknown) => formatChartValue(v) };
  return option;
}

function valueFor(spec: ChartSpec, indicator: string, key: string): number {
  const row = (spec.data ?? []).find((r) => r.indicator === indicator);
  const value = row?.[key];
  return typeof value === 'number' ? value : 0;
}

function renderHeatmap(spec: ChartSpec, theme: ChartThemeColors, opts: RenderOpts = {}): ChartOption {
  const expanded = opts.expanded === true;
  const option = baseOption(spec, theme, opts);
  const xAxis = { ...(spec.xAxis ?? { type: 'category', data: [] }) };
  const yAxis = { ...(spec.yAxis?.[0] ?? { type: 'category', data: [] }) };
  option.xAxis = {
    ...xAxis,
    axisLabel: { color: theme.dim, fontSize: 11, fontFamily: 'inherit', hideOverlap: true },
    axisLine: { lineStyle: { color: theme.lineStrong } },
  };
  option.yAxis = {
    ...yAxis,
    axisLabel: { color: theme.dim, fontSize: 11, fontFamily: 'inherit' },
    axisLine: { lineStyle: { color: theme.lineStrong } },
  };
  option.tooltip = {
    ...(option.tooltip as Record<string, unknown>),
    position: 'top',
    valueFormatter: (v: unknown) => formatChartValue(v),
  };
  option.visualMap = {
    min: Math.min(...(spec.data ?? []).map((r) => Number(r.value) || 0), 0),
    max: Math.max(...(spec.data ?? []).map((r) => Number(r.value) || 0), 1),
    calculable: true,
    orient: 'horizontal',
    left: 'center',
    bottom: expanded ? 34 : 0,
    inRange: { color: ['#f2f3fa', '#737CE7', '#3b3fb0'] },
    textStyle: { color: theme.dim, fontSize: 10, fontFamily: 'inherit' },
    handleStyle: { borderColor: theme.lineStrong, color: theme.panel },
  };
  option.grid = { left: '3%', right: '4%', top: '8%', bottom: expanded ? '28%' : '20%', containLabel: true };
  // Heatmap data is [xIndex, yIndex, value] triples against the axis categories.
  const xData = (xAxis.data as string[]) ?? [];
  const yData = (yAxis.data as string[]) ?? [];
  const xIndex = new Map(xData.map((d, i) => [d, i]));
  const yIndex = new Map(yData.map((d, i) => [d, i]));
  option.series = [{
    type: 'heatmap',
    name: spec.series?.[0]?.name ?? 'Value',
    data: (spec.data ?? []).map((row) => [
      xIndex.get(row.x) ?? 0,
      yIndex.get(row.y) ?? 0,
      Number(row.value) || 0,
    ]),
    label: { show: true, fontSize: 10, color: theme.text, fontFamily: 'inherit', formatter: (p: { value?: unknown[] }) => formatChartValue(Array.isArray(p.value) ? p.value[2] : p.value) },
    itemStyle: { borderColor: theme.panel, borderWidth: 1 },
    emphasis: { itemStyle: { borderColor: theme.lineStrong, borderWidth: 2 } },
  }];
  return option;
}

type Renderer = (spec: ChartSpec, theme: ChartThemeColors, opts: RenderOpts) => ChartOption | null;

/** Explicit form -> renderer table. Adding a form means adding it here. */
const RENDERERS: Record<ChartType, Renderer> = {
  line: renderCartesian,
  area: renderCartesian,
  bar: renderCartesian,
  stacked_bar: renderCartesian,
  grouped_bar: renderCartesian,
  scatter: renderScatter,
  pie: (spec, theme, opts) => renderSlices(spec, 'pie', theme, opts),
  donut: (spec, theme, opts) => renderSlices(spec, 'donut', theme, opts),
  funnel: (spec, theme, opts) => renderSlices(spec, 'funnel', theme, opts),
  radar: renderRadar,
  heatmap: renderHeatmap,
};

export function renderChartOption(
  spec: ChartSpec,
  theme?: ChartThemeColors,
  opts?: RenderOpts,
): ChartOption | null {
  const chartType = normalizeChartType(spec?.chart_type);
  if (!chartType) return null;
  const resolved = theme ?? getChartThemeColors();
  return RENDERERS[chartType](spec, resolved, opts ?? {});
}

type EChartsInstance = {
  dispatchAction: (a: unknown) => void;
  getDataURL?: (opts?: Record<string, unknown>) => string;
  setOption?: (opt: Record<string, unknown>, notMerge?: boolean) => void;
};

function ChartModal({ title, onClose, children, actions }: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);
  return (
    <div
      className="chart-modal-backdrop"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="chart-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="chart-modal-head">
          <strong className="chart-modal-title">{title}</strong>
          <div className="chart-modal-actions">
            {actions}
            <button
              ref={closeRef}
              type="button"
              className="chart-icon-btn"
              onClick={onClose}
              aria-label="Close expanded chart"
              title="Close (Esc)"
            >
              <ShrinkIcon size={14} />
              Close
            </button>
          </div>
        </div>
        <div className="chart-modal-body">{children}</div>
        <p className="chart-modal-hint">
          Scroll to zoom · drag to pan · use the toolbox (top right) to zoom, view data, or save as PNG.
        </p>
      </div>
    </div>
  );
}

export const ChartRenderer: React.FC<ChartRendererProps> = ({ spec }) => {
  const [view, setView] = useState<'chart' | 'table'>('chart');
  const [selected, setSelected] = useState<number | null>(null);
  const [expanded, setExpanded] = useState(false);
  const chartRef = useRef<EChartsInstance | null>(null);
  const modalChartRef = useRef<EChartsInstance | null>(null);
  const theme = useChartThemeColors();

  const rows = useMemo(
    () => (Array.isArray(spec.data) ? spec.data.filter((r) => r && typeof r === 'object') : []),
    [spec.data],
  );
  const dataColumns = useMemo(() => Array.from(new Set(rows.flatMap((row) => Object.keys(row)))), [rows]);
  const dimensionCol = spec.xAxis?.key && dataColumns.includes(spec.xAxis.key) ? spec.xAxis.key : dataColumns[0];

  const chartType = useMemo(() => normalizeChartType(spec?.chart_type), [spec?.chart_type]);
  const options = useMemo(() => (chartType ? renderChartOption(spec, theme) : null), [spec, chartType, theme]);
  const expandedOptions = useMemo(
    () => (chartType ? renderChartOption(spec, theme, { expanded: true }) : null),
    [spec, chartType, theme],
  );

  // Keep the chart highlight in sync with the shared selection.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    try {
      chart.dispatchAction({ type: 'downplay', seriesIndex: 0 });
      if (selected !== null) {
        chart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: selected });
      }
    } catch { /* highlight is decorative; selection state is authoritative */ }
  }, [selected, view]);

  const handleDownloadCsv = useCallback(() => {
    if (spec.downloadable_csv_url) {
      window.open(spec.downloadable_csv_url, '_blank');
      return;
    }
    exportToCsv(spec.data || [], spec.title || 'chart_data');
  }, [spec]);

  const handleDownloadPng = useCallback((modal: boolean) => {
    const chart = modal ? modalChartRef.current : chartRef.current;
    try {
      const url = chart?.getDataURL?.({ type: 'png', pixelRatio: 2, backgroundColor: theme.panel });
      if (!url) return;
      const link = document.createElement('a');
      link.href = url;
      link.download = `${(spec.title || 'chart').toLowerCase().replace(/[^a-z0-9]+/g, '_')}.png`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch {
      /* export is best-effort */
    }
  }, [spec.title, theme.panel]);

  const handleResetZoom = useCallback((modal: boolean) => {
    const chart = modal ? modalChartRef.current : chartRef.current;
    try {
      chart?.dispatchAction({ type: 'restore' });
      chart?.dispatchAction({ type: 'dataZoom', start: 0, end: 100 });
    } catch {
      /* decorative */
    }
  }, []);

  const onChartClick = useCallback((params: { dataIndex?: number }) => {
    if (typeof params.dataIndex !== 'number') return;
    setSelected((current) => (current === params.dataIndex ? null : (params.dataIndex as number)));
  }, []);

  if (!spec || !spec.chart_type || !chartType) {
    // Fail loudly: a form this UI cannot render must be visible, not blank.
    const unsupported = typeof spec?.chart_type === 'string' ? spec.chart_type : '';
    return (
      <p className="empty-note" role="alert">
        This chart uses an unsupported form{unsupported ? ` (${unsupported})` : ''}.
        Supported forms: line, area, bar, stacked_bar, grouped_bar, pie, donut, funnel, scatter,
        radar, heatmap.
      </p>
    );
  }

  const selectedRow = selected !== null ? (rows[selected] as Record<string, unknown> | undefined) : undefined;

  return (
    <div className="chart-card-container" role="region" aria-label={spec.title || 'Data visualization'}>
      <div className="chart-head">
        <div className="chart-title">
          <div className="segmented" role="tablist" aria-label="Visualization or data view">
            <button
              role="tab" aria-selected={view === 'chart'}
              className={view === 'chart' ? 'active' : ''}
              onClick={() => setView('chart')}
            >Chart</button>
            <button
              role="tab" aria-selected={view === 'table'}
              className={view === 'table' ? 'active' : ''}
              onClick={() => setView('table')}
            >Table</button>
          </div>
          <span className="chart-name">{spec.title || 'Visualization'}</span>
        </div>
        <div className="chart-head-actions">
          <button
            onClick={() => handleResetZoom(false)}
            type="button"
            aria-label="Reset zoom"
            title="Reset zoom"
            className="chart-download"
          >
            <ResetZoomIcon size={12} />
            <span className="chart-btn-label">Reset</span>
          </button>
          <button
            onClick={() => setExpanded(true)}
            type="button"
            aria-label="Expand chart"
            title="Expand — zoom, inspect, export"
            className="chart-download"
          >
            <ExpandIcon size={12} />
            Expand
          </button>
          <button onClick={handleDownloadCsv} type="button" aria-label="Download chart data as CSV" className="chart-download">
            <DownloadIcon size={12} />
            CSV
          </button>
        </div>
      </div>
      {(spec.warnings ?? []).length > 0 && (
        <p className="chart-hint" role="status">{(spec.warnings ?? []).join(' ')}</p>
      )}
      {view === 'chart' ? (
        <div style={{ width: '100%', height: '300px' }}>
          <ReactECharts
            option={options ?? {}}
            style={{ height: '100%', width: '100%' }}
            opts={{ renderer: 'canvas' }}
            onChartReady={(chart) => { chartRef.current = chart as unknown as EChartsInstance; }}
            onEvents={{ click: onChartClick }}
          />
        </div>
      ) : (
        <>
          <div className={rows.length > 20 ? "md-table-wrap tall" : "md-table-wrap"} style={{ marginTop: 4 }}>
            <table aria-label={`${spec.title || 'Chart'} data table`}>
              <thead><tr>{dataColumns.map((col) => <th key={col} scope="col">{col}</th>)}</tr></thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr
                    key={i}
                    className={selected === i ? 'selected-row' : ''}
                    aria-selected={selected === i}
                    tabIndex={0}
                    onClick={() => setSelected((current) => (current === i ? null : i))}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        setSelected((current) => (current === i ? null : i));
                      }
                    }}
                  >
                    {dataColumns.map((col) => <td key={col}>{String(row?.[col] ?? "")}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > 20 && (
            <p className="chart-hint">{rows.length} rows — scroll the table to see all.</p>
          )}
        </>
      )}
      {selectedRow && (
        <SelectionInsight
          columns={dataColumns}
          row={selectedRow}
          onClose={() => setSelected(null)}
        />
      )}
      {!selectedRow && dimensionCol && (
        <p className="chart-hint">
          Select a {chartType === 'pie' || chartType === 'donut' || chartType === 'funnel' ? 'segment' : 'row'} to inspect its exact values.
        </p>
      )}
      {expanded && (
        <ChartModal
          title={spec.title || 'Visualization'}
          onClose={() => setExpanded(false)}
          actions={
            <>
              <button
                type="button"
                className="chart-icon-btn"
                onClick={() => handleResetZoom(true)}
                aria-label="Reset zoom in expanded chart"
                title="Reset zoom"
              >
                <ResetZoomIcon size={14} />
                Reset zoom
              </button>
              <button
                type="button"
                className="chart-icon-btn"
                onClick={() => handleDownloadPng(true)}
                aria-label="Download expanded chart as PNG"
                title="Download PNG"
              >
                <ImageIcon size={14} />
                PNG
              </button>
              <button
                type="button"
                className="chart-icon-btn"
                onClick={handleDownloadCsv}
                aria-label="Download chart data as CSV"
                title="Download CSV"
              >
                <DownloadIcon size={14} />
                CSV
              </button>
            </>
          }
        >
          <div style={{ width: '100%', height: '56vh', minHeight: 320 }}>
            <ReactECharts
              option={expandedOptions ?? {}}
              style={{ height: '100%', width: '100%' }}
              opts={{ renderer: 'canvas' }}
              onChartReady={(chart) => { modalChartRef.current = chart as unknown as EChartsInstance; }}
              onEvents={{ click: onChartClick }}
            />
          </div>
        </ChartModal>
      )}
    </div>
  );
};
