import { useEffect, useMemo, useState, type ReactNode } from "react";
import { cn } from "cn";
import { AppFrame } from "../AppFrame";
import { AlertIcon, BotIcon, ChevronLeftIcon, MessageIcon, PlayIcon, PlusIcon, SettingsIcon, XIcon } from "../icons";
import { ThreadSidebar } from "../ThreadSidebar";
import { openInAgent } from "../notifications/NotificationCenter";
import { Button, buttonVariants } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useIsMobile } from "@/hooks/use-mobile";
import { SafeContent } from "../SafeContent";
import type { AnswerCheck, BotActivity, BotRun, EdaCheck, Proposal } from "../../api/bots";
import { POLL_MS, useBotsStore } from "../../stores/bots";
import { BotSettingsPage, humanize, SECTIONS, when, type SectionKey } from "./BotSettingsPage";

export { cronText, triggerText } from "./BotSettingsPage";



const OUTCOME_TONE: Record<string, string> = {
  completed: "bg-[var(--success)]", skipped: "bg-muted-foreground/50", attempted: "bg-[var(--warning)]",
  denied: "bg-destructive", failed: "bg-destructive",
};
const STATUS_LABEL: Record<string, string> = {
  queued: "Queued", running: "Running", completed: "Done", partial: "Partial", failed: "Failed", skipped: "Skipped",
};


/** Activity rows grouped by run, newest run first; rows inside a run oldest first so it reads as a story. */
export function groupByRun(activity: BotActivity[]): { runId: string; rows: BotActivity[] }[] {
  const groups = new Map<string, BotActivity[]>();
  for (const a of activity) groups.set(a.run_id, [...(groups.get(a.run_id) ?? []), a]);
  return [...groups.entries()]
    .map(([runId, rows]) => ({ runId, rows: [...rows].sort((x, y) => x.id - y.id) }))
    .sort((a, b) => b.rows[b.rows.length - 1].id - a.rows[a.rows.length - 1].id);
}


/** Splits the closing "OUTCOME: x — reason" line off the answer so the verdict can lead the card. */
export function splitAnswer(answer: string): { body: string; reason: string } {
  const lines = answer.trimEnd().split("\n");
  const clean = (l: string) => l.replaceAll("*", "").trim();
  // the last OUTCOME line, not the last line: the fact-check note can follow it
  const at = lines.map((l) => clean(l).toUpperCase().startsWith("OUTCOME:")).lastIndexOf(true);
  if (at < 0) return { body: answer, reason: "" };
  const rest = clean(lines[at]).slice("OUTCOME:".length);
  const dash = rest.indexOf("—");
  const reason = dash >= 0 ? rest.slice(dash + 1).trim() : "";
  return { body: [...lines.slice(0, at), ...lines.slice(at + 1)].join("\n").trim(), reason: reason.charAt(0).toUpperCase() + reason.slice(1) };
}

/** The run's verdict in the operator's words (B17 outcomes); tone = band colour. */
const VERDICT: Record<string, { label: string; tone: string; dot: string }> = {
  action_needed: { label: "Needs action", tone: "bg-[var(--signal-soft)] text-[var(--danger)]", dot: "bg-[var(--signal)]" },
  unverified: { label: "Couldn't check", tone: "bg-[var(--warning-soft)] text-[var(--warning)]", dot: "bg-[var(--warning)]" },
  cleared: { label: "Alert cleared", tone: "bg-muted text-muted-foreground", dot: "bg-muted-foreground/60" },
  no_action: { label: "All in order", tone: "bg-[var(--success-soft)] text-[var(--success)]", dot: "bg-[var(--success)]" },
};
const NO_VERDICT = { label: "", tone: "bg-muted text-foreground", dot: "bg-muted-foreground/50" };

export const KIND_LABEL: Record<string, string> = {
  cron: "Schedule", signal: "Monitoring alert", watch: "Campaign edit", webhook: "Webhook", manual: "Run now", chat: "Chat",
};

const live = (r: BotRun) => r.status === "queued" || r.status === "running";

export function verdictOf(r: Pick<BotRun, "outcome" | "status">) {
  const v = VERDICT[r.outcome ?? ""];
  return v ?? { ...NO_VERDICT, label: r.status === "running" || r.status === "queued" ? "Running…" : STATUS_LABEL[r.status] ?? r.status };
}

function RunBody({ r, body }: { r: BotRun; body: string }) {
  if (r.answer) return <div className="text-sm leading-relaxed [&_li]:max-w-[72ch] [&_p]:max-w-[72ch] [&_table]:tabular-nums [&_td]:align-top [&_td]:whitespace-normal [&_td]:[overflow-wrap:normal]"><SafeContent text={body} /></div>;
  if (r.error) return <p className="m-0 text-sm whitespace-pre-wrap text-destructive">{r.error}</p>;
  return live(r) ? <p className="m-0 text-sm text-muted-foreground">The bot is working; its answer appears here.</p> : null;
}

