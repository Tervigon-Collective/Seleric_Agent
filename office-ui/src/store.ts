/**
 * Central office store (Zustand). Four separable slices per the brief:
 * mission state, agent state, office visual state, UI selection state.
 *
 * Idempotency is enforced here: repeated or out-of-order events never
 * double-apply, never duplicate a timeline row, never re-fire an animation.
 */

import { create } from "zustand";
import type {
  BoardStep,
  Handoff,
  OfficeAgent,
  OfficeSnapshot,
  ParallelTask,
  SwarmUIEvent,
} from "./types";
import { applyEventToAgents } from "./office/stateMachine";

export type ConnState = "idle" | "connecting" | "live" | "reconnecting" | "closed" | "error";

interface MissionSlice {
  missionId: string | null;
  query: string;
  status: string;
  stage: string;
  route: string | null;
  missionLead: string | null;
  leadAgentId: string | null;
  leadershipEpoch: number;
  startedAt: string | null;
  board: BoardStep[];
  handoffs: Handoff[];
  parallelTasks: Record<string, ParallelTask[]>;
  artifacts: Record<string, number>;
  unresolvedQuestions: string[];
  limitations: string[];
  finalResponse: string | null;
  traceUrl: string | null;
  traceRequestId: string | null;
}

interface OfficeState extends MissionSlice {
  // agent slice
  agents: Record<string, OfficeAgent>;
  agentOrder: string[];

  // event slice
  timeline: SwarmUIEvent[];
  lastSeq: number;
  seenSeq: Set<number>;
  lastEvent: SwarmUIEvent | null;

  // connection
  conn: ConnState;
  providerMode: "demo" | "seleric";

  // ui / visual slice
  selectedAgentId: string | null;
  hoveredAgentId: string | null;
  followMode: "off" | "lead" | "activity";
  timelineOpen: boolean;
  debugMode: boolean;
  reducedMotion: boolean;

  // actions
  setConn: (c: ConnState) => void;
  setProviderMode: (m: "demo" | "seleric") => void;
  hydrate: (snap: OfficeSnapshot) => void;
  ingestEvent: (ev: SwarmUIEvent) => void;
  select: (id: string | null) => void;
  hover: (id: string | null) => void;
  setFollow: (m: "off" | "lead" | "activity") => void;
  toggleTimeline: () => void;
  toggleDebug: () => void;
  reset: () => void;
}

const MISSION_DEFAULT: MissionSlice = {
  missionId: null,
  query: "",
  status: "idle",
  stage: "intake",
  route: null,
  missionLead: null,
  leadAgentId: null,
  leadershipEpoch: 0,
  startedAt: null,
  board: [],
  handoffs: [],
  parallelTasks: {},
  artifacts: {},
  unresolvedQuestions: [],
  limitations: [],
  finalResponse: null,
  traceUrl: null,
  traceRequestId: null,
};

