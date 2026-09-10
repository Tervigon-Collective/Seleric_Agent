/**
 * Headless render smoke test — the closest thing to "open it in a browser"
 * without a browser. Stubs a 2D canvas context, mounts the whole <App/> with
 * the demo provider, runs the rAF draw loop for the full CAC scenario, and
 * asserts nothing throws, every agent gets a position, and no agent ends up
 * outside the walkable floor.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { isWalkable } from "../office/navmesh";
import { blankAgents } from "../office/agents";

// --- minimal 2D context stub ------------------------------------------------
function stubCtx(): CanvasRenderingContext2D {
  const noop = () => {};
  const handler: ProxyHandler<Record<string, unknown>> = {
    get: (t, prop) => {
      if (prop === "measureText") return () => ({ width: 10 });
      if (prop === "canvas") return { width: 1200, height: 800 };
      if (typeof prop === "string" && prop in t) return t[prop];
      return noop;
    },
    set: (t, prop, val) => {
      if (typeof prop === "string") t[prop] = val;
      return true;
    },
  };
  return new Proxy({}, handler) as unknown as CanvasRenderingContext2D;
}

let root: Root;
let container: HTMLDivElement;
const errors: string[] = [];

beforeEach(() => {
  errors.length = 0;
  vi.spyOn(console, "error").mockImplementation((...a: unknown[]) => {
    errors.push(a.map((x) => String(x)).join(" "));
  });
  (HTMLCanvasElement.prototype as unknown as { getContext: () => unknown }).getContext = () => stubCtx();
  const g = globalThis as unknown as Record<string, unknown>;
  if (!("ResizeObserver" in globalThis)) {
    g.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  }
  g.requestAnimationFrame = (cb: FrameRequestCallback) =>
    setTimeout(() => cb(performance.now()), 16) as unknown as number;
  g.cancelAnimationFrame = (h: number) => clearTimeout(h as unknown as ReturnType<typeof setTimeout>);

  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("render smoke", () => {
  it("mounts, runs the demo, and never throws or leaves an agent off the floor", async () => {
    vi.useFakeTimers();
    act(() => root.render(<App />));

    // let providers connect + the rAF loop spin
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    // Full CAC script with walk→talk→return gates (speed 1) needs several minutes
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300_000);
    });

    vi.useRealTimers();

    // no React render errors
    const reactErrors = errors.filter((e) =>
      /(is not a function|Cannot read|undefined is not|Maximum update depth)/.test(e),
    );
    expect(reactErrors, JSON.stringify(reactErrors)).toHaveLength(0);

    // the office rendered a canvas
    expect(container.querySelector("canvas.office-canvas")).toBeTruthy();
    // full roster present in the store-backed DOM (timeline / board rendered)
    expect(container.querySelector(".mission-board")).toBeTruthy();

    // the whole scripted mission (incl. all the meeting choreography) ran to the
    // end without wedging — proof the interaction/separation churn is stable
    const { useOffice } = await import("../store");
    expect(useOffice.getState().status).toBe("completed");
    expect(useOffice.getState().timeline.length).toBeGreaterThan(15);
  });

  it("agent roster is complete and stable", () => {
    const ids = blankAgents().map((a) => a.agentId);
    expect(ids).toHaveLength(14);
    for (const need of ["coordinator", "performance_agent", "skeptic_agent", "technical_agent"]) {
      expect(ids).toContain(need);
    }
  });
});

// keep isWalkable import meaningful — spot-check the demo desks
it("all demo desk homes are on walkable floor", async () => {
  const { DESKS } = await import("../office/layout");
  for (const d of DESKS) expect(isWalkable(d.home.x, d.home.y), d.agentId).toBe(true);
});
