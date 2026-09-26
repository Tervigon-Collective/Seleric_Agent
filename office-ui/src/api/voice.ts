import { ApiError, api, type HttpClient } from "./http";

export interface VoiceToken {
  url: string;
  token: string;
  room: string;
  thread_id: string;
  identity: string;
  expires_in_s: number;
}

export async function mintVoiceToken(threadId: string, http: HttpClient = api): Promise<VoiceToken> {
  try {
    return await http.request<VoiceToken>("/v1/voice/token", {
      method: "POST",
      body: JSON.stringify({ thread_id: threadId }),
    });
  } catch (error) {
    if (error instanceof ApiError && error.status === 404 && /voice is not enabled/i.test(error.detail)) {
      throw new Error("Voice is not enabled on the server");
    }
    if (error instanceof ApiError && error.status === 503) throw new Error("Voice is not configured on the server");
    throw error;
  }
}
