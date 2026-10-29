import { Skeleton } from "@/components/ui/skeleton";
import { ArrowUpRightIcon, GlobeIcon, WrenchIcon } from "lucide-react";
import { Trace, TraceIcon, TraceLine, TraceTitle } from "@/components/ui/trace";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useMemo, useState } from "react";
import type { MessagePart } from "../api/contracts";
import { useShellStore } from "../stores/shell";
import { useConversationStore } from "../stores/conversation";
import { DataTable } from "./DataTable";
import { SafeContent } from "./SafeContent";
import { ApprovalCard } from "./ApprovalCard";
import { EmailPreviewCard } from "./EmailPreviewCard";
import { ChartRenderer, SelectionInsight } from "./charts/ChartRenderer";
import { useChartVocabularyDrift } from "../api/chartContract";
import { buildFollowUp, buildPointContext } from "../api/drillContext";
import { KpiGrid } from "./report/KpiGrid";

const text = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

function ResultTable({ content }: { content: unknown }) {
  const rows = Array.isArray(content) ? content : (record(content).rows as unknown[] ?? []);
  const safeRows = useMemo(
    () => rows.filter((row) => row && typeof row === "object").map(record),
    [rows],
  );
  // The backend sends the column order (entity, then measures as the question listed them). Stored rows lose their key
  // order (JSONB sorts keys), so rows only supply columns the order does not name.
  const columns = useMemo(() => {
    const given = Array.isArray(record(content).columns) ? (record(content).columns as unknown[]).map(String) : [];
    const present = new Set(safeRows.flatMap((row) => Object.keys(row)));
    return [...given.filter((c) => present.has(c)), ...[...present].filter((c) => !given.includes(c))];
  }, [content, safeRows]);
  const [selected, setSelected] = useState<number | null>(null);
  const submit = useConversationStore((s) => s.submit);

  const askAbout = (kind: "ask" | "compare", row: Record<string, unknown>) => {
    const ctx = buildPointContext({ row, series: [], columns });
    const question = buildFollowUp(kind, ctx);
    if (question) void submit(question);
  };

  const state = String(record(content).state || "");
  if (state === "loading") {
    return <Skeleton className="h-40 w-full" role="status" aria-busy="true" aria-label="Loading table" />;
  }
  if (state === "error") {
    return <Alert role="alert"><AlertDescription>Table could not be loaded from certified evidence.</AlertDescription></Alert>;
  }
  if (!columns.length) {
    return <p className="text-sm text-muted-foreground" role="status">No rows returned from certified evidence.</p>;
  }

  const selectedRow = selected !== null ? safeRows[selected] : null;
  return (
    <div className="flex flex-col gap-2">
      <DataTable
        columns={columns}
        rows={safeRows}
        initialSort={record(record(content).sort)}
        caption={typeof record(content).caption === "string" ? String(record(content).caption) : undefined}
        selectedIndex={selected}
        onSelectedIndexChange={setSelected}
      />
      {selectedRow && (
        <SelectionInsight
          columns={columns}
          row={selectedRow}
          onClose={() => setSelected(null)}
          drill={{
            onAsk: () => askAbout("ask", selectedRow),
            onCompare: () => askAbout("compare", selectedRow),
          }}
        />
      )}
    </div>
  );
}

/** Tool pattern: one Trace line, status in the title as well as the colour. */
function ToolLine({ tool, status }: { tool: string; status: string }) {
  const s = status.toLowerCase();
  const failed = /fail|error|denied/.test(s);
  const done = /complet|done|success|ok|finish/.test(s);
  const title = failed ? `Failed to run ${tool}` : done ? `Ran ${tool}` : `Running ${tool}`;
  return (
    <Trace>
      <TraceLine status={failed ? "error" : done ? undefined : "running"}>
        <TraceIcon><WrenchIcon /></TraceIcon>
        <TraceTitle>{title}</TraceTitle>
      </TraceLine>
    </Trace>
  );
}

export function MessagePartRenderer({ part }: { part: MessagePart }) {
  const showArtifact = useShellStore((state) => state.showArtifact);
  useChartVocabularyDrift();
  const value = record(part.content);
  switch (part.type) {
    case "TEXT": return <SafeContent text={text(part.content)} />;
    case "TABLE": return <ResultTable content={part.content} />;
    case "CODE": return <pre data-slot="part-code" className="m-0 overflow-x-auto rounded-md border bg-muted p-3 font-mono text-xs"><code>{text(part.content)}</code></pre>;
    case "SOURCE": {
      const url = typeof value.url === "string" && /^https?:\/\//.test(value.url) ? value.url : null;
      const excerpt = typeof value.excerpt === "string" && value.excerpt.trim() ? value.excerpt : null;
      const line = (
        <TraceLine>
          <TraceIcon><GlobeIcon /></TraceIcon>
          <TraceTitle>
            {text(value.title ?? url ?? value.evidence_id ?? "Evidence")}
            {url && <span className="sr-only"> (opens in a new tab)</span>}
          </TraceTitle>
          {url && <TraceIcon><ArrowUpRightIcon /></TraceIcon>}
        </TraceLine>
      );
      return <Trace title={excerpt ?? undefined}>{url ? <a href={url} target="_blank" rel="noreferrer">{line}</a> : line}</Trace>;
    }
    case "TOOL_CALL": return <ToolLine tool={text(value.tool)} status={text(value.status ?? "")} />;
    case "ARTIFACT": return <Button type="button" variant="outline" size="sm" className="justify-start" onClick={() => showArtifact({ ...value, ...record(value.provenance) })}><strong>Artifact</strong><span>{text(value.title ?? value.artifact_id)}</span></Button>;
    case "CHART": {
      const spec = value.data ?? value;
      if (value.state === "loading") return <Skeleton className="h-72 w-full" role="status" aria-busy="true" aria-label="Loading chart" />;
      if (!spec || (typeof spec === "object" && !(spec as any).data && !(spec as any).chart_type && !(spec as any).series)) {
        return <Alert role="alert"><AlertDescription>Chart is still loading or has no certified data.</AlertDescription></Alert>;
      }
      try {
        return <ChartRenderer spec={spec as any} />;
      } catch {
        return <Alert role="alert"><AlertDescription>Chart could not be rendered from certified evidence.</AlertDescription></Alert>;
      }
    }
    case "KPI_GRID":
      return <KpiGrid content={value} />;
    case "AGENT_STATUS": return <p className="m-0 text-sm text-muted-foreground" aria-live="polite">{text(part.content)}</p>;
    case "APPROVAL": return value.action === "send_email" ? <EmailPreviewCard value={value} /> : <ApprovalCard value={value} />;
    case "WARNING": return <Alert role="alert"><AlertDescription>{text(part.content)}</AlertDescription></Alert>;
    default: return null;
  }
}
