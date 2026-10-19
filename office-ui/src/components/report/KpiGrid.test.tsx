import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { KpiGrid } from "./KpiGrid";

describe("KpiGrid", () => {
  it("renders certified card values with units and evidence ids", () => {
    render(
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
    expect(screen.getByText("Ad spend")).toBeTruthy();
    expect(screen.getByText("INR 12,000")).toBeTruthy();
    expect(screen.getByText("ad_day")).toBeTruthy();
  });

  it("shows an explanatory missing state instead of n/a", () => {
    render(
      <KpiGrid
        content={{
          cards: [{ label: "Sessions", state: "missing", value: null }],
        }}
      />,
    );
    expect(screen.getByText(/not available from certified evidence/i)).toBeTruthy();
    expect(screen.queryByText("n/a")).toBeNull();
  });

  it("renders empty and error states", () => {
    const { rerender } = render(<KpiGrid content={{ state: "empty", cards: [] }} />);
    expect(screen.getByRole("status")).toBeTruthy();
    rerender(<KpiGrid content={{ state: "error", error: "boom" }} />);
    expect(screen.getByRole("alert").textContent).toMatch(/boom|could not/i);
  });
});
