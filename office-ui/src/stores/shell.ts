import { create } from "zustand";

export type DetailTab = "Evidence" | "Definitions" | "Activity" | "Memory" | "Artifacts";
export type Workspace = "conversation" | "office";

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

export const useShellStore = create<ShellState>((set) => ({
  detailTab: "Evidence",
  workspace: new URLSearchParams(location.search).get("office") === "1" ? "office" : "conversation",
  // Sidebar visible on desktop; inspector (Layer C) stays closed until asked.
  sidebarOpen: true,
  detailsOpen: false,
  selectedArtifact: null,
  setDetailTab: (detailTab) => set({ detailTab }),
  setWorkspace: (workspace) => set({ workspace }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  toggleDetails: () => set((state) => ({ detailsOpen: !state.detailsOpen })),
  openInspector: (detailTab) => set({ detailTab, detailsOpen: true }),
  showArtifact: (selectedArtifact) => set({
    selectedArtifact,
    detailTab: "Artifacts",
    detailsOpen: true,
  }),
}));
