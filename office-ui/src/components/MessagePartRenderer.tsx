import { useMemo, useState } from "react";
import type { MessagePart } from "../api/contracts";
import { useShellStore } from "../stores/shell";
import { SafeContent } from "./SafeContent";
import { ApprovalCard } from "./ApprovalCard";
import { ChartRenderer, SelectionInsight } from "./charts/ChartRenderer";
import { DownloadIcon } from "./icons";

const text = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

/** Numeric-looking cells (digits, currency, %, separators) right-align. Values are never reformatted. */
const NUMERIC = /^[+-]?[$€£¥]?\s*\d[\d\s,._]*\s?[%‰]?$/;

function isNumericColumn(values: string[]): boolean {
  const sample = values.filter((v) => v.trim() !== "").slice(0, 20);
  if (sample.length < 2) return false;
  return sample.every((v) => NUMERIC.test(v.trim()));
}

function toCsv(columns: string[], rows: Record<string, unknown>[]): string {
  const escape = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  return [columns.map(escape).join(","), ...rows.map((row) => columns.map((c) => escape(row[c])).join(","))].join("\n");
}

function Table({ content }: { content: unknown }) {
  const rows = Array.isArray(content) ? content : (record(content).rows as unknown[] ?? []);
  const safeRows = useMemo(
    () => rows.filter((row) => row && typeof row === "object").map(record),
    [rows],
  );
  const columns = useMemo(() => [...new Set(safeRows.flatMap((row) => Object.keys(row)))], [safeRows]);
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<1 | -1>(1);
  const [selected, setSelected] = useState<number | null>(null);

  const numericCols = useMemo(() => {
    const map = new Map<string, boolean>();
    for (const col of columns) {
      map.set(col, isNumericColumn(safeRows.map((row) => text(row[col]))));
    }
    return map;
  }, [columns, safeRows]);

  const visible = useMemo(() => {
    if (!sortKey) return safeRows;
    return [...safeRows].sort((a, b) => {
      const left = text(a[sortKey]);
      const right = text(b[sortKey]);
      const numLeft = Number(left.replace(/[^0-9.\-+eE]/g, ""));
      const numRight = Number(right.replace(/[^0-9.\-+eE]/g, ""));
      if (Number.isFinite(numLeft) && Number.isFinite(numRight) && left.trim() !== "" && right.trim() !== "") {
        return (numLeft - numRight) * sortDir;
      }
      return left.localeCompare(right) * sortDir;
    });
  }, [safeRows, sortKey, sortDir]);

  if (!columns.length) {
    return <p className="empty-note" role="status">No rows returned.</p>;
  }

  const download = () => {
    const blob = new Blob([toCsv(columns, visible)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "seleric_table.csv";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <figure style={{ margin: 0 }}>
      <div className="table-toolbar">
        <span>{visible.length} row{visible.length === 1 ? "" : "s"}</span>
        <span className="spacer" />
        <button type="button" onClick={download} aria-label="Download table as CSV">
          <DownloadIcon size={12} /> CSV
        </button>
      </div>
      <div className="part-table-wrap">
        <table aria-label="Analytical results table">
          <thead>
            <tr>
              {columns.map((key) => (
                <th key={key} scope="col" className={numericCols.get(key) ? "num" : ""} aria-sort={sortKey === key ? (sortDir === 1 ? "ascending" : "descending") : "none"}>
                  <button
                    type="button"
                    onClick={() => {
                      if (sortKey !== key) { setSortKey(key); setSortDir(1); }
                      else if (sortDir === 1) setSortDir(-1);
                      else setSortKey(null);
                    }}
                    title={`Sort by ${key}`}
                    style={{ all: "unset", cursor: "pointer", display: "inline-flex", gap: 4, alignItems: "center" }}
                  >
                    {key}
                    <span aria-hidden="true" style={{ color: "var(--text-faint)", fontSize: 10 }}>
                      {sortKey === key ? (sortDir === 1 ? "▲" : "▼") : ""}
                    </span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, index) => (
              <tr
                key={index}
                className={selected === index ? "selected-row" : ""}
                aria-selected={selected === index}
                tabIndex={0}
                onClick={() => setSelected((current) => (current === index ? null : index))}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setSelected((current) => (current === index ? null : index));
                  }
                }}
              >
                {columns.map((key) => (
                  <td key={key} className={numericCols.get(key) ? "num" : ""}>{text(row[key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected !== null && visible[selected] && (
        <SelectionInsight columns={columns} row={visible[selected]} onClose={() => setSelected(null)} />
      )}
    </figure>
  );
}

export function MessagePartRenderer({ part }: { part: MessagePart }) {
  const showArtifact = useShellStore((state) => state.showArtifact);
  const value = record(part.content);
  switch (part.type) {
    case "TEXT": return <SafeContent text={text(part.content)} />;
    case "TABLE": return <Table content={part.content} />;
    case "CODE": return <pre className="part-code"><code>{text(part.content)}</code></pre>;
    case "SOURCE": {
      const url = typeof value.url === "string" && /^https?:\/\//.test(value.url) ? value.url : null;
      const excerpt = typeof value.excerpt === "string" && value.excerpt.trim() ? value.excerpt : null;
      return <aside className="part-card source" title={excerpt ?? undefined}>
        <strong>Source</strong>
        {url
          ? <a href={url} target="_blank" rel="noreferrer">{text(value.title ?? url)}</a>
          : <span>{text(value.title ?? value.evidence_id ?? "Evidence")}</span>}
        {excerpt && <span className="source-excerpt">{excerpt}</span>}
      </aside>;
    }
    case "TOOL_CALL": return <aside className="part-card tool"><strong>Tool</strong><code>{text(value.tool)}</code><span>{text(value.status ?? "")}</span></aside>;
    case "ARTIFACT": return <button className="part-card artifact" onClick={() => showArtifact({ ...value, ...record(value.provenance) })}><strong>Artifact</strong><span>{text(value.title ?? value.artifact_id)}</span></button>;
    case "CHART": return <ChartRenderer spec={value.data ?? value as any} />;
    case "AGENT_STATUS": return <p className="part-status" aria-live="polite">{text(part.content)}</p>;
    case "APPROVAL": return <ApprovalCard value={value} />;
    case "WARNING": return <aside className="part-card warning" role="alert">{text(part.content)}</aside>;
    default: return null;
  }
}