/** What a step adds beyond tool/target/scope: the reason for a denial or failure, a script's purpose and result, the
 * trigger or answer line. A completed read's raw payload says nothing to a reader, so it is left out. */
export function stepText(a: BotActivity): string {
  if (a.kind !== "tool") return a.summary;
  if (a.outcome === "completed" && a.summary.trimStart().startsWith("{")) return "";
  return a.summary.slice(0, 200);
}

const STEP_NAME: Record<string, string> = {
  memory_recall: "Memory", memory_retain: "Saved to memory", notification_center: "Notified", email: "Email",
  bot_eda: "Statistics (EDA)", run_python: "Calculation", meta_campaigns_list: "Live campaign list",
  meta_campaigns_get: "Live campaign details", meta_insights_live: "Live performance",
  bot_settings: "Settings changed", format_document: "Format document",
};


export function stepLabel(a: BotActivity): string {
  if (a.kind === "answer") return `Answer${a.decision ? `: ${(VERDICT[a.decision]?.label ?? a.decision.replace("_", " ")).toLowerCase()}` : ""}`;
  if (a.kind === "trigger") return "Started";
  if (a.kind === "note") return STEP_NAME[a.tool ?? ""] ?? "Note";
  return STEP_NAME[a.tool ?? ""] ?? humanize(a.tool ?? a.kind);
}

export function ActivityTimeline({ activity }: { activity: BotActivity[] }) {
  const groups = useMemo(() => groupByRun(activity), [activity]);
  if (!groups.length) return <p className="m-0 text-sm text-muted-foreground">Nothing yet. Every trigger, tool call and answer shows here.</p>;
  return (
    <ol className="m-0 flex list-none flex-col gap-5 p-0">
      {groups.map((g) => {
        const verdict = VERDICT[g.rows.find((a) => a.kind === "answer")?.decision ?? ""];
        return (
          <li key={g.runId}>
            <p className="m-0 mb-1.5 flex items-center gap-1.5 text-xs font-medium">
              {verdict && <span aria-hidden className={cn("size-2 rounded-full", verdict.dot)} />}
              <span>{verdict?.label ?? "Run"}</span>
              <span className="font-normal text-muted-foreground">· {when(g.rows[0].at)}</span>
            </p>
            <ol className="m-0 flex list-none flex-col gap-1.5 border-l pl-3">
              {g.rows.map((a) => (
                <li key={a.id} className="flex gap-2 text-xs">
                  <span aria-hidden className={cn("mt-1 size-1.5 shrink-0 rounded-full", OUTCOME_TONE[a.outcome])} />
                  <span className="min-w-0 flex-1 break-words">
                    <span className="font-medium" title={[a.target, a.scope && `scope ${a.scope}: ${a.decision}`].filter(Boolean).join("\n") || undefined}>{stepLabel(a)}</span>
                    {a.outcome !== "completed" && <span className="text-muted-foreground"> · {a.outcome}</span>}
                    {a.scope && a.decision && !a.decision.startsWith("allowed") && <span className="block text-destructive">Not allowed: {a.scope} ({a.decision})</span>}
                    {stepText(a) && <span title={stepText(a)} className="line-clamp-2 wrap-anywhere text-muted-foreground">{stepText(a)}</span>}
                  </span>
                </li>
              ))}
            </ol>
          </li>
        );
      })}
    </ol>
  );
}


/* ------------------------------------------------------------------ D-253 inbox: list · decision · campaign today */

const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
const istDay = (iso: string) => new Date(iso).toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
/** "15:06" today, "26 Nov, 15:06" on earlier days (the inbox can cover a week). */
const stamp = (iso: string) => (istDay(iso) === istDay(new Date().toISOString()) ? clock(iso) : when(iso));
export const inr = (n: number) => `₹${Math.round(n).toLocaleString("en-IN")}`;
const capital = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);
const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

export function fmtValue(v: number, unit: EdaCheck["unit"]): string {
  if (unit === "inr") return inr(v);
  if (unit === "pct") { const p = v * 100; return `${p.toFixed(p < 10 ? 2 : 0)}%`; }
  return Math.round(v).toLocaleString("en-IN");
}

const reviewOf = (p: Proposal) => `Review ${p.field || "it"}`;

export interface InboxItem { key: string; run: BotRun; proposal?: Proposal; index: number; total: number }

const single = (run: BotRun): InboxItem => ({ key: `${run.run_id}:0`, run, index: 0, total: 0 });
/** One item per proposed change; a run without proposals is one item. */
export const itemsOf = (runs: BotRun[]): InboxItem[] => runs.flatMap((run) => {
  const ps = run.report?.proposals ?? [];
  return ps.length ? ps.map((proposal, index) => ({ key: `${run.run_id}:${index}`, run, proposal, index, total: ps.length })) : [single(run)];
});

/** Needs your decision = action-needed runs nobody dismissed; dismissed ones are earlier flags; the rest are calm. */
/** One screen per bot: the inbox narrowed to the open bot's runs. */
function useBotRuns(): BotRun[] {
  const inbox = useBotsStore((s) => s.inbox);
  const id = useBotsStore((s) => s.selectedId);
  return useMemo(() => inbox.filter((r) => r.bot_id === id), [inbox, id]);
}

