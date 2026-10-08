import { useMemo } from "react";
import type { Thread } from "../api/contracts";
import { useConversationStore } from "../stores/conversation";
import { useShellStore } from "../stores/shell";
import { ArchiveIcon, PencilIcon, PlusIcon, XIcon } from "./icons";

function groupLabel(date: Date, now: Date): string {
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const diffDays = Math.round((day(now) - day(date)) / 86_400_000);
  if (diffDays <= 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7) return "Previous 7 days";
  if (diffDays < 30) return "Previous 30 days";
  return "Older";
}

function shortDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString(undefined, sameYear
    ? { month: "short", day: "numeric" }
    : { month: "short", day: "numeric", year: "numeric" });
}

export function ThreadSidebar() {
  const threads = useConversationStore((s) => s.threads);
  const selected = useConversationStore((s) => s.selectedThreadId);
  const search = useConversationStore((s) => s.search);
  const setSearch = useConversationStore((s) => s.setSearch);
  const createThread = useConversationStore((s) => s.createThread);
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

  const groups = useMemo(() => {
    const now = new Date();
    const order = ["Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older"];
    const buckets = new Map<string, Thread[]>();
    for (const thread of filtered) {
      const label = groupLabel(new Date(thread.updated_at), now);
      if (!buckets.has(label)) buckets.set(label, []);
      buckets.get(label)!.push(thread);
    }
    return order.filter((label) => buckets.has(label)).map((label) => ({ label, items: buckets.get(label)! }));
  }, [filtered]);

  const choose = (id: string) => {
    void selectThread(id);
    if (window.matchMedia?.("(max-width: 1023px)").matches) toggleSidebar();
  };

  return (
    <aside className="thread-sidebar" aria-label="Conversations">
      <div className="sidebar-head">
        <strong>Conversations</strong>
        <span style={{ display: "flex", gap: 4 }}>
          <button className="icon-btn mobile-panel-close" aria-label="Close conversations" onClick={toggleSidebar}><XIcon size={15} /></button>
          <button className="primary-btn" onClick={() => void createThread()}><PlusIcon size={14} /> New</button>
        </span>
      </div>
      <label className="search-field">
        <span className="sr-only">Search conversations</span>
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search conversations" type="search" />
      </label>
      <nav aria-label="Conversation list">
        {groups.map((group) => (
          <div key={group.label}>
            <div className="thread-group-label" aria-hidden="true">{group.label}</div>
            {group.items.map((thread) => (
              <div className={`thread-row ${selected === thread.id ? "selected" : ""}`} key={thread.id}>
                <button
                  className="thread-select"
                  onClick={() => choose(thread.id)}
                  aria-current={selected === thread.id ? "page" : undefined}
                  title={thread.title || "Untitled conversation"}
                >
                  <span>{thread.title || "Untitled conversation"}</span>
                  <small>{shortDate(thread.updated_at)}</small>
                </button>
                <div className="thread-actions">
                  <button className="icon-btn rename" aria-label={`Rename ${thread.title || "conversation"}`} title="Rename" onClick={() => {
                    const title = window.prompt("Rename conversation", thread.title || "");
                    if (title?.trim()) void renameThread(thread.id, title);
                  }}><PencilIcon size={13} /></button>
                  <button className="icon-btn archive" aria-label={`Archive ${thread.title || "conversation"}`} title="Archive" onClick={() => void archiveThread(thread.id)}><ArchiveIcon size={13} /></button>
                </div>
              </div>
            ))}
          </div>
        ))}
        {!filtered.length && <p className="empty-note">{search ? "No matching conversations." : "No conversations yet. Start a new analysis."}</p>}
      </nav>
      <div className="sidebar-foot">Ask about sales, marketing, products, or operations. Evidence stays with every answer.</div>
    </aside>
  );
}
