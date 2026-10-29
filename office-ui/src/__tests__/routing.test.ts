import { afterEach, describe, expect, it } from "vitest";
import { readRoute, writeRoute } from "../routing";

describe("URL routing", () => {
  const original = location.href;
  afterEach(() => history.replaceState({}, "", original));

  it("round-trips conversation and office deep links through history", () => {
    history.replaceState({}, "", "/app?thread=t%2F1");
    expect(readRoute()).toMatchObject({ workspace: "conversation", threadId: "t/1" });

    writeRoute({ workspace: "office", missionId: "mission-1", threadId: null });
    expect(location.search).toContain("office=1");
    expect(location.search).toContain("mission=mission-1");
    expect(location.search).not.toContain("thread=");

    writeRoute({ workspace: "conversation", threadId: "thread-2", missionId: null });
    expect(readRoute()).toEqual({
      workspace: "conversation", threadId: "thread-2", missionId: null, opsPage: "dashboard",
    });
  });

  it("round-trips the ops dashboard and monitoring pages", () => {
    writeRoute({ workspace: "ops", opsPage: "monitoring" });
    expect(location.search).toContain("ops=monitoring");
    expect(location.search).not.toContain("office=");
    expect(readRoute()).toMatchObject({ workspace: "ops", opsPage: "monitoring" });

    writeRoute({ workspace: "ops", opsPage: "dashboard" });
    expect(readRoute()).toMatchObject({ workspace: "ops", opsPage: "dashboard" });

    writeRoute({ workspace: "conversation" });
    expect(location.search).not.toContain("ops=");
    expect(readRoute().workspace).toBe("conversation");
  });
});
