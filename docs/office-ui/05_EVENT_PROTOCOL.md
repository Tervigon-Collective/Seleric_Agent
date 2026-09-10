# Event protocol

## `SwarmUIEvent`

Emitted by the gateway (`normalize.py::normalize_events`), consumed by the store.

```ts
interface SwarmUIEvent {
  eventId: string;          // `${missionId}:${seq}` — stable, used for dedupe display
  seq: number;              // monotonic per mission (from Blackboard.events)
  timestamp: string;        // ISO 8601 (from the source event `ts`)
  missionId: string;
  taskId?: string | null;
  agentId?: string | null;  // the character this event acts on, if any
  eventType: SwarmUIEventType;
  status?: AgentOfficeState | null;  // state hint for agentId
  summary?: string;         // speech-bubble / timeline ready, structured — never raw LLM text
  artifactRefs?: string[];
  metadata?: Record<string, unknown>;  // source fields minus the envelope
}
```

### `eventType` values

`mission_created · mission_started · mission_completed · mission_partial · mission_blocked ·
decomposition_created · decomposition_refined · task_created · task_assigned · task_started ·
task_completed · agent_started · leadership_transferred · leadership_transfer_rejected ·
artifact_created · skeptic_review_started · skeptic_pass · skeptic_revise · skeptic_reject ·
remediation_created · error`

These are a stable re-labelling of Seleric's own canonical kinds
(`coordinator/observability/events.py`) — see the table in `04_AGENT_STATES.md`. Unknown
kinds pass through unchanged rather than being dropped.

## `OfficeSnapshot`

Full derived state for hydration (`normalize.py::build_office_snapshot`):

```ts
interface OfficeSnapshot {
  missionId; query; status; route; stage;              // mission header
  missionLead; leadAgentId; initialLead; leadershipEpoch;
  startedAt; lastEventAt; lastSeq;
  agents: OfficeAgent[];                               // all 14 characters, view-models
  board: { steps: { id; label; state: pending|active|done }[] };
  handoffs: { from; to; reason; epoch }[];
  artifacts: Record<bucket, count>;
  unresolvedQuestions: string[];
  limitations: string[];
  finalResponse: string | null;
  timeline: SwarmUIEvent[];                            // full normalized log
}
```

`stage` ∈ `intake · decomposing · planning · investigating · specialists · review ·
complete · blocked · failed`.

## HTTP surface (`/v1/office`)

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/missions?limit=` | recent mission refs (id, query, status, route, lead, lastSeq) |
| GET | `/missions/{id}/snapshot` | `OfficeSnapshot` |
| GET | `/missions/{id}/stream` | SSE: `snapshot`, then `event` / `snapshot` / `heartbeat`, finally `done` |

All GET, no side effects. Honours the platform API key when one is configured. CORS is
open to `localhost` / `127.0.0.1` only.
