import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useShellStore } from "../stores/shell";

describe("shell store inspector persistence", () => {
  beforeEach(() => {
    localStorage.clear();
    useShellStore.setState({
      detailTab: "Evidence",
      detailsOpen: false,
      selectedArtifact: null,
    });
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("remembers an open activity panel across reloads", () => {
    useShellStore.getState().openInspector("Activity");
    expect(localStorage.getItem("seleric.detailsOpen")).toBe("1");
    expect(localStorage.getItem("seleric.detailTab")).toBe("Activity");

    // Simulate a fresh page load reading the same keys the store boots from.
    expect(localStorage.getItem("seleric.detailsOpen")).toBe("1");
    expect(localStorage.getItem("seleric.detailTab")).toBe("Activity");
  });

  it("persists closing the inspector", () => {
    useShellStore.getState().openInspector("Activity");
    useShellStore.getState().toggleDetails();
    expect(localStorage.getItem("seleric.detailsOpen")).toBe("0");
    expect(useShellStore.getState().detailsOpen).toBe(false);
  });
});
