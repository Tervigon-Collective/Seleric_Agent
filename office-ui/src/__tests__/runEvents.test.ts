import { describe, expect, it, vi } from "vitest";
import { HttpClient } from "../api/http";
import { subscribeToRunEvents } from "../api/runEvents";

describe("run event reconnection", () => {
  it("resumes with after_sequence and Last-Event-ID", async () => {
    vi.useFakeTimers();
    const calls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    let stop = () => {};
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push([input, init]);
      if (calls.length === 1) {
        const payload = 'id: 4\nevent: run.started\ndata: {"id":"e4","thread_id":"t1","workspace_id":"w","run_id":"r1","sequence":4,"event_type":"run.started","actor_type":null,"actor_id":null,"title":null,"summary":null,"evidence_ids":[],"payload":{},"metadata":{},"started_at":null,"completed_at":null,"duration_ms":null,"created_at":"2026-01-01"}\n\n';
        return new Response(new ReadableStream({
          start(controller) { controller.enqueue(new TextEncoder().encode(payload)); controller.close(); },
        }), { status: 200 });
      }
      stop();
      throw new DOMException("aborted", "AbortError");
    });
    const client = new HttpClient({ fetchImpl, getToken: () => "key" });
    const events: number[] = [];
    stop = subscribeToRunEvents("r1", { onEvent: (event) => events.push(event.sequence) }, { client });
    await vi.advanceTimersByTimeAsync(600);
    expect(events).toEqual([4]);
    expect(String(calls[1][0])).toContain("after_sequence=4");
    expect(new Headers(calls[1][1]?.headers).get("Last-Event-ID")).toBe("4");
    vi.useRealTimers();
  });
});
