import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../../api/http";
import {
  liveConfigApi,
  type Condition,
  type CustomRule,
  type DryRun,
  type FieldSchema,
  type LiveConfig,
  type LiveConfigView,
  type LiveSchema,
  type LiveTenant,
  type RuleSchema,
  type RuleSetting,
  type Severity,
} from "../../api/notifications";
import { BrandSettingsDialog } from "./BrandSettingsDialog";

const CADENCE: Record<string, string> = { "5m": "every 5 min", "1h": "hourly", daily: "daily" };
const OP_LABEL: Record<string, string> = {
  gt: "above", gte: "at least", lt: "below", lte: "at most", eq: "is", ne: "is not", in: "is one of", not_in: "is none of",
  contains: "contains", not_contains: "does not contain", exists: "is set", missing: "is not set",
};

const message = (e: unknown) => (e instanceof ApiError ? e.detail : e instanceof Error ? e.message : "Request failed");
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

/** Only params that differ from the rule's defaults are saved, so a later change to a default still reaches this rule. */
export function paramsDiff(defaults: Record<string, unknown>, values: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(values).filter(([k, v]) => v !== undefined && v !== "" && v !== defaults[k]));
}

export function slug(title: string): string {
  const s = title.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40);
  return /^[a-z]/.test(s) ? s : `r_${s}`.slice(0, 40);
}

function setting(draft: LiveConfig, id: string): RuleSetting {
  return draft.rules[id] ?? {};
}

function withSetting(draft: LiveConfig, id: string, patch: RuleSetting): LiveConfig {
  const next = { ...setting(draft, id), ...patch };
  const empty = next.enabled !== false && !next.severity && !Object.keys(next.params ?? {}).length;
  const rules = { ...draft.rules };
  if (empty) delete rules[id]; else rules[id] = next;
  return { ...draft, rules };
}

function NumberField({ id, label, value, onChange, min, max, step = "any", hint }: {
  id: string; label: string; value: number | undefined; onChange: (v: number | undefined) => void; min?: number; max?: number; step?: number | "any"; hint?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id} className="text-xs font-normal text-muted-foreground">{label}</Label>
      <Input id={id} type="number" className="h-8 w-32" min={min} max={max} step={step} value={value ?? ""} placeholder={hint}
        onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />
    </div>
  );
}

