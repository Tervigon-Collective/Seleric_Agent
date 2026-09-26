import { useEffect, useState } from "react";
import { Composer } from "./components/Composer";
import { DetailPanel } from "./components/DetailPanel";
import { OfficeWorkspace } from "./components/OfficeWorkspace";
import { ThreadSidebar } from "./components/ThreadSidebar";
import { Transcript } from "./components/Transcript";
import { VoiceOverlay } from "./components/VoiceOverlay";
import { CommandSearch } from "./components/CommandSearch";
import { AdminDiagnostics } from "./components/AdminDiagnostics";
import { SelericAssistantRuntimeProvider } from "./providers/SelericAssistantRuntime";
import { useConversationStore } from "./stores/conversation";
import { useShellStore } from "./stores/shell";
import { readRoute, writeRoute } from "./routing";

export default function App() {
  const [searchOpen, setSearchOpen] = useState(false);
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
  useEffect(() => {
    if (window.matchMedia?.("(max-width: 900px)").matches) {
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
    };
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
    window.addEventListener("popstate", popstate);
    return () => {
      window.removeEventListener("keydown", keyboard);
      window.removeEventListener("seleric:search-result", result);
      window.removeEventListener("popstate", popstate);
    };
  }, [clearSelection, selectThread, setWorkspace]);

  if (workspace === "office") {
    return (
      <OfficeWorkspace
        dark={dark}
        onToggleTheme={() => setDark((value) => !value)}
        onOpenConversations={() => setWorkspace("conversation")}
      />
    );
  }

  return (
    <div className="app-shell">
      <header className="shell-header">
        <button className="brand-button" onClick={() => setWorkspace("conversation")} aria-label="Open conversations"><span className="brand-mark">S</span><strong>Seleric</strong></button>
        <nav aria-label="Workspace">
          <button className={workspace === "conversation" ? "active" : ""} onClick={() => setWorkspace("conversation")}>Conversations</button>
          <button onClick={() => setWorkspace("office")}>Office</button>
        </nav>
        <button
          className="icon-btn mobile-panel-toggle"
          aria-label="Toggle conversations"
          aria-expanded={sidebarOpen}
          onClick={toggleSidebar}
        >☰</button>
        <button
          className="icon-btn mobile-panel-toggle"
          aria-label="Toggle conversation details"
          aria-expanded={detailsOpen}
          onClick={toggleDetails}
        >ⓘ</button>
        <span className="header-spacer" />
        <button className="search-trigger" onClick={() => setSearchOpen(true)}
          aria-keyshortcuts="Control+K Meta+K">Search <kbd>⌘/Ctrl K</kbd></button>
        <button className="icon-btn" onClick={() => setDiagnosticsOpen(true)}
          aria-label="Open admin diagnostics">Diagnostics</button>
        <button className="icon-btn" onClick={() => setDark((value) => !value)} aria-label={`Use ${dark ? "light" : "dark"} theme`}>{dark ? "☀" : "◐"}</button>
      </header>
      <main className={`conversation-layout ${!sidebarOpen ? "sidebar-closed" : ""} ${!detailsOpen ? "details-closed" : ""}`}>
        {(sidebarOpen || detailsOpen) && <button className="mobile-panel-backdrop" aria-label="Close open panel" onClick={sidebarOpen ? toggleSidebar : toggleDetails} />}
        {sidebarOpen && <ThreadSidebar />}
        <div className="conversation-main">
          {error && <div className="error-banner" role="alert">{error}</div>}
          <SelericAssistantRuntimeProvider>
            <Transcript />
            <Composer />
          </SelericAssistantRuntimeProvider>
        </div>
        {detailsOpen && <DetailPanel />}
      </main>
      <CommandSearch open={searchOpen} onClose={() => setSearchOpen(false)} />
      <VoiceOverlay />
      <AdminDiagnostics open={diagnosticsOpen} onClose={() => setDiagnosticsOpen(false)} />
    </div>
  );
}
