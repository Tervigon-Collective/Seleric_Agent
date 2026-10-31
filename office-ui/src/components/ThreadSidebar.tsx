import { useMemo } from "react";
import type { Thread } from "../api/contracts";
import { useConversationStore } from "../stores/conversation";
import { useShellStore } from "../stores/shell";
import { Button, buttonVariants } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "cn";
import { ArchiveIcon, PencilIcon } from "./icons";

function groupLabel(date: Date, now: Date): string {
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diffDays = Math.round((day(now) - day(date)) / 86_400_000);
  if (diffDays <= 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return "Previous 7 days";
  if (diffDays < 30) return "Previous 30 days";
  return "Older";
}

/** Development/evaluation runs (e.g. "GOLDEN Q18") live apart from ordinary conversations. */
export function isEvalThread(title: string | null | undefined): boolean {
  if (!title) return false;
  return /^\s*golden[\s\-_]*q\d+/i.test(title) || /eval(uation)?\s*(run|set|suite)/i.test(title);
}

function ThreadRow({ thread, selected, onChoose, onRename, onArchive }: {
  thread: Thread;
  selected: boolean;
  onChoose: (id: string) => void;
  onRename: (thread: Thread) => void;
  onArchive: (id: string) => void;
}) {
  const title = thread.title || "Untitled conversation";
  return (
    <div data-slot="thread-row" className={cn("group relative flex items-center gap-1 rounded-lg transition-colors duration-150 hover:bg-accent/60", selected && "bg-accent")}>
      <Button
        variant="ghost"
        size="sm"
        className="h-8 min-w-0 flex-1 justify-between gap-2 rounded-lg px-2.5 text-[13.5px] font-normal transition-colors duration-150"
        onClick={() => onChoose(thread.id)}
        aria-current={selected ? "page" : undefined}
        title={title}
      >
        <span className="truncate">{title}</span>
        
      </Button>
      <div className="absolute right-0.5 flex shrink-0 rounded-lg bg-accent opacity-0 transition-opacity duration-150 focus-within:opacity-100 group-hover:opacity-100 max-lg:opacity-100">
        <Button variant="ghost" size="icon-sm" aria-label={`Rename ${thread.title || "conversation"}`} title="Rename" onClick={() => onRename(thread)}><PencilIcon /></Button>
        <Button variant="ghost" size="icon-sm" aria-label={`Archive ${thread.title || "conversation"}`} title="Archive" onClick={() => onArchive(thread.id)}><ArchiveIcon /></Button>
      </div>
    </div>
  );
}

export function ThreadSidebar() {
  const threads = useConversationStore((s) => s.threads);
  const selected = useConversationStore((s) => s.selectedThreadId);
  const search = useConversationStore((s) => s.search);
  const setSearch = useConversationStore((s) => s.setSearch);
    const selectThread = useConversationStore((s) => s.selectThread);
  const renameThread = useConversationStore((s) => s.renameThread);
  const archiveThread = useConversationStore((s) => s.archiveThread);
  const toggleSidebar = useShellStore((s) => s.toggleSidebar);

  const filtered = useMemo(
    () => threads
      .filter((thread) => (thread.title ?? "Untitled").toLowerCase().includes(search.toLowerCase()))
      .slice()
      .sort((a, b) => +new Date(b.updated_at) - +new Date(a.updated_at)),
    [threads, search],
  );

  // Evaluation runs never mix into the date-grouped user history.
  const userThreads = useMemo(() => filtered.filter((thread) => !isEvalThread(thread.title)), [filtered]);
  const evalThreads = useMemo(() => filtered.filter((thread) => isEvalThread(thread.title)), [filtered]);

  const groups = useMemo(() => {
    const now = new Date();
    const order = ["Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older"];
    const buckets = new Map<string, Thread[]>();
    for (const thread of userThreads) {
      const label = groupLabel(new Date(thread.updated_at), now);
      if (!buckets.has(label)) buckets.set(label, []);
      buckets.get(label)!.push(thread);
    }
    return order.filter((label) => buckets.has(label)).map((label) => ({ label, items: buckets.get(label)! }));
  }, [userThreads]);

  const choose = (id: string) => {
    void selectThread(id);
    if (window.matchMedia?.("(max-width: 1023px)").matches) toggleSidebar();
  };

  const handleRename = (thread: Thread) => {
    const title = window.prompt("Rename conversation", thread.title || "");
    if (title?.trim()) void renameThread(thread.id, title);
  };

  const handleArchive = (id: string) => {
    void archiveThread(id);
  };

  return (
    <div data-slot="thread-sidebar" className="flex min-h-0 flex-1 flex-col gap-2">
      <Input type="search" className="h-8 border-transparent bg-background/60 text-[13.5px] shadow-none transition-colors focus-visible:bg-background" aria-label="Search conversations" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search conversations" />
      <ScrollArea className="min-h-0 flex-1">
        <nav aria-label="Conversation list" className="flex flex-col gap-1">
          {groups.map((group) => (
            <div key={group.label} className="flex flex-col gap-0.5">
              <div className="px-2.5 pt-3 pb-1 text-xs font-medium text-muted-foreground" aria-hidden="true">{group.label}</div>
              {group.items.map((thread) => (
                <ThreadRow
                  key={thread.id}
                  thread={thread}
                  selected={selected === thread.id}
                  onChoose={choose}
                  onRename={handleRename}
                  onArchive={handleArchive}
                />
              ))}
            </div>
          ))}
          {!userThreads.length && !evalThreads.length && <p className="px-2 text-sm text-muted-foreground">{search ? "No matching conversations." : "No conversations yet. Start a new analysis."}</p>}
          {evalThreads.length > 0 && (
            <Collapsible>
              <CollapsibleTrigger className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "w-full justify-start text-muted-foreground")}>
                Evaluation runs ({evalThreads.length})
              </CollapsibleTrigger>
              <CollapsibleContent className="flex flex-col gap-0.5">
                {evalThreads.map((thread) => (
                  <ThreadRow
                    key={thread.id}
                    thread={thread}
                    selected={selected === thread.id}
                    onChoose={choose}
                    onRename={handleRename}
                    onArchive={handleArchive}
                  />
                ))}
              </CollapsibleContent>
            </Collapsible>
          )}
        </nav>
      </ScrollArea>
    </div>
  );
}