function RuleRow({ rule, draft, onChange }: { rule: RuleSchema; draft: LiveConfig; onChange: (d: LiveConfig) => void }) {
  const [open, setOpen] = useState(false);
  const s = setting(draft, rule.id);
  const params = { ...rule.params_defaults, ...(s.params ?? {}) };
  const props = Object.entries(rule.params_schema.properties ?? {});
  const setParam = (k: string, v: unknown) =>
    onChange(withSetting(draft, rule.id, { params: paramsDiff(rule.params_defaults, { ...params, [k]: v ?? rule.params_defaults[k] }) }));
  return (
    <li className="rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Switch id={`rule-${rule.id}`} checked={s.enabled !== false} onCheckedChange={(on) => onChange(withSetting(draft, rule.id, { enabled: on }))} aria-label={`Run ${rule.title}`} />
        <Label htmlFor={`rule-${rule.id}`} className="font-medium">{rule.title}</Label>
        <Badge variant="outline">{CADENCE[rule.cadence]}</Badge>
        {Object.keys(s.params ?? {}).length > 0 && <Badge variant="secondary">tuned</Badge>}
        <span className="ml-auto flex items-center gap-2">
          <NativeSelect aria-label={`Severity for ${rule.title}`} value={s.severity ?? ""} onChange={(e) => onChange(withSetting(draft, rule.id, { severity: (e.target.value || null) as Severity | null }))}>
            <NativeSelectOption value="">Rule decides severity</NativeSelectOption>
            <NativeSelectOption value="critical">Always critical</NativeSelectOption>
            <NativeSelectOption value="warning">Always warning</NativeSelectOption>
            <NativeSelectOption value="info">Always info</NativeSelectOption>
          </NativeSelect>
          {props.length > 0 && <Button variant="ghost" size="sm" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide thresholds" : "Thresholds"}</Button>}
        </span>
      </div>
      <p className="mt-1 mb-0 text-xs text-muted-foreground">{rule.description}{rule.efficiency ? " Shown as info while the ad set is learning." : ""}</p>
      {open && (
        <div className="mt-3 flex flex-wrap gap-3">
          {props.map(([k, p]) => p.type === "boolean" ? (
            <Label key={k} className="flex items-center gap-2 text-xs font-normal">
              <Checkbox checked={params[k] === true} onCheckedChange={(c) => setParam(k, c === true)} />{p.description || p.title || k}
            </Label>
          ) : (
            <NumberField key={k} id={`p-${rule.id}-${k}`} label={p.description || p.title || k} value={params[k] as number | undefined}
              min={p.minimum ?? p.exclusiveMinimum} max={p.maximum ?? p.exclusiveMaximum} step={p.type === "integer" ? 1 : "any"}
              hint={String(rule.params_defaults[k] ?? "")} onChange={(v) => setParam(k, v)} />
          ))}
        </div>
      )}
    </li>
  );
}

const emptyRule = (): CustomRule => ({
  id: "", title: "", platform: "meta", level: "ad", match: "all", severity: "warning", actions: ["open_agent"], explain_hint: "", enabled: true,
  conditions: [{ field: "metrics.today.spend", op: "gt", value: 0 }],
});

function ValueEditor({ c, field, refs, onChange }: { c: Condition; field: FieldSchema | undefined; refs: string[]; onChange: (c: Condition) => void }) {
  if (!field || ["exists", "missing"].includes(c.op)) return null;
  if (field.type === "number") {
    const isRef = typeof c.value === "object" && c.value !== null;
    const ref = isRef ? (c.value as { ref: string; multiply?: number }) : null;
    return (
      <span className="flex items-center gap-1">
        <NativeSelect aria-label="Compare with" value={ref ? ref.ref : ""} onChange={(e) => onChange({ ...c, value: e.target.value ? { ref: e.target.value, multiply: ref?.multiply ?? 1 } : 0 })}>
          <NativeSelectOption value="">a number</NativeSelectOption>
          {refs.map((r) => <NativeSelectOption key={r} value={r}>{r.replace("guardrail.", "").replace("baseline.", "usual ").replaceAll("_", " ")}</NativeSelectOption>)}
        </NativeSelect>
        {ref
          ? <Input aria-label="Multiplier" className="h-8 w-20" type="number" step="0.1" min={0.01} max={100} value={ref.multiply ?? 1} onChange={(e) => onChange({ ...c, value: { ...ref, multiply: Number(e.target.value) } })} />
          : <Input aria-label="Value" className="h-8 w-28" type="number" value={Number(c.value ?? 0)} onChange={(e) => onChange({ ...c, value: Number(e.target.value) })} />}
        {ref && <span className="text-xs text-muted-foreground">×</span>}
      </span>
    );
  }
  if (field.type === "bool") {
    return <NativeSelect aria-label="Value" value={String(c.value === true)} onChange={(e) => onChange({ ...c, value: e.target.value === "true" })}><NativeSelectOption value="true">yes</NativeSelectOption><NativeSelectOption value="false">no</NativeSelectOption></NativeSelect>;
  }
  const list = c.op === "in" || c.op === "not_in";
  return <Input aria-label="Value" className="h-8 w-48" value={list ? (Array.isArray(c.value) ? c.value.join(", ") : "") : String(c.value ?? "")}
    placeholder={list ? "comma separated" : ""} onChange={(e) => onChange({ ...c, value: list ? e.target.value.split(",").map((x) => x.trim()).filter(Boolean) : e.target.value })} />;
}

function CustomRuleEditor({ rule, schema, onChange, onRemove }: { rule: CustomRule; schema: LiveSchema; onChange: (r: CustomRule) => void; onRemove: () => void }) {
  const fields = schema.platforms[rule.platform]?.fields.filter((f) => f.levels.includes(rule.level)) ?? [];
  const actions = schema.actions.filter((a) => a.platform === rule.platform && a.levels.includes(rule.level));
  const setCond = (i: number, c: Condition) => onChange({ ...rule, conditions: rule.conditions.map((x, j) => (j === i ? c : x)) });
  return (
    <li className="flex flex-col gap-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Switch checked={rule.enabled} onCheckedChange={(on) => onChange({ ...rule, enabled: on })} aria-label={`Run ${rule.title || "this rule"}`} />
        <Input aria-label="Rule name" className="h-8 min-w-0 flex-1" maxLength={120} value={rule.title} placeholder="What this rule catches"
          onChange={(e) => onChange({ ...rule, title: e.target.value, id: rule.id || "" })} />
        <NativeSelect aria-label="Level" value={rule.level} onChange={(e) => onChange({ ...rule, level: e.target.value, conditions: [{ field: "metrics.today.spend", op: "gt", value: 0 }], actions: ["open_agent"] })}>
          {schema.levels.map((l) => <NativeSelectOption key={l} value={l}>{l === "adset" ? "Ad set" : l[0].toUpperCase() + l.slice(1)}</NativeSelectOption>)}
        </NativeSelect>
        <NativeSelect aria-label="Severity" value={rule.severity} onChange={(e) => onChange({ ...rule, severity: e.target.value as Severity })}>
          {schema.severities.map((s) => <NativeSelectOption key={s} value={s}>{s}</NativeSelectOption>)}
        </NativeSelect>
        <Button variant="ghost" size="sm" onClick={onRemove}>Remove</Button>
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        Alert when
        <NativeSelect aria-label="Match" value={rule.match} onChange={(e) => onChange({ ...rule, match: e.target.value as "all" | "any" })}>
          <NativeSelectOption value="all">all</NativeSelectOption><NativeSelectOption value="any">any</NativeSelectOption>
        </NativeSelect>
        of these hold:
      </div>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {rule.conditions.map((c, i) => {
          const field = fields.find((f) => f.path === c.field);
          const ops = schema.conditions.operators[field?.type ?? "number"] ?? [];
          return (
            <li key={i} className="flex flex-wrap items-center gap-1.5">
              <NativeSelect aria-label="Field" value={c.field} onChange={(e) => {
                const f = fields.find((x) => x.path === e.target.value);
                const op = schema.conditions.operators[f?.type ?? "number"][0];
                setCond(i, { field: e.target.value, op, value: f?.type === "number" ? 0 : f?.type === "bool" ? true : "" });
              }}>
                {fields.map((f) => <NativeSelectOption key={f.path} value={f.path}>{f.label}</NativeSelectOption>)}
              </NativeSelect>
              <NativeSelect aria-label="Operator" value={c.op} onChange={(e) => setCond(i, { ...c, op: e.target.value })}>
                {ops.map((o) => <NativeSelectOption key={o} value={o}>{OP_LABEL[o] ?? o}</NativeSelectOption>)}
              </NativeSelect>
              <ValueEditor c={c} field={field} refs={schema.conditions.refs} onChange={(n) => setCond(i, n)} />
              {rule.conditions.length > 1 && <Button variant="ghost" size="icon-sm" aria-label="Remove condition" onClick={() => onChange({ ...rule, conditions: rule.conditions.filter((_, j) => j !== i) })}>×</Button>}
            </li>
          );
        })}
      </ul>
      {rule.conditions.length < schema.conditions.max_conditions && (
        <Button variant="outline" size="sm" className="self-start" onClick={() => onChange({ ...rule, conditions: [...rule.conditions, { field: fields[0]?.path ?? "metrics.today.spend", op: "gt", value: 0 }] })}>Add condition</Button>
      )}
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <span className="text-muted-foreground">Offer:</span>
        {actions.map((a) => (
          <Label key={a.id} className="flex items-center gap-1.5 font-normal">
            <Checkbox checked={rule.actions.includes(a.id)} onCheckedChange={(c) => onChange({ ...rule, actions: c === true ? [...rule.actions, a.id] : rule.actions.filter((x) => x !== a.id) })} />{a.title}
          </Label>
        ))}
      </div>
      <Input aria-label="Hint for the notification" className="h-8" maxLength={300} value={rule.explain_hint} placeholder="Optional: what to tell the person when it fires"
        onChange={(e) => onChange({ ...rule, explain_hint: e.target.value })} />
    </li>
  );
}

function DryRunResult({ result }: { result: DryRun }) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border bg-muted/40 p-3 text-sm" role="status">
      {result.tenants.map((t) => t.error ? <p key={t.tenant_id} className="m-0">{t.error}</p> : (
        <div key={t.tenant_id}>
          <p className="m-0 font-medium">On the last check ({t.snapshot_at ? new Date(t.snapshot_at).toLocaleTimeString() : ""}) this would flag {t.counts?.critical ?? 0} critical, {t.counts?.warning ?? 0} warning, {t.counts?.info ?? 0} info.</p>
          <ul className="m-0 mt-1 flex list-none flex-col gap-0.5 p-0 text-xs text-muted-foreground">
            {(t.findings ?? []).slice(0, 12).map((f, i) => <li key={i}><b className="font-medium text-foreground">{f.title}</b> · {f.path.join(" › ") || "Account"}: {f.summary}</li>)}
          </ul>
        </div>
      ))}
    </div>
  );
}

