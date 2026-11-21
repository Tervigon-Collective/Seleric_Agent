import { useEffect, useMemo, useState } from "react";
import { cn } from "cn";
import { AppFrame } from "../AppFrame";
import { BotIcon, PlusIcon } from "../icons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import type { Bot, BotActivity, BotRun, BotTrigger } from "../../api/bots";
import { POLL_MS, useBotsStore } from "../../stores/bots";

const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–";

/** Limits the backend enforces today; the rest (tool calls, pending proposals) arrive with proposals in P3. */
const ENFORCED_LIMITS = ["max_scheduled_runs_per_day"];

/** Run outcomes (B17): only action_needed is an alert; unverified means the bot could not check. */
const OUTCOME_BADGE: Record<string, "destructive" | "outline" | "secondary"> = { action_needed: "destructive", unverified: "outline" };

const OUTCOME_TONE: Record<string, string> = {
  completed: "bg-[var(--success)]", skipped: "bg-muted-foreground/50", attempted: "bg-[var(--warning)]",
  denied: "bg-destructive", failed: "bg-destructive",
};
const STATUS_LABEL: Record<string, string> = {
  queued: "Queued", running: "Running", completed: "Done", partial: "Partial", failed: "Failed", skipped: "Skipped",
};

export function triggerText(t: BotTrigger): string {
  if (t.kind === "cron") return `Schedule ${t.config.cron} (${t.config.timezone})`;
  return `Monitoring: ${t.config.detector_prefix}* · ${(t.config.severities ?? []).join(", ")}`;
}

/** Activity rows grouped by run, newest run first; rows inside a run oldest first so it reads as a story. */
export function groupByRun(activity: BotActivity[]): { runId: string; rows: BotActivity[] }[] {
  const groups = new Map<string, BotActivity[]>();
  for (const a of activity) groups.set(a.run_id, [...(groups.get(a.run_id) ?? []), a]);
  return [...groups.entries()]
    .map(([runId, rows]) => ({ runId, rows: [...rows].sort((x, y) => x.id - y.id) }))
    .sort((a, b) => b.rows[b.rows.length - 1].id - a.rows[a.rows.length - 1].id);
}

function BotList() {
  const bots = useBotsStore((s) => s.bots);
  const selectedId = useBotsStore((s) => s.selectedId);
  const select = useBotsStore((s) => s.select);
  const templates = useBotsStore((s) => s.templates);
  const create = useBotsStore((s) => s.create);
  const busy = useBotsStore((s) => s.busy);
  const [template, setTemplate] = useState("");
  const pick = template || templates[0]?.template || "";
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <h2 className="m-0 px-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">Bots</h2>
      <ul className="m-0 flex list-none flex-col gap-0.5 overflow-y-auto p-0" aria-label="Bots">
        {bots.map((b) => (
          <li key={b.bot_id}>
            <button type="button" onClick={() => void select(b.bot_id)} aria-current={b.bot_id === selectedId || undefined}
              className={cn("flex w-full flex-col rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent", b.bot_id === selectedId && "bg-accent")}>
              <span className="flex items-center gap-1.5 font-medium">
                <span aria-hidden className={cn("size-1.5 rounded-full", b.enabled ? "bg-[var(--success)]" : "bg-muted-foreground/40")} />
                {b.name}{!b.enabled && <span className="text-xs font-normal text-muted-foreground"> · paused</span>}
              </span>
              <span className="text-xs text-muted-foreground">{b.role}</span>
              <span className="text-xs text-muted-foreground">
                Last: {b.last_run ? `${STATUS_LABEL[b.last_run.status] ?? b.last_run.status} ${when(b.last_run.created_at)}` : "never"}
                {" · "}Next: {when(b.next_run_at)}
              </span>
            </button>
          </li>
        ))}
        {!bots.length && <li className="px-2 text-sm text-muted-foreground">No bots yet.</li>}
      </ul>
      {templates.length > 0 && (
        <div className="mt-auto flex gap-1 border-t px-1 pt-2">
          <NativeSelect aria-label="Template" value={pick} onChange={(e) => setTemplate(e.target.value)} className="min-w-0 flex-1">
            {templates.map((t) => <NativeSelectOption key={t.template} value={t.template}>{t.name}</NativeSelectOption>)}
          </NativeSelect>
          <Button size="sm" disabled={busy || !pick} onClick={() => void create(pick)}><PlusIcon /> New bot</Button>
        </div>
      )}
    </div>
  );
}

