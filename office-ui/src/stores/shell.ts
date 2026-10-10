import { create } from "zustand";

export type DetailTab = "Evidence" | "Definitions" | "Activity" | "Memory" | "Artifacts";
export type Workspace = "conversation" | "office";

const DETAIL_TABS: readonly DetailTab[] = [
  "Evidence", "Definitions", "Activity", "Memory", "Artifacts",
];
const DETAILS_OPEN_KEY = "seleric.detailsOpen";
const DETAIL_TAB_KEY = "seleric.detailTab";

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
    return DETAIL_TABS.includes(value as DetailTab) ? (value as DetailTab) : "Evidence";
  } catch {
    return "Evidence";
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

interface ShellState {
  detailTab: DetailTab;
  workspace: Workspace;
  sidebarOpen: boolean;
  detailsOpen: boolean;
  selectedArtifact: Record<string, unknown> | null;
  setDetailTab: (tab: DetailTab) => void;
  setWorkspace: (workspace: Workspace) => void;
  toggleSidebar: () => void;
  toggleDetails: () => void;
  /** Open the inspector at a specific tab (evidence on demand). */
  openInspector: (tab: DetailTab) => void;
  showArtifact: (artifact: Record<string, unknown>) => void;
}

export const useShellStore = create<ShellState>((set, get) => ({
  detailTab: readStoredDetailTab(),
  workspace: new URLSearchParams(location.search).get("office") === "1" ? "office" : "conversation",
  // Sidebar visible on desktop; inspector restores the last open/closed choice.
  sidebarOpen: true,
  detailsOpen: readStoredDetailsOpen(),
  selectedArtifact: null,
  setDetailTab: (detailTab) => {
    set({ detailTab });
    persistDetails(get().detailsOpen, detailTab);
  },
  setWorkspace: (workspace) => set({ workspace }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
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
    set({
      selectedArtifact,
      detailTab: "Artifacts",
      detailsOpen: true,
    });
    persistDetails(true, "Artifacts");
  },
}));
