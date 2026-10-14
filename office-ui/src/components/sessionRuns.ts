import type { ActivityEvent, Message } from "../api/contracts";
import { isTraceableMissionId } from "../store";

export type SessionRunStatus = "running" | "completed" | "failed" | "cancelled" | "unknown";

export type SessionRun = {
  runId: string;
  missionId: string | null;
  query: string;
  status: SessionRunStatus;
  startedAt: string | null;
};

function asText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function messageText(message: Message): string {
  return message.parts
    .filter((part) => part.type === "TEXT" && typeof part.content === "string")
    .map((part) => String(part.content))
    .join("\n")
    .trim();
}

function payloadOf(event: ActivityEvent): Record<string, unknown> {
  return event.payload && typeof event.payload === "object"
    ? event.payload as Record<string, unknown>
    : {};
}

/** Build newest-first run cards for the Debug inspector from thread messages + events. */
export function sessionRunsFromThread(
  messages: Message[],
  events: ActivityEvent[],
  opts: {
    currentRunId?: string | null;
    currentMissionId?: string | null;
    live?: boolean;
  } = {},
): SessionRun[] {
  const byRun = new Map<string, SessionRun>();

  for (const message of messages) {
    if (message.role !== "USER" || !message.run_id) continue;
    const query = messageText(message) || "Untitled question";
    byRun.set(message.run_id, {
      runId: message.run_id,
      missionId: null,
      query,
      status: "unknown",
      startedAt: message.created_at ?? null,
    });
  }

  for (const event of events) {
    if (!event.run_id) continue;
    const payload = payloadOf(event);
    const missionId = asText(payload.mission_id);
    let row = byRun.get(event.run_id);
    if (!row) {
      row = {
        runId: event.run_id,
        missionId: missionId,
        query: asText(event.summary) || asText(event.title) || "Run",
        status: "unknown",
        startedAt: event.created_at ?? null,
      };
      byRun.set(event.run_id, row);
    } else {
      if (isTraceableMissionId(missionId) && !isTraceableMissionId(row.missionId)) {
        row.missionId = missionId;
      } else if (!row.missionId && missionId) {
        row.missionId = missionId;
      }
      if (!row.startedAt && event.created_at) row.startedAt = event.created_at;
    }
    if (event.event_type === "run.completed") row.status = "completed";
    else if (event.event_type === "run.failed") row.status = "failed";
    else if (event.event_type === "run.cancelled") row.status = "cancelled";
    else if (
      (event.event_type === "run.started" || event.event_type === "run.queued")
      && row.status === "unknown"
    ) {
      row.status = "running";
    }
  }

  const currentRunId = opts.currentRunId ?? null;
  if (currentRunId) {
    const row = byRun.get(currentRunId);
    if (row) {
      if (opts.live) row.status = "running";
      if (isTraceableMissionId(opts.currentMissionId)) row.missionId = opts.currentMissionId;
    } else {
      byRun.set(currentRunId, {
        runId: currentRunId,
        missionId: opts.currentMissionId ?? null,
        query: "Current run",
        status: opts.live ? "running" : "unknown",
        startedAt: null,
      });
    }
  }

  return [...byRun.values()].sort((a, b) => {
    const ta = a.startedAt ? Date.parse(a.startedAt) : 0;
    const tb = b.startedAt ? Date.parse(b.startedAt) : 0;
    if (ta !== tb) return tb - ta;
    return b.runId.localeCompare(a.runId);
  });
}

export function truncateQuery(query: string, max = 72): string {
  const one = query.replace(/\s+/g, " ").trim();
  if (one.length <= max) return one;
  return `${one.slice(0, max - 1)}…`;
}
