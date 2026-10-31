import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { KpiGrid } from "./KpiGrid";

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("KpiGrid", () => {
  it("renders certified card values with units and evidence ids", () => {
    act(() => {
      root.render(
        <KpiGrid
          content={{
            title: "Overall",
            cards: [
              {
                label: "Ad spend",
                value: 12000,
                display: "INR 12,000",
                unit: "INR",
                population: "ad_day",
                time_basis: "2026-10-01..2026-10-11",
                evidence_ids: ["e1"],
                state: "ok",
              },
            ],
          }}
        />,
      );
    });
    expect(container.textContent).toContain("Ad spend");
    expect(container.textContent).toContain("INR 12,000");
    expect(container.textContent).toContain("ad_day");
  });

  it("shows an explanatory missing state instead of n/a", () => {
    act(() => {
      root.render(
        <KpiGrid content={{ cards: [{ label: "Sessions", state: "missing", value: null }] }} />,
      );
    });
    expect(container.textContent).toMatch(/not available from certified evidence/i);
    expect(container.textContent).not.toMatch(/\bn\/a\b/i);
  });

  it("renders empty and error states", () => {
    act(() => {
      root.render(<KpiGrid content={{ state: "empty", cards: [] }} />);
    });
    expect(container.querySelector("[role='status']")).toBeTruthy();
    act(() => {
      root.render(<KpiGrid content={{ state: "error", error: "boom" }} />);
    });
    expect(container.querySelector("[role='alert']")?.textContent).toMatch(/boom|could not/i);
  });
});
