import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Composer } from "./components/Composer";
import { DetailPanel } from "./components/DetailPanel";
import { PanelErrorBoundary } from "./components/ErrorBoundary";
import { AppFrame, APP_EVENTS } from "./components/AppFrame";
import { ResponsivePanel } from "./components/ResponsivePanel";
import { ThreadSidebar } from "./components/ThreadSidebar";
import { Transcript } from "./components/Transcript";
import { VoiceOverlay } from "./components/VoiceOverlay";
import { useWakeWord } from "./wakeWord";
import { Skeleton } from "@/components/ui/skeleton";
import { CheckIcon, LinkIcon, PanelRightIcon } from "./components/icons";
import { SelericAssistantRuntimeProvider } from "./providers/SelericAssistantRuntime";
import { useConversationStore } from "./stores/conversation";
import { useShellStore } from "./stores/shell";
import { useThemeStore } from "./stores/theme";
import { isTraceableMissionId, useOffice } from "./store";
import { readRoute, writeRoute } from "./routing";

// Rarely-used surfaces load on demand: the canvas/ops workspaces on navigation, the dialogs on first open.
const OfficeWorkspace = lazy(() => import("./components/OfficeWorkspace").then((m) => ({ default: m.OfficeWorkspace })));
const OpsWorkspace = lazy(() => import("./components/ops/OpsWorkspace").then((m) => ({ default: m.OpsWorkspace })));
const CommandSearch = lazy(() => import("./components/CommandSearch").then((m) => ({ default: m.CommandSearch })));
const AdminDiagnostics = lazy(() => import("./components/AdminDiagnostics").then((m) => ({ default: m.AdminDiagnostics })));
const PromptRegistryModal = lazy(() => import("./components/PromptRegistryModal").then((m) => ({ default: m.PromptRegistryModal })));

const workspaceFallback = <Skeleton className="h-screen w-full rounded-none" role="status" aria-busy="true" aria-label="Loading workspace" />;

/** Copy-link sharing: the URL already encodes the thread, so the link reopens it. */
function useCopyLink() {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  const copy = () => {
    const url = location.href;
    const done = () => {
      setCopied(true);
      if (timer.current) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1600);
    };
    if (navigator.clipboard?.writeText) void navigator.clipboard.writeText(url).then(done).catch(() => undefined);
    else {
      window.prompt("Copy this link:", url);
      done();
    }
  };
  return { copied, copy };
}

function ShareButton({ threadId, className }: { threadId: string | null; className?: string }) {
  const { copied, copy } = useCopyLink();
  if (!threadId) return null;
  return (
    <Button
      variant="outline"
      size="sm"
      className={className}
      aria-label={copied ? "Link copied" : "Copy link to this conversation"}
      title="Copy link to this conversation"
      onClick={copy}
    >
      {copied ? <CheckIcon /> : <LinkIcon />}
      <span>{copied ? "Copied" : "Share"}</span>
    </Button>
  );
}

