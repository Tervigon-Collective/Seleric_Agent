import { useEffect, useRef } from "react";
import type { MessagePart } from "../api/contracts";
import { useConversationStore } from "../stores/conversation";
import { useMissionRuntimeStore } from "../stores/missionRuntime";
import { type DetailTab, useShellStore } from "../stores/shell";

const TABS: DetailTab[] = ["Activity", "Context", "Memory", "Sources", "Artifacts"];

export function DetailPanel() {
  const tab = useShellStore((s) => s.detailTab);
  const setTab = useShellStore((s) => s.setDetailTab);
  const toggleDetails = useShellStore((s) => s.toggleDetails);
  const selectedArtifact = useShellStore((s) => s.selectedArtifact);
  const timeline = useMissionRuntimeStore((s) => s.timeline);
  const artifacts = useMissionRuntimeStore((s) => s.artifacts);
  const query = useMissionRuntimeStore((s) => s.query);
  const route = useMissionRuntimeStore((s) => s.route);
  const stage = useMissionRuntimeStore((s) => s.stage);
  const threadId = useConversationStore((s) => s.selectedThreadId);
  const thread = useConversationStore((s) => s.threads.find((item) => item.id === threadId));
  const messages = useConversationStore((s) => threadId ? s.messages[threadId] ?? [] : []);
  const userTurns = messages.flatMap((message) => {
    if (message.role !== "USER") return [];
    const text = message.parts
      .filter((part) => part.type === "TEXT" && typeof part.content === "string")
      .map((part) => String(part.content).trim())
      .filter(Boolean)
      .join("\n");
    return text ? [text] : [];
  });
  const priorAsks = userTurns.slice(0, -1).slice(-4);
  const memories = useConversationStore((s) => s.memories);
  const usedMemories = useConversationStore((s) => s.usedMemories);
  const memoryOptedOut = useConversationStore((s) => s.memoryOptedOut);
  const loadMemories = useConversationStore((s) => s.loadMemories);
  const addMemory = useConversationStore((s) => s.addMemory);
  const editMemory = useConversationStore((s) => s.editMemory);
  const pinMemory = useConversationStore((s) => s.pinMemory);
  const moveMemory = useConversationStore((s) => s.moveMemory);
  const archiveMemory = useConversationStore((s) => s.archiveMemory);
  const deleteMemory = useConversationStore((s) => s.deleteMemory);
  const setMemoryOptOut = useConversationStore((s) => s.setMemoryOptOut);
  const visibleTimeline = threadId ? timeline : [];
  const sources = messages.flatMap((message) =>
    message.parts.filter((part) => part.type === "SOURCE").map(sourceView),
  );
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    if (tab === "Memory") void loadMemories();
  }, [tab, threadId, loadMemories]);

  return (
    <aside className="detail-panel" aria-label="Conversation details">
      <button className="icon-btn mobile-panel-close detail-close" aria-label="Close conversation details" onClick={toggleDetails}>×</button>
      <div className="detail-tabs" role="tablist" aria-label="Details">
        {TABS.map((item) => (
          <button
            key={item}
            id={`detail-tab-${item.toLowerCase()}`}
            ref={(node) => { tabRefs.current[TABS.indexOf(item)] = node; }}
            role="tab"
            aria-controls={`detail-panel-${item.toLowerCase()}`}
            aria-selected={tab === item}
            tabIndex={tab === item ? 0 : -1}
            className={tab === item ? "active" : ""}
            onClick={() => setTab(item)}
            onKeyDown={(event) => {
              const current = TABS.indexOf(item);
              const next = event.key === "ArrowRight"
                ? (current + 1) % TABS.length
                : event.key === "ArrowLeft"
                  ? (current - 1 + TABS.length) % TABS.length
                  : event.key === "Home" ? 0 : event.key === "End" ? TABS.length - 1 : -1;
              if (next < 0) return;
              event.preventDefault();
              setTab(TABS[next]);
              tabRefs.current[next]?.focus();
            }}
          >{item}</button>
        ))}
      </div>
      <div
        className="detail-content"
        id={`detail-panel-${tab.toLowerCase()}`}
        role="tabpanel"
        aria-labelledby={`detail-tab-${tab.toLowerCase()}`}
      >
        {tab === "Activity" && (
          <section><h2>Run activity</h2>{visibleTimeline.slice(-30).reverse().map((event) => (
            <div className="activity-row" key={event.eventId}><span className="activity-dot" /><div><strong>{event.summary || event.eventType.replaceAll("_", " ")}</strong><small>{event.agentId || "Swarm"} · #{event.seq}</small></div></div>
          ))}{!visibleTimeline.length && <Empty text={threadId ? "Activity appears here while this conversation runs." : "Select or start a conversation to see its activity."} />}</section>
        )}
        {tab === "Context" && <section><h2>Thread context</h2><dl>
          <dt>Title</dt><dd>{thread?.title || "Untitled"}</dd>
          <dt>Question</dt><dd>{userTurns[userTurns.length - 1] || query || "No active mission"}</dd>
          {priorAsks.length > 0 && <>
            <dt>Prior asks</dt>
            <dd><ol className="prior-asks">{priorAsks.map((text, index) => <li key={`${index}-${text}`}>{text}</li>)}</ol></dd>
          </>}
          <dt>Route</dt><dd>{route || "Pending"}</dd>
          <dt>Stage</dt><dd>{stage}</dd>
        </dl></section>}
        {tab === "Memory" && <section className="memory-panel">
          <div className="memory-heading"><h2>Memory</h2>
            <button onClick={() => {
              const content = window.prompt("Memory to save");
              if (content?.trim()) void addMemory(content.trim());
            }}>Add</button>
          </div>
          <label className="memory-optout">
            <input type="checkbox" checked={memoryOptedOut} onChange={(event) => void setMemoryOptOut(event.target.checked)} />
            Do not save or use my memories
          </label>
          {!!usedMemories.length && <><h3>Used by current run</h3>
            {usedMemories.map((memory) => <MemoryRow key={`used-${memory.id}`} memory={memory} readonly />)}
          </>}
          <h3>Saved memories</h3>
          {memories.map((memory) => <MemoryRow key={memory.id} memory={memory}
            onEdit={() => {
              const content = window.prompt("Edit memory", displayMemory(memory.content));
              if (content?.trim()) void editMemory(memory.id, content.trim());
            }}
            onPin={() => void pinMemory(memory.id, !memory.pinned)}
            onMove={() => void moveMemory(memory.id)}
            onArchive={() => void archiveMemory(memory.id)}
            onDelete={() => void deleteMemory(memory.id)}
          />)}
          {!memories.length && <Empty text="No saved memories for this thread." />}
        </section>}
        {tab === "Sources" && <section><h2>Sources</h2>
          {sources.map((source, index) => <article className="source-row" key={`${source.id}-${index}`}>
            <strong>{source.url
              ? <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>
              : source.title}</strong>
            {source.excerpt && <p>{source.excerpt}</p>}
            <small>{source.id}</small>
          </article>)}
          {!sources.length && <Empty text="No sources have been attached to this thread yet." />}
        </section>}
        {tab === "Artifacts" && <section><h2>Artifacts</h2>
          {selectedArtifact && <div className="artifact-detail">
            <h3>{String(selectedArtifact.title ?? selectedArtifact.artifact_id ?? "Artifact")}</h3>
            <dl>
              <dt>Evidence</dt><dd>{safeList(selectedArtifact.evidence_ids ?? selectedArtifact.evidence_refs)}</dd>
              <dt>Calculation</dt><dd>{safeValue(selectedArtifact.calculation_version)}</dd>
              <dt>Query</dt><dd>{safeValue(selectedArtifact.query_version)}</dd>
              <dt>Prompt</dt><dd>{safeValue(selectedArtifact.prompt_version)}</dd>
              <dt>Tool</dt><dd>{safeValue(selectedArtifact.tool_version)}</dd>
              <dt>Model</dt><dd>{safeValue(selectedArtifact.model_version)}</dd>
            </dl>
          </div>}
          {Object.entries(artifacts).map(([type, count]) => <div className="artifact-row" key={type}><span>{type}</span><b>{count}</b></div>)}
          {!selectedArtifact && !Object.keys(artifacts).length && <Empty text="Generated analyses, charts, and files appear here." />}
        </section>}
      </div>
    </aside>
  );
}

