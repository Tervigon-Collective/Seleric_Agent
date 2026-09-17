import type { MissionRef, OfficeSnapshot } from "../types";
import { api, type HttpClient } from "./http";

export class MissionsApi {
  constructor(private readonly http: HttpClient = api) {}

  async list(): Promise<MissionRef[]> {
    const result = await this.http.request<{ missions: MissionRef[] }>("/v1/office/missions");
    return result.missions ?? [];
  }

  snapshot(missionId: string): Promise<OfficeSnapshot> {
    return this.http.request(
      `/v1/office/missions/${encodeURIComponent(missionId)}/snapshot`,
    );
  }
}

export const missionsApi = new MissionsApi();
