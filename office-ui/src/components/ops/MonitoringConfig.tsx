import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardFooter, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { useMemo, useState } from "react";
import type { AlertRule, DomainView, MetricView } from "../../api/monitoring";
import { isDirty, useMonitoringStore, type DraftMetric } from "../../stores/monitoring";
import { thresholdUnit } from "./format";

const POLARITY: { value: string; label: string }[] = [
  { value: "", label: "Use the catalogue" },
  { value: "up", label: "Up is bad" },
  { value: "down", label: "Down is bad" },
  { value: "none", label: "Neither, watch only" },
];

function RuleEditor({ domainId, m, unit }: { domainId: string; m: DraftMetric; unit: string }) {
  const update = useMonitoringStore((s) => s.updateMetric);
  const kind = m.alert?.on ?? "anomaly";
  const threshold = m.alert?.on === "worse_by" ? m.alert.threshold : 10;
  const set = (alert: AlertRule | null) => update(domainId, m.metric_id, { alert });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <NativeSelect
        aria-label={`When to alert for ${m.metric_id}`}
        value={kind}
        onChange={(e) => set(e.target.value === "worse_by" ? { on: "worse_by", threshold } : null)}
      >
        <NativeSelectOption value="anomaly">Alert when outside its normal range the bad way</NativeSelectOption>
        <NativeSelectOption value="worse_by">Alert when worse than expected by</NativeSelectOption>
      </NativeSelect>
      {kind === "worse_by" && (
        <div className="flex items-center gap-1.5">
          <Input
            className="w-24"
            type="number" min={0.1} max={1000} step={0.5} value={threshold}
            aria-label={`Alert threshold for ${m.metric_id}`}
            onChange={(e) => set({ on: "worse_by", threshold: Number(e.target.value) })}
          />
          <span className="text-sm text-muted-foreground">{unit}</span>
        </div>
      )}
    </div>
  );
}

function SelectedRow({ domain, m, live }: { domain: DomainView; m: DraftMetric; live: MetricView | undefined }) {
  const update = useMonitoringStore((s) => s.updateMetric);
  const toggle = useMonitoringStore((s) => s.toggleMetric);
  const candidate = useMonitoringStore((s) => s.catalogue[domain.id]?.find((c) => c.id === m.metric_id));
  const unit = thresholdUnit(live?.shape, live?.unit ?? candidate?.unit ?? null);
  const polaritySet = m.direction_bad ?? (live?.polarity_known || candidate?.direction_bad ? "catalogue" : null);
  return (
    <li>
      <Card size="sm">
        <CardContent className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <Input
              className="min-w-0 flex-1" value={m.label} maxLength={80}
              placeholder={live?.name || candidate?.name || m.metric_id}
              aria-label={`Name for ${m.metric_id}`}
              onChange={(e) => update(domain.id, m.metric_id, { label: e.target.value })}
            />
            <code className="hidden text-xs text-muted-foreground md:inline">{m.metric_id}</code>
            <Button variant="ghost" size="icon-sm" aria-label={`Stop monitoring ${m.metric_id}`} title="Remove"
              onClick={() => toggle(domain.id, m.metric_id, false)}>×</Button>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <RuleEditor domainId={domain.id} m={m} unit={unit} />
            <div className="flex items-center gap-2">
              <Label htmlFor={`direction-${domain.id}-${m.metric_id}`}>Direction</Label>
              <NativeSelect
                id={`direction-${domain.id}-${m.metric_id}`}
                aria-label={`Bad direction for ${m.metric_id}`}
                value={m.direction_bad ?? ""}
                onChange={(e) => update(domain.id, m.metric_id, { direction_bad: (e.target.value || null) as DraftMetric["direction_bad"] })}
              >{POLARITY.map((p) => <NativeSelectOption key={p.value} value={p.value}>{p.label}</NativeSelectOption>)}</NativeSelect>
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id={`headline-${domain.id}-${m.metric_id}`}
                checked={m.headline}
                aria-label={`Include ${m.metric_id} in the business snapshot`}
                onCheckedChange={(checked) => update(domain.id, m.metric_id, { headline: checked === true })}
              />
              <Label htmlFor={`headline-${domain.id}-${m.metric_id}`}>In business snapshot</Label>
            </div>
          </div>
          {!polaritySet && <Alert><AlertDescription>The catalogue has no direction for this metric; it is watched but never alerted unless you set one.</AlertDescription></Alert>}
        </CardContent>
      </Card>
    </li>
  );
}

