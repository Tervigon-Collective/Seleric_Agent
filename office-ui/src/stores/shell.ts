import { create } from "zustand";

export type DetailTab = "Activity" | "Context" | "Memory" | "Sources" | "Artifacts";
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
  showArtifact: (artifact: Record<string, unknown>) => void;
}

export const useShellStore = create<ShellState>((set) => ({
  detailTab: "Activity",
  workspace: new URLSearchParams(location.search).get("office") === "1" ? "office" : "conversation",
  sidebarOpen: true,
  detailsOpen: true,
  selectedArtifact: null,
  setDetailTab: (detailTab) => set({ detailTab }),
  setWorkspace: (workspace) => set({ workspace }),
  toggleSidebar: () => set((state) => ({
    sidebarOpen: !state.sidebarOpen,
    detailsOpen: state.sidebarOpen ? state.detailsOpen : false,
  })),
  toggleDetails: () => set((state) => ({
    detailsOpen: !state.detailsOpen,
    sidebarOpen: state.detailsOpen ? state.sidebarOpen : false,
  })),
  showArtifact: (selectedArtifact) => set({
    selectedArtifact,
    detailTab: "Artifacts",
    detailsOpen: true,
  }),
}));
