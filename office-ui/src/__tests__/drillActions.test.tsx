import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SelectionInsight } from "../components/charts/ChartRenderer";

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

const ROW = { time: "2026-10-05", net_sales: 717245.58 };

describe("SelectionInsight drill actions", () => {
  it("offers ask and compare follow-ups pinned to the point", () => {
    const onAsk = vi.fn();
    const onCompare = vi.fn();
    act(() => root.render(
      <SelectionInsight
        columns={["time", "net_sales"]}
        row={ROW}
        onClose={() => undefined}
        drill={{ onAsk, onCompare }}
      />,
    ));
    const ask = container.querySelector('[aria-label="Ask Seleric about this point"]') as HTMLButtonElement;
    const compare = container.querySelector('[aria-label="Compare this point with the previous period"]') as HTMLButtonElement;
    expect(ask).toBeTruthy();
    expect(compare).toBeTruthy();
    act(() => ask.click());
    act(() => compare.click());
    expect(onAsk).toHaveBeenCalledTimes(1);
    expect(onCompare).toHaveBeenCalledTimes(1);
  });

  it("shows the data-view jump only when provided", () => {
    const onShowData = vi.fn();
    act(() => root.render(
      <SelectionInsight
        columns={["time"]}
        row={ROW}
        onClose={() => undefined}
        drill={{ onShowData, onAsk: () => undefined, onCompare: () => undefined }}
      />,
    ));
    expect(container.querySelector('[aria-label="Show underlying data"]')).toBeTruthy();
  });

  it("stays a plain inspector without drill actions", () => {
    act(() => root.render(
      <SelectionInsight columns={["time"]} row={ROW} onClose={() => undefined} />,
    ));
    expect(container.querySelector('[aria-label="Ask Seleric about this point"]')).toBeNull();
    expect(container.textContent).toContain("2026-10-05");
  });
});
