import { useEffect, useMemo, useState, type ReactNode } from "react";
import { cn } from "cn";
import { AppFrame } from "../AppFrame";
import { AlertIcon, BotIcon, ChevronLeftIcon, FileTextIcon, MessageIcon, PlayIcon, PlusIcon, SettingsIcon, SlidersIcon, XIcon } from "../icons";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useIsMobile } from "@/hooks/use-mobile";
import { SafeContent } from "../SafeContent";
import { botsApi, type AnswerCheck, type Bot, type BotActivity, type BotRun, type BotSkill, type BotTrigger, type EdaCheck, type MemoryBank, type MemoryItem, type Proposal, type TriggerKind } from "../../api/bots";
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
          <label className="flex items-center gap-1.5 text-xs"><Checkbox checked={critical} onCheckedChange={(v) => setCritical(v === true)} /> critical</label>
          <label className="flex items-center gap-1.5 text-xs"><Checkbox checked={warning} onCheckedChange={(v) => setWarning(v === true)} /> warning</label>
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
              <Checkbox checked={bot.tools.includes(tool)} disabled={busy} onCheckedChange={(v) => toggle(tool, v === true)} />{tool}
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

function Chat({ chat, draft = "" }: { chat: BotRun[]; draft?: string }) {
  const ask = useBotsStore((s) => s.ask);
  const busy = useBotsStore((s) => s.busy);
  const [text, setText] = useState(draft);
  useEffect(() => { if (draft) setText(draft); }, [draft]);
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

function RunMeta({ r }: { r: BotRun }) {
  return (
    <p title={r.reason} className="m-0 line-clamp-2 text-xs wrap-anywhere text-muted-foreground">
      <span className="font-medium text-foreground/80">Why it ran:</span> {r.reason || KIND_LABEL[r.trigger_kind]}
      {r.merged.length > 0 && <> (+ {r.merged.length} more {r.merged.length === 1 ? "trigger" : "triggers"} while it waited)</>}
      {r.instructions_version ? <> · instructions v{r.instructions_version}</> : null}
      {r.status === "partial" && <> · some figures could not be traced to a source</>}
    </p>
  );
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
          <TabsList className="mx-6 mt-4 max-w-[calc(100%-3rem)] justify-start overflow-x-auto overflow-y-hidden [scrollbar-width:none] max-sm:mx-4 max-sm:grid max-sm:h-auto max-sm:max-w-[calc(100%-2rem)] max-sm:grid-cols-3 max-sm:[&>button]:h-10">
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

/** A stop on the metro line: a ring, scarlet when it needs a person, dashed when it is only proposed. */
function Stop({ size = "md", tone = "navy", dashed = false }: { size?: "sm" | "md" | "lg"; tone?: "navy" | "signal" | "muted"; dashed?: boolean }) {
  return (
    <span aria-hidden className={cn("block shrink-0 rounded-full bg-card",
      size === "lg" ? "size-[18px] border-4" : size === "sm" ? "size-2.5 border-[2.5px]" : "size-3.5 border-[3px]",
      dashed && "border-dashed",
      tone === "signal" ? "border-[var(--signal)]" : tone === "muted" ? "border-muted-foreground/60" : "border-[var(--brand)] dark:border-foreground")} />
  );
}

function ItemRow({ item, selected, onOpen, big = false }: { item: InboxItem; selected: boolean; onOpen: () => void; big?: boolean }) {
  const { run, proposal } = item;
  const sub = proposal ? capital(proposal.condition || proposal.reason || proposal.field) : runLine(run);
  return (
    <li>
      <Button variant="ghost" onClick={onOpen} aria-current={selected || undefined}
        className={cn("grid h-auto w-full grid-cols-[18px_minmax(0,1fr)_auto] items-start gap-2.5 rounded-lg border border-transparent px-3 py-2.5 text-left font-normal whitespace-normal pointer-coarse:py-3.5",
          selected && "border-[var(--brand-soft-line)] bg-[var(--brand-soft)] hover:bg-[var(--brand-soft)]")}>
        <span className="pt-0.5"><Stop size={big ? "lg" : "md"} /></span>
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

/** Which bot the buttons act on, whether it is on, its settings, and a new bot from a template. */
function BotBar({ onSettings }: { onSettings: () => void }) {
  const bots = useBotsStore((s) => s.bots);
  const bot = useBotsStore((s) => s.bot);
  const select = useBotsStore((s) => s.select);
  const save = useBotsStore((s) => s.save);
  const templates = useBotsStore((s) => s.templates);
  const create = useBotsStore((s) => s.create);
  const busy = useBotsStore((s) => s.busy);
  return (
    <div className="flex items-center gap-2 px-4 pb-3 text-sm">
      {bots.length > 1
        ? <NativeSelect aria-label="Bot" value={bot?.bot_id ?? ""} onChange={(e) => void select(e.target.value)} className="min-w-0 flex-1">
            {bots.map((b) => <NativeSelectOption key={b.bot_id} value={b.bot_id}>{b.name}</NativeSelectOption>)}
          </NativeSelect>
        : <span className="flex min-w-0 flex-1 items-center gap-2 truncate font-semibold"><BotIcon aria-hidden />{bot?.name ?? "No bot yet"}</span>}
      {bot && (
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground pointer-coarse:min-h-11">
          <Switch checked={bot.enabled} onCheckedChange={(v) => void save({ enabled: v })} aria-label="Bot on" />{bot.enabled ? "On" : "Paused"}
        </label>
      )}
      {bot && <Button variant="ghost" size="icon-sm" className="pointer-coarse:size-11" aria-label="Bot settings" onClick={onSettings}><SettingsIcon /></Button>}
      {templates.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger aria-label="New bot" disabled={busy} className={cn(buttonVariants({ variant: "ghost", size: "icon-sm" }), "pointer-coarse:size-11")}><PlusIcon /></DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel>New bot from a template</DropdownMenuLabel>
            {templates.map((t) => <DropdownMenuItem key={t.template} onSelect={() => void create(t.template)}>{t.name}</DropdownMenuItem>)}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}

function InboxList({ tab, onTab, current, onOpen, onSettings, className }: {
  tab: string; onTab: (t: string) => void; current: string | null; onOpen: (key: string) => void; onSettings: () => void; className?: string;
}) {
  const inbox = useBotsStore((s) => s.inbox);
  const days = useBotsStore((s) => s.inboxDays);
  const loadInbox = useBotsStore((s) => s.loadInbox);
  const bot = useBotsStore((s) => s.bot);
  const runNow = useBotsStore((s) => s.runNow);
  const busy = useBotsStore((s) => s.busy);
  const g = useMemo(() => inboxGroups(inbox), [inbox]);
  const running = inbox.some((r) => r.bot_id === bot?.bot_id && live(r));
  return (
    <section aria-label="Bot runs" className={className}>
      <div className="flex items-center gap-2 px-4 pt-4 pb-2.5">
        <h1 className="m-0 text-xl font-extrabold tracking-tight">Bots</h1>
        <Button className="ml-auto pointer-coarse:h-11" disabled={busy || !bot?.enabled || running} onClick={() => void runNow()}>
          <PlayIcon />{running ? "Running…" : "Run now"}
        </Button>
      </div>
      <BotBar onSettings={onSettings} />
      <Tabs value={tab} onValueChange={onTab}>
        <TabsList className="mx-4 grid tabular-nums w-[calc(100%-2rem)] grid-cols-2 pointer-coarse:h-12">
          <TabsTrigger value="needs">Needs you · {g.decide.length}</TabsTrigger>
          <TabsTrigger value="all">All runs · {inbox.length}</TabsTrigger>
        </TabsList>
        <TabsContent value="needs" className="pb-6">
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
              className="mx-2 grid h-auto w-[calc(100%-1rem)] grid-cols-[18px_minmax(0,1fr)] items-start gap-2.5 rounded-lg px-3 py-2.5 text-left font-normal whitespace-normal">
              <span className="pt-1 pl-0.5"><Stop size="sm" tone="muted" /></span>
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
                    className={cn("grid h-auto w-full grid-cols-[18px_minmax(0,1fr)] items-start gap-2.5 rounded-lg border border-transparent px-3 py-2.5 text-left font-normal whitespace-normal pointer-coarse:py-3.5",
                      key === current && "border-[var(--brand-soft-line)] bg-[var(--brand-soft)] hover:bg-[var(--brand-soft)]")}>
                    <span className="pt-0.5"><Stop tone={r.outcome === "action_needed" ? "signal" : r.outcome ? "navy" : "muted"} /></span>
                    <span className="min-w-0">
                      <span className="flex items-baseline gap-2 text-[13.5px] font-bold"><span className="truncate">{v.label}</span>
                        <span className="ml-auto shrink-0 text-xs font-normal text-muted-foreground tabular-nums">{stamp(r.created_at)}</span></span>
                      <span className="block truncate text-xs text-muted-foreground">{[r.bot_name, KIND_LABEL[r.trigger_kind]].filter(Boolean).join(" · ")}</span>
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
      </Tabs>
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

function Decision({ item, onReport, onAsk, onBack, backLabel = "All runs", children }: {
  item: InboxItem; onReport: () => void; onAsk: () => void; onBack?: () => void; backLabel?: string; children?: ReactNode;
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
    <article className="mx-auto flex w-full max-w-[760px] flex-col px-7 pt-5 pb-8 max-md:px-4">
      {onBack && <Button variant="ghost" className="-ml-2 mb-2 self-start pointer-coarse:h-11" onClick={onBack}><ChevronLeftIcon />{backLabel}</Button>}
      <p className="m-0 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] text-muted-foreground tabular-nums">
        <Pill tone={v.tone}>{run.outcome === "action_needed" && <AlertIcon className="size-3.5" />}{v.label}</Pill>
        <span>{[run.bot_name, KIND_LABEL[run.trigger_kind], stamp(run.created_at), proposal?.field].filter(Boolean).join(" · ")}</span>
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
      <div className="mt-4 flex flex-wrap gap-2 max-sm:grid max-sm:grid-cols-2 max-sm:[&>*:first-child]:col-span-2">
        <Button className="pointer-coarse:h-11" onClick={onReport}><FileTextIcon />Open report</Button>
        <Button variant="outline" className="pointer-coarse:h-11" onClick={onAsk}><MessageIcon />Ask about this</Button>
        {run.outcome === "action_needed" && (run.dismissed_at
          ? <Button variant="ghost" className="pointer-coarse:h-11" disabled={busy} onClick={() => void dismiss(run.run_id, true)}>Bring back</Button>
          : <Button variant="ghost" className="pointer-coarse:h-11" disabled={busy} onClick={() => void dismiss(run.run_id)}><XIcon />{item.total > 1 ? `Dismiss all ${item.total}` : "Dismiss"}</Button>)}
      </div>
      {r ? <>
        {(checks.length > 0 || (r.checks?.length ?? 0) > 0) && (
          <section aria-label="Checks" className="mt-7">
            <h3 className="m-0 mb-1 text-sm font-bold">Checks at {stamp(run.finished_at ?? run.created_at)}</h3>
            <div className="grid grid-cols-2 gap-x-6 max-sm:grid-cols-1">
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
                <TableHead className="h-auto py-[7px] text-xs font-semibold text-muted-foreground max-sm:hidden">Note</TableHead>
              </TableRow></TableHeader>
              <TableBody>{r.movers!.map((m) => (
                <TableRow key={m.entity} className="border-b hover:bg-transparent">
                  <TableCell className="py-[7px] pl-0 whitespace-normal break-words">{m.entity}</TableCell>
                  <TableCell className="py-[7px] text-right whitespace-normal">{m.figure}</TableCell>
                  <TableCell className="py-[7px] whitespace-normal text-muted-foreground max-sm:hidden">{m.note}</TableCell>
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

interface Event { key: string; time: string; title: string; note: string; tone?: "navy" | "signal"; big?: boolean; dashed?: boolean }

/** The selected campaign through today's runs (every bot), or, with no campaign, the bot's day. Matched by name as the
 * answers write it. shortcut: name match, not Meta id; upgrade when the report carries entity ids. */
export function historyFor(item: InboxItem, runs: BotRun[]): { title: string; sub: string; events: Event[] } {
  const entity = item.proposal?.entity;
  if (!entity) {
    return {
      title: "Today on this bot", sub: item.run.bot_name ?? "",
      events: runs.filter((r) => r.bot_id === item.run.bot_id).map((r) => ({
        key: r.run_id, time: `${stamp(r.created_at)} · ${KIND_LABEL[r.trigger_kind] ?? r.trigger_kind}`, title: verdictOf(r).label, note: runLine(r),
        tone: r.outcome === "action_needed" ? "signal" : "navy", big: r.run_id === item.run.run_id,
      })),
    };
  }
  const events: Event[] = [];
  const p = item.proposal!;
  events.push({ key: "proposed", time: "Proposed", title: p.proposed != null ? `${inr(p.proposed)}${p.field ? ` ${p.field}` : ""}` : reviewOf(p),
    note: p.condition ? capital(p.condition) : p.current != null ? `Now ${inr(p.current)}` : "", tone: "signal", big: true, dashed: true });
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
  return { title: "This campaign today", sub: `What the bots saw on ${entity}`, events };
}

function CampaignHistory({ item, className }: { item: InboxItem; className?: string }) {
  const inbox = useBotsStore((s) => s.inbox);
  const h = useMemo(() => historyFor(item, inbox), [item, inbox]);
  return (
    <section aria-label={h.title} className={className}>
      <h3 className="m-0 text-sm font-bold">{h.title}</h3>
      {h.sub && <p className="m-0 mb-3.5 text-[12.5px] text-muted-foreground wrap-anywhere">{h.sub}</p>}
      <ol className="relative m-0 list-none p-0 pl-[30px] tabular-nums before:absolute before:top-2 before:bottom-2 before:left-[9px] before:w-1 before:rounded-full before:bg-[var(--signal)]">
        {h.events.map((e) => (
          <li key={e.key} className="relative pb-[18px] last:pb-0">
            <span className={cn("absolute top-[3px]", e.big ? "-left-[29px]" : "-left-[27px]")}><Stop size={e.big ? "lg" : "md"} tone={e.tone} dashed={e.dashed} /></span>
            <span className="block text-xs text-muted-foreground">{e.time}</span>
            <b className="block text-sm wrap-anywhere">{e.title}</b>
            {e.note && <small className="block text-[12.5px] text-muted-foreground">{e.note}</small>}
          </li>
        ))}
      </ol>
      {h.events.length === 1 && <p className="m-0 mt-3 text-xs text-muted-foreground">No other run mentioned it today.</p>}
    </section>
  );
}

/** "Open report": the bot's full answer, why it ran, and every step of that run. */
function ReportSheet({ item, open, onOpenChange }: { item: InboxItem | null; open: boolean; onOpenChange: (v: boolean) => void }) {
  const activity = useBotsStore((s) => s.activity);
  if (!item) return null;
  const r = item.run;
  const { body, reason } = splitAnswer(r.answer ?? "");
  const steps = activity.filter((a) => a.run_id === r.run_id);
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-2xl">
        <SheetHeader className="border-b px-6 py-4 pr-12 max-sm:px-4">
          <SheetTitle className="m-0 text-lg">{verdictOf(r).label} · {stamp(r.created_at)}</SheetTitle>
          <SheetDescription className="m-0">{[r.bot_name, KIND_LABEL[r.trigger_kind]].filter(Boolean).join(" · ")}</SheetDescription>
        </SheetHeader>
        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-6 pt-4 pb-8 max-sm:px-4">
          {reason && <p className="m-0 text-sm font-medium">{reason}</p>}
          <RunBody r={r} body={body} />
          <RunMeta r={r} />
          <section aria-label="Bot activity">
            <h3 className="m-0 mb-2 text-sm font-bold">Steps</h3>
            <ActivityTimeline activity={steps} />
          </section>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function AskSheet({ draft, open, onOpenChange }: { draft: string; open: boolean; onOpenChange: (v: boolean) => void }) {
  const chat = useBotsStore((s) => s.chat);
  const bot = useBotsStore((s) => s.bot);
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="gap-0 data-[side=right]:w-full data-[side=right]:sm:max-w-xl">
        <SheetHeader className="border-b px-6 py-4 pr-12 max-sm:px-4">
          <SheetTitle className="m-0 text-lg">Ask {bot?.name ?? "the bot"}</SheetTitle>
          <SheetDescription className="m-0">It answers with its own instructions and tools.</SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 pt-4 pb-8 max-sm:px-4"><Chat chat={chat} draft={draft} /></div>
      </SheetContent>
    </Sheet>
  );
}

const urlRun = () => new URL(location.href).searchParams.get("run");
function writeUrlRun(key: string | null, push: boolean) {
  const url = new URL(location.href);
  if (key) url.searchParams.set("run", key); else url.searchParams.delete("run");
  if (url.href === location.href) return;
  if (push) history.pushState({ botsRun: true }, "", url); else history.replaceState(history.state, "", url);
}

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
  const [sheet, setSheet] = useState<"report" | "ask" | "settings" | null>(null);

  useEffect(() => {
    void loadList();
    void loadInbox();
    const id = window.setInterval(() => { if (!document.hidden) { void refresh(); void loadList(); void loadInbox(); } }, POLL_MS);
    const pop = () => setSel(urlRun());
    window.addEventListener("popstate", pop);
    return () => { window.clearInterval(id); window.removeEventListener("popstate", pop); };
  }, [loadList, loadInbox, refresh]);

  const items = useMemo(() => itemsOf(inbox), [inbox]);
  const decide = useMemo(() => inboxGroups(inbox).decide, [inbox]);
  // Phones open the list first; wider screens show the first decision (else the newest run) straight away.
  const item = items.find((i) => i.key === sel) ?? items.find((i) => sel && i.run.run_id === sel.split(":")[0])
    ?? (narrow ? undefined : (tab === "needs" ? decide[0] : undefined) ?? items[0]);

  // Settings, Ask and the report's steps act on the bot whose run is open.
  const owner = item?.run.bot_id;
  useEffect(() => { if (owner && owner !== useBotsStore.getState().selectedId) void select(owner); }, [owner, select]);

  const open = (key: string) => { setSel(key); writeUrlRun(key, narrow); };
  const back = () => { if (history.state?.botsRun) history.back(); else { setSel(null); writeUrlRun(null, false); } };
  const badge = decide.length;
  const draft = item ? `About ${item.proposal?.entity ?? runLine(item.run)} (${KIND_LABEL[item.run.trigger_kind] ?? item.run.trigger_kind}, ${stamp(item.run.created_at)}): ` : "";

  return (
    <AppFrame workspace="bots" rail sidebarLabel="Navigation" toggleLabel="Open navigation"
      header={<>
        <span className="text-[17px] font-extrabold">Bots</span>
        {badge > 0 && <span className="rounded-full bg-[var(--signal)] px-1.5 tabular-nums text-[11.5px] leading-[18px] font-bold text-[var(--on-accent)]" aria-label={`${badge} need your decision`}>{badge}</span>}
        <span className="flex-1" />
        {bot && <Button variant="ghost" size="icon" className="pointer-coarse:size-11" aria-label="Bot settings" onClick={() => setSheet("settings")}><SlidersIcon /></Button>}
      </>}>
      {error && <p role="alert" className="m-0 border-b bg-[var(--danger-soft)] px-4 py-2 text-sm text-destructive">{error}</p>}
      <div className="grid min-h-0 flex-1 md:grid-cols-[minmax(280px,320px)_minmax(0,1fr)] min-[80rem]:grid-cols-[300px_minmax(0,1fr)_280px]">
        <InboxList tab={tab} onTab={setTab} current={item?.key ?? null} onOpen={open} onSettings={() => setSheet("settings")}
          className={cn("min-h-0 overflow-y-auto bg-card md:border-r", narrow && item && "hidden")} />
        <main className={cn("min-h-0 min-w-0 overflow-y-auto", narrow && !item && "hidden")}>
          {item
            ? <Decision item={item} onReport={() => setSheet("report")} onAsk={() => setSheet("ask")} onBack={narrow ? back : undefined} backLabel={tab === "needs" ? "Needs you" : "All runs"}>
                <CampaignHistory item={item} className="mt-8 border-t pt-6 min-[80rem]:hidden" />
              </Decision>
            : <p className="m-0 p-8 text-sm text-muted-foreground">{selectedId || inbox.length ? "No runs to show yet. Press Run now, or the bot reports on its next trigger." : "Create a bot from a template with the + button."}</p>}
        </main>
        {item && <aside aria-label="Campaign history" className="hidden min-h-0 overflow-y-auto border-l bg-card p-[18px] min-[80rem]:block"><CampaignHistory item={item} /></aside>}
      </div>
      <ReportSheet item={item ?? null} open={sheet === "report"} onOpenChange={(v) => setSheet(v ? "report" : null)} />
      <AskSheet draft={sheet === "ask" ? draft : ""} open={sheet === "ask"} onOpenChange={(v) => setSheet(v ? "ask" : null)} />
      {bot && <BotSettings bot={bot} open={sheet === "settings"} onOpenChange={(v) => setSheet(v ? "settings" : null)} />}
    </AppFrame>
  );
}
