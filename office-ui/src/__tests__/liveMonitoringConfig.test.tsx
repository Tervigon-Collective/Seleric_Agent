import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { liveConfigApi, type LiveConfig, type LiveConfigView, type LiveSchema } from "../api/notifications";
import { LiveMonitoringConfig, paramsDiff, slug } from "../components/ops/LiveMonitoringConfig";

const schema: LiveSchema = {
  levels: ["account", "campaign", "adset", "ad"], severities: ["critical", "warning", "info"],
  rules: [{
    id: "roas_below", title: "ROAS below target / break-even", description: "3-day ROAS against the guardrails.", tier: "performance",
    platforms: ["meta"], levels: ["campaign", "adset", "ad"], cadence: "1h", actions: ["budget_down"], playbook_ref: "", efficiency: true, rollup: true,
    params_schema: { properties: { min_spend_inr: { type: "number", description: "Minimum 3-day spend", minimum: 0 } } }, params_defaults: { min_spend_inr: 3000 },
  }],
  actions: [{ platform: "meta", id: "open_agent", title: "Open in Marketing Agent", levels: ["account", "campaign", "adset", "ad"], risk: "link" }],
  platforms: { meta: { fields: [{ path: "metrics.today.spend", type: "number", levels: ["ad"], label: "Spend (today)", unit: "INR" }] } },
  conditions: { operators: { number: ["gt", "lt"] }, no_value_operators: ["exists", "missing"], max_conditions: 10, refs: ["guardrail.allowable_cpa"] },
};

const config: LiveConfig = {
  tenants: [{
    tenant_id: "th-meta", name: "Tilting Heads (Meta)", brand_id: 20, platform: "meta", account_id: "act_1685189008684458",
    timezone: "Asia/Kolkata", enabled: true, structure_cache_ttl_min: 30, exclude_entity_ids: [],
    expectations: {
      pixel_id: "1533635213935845", conversion_event: "PURCHASE", display_domain: "tiltingheads.com",
      attribution: [{ event_type: "CLICK_THROUGH", window_days: 7 }, { event_type: "VIEW_THROUGH", window_days: 1 }],
      compliance_name_pattern: "^TH-\\d+-", label_from_name: "^(TH-\\d+-[A-Z0-9]+)",
    },
    actions: { budget_step_pct: 20, max_daily_budget_minor: 100000, min_daily_budget_minor: 10000 },
  }],
  guardrails: { mode: "auto", target_roas_multiple: 1.3, manual: { be_roas_platform: 2.2, target_roas: 2.9, allowable_cpa: 1200 }, campaign_overrides: [] },
  baselines: {}, stats: { confidence: 0.8 }, rules: {}, custom_rules: [],
  notify: { quiet_hours: { start: "23:00", end: "08:00" }, cooldown_ticks: 2, max_narrations_per_tick: 20 },
};
const view: LiveConfigView = { config, defaults: config, custom: false, version: 3, updated_at: null, updated_by: null, error: null };

let container: HTMLDivElement;
let root: Root;
beforeEach(() => { container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(() => { act(() => root.unmount()); container.remove(); vi.restoreAllMocks(); });

describe("live monitoring config", () => {
  it("keeps only changed params and makes safe rule ids", () => {
    expect(paramsDiff({ a: 1, b: 2 }, { a: 1, b: 5 })).toEqual({ b: 5 });
    expect(slug("CPA > 2x on new ads!")).toBe("cpa_2x_on_new_ads");
    expect(slug("3 day spend")).toBe("r_3_day_spend");
  });

  it("draws rules from the schema and saves the edited document with its version", async () => {
    vi.spyOn(liveConfigApi, "schema").mockResolvedValue(schema);
    vi.spyOn(liveConfigApi, "get").mockResolvedValue(view);
    const save = vi.spyOn(liveConfigApi, "save").mockResolvedValue({ ...view, version: 4, custom: true });
    await act(async () => { root.render(<LiveMonitoringConfig />); });
    expect(container.textContent).toContain("ROAS below target / break-even");
    expect(container.textContent).toContain("hourly");

    const toggle = container.querySelector<HTMLButtonElement>('[aria-label="Run ROAS below target / break-even"]')!;
    await act(async () => { toggle.click(); });
    expect(container.textContent).toContain("Unsaved changes");
    const saveButton = [...container.querySelectorAll("button")].find((b) => b.textContent === "Save")!;
    await act(async () => { saveButton.click(); });
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ rules: { roas_below: { enabled: false } } }), 3);
    expect(container.textContent).toContain("Saved.");
  });
});

  it("opens the brand settings dialog from Configure", async () => {
    vi.spyOn(liveConfigApi, "schema").mockResolvedValue(schema);
    vi.spyOn(liveConfigApi, "get").mockResolvedValue(view);
    await act(async () => { root.render(<LiveMonitoringConfig />); });
    expect(container.textContent).toContain("Tilting Heads (Meta)");
    const configure = [...container.querySelectorAll("button")].find((b) => b.textContent === "Configure")!;
    await act(async () => { configure.click(); });
    expect(document.body.textContent).toContain("Brand settings");
    expect(document.body.textContent).toContain("Tracking expectations");
    expect(document.body.textContent).toContain("Budget actions");
  });
