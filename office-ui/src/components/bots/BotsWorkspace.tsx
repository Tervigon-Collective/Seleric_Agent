import { useEffect, useMemo, useState } from "react";
import { cn } from "cn";
import { AppFrame } from "../AppFrame";
import { ActivityIcon, BotIcon, PlusIcon, SettingsIcon, XIcon } from "../icons";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
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

const OUTCOME_TONE: Record<string, string> = {
  completed: "bg-[var(--success)]", skipped: "bg-muted-foreground/50", attempted: "bg-[var(--warning)]",
  denied: "bg-destructive", failed: "bg-destructive",
};
const STATUS_LABEL: Record<string, string> = {
  queued: "Queued", running: "Running", completed: "Done", partial: "Partial", failed: "Failed", skipped: "Skipped",
};

/** "0 9 * * *" reads as "Every day at 09:00"; any other cron is shown as written. */
export function cronText(cron: string, tz: string): string {
  const [min = "", hour = "", ...rest] = cron.trim().split(" ").filter(Boolean);
  const digits = (x: string) => x.length > 0 && [...x].every((c) => c >= "0" && c <= "9");
  const daily = rest.length === 3 && rest.every((x) => x === "*") && digits(min) && digits(hour);
  return daily ? `Every day at ${hour.padStart(2, "0")}:${min.padStart(2, "0")} (${tz})` : `Schedule ${cron} (${tz})`;
}

