import { describe, expect, it } from "vitest";
import { formatDelta } from "../components/report/KpiGrid";

describe("formatDelta", () => {
  it("writes the change as a share of the previous value with an arrow", () => {
    expect(formatDelta({ delta: -31867, delta_pct: -11 })).toEqual({ text: "▼ 11%", direction: "down" });
    expect(formatDelta({ delta: 5, delta_pct: 12.34 })).toEqual({ text: "▲ 12.3%", direction: "up" });
  });
  it("falls back to the signed absolute change when the previous value was zero", () => {
    expect(formatDelta({ delta: 7 })).toEqual({ text: "▲ +7", direction: "up" });
    expect(formatDelta({ delta: 0, delta_pct: 0 })).toEqual({ text: "– 0%", direction: "flat" });
  });
  it("shows nothing without a comparison", () => {
    expect(formatDelta(null)).toBeNull();
    expect(formatDelta({})).toBeNull();
  });
});
