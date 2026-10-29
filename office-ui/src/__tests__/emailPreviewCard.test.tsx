import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EmailPreviewCard } from "../components/EmailPreviewCard";
import { MessagePartRenderer } from "../components/MessagePartRenderer";
import { approveEmail, getEmail } from "../api/emails";
import { ApiError } from "../api/http";

vi.mock("../api/emails", () => ({ getEmail: vi.fn(), approveEmail: vi.fn() }));

const card = {
  approval_id: "draft_1", action: "send_email", format: "ad_hoc", version: 1, status: "draft",
  sender: "agent@seleric.ai", to: ["gaurav@seleric.ai"], cc: ["ops@seleric.ai"], subject: "Net sales last week",
  body: "Hi Gaurav,\n\nNet sales were ₹3,42,703.", attachments: [{ name: "net-sales.xlsx", size_bytes: 20480, partial: false, note: "" }],
};
const live = (over: Record<string, unknown> = {}) => ({
  approval_id: "draft_1", version: 1, status: "draft", error: "", provider_message_id: null,
  to: card.to, subject: card.subject, ...over,
});

let container: HTMLDivElement;
let root: Root;
beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vi.mocked(getEmail).mockResolvedValue(live() as never);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});
const show = async (value: Record<string, unknown> = card) => {
  await act(async () => { root.render(<EmailPreviewCard value={value} />); });
};
const approve = () => [...container.querySelectorAll("button")].find((b) => /approve/i.test(b.textContent ?? "")) as HTMLButtonElement;
const click = async (el: HTMLElement) => { await act(async () => { el.click(); await Promise.resolve(); }); };

describe("EmailPreviewCard", () => {
  it("shows who sends to whom, the subject, the body and the attachments before anything is sent", async () => {
    await show();
    const text = container.textContent ?? "";
    for (const part of ["agent@seleric.ai", "gaurav@seleric.ai", "ops@seleric.ai", "Net sales last week", "Net sales were ₹3,42,703.", "net-sales.xlsx"]) {
      expect(text).toContain(part);
    }
    expect(approveEmail).not.toHaveBeenCalled();
  });

  it("shows the data as a real table, with the headline in bold", async () => {
    const body = "Hi Gaurav,\n\n## What happened\n\nNet sales fell **10.8%**.\n\n**Net sales by channel** · 2026-10-21 to 2026-10-27\n\n"
      + "| Channel | Net sales (INR) |\n| --- | ---: |\n| ig_feed | ₹1,31,269 |\n| google_pmax | ₹1,03,521 |\n\nThanks,\nSeleric Reporting Assistant";
    await show({ ...card, body });
    expect(container.querySelector("table")).not.toBeNull();
    expect([...container.querySelectorAll("th")].map((th) => th.textContent)).toEqual(["Channel", "Net sales (INR)"]);
    expect(container.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(container.querySelector(".email-body strong")?.textContent).toBe("10.8%");
    expect(container.querySelector(".email-body h2, .email-body h3, .email-body h4")?.textContent).toBe("What happened");
  });

  it("warns that a partial attachment is partial", async () => {
    await show({ ...card, attachments: [{ name: "n.xlsx", size_bytes: 100, partial: true, note: "PARTIAL: 2 of 9 rows" }] });
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("PARTIAL: 2 of 9 rows");
  });

  it("approves exactly the version shown and says submitted is not delivery", async () => {
    vi.mocked(approveEmail).mockResolvedValue(live({ status: "submitted" }) as never);
    await show();
    await click(approve());
    expect(approveEmail).toHaveBeenCalledWith("draft_1", 1);
    expect(container.querySelector('[role="status"]')?.textContent).toMatch(/submitted/i);
    expect(container.textContent).toMatch(/not proof of delivery/i);
    expect(approve()?.disabled ?? true).toBe(true);
  });

  it("does not offer approval for an old version", async () => {
    vi.mocked(getEmail).mockResolvedValue(live({ version: 2 }) as never);
    await show();
    expect(container.textContent).toMatch(/newer version/i);
    expect(approve().disabled).toBe(true);
  });

  it("tells the user an out-of-date approval was refused and keeps nothing sent", async () => {
    vi.mocked(approveEmail).mockRejectedValue(new ApiError(409, "This preview is out of date (version 1). Review the latest draft."));
    await show();
    await click(approve());
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("out of date");
  });

  it("explains an expired draft", async () => {
    vi.mocked(getEmail).mockRejectedValue(new ApiError(404, "draft not found or expired; ask the agent to draft it again"));
    await show();
    expect(container.textContent).toMatch(/expired/i);
    expect(approve().disabled).toBe(true);
  });

  it("reports a failed send and allows a retry, and an unknown outcome without a retry", async () => {
    vi.mocked(approveEmail).mockResolvedValue(live({ status: "failed", error: "Graph refused the message: 403" }) as never);
    await show();
    await click(approve());
    expect(container.querySelector('[role="alert"]')?.textContent).toContain("403");
    expect(approve().disabled).toBe(false);

    vi.mocked(approveEmail).mockResolvedValue(live({ status: "unknown", error: "TimeoutError: slow" }) as never);
    await click(approve());
    expect(container.textContent).toMatch(/may or may not|unknown/i);
    expect(approve().disabled).toBe(true);
  });

  it("is what an APPROVAL part with action send_email renders", async () => {
    await act(async () => {
      root.render(<MessagePartRenderer part={{ type: "APPROVAL", content: card } as never} />);
    });
    expect(container.textContent).toContain("Net sales last week");
  });
});
