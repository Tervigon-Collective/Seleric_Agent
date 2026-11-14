import { Skeleton } from "@/components/ui/skeleton";
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { CheckIcon, CopyIcon, DownloadIcon, MaximizeIcon } from "lucide-react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CHART_TYPES } from "../../api/chartContract";
import { buildFollowUp, buildPointContext } from "../../api/drillContext";
import { useConversationStore } from "../../stores/conversation";
import { useShellStore } from "../../stores/shell";
import { DataTable } from "../DataTable";
import { ShadcnChart } from "./LazyShadcnChart";
import {
  MARK_SWITCHABLE, chartHeightPx, chartPlan, type CartesianMark, type ChartSpec,
} from "./chartPlan";

export {
  chartHeightPx, formatAxisTick, formatChartValue, mixedUnitAxes, mustUnstack, MARK_SWITCHABLE,
  type CartesianMark, type CausalGraphLink, type CausalGraphNode, type ChartPanel, type ChartSeries, type ChartSpec,
} from "./chartPlan";

interface ChartRendererProps {
  spec: ChartSpec;
}

function exportToCsv(data: any[], filename: string) {
  if (!data || data.length === 0) return;
  const headers = Array.from(new Set(data.flatMap(Object.keys)));
  const rows = data.map((row) =>
    headers.map((header) => {
      const val = row[header];
      if (val === null || val === undefined) return '""';
      return `"${String(val).replace(/"/g, '""')}"`;
    }).join(","),
  );
  const blob = new Blob([[headers.join(","), ...rows].join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.setAttribute("href", url);
  link.setAttribute("download", `${filename.toLowerCase().replace(/[^a-z0-9]+/g, "_")}.csv`);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export interface DrillActions {
  /** Chart only: jump to the table view with this row selected. */
  onShowData?: () => void;
  /** Submit a follow-up pinned to this point's exact values. */
  onAsk: () => void;
  /** Submit a previous-period comparison for this point. */
  onCompare: () => void;
}

export function SelectionInsight({ columns, row, onClose, drill }: {
  columns: string[]; row: Record<string, unknown>; onClose: () => void; drill?: DrillActions;
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
    <Card size="sm" role="status" aria-label="Selected data point">
      <CardContent>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          {entries.map(([col, val]) => (
            <React.Fragment key={col}>
              <dt className="text-muted-foreground">{col}</dt>
              <dd className="m-0 break-words">{String(val ?? "—")}</dd>
            </React.Fragment>
          ))}
        </dl>
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2">
        {drill?.onShowData && (
          <Button type="button" variant="outline" size="sm" onClick={drill.onShowData} aria-label="Show underlying data">Data</Button>
        )}
        {drill && (
          <>
            <Button type="button" variant="outline" size="sm" onClick={drill.onAsk} aria-label="Ask Seleric about this point">Ask about this</Button>
            <Button type="button" variant="outline" size="sm" onClick={drill.onCompare} aria-label="Compare this point with the previous period">Compare previous</Button>
          </>
        )}
        <Button type="button" variant="outline" size="sm" onClick={copy} aria-label="Copy selected values">
          {copied ? <CheckIcon /> : <CopyIcon />}
          {copied ? "Copied" : "Copy"}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => openInspector("Debug")}>Debug</Button>
        <Button type="button" variant="ghost" size="sm" onClick={onClose} aria-label="Clear selection">Clear</Button>
      </CardFooter>
    </Card>
  );
}

export const ChartRenderer: React.FC<ChartRendererProps> = ({ spec }) => {
  const [view, setView] = useState<"chart" | "table">("chart");
  const [selected, setSelected] = useState<number | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [mark, setMark] = useState<"auto" | CartesianMark>("auto");
  const submit = useConversationStore((s) => s.submit);
  // Recharts draws nothing until it has measured its container; show a skeleton for that first frame.
  const [drawn, setDrawn] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setDrawn(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  const rows = useMemo(
    () => (Array.isArray(spec.data) ? spec.data.filter((r) => r && typeof r === "object") : []),
    [spec.data],
  );
  // Stored rows lose key order: lead with the x axis, then the series in the order the spec lists them.
  const dataColumns = useMemo(() => {
    const present = new Set(rows.flatMap((row) => Object.keys(row)));
    const preferred = [spec.xAxis?.key, ...(spec.series ?? []).map((x) => x.key)].filter((c): c is string => !!c && present.has(c));
    return [...new Set([...preferred, ...present])];
  }, [rows, spec.xAxis?.key, spec.series]);
  const dimensionCol = spec.xAxis?.key && dataColumns.includes(spec.xAxis.key) ? spec.xAxis.key : dataColumns[0];

  const plan = useMemo(() => chartPlan(spec, { mark: mark === "auto" ? undefined : mark }), [spec, mark]);
  const canSwitchMark = plan?.kind === "cartesian" && MARK_SWITCHABLE.has(String(spec.chart_type));

  // Follow-ups pinned to the selected point's exact values.
  const askAbout = useCallback((kind: "ask" | "compare", row: Record<string, unknown>) => {
    const ctx = buildPointContext({
      chartTitle: spec.title,
      dimensionKey: dimensionCol,
      row,
      series: (spec.series ?? []).map((s) => ({ key: s.key, name: s.name })),
      columns: dataColumns,
    });
    const question = buildFollowUp(kind, ctx);
    if (question) void submit(question);
  }, [spec.title, spec.series, dimensionCol, dataColumns, submit]);

  const handleDownloadCsv = useCallback(() => {
    if (spec.downloadable_csv_url) {
      window.open(spec.downloadable_csv_url, "_blank");
      return;
    }
    exportToCsv(spec.data || [], spec.title || "chart_data");
  }, [spec]);

  const toggleSelected = useCallback((index: number) => setSelected((current) => (current === index ? null : index)), []);

  if (!spec || !spec.chart_type || !plan) {
    // Fail loudly: a form this UI cannot render must be visible, not blank.
    const unsupported = typeof spec?.chart_type === "string" ? spec.chart_type : "";
    return (
      <Alert variant="destructive" role="alert">
        <AlertDescription>
          This chart uses an unsupported form{unsupported ? ` (${unsupported})` : ""}. Supported forms: {CHART_TYPES.join(", ")}.
        </AlertDescription>
      </Alert>
    );
  }

  const selectedRow = selected !== null ? (rows[selected] as Record<string, unknown> | undefined) : undefined;
  const pickable = plan.kind === "cartesian" || plan.kind === "waterfall" || plan.kind === "slices";
  const height = chartHeightPx(spec);
  const title = spec.title || "Visualization";
  const csvButton = (
    <Button onClick={handleDownloadCsv} type="button" variant="outline" size="sm" aria-label="Download chart data as CSV">
      <DownloadIcon /> CSV
    </Button>
  );

  return (
    <Card role="region" aria-label={spec.title || "Data visualization"}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          {rows.length > 0 && (
            <Tabs value={view} onValueChange={(value) => setView(value as "chart" | "table")}>
              <TabsList aria-label="Visualization or data view">
                <TabsTrigger value="chart">Chart</TabsTrigger>
                <TabsTrigger value="table">Table</TabsTrigger>
              </TabsList>
            </Tabs>
          )}
          <span>{title}</span>
          {canSwitchMark && view === "chart" && (
            <Tabs value={mark} onValueChange={(value) => setMark(value as "auto" | CartesianMark)}>
              <TabsList aria-label="Chart mark">
                {(["auto", "bar", "line"] as const).map((mode) => (
                  <TabsTrigger key={mode} value={mode} title={mode === "auto" ? "Backend-chosen form" : `Show as ${mode} chart`}>
                    {mode === "auto" ? "Auto" : mode === "bar" ? "Bars" : "Lines"}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          )}
        </CardTitle>
        <CardAction className="flex gap-2">
          {pickable && (
            <Button onClick={() => setExpanded(true)} type="button" variant="outline" size="sm" aria-label="Expand chart" title="Expand">
              <MaximizeIcon /> Expand
            </Button>
          )}
          {csvButton}
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {(spec.warnings ?? []).length > 0 && (
          <p className="text-xs text-muted-foreground" role="status">{(spec.warnings ?? []).join(" ")}</p>
        )}
        {view === "chart" && !drawn ? (
          <Skeleton className="w-full" style={{ height }} role="status" aria-busy="true" aria-label="Loading chart" />
        ) : view === "chart" ? (
          <ShadcnChart plan={plan} spec={spec} selected={selected} onSelect={toggleSelected} height={height} />
        ) : (
          <DataTable
            columns={dataColumns}
            rows={rows}
            ariaLabel={`${spec.title || "Chart"} data table`}
            selectedIndex={selected}
            onSelectedIndexChange={setSelected}
          />
        )}
        {selectedRow && (
          <SelectionInsight
            columns={dataColumns}
            row={selectedRow}
            onClose={() => setSelected(null)}
            drill={{
              onShowData: view === "chart" ? () => setView("table") : undefined,
              onAsk: () => askAbout("ask", selectedRow),
              onCompare: () => askAbout("compare", selectedRow),
            }}
          />
        )}
        {!selectedRow && pickable && dimensionCol && (
          <p className="text-xs text-muted-foreground">
            Select a {plan.kind === "slices" ? "segment" : "row"} to inspect its exact values.
          </p>
        )}
      </CardContent>
      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent className="sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription className="sr-only">Expanded chart</DialogDescription>
          </DialogHeader>
          <ShadcnChart plan={plan} spec={spec} selected={selected} onSelect={toggleSelected} height={height > 300 ? height : "56vh"} />
          <div className="flex justify-end">{csvButton}</div>
        </DialogContent>
      </Dialog>
    </Card>
  );
};
