import { useEffect, useState } from "react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  notificationsApi,
  type ActivityEntry,
  type BotReport,
  type Feed,
  type FindingAction,
  type NotificationItem,
  type Severity,
  type TenantStatus,
  type TimelineEvent,
} from "../../api/notifications";
import { useBotsStore } from "../../stores/bots";
import { useConversationStore } from "../../stores/conversation";
import { useNotificationsStore } from "../../stores/notifications";
import { useShellStore } from "../../stores/shell";
import { ago, formatValue } from "../ops/format";
import { ChevronDownIcon, ThumbDownIcon, ThumbUpIcon } from "../icons";

const SEVERITY: Record<Severity, { label: string; tone: string; bar: string; frame: string }> = {
  critical: { label: "Critical", tone: "bg-[var(--danger-soft)] text-[var(--danger)]", bar: "bg-[var(--danger)]", frame: "border-[var(--danger)]" },
  warning: { label: "Warning", tone: "bg-[var(--warning-soft)] text-[var(--warning)]", bar: "bg-[var(--warning)]", frame: "border-[var(--warning)]" },
  info: { label: "Info", tone: "bg-muted text-muted-foreground", bar: "bg-muted-foreground/40", frame: "" },
};

const STATUS: Record<TenantStatus["status"], { label: string; dot: string }> = {
  ok: { label: "Live", dot: "bg-[var(--success)]" },
  degraded: { label: "Live, some data missing", dot: "bg-[var(--warning)]" },
  stale: { label: "Not checked recently", dot: "bg-[var(--warning)]" },
  down: { label: "Monitor failing", dot: "bg-[var(--danger)]" },
  never_run: { label: "Not started", dot: "bg-muted-foreground" },
};

const RISK: Record<string, string> = {
  low: "Changes setup only; no effect on spend.",
  delivery: "Stops delivery for this entity until it is switched back on.",
  spend: "Changes how much this can spend per day.",
  link: "",
};

const inr = (v: number | null | undefined) => formatValue(v ?? null, "INR");
const x = (v: number | null | undefined) => (v == null ? "–" : `${v.toFixed(2)}x`);
const since = (iso: string | null | undefined, now: string) =>
  iso ? ago(Math.max(0, (Date.parse(now) - Date.parse(iso)) / 1000)) : "";

function SeverityPill({ severity }: { severity: Severity }) {
  return <span className={cn("inline-flex h-5 shrink-0 items-center rounded-full px-2 text-[11px] font-medium", SEVERITY[severity].tone)}>{SEVERITY[severity].label}</span>;
}

/** One line per monitored account: is it watching, when did it last look, and how today is going. */
export function StatusStrip({ tenant }: { tenant: TenantStatus }) {
  const s = STATUS[tenant.status];
  const p = tenant.pulse;
  const g = p?.guardrails;
  const nextIn = tenant.age_s != null && tenant.tick_s ? Math.max(0, tenant.tick_s - tenant.age_s) : null;
  const detail = tenant.status === "down" ? tenant.error
    : tenant.status === "degraded" ? [...(tenant.partial_errors ?? []), ...(tenant.rule_errors ?? []), tenant.config_error].filter(Boolean)[0]
      : null;
  return (
    <section aria-label={`${tenant.name} monitor status`} className="rounded-lg border bg-card p-3">
      <div className="flex items-center gap-2 text-sm">
        <span aria-hidden="true" className={cn("size-2 rounded-full", s.dot, tenant.status === "ok" && "animate-pulse")} />
        <strong className="font-medium">{tenant.name}</strong>
        <span className="text-muted-foreground">{s.label}</span>
        <span className="ml-auto text-xs text-muted-foreground tabular-nums">
          {tenant.age_s != null ? `checked ${ago(tenant.age_s)}` : ""}{nextIn != null && tenant.status === "ok" ? ` · next in ${Math.ceil(nextIn / 60)} min` : ""}
        </span>
      </div>
      {detail && <p className="mt-1 mb-0 truncate text-xs text-[var(--warning)]" title={String(detail)}>{String(detail)}</p>}
      {p && g && (
        <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs sm:grid-cols-4">
          <div><dt className="text-muted-foreground">Spend today</dt>
            <dd className="m-0 font-medium tabular-nums">{inr(p.spend)}{p.pace != null && <span className={cn("ml-1 font-normal", p.pace > 1.3 || p.pace < 0.5 ? "text-[var(--warning)]" : "text-muted-foreground")}>{Math.round(p.pace * 100)}% of pace</span>}</dd></div>
          <div><dt className="text-muted-foreground">Purchases</dt><dd className="m-0 font-medium tabular-nums">{p.purchases ?? "–"}</dd></div>
          <div><dt className="text-muted-foreground">ROAS</dt>
            <dd className={cn("m-0 font-medium tabular-nums", p.roas != null && p.roas < g.be_roas_platform && "text-[var(--danger)]")}>{x(p.roas)}<span className="ml-1 font-normal text-muted-foreground">target {x(g.target_roas)}</span></dd></div>
          <div><dt className="text-muted-foreground">CPA</dt>
            <dd className={cn("m-0 font-medium tabular-nums", p.cpa != null && p.cpa > g.allowable_cpa && "text-[var(--danger)]")}>{inr(p.cpa)}<span className="ml-1 font-normal text-muted-foreground">max {inr(g.allowable_cpa)}</span></dd></div>
        </dl>
      )}
    </section>
  );
}

