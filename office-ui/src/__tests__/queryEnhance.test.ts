import { describe, expect, it, vi } from "vitest";
import { ApiError, HttpClient } from "../api/http";
import { enhanceQuery } from "../api/queryEnhance";

describe("enhanceQuery", () => {
  it("posts the draft and returns the enhanced text", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          original: "which products highest return",
          enhanced: "Which product variants had the highest returned units in the last 7 days?",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    const http = new HttpClient({ fetchImpl });
    const result = await enhanceQuery("which products highest return", http);
    expect(result.enhanced).toMatch(/returned units/i);
    expect(fetchImpl).toHaveBeenCalledWith(
      "/v1/query/enhance",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("rejects blank drafts before calling the API", async () => {
    const fetchImpl = vi.fn();
    await expect(enhanceQuery("   ", new HttpClient({ fetchImpl }))).rejects.toThrow(
      /type a question/i,
    );
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("surfaces API errors", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "authentication required" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(enhanceQuery("sales", new HttpClient({ fetchImpl }))).rejects.toThrow(
      /authentication required/i,
    );
    await expect(enhanceQuery("sales", new HttpClient({ fetchImpl }))).rejects.toBeInstanceOf(
      Error,
    );
    expect(ApiError).toBeDefined();
  });
});
