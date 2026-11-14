import { useEffect, useState } from "react";
import { Command, CommandDialog, CommandEmpty, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { searchAll, type SearchResult } from "../api/phase7";

export function CommandSearch({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);

  useEffect(() => {
    if (!open || !query.trim()) { setResults([]); return; }
    const timer = window.setTimeout(() => {
      void searchAll(query).then(setResults).catch(() => setResults([]));
    }, 150);
    return () => window.clearTimeout(timer);
  }, [open, query]);

  const choose = (result: SearchResult) => {
    window.dispatchEvent(new CustomEvent("seleric:search-result", { detail: result }));
    onClose();
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={(next) => { if (!next) onClose(); }}
      title="Search conversations"
      description="Search threads, messages, memories, artifacts and runs."
    >
      {/* Results come from the server, so cmdk's own filtering is off. */}
      <Command shouldFilter={false}>
        <CommandInput value={query} onValueChange={setQuery} placeholder="Search threads, messages, memories, artifacts, runs…" />
        <CommandList>
          {query.trim() && !results.length && <CommandEmpty>No scoped results</CommandEmpty>}
          {results.map((result) => (
            <CommandItem key={`${result.kind}:${result.id}`} value={`${result.kind}:${result.id}`} onSelect={() => choose(result)} className="flex-col items-start gap-0.5">
              <span className="text-xs text-muted-foreground">{result.kind}</span>
              <strong className="font-medium">{result.title}</strong>
              <span className="text-xs text-muted-foreground">{result.snippet.slice(0, 180)}</span>
            </CommandItem>
          ))}
        </CommandList>
      </Command>
    </CommandDialog>
  );
}