const prefersReduced =
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export const useOffice = create<OfficeState>((set, get) => ({
  ...MISSION_DEFAULT,
  agents: {},
  agentOrder: [],
  timeline: [],
  lastSeq: 0,
  seenSeq: new Set<number>(),
  lastEvent: null,
  conn: "idle",
  providerMode: "demo",
  selectedAgentId: null,
  hoveredAgentId: null,
  followMode: "lead",
  timelineOpen: false,
  debugMode: false,
  reducedMotion: prefersReduced,

  setConn: (conn) => set({ conn }),
  setProviderMode: (providerMode) => set({ providerMode }),

  /**
   * Reconcile from a snapshot. Snapshot agent state is authoritative when
   * present; the event-folded timeline is *merged* (union by seq) so a
   * re-emitted snapshot never drops rows or breaks dedupe. An empty `agents`
   * array means "mission-level patch only" (used by the demo provider).
   */
  hydrate: (snap) => {
    const s = get();
    const hasAgents = Array.isArray(snap.agents) && snap.agents.length > 0;

    let agents = s.agents;
    let agentOrder = s.agentOrder;
    if (hasAgents) {
      agents = {};
      for (const a of snap.agents) agents[a.agentId] = { ...a, waitingOn: a.waitingOn ?? [] };
      agentOrder = snap.agents.map((a) => a.agentId);
    }

    const bySeq = new Map<number, SwarmUIEvent>();
    for (const e of s.timeline) bySeq.set(e.seq, e);
    for (const e of snap.timeline ?? []) if (!bySeq.has(e.seq)) bySeq.set(e.seq, e);
    const timeline = [...bySeq.values()].sort((a, b) => a.seq - b.seq);
    const seenSeq = new Set(timeline.map((e) => e.seq));

    set({
      missionId: snap.missionId ?? s.missionId,
      query: snap.query || s.query,
      status: snap.status ?? s.status,
      stage: snap.stage ?? s.stage,
      route: snap.route ?? s.route,
      missionLead: snap.missionLead ?? s.missionLead,
      leadAgentId: snap.leadAgentId ?? s.leadAgentId,
      leadershipEpoch: snap.leadershipEpoch ?? s.leadershipEpoch,
      startedAt: snap.startedAt ?? s.startedAt,
      board: snap.board?.steps ?? s.board,
      handoffs: snap.handoffs ?? s.handoffs,
      parallelTasks: snap.parallelTasks ?? s.parallelTasks,
      artifacts: snap.artifacts ?? s.artifacts,
      unresolvedQuestions: snap.unresolvedQuestions ?? s.unresolvedQuestions,
      limitations: snap.limitations ?? s.limitations,
      finalResponse: snap.finalResponse ?? s.finalResponse,
      traceUrl: snap.traceUrl ?? s.traceUrl,
      traceRequestId: snap.trace?.requestId ?? s.traceRequestId,
      agents,
      agentOrder,
      timeline,
      lastSeq: Math.max(s.lastSeq, snap.lastSeq ?? 0, ...timeline.map((e) => e.seq), 0),
      seenSeq,
      lastEvent: timeline.slice(-1)[0] ?? s.lastEvent,
    });
  },

  ingestEvent: (ev) => {
    const s = get();
    if (s.seenSeq.has(ev.seq)) return; // idempotent: dedupe by seq
    const seenSeq = new Set(s.seenSeq);
    seenSeq.add(ev.seq);

    const timeline = [...s.timeline, ev].sort((a, b) => a.seq - b.seq);
    const agents = applyEventToAgents(s.agents, ev);

    // leadership ring follows the freshest transfer
    let leadAgentId = s.leadAgentId;
    let missionLead = s.missionLead;
    if (ev.eventType === "leadership_transferred" && ev.agentId) {
      leadAgentId = ev.agentId;
      missionLead = ev.agentId.replace(/_agent$/, "");
    }

    set({
      timeline,
      agents,
      seenSeq,
      leadAgentId,
      missionLead,
      lastSeq: Math.max(s.lastSeq, ev.seq),
      lastEvent: ev,
      status:
        ev.eventType === "mission_completed"
          ? "completed"
          : ev.eventType === "mission_blocked"
            ? "blocked"
            : s.status,
    });
  },

  select: (selectedAgentId) => set({ selectedAgentId }),
  hover: (hoveredAgentId) => set({ hoveredAgentId }),
  setFollow: (followMode) => set({ followMode }),
  toggleTimeline: () => set((s) => ({ timelineOpen: !s.timelineOpen })),
  toggleDebug: () => set((s) => ({ debugMode: !s.debugMode })),
  reset: () =>
    set({
      ...MISSION_DEFAULT,
      agents: {},
      agentOrder: [],
      timeline: [],
      lastSeq: 0,
      seenSeq: new Set<number>(),
      lastEvent: null,
      selectedAgentId: null,
      hoveredAgentId: null,
    }),
}));
