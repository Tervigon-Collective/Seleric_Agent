import { create } from "zustand";

export type DetailTab = "Causal" | "Debug" | "Memory";
export type Workspace = "conversation" | "office" | "ops";
export type OpsPage = "dashboard" | "monitoring";

const DETAIL_TABS: readonly DetailTab[] = ["Debug", "Causal", "Memory"];
const DETAILS_OPEN_KEY = "seleric.detailsOpen";
const DETAIL_TAB_KEY = "seleric.detailTab";
const SIDEBAR_OPEN_KEY = "seleric.sidebarOpen";
const SIDEBAR_COLLAPSED_KEY = "seleric.sidebarCollapsed";

const wide = (px: number) => typeof window !== "undefined" && window.innerWidth >= px;

/** The user's choice is remembered; with none stored, the navigation is open at desktop width and tucked away below it. */
function readStoredSidebarOpen(): boolean {
  try {
    const stored = localStorage.getItem(SIDEBAR_OPEN_KEY);
    return stored === null ? wide(1024) : stored === "1";
  } catch {
    return wide(1024);
  }
}

function readStoredSidebarCollapsed(): boolean {
  try { return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1"; } catch { return false; }
}

function readStoredDetailsOpen(): boolean {
  try {
    return localStorage.getItem(DETAILS_OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

function readStoredDetailTab(): DetailTab {
  try {
    const value = localStorage.getItem(DETAIL_TAB_KEY);
    // Prefer Debug as the landing tab; Causal stays available when a DAG exists.
    if (DETAIL_TABS.includes(value as DetailTab) && value !== "Causal") {
      return value as DetailTab;
    }
    return "Debug";
  } catch {
    return "Debug";
  }
}

function persistDetails(detailsOpen: boolean, detailTab: DetailTab) {
  try {
    localStorage.setItem(DETAILS_OPEN_KEY, detailsOpen ? "1" : "0");
    localStorage.setItem(DETAIL_TAB_KEY, detailTab);
  } catch {
    // Private mode / quota — panel state is best-effort.
  }
}

function initialWorkspace(): Workspace {
  const params = new URLSearchParams(location.search);
  if (params.get("office") === "1") return "office";
  return params.has("ops") ? "ops" : "conversation";
}

function initialOpsPage(): OpsPage {
  return new URLSearchParams(location.search).get("ops") === "monitoring" ? "monitoring" : "dashboard";
}

interface ShellState {
  detailTab: DetailTab;
  workspace: Workspace;
  opsPage: OpsPage;
  sidebarOpen: boolean;
  /** Desktop only: the sidebar shrinks to an icon rail. Below the panel breakpoint it stays a drawer. */
  sidebarCollapsed: boolean;
  detailsOpen: boolean;
  /** Monitoring's chat overlay: whether it is over the page, and what it is investigating. */
  askOpen: boolean;
  askContext: string | null;
  selectedArtifact: Record<string, unknown> | null;
  setDetailTab: (tab: DetailTab) => void;
  setWorkspace: (workspace: Workspace) => void;
  /** Open the Ops workspace on one of its pages (dashboard, or the monitoring configuration). */
  openOps: (page: OpsPage) => void;
  toggleSidebar: () => void;
  toggleSidebarCollapsed: () => void;
  setAskOpen: (open: boolean) => void;
  /** Open the chat overlay over Monitoring and say what it is looking into. */
  askAbout: (context: string) => void;
  toggleDetails: () => void;
  /** Open the inspector at a specific tab (evidence on demand). */
  openInspector: (tab: DetailTab) => void;
  showArtifact: (artifact: Record<string, unknown>) => void;
}

export const useShellStore = create<ShellState>((set, get) => ({
  detailTab: readStoredDetailTab(),
  workspace: initialWorkspace(),
  opsPage: initialOpsPage(),
  // Navigation stays tucked until asked for; both panels restore the last choice.
  sidebarOpen: readStoredSidebarOpen(),
  sidebarCollapsed: readStoredSidebarCollapsed(),
  detailsOpen: readStoredDetailsOpen(),
  askOpen: false,
  askContext: null,
  selectedArtifact: null,
  setDetailTab: (detailTab) => {
    set({ detailTab });
    persistDetails(get().detailsOpen, detailTab);
  },
  setWorkspace: (workspace) => set({ workspace }),
  openOps: (opsPage) => set({ workspace: "ops", opsPage }),
  toggleSidebarCollapsed: () => {
    const sidebarCollapsed = !get().sidebarCollapsed;
    set({ sidebarCollapsed });
    try { localStorage.setItem(SIDEBAR_COLLAPSED_KEY, sidebarCollapsed ? "1" : "0"); } catch { /* best effort */ }
  },
  setAskOpen: (askOpen) => set({ askOpen }),
  askAbout: (askContext) => set({ askOpen: true, askContext }),
  toggleSidebar: () => {
    const sidebarOpen = !get().sidebarOpen;
    set({ sidebarOpen });
    try { localStorage.setItem(SIDEBAR_OPEN_KEY, sidebarOpen ? "1" : "0"); } catch { /* best effort */ }
  },
  toggleDetails: () => {
    const detailsOpen = !get().detailsOpen;
    set({ detailsOpen });
    persistDetails(detailsOpen, get().detailTab);
  },
  openInspector: (detailTab) => {
    set({ detailTab, detailsOpen: true });
    persistDetails(true, detailTab);
  },
  showArtifact: (selectedArtifact) => {
    // Artifacts tab removed — open Debug with the selection retained for any
    // future inspector surface that still reads selectedArtifact.
    set({
      selectedArtifact,
      detailTab: "Debug",
      detailsOpen: true,
    });
    persistDetails(true, "Debug");
  },
}));
