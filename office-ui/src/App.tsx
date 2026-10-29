import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button, buttonVariants } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "cn";
import { Composer } from "./components/Composer";
import { DetailPanel } from "./components/DetailPanel";
import { PanelErrorBoundary } from "./components/ErrorBoundary";
import { ResponsivePanel } from "./components/ResponsivePanel";
import { ShellHeader } from "./components/ShellHeader";
import { ThreadSidebar } from "./components/ThreadSidebar";
import { Transcript } from "./components/Transcript";
import { VoiceOverlay } from "./components/VoiceOverlay";
import { useWakeWord } from "./wakeWord";
import { Skeleton } from "@/components/ui/skeleton";
import {
  CheckIcon, LinkIcon, MenuIcon, MoreIcon, PanelRightIcon, PromptsIcon, SearchIcon, SlidersIcon,
} from "./components/icons";
import { SelericAssistantRuntimeProvider } from "./providers/SelericAssistantRuntime";
import { useConversationStore } from "./stores/conversation";
import { useShellStore } from "./stores/shell";
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

/** Narrow screens fold the secondary header actions into one menu. */
function HeaderMenu({ threadId, onPrompts, onDiagnostics }: { threadId: string | null; onPrompts: () => void; onDiagnostics: () => void }) {
  const { copied, copy } = useCopyLink();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className={cn(buttonVariants({ variant: "ghost", size: "icon" }), "md:hidden")} aria-label="More actions">
        <MoreIcon />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onSelect={onPrompts}><PromptsIcon />Prompts</DropdownMenuItem>
        {threadId && <DropdownMenuItem onSelect={copy}>{copied ? <CheckIcon /> : <LinkIcon />}{copied ? "Copied" : "Share"}</DropdownMenuItem>}
        <DropdownMenuItem onSelect={onDiagnostics}><SlidersIcon />Diagnostics</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export default function App() {
  useWakeWord();
  const [searchOpen, setSearchOpen] = useState(false);
  const [promptsOpen, setPromptsOpen] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [routingReady, setRoutingReady] = useState(false);
  const [dark, setDark] = useState(() => {
    const saved = localStorage.getItem("seleric.theme");
    // HIG: respect the system appearance when the user has no saved choice.
    if (saved) return saved === "dark";
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  });
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
    localStorage.setItem("seleric.theme", dark ? "dark" : "light");
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      "content",
      dark ? "#1b1b1e" : "#ffffff",
    );
  }, [dark]);
  const workspace = useShellStore((s) => s.workspace);
  // Union-typed alias: `workspace` narrows to "conversation" past the office
  // early-return below, which would make the Office tab comparison a type error.
  const workspaceTab: typeof workspace = workspace;
  const opsPage = useShellStore((s) => s.opsPage);
  const setWorkspace = useShellStore((s) => s.setWorkspace);
  const sidebarOpen = useShellStore((s) => s.sidebarOpen);
  const detailsOpen = useShellStore((s) => s.detailsOpen);
  const toggleSidebar = useShellStore((s) => s.toggleSidebar);
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
    const openPrompts = () => setPromptsOpen(true);
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
    window.addEventListener("seleric:open-prompts", openPrompts);
    window.addEventListener("popstate", popstate);
    return () => {
      window.removeEventListener("keydown", keyboard);
      window.removeEventListener("seleric:search-result", result);
      window.removeEventListener("seleric:open-prompts", openPrompts);
      window.removeEventListener("popstate", popstate);
    };
  }, [clearSelection, selectThread, setWorkspace, searchOpen, promptsOpen, diagnosticsOpen]);

  if (workspace === "ops") {
    return (
      <Suspense fallback={workspaceFallback}>
        <OpsWorkspace dark={dark} onToggleTheme={() => setDark((value) => !value)} onSwitch={setWorkspace} />
      </Suspense>
    );
  }

  if (workspace === "office") {
    return (
      <Suspense fallback={workspaceFallback}>
        <OfficeWorkspace
          dark={dark}
          onToggleTheme={() => setDark((value) => !value)}
          onSwitch={setWorkspace}
        />
      </Suspense>
    );
  }

  return (
    <div className="grid h-full grid-cols-[minmax(0,1fr)] grid-rows-[52px_minmax(0,1fr)] bg-background">
      <ShellHeader
        workspace={workspaceTab}
        onSwitch={setWorkspace}
        dark={dark}
        onToggleTheme={() => setDark((value) => !value)}
        leading={
          <Button
            variant="ghost"
            size="icon"
            aria-label="Toggle conversations"
            aria-expanded={sidebarOpen}
            onClick={toggleSidebar}
            title="Toggle conversations"
          ><MenuIcon /></Button>
        }
      >
        {currentTitle && <span className="min-w-0 flex-1 truncate text-center text-sm text-muted-foreground max-lg:hidden" title={currentTitle}>{currentTitle}</span>}
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => setSearchOpen(true)}
          aria-keyshortcuts="Control+K Meta+K" aria-label="Search conversations">
          <SearchIcon /><span className="max-lg:hidden">Search</span><Kbd className="max-lg:hidden">⌘K</Kbd>
        </Button>
        <ShareButton threadId={selectedThreadId} className="max-md:hidden" />
        <Button
          variant="ghost"
          size="icon"
          aria-label="Toggle conversation details"
          aria-expanded={detailsOpen}
          onClick={toggleDetails}
          title="Inspector (causal, debug, memory)"
        ><PanelRightIcon /></Button>
        <Button variant="ghost" size="icon" className="max-md:hidden" onClick={() => setDiagnosticsOpen(true)}
          aria-label="Open admin diagnostics" title="Diagnostics"><SlidersIcon /></Button>
        <HeaderMenu threadId={selectedThreadId} onPrompts={() => setPromptsOpen(true)} onDiagnostics={() => setDiagnosticsOpen(true)} />
      </ShellHeader>
      <main className="flex min-h-0 items-stretch bg-background">
        <PanelErrorBoundary name="sidebar">
          <ResponsivePanel open={sidebarOpen} onOpenChange={(open) => open !== sidebarOpen && toggleSidebar()} side="left" label="Conversations">
            <ThreadSidebar />
          </ResponsivePanel>
        </PanelErrorBoundary>
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
      {searchOpen && <Suspense fallback={null}><CommandSearch open onClose={() => setSearchOpen(false)} /></Suspense>}
      <VoiceOverlay />
      {diagnosticsOpen && <Suspense fallback={null}><AdminDiagnostics open onClose={() => setDiagnosticsOpen(false)} /></Suspense>}
    </div>
  );
}
