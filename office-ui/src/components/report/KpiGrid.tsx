import type { CSSProperties } from "react";

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

const gridStyle: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
  gap: "12px",
  width: "100%",
};

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

export function KpiGrid({ content }: { content: KpiGridContent | Record<string, unknown> }) {
  const data = content as KpiGridContent;
  const state = data.state ?? (data.cards && data.cards.length ? "ok" : "empty");

  if (state === "loading") {
    return (
      <section className="kpi-grid kpi-grid--loading" aria-busy="true" aria-label="Loading KPIs">
        <div className="kpi-skeleton" />
        <div className="kpi-skeleton" />
        <div className="kpi-skeleton" />
      </section>
    );
  }

  if (state === "error") {
    return (
      <aside className="kpi-grid kpi-grid--error" role="alert">
        {data.error || "KPI cards could not be loaded from certified evidence."}
      </aside>
    );
  }

  const cards = data.cards ?? [];
  if (!cards.length || state === "empty") {
    return (
      <aside className="kpi-grid kpi-grid--empty" role="status">
        No certified headline metrics were available for this section.
      </aside>
    );
  }

  return (
    <section className="kpi-grid" aria-label={data.title || "KPI cards"} style={gridStyle}>
      {data.title ? <h3 className="kpi-grid__title">{data.title}</h3> : null}
      {cards.map((card, index) => {
        const missing = card.state === "missing" || card.value === null || card.value === undefined;
        const delta = formatDelta(card.comparison);
        return (
          <article
            key={`${card.label}-${index}`}
            className={`kpi-card${missing ? " kpi-card--missing" : ""}`}
            data-evidence-ids={(card.evidence_ids || []).join(",")}
          >
            <header className="kpi-label">{card.label}</header>
            <div className="kpi-value">
              {missing ? "not available from certified evidence" : card.display ?? String(card.value)}
            </div>
            <footer className="kpi-basis">
              {card.population ? <span>{card.population}</span> : null}
              {card.time_basis ? <span>{card.time_basis}</span> : null}
              {delta ? <span className={`kpi-delta neutral kpi-delta--${delta.direction}`}>{delta.text}</span> : null}
              {delta && card.comparison?.previous_display ? <span>was {card.comparison.previous_display}</span> : null}
            </footer>
          </article>
        );
      })}
    </section>
  );
}

export default KpiGrid;
