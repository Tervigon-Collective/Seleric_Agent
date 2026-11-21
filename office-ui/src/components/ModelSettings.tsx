import { useEffect, useState } from "react";
import { modelSettingsApi, type ModelChoice, type ModelSettings as Settings } from "@/api/modelSettings";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

/**
 * Settings → Models: a ChatGPT plan sign-in, the Claude Code CLI signed in on this machine,
 * or an API key. Main model + fallbacks, and a Helper list.
 */
export function ModelSettings({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [data, setData] = useState<Settings | null>(null);
  const [main, setMain] = useState<ModelChoice[]>([]);
  const [helper, setHelper] = useState<ModelChoice[]>([]);
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [adding, setAdding] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [signIn, setSignIn] = useState<{ id: string; url: string } | null>(null);

  const load = (next?: Settings) => {
    const apply = (s: Settings) => { setData(s); setMain(s.main); setHelper(s.helper); };
    if (next) return apply(next);
    modelSettingsApi.get().then(apply, (e: Error) => setError(e.message));
  };
  useEffect(() => { if (open) load(); }, [open]);

  const loadModels = (id: string) => {
    if (!id || models[id]) return;
    modelSettingsApi.models(id).then((r) => setModels((m) => ({ ...m, [id]: r.models })), () => setModels((m) => ({ ...m, [id]: [] })));
  };

  const run = async (label: string, task: () => Promise<void>) => {
    setPending(label); setError(null); setSaved(false);
    try { await task(); } catch (e) { setError((e as Error).message); } finally { setPending(null); }
  };

  const nameOf = (id: string) => data?.connections.find((c) => c.id === id)?.label ?? id;

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Models</DialogTitle>
          <DialogDescription>Which models the agent uses.</DialogDescription>
        </DialogHeader>
        {error && <Alert variant="destructive" role="alert"><AlertDescription>{error}</AlertDescription></Alert>}
        {!data ? <p className="text-sm text-muted-foreground">Loading…</p> : (
          <div className="flex flex-col gap-6">
            <RoleList
              title="Main model"
              rows={main} onChange={setMain} defaults={data.defaults.main} data={data} models={models} loadModels={loadModels} nameOf={nameOf} thinking
            />
            <RoleList
              title="Helper model"
              rows={helper} onChange={setHelper} defaults={data.defaults.helper} data={data} models={models} loadModels={loadModels} nameOf={nameOf}
            />

            <section aria-labelledby="models-providers" className="flex flex-col gap-2">
              <h3 id="models-providers" className="text-sm font-semibold">Providers</h3>
              {!data.can_save_keys && <p className="text-xs text-muted-foreground">Adding providers needs SELERIC_MODELS_KEY on the server.</p>}
              <div className="overflow-hidden rounded-lg border">
                {data.connections.map((c) => (
                  <ProviderRow key={c.id} connection={c} models={models[c.id]} pending={pending}
                    onOpen={() => loadModels(c.id)}
                    onDisconnect={!c.builtin ? () => window.confirm(`Disconnect ${c.label}? Its models leave both lists.`) && void run("disconnect", async () => { await modelSettingsApi.disconnect(c.id); load(); }) : undefined}
                  />
                ))}
                {!adding && (
                  <button type="button" className="flex w-full items-center gap-2 px-3 py-3 text-left text-sm text-muted-foreground hover:bg-muted/40" onClick={() => setAdding(true)} disabled={!data.can_save_keys}>
                    + Add provider
                  </button>
                )}
              </div>
              {signIn && (
                <p className="text-xs text-muted-foreground">Waiting for ChatGPT to return to this site. The plan sign-in is saved when that tab finishes. If the tab did not open, <a className="underline" href={signIn.url} target="_blank" rel="noreferrer">open ChatGPT sign-in</a>.</p>
              )}
              {adding && (
                <ConnectForm
                  providers={data.providers}
                  busy={pending === "connect"}
                  onCancel={() => setAdding(false)}
                  onConnect={(body) => run("connect", async () => {
                    const r = await modelSettingsApi.connect(body);
                    setModels((m) => ({ ...m, [r.connection.id]: r.models }));
                    setAdding(false);
                    setData(await modelSettingsApi.get());
                  })}
                  onChatGPT={() => run("connect", async () => {
                    const started = await modelSettingsApi.startChatGPT();
                    setSignIn({ id: started.login_id, url: started.url });
                    window.open(started.url, "_blank", "noopener,noreferrer");
                    const deadline = Date.now() + 5 * 60 * 1000;
                    while (Date.now() < deadline) {
                      await new Promise((r) => setTimeout(r, 1500));
                      const status = await modelSettingsApi.chatgptStatus(started.login_id);
                      if (status.status === "connected" && status.connection) {
                        setModels((m) => ({ ...m, [status.connection!.id]: status.models ?? [] }));
                        setAdding(false);
                        setData(await modelSettingsApi.get());
                        return;
                      }
                      if (status.status === "error") throw new Error(status.error || "Sign-in failed");
                    }
                    throw new Error("Sign-in timed out");
                  }).finally(() => setSignIn(null))}
                  onClaude={() => run("connect", async () => {
                    const r = await modelSettingsApi.connectClaude();
                    setModels((m) => ({ ...m, [r.connection.id]: r.models }));
                    setAdding(false);
                    setData(await modelSettingsApi.get());
                  })}
                />
              )}
            </section>

            <div className="flex items-center justify-end gap-2">
              {saved && <span className="text-sm text-muted-foreground" role="status">Saved</span>}
              <Button disabled={pending !== null} onClick={() => void run("save", async () => {
                load(await modelSettingsApi.saveRoles({ version: data.version, main, helper }));
                setSaved(true);
              })}>{pending === "save" ? "Saving… (testing the main model)" : "Save"}</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ConnectForm({ providers, busy, onCancel, onConnect, onChatGPT, onClaude }: {
  providers: Settings["providers"]; busy: boolean; onCancel: () => void;
  onConnect: (body: { provider: string; api_key: string; base_url?: string }) => void;
  onChatGPT: () => void;
  onClaude: () => void;
}) {
  const [provider, setProvider] = useState(providers[0]?.id ?? "openai");
  const [key, setKey] = useState("");
  const [url, setUrl] = useState("");
  const selected = providers.find((p) => p.id === provider);
  const auth = selected?.auth ?? "key";
  const custom = provider === "compatible";
  const name = selected?.name ?? provider;
  return (
    <form className="flex flex-col gap-2 rounded-lg border p-3" onSubmit={(e) => { e.preventDefault(); if (auth === "key") onConnect({ provider, api_key: key, ...(custom ? { base_url: url } : {}) }); }}>
      <NativeSelect aria-label="Provider" className="w-full" value={provider} onChange={(e) => setProvider(e.target.value)}>
        {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </NativeSelect>
      {auth === "oauth" && <p className="text-xs text-muted-foreground">Opens ChatGPT sign-in in a new tab. ChatGPT sends you back to this site, and the plan sign-in is saved here. The plan pays for the runs.</p>}
      {auth === "cli" && <p className="text-xs text-muted-foreground">Uses the Claude Code sign-in already on this machine. Run claude auth login first. The plan pays for the runs, and the token stays in Claude Code.</p>}
      {auth === "key" && custom && <Input aria-label="Server URL" placeholder="https://your-server/v1" value={url} onChange={(e) => setUrl(e.target.value)} required />}
      {auth === "key" && <Input aria-label="API key" type="password" autoComplete="off" placeholder="API key" value={key} onChange={(e) => setKey(e.target.value)} required />}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
        {auth === "oauth" && <Button type="button" size="sm" disabled={busy} onClick={onChatGPT}>{busy ? "Waiting for sign-in…" : "Sign in with ChatGPT"}</Button>}
        {auth === "cli" && <Button type="button" size="sm" disabled={busy} onClick={onClaude}>{busy ? "Checking…" : "Use Claude Code"}</Button>}
        {auth === "key" && <Button type="submit" size="sm" disabled={busy}>{busy ? "Connecting…" : `Connect ${name}`}</Button>}
      </div>
    </form>
  );
}

function ProviderRow({ connection, models, pending, onOpen, onDisconnect }: {
  connection: Settings["connections"][number]; models: string[] | undefined; pending: string | null;
  onOpen: () => void; onDisconnect?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const count = models ? `${models.length} model${models.length === 1 ? "" : "s"}` : "…";
  return (
    <div className="border-b last:border-b-0">
      <button type="button" className="flex w-full items-center justify-between gap-3 px-3 py-3 text-left text-sm hover:bg-muted/40"
        aria-expanded={open}
        onClick={() => { onOpen(); setOpen((v) => !v); }}>
        <span className="min-w-0 truncate">{connection.label}</span>
        <span className="shrink-0 text-muted-foreground">{count} ›</span>
      </button>
      {open && (
        <div className="flex flex-col gap-2 border-t px-3 py-2">
          <p className="text-xs text-muted-foreground">{models?.length ? models.slice(0, 8).join(", ") : "No models listed"}{models && models.length > 8 ? "…" : ""}</p>
          {onDisconnect && (
            <Button size="sm" variant="ghost" className="self-start" disabled={pending !== null} onClick={onDisconnect}>
              {pending === "disconnect" ? "Disconnecting…" : "Disconnect"}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function RoleList({ title, rows, onChange, defaults, data, models, loadModels, nameOf, thinking }: {
  title: string; rows: ModelChoice[]; onChange: (rows: ModelChoice[]) => void; defaults: ModelChoice[];
  data: Settings; models: Record<string, string[]>; loadModels: (id: string) => void; nameOf: (id: string) => string; thinking?: boolean;
}) {
  const id = title.toLowerCase().replace(" ", "-");
  const set = (i: number, patch: Partial<ModelChoice>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const move = (i: number, to: number) => { const next = [...rows]; [next[i], next[to]] = [next[to], next[i]]; onChange(next.map((r, j) => (j ? { ...r, thinking: undefined } : r))); };
  const first = data.connections[0]?.id ?? "";
  useEffect(() => { rows.forEach((r) => loadModels(r.connection)); }, [rows]);
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <h3 id={id} className="text-sm font-semibold">{title}</h3>
      <div className="overflow-hidden rounded-lg border">
        {rows.length === 0 && (
          <p className="border-b px-3 py-3 text-sm text-muted-foreground">Server default: {defaults.length ? defaults.map((d) => `${nameOf(d.connection)} · ${d.model}`).join(" → ") : "none"}</p>
        )}
        {rows.map((row, i) => (
          <div key={i} className="flex flex-col gap-2 border-b px-3 py-3" aria-label={i === 0 ? title : `Fallback ${i}`} role="group">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="truncate text-sm">{row.model || (i === 0 ? "Choose a model" : `Fallback ${i}`)}</p>
                <p className="text-xs text-muted-foreground">{nameOf(row.connection)}</p>
              </div>
              <div className="flex shrink-0 items-center">
                <Button size="xs" variant="ghost" disabled={i === 0} onClick={() => move(i, i - 1)} aria-label="Move up">↑</Button>
                <Button size="xs" variant="ghost" disabled={i === rows.length - 1} onClick={() => move(i, i + 1)} aria-label="Move down">↓</Button>
                <Button size="xs" variant="ghost" onClick={() => onChange(rows.filter((_, j) => j !== i).map((r, j) => (j ? { ...r, thinking: undefined } : r)))} aria-label="Remove">Remove</Button>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <NativeSelect aria-label="Provider" size="sm" value={row.connection} onChange={(e) => set(i, { connection: e.target.value })}>
                {data.connections.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
              </NativeSelect>
              <Input aria-label="Model" className="h-7 w-48" list={`${id}-${row.connection}`} value={row.model} placeholder="Model" onChange={(e) => set(i, { model: e.target.value })} />
              <datalist id={`${id}-${row.connection}`}>{(models[row.connection] ?? []).map((m) => <option key={m} value={m} />)}</datalist>
              {thinking && i === 0 && (
                <NativeSelect aria-label="Thinking" size="sm" value={row.thinking ?? ""} onChange={(e) => set(i, { thinking: e.target.value || undefined })}>
                  <option value="">Thinking: default</option>
                  {data.thinking_levels.map((t) => <option key={t} value={t}>Thinking: {t}</option>)}
                </NativeSelect>
              )}
            </div>
          </div>
        ))}
        {rows.length <= data.max_fallbacks && (
          <button type="button" className="flex w-full items-center gap-2 px-3 py-3 text-left text-sm text-muted-foreground hover:bg-muted/40 disabled:opacity-50" disabled={!first}
            onClick={() => onChange([...rows, { connection: rows.at(-1)?.connection ?? first, model: "" }])}>
            {rows.length ? "+ Add fallback" : "Choose model"}
          </button>
        )}
      </div>
    </section>
  );
}
