import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, describe, expect, it } from "vitest";
import { MessagePartRenderer } from "../components/MessagePartRenderer";
import { useShellStore } from "../stores/shell";

describe("typed message part rendering", () => {
  beforeEach(() => useShellStore.setState({ selectedArtifact: null, detailTab: "Debug" }));
  const draw = (part: Parameters<typeof MessagePartRenderer>[0]["part"]) => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => root.render(<MessagePartRenderer part={part} />));
    return container;
  };

  it("renders tables as cells instead of injected markup", () => {
    const container = draw({ type: "TABLE", content: [{ name: "<b>safe</b>" }] });
    expect(container.textContent).toContain("<b>safe</b>");
    expect(container.querySelector("b")).toBeNull();
  });

  it("orders table columns as the backend sent them, not by the key order of stored rows", () => {
    const container = draw({ type: "TABLE", content: {
      columns: ["Date", "Campaign Name", "Ad spend", "CTR"],
      sort: { column: "Ad spend", direction: "desc" },
      rows: [{ CTR: "1.4%", Date: "2026-10-09", "Ad spend": "₹34,506", "Campaign Name": "PMax" }],
    } } as never);
    const headers = Array.from(container.querySelectorAll("th")).map((th) => th.textContent?.replace(/[▲▼]/g, "").trim());
    expect(headers).toEqual(["Date", "Campaign Name", "Ad spend", "CTR"]);
    expect(container.querySelector('th[aria-sort="descending"]')?.textContent).toContain("Ad spend");
  });

  it("opens artifact provenance in the details state", () => {
    const container = draw({
      type: "ARTIFACT",
      content: {
        artifact_id: "artifact-1",
        provenance: { evidence_ids: ["EV-1"], calculation_version: "1" },
      },
    });
    act(() => container.querySelector("button")?.click());
    expect(useShellStore.getState().detailTab).toBe("Debug");
    expect(useShellStore.getState().selectedArtifact?.calculation_version).toBe("1");
  });

  it.each(["TEXT", "CODE", "SOURCE", "TOOL_CALL", "CHART", "AGENT_STATUS", "APPROVAL", "WARNING"] as const)(
    "renders %s safely",
    (type) => {
      const content = type === "TEXT" || type === "CODE" || type === "AGENT_STATUS" || type === "WARNING"
        ? "<script>alert(1)</script>"
        : type === "SOURCE" ? { title: "Source" }
        : type === "TOOL_CALL" ? { tool: "metrics" }
        : type === "APPROVAL" ? { approval_id: "a", summary: "Approve" }
        : { title: "Chart", data: [] };
      const container = draw({ type, content });
      expect(container.querySelector("script")).toBeNull();
    },
  );
});
