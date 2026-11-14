import { describe, expect, it } from "vitest";
import { answerText } from "../components/Transcript";

const message = (inner: string) => {
  const root = document.createElement("div");
  root.innerHTML = inner;
  return root;
};

describe("answerText (Copy response)", () => {
  it("copies the answer prose, tables and code, and nothing from the footer", () => {
    const root = message(`
      <div data-slot="message-content">
        <div class="safe-content">Sales are up.</div>
        <table data-slot="table"><tr><th>Channel</th><th>Spend</th></tr><tr><td>Meta</td><td>1,200</td></tr></table>
        <pre data-slot="part-code">select 1</pre>
        <p data-slot="message-context">Period: last 7 days</p>
        <div data-slot="message-feedback">Was this helpful?</div>
        <div data-slot="message-actions">Copy</div>
      </div>`);
    const text = answerText(root);
    expect(text).toContain("Sales are up.");
    expect(text).toContain("Channel\tSpend");
    expect(text).toContain("select 1");
    expect(text).not.toMatch(/Period:|Was this helpful|Copy/);
  });

  it("falls back to the message body minus the chrome rows", () => {
    const root = message(`
      <div data-slot="message-content">
        Plain answer
        <p data-slot="message-context">Period: last 7 days</p>
        <div data-part="message-sources">Used 2 sources</div>
        <div data-part="thinking">Thinking</div>
        <div data-slot="run-status">Working</div>
      </div>`);
    const text = answerText(root);
    expect(text).toContain("Plain answer");
    expect(text).not.toMatch(/Period:|sources|Thinking|Working/);
  });
});
