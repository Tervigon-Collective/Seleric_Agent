import { useEffect, useMemo } from "react";
import { XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { SwarmUIEvent } from "../types";
import { isTraceableMissionId } from "../store";
import { useConversationStore } from "../stores/conversation";
import { useMissionRuntimeStore } from "../stores/missionRuntime";
import { type DetailTab, useShellStore } from "../stores/shell";
import { CausalGraphPanel } from "./CausalGraphPanel";
import { LoopDebugConsole } from "./LoopDebugConsole";
import type { ChartSpec } from "./charts/ChartRenderer";

/** Prefer a real MS* id from store or this thread's timeline. Never the URL —
 * stale ?mission= from a prior chat would otherwise paint Debug onto empty threads. */
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
  useEffect(() => {
    if (tab === "Memory") void loadMemories();
  }, [tab, threadId, loadMemories]);

  return (
    <aside aria-label="Conversation details" className="flex h-full min-h-0 w-full flex-col gap-3 overflow-y-auto border-l bg-sidebar p-3 lg:w-[340px] lg:shrink-0">
      <Tabs value={tab} onValueChange={(value) => setTab(value as DetailTab)} className="min-h-0 flex-1">
        <div className="flex items-center gap-2">
          <TabsList aria-label="Details" className="flex-1">
            {TABS.map((item) => <TabsTrigger key={item} value={item}>{item}</TabsTrigger>)}
          </TabsList>
          <Button variant="ghost" size="icon" aria-label="Close conversation details" title="Close inspector" onClick={toggleDetails}>
            <XIcon />
          </Button>
        </div>
        <TabsContent value="Causal" className="flex flex-col gap-2">
          <h2 className="text-sm font-medium">Causal graph</h2>
          <CausalGraphPanel
            missionId={missionId}
            graphs={(causalGraphs as ChartSpec[] | null) ?? null}
            live={submitting}
          />
        </TabsContent>
        <TabsContent value="Debug" className="flex flex-col gap-2">
          <h2 className="text-sm font-medium">Debug</h2>
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
        </TabsContent>
        <TabsContent value="Memory" className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-sm font-medium">Memory</h2>
            <Button variant="outline" size="sm" onClick={() => {
              const content = window.prompt("Memory to save");
              if (content?.trim()) void addMemory(content.trim());
            }}>Add</Button>
          </div>
          <div className="flex items-center gap-2">
            <Checkbox id="memory-optout" checked={memoryOptedOut} onCheckedChange={(checked) => void setMemoryOptOut(checked === true)} />
            <Label htmlFor="memory-optout">Do not save or use my memories</Label>
          </div>
          {!!usedMemories.length && <>
            <h3 className="text-xs font-medium text-muted-foreground">Used by current run</h3>
            {usedMemories.map((memory) => <MemoryRow key={`used-${memory.id}`} memory={memory} readonly />)}
          </>}
          <h3 className="text-xs font-medium text-muted-foreground">Saved memories</h3>
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
        </TabsContent>
      </Tabs>
    </aside>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-sm text-muted-foreground">{text}</p>;
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
    <Card size="sm">
      <CardContent className="flex flex-col gap-1">
        <strong className="text-sm font-medium">{displayMemory(memory.content)}</strong>
        <small className="text-xs text-muted-foreground">{memory.type} · {memory.pinned ? "pinned" : "unpinned"} · conf {memory.confidence.toFixed(2)}</small>
        <small className="text-xs text-muted-foreground">Evidence: {memory.source_evidence_ids.join(", ") || "none"}</small>
      </CardContent>
      {!readonly && (
        <CardFooter className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" onClick={onEdit}>Edit</Button>
          <Button type="button" variant="outline" size="sm" onClick={onPin}>{memory.pinned ? "Unpin" : "Pin"}</Button>
          <Button type="button" variant="outline" size="sm" onClick={onMove}>Move</Button>
          <Button type="button" variant="outline" size="sm" onClick={onArchive}>Archive</Button>
          <Button type="button" variant="outline" size="sm" onClick={onDelete}>Delete</Button>
        </CardFooter>
      )}
    </Card>
  );
}