function Evidence({ evidence }: { evidence: Record<string, unknown> }) {
  const rows = Object.entries(evidence).filter(([, v]) => v !== null && v !== undefined && typeof v !== "object");
  if (!rows.length) return null;
  return (
    <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted-foreground">{k.replaceAll("_", " ")}</dt>
          <dd className="m-0 truncate tabular-nums" title={String(v)}>{typeof v === "number" ? (Math.abs(v) >= 1000 ? Math.round(v).toLocaleString("en-IN") : String(Math.round(v * 1000) / 1000)) : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function GateChips({ item }: { item: NotificationItem }) {
  const g = item.gates ?? {};
  const conf = g.confidence ?? item.confidence;
  const learning = g.learning === true || item.evidence.learning === true;
  const chips: { label: string; ok?: boolean }[] = [];
  if (conf != null) chips.push({ label: `${Math.round(Number(conf) * 100)}% confidence`, ok: Number(conf) >= 0.8 });
  if (g.min_volume_ok != null) chips.push({ label: g.min_volume_ok ? "volume ok" : "low volume", ok: g.min_volume_ok });
  if (learning) chips.push({ label: "learning", ok: false });
  if (g.window) chips.push({ label: g.window });
  if (!chips.length) return null;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1">
      {chips.map((c) => (
        <span key={c.label} className={cn("rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground",
          c.ok === true && "bg-[var(--success-soft)] text-[var(--success)]",
          c.ok === false && "bg-[var(--warning-soft)] text-[var(--warning)]")}>{c.label}</span>
      ))}
    </div>
  );
}

function primaryAction(item: NotificationItem): FindingAction | null {
  const id = item.recommended_action_id;
  if (id) {
    const a = item.actions.find((x) => x.id === id);
    if (a) return a;
  }
  return item.actions.find((a) => a.risk !== "link") ?? item.actions.find((a) => a.id === "open_agent") ?? item.actions[0] ?? null;
}

function kindLabel(kind: string | null | undefined): string {
  switch (kind) {
    case "lifecycle": return "Lifecycle";
    case "diagnosis": return "Diagnosis";
    case "preview": return "Preview";
    case "action": return "Action";
    case "label": return "Label";
    default: return kind || "Event";
  }
}

function FindingTimeline({ events, now }: { events: TimelineEvent[]; now: string }) {
  if (!events.length) return <p className="m-0 text-xs text-muted-foreground">No activity yet.</p>;
  return (
    <ol className="m-0 flex list-none flex-col gap-1.5 p-0">
      {events.map((e, i) => (
        <li key={`${e.at}-${e.kind}-${i}`} className="flex gap-2 text-xs">
          <span className="mt-0.5 size-1.5 shrink-0 rounded-full bg-muted-foreground/50" aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="font-medium">{kindLabel(e.kind)}</span>
            {e.actor ? <span className="text-muted-foreground"> · {e.actor}</span> : null}
            <br />
            <span className="text-muted-foreground">{e.change_summary || e.note}</span>
            {e.request_status ? <span className="text-muted-foreground"> · {e.request_status}</span> : null}
          </span>
          <span className="shrink-0 text-muted-foreground tabular-nums">{since(e.at, now)}</span>
        </li>
      ))}
    </ol>
  );
}

export function FindingCard({ item, now, actionsEnabled }: { item: NotificationItem; now: string; actionsEnabled: boolean }) {
  const [open, setOpen] = useState(false);
  const [more, setMore] = useState(false);
  const [events, setEvents] = useState<TimelineEvent[] | null>(null);
  const [entityEvents, setEntityEvents] = useState<TimelineEvent[] | null>(null);
  const [trailError, setTrailError] = useState<string | null>(null);
  const startAction = useNotificationsStore((s) => s.startAction);
  const label = useNotificationsStore((s) => s.label);
  const resolved = item.state === "resolved";
  const learning = item.gates?.learning === true || item.evidence.learning === true;
  const primary = !resolved ? primaryAction(item) : null;
  const moreActions = primary ? item.actions.filter((a) => a.id !== primary.id) : item.actions;

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setTrailError(null);
    void (async () => {
      try {
        const [findingTrail, entityTrail] = await Promise.all([
          notificationsApi.events(item.finding_id),
          item.level && item.entity_id
            ? notificationsApi.entityActivity(item.level, item.entity_id)
            : Promise.resolve({ events: [] as TimelineEvent[] }),
        ]);
        if (cancelled) return;
        setEvents(findingTrail.events);
        setEntityEvents(entityTrail.events);
      } catch (e) {
        if (!cancelled) setTrailError(e instanceof Error ? e.message : "Could not load activity");
      }
    })();
    return () => { cancelled = true; };
  }, [open, item.finding_id, item.level, item.entity_id]);

  return (
    <article aria-label={`${SEVERITY[item.severity].label}: ${item.title}`} className={cn("relative overflow-hidden rounded-lg border bg-card p-3", resolved ? "opacity-70" : SEVERITY[item.severity].frame)}>
      <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
        {resolved ? <span className="inline-flex h-5 items-center rounded-full bg-[var(--success-soft)] px-2 text-[11px] font-medium text-[var(--success)]">Resolved</span> : <SeverityPill severity={item.severity} />}
        <span>{item.title}</span>
        {learning && <span className="rounded-full bg-muted px-1.5">learning</span>}
        <span className="ml-auto tabular-nums">{resolved ? `resolved ${since(item.resolved_at, now)}` : `since ${since(item.opened_at, now)}`}</span>
      </div>
      <p className="mt-1 mb-0 truncate text-xs text-muted-foreground" title={item.path.join(" › ")}>{item.path.join(" › ") || "Account"}</p>
      <p className="mt-1.5 mb-0 text-sm leading-snug">{item.cause || item.narration || item.summary}</p>
      {(item.impact_inr != null || item.confidence != null) && !resolved && (
        <p className="mt-1 mb-0 text-xs text-muted-foreground tabular-nums">
          {item.impact_inr != null && item.impact_inr > 0 && <>About {inr(item.impact_inr)} at stake</>}
          {item.impact_inr != null && item.impact_inr > 0 && item.confidence != null && " · "}
          {item.confidence != null && <>{Math.round(item.confidence * 100)}% sure</>}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {primary && (
          <Button size="sm" variant={primary.risk === "link" ? "outline" : "default"} className="h-7 text-xs"
            disabled={primary.risk !== "link" && !actionsEnabled}
            title={primary.risk !== "link" && !actionsEnabled ? "One-click actions are switched off" : (item.recommended_summary || primary.title)}
            onClick={() => void startAction(item, primary.id)}>{item.recommended_summary || primary.title}</Button>
        )}
        {!resolved && moreActions.length > 0 && (
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setMore((v) => !v)} aria-expanded={more}>
            More
          </Button>
        )}
        <span className="ml-auto flex items-center gap-0.5">
          <Button variant="ghost" size="icon-sm" aria-label="Useful" aria-pressed={item.label === "useful"} className={cn(item.label === "useful" && "text-[var(--success)]")} onClick={() => void label(item, "useful")}><ThumbUpIcon /></Button>
          <Button variant="ghost" size="icon-sm" aria-label="Noise" aria-pressed={item.label === "noise"} className={cn(item.label === "noise" && "text-[var(--danger)]")} onClick={() => void label(item, "noise")}><ThumbDownIcon /></Button>
          <Button variant="ghost" size="icon-sm" aria-label={open ? "Hide details" : "Review evidence"} aria-expanded={open} onClick={() => setOpen(!open)}>
            <ChevronDownIcon className={cn("transition-transform", open && "rotate-180")} />
          </Button>
        </span>
      </div>
      {more && !resolved && moreActions.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {moreActions.map((a) => (
            <Button key={a.id} size="sm" variant="outline" className="h-7 text-xs"
              disabled={a.risk !== "link" && !actionsEnabled}
              onClick={() => void startAction(item, a.id)}>{a.title}</Button>
          ))}
        </div>
      )}
      {open && (
        <div className="mt-2 flex flex-col gap-3 border-t pt-2">
          <section>
            <h4 className="m-0 text-xs font-medium">Review evidence</h4>
            <GateChips item={item} />
            {item.bullets && item.bullets.length > 0 && (
              <ul className="mt-1.5 mb-0 list-disc pl-4 text-xs text-muted-foreground">
                {item.bullets.map((b) => <li key={b}>{b}</li>)}
              </ul>
            )}
            {(item.cause || item.narration) && item.summary && (item.cause || item.narration) !== item.summary && (
              <p className="mt-1.5 mb-0 text-xs text-muted-foreground">Rule: {item.summary}</p>
            )}
            <div className="mt-1.5"><Evidence evidence={item.evidence} /></div>
            {item.playbook_ref && <p className="mt-1.5 mb-0 text-[11px] text-muted-foreground">{item.playbook_ref}</p>}
          </section>
          <section>
            <h4 className="m-0 mb-1.5 text-xs font-medium">Activity</h4>
            {trailError && <p className="m-0 text-xs text-[var(--danger)]">{trailError}</p>}
            {!trailError && events == null && <p className="m-0 text-xs text-muted-foreground">Loading…</p>}
            {events && <FindingTimeline events={events} now={now} />}
          </section>
          {item.level && item.entity_id && (
            <section>
              <h4 className="m-0 mb-1.5 text-xs font-medium">
                Activity on this {item.level === "adset" ? "ad set" : item.level}
              </h4>
              {entityEvents == null && !trailError ? <p className="m-0 text-xs text-muted-foreground">Loading…</p>
                : <FindingTimeline events={entityEvents ?? []} now={now} />}
            </section>
          )}
        </div>
      )}
    </article>
  );
}

