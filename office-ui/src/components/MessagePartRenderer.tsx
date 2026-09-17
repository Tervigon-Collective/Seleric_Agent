import type { MessagePart } from "../api/contracts";
import { useShellStore } from "../stores/shell";
import { SafeContent } from "./SafeContent";
import { ApprovalCard } from "./ApprovalCard";

const text = (value: unknown) => typeof value === "string" ? value : JSON.stringify(value, null, 2);
const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

function Table({ content }: { content: unknown }) {
  const rows = Array.isArray(content) ? content : (record(content).rows as unknown[] ?? []);
  const safeRows = rows.filter((row) => row && typeof row === "object").map(record);
  const columns = [...new Set(safeRows.flatMap((row) => Object.keys(row)))];
  return <div className="part-table-wrap"><table><thead><tr>{columns.map((key) => <th key={key}>{key}</th>)}</tr></thead>
    <tbody>{safeRows.map((row, index) => <tr key={index}>{columns.map((key) => <td key={key}>{text(row[key])}</td>)}</tr>)}</tbody>
  </table></div>;
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
      return <aside className="part-card source"><strong>Source</strong> {url
        ? <a href={url} target="_blank" rel="noreferrer">{text(value.title ?? url)}</a>
        : <span>{text(value.title ?? value.evidence_id ?? "Evidence")}</span>}</aside>;
    }
    case "TOOL_CALL": return <aside className="part-card tool"><strong>Tool</strong><code>{text(value.tool)}</code><span>{text(value.status ?? "")}</span></aside>;
    case "ARTIFACT": return <button className="part-card artifact" onClick={() => showArtifact({ ...value, ...record(value.provenance) })}><strong>Artifact</strong><span>{text(value.title ?? value.artifact_id)}</span></button>;
    case "CHART": return <figure className="part-card chart"><figcaption>{text(value.title ?? "Chart")}</figcaption><pre>{text(value.data ?? value)}</pre></figure>;
    case "AGENT_STATUS": return <p className="part-status" aria-live="polite">● {text(part.content)}</p>;
    case "APPROVAL": return <ApprovalCard value={value} />;
    case "WARNING": return <aside className="part-card warning" role="alert">{text(part.content)}</aside>;
    default: return null;
  }
}
