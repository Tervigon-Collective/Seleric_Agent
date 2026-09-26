import { describe, expect, it, vi } from "vitest";
import { HttpClient } from "../api/http";
import { SelericEventProvider } from "../providers/seleric";

describe("Seleric office provider", () => {
  it("loads list, snapshot, and fetch-based SSE requests", async () => {
    const stream = [
      'event: snapshot\ndata: {"missionId":"m1","query":"q","agents":[]}\n\n',
      'event: event\ndata: {"eventId":"e1","seq":1,"timestamp":"now","missionId":"m1","eventType":"started","summary":"Started"}\n\n',
      'event: done\ndata: {"status":"complete"}\n\n',
    ].join("");
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ missions: [{ missionId: "m1", query: "q" }] }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ missionId: "m1", query: "q", agents: [] }), {
        status: 200, headers: { "Content-Type": "application/json" },
      }))
      .mockResolvedValueOnce(new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(stream));
          controller.close();
        },
      }), { status: 200 }));
    const client = new HttpClient({ baseUrl: "https://api.test", fetchImpl });
    const provider = new SelericEventProvider({ client });

    await provider.listMissions();
    await provider.getSnapshot("m1");
    const received: string[] = [];
    await new Promise<void>((resolve) => {
      provider.subscribe("m1", {
        onSnapshot: () => received.push("snapshot"),
        onEvent: () => received.push("event"),
        onState: () => undefined,
        onDone: (status) => { received.push(status); resolve(); },
      });
    });

    expect(received).toEqual(["snapshot", "event", "complete"]);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });
});
