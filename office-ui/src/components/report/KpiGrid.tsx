import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { cn } from "cn";
import { Skeleton } from "@/components/ui/skeleton";

export interface KpiCardData {
  label: string;
  value?: number | null;
  display?: string;
  unit?: string | null;
  population?: string;
  time_basis?: string;
  provenance?: string;
  evidence_ids?: string[];
  comparison?: { value?: number; previous_display?: string; delta?: number; delta_pct?: number; delta_display?: string; direction?: "up" | "down" | "flat" } | null;
  state?: "ok" | "missing" | "loading" | "error";
  section_id?: string;
}

export interface KpiGridContent {
  title?: string;
  section_id?: string;
  cards?: KpiCardData[];
  state?: "ok" | "loading" | "error" | "empty";
  error?: string;
}

/** "▲ 12.3%" (the change as a share of the previous value), else the signed absolute change. */
export function formatDelta(comparison: KpiCardData["comparison"]): { text: string; direction: "up" | "down" | "flat" } | null {
  if (!comparison) return null;
  // The server writes the change the way its unit should be read (points for a share, absolute for an average).
  if (comparison.delta_display) return { text: comparison.delta_display, direction: comparison.direction ?? "flat" };
  const { delta, delta_pct: pct } = comparison;
  if (delta === undefined || delta === null || Number.isNaN(delta)) return null;
  const direction = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
  const arrow = direction === "up" ? "▲" : direction === "down" ? "▼" : "–";
  if (pct !== undefined && pct !== null && Number.isFinite(pct)) return { text: `${arrow} ${Math.abs(pct).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`, direction };
  const sign = delta > 0 ? "+" : "";
  return { text: `${arrow} ${sign}${delta.toLocaleString(undefined, { maximumFractionDigits: 2 })}`, direction };
}

const GRID = "grid w-full grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-3";

export function KpiGrid({ content }: { content: KpiGridContent | Record<string, unknown> }) {
  const data = content as KpiGridContent;
  const state = data.state ?? (data.cards && data.cards.length ? "ok" : "empty");

  if (state === "loading") {
    return (
      <section className={GRID} aria-busy="true" aria-label="Loading KPIs">
        <Skeleton className="h-[72px]" />
        <Skeleton className="h-[72px]" />
        <Skeleton className="h-[72px]" />
      </section>
    );
  }

  if (state === "error") {
    return (
      <Alert variant="destructive" role="alert">
        <AlertDescription>{data.error || "KPI cards could not be loaded from certified evidence."}</AlertDescription>
      </Alert>
    );
  }

  const cards = data.cards ?? [];
  if (!cards.length || state === "empty") {
    return (
      <Alert role="status">
        <AlertDescription>No certified headline metrics were available for this section.</AlertDescription>
      </Alert>
    );
  }

  return (
    <section className={GRID} aria-label={data.title || "KPI cards"}>
      {data.title ? <h3 className="col-span-full m-0 text-sm font-medium">{data.title}</h3> : null}
      {cards.map((card, index) => {
        const missing = card.state === "missing" || card.value === null || card.value === undefined;
        const delta = formatDelta(card.comparison);
        return (
          <Card
            key={`${card.label}-${index}`}
            size="sm"
            data-slot="kpi-card"
            data-evidence-ids={(card.evidence_ids || []).join(",")}
          >
            <CardHeader>
              <CardDescription className="truncate">{card.label}</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-1">
              <div className={cn("text-2xl font-semibold tabular-nums", missing && "text-sm font-normal text-muted-foreground")}>
                {missing ? "not available from certified evidence" : card.display ?? String(card.value)}
              </div>
              <div className="flex flex-wrap gap-x-2 text-xs text-muted-foreground">
                {card.population ? <span>{card.population}</span> : null}
                {card.time_basis ? <span>{card.time_basis}</span> : null}
                {delta ? <span className="tabular-nums" data-direction={delta.direction}>{delta.text}</span> : null}
                {delta && card.comparison?.previous_display ? <span>was {card.comparison.previous_display}</span> : null}
              </div>
            </CardContent>
          </Card>
        );
      })}
    </section>
  );
}

export default KpiGrid;
