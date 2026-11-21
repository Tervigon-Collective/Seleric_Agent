import { useEffect, useState } from "react";
import { modelSettingsApi, type ModelChoice, type ModelSettings as Settings } from "@/api/modelSettings";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

/**
 * Settings → Models, laid out like Rakazo's: Providers (connect with an API key or a compatible server URL),
 * Main model + fallbacks with a Thinking level on the first, and a Helper model list. Applies to the whole workspace.
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
          <DialogDescription>Choose which AI models the agent thinks with, using your own API key from any provider. These settings apply to everyone in this workspace.</DialogDescription>
        </DialogHeader>
        {error && <Alert variant="destructive" role="alert"><AlertDescription>{error}</AlertDescription></Alert>}
        {!data ? <p className="text-sm text-muted-foreground">Loading…</p> : (
          <div className="flex flex-col gap-6">
            <section aria-labelledby="models-providers" className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <h3 id="models-providers" className="text-sm font-semibold">Providers</h3>
                {!adding && <Button size="sm" variant="outline" onClick={() => setAdding(true)} disabled={!data.can_save_keys}>Add provider</Button>}
              </div>
              {!data.can_save_keys && <p className="text-xs text-muted-foreground">Adding providers needs SELERIC_MODELS_KEY on the server.</p>}
              <ul className="flex flex-col divide-y rounded-lg border">
                {data.connections.map((c) => (
                  <li key={c.id} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                    <span className="min-w-0 truncate">{c.label}{c.key_hint && <span className="text-muted-foreground"> · ••••{c.key_hint}</span>}{c.builtin && <span className="text-muted-foreground"> · server</span>}</span>
                    {!c.builtin && (
                      <Button size="sm" variant="ghost" disabled={pending !== null}
                        onClick={() => window.confirm(`Disconnect ${c.label}? Its models leave both lists.`) && void run("disconnect", async () => { await modelSettingsApi.disconnect(c.id); load(); })}>
                        {pending === "disconnect" ? "Disconnecting…" : "Disconnect"}
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
              {adding && (
                <ConnectForm
                  providers={data.providers}
                  busy={pending === "connect"}
                  onCancel={() => setAdding(false)}
                  onConnect={(body) => run("connect", async () => {
                    const r = await modelSettingsApi.connect(body);
                    setModels((m) => ({ ...m, [r.connection.id]: r.models }));
                    setAdding(false);
                    const next = await modelSettingsApi.get();
                    setData(next);
                  })}
                />
              )}
            </section>

            <RoleList
              title="Main model" description="What every question uses. If it fails or hits a limit, the agent moves to the next fallback."
              rows={main} onChange={setMain} defaults={data.defaults.main} data={data} models={models} loadModels={loadModels} nameOf={nameOf} thinking
            />
            <RoleList
              title="Helper model" description="Rewrites questions and small checks. A fast, inexpensive model works well here."
              rows={helper} onChange={setHelper} defaults={data.defaults.helper} data={data} models={models} loadModels={loadModels} nameOf={nameOf}
            />

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

function ConnectForm({ providers, busy, onCancel, onConnect }: {
  providers: Settings["providers"]; busy: boolean; onCancel: () => void;
  onConnect: (body: { provider: string; api_key: string; base_url?: string }) => void;
}) {
  const [provider, setProvider] = useState(providers[0]?.id ?? "openai");
  const [key, setKey] = useState("");
  const [url, setUrl] = useState("");
  const custom = provider === "compatible";
  const name = providers.find((p) => p.id === provider)?.name ?? provider;
  return (
    <form className="flex flex-col gap-2 rounded-lg border p-3" onSubmit={(e) => { e.preventDefault(); onConnect({ provider, api_key: key, ...(custom ? { base_url: url } : {}) }); }}>
      <NativeSelect aria-label="Provider" className="w-full" value={provider} onChange={(e) => setProvider(e.target.value)}>
        {providers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </NativeSelect>
      {custom && <Input aria-label="Server URL" placeholder="https://your-server/v1" value={url} onChange={(e) => setUrl(e.target.value)} required />}
      <Input aria-label="API key" type="password" autoComplete="off" placeholder="API key" value={key} onChange={(e) => setKey(e.target.value)} required />
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
        <Button type="submit" size="sm" disabled={busy}>{busy ? "Connecting…" : `Connect ${name}`}</Button>
      </div>
    </form>
  );
}

function RoleList({ title, description, rows, onChange, defaults, data, models, loadModels, nameOf, thinking }: {
  title: string; description: string; rows: ModelChoice[]; onChange: (rows: ModelChoice[]) => void; defaults: ModelChoice[];
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
      <p className="text-xs text-muted-foreground">{description}</p>
      {rows.length === 0 && (
        <p className="text-sm text-muted-foreground">Server default: {defaults.length ? defaults.map((d) => `${nameOf(d.connection)} · ${d.model}`).join(" → ") : "none"}</p>
      )}
      {rows.map((row, i) => (
        <div key={i} className="flex flex-wrap items-center gap-2" aria-label={i === 0 ? title : `Fallback ${i}`} role="group">
          <span className="w-16 text-xs text-muted-foreground">{i === 0 ? "Main" : `Fallback ${i}`}</span>
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
          <Button size="xs" variant="ghost" disabled={i === 0} onClick={() => move(i, i - 1)} aria-label="Move up">↑</Button>
          <Button size="xs" variant="ghost" disabled={i === rows.length - 1} onClick={() => move(i, i + 1)} aria-label="Move down">↓</Button>
          <Button size="xs" variant="ghost" onClick={() => onChange(rows.filter((_, j) => j !== i).map((r, j) => (j ? { ...r, thinking: undefined } : r)))} aria-label="Remove">Remove</Button>
        </div>
      ))}
      {rows.length <= data.max_fallbacks && (
        <Button size="sm" variant="outline" className="self-start" disabled={!first}
          onClick={() => onChange([...rows, { connection: rows.at(-1)?.connection ?? first, model: "" }])}>
          {rows.length ? "Add fallback" : "Choose model"}
        </Button>
      )}
    </section>
  );
}