function Instructions({ bot }: { bot: Bot }) {
  const save = useBotsStore((s) => s.save);
  const busy = useBotsStore((s) => s.busy);
  const [text, setText] = useState(bot.instructions);
  const [name, setName] = useState(bot.name);
  const [role, setRole] = useState(bot.role);
  useEffect(() => { setText(bot.instructions); setName(bot.name); setRole(bot.role); }, [bot.bot_id, bot.version]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = text !== bot.instructions || name !== bot.name || role !== bot.role;
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-2 max-md:grid-cols-1">
        <label className="flex flex-col gap-1 text-sm">Name<Input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="flex flex-col gap-1 text-sm">Role<Input value={role} onChange={(e) => setRole(e.target.value)} /></label>
      </div>
      <label className="flex flex-col gap-1 text-sm">
        Standing instructions <span className="text-xs text-muted-foreground">version {bot.instructions_version} · every save keeps the old text; each run records the version it used</span>
        <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={16} className="font-mono text-xs" />
      </label>
      <div className="flex gap-2">
        <Button disabled={!dirty || busy || !name.trim()} onClick={() => void save({ instructions: text, name: name.trim(), role })}>Save</Button>
        {dirty && <Button variant="ghost" onClick={() => { setText(bot.instructions); setName(bot.name); setRole(bot.role); }}>Discard</Button>}
      </div>
    </div>
  );
}

function Triggers({ bot }: { bot: Bot }) {
  const addTrigger = useBotsStore((s) => s.addTrigger);
  const setTrigger = useBotsStore((s) => s.setTrigger);
  const deleteTrigger = useBotsStore((s) => s.deleteTrigger);
  const busy = useBotsStore((s) => s.busy);
  const [kind, setKind] = useState<"cron" | "signal">("cron");
  const [cron, setCron] = useState("0 9 * * *");
  const [tz, setTz] = useState("Asia/Kolkata");
  const [prefix, setPrefix] = useState("live.meta.");
  const [critical, setCritical] = useState(true);
  const [warning, setWarning] = useState(true);
  const add = () => void addTrigger(kind, kind === "cron" ? { cron, timezone: tz }
    : { detector_prefix: prefix, severities: [critical && "critical", warning && "warning"].filter(Boolean) as string[] });
  return (
    <div className="flex flex-col gap-4">
      <p className="m-0 text-sm text-muted-foreground">Any trigger starts the same bot. Run now and chat are always available. A scheduled bot needs no monitoring trigger.</p>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {(bot.triggers ?? []).map((t) => (
          <li key={t.trigger_id} className="flex flex-wrap items-center gap-3 rounded-md border px-3 py-2 text-sm">
            <Switch checked={t.enabled} onCheckedChange={(v) => void setTrigger(t.trigger_id, v)} aria-label={`Enable ${triggerText(t)}`} />
            <span className="min-w-0 flex-1">{triggerText(t)}
              <span className="block text-xs text-muted-foreground">
                {t.kind === "cron" ? `Next: ${t.enabled ? when(t.next_run_at) : "off"}` : "Runs when a matching finding opens"} · Last fired: {when(t.last_fired_at)}
              </span>
            </span>
            <Button variant="ghost" size="sm" onClick={() => void deleteTrigger(t.trigger_id)}>Remove</Button>
          </li>
        ))}
        {!(bot.triggers ?? []).length && <li className="text-sm text-muted-foreground">No triggers: this bot runs only when started by hand or asked.</li>}
      </ul>
      <fieldset className="flex flex-wrap items-end gap-2 rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">Add trigger</legend>
        <NativeSelect aria-label="Trigger kind" value={kind} onChange={(e) => setKind(e.target.value as "cron" | "signal")}>
          <NativeSelectOption value="cron">Schedule (cron)</NativeSelectOption>
          <NativeSelectOption value="signal">Monitoring signal</NativeSelectOption>
        </NativeSelect>
        {kind === "cron" ? <>
          <label className="flex flex-col gap-1 text-xs">Cron (min hour day month weekday)<Input value={cron} onChange={(e) => setCron(e.target.value)} className="w-40 font-mono" /></label>
          <label className="flex flex-col gap-1 text-xs">Timezone<Input value={tz} onChange={(e) => setTz(e.target.value)} className="w-40" /></label>
        </> : <>
          <label className="flex flex-col gap-1 text-xs">Detector starts with<Input value={prefix} onChange={(e) => setPrefix(e.target.value)} className="w-40 font-mono" /></label>
          <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={critical} onChange={(e) => setCritical(e.target.checked)} /> critical</label>
          <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={warning} onChange={(e) => setWarning(e.target.checked)} /> warning</label>
        </>}
        <Button size="sm" disabled={busy} onClick={add}>Add</Button>
      </fieldset>
    </div>
  );
}

