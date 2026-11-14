import { useEffect, useRef } from "react";
import { Transcript } from "../Transcript";
import { Composer } from "../Composer";
import { PanelErrorBoundary } from "../ErrorBoundary";
import { SelericAssistantRuntimeProvider } from "../../providers/SelericAssistantRuntime";
import { XIcon } from "../icons";
import { AppFrame } from "../AppFrame";
import { Button, buttonVariants } from "@/components/ui/button";
import { ThreadSidebar, isEvalThread } from "../ThreadSidebar";
import { useConversationStore } from "../../stores/conversation";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { HistoryIcon } from "lucide-react";
import { useMonitoringStore } from "../../stores/monitoring";
import { useShellStore } from "../../stores/shell";
import { MonitoringConfig } from "./MonitoringConfig";
import { OpsDashboard } from "./OpsDashboard";

const POLL_MS = 60_000;

/** Recent conversations, newest first; picking one loads it into the Ask panel. */
function AskHistory() {
  const threads = useConversationStore((s) => s.threads);
  const selected = useConversationStore((s) => s.selectedThreadId);
  const selectThread = useConversationStore((s) => s.selectThread);
  const recent = threads.filter((t) => !isEvalThread(t.title)).sort((a, b) => +new Date(b.updated_at) - +new Date(a.updated_at)).slice(0, 15);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger aria-label="Conversation history" title="History" className={buttonVariants({ variant: "ghost", size: "icon-sm" })}><HistoryIcon /></DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-80 w-72">
        <DropdownMenuLabel>History</DropdownMenuLabel>
        {recent.length === 0 && <p className="m-0 px-2 py-1.5 text-sm text-muted-foreground">No conversations yet</p>}
        {recent.map((t) => (
          <DropdownMenuItem key={t.id} className={t.id === selected ? "bg-accent" : undefined} onSelect={() => void selectThread(t.id)}>
            <span className="truncate">{t.title || "Untitled conversation"}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function OpsWorkspace(_props: { dark?: boolean } = {}) {
  const page = useShellStore((s) => s.opsPage);
  const openOps = useShellStore((s) => s.openOps);
  const askOpen = useShellStore((s) => s.askOpen);
  const askContext = useShellStore((s) => s.askContext);
  const setAskOpen = useShellStore((s) => s.setAskOpen);
  const returnFocus = useRef<Element | null>(null);
  const load = useMonitoringStore((s) => s.load);

  useEffect(() => {
    void load();
    // Re-read while the tab is visible; the server only recomputes on its own schedule, so this is cheap.
    const id = window.setInterval(() => { if (!document.hidden) void load(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  // The chat comes over the page when asked for. Esc closes it, and focus goes back to what opened it.
  useEffect(() => {
    if (!askOpen) return;
    returnFocus.current = document.activeElement;
    const key = (e: KeyboardEvent) => { if (e.key === "Escape" && !document.querySelector('[data-slot="dropdown-menu-content"]')) { e.stopPropagation(); setAskOpen(false); } };
    window.addEventListener("keydown", key, true);
    const focus = window.setTimeout(() => document.querySelector<HTMLTextAreaElement>('[data-slot="ask-overlay"] textarea[aria-label="Message"]')?.focus(), 220);
    return () => { window.removeEventListener("keydown", key, true); window.clearTimeout(focus); (returnFocus.current as HTMLElement | null)?.focus?.(); };
  }, [askOpen, setAskOpen]);

  return (
    <AppFrame
      workspace="ops"
      sidebarLabel="Ops navigation"
      toggleLabel="Toggle navigation"
      sidebarBody={<ThreadSidebar />}
    >
      <main className="min-h-0 flex-1 overflow-y-auto scroll-smooth">
        <div className="w-full px-4 pt-0 pb-10 max-md:px-3 page-enter" key={page}>
          {page === "dashboard" ? <OpsDashboard /> : <>
            <header className="mb-4 flex max-w-6xl flex-wrap items-end justify-between gap-3">
              <h2 className="m-0 font-serif text-[34px] leading-tight font-normal tracking-tight max-md:text-3xl">Choose what to monitor</h2>
              <Button variant="outline" onClick={() => openOps("dashboard")}>Back to Monitoring</Button>
            </header>
            <div className="max-w-6xl"><MonitoringConfig /></div>
          </>}
        </div>
      </main>
      {askOpen && (
        <>
          <div className="fixed inset-0 z-40 bg-black/20 animate-in fade-in duration-200" aria-hidden="true" onClick={() => setAskOpen(false)} />
          <aside role="dialog" aria-label="Ask Seleric" data-slot="ask-overlay" className="fixed inset-y-0 right-0 z-50 flex w-[420px] max-w-full flex-col border-l bg-background shadow-[var(--shadow-lg)] animate-in slide-in-from-right duration-200">
            <div className="flex h-12 shrink-0 items-center justify-between border-b px-3">
              <h2 className="m-0 font-serif text-lg font-medium">Ask Seleric</h2>
              <div className="flex items-center gap-0.5">
                <AskHistory />
                <Button variant="ghost" size="icon-sm" aria-label="Close chat panel" onClick={() => setAskOpen(false)}><XIcon /></Button>
              </div>
            </div>
            {askContext && <p role="status" className="m-0 mx-3 mt-2 rounded-lg bg-[var(--brand-soft)] px-2.5 py-1.5 text-xs animate-in fade-in slide-in-from-bottom-1 duration-200">Investigating: {askContext}</p>}
            <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
              <PanelErrorBoundary name="ask">
                <SelericAssistantRuntimeProvider>
                  <Transcript />
                  <Composer />
                </SelericAssistantRuntimeProvider>
              </PanelErrorBoundary>
            </div>
          </aside>
        </>
      )}
    </AppFrame>
  );
}
