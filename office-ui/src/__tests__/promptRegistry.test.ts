import { describe, expect, it } from "vitest";
import {
  PROMPT_REGISTRY,
  filterPrompts,
  frontChatPrompts,
  groupByDomain,
} from "../data/promptRegistry";

describe("prompt registry (demo set)", () => {
  it("has no simple lookups and unique ids and texts", () => {
    expect(PROMPT_REGISTRY.some((p) => p.level === "L1" || p.level === "L2")).toBe(false);
    expect(new Set(PROMPT_REGISTRY.map((p) => p.id)).size).toBe(PROMPT_REGISTRY.length);
    expect(new Set(PROMPT_REGISTRY.map((p) => p.text)).size).toBe(PROMPT_REGISTRY.length);
  });

  it("offers exactly 10 executive prompts spanning several domains, all eval-passed", () => {
    const exec = filterPrompts(PROMPT_REGISTRY, "exec", "all");
    expect(exec).toHaveLength(10);
    expect(exec.every((p) => p.tags.includes("successful"))).toBe(true);
    expect(new Set(exec.map((p) => p.domain)).size).toBeGreaterThanOrEqual(3);
  });

  it("filters diagnosis and intersects with domain", () => {
    expect(filterPrompts(PROMPT_REGISTRY, "diagnosis", "all").every((p) => p.tags.includes("diagnosis"))).toBe(true);
    const marketingExec = filterPrompts(PROMPT_REGISTRY, "exec", "marketing");
    expect(marketingExec.length).toBeGreaterThan(0);
    expect(marketingExec.every((p) => p.domain === "marketing" && p.tags.includes("exec"))).toBe(true);
  });

  it("starts the empty chat with the executive set", () => {
    expect(frontChatPrompts()).toEqual(filterPrompts(PROMPT_REGISTRY, "exec", "all"));
  });

  it("groups filtered prompts by domain without empty sections", () => {
    const groups = groupByDomain(filterPrompts(PROMPT_REGISTRY, "exec", "all"));
    expect(groups.every((g) => g.prompts.length > 0)).toBe(true);
  });
});
