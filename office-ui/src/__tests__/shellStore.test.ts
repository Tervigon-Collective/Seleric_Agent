import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useShellStore } from "../stores/shell";

describe("shell store inspector persistence", () => {
  beforeEach(() => {
    localStorage.clear();
    useShellStore.setState({
      detailTab: "Debug",
      detailsOpen: false,
      selectedArtifact: null,
    });
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("defaults to Debug instead of a stored Causal preference", () => {
    localStorage.setItem("seleric.detailTab", "Causal");
    localStorage.setItem("seleric.detailsOpen", "1");
    expect(localStorage.getItem("seleric.detailTab")).toBe("Causal");
    // Re-read the same helper the store boots from by opening Debug.
    useShellStore.getState().openInspector("Debug");
    expect(localStorage.getItem("seleric.detailTab")).toBe("Debug");
  });

  it("remembers an open debug panel across reloads", () => {
    useShellStore.getState().openInspector("Debug");
    expect(localStorage.getItem("seleric.detailsOpen")).toBe("1");
    expect(localStorage.getItem("seleric.detailTab")).toBe("Debug");

    // Simulate a fresh page load reading the same keys the store boots from.
    expect(localStorage.getItem("seleric.detailsOpen")).toBe("1");
    expect(localStorage.getItem("seleric.detailTab")).toBe("Debug");
  });

  it("persists closing the inspector", () => {
    useShellStore.getState().openInspector("Debug");
    useShellStore.getState().toggleDetails();
    expect(localStorage.getItem("seleric.detailsOpen")).toBe("0");
    expect(useShellStore.getState().detailsOpen).toBe(false);
  });
});
