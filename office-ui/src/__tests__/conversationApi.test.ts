import { describe, expect, it, vi } from "vitest";
import { ConversationApi } from "../api/conversations";
import { HttpClient } from "../api/http";
import { parseSseFrame } from "../api/runEvents";

describe("conversation API", () => {
  it("adds authentication and serializes message submissions", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ message_id: "m1", run_id: "r1", mission_id: "ms1" }),
      { status: 202, headers: { "Content-Type": "application/json" } },
    ));
    const client = new HttpClient({ baseUrl: "https://api.test", getToken: () => "secret", fetchImpl });
    await new ConversationApi(client).submitMessage("thread/a", {
      parts: [{ type: "TEXT", content: "hello" }],
    });
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/v1/threads/thread%2Fa/messages");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer secret");
    expect(JSON.parse(String(init.body)).parts[0].content).toBe("hello");
  });

  it("parses SSE ids, event names, and multiline data", () => {
    expect(parseSseFrame("id: 7\nevent: run.started\ndata: {\"a\":\ndata: 1}")).toEqual({
      id: "7", event: "run.started", data: "{\"a\":\n1}",
    });
  });
});
