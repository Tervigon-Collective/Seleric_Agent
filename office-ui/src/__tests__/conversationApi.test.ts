import { describe, expect, it, vi } from "vitest";
import { ConversationApi, MAX_ATTACHMENT_SIZE, validateAttachment } from "../api/conversations";
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

  it("uploads cross-origin attachments without API auth and completes with SHA-256", async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        attachment: {
          id: "a1", filename: "note.txt", content_type: "text/plain", size_bytes: 5,
          checksum_sha256: null, status: "PENDING",
        },
        upload: { method: "PUT", url: "https://objects.test/upload/a1" },
      }), { status: 201, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "a1", filename: "note.txt", content_type: "text/plain", size_bytes: 5,
        checksum_sha256: "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        status: "READY",
      }), { status: 200, headers: { "Content-Type": "application/json" } }));
    const client = new HttpClient({ baseUrl: "https://api.test", getToken: () => "secret", fetchImpl });

    const result = await new ConversationApi(client).uploadAttachment(
      "t1",
      new File(["hello"], "note.txt", { type: "text/plain" }),
    );

    expect(result.status).toBe("READY");
    expect(new Headers(fetchImpl.mock.calls[1][1].headers).has("Authorization")).toBe(false);
    expect(JSON.parse(String(fetchImpl.mock.calls[2][1].body))).toEqual({
      checksum_sha256: "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
    });
  });

  it("rejects unsupported and oversized attachments before upload", () => {
    expect(() => validateAttachment(new File(["x"], "app.exe", { type: "application/x-msdownload" })))
      .toThrow("not supported");
    expect(() => validateAttachment({
      name: "large.pdf", type: "application/pdf", size: MAX_ATTACHMENT_SIZE + 1,
    } as File)).toThrow("25 MB");
  });
});
