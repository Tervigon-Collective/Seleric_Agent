/**
 * Shared vocabulary for the Seleric AI Office.
 *
 * `AgentOfficeState` and `SwarmUIEvent` mirror the backend gateway
 * (`src/seleric_swarm/api/office/normalize.py`). The office is a spatial
 * projection of real mission state — every visual state here is derived from a
 * backend event, never invented by the renderer.
 */

export type AgentOfficeState =
  | "offline"
  | "idle"
  | "assigned"
  | "planning"
  | "thinking"
  | "working"
  | "retrieving_evidence"
  | "tool_running"
  | "waiting_for_agent"
  | "waiting_for_evidence"
  | "collaborating"
  | "reviewing"
  | "blocked"
  | "needs_attention"
  | "handoff"
  | "completed"
  | "failed";

export type AgentRole = "coordinator" | "specialist" | "domain";

export interface OfficeProgress {
  current: number;
  total: number;
  label?: string;
}

/** Runtime view-model for one persistent character. */
export interface OfficeAgent {
  agentId: string;
  name: string;
  role: AgentRole;
  domain?: string;

  status: AgentOfficeState;
  missionId?: string;
  taskId?: string;
  task?: string;
  subquestion?: string;
  currentAction?: string;
  currentTool?: string;
  currentArtifact?: string;
  currentHypothesis?: string;
  progress?: OfficeProgress;

  missionLead: boolean;
  waitingOn: string[];
  lastEventAt?: string;
  error?: string;
}

export type SwarmUIEventType =
  | "mission_created"
  | "mission_started"
  | "mission_completed"
  | "mission_partial"
  | "mission_blocked"
  | "decomposition_created"
  | "decomposition_refined"
  | "task_created"
  | "task_assigned"
  | "task_started"
  | "task_completed"
  | "agent_started"
  | "leadership_transferred"
  | "leadership_transfer_rejected"
  | "artifact_created"
  | "skeptic_review_started"
  | "skeptic_pass"
  | "skeptic_revise"
  | "skeptic_reject"
  | "remediation_created"
  | "error"
  | (string & {});

export interface SwarmUIEvent {
  eventId: string;
  seq: number;
  timestamp: string;
  missionId: string;
  taskId?: string | null;
  agentId?: string | null;
  eventType: SwarmUIEventType;
  status?: AgentOfficeState | null;
  summary?: string;
  artifactRefs?: string[];
  metadata?: Record<string, unknown>;
}

export interface BoardStep {
  id: string;
  label: string;
  state: "pending" | "active" | "done";
}

export interface Handoff {
  from?: string | null;
  to?: string | null;
  reason?: string | null;
  epoch?: number | null;
}

export interface ParallelTask {
  taskId?: string | null;
  label: string;
  status: string;
}

export interface OfficeSnapshot {
  missionId: string;
  query: string;
  status: string;
  route?: string | null;
  stage: string;
  missionLead?: string | null;
  leadAgentId?: string | null;
  initialLead?: string | null;
  leadershipEpoch: number;
  startedAt?: string | null;
  lastEventAt?: string | null;
  lastSeq: number;
  agents: OfficeAgent[];
  board: { steps: BoardStep[] };
  handoffs: Handoff[];
  parallelTasks?: Record<string, ParallelTask[]>;
  artifacts: Record<string, number>;
  unresolvedQuestions: string[];
  limitations: string[];
  finalResponse?: string | null;
  trace?: { requestId?: string | null; sessionId?: string | null };
  traceUrl?: string | null;
  timeline: SwarmUIEvent[];
}

export interface MissionRef {
  missionId: string;
  query: string;
  status: string;
  route?: string | null;
  missionLead?: string | null;
  lastSeq: number;
}
