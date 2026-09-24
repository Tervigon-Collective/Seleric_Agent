import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SafeContent } from "../components/SafeContent";

let container: HTMLDivElement;
let root: Root;

const render = (text: string) => {
  act(() => root.render(<SafeContent text={text} />));
  return container;
};

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("SafeContent markdown", () => {
  it("renders headings, bold, italic and inline code", () => {
    const el = render("## Summary\n\nNet sales were **up 12%** and *steady*, see `metric.net_sales`.");
    expect(el.querySelector("h3")?.textContent).toBe("Summary");
    expect(el.querySelector("strong")?.textContent).toBe("up 12%");
    expect(el.querySelector("em")?.textContent).toBe("steady");
    expect(el.querySelector("code")?.textContent).toBe("metric.net_sales");
    expect(el.textContent).not.toContain("**");
    expect(el.textContent).not.toContain("##");
  });

  it("renders bullet and numbered lists", () => {
    const el = render("- alpha\n- **beta**\n\n1. first\n2. second");
    expect(el.querySelectorAll("ul > li")).toHaveLength(2);
    expect(el.querySelector("ul strong")?.textContent).toBe("beta");
    expect(el.querySelectorAll("ol > li")).toHaveLength(2);
  });

  it("joins an indented continuation line onto its list item", () => {
    const el = render("- first item\n  continued here\n- second");
    const items = el.querySelectorAll("li");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toBe("first item continued here");
  });

  it("renders GFM tables with header and body cells", () => {
    const el = render("| Metric | Value |\n| --- | ---: |\n| CAC | **412** |\n| CPM | 88 |");
    expect(el.querySelectorAll("thead th")).toHaveLength(2);
    expect(el.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(el.querySelector("tbody td strong")?.textContent).toBe("412");
    expect(el.textContent).not.toContain("---");
  });

  it("renders blockquotes and rules", () => {
    const el = render("> caution: partial data\n\n---\n\nnext");
    expect(el.querySelector("blockquote")?.textContent).toContain("partial data");
    expect(el.querySelector("hr")).toBeTruthy();
  });

  it("keeps fenced code verbatim, without parsing markdown inside", () => {
    const el = render("before\n\n```sql\nSELECT **x** FROM t\n```\n\nafter");
    expect(el.querySelector("pre code")?.textContent).toBe("SELECT **x** FROM t");
    expect(el.querySelector("pre strong")).toBeNull();
  });

  it("links only http(s) URLs and never javascript:", () => {
    const el = render("[ok](https://example.com/a) and [bad](javascript:alert(1)) and https://x.io/y.");
    const hrefs = [...el.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["https://example.com/a", "https://x.io/y"]);
    expect(el.textContent).toContain("[bad](javascript:alert(1))");
    for (const a of el.querySelectorAll("a")) expect(a.getAttribute("rel")).toBe("noreferrer");
  });

  it("never injects markup, including from table cells and headings", () => {
    const el = render("# <img src=x onerror=alert(1)>\n\n| a |\n| - |\n| <script>alert(1)</script> |");
    expect(el.querySelector("img")).toBeNull();
    expect(el.querySelector("script")).toBeNull();
    expect(el.textContent).toContain("<script>");
  });

  it("does not treat snake_case or a lone asterisk as emphasis", () => {
    const el = render("metric_net_sales_daily rose 5 * 3 = 15");
    expect(el.querySelector("em")).toBeNull();
    expect(el.textContent).toBe("metric_net_sales_daily rose 5 * 3 = 15");
  });

  it("handles a very long multi-section answer without dropping content", () => {
    const sections = Array.from({ length: 40 }, (_, i) =>
      `### Section ${i}\n\n- point **${i}**\n- detail ${i}\n\n| k | v |\n| - | - |\n| a | ${i} |`,
    ).join("\n\n");
    const el = render(sections);
    expect(el.querySelectorAll("h4")).toHaveLength(40);
    expect(el.querySelectorAll("table")).toHaveLength(40);
    expect(el.querySelectorAll("li")).toHaveLength(80);
  });

  it("stays fast on pathological input (no regex backtracking blow-up)", () => {
    const cases = [
      "*".repeat(20000),
      "**a ".repeat(5000),
      "`".repeat(20000),
      "[a](".repeat(3000),
      `${"_x ".repeat(6000)}`,
      `${"| a ".repeat(3000)}\n${"| - ".repeat(3000)}\n${"| b ".repeat(3000)}`,
      "https://".repeat(3000),
      "- ".repeat(8000),
    ];
    for (const input of cases) {
      const t0 = performance.now();
      render(input);
      expect(performance.now() - t0).toBeLessThan(2000);
    }
  });

  it("tolerates unbalanced markers and empty input", () => {
    expect(() => render("**unclosed and `tick and [broken](")).not.toThrow();
    expect(render("").textContent).toBe("");
  });
});
