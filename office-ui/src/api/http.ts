export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

export interface HttpClientOptions {
  baseUrl?: string;
  getToken?: () => string | null;
  fetchImpl?: typeof fetch;
}

export class HttpClient {
  readonly baseUrl: string;
  private readonly getToken: () => string | null;
  readonly fetchImpl: typeof fetch;

  constructor(options: HttpClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? "";
    this.getToken = options.getToken ?? (() => null);
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  }

  headers(extra?: HeadersInit): Headers {
    const headers = new Headers(extra);
    return headers;
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers = this.headers(init.headers);
    if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await this.fetchImpl(`${this.baseUrl}${path}`, { ...init, headers });
    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`.trim();
      try {
        const body = await response.json() as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch { /* non-JSON error */ }
      throw new ApiError(response.status, detail);
    }
    if (response.status === 204) return undefined as T;
    return await response.json() as T;
  }
}

export const api = new HttpClient();
