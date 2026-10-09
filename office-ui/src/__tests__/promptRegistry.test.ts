import { describe, expect, it } from "vitest";
import {
  PROMPT_REGISTRY,
  filterPrompts,
  groupByDomain,
} from "../data/promptRegistry";

describe("prompt registry", () => {
  it("includes L1 lookup questions tagged required and successful", () => {
    const l1 = filterPrompts(PROMPT_REGISTRY, "L1", "all");
    expect(l1.length).toBeGreaterThanOrEqual(4);
    expect(l1.every((p) => p.level === "L1")).toBe(true);
    expect(l1.some((p) => p.text === "What were net sales yesterday?")).toBe(true);
  });

  it("filters golden, diagnosis, required, and successful tags", () => {
    expect(filterPrompts(PROMPT_REGISTRY, "golden", "all").every((p) => p.tags.includes("golden"))).toBe(true);
    expect(filterPrompts(PROMPT_REGISTRY, "diagnosis", "all").every((p) => p.tags.includes("diagnosis"))).toBe(true);
    expect(filterPrompts(PROMPT_REGISTRY, "required", "all").every((p) => p.tags.includes("required"))).toBe(true);
    expect(filterPrompts(PROMPT_REGISTRY, "successful", "all").every((p) => p.tags.includes("successful"))).toBe(true);
  });

  it("intersects domain with kind filters", () => {
    const marketingGolden = filterPrompts(PROMPT_REGISTRY, "golden", "marketing");
    expect(marketingGolden.length).toBeGreaterThan(0);
    expect(marketingGolden.every((p) => p.domain === "marketing" && p.tags.includes("golden"))).toBe(true);
  });

  it("groups filtered prompts by domain without empty sections", () => {
    const groups = groupByDomain(filterPrompts(PROMPT_REGISTRY, "L1", "all"));
    expect(groups.every((g) => g.prompts.length > 0)).toBe(true);
    expect(groups.some((g) => g.domain === "sales")).toBe(true);
  });
});
