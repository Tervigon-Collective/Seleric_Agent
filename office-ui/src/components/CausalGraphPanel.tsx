import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactECharts from "echarts-for-react";
import { traceApi } from "../api/trace";
import { isTraceableMissionId, useOffice } from "../store";
import { ExpandIcon, ImageIcon, ShrinkIcon } from "./icons";
import {
  getChartThemeColors,
  type CausalGraphLink,
  type CausalGraphNode,
  type ChartSpec,
  type ChartThemeColors,
} from "./charts/ChartRenderer";

const CLASS_COLOR: Record<string, string> = {
  supported_cause: "#30D158",
  likely_contributor: "#FF9F0A",
  correlation: "#BF5AF2",
  insufficient_evidence: "#8E8E93",
};

/** Python weekday() Mon=0 … Sun=6; DoWhy omits Mon as the reference level. */
const DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

function friendlyNodeName(id: string, name?: string): string {
  const raw = (name || id || "").trim();
  const match = /^(?:dow[_\s-]?)(\d)$/i.exec(id.trim())
    || /^(?:dow[_\s-]?)(\d)$/i.exec(raw)
    || /^([A-Za-z]{3})\s*\(day of week\)$/i.exec(raw);
  if (match && match[1] && /^\d$/.test(match[1])) {
    const k = Number(match[1]);
    if (k >= 0 && k <= 6) return `${DOW_NAMES[k]} (day of week)`;
  }
  if (match && match[1] && !/^\d$/.test(match[1])) return raw; // already labelled
  if (raw === "trend" || id === "trend") return "trend";
  return raw || id;
}

function nodeColor(node: CausalGraphNode, theme: ChartThemeColors): string {
  if (node.role === "outcome") return theme.accent;
  if (node.status === "ruled_out") return theme.faint;
  const cls = node.classification || "";
  return CLASS_COLOR[cls] ?? (node.role === "driver" ? "#FF9F0A" : theme.dim);
}

type GraphLayoutOpts = { expanded?: boolean };

/** ECharts force-directed DAG option for a diagnosis causal graph. */
export function causalGraphOption(
  spec: ChartSpec,
  theme: ChartThemeColors,
  layout: GraphLayoutOpts = {},
) {
  const expanded = !!layout.expanded;
  const nodes = (spec.nodes ?? []).map((node) => {
    const label = friendlyNodeName(node.id, node.name);
    const share = typeof node.share_of_change === "number"
      ? ` · ${Math.round(Math.abs(node.share_of_change) * 100)}%`
      : "";
    const cls = node.classification ? ` (${node.classification.replaceAll("_", " ")})` : "";
    return {
      id: node.id,
      name: label,
      value: node.share_of_change ?? 0,
      category: node.role === "outcome" ? 0 : node.role === "driver" ? 1 : 2,
      symbolSize: node.role === "outcome"
        ? (expanded ? 52 : 42)
        : node.role === "driver"
          ? (expanded ? 40 : 32)
          : (expanded ? 30 : 24),
      itemStyle: { color: nodeColor(node, theme) },
      label: {
        show: true,
        color: theme.text,
        fontSize: expanded ? 13 : 11,
        formatter: `{b}${share}`,
      },
      tooltip: {
        formatter: `${label}${cls}${share}`,
      },
    };
  });
  const links = (spec.links ?? []).map((link: CausalGraphLink) => ({
    source: link.source,
    target: link.target,
    lineStyle: {
      color: link.kind === "treatment" ? theme.accent : theme.lineStrong,
      width: link.kind === "treatment" ? 2.4 : 1.2,
      curveness: 0.12,
      type: link.kind === "treatment" ? "solid" : "dashed",
    },
    symbol: ["none", "arrow"],
    symbolSize: link.kind === "treatment" ? 10 : 7,
  }));
  return {
    backgroundColor: "transparent",
    tooltip: { show: true },
    legend: {
      data: ["Outcome", "Driver", "Confounder"],
      textStyle: { color: theme.dim, fontSize: expanded ? 12 : 11 },
      bottom: 0,
    },
    series: [{
      type: "graph",
      layout: "force",
      roam: true,
      draggable: true,
      categories: [
        { name: "Outcome" },
        { name: "Driver" },
        { name: "Confounder" },
      ],
      data: nodes,
      links,
      edgeSymbol: ["none", "arrow"],
      edgeSymbolSize: 8,
      force: {
        repulsion: expanded ? 520 : 280,
        edgeLength: expanded ? [90, 200] : [60, 140],
        gravity: expanded ? 0.05 : 0.08,
      },
      lineStyle: { opacity: 0.85 },
      emphasis: { focus: "adjacency", lineStyle: { width: 3 } },
    }],
  };
}

