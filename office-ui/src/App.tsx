import { useEffect, useState } from "react";
import { Composer } from "./components/Composer";
import { DetailPanel } from "./components/DetailPanel";
import { OfficeWorkspace } from "./components/OfficeWorkspace";
import { ThreadSidebar } from "./components/ThreadSidebar";
import { Transcript } from "./components/Transcript";
import { CommandSearch } from "./components/CommandSearch";
import { AdminDiagnostics } from "./components/AdminDiagnostics";
import { SelericAssistantRuntimeProvider } from "./providers/SelericAssistantRuntime";
import { useConversationStore } from "./stores/conversation";
import { useShellStore } from "./stores/shell";

export default function App() {
  const [searchOpen, setSearchOpen] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
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
  const demoMode = useConversationStore((s) => s.demoMode);
  const setDemoMode = useConversationStore((s) => s.setDemoMode);
  const loadThreads = useConversationStore((s) => s.loadThreads);
  const error = useConversationStore((s) => s.error);
  const selectThread = useConversationStore((s) => s.selectThread);
  useEffect(() => { void loadThreads(); }, [loadThreads, demoMode]);
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
    window.addEventListener("keydown", keyboard);
    window.addEventListener("seleric:search-result", result);
    return () => {
      window.removeEventListener("keydown", keyboard);
      window.removeEventListener("seleric:search-result", result);
    };
  }, [selectThread, setWorkspace]);

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
        <span className="header-spacer" />
        <button className="search-trigger" onClick={() => setSearchOpen(true)}
          aria-keyshortcuts="Control+K Meta+K">Search <kbd>⌘/Ctrl K</kbd></button>
        <button className="icon-btn" onClick={() => setDiagnosticsOpen(true)}
          aria-label="Open admin diagnostics">Diagnostics</button>
        <label className="mode-switch">Mode <select value={demoMode ? "demo" : "live"} onChange={(e) => setDemoMode(e.target.value === "demo")}><option value="demo">Demo</option><option value="live">Live</option></select></label>
        <button className="icon-btn" onClick={() => setDark((value) => !value)} aria-label={`Use ${dark ? "light" : "dark"} theme`}>{dark ? "☀" : "◐"}</button>
      </header>
      <main className={`conversation-layout ${!sidebarOpen ? "sidebar-closed" : ""} ${!detailsOpen ? "details-closed" : ""}`}>
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
      <AdminDiagnostics open={diagnosticsOpen} onClose={() => setDiagnosticsOpen(false)} />
    </div>
  );
}
