import { useEffect, useRef, useState } from "react";
import { Composer } from "./components/Composer";
import { DetailPanel } from "./components/DetailPanel";
import { PanelErrorBoundary } from "./components/ErrorBoundary";
import { OfficeWorkspace } from "./components/OfficeWorkspace";
import { ThreadSidebar } from "./components/ThreadSidebar";
import { Transcript } from "./components/Transcript";
import { VoiceOverlay } from "./components/VoiceOverlay";
import { CommandSearch } from "./components/CommandSearch";
import { AdminDiagnostics } from "./components/AdminDiagnostics";
import { PromptRegistryModal } from "./components/PromptRegistryModal";
import {
  CheckIcon, LinkIcon, MenuIcon, MoonIcon, PanelRightIcon, PromptsIcon, SearchIcon, SlidersIcon, SunIcon,
} from "./components/icons";
import { SelericAssistantRuntimeProvider } from "./providers/SelericAssistantRuntime";
import { useConversationStore } from "./stores/conversation";
import { useShellStore } from "./stores/shell";
import { readRoute, writeRoute } from "./routing";

/** Copy-link sharing: the URL already encodes the thread, so the link reopens it. */
function ShareButton({ threadId }: { threadId: string | null }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);
  if (!threadId) return null;
  return (
    <button
      className="search-trigger"
      aria-label={copied ? "Link copied" : "Copy link to this conversation"}
      title="Copy link to this conversation"
      onClick={() => {
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
      }}
    >
      {copied ? <CheckIcon size={14} /> : <LinkIcon size={14} />}
      <span className="label">{copied ? "Copied" : "Share"}</span>
    </button>
  );
}

export default function App() {
  const [searchOpen, setSearchOpen] = useState(false);
  const [promptsOpen, setPromptsOpen] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [routingReady, setRoutingReady] = useState(false);
  const [dark, setDark] = useState(() => {
    const saved = localStorage.getItem("seleric.theme");
    return saved ? saved === "dark" : false;
  });
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
    localStorage.setItem("seleric.theme", dark ? "dark" : "light");
  }, [dark]);
  const workspace = useShellStore((s) => s.workspace);
  // Union-typed alias: `workspace` narrows to "conversation" past the office
  // early-return below, which would make the Office tab comparison a type error.
  const workspaceTab: typeof workspace = workspace;
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
  useEffect(() => {
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
    if (routingReady) writeRoute({ workspace, threadId: selectedThreadId });
  }, [routingReady, workspace, selectedThreadId]);
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
      setWorkspace(route.workspace);
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

  if (workspace === "office") {
    return (
      <OfficeWorkspace
        dark={dark}
        onToggleTheme={() => setDark((value) => !value)}
        onOpenConversations={() => setWorkspace("conversation")}
      />
    );
  }

  const closePanels = () => {
    if (sidebarOpen) toggleSidebar();
    else if (detailsOpen) toggleDetails();
  };

  return (
    <div className="app-shell">
      <header className="shell-header">
        <button
          className="icon-btn"
          aria-label="Toggle conversations"
          aria-expanded={sidebarOpen}
          onClick={toggleSidebar}
          title="Toggle conversations"
        ><MenuIcon /></button>
        <button className="brand-button" onClick={() => setWorkspace("conversation")} aria-label="Seleric home"><span className="brand-mark">S</span><strong>Seleric</strong></button>
        <div className="workspace-switch" role="tablist" aria-label="Workspace">
          <button role="tab" aria-selected={workspaceTab === "conversation"} className={workspaceTab === "conversation" ? "active" : ""} onClick={() => setWorkspace("conversation")}>Conversations</button>
          <button role="tab" aria-selected={workspaceTab === "office"} className={workspaceTab === "office" ? "active" : ""} onClick={() => setWorkspace("office")}>Office</button>
        </div>
        {currentTitle && <span className="thread-context-title" title={currentTitle}>{currentTitle}</span>}
        <span className="header-spacer" />
        <button
          className="search-trigger"
          onClick={() => setPromptsOpen(true)}
          aria-label="Open prompt registry"
          title="Prompt registry"
        >
          <PromptsIcon size={14} /><span className="label">Prompts</span>
        </button>
        <button className="search-trigger" onClick={() => setSearchOpen(true)}
          aria-keyshortcuts="Control+K Meta+K" aria-label="Search conversations">
          <SearchIcon size={14} /><span className="label">Search</span><kbd>⌘K</kbd>
        </button>
        <ShareButton threadId={selectedThreadId} />
        <button
          className="icon-btn"
          aria-label="Toggle conversation details"
          aria-expanded={detailsOpen}
          onClick={toggleDetails}
          title="Inspector (activity, context, sources)"
        ><PanelRightIcon /></button>
        <button className="icon-btn" onClick={() => setDiagnosticsOpen(true)}
          aria-label="Open admin diagnostics" title="Diagnostics"><SlidersIcon /></button>
        <button className="icon-btn" onClick={() => setDark((value) => !value)} aria-label={`Use ${dark ? "light" : "dark"} theme`} title="Theme">
          {dark ? <SunIcon /> : <MoonIcon />}
        </button>
      </header>
      <main className="conversation-layout">
        {(sidebarOpen || detailsOpen) && <button className="mobile-panel-backdrop" aria-label="Close open panel" onClick={closePanels} tabIndex={-1} />}
        <PanelErrorBoundary name="sidebar">
          {sidebarOpen && <ThreadSidebar />}
        </PanelErrorBoundary>
        <div className="conversation-main">
          {error && <div className="error-banner" role="alert">{error}</div>}
          <PanelErrorBoundary name="transcript">
            <SelericAssistantRuntimeProvider>
              <Transcript />
              <Composer />
              <PromptRegistryModal open={promptsOpen} onClose={() => setPromptsOpen(false)} />
            </SelericAssistantRuntimeProvider>
          </PanelErrorBoundary>
        </div>
        <PanelErrorBoundary name="inspector">
          {detailsOpen && <DetailPanel />}
        </PanelErrorBoundary>
      </main>
      <CommandSearch open={searchOpen} onClose={() => setSearchOpen(false)} />
      <VoiceOverlay />
      <AdminDiagnostics open={diagnosticsOpen} onClose={() => setDiagnosticsOpen(false)} />
    </div>
  );
}