export function LiveMonitoringConfig() {
  const [schema, setSchema] = useState<LiveSchema | null>(null);
  const [view, setView] = useState<LiveConfigView | null>(null);
  const [draft, setDraft] = useState<LiveConfig | null>(null);
  const [busy, setBusy] = useState<"saving" | "resetting" | "checking" | null>(null);
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [dry, setDry] = useState<DryRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [brandEdit, setBrandEdit] = useState<LiveTenant | null>(null);

  const adopt = (v: LiveConfigView) => { setView(v); setDraft(clone(v.config)); setDry(null); };
  useEffect(() => {
    let live = true;
    Promise.all([liveConfigApi.schema(), liveConfigApi.get()])
      .then(([s, v]) => { if (live) { setSchema(s); adopt(v); } })
      .catch((e) => { if (live) setError(message(e)); });
    return () => { live = false; };
  }, []);

  const dirty = useMemo(() => !!view && !!draft && JSON.stringify(view.config) !== JSON.stringify(draft), [view, draft]);
  if (error) return <Card aria-label="Live ads monitoring"><CardContent><p role="alert" className="text-sm text-muted-foreground">Live ads monitoring is unavailable: {error}</p></CardContent></Card>;
  if (!schema || !view || !draft) return <Skeleton className="h-40 w-full" role="status" aria-busy="true" aria-label="Loading live monitoring" />;

  const g = draft.guardrails;
  const setG = (patch: Partial<LiveConfig["guardrails"]>) => setDraft({ ...draft, guardrails: { ...g, ...patch } });
  const setManual = (k: keyof LiveConfig["guardrails"]["manual"], v: number | undefined) => setG({ manual: { ...g.manual, [k]: v ?? 0 } });
  const tiers = (["performance", "compliance"] as const).map((t) => ({ tier: t, rules: schema.rules.filter((r) => r.tier === t) }));
  const withIds = (d: LiveConfig): LiveConfig => {
    const taken = new Set([...schema.rules.map((r) => r.id), ...d.custom_rules.map((r) => r.id).filter(Boolean)]);
    return { ...d, custom_rules: d.custom_rules.map((r) => {
      if (r.id) return r;
      const base = slug(r.title || "rule").slice(0, 36);
      let id = base;
      for (let n = 2; taken.has(id); n++) id = `${base}_${n}`;
      taken.add(id);
      return { ...r, id };
    }) };
  };

  const run = async (kind: "saving" | "resetting" | "checking") => {
    setBusy(kind); setNotice(null);
    try {
      if (kind === "saving") { adopt(await liveConfigApi.save(withIds(draft), view.version)); setNotice({ kind: "ok", text: "Saved. The monitor uses it from its next check." }); }
      if (kind === "resetting") { adopt(await liveConfigApi.reset()); setNotice({ kind: "ok", text: "Back to the shipped defaults." }); }
      if (kind === "checking") setDry(await liveConfigApi.dryRun(withIds(draft)));
    } catch (e) {
      setNotice({ kind: "error", text: e instanceof ApiError && e.status === 409 ? "Someone else saved meanwhile. Discard and reapply your change." : message(e) });
    } finally { setBusy(null); }
  };

  return (
    <Card id="mon-live" aria-label="Live ads monitoring" className="scroll-mt-3">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 font-serif text-xl font-normal">Live ads monitoring <Badge variant="outline">{view.custom ? "Customised" : "Default"}</Badge></CardTitle>
        <CardDescription>Every active Meta campaign, ad set and ad, checked every 5 minutes. Alerts land in the Notifications tab. Use Configure on a brand for account, pixel, attribution and budget caps.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {view.error && <Alert><AlertDescription>{view.error}</AlertDescription></Alert>}

        <section aria-label="Brands" className="flex flex-col gap-2">
          <h3 className="m-0 text-sm font-medium">Brands</h3>
          <p className="m-0 text-xs text-muted-foreground">Account, pixel, attribution, playbook naming and budget action caps for each monitored brand.</p>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {draft.tenants.map((t) => (
              <li key={t.tenant_id} className="flex flex-wrap items-center gap-2 rounded-lg border p-3">
                <Switch id={`ten-${t.tenant_id}`} checked={t.enabled}
                  onCheckedChange={(on) => setDraft({ ...draft, tenants: draft.tenants.map((x) => (x.tenant_id === t.tenant_id ? { ...x, enabled: on } : x)) })}
                  aria-label={`Monitor ${t.name}`} />
                <div className="min-w-0 flex-1">
                  <Label htmlFor={`ten-${t.tenant_id}`} className="font-medium">{t.name || t.tenant_id}</Label>
                  <p className="m-0 text-xs text-muted-foreground truncate">
                    brand {t.brand_id} · {t.account_id} · {t.timezone}
                    {t.expectations.pixel_id ? ` · pixel ${t.expectations.pixel_id}` : ""}
                  </p>
                </div>
                <Button variant="outline" size="sm" onClick={() => setBrandEdit(t)}>Configure</Button>
              </li>
            ))}
          </ul>
          {brandEdit && (
            <BrandSettingsDialog
              tenant={brandEdit}
              open
              onOpenChange={(o) => { if (!o) setBrandEdit(null); }}
              onApply={(next) => {
                setDraft({ ...draft, tenants: draft.tenants.map((x) => (x.tenant_id === next.tenant_id ? next : x)) });
                setBrandEdit(null);
              }}
            />
          )}
        </section>

        <section aria-label="Guardrails" className="flex flex-col gap-2">
          <h3 className="m-0 text-sm font-medium">Profit guardrails</h3>
          <p className="m-0 text-xs text-muted-foreground">ROAS below target is a warning; below break-even is critical. Auto recomputes them nightly from finance (contribution margin, net AOV).</p>
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex flex-col gap-1">
              <Label htmlFor="g-mode" className="text-xs font-normal text-muted-foreground">Source</Label>
              <NativeSelect id="g-mode" value={g.mode} onChange={(e) => setG({ mode: e.target.value as "auto" | "manual" })}>
                <NativeSelectOption value="auto">Auto from finance</NativeSelectOption>
                <NativeSelectOption value="manual">Set by hand</NativeSelectOption>
              </NativeSelect>
            </div>
            <NumberField id="g-mult" label="Target = break-even ×" value={g.target_roas_multiple} min={1} max={5} step={0.05} onChange={(v) => setG({ target_roas_multiple: v ?? 1.3 })} />
            <NumberField id="g-be" label={g.mode === "auto" ? "Break-even ROAS (until first run)" : "Break-even ROAS"} value={g.manual.be_roas_platform} min={0.1} max={50} step={0.05} onChange={(v) => setManual("be_roas_platform", v)} />
            <NumberField id="g-target" label="Target ROAS" value={g.manual.target_roas} min={0.1} max={50} step={0.05} onChange={(v) => setManual("target_roas", v)} />
            <NumberField id="g-cpa" label="Allowable CPA (₹)" value={g.manual.allowable_cpa} min={1} step={10} onChange={(v) => setManual("allowable_cpa", v)} />
          </div>
        </section>

        <section aria-label="Judgement" className="flex flex-wrap items-end gap-3">
          <NumberField id="s-conf" label="Confidence before alerting (0.5–0.99)" value={draft.stats.confidence} min={0.5} max={0.99} step={0.01} onChange={(v) => setDraft({ ...draft, stats: { ...draft.stats, confidence: v ?? 0.8 } })} />
          <NumberField id="n-cool" label="Checks before resolving" value={draft.notify.cooldown_ticks} min={0} max={12} step={1} onChange={(v) => setDraft({ ...draft, notify: { ...draft.notify, cooldown_ticks: v ?? 2 } })} />
          <div className="flex flex-col gap-1">
            <Label className="text-xs font-normal text-muted-foreground">Quiet hours (only critical pages)</Label>
            <div className="flex items-center gap-1">
              <Input aria-label="Quiet from" type="time" className="h-8 w-28" value={draft.notify.quiet_hours?.start ?? ""} onChange={(e) => setDraft({ ...draft, notify: { ...draft.notify, quiet_hours: e.target.value ? { start: e.target.value, end: draft.notify.quiet_hours?.end ?? "08:00" } : null } })} />
              <span className="text-xs text-muted-foreground">to</span>
              <Input aria-label="Quiet until" type="time" className="h-8 w-28" value={draft.notify.quiet_hours?.end ?? ""} disabled={!draft.notify.quiet_hours} onChange={(e) => setDraft({ ...draft, notify: { ...draft.notify, quiet_hours: { start: draft.notify.quiet_hours?.start ?? "23:00", end: e.target.value } } })} />
            </div>
          </div>
        </section>

        {tiers.map(({ tier, rules }) => (
          <section key={tier} aria-label={tier === "performance" ? "Performance rules" : "Playbook rules"} className="flex flex-col gap-2">
            <h3 className="m-0 text-sm font-medium">{tier === "performance" ? "Performance" : "Playbook setup"}</h3>
            {tier === "compliance" && <p className="m-0 text-xs text-muted-foreground">Setup rules apply to campaigns named like the playbook; status and pixel rules apply to every campaign.</p>}
            <ul className="m-0 flex list-none flex-col gap-2 p-0">
              {rules.map((r) => <RuleRow key={r.id} rule={r} draft={draft} onChange={setDraft} />)}
            </ul>
          </section>
        ))}

        <section aria-label="Your rules" className="flex flex-col gap-2">
          <h3 className="m-0 text-sm font-medium">Your rules</h3>
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {draft.custom_rules.map((r, i) => (
              <CustomRuleEditor key={i} rule={r} schema={schema}
                onChange={(n) => setDraft({ ...draft, custom_rules: draft.custom_rules.map((x, j) => (j === i ? n : x)) })}
                onRemove={() => setDraft({ ...draft, custom_rules: draft.custom_rules.filter((_, j) => j !== i) })} />
            ))}
          </ul>
          <Button variant="outline" size="sm" className="self-start" onClick={() => setDraft({ ...draft, custom_rules: [...draft.custom_rules, emptyRule()] })}>Add a rule</Button>
        </section>
        {dry && <DryRunResult result={dry} />}
      </CardContent>
      <CardFooter className="flex flex-wrap items-center gap-2">
        <Button disabled={!dirty || !!busy} onClick={() => void run("saving")}>{busy === "saving" ? "Saving…" : "Save"}</Button>
        <Button variant="outline" disabled={!!busy} onClick={() => void run("checking")}>{busy === "checking" ? "Checking…" : "Preview on last check"}</Button>
        <Button variant="outline" disabled={!dirty || !!busy} onClick={() => { setDraft(clone(view.config)); setNotice(null); setDry(null); }}>Discard changes</Button>
        <Button variant="outline" disabled={!view.custom || !!busy} onClick={() => void run("resetting")}>Back to default</Button>
        {dirty && <span className="text-sm text-muted-foreground">Unsaved changes</span>}
        {notice && <span className={notice.kind === "error" ? "text-sm text-destructive" : "text-sm text-muted-foreground"} role={notice.kind === "error" ? "alert" : "status"}>{notice.text}</span>}
        {view.updated_by && <span className="text-sm text-muted-foreground">Last saved by {view.updated_by}</span>}
      </CardFooter>
    </Card>
  );
}