export function triggerText(t: BotTrigger): string {
  if (t.kind === "cron") return cronText(String(t.config.cron), String(t.config.timezone));
  if (t.kind === "watch") return `When ${humanize(String(t.config.tool)).toLowerCase()} changes: ${(t.config.fields ?? []).map((f: string) => f.replaceAll("_", " ")).join(", ")} · checked every ${t.config.interval_min} min`;
  if (t.kind === "webhook") return "When another system calls its webhook";
  return `On monitoring alerts ${t.config.detector_prefix}* · ${(t.config.severities ?? []).join(", ")}`;
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
              <span className="truncate text-xs text-muted-foreground">{b.role}</span>
              <span className="mt-0.5 text-xs text-muted-foreground">
                {b.last_run ? <>Last: <span className="text-foreground/80">{verdictOf(b.last_run).label}</span>, {when(b.last_run.created_at)}</> : "Not run yet"}
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
        <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={18} className="text-sm leading-relaxed" />
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
            <div className="rounded-lg border px-3 py-2 text-sm leading-relaxed">
              {r.status === "queued" || r.status === "running" ? <span className="text-muted-foreground">Working…</span>
                : r.answer ? <SafeContent text={r.answer} /> : <span className="whitespace-pre-wrap">{r.error || "No answer."}</span>}
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

/** Splits the closing "OUTCOME: x — reason" line off the answer so the verdict can lead the card. */
export function splitAnswer(answer: string): { body: string; reason: string } {
  const lines = answer.trimEnd().split("\n");
  const last = (lines.at(-1) ?? "").replaceAll("*", "").trim();
  if (!last.toUpperCase().startsWith("OUTCOME:")) return { body: answer, reason: "" };
  const rest = last.slice("OUTCOME:".length);
  const dash = rest.indexOf("—");
  const reason = dash >= 0 ? rest.slice(dash + 1).trim() : "";
  return { body: lines.slice(0, -1).join("\n").trim(), reason: reason.charAt(0).toUpperCase() + reason.slice(1) };
}

/** The run's verdict in the operator's words (B17 outcomes); tone = band colour. */
const VERDICT: Record<string, { label: string; tone: string; dot: string }> = {
  action_needed: { label: "Needs action", tone: "bg-[var(--danger-soft)] text-destructive", dot: "bg-destructive" },
  unverified: { label: "Couldn't check", tone: "bg-[var(--warning-soft)] text-[var(--warning)]", dot: "bg-[var(--warning)]" },
  cleared: { label: "Alert cleared", tone: "bg-[var(--success-soft)] text-[var(--success)]", dot: "bg-[var(--success)]" },
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

function RunMeta({ r }: { r: BotRun }) {
  return (
    <p className="m-0 text-xs text-muted-foreground">
      <span className="font-medium text-foreground/80">Why it ran:</span> {r.reason || KIND_LABEL[r.trigger_kind]}
      {r.merged.length > 0 && <> (+ {r.merged.length} more {r.merged.length === 1 ? "trigger" : "triggers"} while it waited)</>}
      {r.instructions_version ? <> · instructions v{r.instructions_version}</> : null}
      {r.status === "partial" && <> · some figures could not be traced to a source</>}
    </p>
  );
}

function RunBody({ r, body }: { r: BotRun; body: string }) {
  if (r.answer) return <div className="max-w-[72ch] text-sm leading-relaxed"><SafeContent text={body} /></div>;
  if (r.error) return <p className="m-0 text-sm whitespace-pre-wrap text-destructive">{r.error}</p>;
  return live(r) ? <p className="m-0 text-sm text-muted-foreground">The bot is working; its answer appears here.</p> : null;
}

/** The newest run, read first: verdict band, the bot's reason, then the answer. */
function LatestRun({ r }: { r: BotRun }) {
  const { body, reason } = splitAnswer(r.answer ?? "");
  const v = verdictOf(r);
  return (
    <section aria-label="Latest run" className="overflow-hidden rounded-xl border">
      <div className={cn("flex flex-wrap items-baseline gap-x-3 gap-y-1 px-5 py-3 max-sm:px-4", v.tone)}>
        <h2 className="m-0 text-lg font-semibold">{v.label}</h2>
        <span className="text-xs opacity-80">{KIND_LABEL[r.trigger_kind] ?? r.trigger_kind} · {when(r.created_at)}</span>
        {reason && <p className="m-0 w-full text-sm text-foreground">{reason}</p>}
      </div>
      <div className="flex flex-col gap-3 px-5 py-4 max-sm:px-4">
        <RunBody r={r} body={body} />
        <RunMeta r={r} />
      </div>
    </section>
  );
}

function EarlierRun({ r }: { r: BotRun }) {
  const { body, reason } = splitAnswer(r.answer ?? "");
  const v = verdictOf(r);
  return (
    <li className="border-b last:border-b-0">
      <details className="group">
        <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-0.5 px-1 py-2.5 text-sm hover:bg-accent/50 [&::-webkit-details-marker]:hidden">
          <span aria-hidden className={cn("size-2 shrink-0 rounded-full", v.dot)} />
          <span className="shrink-0 font-medium sm:w-28">{v.label}</span>
          <span className="min-w-0 flex-1 truncate text-muted-foreground max-sm:order-last max-sm:line-clamp-2 max-sm:basis-full max-sm:pl-5 max-sm:whitespace-normal">{reason || r.reason}</span>
          <span className="shrink-0 text-xs text-muted-foreground max-sm:hidden">{KIND_LABEL[r.trigger_kind] ?? r.trigger_kind}</span>
          <span className="shrink-0 text-right text-xs whitespace-nowrap text-muted-foreground max-sm:ml-auto sm:w-24">{when(r.created_at)}</span>
        </summary>
        <div className="flex flex-col gap-3 pt-1 pb-4 pl-6">
          {reason && <p className="m-0 text-sm font-medium">{reason}</p>}
          <RunBody r={r} body={body} />
          <RunMeta r={r} />
        </div>
      </details>
    </li>
  );
}

function Runs({ runs }: { runs: BotRun[] }) {
  if (!runs.length) return <p className="text-sm text-muted-foreground">No runs yet. Press Run now, or add a trigger so the bot runs by itself.</p>;
  const [latest, ...earlier] = runs;
  return (
    <div className="flex flex-col gap-6">
      <LatestRun r={latest} />
      {earlier.length > 0 && (
        <section aria-label="Earlier runs">
          <h2 className="m-0 mb-1 text-sm font-medium text-muted-foreground">Earlier runs</h2>
          <ol className="m-0 list-none p-0">{earlier.map((r) => <EarlierRun key={r.run_id} r={r} />)}</ol>
        </section>
      )}
    </div>
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
  bot_eda: "Statistics (EDA)", run_python: "Calculation", meta_campaigns_list: "Live campaign list",
  meta_campaigns_get: "Live campaign details", meta_insights_live: "Live performance",
};

/** Tool names a person has not seen before read as words: "meta_adsets_list" → "Meta adsets list". */
const humanize = (name: string) => name.charAt(0).toUpperCase() + name.slice(1).replaceAll("_", " ");

export function stepLabel(a: BotActivity): string {
  if (a.kind === "answer") return `Answer${a.decision ? `: ${(VERDICT[a.decision]?.label ?? a.decision.replace("_", " ")).toLowerCase()}` : ""}`;
  if (a.kind === "trigger") return "Started";
  if (a.kind === "note") return "Note";
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

/** Everything that configures the bot, kept out of the way of its reports (owner 2026-11-26). */
function BotSettings({ bot, open, onOpenChange }: { bot: Bot; open: boolean; onOpenChange: (v: boolean) => void }) {
  const busy = useBotsStore((s) => s.busy);
  const remove = useBotsStore((s) => s.remove);
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-2xl">
        <SheetHeader className="border-b px-6 py-4 pr-12 max-sm:px-4 max-sm:pr-12">
          <SheetTitle className="m-0 text-lg">Settings · {bot.name}</SheetTitle>
          <SheetDescription className="m-0">Changes apply from the bot's next run.</SheetDescription>
        </SheetHeader>
        <Tabs defaultValue="general" className="min-h-0 flex-1">
          <TabsList className="mx-6 mt-4 max-w-[calc(100%-3rem)] max-sm:mx-4 max-sm:max-w-[calc(100%-2rem)] justify-start overflow-x-auto overflow-y-hidden [scrollbar-width:none]">
            <TabsTrigger value="general">General</TabsTrigger>
            <TabsTrigger value="triggers">When it runs</TabsTrigger>
            <TabsTrigger value="skills">Skills</TabsTrigger>
            <TabsTrigger value="memory">Memory</TabsTrigger>
            <TabsTrigger value="tools">Tools &amp; email</TabsTrigger>
          </TabsList>
          <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-4 pb-8 max-sm:px-4">
            <TabsContent value="general" className="flex flex-col gap-8">
              <Instructions bot={bot} />
              <section className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/30 px-4 py-3">
                <div>
                  <h3 className="m-0 text-sm font-medium">Delete this bot</h3>
                  <p className="m-0 text-xs text-muted-foreground">Removes its runs, activity, triggers and skills for good. Its memory is kept.</p>
                </div>
                <Button variant="destructive" disabled={busy}
                  onClick={() => window.confirm(`Delete “${bot.name}”? Its runs, activity, triggers and skills are removed for good. Its memory is kept.`) && void remove(bot.bot_id)}>Delete bot</Button>
              </section>
            </TabsContent>
            <TabsContent value="triggers"><Triggers bot={bot} /></TabsContent>
            <TabsContent value="skills"><Skills bot={bot} /></TabsContent>
            <TabsContent value="memory"><Memory bot={bot} /></TabsContent>
            <TabsContent value="tools"><Tools bot={bot} /></TabsContent>
          </div>
        </Tabs>
      </SheetContent>
    </Sheet>
  );
}

function BotDetail({ bot, onShowActivity }: { bot: Bot; onShowActivity?: () => void }) {
  const runs = useBotsStore((s) => s.runs);
  const chat = useBotsStore((s) => s.chat);
  const save = useBotsStore((s) => s.save);
  const runNow = useBotsStore((s) => s.runNow);
  const busy = useBotsStore((s) => s.busy);
  const activity = useBotsStore((s) => s.activity);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const running = runs.some((r) => r.status === "queued" || r.status === "running");
  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h1 className="m-0 flex items-center gap-2 font-serif text-[26px] leading-tight font-semibold tracking-tight"><BotIcon aria-hidden />{bot.name}</h1>
          <p className="m-0 mt-1 flex flex-wrap items-center gap-x-1.5 text-sm text-muted-foreground">
            <span>{bot.role}</span>
            <span aria-hidden>·</span>
            <span aria-hidden className={cn("size-1.5 rounded-full", bot.enabled ? "bg-[var(--success)]" : "bg-muted-foreground/40")} />
            <span>{bot.enabled ? (bot.next_run_at ? `Next report ${when(bot.next_run_at)}` : "On") : "Paused"}</span>
            <span aria-hidden>·</span>
            <span title="It proposes changes; a person makes them.">Read-only</span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="mr-1 flex items-center gap-2 text-sm"><Switch checked={bot.enabled} onCheckedChange={(v) => void save({ enabled: v })} aria-label="Bot on" />{bot.enabled ? "On" : "Paused"}</label>
          <Button disabled={busy || !bot.enabled || running} onClick={() => void runNow()}>{running ? "Running…" : "Run now"}</Button>
          <Button variant="outline" onClick={() => setSettingsOpen(true)}><SettingsIcon /> Settings</Button>
          {onShowActivity && <Button variant="ghost" className="max-lg:hidden" aria-label="Show activity" onClick={onShowActivity}><ActivityIcon /> Activity</Button>}
        </div>
      </header>
      <Tabs defaultValue="runs">
        <TabsList>
          <TabsTrigger value="runs">Reports</TabsTrigger>
          <TabsTrigger value="chat">Chat</TabsTrigger>
          <TabsTrigger value="activity" className="lg:hidden">Activity</TabsTrigger>
        </TabsList>
        <TabsContent value="runs" className="pt-4"><Runs runs={runs.filter((r) => r.trigger_kind !== "chat")} /></TabsContent>
        <TabsContent value="chat" className="pt-4"><Chat chat={chat} /></TabsContent>
        <TabsContent value="activity" className="pt-4 lg:hidden"><ActivityTimeline activity={activity} /></TabsContent>
      </Tabs>
      <BotSettings bot={bot} open={settingsOpen} onOpenChange={setSettingsOpen} />
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
  const [activityOpen, setActivityOpen] = useState(true);

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
          {bot ? <div className="mx-auto max-w-4xl pt-1"><BotDetail bot={bot} onShowActivity={activityOpen ? undefined : () => setActivityOpen(true)} /></div> : <p className="text-sm text-muted-foreground">{selectedId ? "Loading…" : "Create a bot from a template in the left panel."}</p>}
        </main>
        {bot && activityOpen && (
          <aside aria-label="Bot activity" className="w-[340px] shrink-0 overflow-y-auto border-l px-3 py-3 max-lg:hidden">
            <div className="mb-3 flex items-center justify-between gap-2">
              <h2 className="m-0 font-serif text-lg font-medium">Activity</h2>
              <Button variant="ghost" size="icon-sm" aria-label="Hide activity" onClick={() => setActivityOpen(false)}><XIcon /></Button>
            </div>
            <ActivityTimeline activity={activity} />
          </aside>
        )}
      </div>
    </AppFrame>
  );
}
