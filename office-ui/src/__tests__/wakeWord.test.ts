import { describe, expect, it } from "vitest";
import { isWakePhrase } from "../wakeWord";

describe("isWakePhrase", () => {
  it.each(["hey", "Hey, Seleric what's up", "so hey celeric", "hay selaric"])("wakes on %s", (t) => {
    expect(isWakePhrase(t)).toBe(true);
  });
  it.each(["seleric revenue last week", "they said", "hello there"])("ignores %s", (t) => {
    expect(isWakePhrase(t)).toBe(false);
  });
});