function Picker({ domain }: { domain: DomainView }) {
  const [query, setQuery] = useState("");
  const candidates = useMonitoringStore((s) => s.catalogue[domain.id]);
  const draft = useMonitoringStore((s) => s.drafts[domain.id]);
  const cap = useMonitoringStore((s) => s.state?.max_metrics_per_domain ?? 40);
  const toggle = useMonitoringStore((s) => s.toggleMetric);
  const selected = useMemo(() => new Set(draft?.metrics.map((m) => m.metric_id)), [draft]);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (candidates ?? []).filter((c) => !q || c.id.includes(q) || c.name.toLowerCase().includes(q)).slice(0, 80);
  }, [candidates, query]);
  if (!candidates) return <Skeleton className="h-40 w-full" role="status" aria-busy="true" aria-label="Loading the catalogue" />;
  const full = (draft?.metrics.length ?? 0) >= cap;
  return (
    <div className="flex flex-col gap-2">
      <Input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search metrics in this domain" aria-label={`Search ${domain.title} metrics`} />
      {full && <p className="text-sm text-muted-foreground">Limit of {cap} metrics reached: every metric is one query on each refresh. Remove one to add another.</p>}
      <ul className="m-0 flex max-h-72 list-none flex-col gap-1 overflow-y-auto p-0">
        {shown.map((c) => (
          <li key={c.id}>
            <Label className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 font-normal hover:bg-accent">
              <Checkbox
                checked={selected.has(c.id)} disabled={!selected.has(c.id) && full}
                onCheckedChange={(checked) => toggle(domain.id, c.id, checked === true)}
              />
              <span className="min-w-0 flex-1 truncate">{c.name}</span>
              <code className="hidden text-xs text-muted-foreground md:inline">{c.id}</code>
              <span className="text-xs text-muted-foreground">{c.unit ?? ""}</span>
              {!c.direction_bad && <Badge variant="outline" title="No direction in the catalogue">no direction</Badge>}
            </Label>
          </li>
        ))}
      </ul>
      {candidates.length > shown.length && <p className="text-sm text-muted-foreground">Showing {shown.length} of {candidates.length}. Type to narrow.</p>}
    </div>
  );
}

function DomainSection({ domain }: { domain: DomainView }) {
  const draft = useMonitoringStore((s) => s.drafts[domain.id]);
  const busy = useMonitoringStore((s) => s.busy[domain.id]);
  const notice = useMonitoringStore((s) => s.notice[domain.id]);
  const { save, reset, discard, setEnabled, loadCatalogue } = useMonitoringStore.getState();
  const [adding, setAdding] = useState(false);
  const dirty = isDirty(domain, draft);
  const live = new Map(domain.snapshot?.metrics.map((m) => [m.metric_id, m]));
  if (!draft) return null;
  const working = !!busy;
  return (
    <Card id={`mon-${domain.id}`} data-slot="mon-domain" aria-label={domain.title} className="scroll-mt-3">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">{domain.title} <Badge variant="outline">{domain.custom ? "Customised" : "Default"}</Badge></CardTitle>
        <CardDescription>{domain.description}</CardDescription>
        <CardAction className="flex items-center gap-2">
          <Switch id={`enabled-${domain.id}`} checked={draft.enabled} onCheckedChange={(checked) => setEnabled(domain.id, checked)} />
          <Label htmlFor={`enabled-${domain.id}`}>Monitor this domain</Label>
        </CardAction>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <ul className="m-0 flex list-none flex-col gap-2 p-0" aria-label={`Metrics monitored in ${domain.title}`}>
          {draft.metrics.map((m) => <SelectedRow key={m.metric_id} domain={domain} m={m} live={live.get(m.metric_id)} />)}
        </ul>
        {draft.metrics.length === 0 && <p className="text-sm text-muted-foreground">Nothing selected. Add at least one metric to see this domain on the dashboard.</p>}
        <div className="flex flex-col gap-2">
          <Button variant="outline" size="sm" className="self-start" aria-expanded={adding} onClick={() => { setAdding(!adding); void loadCatalogue(domain.id); }}>
            {adding ? "Hide catalogue" : "Add metrics"}
          </Button>
          {adding && <Picker domain={domain} />}
        </div>
      </CardContent>
      <CardFooter className="flex flex-wrap items-center gap-2">
        <Button disabled={!dirty || working} onClick={() => void save(domain.id)}>
          {busy === "saving" ? "Saving…" : busy === "refreshing" ? "Fetching numbers…" : "Save and refresh"}
        </Button>
        <Button variant="outline" disabled={!dirty || working} onClick={() => discard(domain.id)}>Discard changes</Button>
        <Button variant="outline" disabled={!domain.custom || working} onClick={() => void reset(domain.id)}>Back to default</Button>
        {dirty && <span className="text-sm text-muted-foreground">Unsaved changes</span>}
        {notice && <span className={notice.kind === "error" ? "text-sm text-destructive" : "text-sm text-muted-foreground"} role={notice.kind === "error" ? "alert" : "status"}>{notice.text}</span>}
        {domain.updated_by && <span className="text-sm text-muted-foreground">Last saved by {domain.updated_by}</span>}
      </CardFooter>
    </Card>
  );
}

export function MonitoringConfig() {
  const state = useMonitoringStore((s) => s.state);
  const error = useMonitoringStore((s) => s.error);
  if (!state) return error ? <div className="py-8 text-muted-foreground"><p role="alert">{error}</p></div> : <div className="flex flex-col gap-3" role="status" aria-busy="true" aria-label="Loading the configuration">
      <Skeleton className="h-28 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
    </div>;
  return (
    <div className="flex flex-col gap-4">
      <p className="max-w-prose text-sm text-muted-foreground">
        Pick what each domain watches and when to raise an alert. Changes apply to the dashboard and to the business snapshot that
        answers “how is the business doing”. Metrics are read from the live catalogue, up to {state.max_metrics_per_domain} per domain.
      </p>
      {state.domains.map((d) => <DomainSection key={d.id} domain={d} />)}
    </div>
  );
}
