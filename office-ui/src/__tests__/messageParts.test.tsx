import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, describe, expect, it } from "vitest";
import { MessagePartRenderer } from "../components/MessagePartRenderer";
import { useShellStore } from "../stores/shell";

describe("typed message part rendering", () => {
  beforeEach(() => useShellStore.setState({ selectedArtifact: null, detailTab: "Activity" }));
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

  it("opens artifact provenance in the details state", () => {
    const container = draw({
      type: "ARTIFACT",
      content: {
        artifact_id: "artifact-1",
        provenance: { evidence_ids: ["EV-1"], calculation_version: "1" },
      },
    });
    act(() => container.querySelector("button")?.click());
    expect(useShellStore.getState().detailTab).toBe("Artifacts");
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
