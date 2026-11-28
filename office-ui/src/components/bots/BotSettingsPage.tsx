/* D-257: a bot's settings as one page with a section list. Each section says in one line what it is for:
 * Goal (plain words) · Skills (how to do the work, with the tools they bring) · Knowledge (uploaded documents on
 * Hindsight + what it learned) · Schedules (when it runs, what to do, in what layout, to whom) · Chat · Advanced. */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { cn } from "cn";
import { ChevronLeftIcon, PlayIcon, PlusIcon } from "../icons";
import { SafeContent } from "../SafeContent";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { botsApi, type Bot, type BotRun, type BotSkill, type BotTrigger, type KnowledgeDoc, type LibrarySkill, type MemoryBank, type MemoryItem, type TriggerKind } from "../../api/bots";
import { useBotsStore } from "../../stores/bots";

export const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–";

/** Tool names a person has not seen before read as words: "meta_adsets_list" → "Meta adsets list". */
export const humanize = (name: string) => name.charAt(0).toUpperCase() + name.slice(1).replaceAll("_", " ");

/** Limits the backend enforces today; the rest (tool calls, pending proposals) arrive with proposals in P3. */
const ENFORCED_LIMITS = ["max_scheduled_runs_per_day"];

/* ------------------------------------------------------------------ schedules in words */

const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const digits = (x: string) => x.length > 0 && [...x].every((c) => c >= "0" && c <= "9");

export type Freq = "daily" | "weekly" | "monthly" | "custom";
export interface CronParts { freq: Freq; days: number[]; dom: number; time: string }

/** The simple shapes the schedule form builds; anything else is "custom" and kept as written. */
export function parseCron(cron: string): CronParts {
  const [min = "", hour = "", dom = "", month = "", dow = ""] = cron.trim().split(" ").filter(Boolean);
  const custom: CronParts = { freq: "custom", days: [1], dom: 1, time: "09:00" };
  if (cron.trim().split(" ").filter(Boolean).length !== 5 || !digits(min) || !digits(hour) || month !== "*") return custom;
  const time = `${hour.padStart(2, "0")}:${min.padStart(2, "0")}`;
  if (dom === "*" && dow === "*") return { freq: "daily", days: [1], dom: 1, time };
  if (dom === "*" && dow.split(",").every(digits)) return { freq: "weekly", days: dow.split(",").map(Number).map((d) => d % 7), dom: 1, time };
  if (digits(dom) && dow === "*") return { freq: "monthly", days: [1], dom: Number(dom), time };
  return custom;
}

export function buildCron({ freq, days, dom, time }: CronParts, custom: string): string {
  if (freq === "custom") return custom.trim();
  const [h = "9", m = "0"] = time.split(":");
  const base = `${Number(m)} ${Number(h)}`;
  if (freq === "weekly") return `${base} * * ${[...new Set(days)].sort().join(",") || "1"}`;
  if (freq === "monthly") return `${base} ${dom} * *`;
  return `${base} * * *`;
}

/** "0 9 * * *" reads as "Every day at 09:00"; weekly and monthly too; any other cron is shown as written. */
export function cronText(cron: string, tz: string): string {
  const p = parseCron(cron);
  if (p.freq === "daily") return `Every day at ${p.time} (${tz})`;
  if (p.freq === "weekly") return `Every ${p.days.map((d) => DAYS[d]).join(", ")} at ${p.time} (${tz})`;
  if (p.freq === "monthly") return `On day ${p.dom} of every month at ${p.time} (${tz})`;
  return `Schedule ${cron} (${tz})`;
}

export function triggerText(t: BotTrigger): string {
  if (t.kind === "cron") return cronText(String(t.config.cron), String(t.config.timezone));
  if (t.kind === "watch") return `When ${humanize(String(t.config.tool)).toLowerCase()} changes: ${(t.config.fields ?? []).map((f: string) => f.replaceAll("_", " ")).join(", ")} · checked every ${t.config.interval_min} min`;
  if (t.kind === "webhook") return "When another system calls its webhook";
  return `On monitoring alerts ${t.config.detector_prefix}* · ${(t.config.severities ?? []).join(", ")}`;
}

export const hookUrl = (triggerId: string) => `${window.location.origin}/hooks/bots/${triggerId}`;

