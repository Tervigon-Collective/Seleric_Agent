import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { traceApi } from "../api/trace";
import { isTraceableMissionId, useOffice } from "../store";
import { ShadcnChart } from "./charts/LazyShadcnChart";
import { chartPlan, type ChartSpec } from "./charts/chartPlan";

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

function traceUsedDiagnosis(trace: Record<string, unknown> | null | undefined): boolean {
  const steps = Array.isArray(trace?.steps) ? trace.steps : [];
  return steps.some((step) => {
    if (!step || typeof step !== "object") return false;
    const row = step as Record<string, unknown>;
    return row.kind === "tool_call" && row.tool === "diagnose_metric_change";
  });
}

/** One diagnosis: the drivers of the change as a signed bar chart and a table that states each classification. */
function GraphCard({ spec }: { spec: ChartSpec }) {
  const plan = chartPlan({
    ...spec,
    nodes: (spec.nodes ?? []).map((node) => ({ ...node, name: friendlyNodeName(node.id, node.name) })),
  });
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle>{spec.title || "Causal graph"}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {!!spec.warnings?.length && <p className="text-xs text-muted-foreground">{spec.warnings[0]}</p>}
        {plan && <ShadcnChart plan={plan} spec={{ ...spec, nodes: (spec.nodes ?? []).map((n) => ({ ...n, name: friendlyNodeName(n.id, n.name) })) }} selected={null} onSelect={() => undefined} height={0} />}
        <p className="text-xs text-muted-foreground">
          Day-of-week drivers (Tue–Sun) adjust for weekly seasonality; Monday is the omitted reference level.
        </p>
      </CardContent>
    </Card>
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

  if (!missionId) {
    return <p className="text-sm text-muted-foreground">Run a diagnosis question to see the causal graph.</p>;
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
    return <p className="text-sm text-muted-foreground">{empty}</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {graphs.map((spec, index) => (
        <GraphCard key={spec.title || index} spec={spec} />
      ))}
    </div>
  );
}
