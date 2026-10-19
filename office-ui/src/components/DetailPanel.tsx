import { useEffect, useMemo, useRef } from "react";
import type { SwarmUIEvent } from "../types";
import { readRoute } from "../routing";
import { isTraceableMissionId } from "../store";
import { useConversationStore } from "../stores/conversation";
import { useMissionRuntimeStore } from "../stores/missionRuntime";
import { type DetailTab, useShellStore } from "../stores/shell";
import { XIcon } from "./icons";
import { CausalGraphPanel } from "./CausalGraphPanel";
import { LoopDebugConsole } from "./LoopDebugConsole";
import type { ChartSpec } from "./charts/ChartRenderer";

/** Prefer a real MS* id from store, live timeline, or URL (Debug must survive refresh). */
function resolveMissionId(
  storeId: string | null,
  timeline: SwarmUIEvent[],
): string | null {
  if (isTraceableMissionId(storeId) || storeId === "pending") return storeId;
  const fromTimeline = [...timeline]
    .reverse()
    .map((event) => event.missionId)
    .find((id) => isTraceableMissionId(id));
  if (fromTimeline) return fromTimeline;
  const fromUrl = readRoute().missionId;
  if (isTraceableMissionId(fromUrl)) return fromUrl;
  return storeId;
}

export type ActivityPhaseEvent = { key: string; summary: string; title: string };
export type ActivityPhase = { key: string; agent: string; headline: string; count: number; events: ActivityPhaseEvent[] };

/**
 * Collapse a raw event stream into per-agent phases (newest first). Consecutive
 * events from one agent form a phase; consecutive duplicate summaries merge.
 * Kept exported for activity/run-status helpers and tests.
 */
export function groupActivityPhases(events: SwarmUIEvent[], route: string | null): ActivityPhase[] {
  const rows = events.slice(-60).reverse();
  const fallback = route === "v3" ? "Seleric" : "Swarm";
  const out: ActivityPhase[] = [];
  for (const event of rows) {
    const eventType = typeof event.eventType === "string" && event.eventType ? event.eventType : "unknown_event";
    const summary = (typeof event.summary === "string" && event.summary) || eventType.replaceAll("_", " ");
    const agent = (typeof event.agentId === "string" ? event.agentId.replace(/_agent$/, "") : "") || fallback;
    const title = `${eventType} · #${Number.isFinite(Number(event.seq)) ? event.seq : "?"}`;
    const last = out[out.length - 1];
    if (last && last.agent === agent) {
      last.count += 1;
      const prev = last.events[last.events.length - 1];
      if (!prev || prev.summary !== summary) {
        last.events.push({ key: event.eventId, summary, title });
      }
    } else {
      out.push({ key: event.eventId, agent, headline: summary, count: 1, events: [{ key: event.eventId, summary, title }] });
    }
  }
  return out;
}

const TABS: DetailTab[] = ["Debug", "Causal", "Memory"];

export function DetailPanel() {
  const tab = useShellStore((s) => s.detailTab);
  const setTab = useShellStore((s) => s.setDetailTab);
  const toggleDetails = useShellStore((s) => s.toggleDetails);
  const timeline = useMissionRuntimeStore((s) => s.timeline);
  const storeMissionId = useMissionRuntimeStore((s) => s.missionId);
  const setMissionId = useMissionRuntimeStore((s) => s.setMissionId);
  const loopDebug = useMissionRuntimeStore((s) => s.loopDebug);
  const causalGraphs = useMissionRuntimeStore((s) => s.causalGraphs);
  const submitting = useConversationStore((s) => s.submitting);
  const threadId = useConversationStore((s) => s.selectedThreadId);
  const currentRunId = useConversationStore((s) => s.currentRunId);
  const messagesByThread = useConversationStore((s) => s.messages);
  const threadMessages = threadId ? (messagesByThread[threadId] ?? []) : [];
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
  // No selected thread → no mission (ignore leftover store/URL from a prior chat).
  const visibleTimeline = threadId ? timeline : [];
  const missionId = useMemo(
    () => (threadId ? resolveMissionId(storeMissionId, visibleTimeline) : null),
    [threadId, storeMissionId, visibleTimeline],
  );
  useEffect(() => {
    if (threadId && isTraceableMissionId(missionId) && missionId !== storeMissionId) {
      setMissionId(missionId);
    }
  }, [threadId, missionId, storeMissionId, setMissionId]);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    if (tab === "Memory") void loadMemories();
  }, [tab, threadId, loadMemories]);

  return (
    <aside className="detail-panel" aria-label="Conversation details">
      <button className="icon-btn detail-close" aria-label="Close conversation details" title="Close inspector" onClick={toggleDetails}>
        <XIcon size={15} />
      </button>
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
        {tab === "Causal" && (
          <section className="causal-panel-section">
            <h2>Causal graph</h2>
            <CausalGraphPanel
              missionId={missionId}
              graphs={(causalGraphs as ChartSpec[] | null) ?? null}
              live={submitting}
            />
          </section>
        )}
        {tab === "Debug" && (
          <section className="loop-debug-section">
            <h2>Debug</h2>
            <LoopDebugConsole
              missionId={missionId}
              timeline={visibleTimeline}
              stats={loopDebug?.stats}
              grounding={loopDebug?.grounding}
              steps={loopDebug?.steps}
              live={submitting}
              threadId={threadId}
              messages={threadMessages}
              currentRunId={currentRunId}
            />
          </section>
        )}
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
      </div>
    </aside>
  );
}

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
  return (
    <article className="memory-row">
      <strong>{displayMemory(memory.content)}</strong>
      <small>{memory.type} · {memory.pinned ? "pinned" : "unpinned"} · conf {memory.confidence.toFixed(2)}</small>
      <small>Evidence: {memory.source_evidence_ids.join(", ") || "none"}</small>
      {!readonly && (
        <div className="memory-actions">
          <button type="button" onClick={onEdit}>Edit</button>
          <button type="button" onClick={onPin}>{memory.pinned ? "Unpin" : "Pin"}</button>
          <button type="button" onClick={onMove}>Move</button>
          <button type="button" onClick={onArchive}>Archive</button>
          <button type="button" onClick={onDelete}>Delete</button>
        </div>
      )}
    </article>
  );
}