function triggerDetail(t: BotTrigger): string {
  if (!t.enabled) return "Paused";
  if (t.kind === "cron") return `Next: ${when(t.next_run_at)}`;
  if (t.kind === "signal") return "Runs when a matching finding opens";
  if (t.kind === "watch") return t.state?.error ? `Last check failed: ${t.state.error}` : `Last check: ${when(t.state?.checked_at)}`;
  return "Runs when called with its secret";
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

/* ------------------------------------------------------------------ small parts */

function Block({ title, hint, children }: { title: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        <h4 className="m-0 text-sm font-semibold">{title}</h4>
        {hint && <p className="m-0 mt-0.5 text-sm text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

const Row = ({ children, className }: { children: ReactNode; className?: string }) =>
  <li className={cn("flex flex-wrap items-start gap-3 rounded-md border px-3 py-2.5 text-sm", className)}>{children}</li>;

/* ------------------------------------------------------------------ Goal */

function Goal({ bot }: { bot: Bot }) {
  const save = useBotsStore((s) => s.save);
  const busy = useBotsStore((s) => s.busy);
  const [text, setText] = useState(bot.instructions);
  const [name, setName] = useState(bot.name);
  const [role, setRole] = useState(bot.role);
  useEffect(() => { setText(bot.instructions); setName(bot.name); setRole(bot.role); }, [bot.bot_id, bot.version]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = text !== bot.instructions || name !== bot.name || role !== bot.role;
  return (
    <div className="flex flex-col gap-4">
      <label className="flex flex-col gap-1.5 text-sm font-medium">Goal
        <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={5} className="text-base leading-relaxed font-normal"
          placeholder="Watch our Meta ads and tell me when money is being wasted." />
        <span className="text-xs font-normal text-muted-foreground">
          One to three plain sentences. Its skills handle how; you don't need to name tools or steps. Version {bot.instructions_version}: every save keeps the old goal, and each run records the version it used.
        </span>
      </label>
      <div className="grid grid-cols-2 gap-3 max-md:grid-cols-1">
        <label className="flex flex-col gap-1.5 text-sm font-medium">Name<Input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="flex flex-col gap-1.5 text-sm font-medium">Role <span className="sr-only">(short description)</span><Input value={role} onChange={(e) => setRole(e.target.value)} /></label>
      </div>
      <div className="flex gap-2">
        <Button disabled={!dirty || busy || !name.trim() || !text.trim()} onClick={() => void save({ instructions: text.trim(), name: name.trim(), role })}>Save</Button>
        {dirty && <Button variant="ghost" onClick={() => { setText(bot.instructions); setName(bot.name); setRole(bot.role); }}>Discard</Button>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ Skills */

function SkillEditor({ skill, onDone }: { skill?: BotSkill; onDone: () => void }) {
  const saveSkill = useBotsStore((s) => s.saveSkill);
  const busy = useBotsStore((s) => s.busy);
  const [name, setName] = useState(skill?.name ?? "");
  const [body, setBody] = useState(skill?.body ?? "");
  return (
    <div className="flex w-full flex-col gap-2">
      <label className="flex flex-col gap-1 text-sm">Name<Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Pacing check" /></label>
      <label className="flex flex-col gap-1 text-sm">How to do it<Textarea value={body} onChange={(e) => setBody(e.target.value)} rows={8} className="text-sm" /></label>
      <div className="flex gap-2">
        <Button size="sm" disabled={busy || !name.trim() || !body.trim()}
          onClick={async () => { if (await saveSkill({ skill_id: skill?.skill_id, name, body, enabled: skill?.enabled ?? true })) onDone(); }}>Save</Button>
        <Button size="sm" variant="ghost" onClick={onDone}>Cancel</Button>
      </div>
    </div>
  );
}

function SkillRow({ name, summary, tools, skill, onToggle }: {
  name: string; summary?: string; tools: string[]; skill?: BotSkill; onToggle: (on: boolean) => void;
}) {
  const deleteSkill = useBotsStore((s) => s.deleteSkill);
  const busy = useBotsStore((s) => s.busy);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const on = !!skill?.enabled;
  return (
    <Row>
      <Switch checked={on} disabled={busy} onCheckedChange={onToggle} aria-label={`${name} on`} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <p className="m-0 font-medium">{name}</p>
        {summary && <p className="m-0 text-muted-foreground">{summary}</p>}
        {tools.length > 0 && <p className="m-0 mt-1 text-xs text-muted-foreground">Reads: {tools.map(humanize).join(", ")}</p>}
        {skill && !editing && (
          <Button variant="link" size="sm" className="mt-1 h-auto p-0 text-xs text-brand" aria-expanded={open} onClick={() => setOpen(!open)}>
            {open ? "Hide how it works" : "Show how it works"}
          </Button>
        )}
        {open && !editing && skill && <p className="m-0 mt-2 max-w-[75ch] whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">{skill.body}</p>}
        {editing && skill && <div className="mt-2"><SkillEditor skill={skill} onDone={() => setEditing(false)} /></div>}
      </div>
      {skill && !editing && <Button variant="ghost" size="sm" onClick={() => { setEditing(true); setOpen(false); }}>Edit</Button>}
      {skill && !skill.library && !editing && <Button variant="ghost" size="sm" onClick={() => void deleteSkill(skill.skill_id)}>Remove</Button>}
    </Row>
  );
}

function Skills({ bot }: { bot: Bot }) {
  const saveSkill = useBotsStore((s) => s.saveSkill);
  const addLibrarySkill = useBotsStore((s) => s.addLibrarySkill);
  const [library, setLibrary] = useState<LibrarySkill[]>([]);
  const [writing, setWriting] = useState(false);
  useEffect(() => { botsApi.skillLibrary().then((r) => setLibrary(r.skills)).catch(() => setLibrary([])); }, []);
  const skills = bot.skills ?? [];
  const toggle = (k: BotSkill, on: boolean) => void saveSkill({ skill_id: k.skill_id, name: k.name, body: k.body, enabled: on });
  const own = skills.filter((k) => !k.library || !library.some((l) => l.key === k.library));
  return (
    <div className="flex flex-col gap-8">
      <Block title="Ready-made skills" hint="Turn on what this bot should be good at. Each brings the live data it needs; you can edit a skill's steps for this bot.">
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {library.map((l) => {
            const mine = skills.find((k) => k.library === l.key);
            return <SkillRow key={l.key} name={l.name} summary={l.summary} tools={l.tools} skill={mine}
              onToggle={(on) => (mine ? toggle(mine, on) : on && void addLibrarySkill(l.key))} />;
          })}
          {!library.length && <li className="text-sm text-muted-foreground">Loading skills…</li>}
        </ul>
      </Block>
      <Block title="Your own skills" hint="A how-to in your words, for something the ready-made skills don't cover.">
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {own.map((k) => <SkillRow key={k.skill_id} name={k.name} tools={k.tools ?? []} skill={k} onToggle={(on) => toggle(k, on)} />)}
        </ul>
        {writing ? <div className="rounded-md border p-3"><SkillEditor onDone={() => setWriting(false)} /></div>
          : <Button size="sm" variant="outline" className="self-start" onClick={() => setWriting(true)}><PlusIcon />Write your own skill</Button>}
      </Block>
    </div>
  );
}

/* ------------------------------------------------------------------ Knowledge */

const STATUS: Record<KnowledgeDoc["status"], string> = { ready: "Ready", processing: "Reading…", failed: "Failed" };

function useKnowledge(botId: string) {
  const [docs, setDocs] = useState<KnowledgeDoc[] | null>(null);
  const [info, setInfo] = useState<{ max_mb: number; types: string[] }>({ max_mb: 20, types: [] });
  const [error, setError] = useState("");
  const load = async () => {
    try { const r = await botsApi.knowledge(botId); setDocs(r.documents); setInfo({ max_mb: r.max_mb, types: r.types }); setError(""); }
    catch (e) { setDocs((d) => d ?? []); setError(errText(e)); }
  };
  useEffect(() => { setDocs(null); void load(); }, [botId]); // eslint-disable-line react-hooks/exhaustive-deps
  return { docs, info, error, setError, load };
}

function Knowledge({ bot }: { bot: Bot }) {
  const { docs, info, error, setError, load } = useKnowledge(bot.bot_id);
  const [shared, setShared] = useState(false);
  const [uploading, setUploading] = useState<string[]>([]);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const reading = (docs ?? []).some((d) => d.status === "processing");
  useEffect(() => {
    if (!reading) return;
    const id = window.setInterval(() => { if (!document.hidden) void load(); }, 5000);
    return () => window.clearInterval(id);
  }, [reading]); // eslint-disable-line react-hooks/exhaustive-deps

  const upload = async (files: File[]) => {
    const failed: string[] = [];
    for (const f of files) {
      setUploading((u) => [...u, f.name]);
      try { await botsApi.upload(bot.bot_id, f, shared); }
      catch (e) { failed.push(`${f.name}: ${errText(e)}`); }
      finally { setUploading((u) => u.filter((n) => n !== f.name)); }
    }
    setError(failed.join(" · "));
    await load();
  };
  const remove = async (d: KnowledgeDoc) => {
    if (!window.confirm(`Remove “${d.name}”? The bot stops using it, and it is deleted from Hindsight.`)) return;
    try { await botsApi.removeDocument(bot.bot_id, d.document_id, d.scope === "shared"); await load(); } catch (e) { setError(errText(e)); }
  };

  return (
    <div className="flex flex-col gap-8">
      <Block title="Documents you added" hint={<>
        Reports, price lists, brand notes, a report layout to copy: the bot finds the right passage when it runs. Documents are stored in
        <strong className="font-medium text-foreground"> Hindsight Cloud</strong> (vectorize.io), which reads them, splits them into passages and indexes them.
        Removing a document deletes it there.
      </>}>
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); void upload([...e.dataTransfer.files]); }}
          className={cn("flex flex-col items-start gap-3 rounded-lg border border-dashed px-4 py-4 transition-colors", drag && "border-brand bg-[var(--brand-soft)]")}>
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => input.current?.click()} disabled={uploading.length > 0}><PlusIcon />Upload documents</Button>
            <span className="text-sm text-muted-foreground">or drop files here</span>
          </div>
          <input ref={input} type="file" multiple hidden accept={info.types.join(",")} aria-label="Choose documents"
            onChange={(e) => { const f = [...(e.target.files ?? [])]; e.target.value = ""; if (f.length) void upload(f); }} />
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={shared} onCheckedChange={setShared} aria-label="Share with every bot of this brand" />
            Share with every bot of this brand
            <span className="text-muted-foreground">{shared ? "(all bots can read it)" : "(only this bot)"}</span>
          </label>
          <p className="m-0 text-xs text-muted-foreground">PDF, Word, PowerPoint, Excel, CSV, text, Markdown, JSON, HTML or images (text in images is read too), up to {info.max_mb} MB each. Files are virus-scanned first; we keep no copy.</p>
        </div>
        {error && <p role="alert" className="m-0 text-sm text-destructive">{error}</p>}
        <ul className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Documents">
          {uploading.map((n) => <Row key={`up-${n}`}><span className="min-w-0 flex-1 font-medium">{n}</span><span className="text-muted-foreground">Uploading…</span></Row>)}
          {(docs ?? []).map((d) => (
            <Row key={`${d.scope}-${d.document_id}`} className="items-center">
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium" title={d.name}>{d.name}</span>
                <span className="block text-xs text-muted-foreground">{d.scope === "shared" ? "All bots of this brand" : "This bot"} · {when(d.updated_at)}</span>
                {d.status === "failed" && d.error && <span className="block text-xs text-destructive">{d.error}</span>}
              </span>
              <span className={cn("text-xs font-medium", d.status === "failed" ? "text-destructive" : d.status === "processing" ? "text-muted-foreground" : "text-[var(--success)]")}>{STATUS[d.status]}</span>
              <Button variant="ghost" size="sm" onClick={() => void remove(d)}>Remove</Button>
            </Row>
          ))}
          {docs && !docs.length && !uploading.length && <li className="text-sm text-muted-foreground">No documents yet.</li>}
          {!docs && <li className="text-sm text-muted-foreground">Loading…</li>}
        </ul>
      </Block>
      <Block title="Learned from runs" hint="What it noted from earlier runs and chats, and notes you add. Context only: it re-checks live data before acting on any of it.">
        <Memory bot={bot} />
      </Block>
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
    try { setItems((await botsApi.memory(bot.bot_id, bank, q.trim())).items); } catch (e) { setItems([]); setError(errText(e)); }
  };
  useEffect(() => { void load(); }, [bot.bot_id, bank]); // eslint-disable-line react-hooks/exhaustive-deps
  const act = async (fn: () => Promise<unknown>) => {
    setPending(true);
    try { await fn(); await load(); } catch (e) { setError(errText(e)); } finally { setPending(false); }
  };
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-2">
        <NativeSelect aria-label="Memory bank" value={bank} onChange={(e) => setBank(e.target.value as MemoryBank)}>
          <NativeSelectOption value="bot">This bot</NativeSelectOption>
          <NativeSelectOption value="shared">All bots of this brand</NativeSelectOption>
        </NativeSelect>
        <Input aria-label="Search memory" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search"
          onKeyDown={(e) => { if (e.key === "Enter") void load(); }} className="w-56" />
      </div>
      {error && <p role="alert" className="m-0 text-sm text-destructive">{error}</p>}
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0" aria-label="Memories">
        {(items ?? []).map((m) => (
          <Row key={m.id}>
            <span className="min-w-0 flex-1">{m.text}
              <span className="block text-xs text-muted-foreground">{when(m.mentioned_at || m.date)}{m.document_id?.startsWith("run_") ? " · from a run" : m.document_id ? " · note" : ""}</span>
            </span>
            {m.document_id && <Button variant="ghost" size="sm" disabled={pending} onClick={() => void act(() => botsApi.forget(bot.bot_id, bank, m.document_id!))}>Forget</Button>}
          </Row>
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

/* ------------------------------------------------------------------ Schedules */

function ScheduleEditor({ bot, trigger, onDone }: { bot: Bot; trigger?: BotTrigger; onDone: () => void }) {
  const addTrigger = useBotsStore((s) => s.addTrigger);
  const editTrigger = useBotsStore((s) => s.editTrigger);
  const runTrigger = useBotsStore((s) => s.runTrigger);
  const busy = useBotsStore((s) => s.busy);
  const c = trigger?.config ?? {};
  const [parts, setParts] = useState<CronParts>(() => (c.cron ? parseCron(c.cron) : { freq: "weekly", days: [1], dom: 1, time: "09:00" }));
  const [custom, setCustom] = useState(c.cron ?? "0 9 * * 1");
  const [tz, setTz] = useState(c.timezone ?? "Asia/Kolkata");
  const [title, setTitle] = useState(c.title ?? "");
  const [task, setTask] = useState(c.task ?? "");
  const [email, setEmail] = useState((c.email ?? []).join(", "));
  const [format, setFormat] = useState(c.format_doc ? `${c.format_doc.bank}|${c.format_doc.document_id}` : "");
  const { docs } = useKnowledge(bot.bot_id);
  const ready = (docs ?? []).filter((d) => d.status === "ready");
  const set = (p: Partial<CronParts>) => setParts({ ...parts, ...p });
  const cron = buildCron(parts, custom);
  const save = async () => {
    const doc = ready.find((d) => `${d.bank}|${d.document_id}` === format);
    const config: BotTrigger["config"] = {
      cron, timezone: tz.trim() || "Asia/Kolkata", title: title.trim(), task: task.trim(), email: list(email),
      ...(doc ? { format_doc: { bank: doc.bank, document_id: doc.document_id, name: doc.name } }
        : format && c.format_doc ? { format_doc: c.format_doc } : {}),
    };
    if (await (trigger ? editTrigger(trigger.trigger_id, config) : addTrigger("cron", config))) onDone();
  };
  const field = "flex flex-col gap-1.5 text-sm font-medium";
  return (
    <div className="flex w-full flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className={field}>Repeats
          <NativeSelect value={parts.freq} onChange={(e) => set({ freq: e.target.value as Freq })} aria-label="Repeats">
            <NativeSelectOption value="daily">Every day</NativeSelectOption>
            <NativeSelectOption value="weekly">Every week</NativeSelectOption>
            <NativeSelectOption value="monthly">Every month</NativeSelectOption>
            <NativeSelectOption value="custom">Custom (cron)</NativeSelectOption>
          </NativeSelect>
        </label>
        {parts.freq === "monthly" && (
          <label className={field}>On day<Input type="number" min={1} max={28} value={parts.dom} onChange={(e) => set({ dom: Math.min(28, Math.max(1, Number(e.target.value) || 1)) })} className="w-20" /></label>
        )}
        {parts.freq === "custom"
          ? <label className={field}>Cron (min hour day month weekday)<Input value={custom} onChange={(e) => setCustom(e.target.value)} className="w-44 font-mono" /></label>
          : <label className={field}>At<Input type="time" value={parts.time} onChange={(e) => set({ time: e.target.value || "09:00" })} className="w-32" /></label>}
        <label className={field}>Timezone<Input value={tz} onChange={(e) => setTz(e.target.value)} className="w-40" /></label>
      </div>
      {parts.freq === "weekly" && (
        <fieldset className="m-0 flex flex-wrap gap-x-4 gap-y-2 border-0 p-0">
          <legend className="mb-1.5 p-0 text-sm font-medium">On</legend>
          {[1, 2, 3, 4, 5, 6, 0].map((d) => (
            <label key={d} className="flex items-center gap-1.5 text-sm pointer-coarse:min-h-11">
              <Checkbox checked={parts.days.includes(d)} onCheckedChange={(v) => set({ days: v === true ? [...parts.days, d] : parts.days.filter((x) => x !== d) })} />
              {DAYS[d].slice(0, 3)}
            </label>
          ))}
        </fieldset>
      )}
      <label className={field}>Name <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Weekly Meta report" /></label>
      <label className={field}>What to do
        <Textarea value={task} onChange={(e) => setTask(e.target.value)} rows={3} className="font-normal"
          placeholder="Email me last week's Meta spend, orders and ROAS, and the three campaigns to change." />
        <span className="text-xs font-normal text-muted-foreground">Leave empty to do its goal. It runs with everything this bot knows: goal, skills, documents and memory.</span>
      </label>
      <label className={field}>Lay the email out like
        <NativeSelect value={format} onChange={(e) => setFormat(e.target.value)} aria-label="Format document">
          <NativeSelectOption value="">The usual layout</NativeSelectOption>
          {c.format_doc && !ready.some((d) => `${d.bank}|${d.document_id}` === format) && format &&
            <NativeSelectOption value={format}>{c.format_doc.name}</NativeSelectOption>}
          {ready.map((d) => <NativeSelectOption key={`${d.bank}|${d.document_id}`} value={`${d.bank}|${d.document_id}`}>{d.name}</NativeSelectOption>)}
        </NativeSelect>
        <span className="text-xs font-normal text-muted-foreground">Upload a sample report under Knowledge to pick it here.</span>
      </label>
      <label className={field}>Email to
        <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder={(bot.notify?.email ?? []).join(", ") || "Nobody"} />
        <span className="text-xs font-normal text-muted-foreground">Names, groups or addresses from the email contacts. Empty: the bot's default recipients.</span>
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <Button disabled={busy || !cron || (parts.freq === "weekly" && !parts.days.length)} onClick={() => void save()}>{trigger ? "Save schedule" : "Add schedule"}</Button>
        {trigger && (
          <Button variant="outline" disabled={busy || !bot.enabled} onClick={() => void runTrigger(trigger.trigger_id)}
            title={bot.enabled ? "Run this schedule once now (saved settings)" : "Turn the bot on to run"}>
            <PlayIcon />Run now
          </Button>
        )}
        <Button variant="ghost" onClick={onDone}>Cancel</Button>
        <span className="text-xs text-muted-foreground">{cronText(cron, tz)}</span>
      </div>
    </div>
  );
}

function Schedules({ bot }: { bot: Bot }) {
  const setTrigger = useBotsStore((s) => s.setTrigger);
  const deleteTrigger = useBotsStore((s) => s.deleteTrigger);
  const runTrigger = useBotsStore((s) => s.runTrigger);
  const busy = useBotsStore((s) => s.busy);
  const [editing, setEditing] = useState<string | null>(null);
  const schedules = (bot.triggers ?? []).filter((t) => t.kind === "cron");
  const defaults = (bot.notify?.email ?? []).join(", ") || "nobody yet";
  return (
    <div className="flex flex-col gap-8">
      <Block title="Your schedules" hint={<>Each runs with everything this bot knows. You can also ask in Chat: “Every Monday at 9, email me last week's Meta report laid out like weekly-format.pdf.”</>}>
        <ul className="m-0 flex list-none flex-col gap-2 p-0">
          {schedules.map((t) => editing === t.trigger_id
            ? <Row key={t.trigger_id}><ScheduleEditor bot={bot} trigger={t} onDone={() => setEditing(null)} /></Row>
            : (
              <Row key={t.trigger_id}>
                <Switch checked={t.enabled} onCheckedChange={(v) => void setTrigger(t.trigger_id, v)} aria-label={`${t.config.title || "Schedule"} on`} className="mt-0.5" />
                <div className="min-w-0 flex-1">
                  <p className="m-0 font-medium">{t.config.title || (t.config.task ? "Scheduled task" : "Check (does its goal)")}</p>
                  <p className="m-0">{triggerText(t)}</p>
                  {t.config.task && <p className="m-0 max-w-[75ch] text-muted-foreground">{t.config.task}</p>}
                  <p className="m-0 mt-1 text-xs text-muted-foreground">
                    {t.config.format_doc ? `Laid out like ${t.config.format_doc.name}` : "Usual layout"} · Emails {(t.config.email ?? []).join(", ") || defaults} · {triggerDetail(t)}
                  </p>
                </div>
                <Button variant="ghost" size="sm" disabled={busy || !bot.enabled} onClick={() => void runTrigger(t.trigger_id)}
                  title={bot.enabled ? "Run this schedule once now" : "Turn the bot on to run"}>
                  <PlayIcon />Run now
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setEditing(t.trigger_id)}>Edit</Button>
                <Button variant="ghost" size="sm" onClick={() => window.confirm("Delete this schedule?") && void deleteTrigger(t.trigger_id)}>Delete</Button>
              </Row>
            ))}
          {!schedules.length && editing !== "new" && <li className="text-sm text-muted-foreground">No schedules: it runs on the triggers below, Run now and chat.</li>}
          {editing === "new" && <Row><ScheduleEditor bot={bot} onDone={() => setEditing(null)} /></Row>}
        </ul>
        {editing !== "new" && <Button variant="outline" className="self-start" onClick={() => setEditing("new")}><PlusIcon />Add schedule</Button>}
      </Block>
      <Block title="Other triggers" hint="What else starts a run: a monitoring finding, a change in a live value, or another system calling in.">
        <OtherTriggers bot={bot} />
      </Block>
      <Block title="Default email" hint="Who hears when a schedule names nobody, when something needs action, and when it could not check twice in a row. Also listed in Notification Center.">
        <DefaultEmail bot={bot} />
      </Block>
    </div>
  );
}

function DefaultEmail({ bot }: { bot: Bot }) {
  const save = useBotsStore((s) => s.save);
  const busy = useBotsStore((s) => s.busy);
  const saved = (bot.notify?.email ?? []).join(", ");
  const [email, setEmail] = useState(saved);
  useEffect(() => setEmail(saved), [bot.bot_id, bot.version]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="flex flex-wrap items-end gap-2">
      <label className="flex min-w-0 flex-1 flex-col gap-1.5 text-sm font-medium">Recipients
        <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Nobody" />
      </label>
      <Button disabled={busy || email === saved} onClick={() => void save({ notify: { email: list(email) } })}>Save</Button>
    </div>
  );
}

function OtherTriggers({ bot }: { bot: Bot }) {
  const addTrigger = useBotsStore((s) => s.addTrigger);
  const setTrigger = useBotsStore((s) => s.setTrigger);
  const deleteTrigger = useBotsStore((s) => s.deleteTrigger);
  const busy = useBotsStore((s) => s.busy);
  const newSecret = useBotsStore((s) => s.newSecret);
  const [kind, setKind] = useState<Exclude<TriggerKind, "cron">>("signal");
  const [tool, setTool] = useState("");
  const [fields, setFields] = useState("effective_status, daily_budget");
  const [args, setArgs] = useState("{}");
  const [interval, setInterval_] = useState(15);
  const [argsError, setArgsError] = useState("");
  const [prefix, setPrefix] = useState("live.meta.");
  const [critical, setCritical] = useState(true);
  const [warning, setWarning] = useState(true);
  const tools = bot.run_tools ?? bot.tools;
  const watchTool = tool || tools.find((t) => t !== "bot_eda") || "";
  const others = (bot.triggers ?? []).filter((t) => t.kind !== "cron");
  const add = () => {
    if (kind === "signal") return void addTrigger(kind, { detector_prefix: prefix, severities: [critical && "critical", warning && "warning"].filter(Boolean) as string[] });
    if (kind === "webhook") return void addTrigger(kind, {});
    let parsed: Record<string, unknown>;
    try { parsed = JSON.parse(args || "{}"); setArgsError(""); } catch { setArgsError("Arguments must be JSON, e.g. {\"account_id\": \"act_…\"}"); return; }
    void addTrigger(kind, { tool: watchTool, args: parsed, fields: list(fields), interval_min: interval });
  };
  return (
    <div className="flex flex-col gap-3">
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {others.map((t) => (
          <Row key={t.trigger_id} className="items-center">
            <Switch checked={t.enabled} onCheckedChange={(v) => void setTrigger(t.trigger_id, v)} aria-label={`Enable ${triggerText(t)}`} />
            <span className="min-w-0 flex-1">{triggerText(t)}
              <span className="block text-xs text-muted-foreground">{triggerDetail(t)} · Last fired: {when(t.last_fired_at)}</span>
              {t.kind === "webhook" && <span className="block font-mono text-xs break-all">POST {hookUrl(t.trigger_id)}</span>}
            </span>
            <Button variant="ghost" size="sm" onClick={() => void deleteTrigger(t.trigger_id)}>Remove</Button>
          </Row>
        ))}
        {!others.length && <li className="text-sm text-muted-foreground">None.</li>}
      </ul>
      {newSecret && (
        <div role="status" className="flex flex-col gap-1 rounded-md border border-[var(--warning)] px-3 py-2 text-sm">
          <span className="font-medium">Webhook secret: copy it now, it is not shown again.</span>
          <span className="font-mono text-xs break-all">POST {hookUrl(newSecret.triggerId)}</span>
          <span className="font-mono text-xs break-all">X-Bot-Secret: {newSecret.secret}</span>
        </div>
      )}
      <fieldset className="flex flex-wrap items-end gap-2 rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">Add a trigger</legend>
        <NativeSelect aria-label="Trigger kind" value={kind} onChange={(e) => setKind(e.target.value as Exclude<TriggerKind, "cron">)}>
          <NativeSelectOption value="signal">Monitoring finding</NativeSelectOption>
          <NativeSelectOption value="watch">Watch a live value</NativeSelectOption>
          <NativeSelectOption value="webhook">Webhook</NativeSelectOption>
        </NativeSelect>
        {kind === "watch" ? <>
          <label className="flex flex-col gap-1 text-xs">Tool
            <NativeSelect aria-label="Watch tool" value={watchTool} onChange={(e) => setTool(e.target.value)}>
              {tools.filter((t) => t !== "bot_eda").map((t) => <NativeSelectOption key={t} value={t}>{t}</NativeSelectOption>)}
            </NativeSelect>
          </label>
          <label className="flex flex-col gap-1 text-xs">Fields that start a run<Input value={fields} onChange={(e) => setFields(e.target.value)} className="w-56 font-mono" /></label>
          <label className="flex flex-col gap-1 text-xs">Arguments (JSON)<Input value={args} onChange={(e) => setArgs(e.target.value)} className="w-56 font-mono" /></label>
          <label className="flex flex-col gap-1 text-xs">Every (min)<Input type="number" min={5} value={interval} onChange={(e) => setInterval_(Number(e.target.value))} className="w-20" /></label>
          {argsError && <p role="alert" className="m-0 w-full text-xs text-destructive">{argsError}</p>}
        </> : kind === "webhook" ? <span className="text-xs text-muted-foreground">You get a URL and a secret to call it with.</span>
        : <>
          <label className="flex flex-col gap-1 text-xs">Detector starts with<Input value={prefix} onChange={(e) => setPrefix(e.target.value)} className="w-40 font-mono" /></label>
          <label className="flex items-center gap-1.5 text-xs"><Checkbox checked={critical} onCheckedChange={(v) => setCritical(v === true)} /> critical</label>
          <label className="flex items-center gap-1.5 text-xs"><Checkbox checked={warning} onCheckedChange={(v) => setWarning(v === true)} /> warning</label>
        </>}
        <Button size="sm" disabled={busy} onClick={add}>Add</Button>
      </fieldset>
    </div>
  );
}

/* ------------------------------------------------------------------ Chat */

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
        {!chat.length && <li className="text-sm text-muted-foreground">Ask it anything. You can also say “every Monday at 9, email me the weekly Meta report”: it sets the schedule up itself and tells you what it changed.</li>}
      </ol>
      <div className="flex gap-2">
        <Textarea aria-label="Message the bot" value={text} rows={2} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <Button disabled={busy || !text.trim()} onClick={send}>Send</Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ Advanced */

function Advanced({ bot, onDeleted }: { bot: Bot; onDeleted: () => void }) {
  const readTools = useBotsStore((s) => s.readTools);
  const save = useBotsStore((s) => s.save);
  const remove = useBotsStore((s) => s.remove);
  const busy = useBotsStore((s) => s.busy);
  const [limits, setLimits] = useState(bot.limits);
  useEffect(() => setLimits(bot.limits), [bot.bot_id, bot.version]); // eslint-disable-line react-hooks/exhaustive-deps
  const fromSkills = new Set((bot.run_tools ?? []).filter((t) => !bot.tools.includes(t)));
  const toggle = (tool: string, on: boolean) => void save({ tools: on ? [...bot.tools, tool] : bot.tools.filter((t) => t !== tool) });
  return (
    <div className="flex flex-col gap-8">
      <Block title="Extra tools" hint="Skills bring the tools they need. Add a live read tool here only when no skill covers it. Bots are read-only: changes are proposed and a person makes them.">
        <ul className="m-0 grid list-none grid-cols-2 gap-1.5 p-0 max-md:grid-cols-1">
          {readTools.map((tool) => (
            <li key={tool}>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={bot.tools.includes(tool) || fromSkills.has(tool)} disabled={busy || fromSkills.has(tool)} onCheckedChange={(v) => toggle(tool, v === true)} />
                {humanize(tool)}{fromSkills.has(tool) && <span className="text-xs text-muted-foreground">from a skill</span>}
              </label>
            </li>
          ))}
        </ul>
      </Block>
      <Block title="Limits" hint="Automatic runs per day (schedules, findings, watches, webhooks). Run now and chat are not limited.">
        <div className="flex flex-wrap items-end gap-3">
          {Object.entries(limits).filter(([k]) => ENFORCED_LIMITS.includes(k)).map(([k, v]) => (
            <label key={k} className="flex flex-col gap-1.5 text-sm font-medium">Runs per day
              <Input type="number" min={0} value={v} onChange={(e) => setLimits({ ...limits, [k]: Number(e.target.value) })} className="w-28" />
            </label>
          ))}
          <Button disabled={busy || JSON.stringify(limits) === JSON.stringify(bot.limits)} onClick={() => void save({ limits })}>Save limits</Button>
        </div>
      </Block>
      <section className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/30 px-4 py-3">
        <div>
          <h4 className="m-0 text-sm font-semibold">Delete this bot</h4>
          <p className="m-0 text-sm text-muted-foreground">Removes its runs, activity, schedules and skills for good. Its memory and documents are kept.</p>
        </div>
        <Button variant="destructive" disabled={busy}
          onClick={async () => {
            if (!window.confirm(`Delete “${bot.name}”? Its runs, activity, schedules and skills are removed for good. Its memory is kept.`)) return;
            await remove(bot.bot_id);
            if (!useBotsStore.getState().bots.some((b) => b.bot_id === bot.bot_id)) onDeleted();
          }}>Delete bot</Button>
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ the page */

export const SECTIONS = [
  { key: "goal", name: "Goal", purpose: "What this bot is for, in your words." },
  { key: "skills", name: "Skills", purpose: "What it knows how to do, and the data each skill reads." },
  { key: "knowledge", name: "Knowledge", purpose: "Documents it can read, and what it has learned." },
  { key: "schedules", name: "Schedules", purpose: "When it runs, what it sends, and to whom." },
  { key: "chat", name: "Chat", purpose: "Ask it anything, or tell it to change its goal or schedules." },
  { key: "advanced", name: "Advanced", purpose: "Extra tools, limits, and deleting the bot." },
] as const;
export type SectionKey = (typeof SECTIONS)[number]["key"];

export function BotSettingsPage({ bot, section, onSection, onBack, onDeleted }: {
  bot: Bot; section: SectionKey; onSection: (s: SectionKey) => void; onBack: () => void; onDeleted: () => void;
}) {
  const chat = useBotsStore((s) => s.chat);
  const current = SECTIONS.find((s) => s.key === section) ?? SECTIONS[0];
  const body = useMemo(() => ({
    goal: <Goal bot={bot} />, skills: <Skills bot={bot} />, knowledge: <Knowledge bot={bot} />,
    schedules: <Schedules bot={bot} />, chat: <Chat chat={chat} />, advanced: <Advanced bot={bot} onDeleted={onDeleted} />,
  }[current.key]), [current.key, bot, chat, onDeleted]);
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-6 px-6 pt-4 pb-16 max-md:px-3">
        <header className="flex flex-col gap-1">
          <Button variant="ghost" size="sm" className="-ml-2 self-start text-muted-foreground pointer-coarse:h-11" onClick={onBack}><ChevronLeftIcon />{bot.name}</Button>
          <h2 className="m-0 font-serif text-2xl leading-tight font-normal tracking-tight">Settings</h2>
          <p className="m-0 text-sm text-muted-foreground">{bot.name} · changes apply from its next run</p>
        </header>
        <div className="grid grid-cols-[minmax(0,1fr)] gap-8 md:grid-cols-[220px_minmax(0,1fr)]">
          <nav aria-label="Settings sections" className="min-w-0 md:sticky md:top-4 md:self-start">
            <ul className="m-0 flex list-none flex-col gap-0.5 p-0 max-md:-mx-3 max-md:flex-row max-md:gap-1 max-md:overflow-x-auto max-md:border-b max-md:px-3 max-md:pb-2 max-md:[scrollbar-width:none]">
              {SECTIONS.map((s) => (
                <li key={s.key} className="max-md:shrink-0">
                  <Button variant="ghost" aria-current={s.key === current.key ? "page" : undefined} onClick={() => onSection(s.key)}
                    className={cn("h-auto w-full flex-col items-start gap-0 px-3 py-2 text-left font-normal whitespace-normal pointer-coarse:min-h-11",
                      s.key === current.key && "bg-[var(--brand-soft)] hover:bg-[var(--brand-soft)]")}>
                    <span className="font-medium">{s.name}</span>
                    <span className="text-xs text-muted-foreground max-md:hidden">{s.purpose}</span>
                  </Button>
                </li>
              ))}
            </ul>
          </nav>
          <section aria-labelledby="bot-settings-section" className="flex max-w-[760px] min-w-0 flex-col gap-6">
            <div>
              <h3 id="bot-settings-section" className="m-0 text-lg font-semibold">{current.name}</h3>
              <p className="m-0 text-sm text-muted-foreground">{current.purpose}</p>
            </div>
            {body}
          </section>
        </div>
      </div>
    </div>
  );
}
