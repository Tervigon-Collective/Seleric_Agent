import { useEffect, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup,
  DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "cn";
import { useConversationStore } from "../stores/conversation";
import { useShellStore, type Workspace } from "../stores/shell";
import { useThemeStore, type ThemeMode } from "../stores/theme";
import { ActivityIcon, BellRingIcon, MenuIcon, PlusIcon, PromptsIcon, SearchIcon, SettingsIcon, SlidersIcon, XIcon } from "./icons";
import { PANEL_BREAKPOINT, ResponsivePanel } from "./ResponsivePanel";

/** Global actions live behind window events so any workspace can open them without prop drilling. */
export const APP_EVENTS = { search: "seleric:open-search", prompts: "seleric:open-prompts", diagnostics: "seleric:open-diagnostics" } as const;
const fire = (name: string) => window.dispatchEvent(new Event(name));

const THEME_LABEL: Record<ThemeMode, string> = { light: "Light", system: "System", dark: "Dark" };

function NavItem({ icon, label, current, onClick, trailing, tone = "blue" }: {
  icon: ReactNode; tone?: "blue" | "pink"; label: string; current?: boolean; onClick: () => void; trailing?: ReactNode;
}) {
  return (
    <Button
      variant="ghost"
      onClick={onClick}
      aria-current={current ? "page" : undefined}
      className={cn("h-10 w-full justify-start gap-2.5 rounded-xl px-2 text-[13.5px] font-normal text-foreground/90", current && "font-medium text-foreground")}
    >
      <span className={cn("nav-chip", tone === "pink" ? "nav-chip-pink" : "nav-chip-blue")}>{icon}</span>
      <span className="truncate">{label}</span>
      {trailing && <span className="ml-auto text-xs text-muted-foreground">{trailing}</span>}
    </Button>
  );
}

function OfficeIcon() {
  return <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M4 10h16M10 10v10" /></svg>;
}
function ChatIcon() {
  return <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" /></svg>;
}

function SettingsMenu() {
  const mode = useThemeStore((s) => s.mode);
  const setMode = useThemeStore((s) => s.setMode);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="h-10 w-full justify-start gap-2.5 rounded-xl px-2 text-[13.5px] font-normal" aria-label="Settings">
          <span className="nav-chip nav-chip-pink"><SettingsIcon /></span><span>Settings</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-56">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={mode} onValueChange={(v) => setMode(v as ThemeMode)} aria-label="Theme">
          {(Object.keys(THEME_LABEL) as ThemeMode[]).map((m) => <DropdownMenuRadioItem key={m} value={m}>{THEME_LABEL[m]}</DropdownMenuRadioItem>)}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => fire(APP_EVENTS.prompts)}><PromptsIcon />Prompts</DropdownMenuItem>
        <DropdownMenuItem onSelect={() => fire(APP_EVENTS.diagnostics)} aria-label="Open admin diagnostics"><SlidersIcon />Diagnostics</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * The one app shell: a left sidebar (workspace navigation, a workspace-specific body, settings) and a slim top bar.
 * Each workspace passes its own sidebar body and header controls; navigation always goes through the shell store.
 */
export function AppFrame({ workspace, sidebarLabel, toggleLabel, sidebarBody, header, children, className }: {
  workspace: Workspace;
  /** aria-label of the sidebar landmark. */
  sidebarLabel: string;
  /** aria-label of the top bar's sidebar toggle. */
  toggleLabel: string;
  sidebarBody?: ReactNode;
  header?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const sidebarOpen = useShellStore((s) => s.sidebarOpen);
  const toggleSidebar = useShellStore((s) => s.toggleSidebar);
  const setWorkspace = useShellStore((s) => s.setWorkspace);
  const openOps = useShellStore((s) => s.openOps);
  const opsPage = useShellStore((s) => s.opsPage);
  const createThread = useConversationStore((s) => s.createThread);

  // Gesture: a swipe right from the left edge opens the navigation (touch and pen only; the toggle button does the same).
  useEffect(() => {
    let start: { x: number; y: number } | null = null;
    const down = (e: PointerEvent) => { start = e.pointerType !== "mouse" && e.clientX < 28 ? { x: e.clientX, y: e.clientY } : null; };
    const up = (e: PointerEvent) => {
      if (start && e.clientX - start.x > 70 && Math.abs(e.clientY - start.y) < 60 && !useShellStore.getState().sidebarOpen) useShellStore.getState().toggleSidebar();
      start = null;
    };
    window.addEventListener("pointerdown", down);
    window.addEventListener("pointerup", up);
    return () => { window.removeEventListener("pointerdown", down); window.removeEventListener("pointerup", up); };
  }, []);

  const compactClose = () => { if (window.matchMedia?.(`(max-width: ${PANEL_BREAKPOINT - 1}px)`).matches) toggleSidebar(); };
  const go = (fn: () => void) => () => { fn(); compactClose(); };

  return (
    <div className={cn("flex h-dvh min-h-0 bg-background text-foreground", className)}>
      <ResponsivePanel open={sidebarOpen} onOpenChange={(open) => open !== sidebarOpen && toggleSidebar()} side="left" label={sidebarLabel}>
        <aside aria-label={sidebarLabel} data-slot="app-sidebar" className="flex h-full min-h-0 w-full flex-col gap-1 border-r bg-sidebar px-2 pt-2 pb-2 animate-in fade-in-0 slide-in-from-left-2 duration-200 lg:w-64 lg:shrink-0">
          <div className="flex items-center justify-between px-1 pb-1">
            <Button variant="ghost" className="h-8 gap-2 px-1.5" onClick={go(() => setWorkspace("conversation"))} aria-label="Seleric home">
              <span className="nav-chip nav-chip-blue size-7 text-xs font-semibold">S</span>
              <strong className="text-[15px] font-semibold tracking-tight">Seleric</strong>
            </Button>
            <Button variant="ghost" size="icon-sm" className="lg:hidden" aria-label="Close navigation" onClick={toggleSidebar}><XIcon /></Button>
          </div>
          <NavItem icon={<PlusIcon />} label="New chat" onClick={go(() => { setWorkspace("conversation"); void createThread(); })} />
          <NavItem tone="pink" icon={<SearchIcon />} label="Search" onClick={() => fire(APP_EVENTS.search)} trailing={<Kbd>⌘K</Kbd>} />
          <nav aria-label="Workspace" className="flex flex-col gap-0.5 pt-2">
            <NavItem icon={<ChatIcon />} label="Chat" current={workspace === "conversation"} onClick={go(() => setWorkspace("conversation"))} />
            <NavItem tone="pink" icon={<ActivityIcon />} label="Ops" current={workspace === "ops" && opsPage === "dashboard"} onClick={go(() => openOps("dashboard"))} />
            <NavItem icon={<BellRingIcon />} label="Monitoring" current={workspace === "ops" && opsPage === "monitoring"} onClick={go(() => openOps("monitoring"))} />
            <NavItem tone="pink" icon={<OfficeIcon />} label="Office" current={workspace === "office"} onClick={go(() => setWorkspace("office"))} />
          </nav>
          <div className="mt-2 flex min-h-0 flex-1 flex-col">{sidebarBody}</div>
          <div className="border-t pt-2"><SettingsMenu /></div>
        </aside>
      </ResponsivePanel>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="z-30 flex h-12 shrink-0 items-center gap-1.5 overflow-x-clip px-3 max-md:px-2">
          <Button variant="ghost" size="icon" aria-label={toggleLabel} aria-expanded={sidebarOpen} onClick={toggleSidebar} title={toggleLabel}><MenuIcon /></Button>
          {header}
        </header>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
      </div>
    </div>
  );
}