const sourceView = (part: MessagePart) => {
  const content = part.content && typeof part.content === "object" && !Array.isArray(part.content)
    ? part.content as Record<string, unknown>
    : {};
  const rawUrl = typeof content.url === "string" ? content.url : "";
  return {
    id: String(content.evidence_id ?? content.id ?? part.metadata?.evidence_id ?? "Source"),
    title: String(content.title ?? content.name ?? (rawUrl || "Source")),
    url: /^https?:\/\//.test(rawUrl) ? rawUrl : null,
    excerpt: typeof content.excerpt === "string"
      ? content.excerpt
      : typeof content.snippet === "string" ? content.snippet : null,
  };
};

const safeValue = (value: unknown) =>
  typeof value === "string" || typeof value === "number" ? String(value) : "Not recorded";
const safeList = (value: unknown) =>
  Array.isArray(value) ? value.filter((item) => typeof item === "string").join(", ") || "None" : "None";

function Empty({ text }: { text: string }) {
  return <p className="empty-note">{text}</p>;
}

type MemoryView = {
  id: string; content: string | Record<string, unknown>; type: string; status: string;
  pinned: boolean; confidence: number; source_message_ids: string[];
  source_evidence_ids: string[]; provenance: Record<string, unknown>;
};

const displayMemory = (content: MemoryView["content"]) =>
  typeof content === "string" ? content : JSON.stringify(content);

function MemoryRow({ memory, readonly = false, onEdit, onPin, onMove, onArchive, onDelete }: {
  memory: MemoryView; readonly?: boolean; onEdit?: () => void; onPin?: () => void;
  onMove?: () => void; onArchive?: () => void; onDelete?: () => void;
}) {
  return <article className="memory-row">
    <div><strong>{memory.pinned ? "📌 " : ""}{displayMemory(memory.content)}</strong>
      <small>{memory.type} · {memory.status} · {Math.round(memory.confidence * 100)}% confidence</small>
      <details><summary>Provenance</summary>
        <small>Messages: {memory.source_message_ids.join(", ") || "none"}</small>
        <small>Evidence: {memory.source_evidence_ids.join(", ") || "none"}</small>
        <pre>{JSON.stringify(memory.provenance, null, 2)}</pre>
      </details>
    </div>
    {!readonly && <div className="memory-actions">
      <button onClick={onEdit}>Edit</button><button onClick={onPin}>{memory.pinned ? "Unpin" : "Pin"}</button>
      <button onClick={onMove}>Move</button>
      <button onClick={onArchive}>Archive</button><button onClick={onDelete}>Delete</button>
    </div>}
  </article>;
}
