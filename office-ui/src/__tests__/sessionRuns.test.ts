import { describe, expect, it } from "vitest";
import type { ActivityEvent, Message } from "../api/contracts";
import { sessionRunsFromThread, truncateQuery } from "../components/sessionRuns";

function user(id: string, runId: string, text: string, createdAt: string): Message {
  return {
    id, thread_id: "t1", workspace_id: "w", user_id: "u", role: "USER",
    parts: [{ type: "TEXT", content: text }],
    run_id: runId, parent_message_id: null, created_at: createdAt,
  };
}

function event(
  runId: string,
  type: string,
  payload: Record<string, unknown> = {},
  createdAt = "2026-10-14T00:00:00Z",
): ActivityEvent {
  return {
    id: `${runId}-${type}`,
    thread_id: "t1",
    workspace_id: "w",
    run_id: runId,
    sequence: 1,
    event_type: type,
    actor_type: null,
    actor_id: null,
    title: null,
    summary: type,
    evidence_ids: [],
    payload,
    metadata: {},
    created_at: createdAt,
  };
}

describe("sessionRunsFromThread", () => {
  it("lists every user turn newest-first with mission + status", () => {
    const runs = sessionRunsFromThread(
      [
        user("m1", "run-a", "What was ad spend yesterday?", "2026-10-14T10:00:00Z"),
        user("m2", "run-b", "Which Meta campaigns…", "2026-10-14T11:00:00Z"),
      ],
      [
        event("run-a", "run.queued", { mission_id: "MS3-aaa" }, "2026-10-14T10:00:01Z"),
        event("run-a", "run.completed", { mission_id: "MS3-aaa" }, "2026-10-14T10:00:40Z"),
        event("run-b", "run.started", { mission_id: "MS3-bbb" }, "2026-10-14T11:00:01Z"),
      ],
    );
    expect(runs.map((r) => r.runId)).toEqual(["run-b", "run-a"]);
    expect(runs[0].missionId).toBe("MS3-bbb");
    expect(runs[0].status).toBe("running");
    expect(runs[1].status).toBe("completed");
    expect(runs[1].query).toContain("ad spend");
  });

  it("marks the live current run even before events land", () => {
    const runs = sessionRunsFromThread(
      [user("m1", "run-live", "why did ctr drop", "2026-10-14T12:00:00Z")],
      [],
      { currentRunId: "run-live", currentMissionId: "MS3-live", live: true },
    );
    expect(runs).toHaveLength(1);
    expect(runs[0].status).toBe("running");
    expect(runs[0].missionId).toBe("MS3-live");
  });

  it("truncates long queries for the accordion summary", () => {
    expect(truncateQuery("a".repeat(80)).endsWith("…")).toBe(true);
    expect(truncateQuery("short")).toBe("short");
  });
});
