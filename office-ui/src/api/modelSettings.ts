import { api, type HttpClient } from "./http";

export interface ModelProvider { id: string; name: string; base_url: string }
export interface ModelConnection { id: string; provider: string; label: string; base_url: string; builtin: boolean; key_hint: string }
export interface ModelChoice { connection: string; model: string; thinking?: string }
export interface ModelSettings {
  version: number;
  can_save_keys: boolean;
  providers: ModelProvider[];
  connections: ModelConnection[];
  main: ModelChoice[];
  helper: ModelChoice[];
  defaults: { main: ModelChoice[]; helper: ModelChoice[] };
  max_fallbacks: number;
  thinking_levels: string[];
}

const enc = encodeURIComponent;

export class ModelSettingsApi {
  constructor(private readonly http: HttpClient = api) {}

  get(): Promise<ModelSettings> {
    return this.http.request("/v1/model-settings");
  }
  connect(body: { provider: string; api_key: string; base_url?: string }): Promise<{ connection: ModelConnection; models: string[] }> {
    return this.http.request("/v1/model-settings/connections", { method: "POST", body: JSON.stringify(body) });
  }
  disconnect(id: string): Promise<{ ok: boolean }> {
    return this.http.request(`/v1/model-settings/connections/${enc(id)}`, { method: "DELETE" });
  }
  models(id: string): Promise<{ models: string[] }> {
    return this.http.request(`/v1/model-settings/connections/${enc(id)}/models`);
  }
  saveRoles(body: { version: number; main: ModelChoice[]; helper: ModelChoice[] }): Promise<ModelSettings> {
    return this.http.request("/v1/model-settings/roles", { method: "PUT", body: JSON.stringify(body) });
  }
}

export const modelSettingsApi = new ModelSettingsApi();
