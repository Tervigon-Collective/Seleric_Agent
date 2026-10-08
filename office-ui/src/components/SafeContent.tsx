import type { ReactNode } from "react";

/*
 * Markdown-lite renderer for assistant answers. Builds React nodes only — never
 * HTML strings — so model/user text can't inject markup. Supports headings,
 * paragraphs, bullet/numbered lists, GFM tables, blockquotes, rules, fenced and
 * inline code, **bold**, *italic*, and http(s) links.
 */

const INLINE =
  /(`[^`\n]+`)|(\*\*[^*\n]+?\*\*)|(__[^_\n]+?__)|(\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))|(https?:\/\/[^\s<)]+[^\s<).,;:!?])|(\*[^*\s][^*\n]*?\*)|(\b_[^_\s][^_\n]*?_\b)/g;

function inline(text: string, keyPrefix = "i"): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let n = 0;
  for (const match of text.matchAll(INLINE)) {
    const token = match[0];
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const key = `${keyPrefix}${n++}`;
    if (token.startsWith("`")) {
      out.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**") || token.startsWith("__")) {
      out.push(<strong key={key}>{inline(token.slice(2, -2), `${key}.`)}</strong>);
    } else if (token.startsWith("[")) {
      const split = token.indexOf("](");
      out.push(
        <a key={key} href={token.slice(split + 2, -1)} target="_blank" rel="noreferrer">
          {token.slice(1, split)}
        </a>,
      );
    } else if (/^https?:\/\//.test(token)) {
      out.push(<a key={key} href={token} target="_blank" rel="noreferrer">{token}</a>);
    } else {
      out.push(<em key={key}>{inline(token.slice(1, -1), `${key}.`)}</em>);
    }
    last = at + token.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const BULLET = /^\s*[-*+•]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const TABLE_SEP_SHAPE = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
// The pipe requirement keeps a bare "---" rule from being read as a delimiter row.
const TABLE_SEP = { test: (line: string) => line.includes("|") && TABLE_SEP_SHAPE.test(line) };

/** Split a GFM row. `\|` stays inside its cell (campaign names often contain `|`). */
const splitRow = (line: string): string[] => {
  const body = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  const cells: string[] = [];
  let current = "";
  for (let i = 0; i < body.length; i++) {
    if (body[i] === "\\" && body[i + 1] === "|") {
      current += "|";
      i++;
      continue;
    }
    if (body[i] === "|") {
      cells.push(current.trim());
      current = "";
      continue;
    }
    current += body[i];
  }
  cells.push(current.trim());
  return cells;
};

/**
 * A row wider than its header has an unescaped `|` in a label (live: Google
 * campaign names like "[Google Build] Brand Search | 5th March"). Fold the
 * overflow back into the first cell so later columns stay on their headers.
 * Rendering only `head.length` cells used to drop the last metric.
 */
const alignRow = (row: string[], width: number): string[] => {
  if (width <= 0 || row.length <= width) return row;
  const extra = row.length - width;
  return [row.slice(0, extra + 1).join(" | "), ...row.slice(extra + 1)];
};

/** Currency / count / ratio cells — keep on one line and right-align. */
const NUMERIC_CELL =
  /^(?:[-+−]|₹|\$|€|£)?\s*[\d,]+(?:\.\d+)?\s*%?$/;

const isNumericCell = (cell: string): boolean => {
  const t = cell.replace(/\*\*|__/g, "").trim();
  return t !== "" && NUMERIC_CELL.test(t.replace(/\u2212/g, "-"));
};

const numericColumns = (width: number, rows: string[][]): boolean[] =>
  Array.from({ length: width }, (_, c) => {
    const values = rows.map((r) => (r[c] ?? "").trim()).filter(Boolean);
    return values.length > 0 && values.every(isNumericCell);
  });

const isBlockStart = (line: string, next: string | undefined): boolean =>
  HEADING.test(line) || RULE.test(line) || BULLET.test(line) || ORDERED.test(line)
  || QUOTE.test(line) || (line.includes("|") && next !== undefined && TABLE_SEP.test(next));

function blocks(source: string, keyPrefix: string): ReactNode[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  let k = 0;
  const key = () => `${keyPrefix}${k++}`;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }

    const heading = HEADING.exec(line);
    if (heading) {
      const level = Math.min(6, Math.max(3, heading[1].length + 1)); // chat-scale: h3..h6
      const Tag = `h${level}` as "h3" | "h4" | "h5" | "h6";
      out.push(<Tag key={key()}>{inline(heading[2])}</Tag>);
      i++;
      continue;
    }
    if (RULE.test(line)) { out.push(<hr key={key()} />); i++; continue; }

    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      const head = splitRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      const alignedRows = rows.map((row) => alignRow(row, head.length));
      const numeric = numericColumns(head.length, alignedRows);
      out.push(
        <div className="md-table-wrap" key={key()}>
          <table>
            <thead>
              <tr>
                {head.map((cell, c) => (
                  <th key={c} className={numeric[c] ? "num" : undefined}>{inline(cell)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {alignedRows.map((row, r) => (
                <tr key={r}>
                  {head.map((_, c) => (
                    <td key={c} className={numeric[c] ? "num" : undefined}>{inline(row[c] ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    if (BULLET.test(line) || ORDERED.test(line)) {
      const ordered = ORDERED.test(line);
      const pattern = ordered ? ORDERED : BULLET;
      const items: string[] = [];
      while (i < lines.length) {
        const m = pattern.exec(lines[i]);
        if (m) { items.push(m[1]); i++; continue; }
        // an indented continuation line belongs to the previous item
        if (items.length && /^\s{2,}\S/.test(lines[i]) && !BULLET.test(lines[i]) && !ORDERED.test(lines[i])) {
          items[items.length - 1] += ` ${lines[i].trim()}`;
          i++;
          continue;
        }
        break;
      }
      const List = ordered ? "ol" : "ul";
      out.push(<List key={key()}>{items.map((item, n) => <li key={n}>{inline(item)}</li>)}</List>);
      continue;
    }

    if (QUOTE.test(line)) {
      const quoted: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i])) {
        quoted.push((QUOTE.exec(lines[i]) as RegExpExecArray)[1]);
        i++;
      }
      out.push(<blockquote key={key()}>{blocks(quoted.join("\n"), `${key()}q`)}</blockquote>);
      continue;
    }

    const paragraph: string[] = [];
    while (i < lines.length && lines[i].trim() && (paragraph.length === 0 || !isBlockStart(lines[i], lines[i + 1]))) {
      paragraph.push(lines[i]);
      i++;
    }
    out.push(
      <p key={key()}>
        {paragraph.flatMap((text, n) => (n ? [<br key={`br${n}`} />, ...inline(text.trim(), `p${n}`)] : inline(text.trim(), "p0")))}
      </p>,
    );
  }
  return out;
}

export function SafeContent({ text }: { text: string }) {
  const parts = text.split(/```/);
  return (
    <div className="safe-content">
      {parts.map((part, index) =>
        index % 2
          ? <pre key={index}><code>{part.replace(/^[\w+-]*\n/, "").replace(/\n$/, "")}</code></pre>
          : blocks(part, `b${index}-`),
      )}
    </div>
  );
}