function ActivityList({ activity, now }: { activity: ActivityEntry[]; now: string }) {
  if (!activity.length) return <p className="text-sm text-muted-foreground">Nothing has changed yet.</p>;
  return (
    <ol className="m-0 flex list-none flex-col gap-2 p-0">
      {activity.map((a, i) => (
        <li key={`${a.finding_id}-${a.at}-${i}`} className="flex gap-2 text-xs">
          <span aria-hidden="true" className={cn("mt-1 size-1.5 shrink-0 rounded-full", a.to_state === "resolved" ? "bg-[var(--success)]" : SEVERITY[a.severity]?.bar)} />
          <span className="min-w-0 flex-1"><span className="font-medium">{a.title}</span>{a.entity_name ? ` · ${a.entity_name}` : ""}<br /><span className="text-muted-foreground">{a.note}</span></span>
          <span className="shrink-0 text-muted-foreground tabular-nums">{since(a.at, now)}</span>
        </li>
      ))}
    </ol>
  );
}

function fmtChange(v: unknown): string {
  if (Array.isArray(v)) return v.join(", ") || "none";
  if (typeof v === "number") return v.toLocaleString("en-IN");
  return v == null || v === "" ? "–" : String(v);
}

async function openInAgent(prompt: string) {
  useNotificationsStore.getState().closeAction();
  useShellStore.getState().setWorkspace("conversation");
  await useConversationStore.getState().createThread();
  await useConversationStore.getState().submit(prompt);
}