function Tools({ bot }: { bot: Bot }) {
  const readTools = useBotsStore((s) => s.readTools);
  const save = useBotsStore((s) => s.save);
  const busy = useBotsStore((s) => s.busy);
  const [limits, setLimits] = useState(bot.limits);
  useEffect(() => setLimits(bot.limits), [bot.bot_id, bot.version]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (tool: string, on: boolean) => void save({ tools: on ? [...bot.tools, tool] : bot.tools.filter((t) => t !== tool) });
  return (
    <div className="flex flex-col gap-4">
      <p className="m-0 text-sm text-muted-foreground">
        Every bot gets the agent's read tools (metrics, catalogue, diagnose, charts). Below are the extra live tools it may call through Seleric MCP.
        Bots are read-only for now: changes are proposed in the answer and a person makes them.
      </p>
      <ul className="m-0 grid list-none grid-cols-2 gap-1 p-0 max-md:grid-cols-1">
        {readTools.map((tool) => (
          <li key={tool}>
            <label className="flex items-center gap-2 text-sm font-mono">
              <input type="checkbox" checked={bot.tools.includes(tool)} disabled={busy} onChange={(e) => toggle(tool, e.target.checked)} />{tool}
            </label>
          </li>
        ))}
      </ul>
      <fieldset className="flex flex-wrap items-end gap-3 rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">Limits</legend>
        {Object.entries(limits).filter(([k]) => ENFORCED_LIMITS.includes(k)).map(([k, v]) => (
          <label key={k} className="flex flex-col gap-1 text-xs">{k.replaceAll("_", " ")}
            <Input type="number" min={0} value={v} onChange={(e) => setLimits({ ...limits, [k]: Number(e.target.value) })} className="w-28" />
          </label>
        ))}
        <Button size="sm" disabled={busy || JSON.stringify(limits) === JSON.stringify(bot.limits)} onClick={() => void save({ limits })}>Save limits</Button>
        <p className="m-0 w-full text-xs text-muted-foreground">Runs per day counts schedule and monitoring runs; run now and chat are not limited. Each run also stops at the agent's own tool-call cap.</p>
      </fieldset>
    </div>
  );
}

function Chat({ chat }: { chat: BotRun[] }) {
  const ask = useBotsStore((s) => s.ask);
  const busy = useBotsStore((s) => s.busy);
  const [text, setText] = useState("");
  const send = () => { if (text.trim()) { void ask(text); setText(""); } };
  return (
    <div className="flex flex-col gap-3">
      <ol className="m-0 flex list-none flex-col gap-3 p-0">
        {chat.map((r) => (
          <li key={r.run_id} className="flex flex-col gap-1">
            <div className="self-end rounded-lg bg-[var(--brand-soft)] px-3 py-2 text-sm">{r.query}</div>
            <div className="rounded-lg border px-3 py-2 text-sm whitespace-pre-wrap">
              {r.status === "queued" || r.status === "running" ? <span className="text-muted-foreground">Working…</span>
                : r.answer || r.error || <span className="text-muted-foreground">No answer.</span>}
            </div>
          </li>
        ))}
        {!chat.length && <li className="text-sm text-muted-foreground">Ask this bot anything; it answers with its own instructions and tools.</li>}
      </ol>
      <div className="flex gap-2">
        <Textarea aria-label="Message the bot" value={text} rows={2} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <Button disabled={busy || !text.trim()} onClick={send}>Send</Button>
      </div>
    </div>
  );
}

function Runs({ runs }: { runs: BotRun[] }) {
  if (!runs.length) return <p className="text-sm text-muted-foreground">No runs yet.</p>;
  return (
    <ol className="m-0 flex list-none flex-col gap-2 p-0">
      {runs.map((r) => (
        <li key={r.run_id} className="rounded-md border px-3 py-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{r.trigger_kind}</Badge>
            <span className="font-medium">{STATUS_LABEL[r.status] ?? r.status}</span>
            {r.outcome && <Badge variant={OUTCOME_BADGE[r.outcome] ?? "secondary"}>{r.outcome.replace("_", " ")}</Badge>}
            <span className="ml-auto text-xs text-muted-foreground">{when(r.created_at)}{r.instructions_version ? ` · instructions v${r.instructions_version}` : ""}</span>
          </div>
          <p className="m-0 mt-1 text-xs text-muted-foreground">{r.reason}</p>
          {r.merged.length > 0 && <p className="m-0 text-xs text-muted-foreground">+ {r.merged.length} merged: {r.merged.map((m) => m.reason).join(" | ")}</p>}
          {(r.answer || r.error) && (
            <details className="mt-1"><summary className="cursor-pointer text-xs">Answer</summary>
              <div className="mt-1 text-sm whitespace-pre-wrap">{r.answer || r.error}</div>
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}

/** What a step adds beyond tool/target/scope: the reason for a denial or failure, a script's purpose and result, the
 * trigger or answer line. A completed read's raw payload says nothing to a reader, so it is left out. */
export function stepText(a: BotActivity): string {
  if (a.kind !== "tool") return a.summary;
  if (a.outcome === "completed" && a.summary.trimStart().startsWith("{")) return "";
  return a.summary.slice(0, 200);
}

export function ActivityTimeline({ activity }: { activity: BotActivity[] }) {
  const groups = useMemo(() => groupByRun(activity), [activity]);
  if (!groups.length) return <p className="m-0 text-sm text-muted-foreground">Nothing yet. Every trigger, tool call and answer shows here.</p>;
  return (
    <ol className="m-0 flex list-none flex-col gap-4 p-0">
      {groups.map((g) => (
        <li key={g.runId}>
          <p className="m-0 mb-1 text-xs font-medium text-muted-foreground">Run {g.runId.slice(-6)} · {when(g.rows[0].at)}</p>
          <ol className="m-0 flex list-none flex-col gap-1.5 border-l pl-3">
            {g.rows.map((a) => (
              <li key={a.id} className="flex gap-2 text-xs">
                <span aria-hidden className={cn("mt-1 size-1.5 shrink-0 rounded-full", OUTCOME_TONE[a.outcome])} />
                <span className="min-w-0 flex-1 break-words">
                  <span className="font-medium">{a.kind === "tool" ? a.tool : a.kind === "answer" ? `Answer${a.decision ? `: ${a.decision.replace("_", " ")}` : ""}` : a.kind === "trigger" ? "Trigger" : "Note"}</span>
                  <span className="text-muted-foreground"> · {a.outcome}</span>
                  {a.target && <span className="text-muted-foreground"> · {a.target}</span>}
                  {a.scope && <span className="block text-muted-foreground">scope {a.scope} · {a.decision}</span>}
                  {stepText(a) && <span className="block text-muted-foreground">{stepText(a)}</span>}
                </span>
              </li>
            ))}
          </ol>
        </li>
      ))}
    </ol>
  );
}

function BotDetail({ bot }: { bot: Bot }) {
  const runs = useBotsStore((s) => s.runs);
  const chat = useBotsStore((s) => s.chat);
  const save = useBotsStore((s) => s.save);
  const runNow = useBotsStore((s) => s.runNow);
  const busy = useBotsStore((s) => s.busy);
  const activity = useBotsStore((s) => s.activity);
  const running = runs.some((r) => r.status === "queued" || r.status === "running");
  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="m-0 flex items-center gap-2 font-serif text-[30px] leading-tight font-normal tracking-tight"><BotIcon />{bot.name}</h1>
          <p className="m-0 text-sm text-muted-foreground">{bot.role} · brand {bot.brand_id} · read-only</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm"><Switch checked={bot.enabled} onCheckedChange={(v) => void save({ enabled: v })} aria-label="Bot enabled" />{bot.enabled ? "On" : "Paused"}</label>
          <Button disabled={busy || !bot.enabled} onClick={() => void runNow()}>{running ? "Run queued…" : "Run now"}</Button>
        </div>
      </header>
      <Tabs defaultValue="runs">
        <TabsList className="max-w-full justify-start overflow-x-auto">
          <TabsTrigger value="runs">Runs</TabsTrigger>
          <TabsTrigger value="chat">Chat</TabsTrigger>
          <TabsTrigger value="instructions">Instructions</TabsTrigger>
          <TabsTrigger value="triggers">Triggers</TabsTrigger>
          <TabsTrigger value="tools">Tools &amp; limits</TabsTrigger>
          <TabsTrigger value="activity" className="lg:hidden">Activity</TabsTrigger>
        </TabsList>
        <TabsContent value="runs" className="pt-3"><Runs runs={runs} /></TabsContent>
        <TabsContent value="chat" className="pt-3"><Chat chat={chat} /></TabsContent>
        <TabsContent value="instructions" className="pt-3"><Instructions bot={bot} /></TabsContent>
        <TabsContent value="triggers" className="pt-3"><Triggers bot={bot} /></TabsContent>
        <TabsContent value="tools" className="pt-3"><Tools bot={bot} /></TabsContent>
        <TabsContent value="activity" className="pt-3 lg:hidden"><ActivityTimeline activity={activity} /></TabsContent>
      </Tabs>
    </div>
  );
}

export function BotsWorkspace() {
  const loadList = useBotsStore((s) => s.loadList);
  const refresh = useBotsStore((s) => s.refresh);
  const bot = useBotsStore((s) => s.bot);
  const activity = useBotsStore((s) => s.activity);
  const error = useBotsStore((s) => s.error);
  const selectedId = useBotsStore((s) => s.selectedId);

  useEffect(() => {
    void loadList();
    const id = window.setInterval(() => { if (!document.hidden) { void refresh(); void loadList(); } }, POLL_MS);
    return () => window.clearInterval(id);
  }, [loadList, refresh]);

  return (
    <AppFrame workspace="bots" sidebarLabel="Bots" toggleLabel="Toggle navigation" sidebarBody={<BotList />}>
      <div className="flex min-h-0 flex-1">
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto px-4 pb-10 max-md:px-3">
          {error && <p role="alert" className="m-0 mb-3 rounded-md bg-[var(--danger-soft)] px-3 py-2 text-sm text-destructive">{error}</p>}
          {bot ? <BotDetail bot={bot} /> : <p className="text-sm text-muted-foreground">{selectedId ? "Loading…" : "Create a bot from a template in the left panel."}</p>}
        </main>
        {bot && (
          <aside aria-label="Bot activity" className="w-[340px] shrink-0 overflow-y-auto border-l px-3 py-3 max-lg:hidden">
            <h2 className="m-0 mb-3 font-serif text-lg font-medium">Activity</h2>
            <ActivityTimeline activity={activity} />
          </aside>
        )}
      </div>
    </AppFrame>
  );
}