function traceUsedDiagnosis(trace: Record<string, unknown> | null | undefined): boolean {
  const steps = Array.isArray(trace?.steps) ? trace.steps : [];
  return steps.some((step) => {
    if (!step || typeof step !== "object") return false;
    const row = step as Record<string, unknown>;
    return row.kind === "tool_call" && row.tool === "diagnose_metric_change";
  });
}

function CausalGraphModal({
  title,
  onClose,
  children,
  actions,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  actions?: ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);
  return (
    <div className="chart-modal-backdrop" onClick={onClose} role="presentation">
      <div
        className="chart-modal causal-graph-modal"
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
              aria-label="Close expanded causal graph"
              title="Close (Esc)"
            >
              <ShrinkIcon size={14} />
              Close
            </button>
          </div>
        </div>
        <div className="chart-modal-body">{children}</div>
        <p className="chart-modal-hint">
          Drag nodes to rearrange · scroll to zoom · drag background to pan.
          Grey nodes labelled “day of week” are calendar dummies (Mon is the reference level).
        </p>
      </div>
    </div>
  );
}

function GraphCard({
  spec,
  option,
  theme,
}: {
  spec: ChartSpec;
  option: Record<string, unknown>;
  theme: ChartThemeColors;
}) {
  const [expanded, setExpanded] = useState(false);
  const modalChartRef = useRef<{ getDataURL?: (opts?: Record<string, unknown>) => string } | null>(null);
  const title = spec.title || "Causal graph";
  const expandedOption = useMemo(
    () => causalGraphOption(spec, theme, { expanded: true }),
    [spec, theme],
  );

  const downloadPng = () => {
    try {
      const url = modalChartRef.current?.getDataURL?.({
        type: "png",
        pixelRatio: 2,
        backgroundColor: theme.panel,
      });
      if (!url) return;
      const link = document.createElement("a");
      link.href = url;
      link.download = `${title.toLowerCase().replace(/[^a-z0-9]+/g, "_")}.png`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch {
      /* best-effort */
    }
  };

  return (
    <div className="causal-graph-card">
      <div className="causal-graph-card-head">
        <h3>{title}</h3>
        <button
          type="button"
          className="chart-download"
          onClick={() => setExpanded(true)}
          aria-label="Expand causal graph"
          title="Expand — larger graph, rearrange, export"
        >
          <ExpandIcon size={12} />
          Expand
        </button>
      </div>
      {!!spec.warnings?.length && (
        <p className="empty-note">{spec.warnings[0]}</p>
      )}
      <ReactECharts
        option={option}
        style={{ height: 320, width: "100%" }}
        opts={{ renderer: "canvas" }}
        notMerge
      />
      <ul className="causal-graph-legend">
        <li><span className="swatch outcome" /> Outcome</li>
        <li><span className="swatch driver" /> Driver (solid arrow = estimated effect)</li>
        <li><span className="swatch confounder" /> Confounder / adjustment (dashed)</li>
      </ul>
      <p className="causal-graph-note">
        Day-of-week nodes (Tue–Sun) adjust for weekly seasonality; Monday is the omitted reference level.
      </p>
      <details>
        <summary>Nodes</summary>
        <ul className="causal-graph-nodes">
          {(spec.nodes ?? []).map((node: CausalGraphNode) => (
            <li key={node.id}>
              <strong>{friendlyNodeName(node.id, node.name)}</strong>
              <small>
                {[node.role, node.classification, node.status]
                  .filter(Boolean)
                  .map(String)
                  .join(" · ")
                  .replaceAll("_", " ")}
              </small>
            </li>
          ))}
        </ul>
      </details>
      {expanded && (
        <CausalGraphModal
          title={title}
          onClose={() => setExpanded(false)}
          actions={
            <button
              type="button"
              className="chart-icon-btn"
              onClick={downloadPng}
              aria-label="Download causal graph as PNG"
              title="Download PNG"
            >
              <ImageIcon size={14} />
              PNG
            </button>
          }
        >
          <div style={{ width: "100%", height: "68vh", minHeight: 420 }}>
            <ReactECharts
              option={expandedOption}
              style={{ height: "100%", width: "100%" }}
              opts={{ renderer: "canvas" }}
              notMerge
              onChartReady={(chart) => {
                modalChartRef.current = chart as unknown as {
                  getDataURL?: (opts?: Record<string, unknown>) => string;
                };
              }}
            />
          </div>
        </CausalGraphModal>
      )}
    </div>
  );
}

export function CausalGraphPanel({
  missionId,
  graphs: graphsProp,
  live = false,
}: {
  missionId: string | null;
  graphs?: ChartSpec[] | null;
  live?: boolean;
}) {
  const [fetched, setFetched] = useState<ChartSpec[]>([]);
  const [diagnosed, setDiagnosed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const theme = getChartThemeColors();

  const setCausalGraphs = useOffice((s) => s.setCausalGraphs);

  useEffect(() => {
    if (!isTraceableMissionId(missionId)) {
      setFetched([]);
      setDiagnosed(false);
      return;
    }
    if (live && !(graphsProp && graphsProp.length)) return;
    let alive = true;
    void traceApi.get(missionId)
      .then((response) => {
        if (!alive) return;
        const rows = Array.isArray(response.causal_graphs) ? response.causal_graphs : [];
        const charts = rows
          .filter((row): row is Record<string, unknown> => !!row && typeof row === "object")
          .map((row) => row as unknown as ChartSpec);
        setFetched(charts);
        setDiagnosed(traceUsedDiagnosis(response.trace as Record<string, unknown> | undefined));
        if (charts.length) setCausalGraphs(charts as unknown as Array<Record<string, unknown>>);
      })
      .catch((reason: unknown) => {
        if (alive) setError(reason instanceof Error ? reason.message : "Unable to load causal graph");
      });
    return () => { alive = false; };
  }, [missionId, live, graphsProp, setCausalGraphs]);

  const graphs = (graphsProp && graphsProp.length ? graphsProp : fetched)
    .filter((g) => (g.nodes?.length || 0) > 0 || (g.links?.length || 0) > 0);

  const options = useMemo(
    () => graphs.map((spec) => causalGraphOption(spec, theme)),
    [graphs, theme],
  );

  if (!missionId) {
    return <p className="empty-note">Run a diagnosis question to see the causal graph.</p>;
  }
  if (!graphs.length) {
    let empty = "No causal graph for this run.";
    if (live) {
      empty = "Causal graph appears here when diagnose_metric_change finishes.";
    } else if (error) {
      empty = error;
    } else if (diagnosed) {
      empty = "Diagnosis ran, but no drivers were estimated with a DoWhy DAG (nothing to draw).";
    } else {
      empty = "This run did not call diagnose_metric_change, so there is no causal DAG. Ask a why-question and let the agent escalate → diagnose.";
    }
    return <p className="empty-note">{empty}</p>;
  }

  return (
    <div className="causal-graph-panel">
      {graphs.map((spec, index) => (
        <GraphCard
          key={spec.title || index}
          spec={spec}
          option={options[index] as Record<string, unknown>}
          theme={theme}
        />
      ))}
    </div>
  );
}
