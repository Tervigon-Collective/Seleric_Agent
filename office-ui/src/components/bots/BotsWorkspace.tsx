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
import { SafeContent } from "../SafeContent";
import { botsApi, type Bot, type BotActivity, type BotRun, type BotSkill, type BotTrigger, type MemoryBank, type MemoryItem, type TriggerKind } from "../../api/bots";
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
  if (t.kind === "watch") return `Watch ${t.config.tool}: ${(t.config.fields ?? []).join(", ")} · every ${t.config.interval_min} min`;
  if (t.kind === "webhook") return "Webhook";
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
              </span>
              <span className="text-xs text-muted-foreground">Next: {when(b.next_run_at)}</span>
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
  const newSecret = useBotsStore((s) => s.newSecret);
  const [kind, setKind] = useState<TriggerKind>("cron");
  const [tool, setTool] = useState("");
  const [fields, setFields] = useState("effective_status, daily_budget");
  const [args, setArgs] = useState("{}");
  const [interval, setInterval_] = useState(15);
  const [argsError, setArgsError] = useState("");
  const [cron, setCron] = useState("0 9 * * *");
  const [tz, setTz] = useState("Asia/Kolkata");
  const [prefix, setPrefix] = useState("live.meta.");
  const [critical, setCritical] = useState(true);
  const [warning, setWarning] = useState(true);
  const watchTool = tool || bot.tools[0] || "";
  const add = () => {
    if (kind === "cron") return void addTrigger(kind, { cron, timezone: tz });
    if (kind === "signal") return void addTrigger(kind, { detector_prefix: prefix, severities: [critical && "critical", warning && "warning"].filter(Boolean) as string[] });
    if (kind === "webhook") return void addTrigger(kind, {});
    let parsed: Record<string, unknown>;
    try { parsed = JSON.parse(args || "{}"); setArgsError(""); } catch { setArgsError("Arguments must be JSON, e.g. {\"account_id\": \"act_…\"}"); return; }
    void addTrigger(kind, { tool: watchTool, args: parsed, fields: fields.split(",").map((f) => f.trim()).filter(Boolean), interval_min: interval });
  };
  return (
    <div className="flex flex-col gap-4">
      <p className="m-0 text-sm text-muted-foreground">Any trigger starts the same bot. Run now and chat are always available. A scheduled bot needs no monitoring trigger.</p>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {(bot.triggers ?? []).map((t) => (
          <li key={t.trigger_id} className="flex flex-wrap items-center gap-3 rounded-md border px-3 py-2 text-sm">
            <Switch checked={t.enabled} onCheckedChange={(v) => void setTrigger(t.trigger_id, v)} aria-label={`Enable ${triggerText(t)}`} />
            <span className="min-w-0 flex-1">{triggerText(t)}
              <span className="block text-xs text-muted-foreground">
                {triggerDetail(t)} · Last fired: {when(t.last_fired_at)}
              </span>
              {t.kind === "webhook" && <span className="block font-mono text-xs break-all">POST {hookUrl(t.trigger_id)}</span>}
            </span>
            <Button variant="ghost" size="sm" onClick={() => void deleteTrigger(t.trigger_id)}>Remove</Button>
          </li>
        ))}
        {!(bot.triggers ?? []).length && <li className="text-sm text-muted-foreground">No triggers: this bot runs only when started by hand or asked.</li>}
      </ul>
      {newSecret && (
        <div role="status" className="flex flex-col gap-1 rounded-md border border-[var(--warning)] px-3 py-2 text-sm">
          <span className="font-medium">Webhook secret — copy it now, it is not shown again.</span>
          <span className="font-mono text-xs break-all">POST {hookUrl(newSecret.triggerId)}</span>
          <span className="font-mono text-xs break-all">X-Bot-Secret: {newSecret.secret}</span>
        </div>
      )}
      <fieldset className="flex flex-wrap items-end gap-2 rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">Add trigger</legend>
        <NativeSelect aria-label="Trigger kind" value={kind} onChange={(e) => setKind(e.target.value as TriggerKind)}>
          <NativeSelectOption value="cron">Schedule (cron)</NativeSelectOption>
          <NativeSelectOption value="signal">Monitoring signal</NativeSelectOption>
          <NativeSelectOption value="watch">Watch a live value</NativeSelectOption>
          <NativeSelectOption value="webhook">Webhook</NativeSelectOption>
        </NativeSelect>
        {kind === "watch" ? <>
          <label className="flex flex-col gap-1 text-xs">Tool
            <NativeSelect aria-label="Watch tool" value={watchTool} onChange={(e) => setTool(e.target.value)}>
              {bot.tools.map((t) => <NativeSelectOption key={t} value={t}>{t}</NativeSelectOption>)}
            </NativeSelect>
          </label>
          <label className="flex flex-col gap-1 text-xs">Fields that start a run<Input value={fields} onChange={(e) => setFields(e.target.value)} className="w-56 font-mono" /></label>
          <label className="flex flex-col gap-1 text-xs">Arguments (JSON)<Input value={args} onChange={(e) => setArgs(e.target.value)} className="w-56 font-mono" /></label>
          <label className="flex flex-col gap-1 text-xs">Every (min)<Input type="number" min={5} value={interval} onChange={(e) => setInterval_(Number(e.target.value))} className="w-20" /></label>
          {argsError && <p role="alert" className="m-0 w-full text-xs text-destructive">{argsError}</p>}
        </> : kind === "webhook" ? <span className="text-xs text-muted-foreground">You get a URL and a secret to call it with.</span>
        : kind === "cron" ? <>
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

export const hookUrl = (triggerId: string) => `${window.location.origin}/hooks/bots/${triggerId}`;

function triggerDetail(t: BotTrigger): string {
  if (!t.enabled) return "Off";
  if (t.kind === "cron") return `Next: ${when(t.next_run_at)}`;
  if (t.kind === "signal") return "Runs when a matching finding opens";
  if (t.kind === "watch") return t.state?.error ? `Last check failed: ${t.state.error}` : `Last check: ${when(t.state?.checked_at)}`;
  return "Runs when called with its secret";
}

function SkillEditor({ skill, onDone }: { skill?: BotSkill; onDone: () => void }) {
  const saveSkill = useBotsStore((s) => s.saveSkill);
  const busy = useBotsStore((s) => s.busy);
  const [name, setName] = useState(skill?.name ?? "");
  const [body, setBody] = useState(skill?.body ?? "");
  return (
    <div className="flex flex-col gap-2 rounded-md border p-3">
      <label className="flex flex-col gap-1 text-sm">Name<Input value={name} onChange={(e) => setName(e.target.value)} /></label>
      <label className="flex flex-col gap-1 text-sm">What to do<Textarea value={body} onChange={(e) => setBody(e.target.value)} rows={6} className="font-mono text-xs" /></label>
      <div className="flex gap-2">
        <Button size="sm" disabled={busy || !name.trim() || !body.trim()}
          onClick={async () => { if (await saveSkill({ skill_id: skill?.skill_id, name, body, enabled: skill?.enabled ?? true })) onDone(); }}>Save</Button>
        <Button size="sm" variant="ghost" onClick={onDone}>Cancel</Button>
      </div>
    </div>
  );
}

function Skills({ bot }: { bot: Bot }) {
  const saveSkill = useBotsStore((s) => s.saveSkill);
  const deleteSkill = useBotsStore((s) => s.deleteSkill);
  const [editing, setEditing] = useState<string | null>(null);
  const skills = bot.skills ?? [];
  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 text-sm text-muted-foreground">Named how-tos added to every run while on.</p>
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {skills.map((k) => editing === k.skill_id ? <li key={k.skill_id}><SkillEditor skill={k} onDone={() => setEditing(null)} /></li> : (
          <li key={k.skill_id} className="flex flex-wrap items-start gap-3 rounded-md border px-3 py-2 text-sm">
            <Switch checked={k.enabled} aria-label={`Enable ${k.name}`}
              onCheckedChange={(v) => void saveSkill({ skill_id: k.skill_id, name: k.name, body: k.body, enabled: v })} />
            <span className="min-w-0 flex-1"><span className="font-medium">{k.name}</span>
              <span className="block text-xs whitespace-pre-wrap text-muted-foreground">{k.body.slice(0, 300)}</span></span>
            <Button variant="ghost" size="sm" onClick={() => setEditing(k.skill_id)}>Edit</Button>
            <Button variant="ghost" size="sm" onClick={() => void deleteSkill(k.skill_id)}>Remove</Button>
          </li>
        ))}
      </ul>
      {editing === "new" ? <SkillEditor onDone={() => setEditing(null)} />
        : <Button size="sm" variant="outline" className="self-start" onClick={() => setEditing("new")}><PlusIcon /> Add skill</Button>}
    </div>
  );
}

function Memory({ bot }: { bot: Bot }) {
  const [bank, setBank] = useState<MemoryBank>("bot");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const load = async () => {
    setError("");
    try { setItems((await botsApi.memory(bot.bot_id, bank, q.trim())).items); } catch (e) { setItems([]); setError(e instanceof Error ? e.message : String(e)); }
  };
  useEffect(() => { void load(); }, [bot.bot_id, bank]); // eslint-disable-line react-hooks/exhaustive-deps
  const act = async (fn: () => Promise<unknown>) => {
    setPending(true);
    try { await fn(); await load(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setPending(false); }
  };
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-2">
        <NativeSelect aria-label="Memory bank" value={bank} onChange={(e) => setBank(e.target.value as MemoryBank)}>
          <NativeSelectOption value="bot">This bot</NativeSelectOption>
          <NativeSelectOption value="shared">Brand {bot.brand_id} (all bots)</NativeSelectOption>
        </NativeSelect>
        <Input aria-label="Search memory" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search"
          onKeyDown={(e) => { if (e.key === "Enter") void load(); }} className="w-56" />
      </div>
      {error && <p role="alert" className="m-0 text-sm text-destructive">{error}</p>}
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0" aria-label="Memories">
        {(items ?? []).map((m) => (
          <li key={m.id} className="flex items-start gap-2 rounded-md border px-3 py-2 text-sm">
            <span className="min-w-0 flex-1">{m.text}
              <span className="block text-xs text-muted-foreground">{when(m.mentioned_at || m.date)}{m.document_id?.startsWith("run_") ? " · from a run" : m.document_id ? " · note" : ""}</span>
            </span>
            {m.document_id && <Button variant="ghost" size="sm" disabled={pending} onClick={() => void act(() => botsApi.forget(bot.bot_id, bank, m.document_id!))}>Forget</Button>}
          </li>
        ))}
        {items && !items.length && !error && <li className="text-sm text-muted-foreground">Nothing remembered yet.</li>}
      </ul>
      <div className="flex gap-2">
        <Textarea aria-label="New note" value={note} rows={2} onChange={(e) => setNote(e.target.value)} placeholder="Something the bot should know" />
        <Button disabled={pending || !note.trim()} onClick={() => void act(async () => { await botsApi.addNote(bot.bot_id, bank, note.trim()); setNote(""); setSaved(true); })}>Add note</Button>
      </div>
      {saved && <p role="status" className="m-0 text-xs text-muted-foreground">Saved. It shows here once memory has processed it (usually under a minute).</p>}
    </div>
  );
}

function Tools({ bot }: { bot: Bot }) {
  const readTools = useBotsStore((s) => s.readTools);
  const save = useBotsStore((s) => s.save);
  const busy = useBotsStore((s) => s.busy);
  const [limits, setLimits] = useState(bot.limits);
  const savedEmail = (bot.notify?.email ?? []).join(", ");
  const [email, setEmail] = useState(savedEmail);
  useEffect(() => { setLimits(bot.limits); setEmail(savedEmail); }, [bot.bot_id, bot.version]); // eslint-disable-line react-hooks/exhaustive-deps
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
        <p className="m-0 w-full text-xs text-muted-foreground">Runs per day counts automatic runs (schedule, monitoring, watch, webhook); run now and chat are not limited. Each run also stops at the agent's own tool-call cap.</p>
      </fieldset>
      <fieldset className="flex flex-wrap items-end gap-3 rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">Email</legend>
        <label className="flex min-w-0 flex-1 flex-col gap-1 text-xs">Recipients (names, groups or addresses from the email contacts)
          <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Nobody" />
        </label>
        <Button size="sm" disabled={busy || email === savedEmail}
          onClick={() => void save({ notify: { email: email.split(",").map((x) => x.trim()).filter(Boolean) } })}>Save recipients</Button>
        <p className="m-0 w-full text-xs text-muted-foreground">Sent for every scheduled run, any “action needed”, and “could not check” twice in a row. Also listed in Notification Center.</p>
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
          <p title={r.reason} className="m-0 mt-1 line-clamp-2 text-xs wrap-anywhere text-muted-foreground">{r.reason}</p>
          {r.merged.length > 0 && <p className="m-0 line-clamp-2 text-xs wrap-anywhere text-muted-foreground">+ {r.merged.length} merged: {r.merged.map((m) => m.reason).join(" | ")}</p>}
          {(r.answer || r.error) && (
            <details className="mt-1"><summary className="cursor-pointer text-xs">Answer</summary>
              <div className="mt-2 text-sm">{r.answer ? <SafeContent text={r.answer} /> : <span className="whitespace-pre-wrap">{r.error}</span>}</div>
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

const STEP_NAME: Record<string, string> = {
  memory_recall: "Memory", memory_retain: "Saved to memory", notification_center: "Notified", email: "Email",
};

export function stepLabel(a: BotActivity): string {
  if (a.kind === "answer") return `Answer${a.decision ? `: ${a.decision.replace("_", " ")}` : ""}`;
  if (a.kind === "trigger") return "Trigger";
  if (a.kind === "note") return "Note";
  return STEP_NAME[a.tool ?? ""] ?? a.tool ?? a.kind;
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
                  <span className="font-medium">{stepLabel(a)}</span>
                  <span className="text-muted-foreground"> · {a.outcome}</span>
                  {a.target && <span className="text-muted-foreground"> · {a.target}</span>}
                  {a.scope && <span className="block text-muted-foreground">scope {a.scope} · {a.decision}</span>}
                  {stepText(a) && <span title={stepText(a)} className="line-clamp-3 wrap-anywhere text-muted-foreground">{stepText(a)}</span>}
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
        <TabsList className="max-w-full justify-start overflow-x-auto overflow-y-hidden [scrollbar-width:none]">
          <TabsTrigger value="runs">Runs</TabsTrigger>
          <TabsTrigger value="chat">Chat</TabsTrigger>
          <TabsTrigger value="instructions">Instructions</TabsTrigger>
          <TabsTrigger value="triggers">Triggers</TabsTrigger>
          <TabsTrigger value="skills">Skills</TabsTrigger>
          <TabsTrigger value="memory">Memory</TabsTrigger>
          <TabsTrigger value="tools">Tools &amp; limits</TabsTrigger>
          <TabsTrigger value="activity" className="lg:hidden">Activity</TabsTrigger>
        </TabsList>
        <TabsContent value="runs" className="pt-3"><Runs runs={runs} /></TabsContent>
        <TabsContent value="chat" className="pt-3"><Chat chat={chat} /></TabsContent>
        <TabsContent value="instructions" className="pt-3"><Instructions bot={bot} /></TabsContent>
        <TabsContent value="triggers" className="pt-3"><Triggers bot={bot} /></TabsContent>
        <TabsContent value="skills" className="pt-3"><Skills bot={bot} /></TabsContent>
        <TabsContent value="memory" className="pt-3"><Memory bot={bot} /></TabsContent>
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
          {bot ? <div className="mx-auto max-w-4xl pt-1"><BotDetail bot={bot} /></div> : <p className="text-sm text-muted-foreground">{selectedId ? "Loading…" : "Create a bot from a template in the left panel."}</p>}
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
