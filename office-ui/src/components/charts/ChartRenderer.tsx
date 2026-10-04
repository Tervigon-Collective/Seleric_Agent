import React, { useMemo, useCallback } from 'react';
import ReactECharts from 'echarts-for-react';

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

export const ChartRenderer: React.FC<ChartRendererProps> = ({ spec }) => {
  const handleDownloadCsv = useCallback(() => {
    if (spec.downloadable_csv_url) {
      window.open(spec.downloadable_csv_url, '_blank');
      return;
    }
    exportToCsv(spec.data || [], spec.title || 'chart_data');
  }, [spec]);

  const options = useMemo(() => {
    if (!spec || !spec.chart_type) return {};

    const { title, chart_type, series, xAxis, yAxis, data } = spec;

    const baseOptions: any = {
      color: [
        '#6366f1', '#3b82f6', '#0ea5e9', '#14b8a6', '#10b981',
        '#84cc16', '#f59e0b', '#f97316', '#ef4444', '#ec4899', '#8b5cf6'
      ],
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
      backgroundColor: 'transparent'
    };

    if (chart_type !== 'pie' && chart_type !== 'funnel') {
      baseOptions.xAxis = xAxis || { type: 'category' };
      baseOptions.yAxis = yAxis && yAxis.length > 0 ? yAxis : [{ type: 'value' }];

      // Styling for axes
      if (baseOptions.xAxis.type !== 'value') {
        baseOptions.xAxis.axisLine = { lineStyle: { color: 'var(--line)' } };
        baseOptions.xAxis.axisLabel = { color: 'var(--text-dim)', fontSize: 11 };
      }

      baseOptions.yAxis.forEach((y: any) => {
        y.splitLine = { lineStyle: { color: 'var(--line)', type: 'dashed' } };
        y.axisLabel = { color: 'var(--text-dim)', fontSize: 11 };
        if (y.name) {
          y.nameTextStyle = { color: 'var(--text-dim)', fontSize: 10 };
        }
      });

      baseOptions.grid = { left: '3%', right: '4%', top: '15%', bottom: '12%', containLabel: true };
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
          borderRadius: chart_type === 'bar' ? [4, 4, 0, 0] : 0
        },
        smooth: chart_type === 'line' || chart_type === 'area',
        connectNulls: chart_type === 'line' || chart_type === 'area'
      };
      if (s.areaStyle) {
        item.areaStyle = s.areaStyle;
      }
      return item;
    });

    if (chart_type === 'pie') {
      delete baseOptions.dataset;
      if (baseOptions.series.length > 0) {
        baseOptions.series[0].data = data;
        baseOptions.series[0].radius = ['42%', '72%'];
        baseOptions.series[0].center = ['50%', '50%'];
        baseOptions.series[0].label = { show: false };
        baseOptions.series[0].itemStyle = {
          borderRadius: 4,
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
    return <div className="text-red-500 text-xs">Invalid chart specification</div>;
  }

  const chartLabel = spec.chart_type.toUpperCase();

  return (
    <div
      className="chart-card-container"
      role="region"
      aria-label={spec.title || 'Data visualization'}
      style={{
        width: '100%',
        margin: '0.75rem 0',
        padding: '0.75rem 1rem',
        background: 'var(--panel-2)',
        border: '1px solid var(--line)',
        borderRadius: '8px',
        boxShadow: 'var(--shadow)'
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '0.5rem',
          paddingBottom: '0.4rem',
          borderBottom: '1px solid var(--line)'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span
            style={{
              fontSize: '10px',
              fontWeight: 600,
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
              padding: '2px 6px',
              borderRadius: '4px',
              background: 'var(--st-working)',
              color: 'var(--panel)',
              border: '1px solid var(--st-working)'
            }}
          >
            {chartLabel}
          </span>
          <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text)' }}>
            {spec.title || 'Visualization'}
          </span>
        </div>
        <button
          onClick={handleDownloadCsv}
          type="button"
          aria-label="Download CSV"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            fontSize: '11px',
            color: 'var(--text-dim)',
            background: 'var(--panel)',
            border: '1px solid var(--line)',
            borderRadius: '4px',
            padding: '3px 8px',
            cursor: 'pointer',
            transition: 'color 0.15s, border-color 0.15s'
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--text)';
            e.currentTarget.style.borderColor = 'var(--text-dim)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-dim)';
            e.currentTarget.style.borderColor = 'var(--line)';
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
          CSV
        </button>
      </div>
      <div style={{ width: '100%', height: '320px' }}>
        <ReactECharts
          option={options}
          style={{ height: '100%', width: '100%' }}
          opts={{ renderer: 'canvas' }}
        />
      </div>
    </div>
  );
};