function Counts({ feed }: { feed: Feed }) {
  const open = feed.items.filter((i) => i.state !== "resolved");
  const by = { critical: 0, warning: 0, info: 0 };
  for (const i of open) by[i.severity] += 1;
  return (
    <p className="m-0 text-sm text-muted-foreground">
      {[by.critical && `${by.critical} critical`, by.warning && `${by.warning} warning`, by.info && `${by.info} info`]
        .filter(Boolean).join(" · ") || "Nothing open"}
      {feed.actions_enabled ? "" : " · one-click actions off"}
    </p>
  );
}

/** Preview, edit inputs, approve. The change shown is what Seleric MCP was asked to check; Approve runs exactly that. */
export function ActionDialog() {
  const flow = useNotificationsStore((s) => s.flow);
  const { closeAction, setInput, repreview, approve } = useNotificationsStore.getState();
  if (!flow) return null;
  const p = flow.preview;
  const action = flow.item.actions.find((a) => a.id === flow.actionId);
  const done = flow.result?.status === "executed";
  return (
    <Dialog open onOpenChange={(o) => { if (!o) closeAction(); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{action?.title ?? flow.actionId}</DialogTitle>
          <DialogDescription>{flow.item.path.join(" › ") || "Account"}</DialogDescription>
        </DialogHeader>
        {flow.busy === "previewing" && <p className="text-sm text-muted-foreground">Checking with Seleric MCP…</p>}
        {p?.kind === "link" && <p className="text-sm">Opens a new chat with this alert so Marketing Agent can look into it. Nothing changes until you approve something there.</p>}
        {p?.kind === "write" && (
          <div className="flex flex-col gap-3 text-sm">
            <p className="m-0 font-medium">{p.summary}</p>
            <table className="w-full text-xs">
              <thead><tr className="text-left text-muted-foreground"><th className="font-normal">Setting</th><th className="font-normal">Now</th><th className="font-normal">After</th></tr></thead>
              <tbody>
                {Object.keys(p.change).map((k) => (
                  <tr key={k}><td className="pr-2 text-muted-foreground">{k.replaceAll("_", " ")}</td><td className="pr-2 tabular-nums">{fmtChange(p.current[k])}</td><td className="font-medium tabular-nums">{fmtChange(p.change[k])}</td></tr>
                ))}
              </tbody>
            </table>
            {RISK[p.risk] && <p className="m-0 text-xs text-muted-foreground">{RISK[p.risk]}</p>}
            {!p.enabled && <p className="m-0 text-xs text-[var(--warning)]">One-click actions are switched off on this server, so this can only be previewed.</p>}
          </div>
        )}
        {action && action.inputs.length > 0 && !done && (
          <div className="flex flex-col gap-2">
            {action.inputs.map((name) => (
              <div key={name} className="flex flex-col gap-1">
                <Label htmlFor={`act-${name}`}>{name.replaceAll("_", " ")}</Label>
                <Input id={`act-${name}`} value={flow.inputs[name] ?? ""} onChange={(e) => setInput(name, e.target.value)} />
              </div>
            ))}
          </div>
        )}
        {flow.error && <p role="alert" className="m-0 text-sm text-[var(--danger)]">{flow.error}</p>}
        {done && <p role="status" className="m-0 text-sm text-[var(--success)]">Done. The monitor will confirm on its next check.</p>}
        <DialogFooter>
          <Button variant="ghost" onClick={closeAction}>{done ? "Close" : "Cancel"}</Button>
          {p?.kind === "link" && <Button onClick={() => void openInAgent(p.prompt)}>Open chat</Button>}
          {!done && !p && flow.busy === null && <Button variant="secondary" onClick={() => void repreview()}>Update preview</Button>}
          {p?.kind === "write" && !done && (
            <Button disabled={!p.enabled || flow.busy !== null} onClick={() => void approve()}>{flow.busy === "approving" ? "Applying…" : "Approve"}</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function BotReports({ reports, now }: { reports: BotReport[]; now: string }) {
  if (!reports.length) return <p className="text-sm text-muted-foreground">No bot reports in the last 48 hours.</p>;
  const open = (botId: string) => { useShellStore.getState().setWorkspace("bots"); void useBotsStore.getState().select(botId); };
  return (
    <ol className="m-0 flex list-none flex-col gap-2 p-0">
      {reports.map((r) => (
        <li key={`${r.run_id}-${r.at}`}>
          <button type="button" onClick={() => open(r.bot_id)} className="flex w-full gap-2 rounded-md px-1 py-1 text-left text-xs hover:bg-accent">
            <span aria-hidden="true" className={cn("mt-1 size-1.5 shrink-0 rounded-full", r.outcome === "action_needed" ? "bg-destructive" : "bg-muted-foreground/50")} />
            <span className="min-w-0 flex-1"><span className="font-medium">{r.bot_name}</span> · {r.reason}<br /><span className="text-muted-foreground">{r.summary}</span></span>
            <span className="shrink-0 text-muted-foreground tabular-nums">{since(r.at, now)}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

export function NotificationList({ feed }: { feed: Feed }) {
  const open = feed.items.filter((i) => i.state !== "resolved");
  const resolved = feed.items.filter((i) => i.state === "resolved");
  return (
    <Tabs defaultValue="open" className="min-h-0 flex-1">
      <TabsList>
        <TabsTrigger value="open">Open{open.length ? ` (${open.length})` : ""}</TabsTrigger>
        <TabsTrigger value="activity">Activity</TabsTrigger>
        <TabsTrigger value="bots">Bots{feed.bots?.length ? ` (${feed.bots.length})` : ""}</TabsTrigger>
        <TabsTrigger value="resolved">Resolved</TabsTrigger>
      </TabsList>
      <TabsContent value="open" className="flex flex-col gap-2">
        {open.length ? open.map((i) => <FindingCard key={i.finding_id} item={i} now={feed.server_time} actionsEnabled={feed.actions_enabled} />)
          : <p className="text-sm text-muted-foreground">All clear. The monitor checks every 5 minutes and will post here when something changes.</p>}
      </TabsContent>
      <TabsContent value="activity"><ActivityList activity={feed.activity} now={feed.server_time} /></TabsContent>
      <TabsContent value="bots"><BotReports reports={feed.bots ?? []} now={feed.server_time} /></TabsContent>
      <TabsContent value="resolved" className="flex flex-col gap-2">
        {resolved.length ? resolved.map((i) => <FindingCard key={i.finding_id} item={i} now={feed.server_time} actionsEnabled={false} />)
          : <p className="text-sm text-muted-foreground">Nothing resolved in the last 24 hours.</p>}
      </TabsContent>
    </Tabs>
  );
}

/** Live ads alerts panel for the Monitoring → Notifications tab. */
export function NotificationsPanel({ onConfigure }: { onConfigure?: () => void }) {
  const feed = useNotificationsStore((s) => s.feed);
  const error = useNotificationsStore((s) => s.error);
  const setOpen = useNotificationsStore((s) => s.setOpen);
  useEffect(() => {
    setOpen(true);
    return () => setOpen(false);
  }, [setOpen]);
  return (
    <div className="flex max-w-5xl flex-col gap-4">
      <header className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h3 className="m-0 text-sm font-semibold">Live ads alerts</h3>
          <p className="mt-0.5 mb-0 text-xs text-muted-foreground">Checked every 5 minutes. Each change is explained as it happens.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {feed && <Counts feed={feed} />}
          {onConfigure && <Button variant="outline" size="sm" onClick={onConfigure}>Configure brands</Button>}
        </div>
      </header>
      {error && <p role="alert" className="m-0 text-sm text-[var(--danger)]">Could not refresh: {error}</p>}
      {feed?.config_error && <p className="m-0 text-sm text-[var(--warning)]">{feed.config_error}</p>}
      {feed?.tenants.map((t) => <StatusStrip key={t.tenant_id} tenant={t} />)}
      {feed ? <NotificationList feed={feed} /> : !error && <p className="text-sm text-muted-foreground">Loading…</p>}
      <ActionDialog />
    </div>
  );
}
