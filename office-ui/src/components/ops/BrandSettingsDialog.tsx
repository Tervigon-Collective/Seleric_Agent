import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import { useEffect, useState, type ReactNode } from "react";
import type { AttributionWindow, LiveTenant } from "../../api/notifications";

const EVENT_TYPES: AttributionWindow["event_type"][] = ["CLICK_THROUGH", "VIEW_THROUGH", "ENGAGED_VIDEO_VIEW"];
const EVENT_LABEL: Record<AttributionWindow["event_type"], string> = {
  CLICK_THROUGH: "Click",
  VIEW_THROUGH: "View",
  ENGAGED_VIDEO_VIEW: "Engaged video view",
};

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id} className="text-xs font-normal text-muted-foreground">{label}</Label>
      {children}
      {hint && <p className="m-0 text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-t pt-4 first:border-t-0 first:pt-0">
      <h3 className="m-0 text-sm font-medium">{title}</h3>
      {children}
    </section>
  );
}

/** Full brand / account settings for one live-monitor tenant. Apply writes into the draft; Save on the page persists. */
export function BrandSettingsDialog({
  tenant,
  open,
  onOpenChange,
  onApply,
}: {
  tenant: LiveTenant;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onApply: (tenant: LiveTenant) => void;
}) {
  const [draft, setDraft] = useState<LiveTenant>(() => clone(tenant));
  useEffect(() => { if (open) setDraft(clone(tenant)); }, [open, tenant]);

  const exp = draft.expectations;
  const act = draft.actions;
  const set = (patch: Partial<LiveTenant>) => setDraft({ ...draft, ...patch });
  const setExp = (patch: Partial<LiveTenant["expectations"]>) => set({ expectations: { ...exp, ...patch } });
  const setAct = (patch: Partial<LiveTenant["actions"]>) => set({ actions: { ...act, ...patch } });
  const setAttr = (i: number, patch: Partial<AttributionWindow>) =>
    setExp({ attribution: exp.attribution.map((a, j) => (j === i ? { ...a, ...patch } : a)) });

  const excludeText = draft.exclude_entity_ids.join(", ");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90dvh] flex-col gap-0 overflow-hidden sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Brand settings</DialogTitle>
          <DialogDescription>
            {tenant.name || tenant.tenant_id} · brand {tenant.brand_id}. Changes apply to the draft; use Save on the Live ads tab to keep them.
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-1 py-2">
          <Section title="Account">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2">
                <Switch id="t-enabled" checked={draft.enabled} onCheckedChange={(on) => set({ enabled: on })} />
                <Label htmlFor="t-enabled">Monitor this brand</Label>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field id="t-name" label="Display name">
                <Input id="t-name" className="h-8" maxLength={80} value={draft.name} onChange={(e) => set({ name: e.target.value })} />
              </Field>
              <Field id="t-tz" label="Timezone" hint="Local clock for pacing, quiet hours and daily rules.">
                <Input id="t-tz" className="h-8" value={draft.timezone} onChange={(e) => set({ timezone: e.target.value })} placeholder="Asia/Kolkata" />
              </Field>
              <Field id="t-brand" label="Brand id">
                <Input id="t-brand" className="h-8" type="number" min={1} value={draft.brand_id} onChange={(e) => set({ brand_id: Number(e.target.value) || 1 })} />
              </Field>
              <Field id="t-account" label="Meta account id" hint="act_… from Ads Manager.">
                <Input id="t-account" className="h-8 font-mono text-xs" value={draft.account_id} onChange={(e) => set({ account_id: e.target.value })} />
              </Field>
              <Field id="t-platform" label="Platform">
                <Input id="t-platform" className="h-8" value={draft.platform} disabled />
              </Field>
              <Field id="t-cache" label="Structure cache (minutes)" hint="How long campaign / ad set / ad lists stay between Graph pulls.">
                <Input id="t-cache" className="h-8" type="number" min={1} max={240} value={draft.structure_cache_ttl_min}
                  onChange={(e) => set({ structure_cache_ttl_min: Number(e.target.value) || 30 })} />
              </Field>
            </div>
            <Field id="t-exclude" label="Exclude entity ids" hint="Comma-separated campaign, ad set or ad ids the monitor should ignore.">
              <Input id="t-exclude" className="h-8 font-mono text-xs" value={excludeText} placeholder="12025…, 12026…"
                onChange={(e) => set({ exclude_entity_ids: e.target.value.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean) })} />
            </Field>
          </Section>

          <Section title="Tracking expectations">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field id="t-pixel" label="Pixel id">
                <Input id="t-pixel" className="h-8 font-mono text-xs" value={exp.pixel_id ?? ""} placeholder="15336…"
                  onChange={(e) => setExp({ pixel_id: e.target.value.trim() || null })} />
              </Field>
              <Field id="t-event" label="Conversion event">
                <Input id="t-event" className="h-8" value={exp.conversion_event} onChange={(e) => setExp({ conversion_event: e.target.value.toUpperCase() })} />
              </Field>
              <Field id="t-domain" label="Display domain" hint="Shown on link ads (e.g. tiltingheads.com).">
                <Input id="t-domain" className="h-8" value={exp.display_domain ?? ""}
                  onChange={(e) => setExp({ display_domain: e.target.value.trim() || null })} />
              </Field>
            </div>
            <div className="flex flex-col gap-2">
              <Label className="text-xs font-normal text-muted-foreground">Attribution windows</Label>
              <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
                {exp.attribution.map((a, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-1.5">
                    <NativeSelect aria-label="Event type" value={a.event_type} onChange={(e) => setAttr(i, { event_type: e.target.value as AttributionWindow["event_type"] })}>
                      {EVENT_TYPES.map((t) => <NativeSelectOption key={t} value={t}>{EVENT_LABEL[t]}</NativeSelectOption>)}
                    </NativeSelect>
                    <Input aria-label="Window days" className="h-8 w-20" type="number" min={1} max={28} value={a.window_days}
                      onChange={(e) => setAttr(i, { window_days: Number(e.target.value) || 1 })} />
                    <span className="text-xs text-muted-foreground">days</span>
                    <Button variant="ghost" size="icon-sm" aria-label="Remove window"
                      onClick={() => setExp({ attribution: exp.attribution.filter((_, j) => j !== i) })}>×</Button>
                  </li>
                ))}
              </ul>
              {exp.attribution.length < 4 && (
                <Button variant="outline" size="sm" className="self-start"
                  onClick={() => setExp({ attribution: [...exp.attribution, { event_type: "CLICK_THROUGH", window_days: 7 }] })}>
                  Add window
                </Button>
              )}
            </div>
          </Section>

          <Section title="Playbook naming">
            <Field id="t-comp" label="Compliance name pattern" hint="Only campaigns matching this regex get label / IG / attribution / creative checks. Use .* for all.">
              <Input id="t-comp" className="h-8 font-mono text-xs" value={exp.compliance_name_pattern}
                onChange={(e) => setExp({ compliance_name_pattern: e.target.value })} />
            </Field>
            <Field id="t-label" label="Label from name" hint="One capture group used when attaching the SKU label (e.g. ^(TH-\\d+-[A-Z0-9]+)).">
              <Input id="t-label" className="h-8 font-mono text-xs" value={exp.label_from_name}
                onChange={(e) => setExp({ label_from_name: e.target.value })} />
            </Field>
          </Section>

          <Section title="Budget actions">
            <p className="m-0 text-xs text-muted-foreground">One-click raise / cut on a finding. Amounts are in paise (₹1 = 100). Seleric MCP still enforces its own raise cap.</p>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field id="t-step" label="Step % per click" hint="Max 20.">
                <Input id="t-step" className="h-8" type="number" min={1} max={20} value={act.budget_step_pct}
                  onChange={(e) => setAct({ budget_step_pct: Number(e.target.value) || 20 })} />
              </Field>
              <Field id="t-max" label="Max daily (paise)" hint="₹1,000 = 100000.">
                <Input id="t-max" className="h-8" type="number" min={100} value={act.max_daily_budget_minor}
                  onChange={(e) => setAct({ max_daily_budget_minor: Number(e.target.value) || 100000 })} />
              </Field>
              <Field id="t-min" label="Min daily (paise)">
                <Input id="t-min" className="h-8" type="number" min={100} value={act.min_daily_budget_minor}
                  onChange={(e) => setAct({ min_daily_budget_minor: Number(e.target.value) || 10000 })} />
              </Field>
            </div>
            <p className="m-0 text-xs text-muted-foreground tabular-nums">
              Caps: ₹{(act.min_daily_budget_minor / 100).toLocaleString("en-IN")} – ₹{(act.max_daily_budget_minor / 100).toLocaleString("en-IN")} · ±{act.budget_step_pct}% per approve
            </p>
          </Section>
        </div>

        <DialogFooter className="border-t pt-3">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={() => { onApply(draft); onOpenChange(false); }}>Apply to draft</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
