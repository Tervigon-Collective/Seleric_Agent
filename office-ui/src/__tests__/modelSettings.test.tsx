import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { modelSettingsApi, type ModelSettings as Settings } from "../api/modelSettings";
import { ModelSettings } from "../components/ModelSettings";

const settings: Settings = {
  version: 3,
  can_save_keys: true,
  providers: [{ id: "openai", name: "OpenAI", base_url: "https://api.openai.com/v1" }, { id: "compatible", name: "OpenAI-compatible server", base_url: "" }],
  connections: [{ id: "azure", provider: "azure", label: "Azure (server)", base_url: "", builtin: true, key_hint: "" }],
  main: [],
  helper: [],
  defaults: { main: [{ connection: "azure", model: "gpt-5-mini" }], helper: [] },
  max_fallbacks: 10,
  thinking_levels: ["minimal", "low", "medium", "high"],
};

let container: HTMLDivElement;
let root: Root;
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const button = (text: string) => [...document.querySelectorAll("button")].find((b) => b.textContent === text)!;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(modelSettingsApi, "get").mockResolvedValue(settings);
  vi.spyOn(modelSettingsApi, "models").mockResolvedValue({ models: ["gpt-5-mini", "gpt-4o-code"] });
});
afterEach(() => { act(() => root.unmount()); container.remove(); vi.restoreAllMocks(); });

describe("Settings → Models", () => {
  it("shows the server default, then saves a main model with a fallback and thinking on the first only", async () => {
    const save = vi.spyOn(modelSettingsApi, "saveRoles").mockImplementation(async (b) => ({ ...settings, ...b, version: 4 }));
    await act(async () => root.render(<ModelSettings open onClose={() => {}} />));
    await flush();
    expect(document.body.textContent).toContain("Server default: Azure (server) · gpt-5-mini");
    await act(async () => button("Choose model").click());
    await act(async () => button("Add fallback").click());
    const inputs = [...document.querySelectorAll<HTMLInputElement>('input[aria-label="Model"]')];
    expect(inputs).toHaveLength(2);
    expect(document.querySelectorAll('select[aria-label="Thinking"]')).toHaveLength(1);
    await act(async () => button("Save").click());
    await flush();
    expect(save).toHaveBeenCalledWith({ version: 3, main: [{ connection: "azure", model: "" }, { connection: "azure", model: "" }], helper: [] });
    expect(document.body.textContent).toContain("Saved");
  });

  it("asks for a server URL only for a compatible server and never shows the typed key back", async () => {
    await act(async () => root.render(<ModelSettings open onClose={() => {}} />));
    await flush();
    await act(async () => button("Add provider").click());
    expect(document.querySelector('input[aria-label="Server URL"]')).toBeNull();
    expect(document.querySelector<HTMLInputElement>('input[aria-label="API key"]')!.type).toBe("password");
  });
});
