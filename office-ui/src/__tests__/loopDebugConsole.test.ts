import { describe, expect, it } from "vitest";
import {
  debugStatCells,
  entriesFromTimeline,
  entriesFromTrace,
  liveStatsFromTimeline,
} from "../components/LoopDebugConsole";
import type { SwarmUIEvent } from "../types";

describe("loop debug console helpers", () => {
  it("formats the test_agent stats grid including grounding", () => {
    const cells = Object.fromEntries(debugStatCells(
      {
        elapsed_ms: 34400,
        ttft_ms: 13700,
        answer_ttft_ms: 31100,
        llm_calls: 5,
        tool_calls: 5,
        revisions: 1,
        prompt_tokens: 55282,
        completion_tokens: 3296,
        cached_tokens: 44032,
        errors: ["x"],
      },
      {
        numbers: 22,
        measured: 20,
        derived: [{}, {}],
        unmatched: [],
        misbound: [],
        placeholders: [],
      },
    ));
    expect(cells.Total).toBe("34.4s");
    expect(cells["First token"]).toBe("13.7s");
    expect(cells["Answer starts"]).toBe("31.1s");
    expect(cells["LLM calls"]).toBe(5);
    expect(cells["Cache hits"]).toBe(44032);
    expect(cells.Errors).toBe(1);
    expect(cells["Numbers grounded"]).toBe("22/22");
    expect(cells.Ungrounded).toBe("none");
  });

  it("recovers First token / Answer starts from llm_steps.at_ms when stats omit them", () => {
    const cells = Object.fromEntries(debugStatCells(
      {
        elapsed_ms: 11400,
        llm_calls: 2,
        llm_steps: [
          { ms: 4262, at_ms: 4700, prompt_tokens: 100, completion_tokens: 50 },
          { ms: 6616, at_ms: 11300, prompt_tokens: 120, completion_tokens: 80 },
        ],
      },
      null,
    ));
    expect(cells["First token"]).toBe("4.7s");
    expect(cells["Answer starts"]).toBe("11.3s");
  });

  it("falls Answer starts back to elapsed when no timing fields exist", () => {
    const cells = Object.fromEntries(debugStatCells(
      { elapsed_ms: 11400, llm_calls: 2, llm_steps: [{ ms: 4000 }] },
      null,
    ));
    expect(cells["First token"]).toBe("—");
    expect(cells["Answer starts"]).toBe("11.4s");
  });

  it("builds tool and LLM log entries from trace steps", () => {
    const entries = entriesFromTrace(
      [
        { kind: "tool_call", tool: "metrics_query", args: { measures: ["net_sales"] } },
        { kind: "tool_return", tool: "metrics_query", ok: true, ms: 553, chars: 1200, query_id: "q_abc" },
        { kind: "tool_return", tool: "bad_tool", ok: false, ms: 12, chars: 40 },
      ],
      [{ ms: 9920, model: "gpt-5-mini", prompt_tokens: 11165, completion_tokens: 981, tool_calls: 2, finish: "tool_calls" }],
    );
    expect(entries[0].head).toContain("→ metrics_query");
    expect(entries[1].head).toContain("✓");
    expect(entries[1].head).toContain("553ms");
    expect(entries[1].cls).toBe("ok");
    expect(entries[2].cls).toBe("bad");
    expect(entries[3].head).toContain("LLM #1");
    expect(entries[3].head).toContain("11165→981");
  });

  it("maps live timeline events into the debug log", () => {
    const timeline: SwarmUIEvent[] = [
      {
        eventId: "e1", seq: 1, missionId: "m1", eventType: "tool_started",
        summary: "Fetching metric data", agentId: "seleric", timestamp: "2026-10-13T00:00:00Z",
        metadata: { conversationEventType: "agent.tool_started", tool: "metrics_query" },
      },
      {
        eventId: "e2", seq: 2, missionId: "m1", eventType: "agent_revising",
        summary: "Checking the figures against the data", agentId: "seleric", timestamp: "2026-10-13T00:00:01Z",
        metadata: { conversationEventType: "agent.revising", issues: ["500"] },
      },
    ];
    const entries = entriesFromTimeline(timeline);
    expect(entries[0].head.startsWith("→")).toBe(true);
    expect(entries[1].head.startsWith("↺")).toBe(true);
    expect(entries[1].cls).toBe("bad");
  });

  it("derives provisional live stats from the timeline", () => {
    const start = "2026-10-13T00:00:00.000Z";
    const timeline: SwarmUIEvent[] = [
      {
        eventId: "e0", seq: 0, missionId: "m1", eventType: "mission_started",
        summary: "run started", timestamp: start, metadata: {},
      },
      {
        eventId: "e1", seq: 1, missionId: "m1", eventType: "tool_started",
        summary: "Metrics query", timestamp: "2026-10-13T00:00:02.000Z",
        metadata: { conversationEventType: "agent.tool_started" },
      },
      {
        eventId: "e2", seq: 2, missionId: "m1", eventType: "tool_started",
        summary: "Catalogue resolve values", timestamp: "2026-10-13T00:00:03.000Z",
        metadata: { conversationEventType: "agent.tool_started" },
      },
    ];
    const stats = liveStatsFromTimeline(timeline, { nowMs: Date.parse(start) + 5000 });
    expect(stats?.tool_calls).toBe(2);
    expect(stats?.elapsed_ms).toBe(5000);
    expect(stats?.llm_calls).toBeNull();
  });
});
