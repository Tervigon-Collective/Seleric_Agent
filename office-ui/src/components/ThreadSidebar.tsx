import { useMemo } from "react";
import { useConversationStore } from "../stores/conversation";

export function ThreadSidebar() {
  const threads = useConversationStore((s) => s.threads);
  const selected = useConversationStore((s) => s.selectedThreadId);
  const search = useConversationStore((s) => s.search);
  const setSearch = useConversationStore((s) => s.setSearch);
  const createThread = useConversationStore((s) => s.createThread);
  const selectThread = useConversationStore((s) => s.selectThread);
  const archiveThread = useConversationStore((s) => s.archiveThread);
  const filtered = useMemo(
    () => threads.filter((thread) => (thread.title ?? "Untitled").toLowerCase().includes(search.toLowerCase())),
    [threads, search],
  );

  return (
    <aside className="thread-sidebar" aria-label="Conversation threads">
      <div className="sidebar-head">
        <strong>Threads</strong>
        <button className="primary-btn" onClick={() => void createThread()}>+ New</button>
      </div>
      <label className="search-field">
        <span className="sr-only">Search threads</span>
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search conversations" type="search" />
      </label>
      <nav aria-label="Thread list">
        {filtered.map((thread) => (
          <div className={`thread-row ${selected === thread.id ? "selected" : ""}`} key={thread.id}>
            <button className="thread-select" onClick={() => void selectThread(thread.id)} aria-current={selected === thread.id ? "page" : undefined}>
              <span>{thread.title || "Untitled conversation"}</span>
              <small>{new Date(thread.updated_at).toLocaleDateString()}</small>
            </button>
            <button className="icon-btn archive" aria-label={`Archive ${thread.title || "conversation"}`} onClick={() => void archiveThread(thread.id)}>×</button>
          </div>
        ))}
        {!filtered.length && <p className="empty-note">No matching threads.</p>}
      </nav>
    </aside>
  );
}