export default function App() {
  useWakeWord();
  const [searchOpen, setSearchOpen] = useState(false);
  const [promptsOpen, setPromptsOpen] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [routingReady, setRoutingReady] = useState(false);
  const dark = useThemeStore((s) => s.dark);
  const syncSystemTheme = useThemeStore((s) => s.syncSystem);
  const firstTheme = useRef(true);
  useEffect(() => {
    // A brief crossfade on a real theme change, not on first paint.
    const root = document.documentElement;
    if (!firstTheme.current) {
      root.classList.add("theme-fade");
      const timer = window.setTimeout(() => root.classList.remove("theme-fade"), 320);
      return () => window.clearTimeout(timer);
    }
  }, [dark]);
  useEffect(() => {
    // data-theme is always light or dark, so the CSS and the shadcn dark variant keep one switch.
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
    document.querySelector('meta[name="theme-color"]:not([media])')?.setAttribute("content", dark ? "#17181c" : "#ffffff");
    firstTheme.current = false;
  }, [dark]);
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-color-scheme: dark)");
    query?.addEventListener("change", syncSystemTheme);
    return () => query?.removeEventListener("change", syncSystemTheme);
  }, [syncSystemTheme]);
  const workspace = useShellStore((s) => s.workspace);
  const opsPage = useShellStore((s) => s.opsPage);
  const setWorkspace = useShellStore((s) => s.setWorkspace);
  const detailsOpen = useShellStore((s) => s.detailsOpen);
  const toggleDetails = useShellStore((s) => s.toggleDetails);
  const loadThreads = useConversationStore((s) => s.loadThreads);
  const error = useConversationStore((s) => s.error);
  const selectThread = useConversationStore((s) => s.selectThread);
  const clearSelection = useConversationStore((s) => s.clearSelection);
  const selectedThreadId = useConversationStore((s) => s.selectedThreadId);
  const threads = useConversationStore((s) => s.threads);
  const currentTitle = threads.find((t) => t.id === selectedThreadId)?.title || null;
  const missionId = useOffice((s) => s.missionId);
  useEffect(() => {
    // Narrow viewports always start with panels folded; desktop keeps the
    // last inspector choice from localStorage (see shell store).
    if (window.matchMedia?.("(max-width: 1023px)").matches) {
      useShellStore.setState({ sidebarOpen: false, detailsOpen: false });
    }
  }, []);
  useEffect(() => {
    let alive = true;
    void loadThreads().then(async () => {
      const route = readRoute();
      if (route.threadId) await selectThread(route.threadId);
      if (alive) setRoutingReady(true);
    });
    return () => { alive = false; };
  }, [loadThreads, selectThread]);
  useEffect(() => {
    if (!routingReady) return;
    writeRoute({
      workspace,
      opsPage,
      threadId: selectedThreadId,
      // Keep URL mission only while a real run is attached. "pending" leaves the
      // previous param alone during submit handshake; idle/empty clears it so
      // Debug does not reopen a prior mission on untitled chats.
      ...(isTraceableMissionId(missionId)
        ? { missionId }
        : missionId === "pending"
          ? {}
          : { missionId: null }),
    });
  }, [routingReady, workspace, opsPage, selectedThreadId, missionId]);
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault(); setSearchOpen(true);
      }
      // Modals handle their own Escape; this only folds the side panels.
      if (event.key === "Escape" && !searchOpen && !promptsOpen && !diagnosticsOpen) {
        const shell = useShellStore.getState();
        if (shell.detailsOpen) shell.toggleDetails();
        else if (shell.sidebarOpen) shell.toggleSidebar();
      }
    };
    const openPrompts = () => { setWorkspace("conversation"); setPromptsOpen(true); };
    const openSearch = () => setSearchOpen(true);
    const openDiagnostics = () => setDiagnosticsOpen(true);
    const result = (event: Event) => {
      const detail = (event as CustomEvent<{ thread_id?: string }>).detail;
      if (detail?.thread_id) { setWorkspace("conversation"); void selectThread(detail.thread_id); }
    };
    const popstate = () => {
      const route = readRoute();
      if (route.workspace === "ops") useShellStore.getState().openOps(route.opsPage);
      else setWorkspace(route.workspace);
      if (route.threadId && route.threadId !== useConversationStore.getState().selectedThreadId) {
        void selectThread(route.threadId);
      } else if (!route.threadId) {
        clearSelection();
      }
    };
    window.addEventListener("keydown", keyboard);
    window.addEventListener("seleric:search-result", result);
    window.addEventListener(APP_EVENTS.prompts, openPrompts);
    window.addEventListener(APP_EVENTS.search, openSearch);
    window.addEventListener(APP_EVENTS.diagnostics, openDiagnostics);
    window.addEventListener("popstate", popstate);
    return () => {
      window.removeEventListener("keydown", keyboard);
      window.removeEventListener("seleric:search-result", result);
      window.removeEventListener(APP_EVENTS.prompts, openPrompts);
      window.removeEventListener(APP_EVENTS.search, openSearch);
      window.removeEventListener(APP_EVENTS.diagnostics, openDiagnostics);
      window.removeEventListener("popstate", popstate);
    };
  }, [clearSelection, selectThread, setWorkspace, searchOpen, promptsOpen, diagnosticsOpen]);

  const dialogs = (
    <>
      {searchOpen && <Suspense fallback={null}><CommandSearch open onClose={() => setSearchOpen(false)} /></Suspense>}
      {diagnosticsOpen && <Suspense fallback={null}><AdminDiagnostics open onClose={() => setDiagnosticsOpen(false)} /></Suspense>}
      <VoiceOverlay />
    </>
  );

  if (workspace === "ops") {
    return (
      <>
        <Suspense fallback={workspaceFallback}><OpsWorkspace dark={dark} /></Suspense>
        {dialogs}
      </>
    );
  }

  if (workspace === "office") {
    return (
      <>
        <Suspense fallback={workspaceFallback}><OfficeWorkspace dark={dark} /></Suspense>
        {dialogs}
      </>
    );
  }

  return (
    <>
      <AppFrame
        workspace="conversation"
        sidebarLabel="Conversations"
        toggleLabel="Toggle conversations"
        sidebarBody={<PanelErrorBoundary name="sidebar"><ThreadSidebar /></PanelErrorBoundary>}
        header={
          <>
            {currentTitle && <span className="min-w-0 flex-1 truncate px-1 text-sm text-muted-foreground" title={currentTitle}>{currentTitle}</span>}
            <span className="flex-1" />
            <ShareButton threadId={selectedThreadId} className="max-md:hidden" />
            <Button
              variant="ghost"
              size="icon"
              aria-label="Toggle conversation details"
              aria-expanded={detailsOpen}
              onClick={toggleDetails}
              title="Inspector (causal, debug, memory)"
            ><PanelRightIcon /></Button>
          </>
        }
      >
        <main className="flex min-h-0 flex-1 items-stretch bg-background">
          <div className="relative flex min-h-0 min-w-0 flex-1 flex-col bg-background">
            {error && <Alert variant="destructive" role="alert" className="rounded-none border-x-0 border-t-0"><AlertDescription>{error}</AlertDescription></Alert>}
            <PanelErrorBoundary name="transcript">
              <SelericAssistantRuntimeProvider>
                <Transcript />
                <Composer />
                {promptsOpen && <Suspense fallback={null}><PromptRegistryModal open onClose={() => setPromptsOpen(false)} /></Suspense>}
              </SelericAssistantRuntimeProvider>
            </PanelErrorBoundary>
          </div>
          <PanelErrorBoundary name="inspector">
            <ResponsivePanel open={detailsOpen} onOpenChange={(open) => open !== detailsOpen && toggleDetails()} side="right" label="Conversation details">
              <DetailPanel />
            </ResponsivePanel>
          </PanelErrorBoundary>
        </main>
      </AppFrame>
      {dialogs}
    </>
  );
}
