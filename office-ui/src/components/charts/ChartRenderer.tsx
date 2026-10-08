import React, { useMemo, useCallback, useState, useRef, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import { useShellStore } from '../../stores/shell';
import { CheckIcon, CopyIcon, DownloadIcon } from '../icons';

export interface ChartSpec {
  chart_type: 'line' | 'bar' | 'pie' | 'funnel' | 'area';
  title?: string;
  metrics: string[];
  dimensions: string[];
  series: any[];
  xAxis?: any;
  yAxis?: any[];
  data: any[];
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

export const ChartRenderer: React.FC<ChartRendererProps> = ({ spec }) => {
  const [view, setView] = useState<'chart' | 'table'>('chart');
  const [selected, setSelected] = useState<number | null>(null);
  const chartRef = useRef<{ dispatchAction: (a: unknown) => void } | null>(null);

  const rows = useMemo(
    () => (Array.isArray(spec.data) ? spec.data.filter((r) => r && typeof r === 'object') : []),
    [spec.data],
  );
  const dataColumns = useMemo(() => Array.from(new Set(rows.flatMap((row) => Object.keys(row)))), [rows]);
  const dimensionCol = spec.xAxis?.key && dataColumns.includes(spec.xAxis.key) ? spec.xAxis.key : dataColumns[0];

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

  const onChartClick = useCallback((params: { dataIndex?: number }) => {
    if (typeof params.dataIndex !== 'number') return;
    setSelected((current) => (current === params.dataIndex ? null : (params.dataIndex as number)));
  }, []);

  const options = useMemo(() => {
    if (!spec || !spec.chart_type) return {};

    const { title, chart_type, series, xAxis, yAxis, data } = spec;

    const baseOptions: any = {
      color: PALETTE,
      title: {
        text: title || 'Chart',
        left: 'left',
        textStyle: {
          color: 'var(--text)',
          fontSize: 13,
          fontWeight: 600,
          fontFamily: 'inherit'
        }
      },
      tooltip: {
        trigger: chart_type === 'pie' ? 'item' : 'axis',
        backgroundColor: 'var(--panel)',
        borderColor: 'var(--line)',
        borderWidth: 1,
        textStyle: { color: 'var(--text)', fontSize: 12 },
        padding: [8, 12]
      },
      legend: {
        bottom: 0,
        type: 'scroll',
        pageIconColor: 'var(--text)',
        pageTextStyle: { color: 'var(--text-dim)' },
        textStyle: {
          color: 'var(--text-dim)',
          fontSize: 11,
          overflow: 'truncate',
          width: 140
        },
        icon: 'circle'
      },
      dataset: {
        dimensions: xAxis && xAxis.key ? [xAxis.key, ...(series || []).map((s: any) => s.key)] : undefined,
        source: data
      },
      backgroundColor: 'transparent',
      animationDuration: 300,
      animationEasing: 'cubicOut' as const,
    };

    if (chart_type !== 'pie' && chart_type !== 'funnel') {
      baseOptions.xAxis = xAxis || { type: 'category' };
      baseOptions.yAxis = yAxis && yAxis.length > 0 ? yAxis : [{ type: 'value' }];

      if (baseOptions.xAxis.type !== 'value') {
        baseOptions.xAxis.axisLine = { lineStyle: { color: 'var(--line)' } };
        baseOptions.xAxis.axisTick = { show: false };
        baseOptions.xAxis.axisLabel = { color: 'var(--text-dim)', fontSize: 11 };
      }

      baseOptions.yAxis.forEach((y: any) => {
        y.splitLine = { lineStyle: { color: 'var(--line)', type: 'dashed' } };
        y.axisLabel = { color: 'var(--text-dim)', fontSize: 11 };
        if (y.name) {
          y.nameTextStyle = { color: 'var(--text-dim)', fontSize: 10 };
        }
      });

      baseOptions.grid = { left: '3%', right: '4%', top: '15%', bottom: '14%', containLabel: true };
    }

    baseOptions.series = (series || []).map((s: any) => {
      const item: any = {
        ...s,
        encode: {
          x: xAxis ? xAxis.key : undefined,
          y: s.key,
          itemName: chart_type === 'pie' || chart_type === 'funnel' ? 'name' : undefined,
          value: chart_type === 'pie' || chart_type === 'funnel' ? 'value' : undefined
        },
        itemStyle: {
          borderRadius: chart_type === 'bar' ? [3, 3, 0, 0] : 0,
          borderColor: chart_type === 'bar' ? 'transparent' : undefined,
        },
        lineStyle: chart_type === 'line' || chart_type === 'area' ? { width: 2 } : undefined,
        symbolSize: 5,
        smooth: chart_type === 'line' || chart_type === 'area',
        connectNulls: chart_type === 'line' || chart_type === 'area'
      };
      if (chart_type === 'area') {
        item.areaStyle = { opacity: 0.12 };
      } else if (s.areaStyle) {
        item.areaStyle = s.areaStyle;
      }
      return item;
    });

    if (chart_type === 'pie') {
      delete baseOptions.dataset;
      if (baseOptions.series.length > 0) {
        baseOptions.series[0].data = data;
        baseOptions.series[0].radius = ['44%', '70%'];
        baseOptions.series[0].center = ['50%', '48%'];
        baseOptions.series[0].label = { show: false };
        baseOptions.series[0].itemStyle = {
          borderRadius: 3,
          borderColor: 'var(--panel)',
          borderWidth: 2
        };
      }
    } else if (chart_type === 'funnel') {
      delete baseOptions.dataset;
      if (baseOptions.series.length > 0) {
        baseOptions.series[0].data = data;
        baseOptions.series[0].left = '10%';
        baseOptions.series[0].width = '80%';
      }
    }

    return baseOptions;
  }, [spec]);

  if (!spec || !spec.chart_type) {
    return <p className="empty-note" role="status">Chart data is unavailable.</p>;
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
        <button onClick={handleDownloadCsv} type="button" aria-label="Download chart data as CSV" className="chart-download">
          <DownloadIcon size={12} />
          CSV
        </button>
      </div>
      {view === 'chart' ? (
        <div style={{ width: '100%', height: '300px' }}>
          <ReactECharts
            option={options}
            style={{ height: '100%', width: '100%' }}
            opts={{ renderer: 'canvas' }}
            onChartReady={(chart) => { chartRef.current = chart as unknown as { dispatchAction: (a: unknown) => void }; }}
            onEvents={{ click: onChartClick }}
          />
        </div>
      ) : (
        <div className="md-table-wrap" style={{ marginTop: 4 }}>
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
      )}
      {selectedRow && (
        <SelectionInsight
          columns={dataColumns}
          row={selectedRow}
          onClose={() => setSelected(null)}
        />
      )}
      {!selectedRow && dimensionCol && (
        <p className="chart-hint">Select a {spec.chart_type === 'pie' ? 'segment' : 'row'} to inspect its exact values.</p>
      )}
    </div>
  );
};
