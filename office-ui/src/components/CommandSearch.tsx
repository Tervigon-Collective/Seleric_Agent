import { useEffect, useRef, useState } from "react";
import { searchAll, type SearchResult } from "../api/phase7";

export function CommandSearch({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setActive(0);
      queueMicrotask(() => input.current?.focus());
    }
  }, [open]);
  useEffect(() => {
    if (!open || !query.trim()) { setResults([]); return; }
    const timer = window.setTimeout(() => {
      void searchAll(query).then(setResults).catch(() => setResults([]));
    }, 150);
    return () => window.clearTimeout(timer);
  }, [open, query]);
  if (!open) return null;
  const choose = (result: SearchResult) => {
    window.dispatchEvent(new CustomEvent("seleric:search-result", { detail: result }));
    onClose();
  };
  return <div className="modal-backdrop" onMouseDown={(event) => {
    if (event.currentTarget === event.target) onClose();
  }}>
    <section className="command-search" role="dialog" aria-modal="true" aria-labelledby="search-title">
      <h2 id="search-title" className="sr-only">Search conversations</h2>
      <input ref={input} value={query} onChange={(event) => setQuery(event.target.value)}
        placeholder="Search threads, messages, memories, artifacts, runs…"
        aria-controls="search-results" aria-activedescendant={results[active] ? `search-${active}` : undefined}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
          if (event.key === "ArrowDown") { event.preventDefault(); setActive((v) => Math.min(v + 1, results.length - 1)); }
          if (event.key === "ArrowUp") { event.preventDefault(); setActive((v) => Math.max(v - 1, 0)); }
          if (event.key === "Enter" && results[active]) choose(results[active]);
        }} />
      <ul id="search-results" role="listbox" aria-label="Search results">
        {results.map((result, index) => <li key={`${result.kind}:${result.id}`}>
          <button id={`search-${index}`} role="option" aria-selected={active === index}
            className={active === index ? "active" : ""} onMouseEnter={() => setActive(index)}
            onClick={() => choose(result)}>
            <small>{result.kind}</small><strong>{result.title}</strong>
            <span>{result.snippet.slice(0, 180)}</span>
          </button>
        </li>)}
      </ul>
      {!results.length && query && <p className="empty-note" role="status">No scoped results</p>}
    </section>
  </div>;
}
