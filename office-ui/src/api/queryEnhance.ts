import { ApiError, api, type HttpClient } from "./http";

export interface EnhanceQueryResult {
  original: string;
  enhanced: string;
}

export async function enhanceQuery(
  query: string,
  http: HttpClient = api,
): Promise<EnhanceQueryResult> {
  const trimmed = query.trim();
  if (!trimmed) {
    throw new Error("Type a question first");
  }
  try {
    return await http.request<EnhanceQueryResult>("/v1/query/enhance", {
      method: "POST",
      body: JSON.stringify({ query: trimmed }),
    });
  } catch (error) {
    if (error instanceof ApiError) {
      throw new Error(error.detail || "Unable to enhance query");
    }
    throw error;
  }
}