export function inboxGroups(runs: BotRun[]) {
  const action = runs.filter((r) => r.outcome === "action_needed");
  return {
    decide: itemsOf(action.filter((r) => !r.dismissed_at)),
    earlier: action.filter((r) => r.dismissed_at).map(single),
    calm: runs.filter((r) => r.outcome !== "action_needed"),
  };
}

/** One line for a run: the bot's headline, else its outcome reason, else why it ran. */
const runLine = (r: BotRun) => r.report?.headline || splitAnswer(r.answer ?? "").reason || r.reason || KIND_LABEL[r.trigger_kind] || r.trigger_kind;

const READ_TONE: Record<string, string> = {
  Normal: "bg-[var(--success-soft)] text-[var(--success)]", High: "bg-[var(--signal-soft)] text-[var(--danger)]",
  Low: "bg-[var(--signal-soft)] text-[var(--danger)]",
};

function Pill({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-bold whitespace-nowrap", tone)}>{children}</span>;
}

/** Status dot: red when it needs a person, brand when the run said something, grey otherwise. */
function Dot({ tone = "brand" }: { tone?: "brand" | "signal" | "muted" }) {
  return <span aria-hidden className={cn("mt-1.5 block size-2 shrink-0 rounded-full",
    tone === "signal" ? "bg-[var(--signal)]" : tone === "muted" ? "bg-muted-foreground/50" : "bg-[var(--brand)]")} />;
}

function ItemRow({ item, selected, onOpen, big = false }: { item: InboxItem; selected: boolean; onOpen: () => void; big?: boolean }) {
  const { run, proposal } = item;
  const sub = proposal ? capital(proposal.condition || proposal.reason || proposal.field) : runLine(run);
  return (
    <li>
      <Button variant="ghost" onClick={onOpen} aria-current={selected || undefined}
        className={cn("grid h-auto w-full grid-cols-[8px_minmax(0,1fr)_auto] items-start gap-2.5 rounded-lg border border-transparent px-3 py-2.5 text-left font-normal whitespace-normal pointer-coarse:py-3.5",
          selected && "border-[var(--brand-soft-line)] bg-[var(--brand-soft)] hover:bg-[var(--brand-soft)]")}>
        <Dot tone={big ? "signal" : "muted"} />
        <span className="min-w-0">
          <span className="block truncate text-[13.5px] font-bold">{proposal?.entity || KIND_LABEL[run.trigger_kind] || run.trigger_kind}</span>
          <span className="line-clamp-2 text-xs text-muted-foreground tabular-nums">{stamp(run.created_at)} · {sub}</span>
        </span>
        {proposal?.proposed != null && (
          <span className="text-right text-sm font-extrabold whitespace-nowrap tabular-nums">
            {proposal.current != null && <s className="block text-[11.5px] font-medium text-muted-foreground">{inr(proposal.current)}</s>}
            {inr(proposal.proposed)}
          </span>
        )}
      </Button>
    </li>
  );
}

const GroupHead = ({ children }: { children: ReactNode }) => <h2 className="m-0 px-4 pt-3 pb-1.5 text-xs font-semibold text-muted-foreground">{children}</h2>;


/** Same shape as the Monitoring header: title and subtitle on the left, the page's buttons on the right. */
function PageHead({ onSettings, onSwitch }: { onSettings: () => void; onSwitch: (id: string) => void }) {
  const bots = useBotsStore((s) => s.bots);
  const bot = useBotsStore((s) => s.bot);
  const save = useBotsStore((s) => s.save);
  const templates = useBotsStore((s) => s.templates);
  const create = useBotsStore((s) => s.create);
  const runNow = useBotsStore((s) => s.runNow);
  const busy = useBotsStore((s) => s.busy);
  const running = useBotRuns().some(live);
  return (
    <header className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3 pb-3">
      <div className="min-w-0">
        <h2 className="m-0 font-serif text-2xl leading-tight font-normal tracking-tight">Bots</h2>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
          {bots.length > 1
            ? <NativeSelect aria-label="Bot" size="sm" value={bot?.bot_id ?? ""} onChange={(e) => onSwitch(e.target.value)}>
                {bots.map((b) => <NativeSelectOption key={b.bot_id} value={b.bot_id}>{b.name}</NativeSelectOption>)}
              </NativeSelect>
            : <span className="flex items-center gap-1.5"><BotIcon aria-hidden />{bot?.name ?? "No bot yet"}</span>}
          {bot && (
            <label className="flex items-center gap-1.5 pointer-coarse:min-h-11">
              <Switch checked={bot.enabled} onCheckedChange={(v) => void save({ enabled: v })} aria-label="Bot on" />{bot.enabled ? "On" : "Paused"}
            </label>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        {bot && <Button className="bg-brand text-[var(--on-accent)] hover:bg-[var(--brand-hover)] pointer-coarse:h-11" disabled={busy || !bot.enabled || running} onClick={() => void runNow()}>
          <PlayIcon />{running ? "Running…" : "Run now"}
        </Button>}
        {bot && <Button variant="outline" className="pointer-coarse:h-11" aria-label="Bot settings" onClick={onSettings}><SettingsIcon />Settings</Button>}
        {templates.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger disabled={busy} className={cn(buttonVariants({ variant: "outline" }), "pointer-coarse:h-11")}><PlusIcon />New bot</DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel>New bot from a template</DropdownMenuLabel>
              {templates.map((t) => <DropdownMenuItem key={t.template} onSelect={() => void create(t.template)}>{t.name}</DropdownMenuItem>)}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>
    </header>
  );
}

/** The list pane: one TabsContent per page tab (the tab bar sits in the page header row, as on Monitoring). */
function InboxList({ onTab, current, onOpen, className }: {
  onTab: (t: string) => void; current: string | null; onOpen: (key: string) => void; className?: string;
}) {
  const inbox = useBotRuns();
  const days = useBotsStore((s) => s.inboxDays);
  const loadInbox = useBotsStore((s) => s.loadInbox);
  const g = useMemo(() => inboxGroups(inbox), [inbox]);
  return (
    <section aria-label="Bot runs" className={className}>
        <TabsContent value="needs" className="pt-1 pb-6">
          <GroupHead>Needs your decision</GroupHead>
          <ul className="m-0 flex list-none flex-col gap-0.5 px-2 p-0">
            {g.decide.map((i) => <ItemRow key={i.key} item={i} big selected={i.key === current} onOpen={() => onOpen(i.key)} />)}
          </ul>
          {!g.decide.length && <p className="m-0 px-4 pb-2 text-sm text-muted-foreground">Nothing needs you right now.</p>}
          {g.earlier.length > 0 && <>
            <GroupHead>Dismissed</GroupHead>
            <ul className="m-0 flex list-none flex-col gap-0.5 px-2 p-0">
              {g.earlier.map((i) => <ItemRow key={i.key} item={i} selected={i.key === current} onOpen={() => onOpen(i.key)} />)}
            </ul>
          </>}
          {g.calm.length > 0 && <>
            <GroupHead>Calm runs</GroupHead>
            <Button variant="ghost" onClick={() => onTab("all")}
              className="mx-2 grid h-auto w-[calc(100%-1rem)] grid-cols-[8px_minmax(0,1fr)] items-start gap-2.5 rounded-lg px-3 py-2.5 text-left font-normal whitespace-normal">
              <Dot tone="muted" />
              <span><span className="block text-[13.5px] font-bold">{g.calm.length} {g.calm.length === 1 ? "run" : "runs"}, nothing to do</span>
                <span className="text-xs text-muted-foreground">Alerts cleared on re-check, or all in order</span></span>
            </Button>
          </>}
        </TabsContent>
        <TabsContent value="all" className="pb-6">
          <ol className="m-0 mt-2 flex list-none flex-col gap-0.5 px-2 p-0">
            {inbox.map((r) => {
              const v = verdictOf(r);
              const key = `${r.run_id}:0`;
              return (
                <li key={r.run_id}>
                  <Button variant="ghost" onClick={() => onOpen(key)} aria-current={key === current || undefined}
                    className={cn("grid h-auto w-full grid-cols-[8px_minmax(0,1fr)] items-start gap-2.5 rounded-lg border border-transparent px-3 py-2.5 text-left font-normal whitespace-normal pointer-coarse:py-3.5",
                      key === current && "border-[var(--brand-soft-line)] bg-[var(--brand-soft)] hover:bg-[var(--brand-soft)]")}>
                    <Dot tone={r.outcome === "action_needed" ? "signal" : r.outcome ? "brand" : "muted"} />
                    <span className="min-w-0">
                      <span className="flex items-baseline gap-2 text-[13.5px] font-bold"><span className="truncate">{v.label}</span>
                        <span className="ml-auto shrink-0 text-xs font-normal text-muted-foreground tabular-nums">{stamp(r.created_at)}</span></span>
                      <span className="block truncate text-xs text-muted-foreground">{KIND_LABEL[r.trigger_kind] ?? r.trigger_kind}</span>
                      <span className="line-clamp-2 text-xs text-muted-foreground">{runLine(r)}</span>
                    </span>
                  </Button>
                </li>
              );
            })}
          </ol>
          {!inbox.length && <p className="m-0 px-4 py-2 text-sm text-muted-foreground">No runs {days === 1 ? "today" : `in the last ${days} days`} yet.</p>}
          {days === 1 && <Button variant="ghost" size="sm" className="mx-2 mt-2 text-muted-foreground pointer-coarse:h-11" onClick={() => void loadInbox(7)}>Show the last 7 days</Button>}
        </TabsContent>
    </section>
  );
}

/** Where today sits in its normal range for these hours: band = p10-p90, tick = p50, ring = today so far. */
export function bandScale(c: Pick<EdaCheck, "now" | "p10" | "p50" | "p90">) {
  const lo = Math.min(c.p10, c.now);
  const hi = Math.max(c.p90, c.now);
  const pad = (hi - lo) * 0.15 || Math.abs(hi) * 0.1 || 1;
  const from = lo - pad;
  const at = (v: number) => ((v - from) / (hi + pad - from)) * 100;
  return { band: [at(c.p10), at(c.p90)] as const, tick: c.p50 == null ? null : at(c.p50), marker: at(c.now) };
}

function EdaCheckRow({ c }: { c: EdaCheck }) {
  const s = bandScale(c);
  return (
    <div className="border-b py-2.5 tabular-nums">
      <div className="flex items-baseline justify-between gap-3 text-[13.5px]"><span>{c.label}</span><b className="text-[15px] font-extrabold">{fmtValue(c.now, c.unit)}</b></div>
      <div aria-hidden className="relative mx-2 mt-1.5 mb-1 h-3.5 before:absolute before:inset-x-[-8px] before:top-1.5 before:h-0.5 before:bg-border">
        <span className="absolute top-0.5 h-2.5 rounded-[3px] border border-[var(--success-line)] bg-[var(--success-soft)]" style={{ left: `${s.band[0]}%`, width: `${s.band[1] - s.band[0]}%` }} />
        {s.tick != null && <span className="absolute top-px -ml-px h-3 w-0.5 bg-muted-foreground/60" style={{ left: `${s.tick}%` }} />}
        <span className={cn("absolute top-0 -ml-[7px] size-3.5 rounded-full border-[3.5px] bg-card", c.read === "Normal" ? "border-[var(--brand)] dark:border-foreground" : "border-[var(--signal)]")} style={{ left: `${s.marker}%` }} />
      </div>
      <div className="flex items-center justify-between gap-3">
        <small className="text-xs text-muted-foreground">normal {fmtValue(c.p10, c.unit)}–{fmtValue(c.p90, c.unit)}</small>
        <Pill tone={READ_TONE[c.read]}>{c.read}</Pill>
      </div>
    </div>
  );
}

const NO_RANGE = ["", "-", "–", "—"];

function AnswerCheckRow({ c }: { c: AnswerCheck }) {
  const normal = NO_RANGE.includes(c.normal.trim()) ? "" : c.normal;
  return (
    <div className="border-b py-2.5 tabular-nums">
      <div className={cn("flex items-baseline justify-between gap-x-3 text-[13.5px]", c.now.length > 16 && "flex-col")}>
        <span className="min-w-0">{c.label}</span><b className={c.now.length > 16 ? "text-[13.5px] font-semibold" : "text-[15px] font-extrabold"}>{c.now}</b>
      </div>
      <div className="mt-1 flex items-center justify-between gap-3">
        <small className="text-xs text-muted-foreground">{normal ? `normal ${normal}` : "no normal range"}</small>
        {c.read && <Pill tone={READ_TONE[c.read] ?? "bg-muted text-muted-foreground"}>{c.read}</Pill>}
      </div>
    </div>
  );
}

function Decision({ item, onAsk, onBack, backLabel = "All runs", children }: {
  item: InboxItem; onAsk: () => void; onBack?: () => void; backLabel?: string; children?: ReactNode;
}) {
  const dismiss = useBotsStore((s) => s.dismiss);
  const busy = useBotsStore((s) => s.busy);
  const { run, proposal } = item;
  const r = run.report && !run.report.extract_failed ? run.report : null;
  const v = verdictOf(run);
  const { body, reason } = splitAnswer(run.answer ?? "");
  const why = proposal ? [proposal.condition && `${capital(proposal.condition)}.`, proposal.reason].filter(Boolean).join(" ") : r?.headline || reason;
  const checks = [...(run.report?.eda_checks ?? [])];
  const unverified = r?.unverified ?? [];
  return (
    <article className="mx-auto flex w-full max-w-[1240px] flex-col px-7 pt-5 pb-8 max-md:px-4 min-[96rem]:px-10">
      {onBack && <Button variant="ghost" className="-ml-2 mb-2 self-start pointer-coarse:h-11" onClick={onBack}><ChevronLeftIcon />{backLabel}</Button>}
      <p className="m-0 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] text-muted-foreground tabular-nums">
        <Pill tone={v.tone}>{run.outcome === "action_needed" && <AlertIcon className="size-3.5" />}{v.label}</Pill>
        <span>{[KIND_LABEL[run.trigger_kind], stamp(run.created_at), proposal?.field].filter(Boolean).join(" · ")}</span>
      </p>
      <h2 className="m-0 mt-1.5 text-2xl leading-tight font-extrabold tracking-tight wrap-anywhere">{proposal?.entity || KIND_LABEL[run.trigger_kind] || v.label}</h2>
      {proposal && (proposal.proposed != null
        ? <p className="m-0 mt-2 flex flex-wrap items-baseline gap-x-3 tabular-nums">
            {proposal.current != null && <s className="text-lg text-muted-foreground">{inr(proposal.current)}</s>}
            <b className="text-[40px] leading-none font-extrabold tracking-tight max-sm:text-[34px]">{inr(proposal.proposed)}</b>
            <small className="text-xs text-muted-foreground">proposed {proposal.field || "value"}{proposal.current == null ? " · current value not given" : ""}</small>
          </p>
        : <p className="m-0 mt-2 flex flex-wrap items-baseline gap-x-3">
            <b className="text-[28px] leading-tight font-extrabold tracking-tight">{reviewOf(proposal)}</b>
            <small className="text-xs text-muted-foreground">proposed · no new value given</small>
          </p>)}
      {why && <p className="m-0 mt-3 max-w-[66ch] text-[15px] text-foreground/85">{why}</p>}
      {item.total > 1 && <p className="m-0 mt-1 text-xs text-muted-foreground tabular-nums">Change {item.index + 1} of {item.total} in this run</p>}
      <div className="mt-4 flex flex-wrap gap-2 max-sm:grid max-sm:grid-cols-2">
        <Button variant="outline" className="pointer-coarse:h-11" onClick={onAsk}><MessageIcon />Ask Seleric</Button>
        {run.outcome === "action_needed" && (run.dismissed_at
          ? <Button variant="ghost" className="pointer-coarse:h-11" disabled={busy} onClick={() => void dismiss(run.run_id, true)}>Bring back</Button>
          : <Button variant="ghost" className="pointer-coarse:h-11" disabled={busy} onClick={() => void dismiss(run.run_id)}><XIcon />{item.total > 1 ? `Dismiss all ${item.total}` : "Dismiss"}</Button>)}
      </div>
      {r ? <>
        {(checks.length > 0 || (r.checks?.length ?? 0) > 0) && (
          <section aria-label="Checks" className="mt-7">
            <h3 className="m-0 mb-1 text-sm font-bold">Checks at {stamp(run.finished_at ?? run.created_at)}</h3>
            <div className="grid grid-cols-1 gap-x-6 @xl/detail:grid-cols-2 @4xl/detail:grid-cols-3 @4xl/detail:gap-x-8">
              {checks.map((c) => <EdaCheckRow key={c.metric} c={c} />)}
              {(r.checks ?? []).map((c) => <AnswerCheckRow key={c.label} c={c} />)}
            </div>
          </section>
        )}
        {(r.movers?.length ?? 0) > 0 && (
          <section aria-label="What moved" className="mt-7">
            <h3 className="m-0 mb-1 text-sm font-bold">What moved today</h3>
            <Table className="text-[13px] tabular-nums">
              <TableHeader><TableRow className="hover:bg-transparent">
                <TableHead className="h-auto py-[7px] pl-0 text-xs font-semibold text-muted-foreground">Campaign</TableHead>
                <TableHead className="h-auto py-[7px] text-right text-xs font-semibold text-muted-foreground">Today</TableHead>
                <TableHead className="h-auto py-[7px] text-xs font-semibold text-muted-foreground @max-xl/detail:hidden">Note</TableHead>
              </TableRow></TableHeader>
              <TableBody>{r.movers!.map((m) => (
                <TableRow key={m.entity} className="border-b hover:bg-transparent">
                  <TableCell className="py-[7px] pl-0 whitespace-normal break-words">{m.entity}</TableCell>
                  <TableCell className="py-[7px] text-right whitespace-normal">{m.figure}</TableCell>
                  <TableCell className="py-[7px] whitespace-normal text-muted-foreground @max-xl/detail:hidden">{m.note}</TableCell>
                </TableRow>
              ))}</TableBody>
            </Table>
          </section>
        )}
      </> : <div className="mt-6"><RunBody r={run} body={body} /></div>}
      {(unverified.length > 0 || proposal) && (
        <p className="m-0 mt-6 flex gap-2 rounded-md bg-[var(--warning-soft)] px-3 py-2 text-[12.5px] text-[var(--warning)]">
          <AlertIcon className="mt-0.5 size-4 shrink-0" />
          <span>{unverified.length > 0 && `Not checked against live data: ${unverified.join("; ")}. `}{proposal && "The bot only proposes; a person makes the change. In-app approval arrives with Ads approvals."}</span>
        </p>
      )}
      {children}
    </article>
  );
}

interface Event { key: string; time: string; title: string; note: string; tone?: "brand" | "signal"; big?: boolean }

/** The selected campaign through today's runs (every bot), or, with no campaign, the bot's day. Matched by name as the
 * answers write it. shortcut: name match, not Meta id; upgrade when the report carries entity ids. */
export function historyFor(item: InboxItem, runs: BotRun[]): { title: string; sub: string; events: Event[] } {
  const entity = item.proposal?.entity;
  if (!entity) {
    return {
      title: "Today on this bot", sub: item.run.bot_name ?? "",
      events: runs.filter((r) => r.bot_id === item.run.bot_id).map((r) => ({
        key: r.run_id, time: `${stamp(r.created_at)} · ${KIND_LABEL[r.trigger_kind] ?? r.trigger_kind}`, title: verdictOf(r).label, note: runLine(r),
        tone: r.outcome === "action_needed" ? "signal" : "brand", big: r.run_id === item.run.run_id,
      })),
    };
  }
  const events: Event[] = [];
  const p = item.proposal!;
  events.push({ key: "proposed", time: "Proposed", title: p.proposed != null ? `${inr(p.proposed)}${p.field ? ` ${p.field}` : ""}` : reviewOf(p),
    note: p.condition ? capital(p.condition) : p.current != null ? `Now ${inr(p.current)}` : "", tone: "signal", big: true });
  for (const r of runs) {
    const time = `${stamp(r.created_at)} · ${KIND_LABEL[r.trigger_kind] ?? r.trigger_kind}`;
    (r.report?.proposals ?? []).forEach((q, i) => {
      if (!same(q.entity, entity) || (r.run_id === item.run.run_id && i === item.index)) return;
      events.push({ key: `${r.run_id}:p${i}`, time, title: q.proposed != null ? `Proposed ${inr(q.proposed)}${q.current != null ? ` (was ${inr(q.current)})` : ""}` : reviewOf(q),
        note: verdictOf(r).label, big: r.run_id === item.run.run_id });
    });
    (r.report?.movers ?? []).filter((m) => same(m.entity, entity)).forEach((m, i) =>
      events.push({ key: `${r.run_id}:m${i}`, time, title: m.figure || "Moved", note: m.note, big: r.run_id === item.run.run_id }));
  }
  return { title: "This campaign today", sub: `What this bot saw on ${entity}`, events };
}

function CampaignHistory({ item, className }: { item: InboxItem; className?: string }) {
  const inbox = useBotRuns();
  const h = useMemo(() => historyFor(item, inbox), [item, inbox]);
  return (
    <section aria-label={h.title} className={className}>
      <h3 className="m-0 text-sm font-bold">{h.title}</h3>
      {h.sub && <p className="m-0 mb-3.5 text-[12.5px] text-muted-foreground wrap-anywhere">{h.sub}</p>}
      <ol className="m-0 list-none divide-y p-0 tabular-nums">
        {h.events.map((e) => (
          <li key={e.key} className={cn("grid grid-cols-[8px_minmax(0,1fr)] gap-2.5 py-2.5", e.big && "font-semibold")}>
            <Dot tone={e.tone === "signal" ? "signal" : "brand"} />
            <span className="min-w-0">
              <span className="block text-xs font-normal text-muted-foreground">{e.time}</span>
              <b className="block text-sm wrap-anywhere">{e.title}</b>
              {e.note && <small className="block text-[12.5px] font-normal text-muted-foreground">{e.note}</small>}
            </span>
          </li>
        ))}
      </ol>
      {h.events.length === 1 && <p className="m-0 mt-3 text-xs text-muted-foreground">No other run mentioned it today.</p>}
    </section>
  );
}


/** What "Ask Seleric" sends: the decision in words, so the new chat starts with it (as Monitoring's alerts do). */
export function askPrompt({ run, proposal }: InboxItem): string {
  const change = proposal && [proposal.field, proposal.current != null && `now ${inr(proposal.current)}`, proposal.proposed != null && `proposed ${inr(proposal.proposed)}`].filter(Boolean).join(", ");
  const what = proposal ? `${proposal.entity}${change ? ` (${change})` : ""}${proposal.reason ? `: ${proposal.reason}` : ""}` : runLine(run);
  return `The ${run.bot_name ?? "bot"} bot flagged this at ${stamp(run.created_at)}: ${what}. Check it against live data and tell me what to do.`;
}

const urlRun = () => new URL(location.href).searchParams.get("run");
/** ?settings=<section> opens the settings page; an unknown section opens Goal. */
const urlSettings = (): SectionKey | null => {
  const v = new URL(location.href).searchParams.get("settings");
  return v === null ? null : SECTIONS.find((s) => s.key === v)?.key ?? "goal";
};
/** history.state.bots names the param a push opened, so Back can pop it instead of stacking a new entry. */
function writeUrl(name: "run" | "settings", value: string | null, push: boolean) {
  const url = new URL(location.href);
  if (value) url.searchParams.set(name, value); else url.searchParams.delete(name);
  if (url.href === location.href) return;
  if (push) history.pushState({ bots: name }, "", url); else history.replaceState(history.state, "", url);
}
const writeUrlRun = (key: string | null, push: boolean) => writeUrl("run", key, push);

export function BotsWorkspace() {
  const loadList = useBotsStore((s) => s.loadList);
  const loadInbox = useBotsStore((s) => s.loadInbox);
  const refresh = useBotsStore((s) => s.refresh);
  const bot = useBotsStore((s) => s.bot);
  const inbox = useBotsStore((s) => s.inbox);
  const error = useBotsStore((s) => s.error);
  const selectedId = useBotsStore((s) => s.selectedId);
  const select = useBotsStore((s) => s.select);
  const narrow = useIsMobile(768);
  const [tab, setTab] = useState("needs");
  const [sel, setSel] = useState<string | null>(urlRun);
  const [settings, setSettings] = useState<SectionKey | null>(urlSettings);

  useEffect(() => {
    void loadList();
    void loadInbox();
    const id = window.setInterval(() => { if (!document.hidden) { void refresh(); void loadList(); void loadInbox(); } }, POLL_MS);
    const pop = () => { setSel(urlRun()); setSettings(urlSettings()); };
    window.addEventListener("popstate", pop);
    return () => { window.clearInterval(id); window.removeEventListener("popstate", pop); };
  }, [loadList, loadInbox, refresh]);

  const mine = useBotRuns();
  const items = useMemo(() => itemsOf(mine), [mine]);
  const decide = useMemo(() => inboxGroups(mine).decide, [mine]);
  // Phones open the list first; wider screens show the first decision (else the newest run) straight away.
  const item = items.find((i) => i.key === sel) ?? items.find((i) => sel && i.run.run_id === sel.split(":")[0])
    ?? (narrow ? undefined : (tab === "needs" ? decide[0] : undefined) ?? items[0]);

  // A link to another bot's run (?run=) opens that bot's screen.
  const linked = sel ? inbox.find((r) => r.run_id === sel.split(":")[0])?.bot_id : undefined;
  useEffect(() => { if (linked && linked !== useBotsStore.getState().selectedId) void select(linked); }, [linked, select]);
  const switchBot = (id: string) => { setSel(null); writeUrlRun(null, false); void select(id); };

  const open = (key: string) => { setSel(key); writeUrlRun(key, narrow); };
  const back = () => { if (history.state?.bots === "run") history.back(); else { setSel(null); writeUrlRun(null, false); } };
  const openSettings = (s: SectionKey) => { writeUrl("settings", s, settings === null); setSettings(s); };
  const closeSettings = () => { if (history.state?.bots === "settings") history.back(); else { writeUrl("settings", null, false); setSettings(null); } };
  const badge = decide.length;

  const detailOnly = narrow && !!item;
  return (
    <AppFrame workspace="bots" sidebarLabel="Bots navigation" toggleLabel="Toggle navigation" sidebarBody={<ThreadSidebar />}>
      {error && <p role="alert" className="m-0 border-b bg-[var(--danger-soft)] px-4 py-2 text-sm text-destructive">{error}</p>}
      {bot && settings ? <BotSettingsPage bot={bot} section={settings} onSection={openSettings} onBack={closeSettings}
        onDeleted={() => { writeUrl("settings", null, false); setSettings(null); }} /> : <Tabs value={tab} onValueChange={setTab} className="min-h-0 flex-1 gap-0">
        <div className={cn("px-4 max-md:px-3", detailOnly && "hidden")}>
          <PageHead onSettings={() => openSettings("goal")} onSwitch={switchBot} />
          <TabsList variant="line" className="h-auto w-full justify-start gap-1 rounded-none border-b p-0" aria-label="Bot runs">
            <TabsTrigger value="needs" className="flex-none rounded-none px-3 py-2 data-active:border-b-brand pointer-coarse:py-3">Needs you <span className="tabular-nums text-muted-foreground">{badge}</span></TabsTrigger>
            <TabsTrigger value="all" className="flex-none rounded-none px-3 py-2 data-active:border-b-brand pointer-coarse:py-3">All runs <span className="tabular-nums text-muted-foreground">{mine.length}</span></TabsTrigger>
          </TabsList>
        </div>
        <div className="grid min-h-0 flex-1 md:grid-cols-[minmax(280px,320px)_minmax(0,1fr)] min-[80rem]:grid-cols-[300px_minmax(0,1fr)_280px] min-[96rem]:grid-cols-[340px_minmax(0,1fr)_340px]">
          <InboxList onTab={setTab} current={item?.key ?? null} onOpen={open}
            className={cn("min-h-0 overflow-y-auto md:border-r", detailOnly && "hidden")} />
          <main className={cn("@container/detail min-h-0 min-w-0 overflow-y-auto", narrow && !item && "hidden")}>
            {item
              ? <Decision item={item} onAsk={() => void openInAgent(askPrompt(item))} onBack={narrow ? back : undefined} backLabel={tab === "needs" ? "Needs you" : "All runs"}>
                  <CampaignHistory item={item} className="mt-8 border-t pt-6 min-[80rem]:hidden" />
                </Decision>
              : <p className="m-0 p-8 text-sm text-muted-foreground">{selectedId ? "No runs to show yet. Press Run now, or the bot reports on its next trigger." : "Create a bot from a template with New bot."}</p>}
          </main>
          {item && <aside aria-label="Campaign history" className="hidden min-h-0 overflow-y-auto border-l p-[18px] min-[80rem]:block"><CampaignHistory item={item} /></aside>}
        </div>
      </Tabs>}
    </AppFrame>
  );
}
