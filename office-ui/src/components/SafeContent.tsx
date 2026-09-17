import type { ReactNode } from "react";

const URL = /(https?:\/\/[^\s<]+)/g;

function linkify(text: string): ReactNode[] {
  return text.split(URL).map((part, index) =>
    /^https?:\/\//.test(part)
      ? <a key={index} href={part} target="_blank" rel="noreferrer">{part}</a>
      : part,
  );
}

/** Plain-text-first renderer: links and fenced code, never injected HTML. */
export function SafeContent({ text }: { text: string }) {
  const blocks = text.split(/```/);
  return (
    <div className="safe-content">
      {blocks.map((block, index) =>
        index % 2
          ? <pre key={index}><code>{block.replace(/^[a-z]+\n/i, "")}</code></pre>
          : block.split(/\n{2,}/).map((paragraph, paragraphIndex) => (
              paragraph && <p key={`${index}-${paragraphIndex}`}>{linkify(paragraph)}</p>
            )),
      )}
    </div>
  );
}
