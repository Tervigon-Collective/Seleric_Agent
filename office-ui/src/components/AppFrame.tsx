import { useEffect, type ReactNode } from "react";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup,
  DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Kbd } from "@/components/ui/kbd";
import { cn } from "cn";
import { useConversationStore } from "../stores/conversation";
import { useMonitoringStore } from "../stores/monitoring";
import { useShellStore, type Workspace } from "../stores/shell";
import { useThemeStore, type ThemeMode } from "../stores/theme";
import { ActivityIcon, MenuIcon, PlusIcon, PromptsIcon, SearchIcon, SettingsIcon, SlidersIcon, XIcon } from "./icons";
import { PANEL_BREAKPOINT, ResponsivePanel } from "./ResponsivePanel";

/** Global actions live behind window events so any workspace can open them without prop drilling. */
export const APP_EVENTS = { search: "seleric:open-search", prompts: "seleric:open-prompts", diagnostics: "seleric:open-diagnostics" } as const;
const fire = (name: string) => window.dispatchEvent(new Event(name));

const THEME_LABEL: Record<ThemeMode, string> = { light: "Light", system: "System", dark: "Dark" };

/** Labels and trailing bits hide in the collapsed desktop rail (group-data-[collapsed=true]/sb at lg and up). */
const RAIL_HIDE = "lg:group-data-[collapsed=true]/sb:hidden";

function NavItem({ icon, label, current, onClick, trailing, badge }: {
  icon: ReactNode; label: string; current?: boolean; onClick: () => void; trailing?: ReactNode; badge?: ReactNode;
}) {
  return (
    <Button
      variant="ghost"
      onClick={onClick}
      aria-current={current ? "page" : undefined}
      title={label}
      className={cn("relative h-9 w-full justify-start gap-2.5 rounded-lg px-2.5 text-sm font-normal text-foreground transition-[background-color,transform] duration-150 active:scale-[0.985] lg:group-data-[collapsed=true]/sb:justify-center lg:group-data-[collapsed=true]/sb:px-0", current && "bg-[var(--active)] hover:bg-[var(--active)]")}
    >
      <span className="text-muted-foreground">{icon}</span>
      <span className={cn("truncate", RAIL_HIDE)}>{label}</span>
      {trailing && <span className={cn("ml-auto text-xs text-muted-foreground", RAIL_HIDE)}>{trailing}</span>}
      {badge}
    </Button>
  );
}

function SidebarToggleIcon({ collapsed }: { collapsed: boolean }) {
  return <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={cn("transition-transform duration-200", collapsed && "-scale-x-100")}><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><path d="M9 4.5v15" /></svg>;
}

function OfficeIcon() {
  return <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M4 10h16M10 10v10" /></svg>;
}

function SettingsMenu() {
  const mode = useThemeStore((s) => s.mode);
  const setMode = useThemeStore((s) => s.setMode);
  return (
    <DropdownMenu>
      {/* No asChild: the Button part is not a forwardRef component under React 18, which would drop the menu's anchor and leave it off-screen. */}
      <DropdownMenuTrigger aria-label="Settings" className={cn(buttonVariants({ variant: "ghost" }), "h-auto w-full justify-start gap-3 rounded-lg px-2.5 py-2 text-left font-normal lg:group-data-[collapsed=true]/sb:justify-center lg:group-data-[collapsed=true]/sb:px-0")}>
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-[var(--brand-soft)] text-[var(--brand-text)]"><SettingsIcon /></span>
        <span className={cn("flex min-w-0 flex-col leading-tight", RAIL_HIDE)}><span className="text-sm font-medium">Settings</span><span className="text-xs text-muted-foreground">Theme, prompts and tools</span></span>
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
  const collapsed = useShellStore((s) => s.sidebarCollapsed);
  const toggleCollapsed = useShellStore((s) => s.toggleSidebarCollapsed);
  const setWorkspace = useShellStore((s) => s.setWorkspace);
  const openOps = useShellStore((s) => s.openOps);
  const selectedThreadId = useConversationStore((s) => s.selectedThreadId);
  const createThread = useConversationStore((s) => s.createThread);
  // The nav shows how many metrics need attention, so Monitoring is loaded once here; its own page keeps it fresh.
  const alerts = useMonitoringStore((s) => s.state?.domains.reduce((n, d) => n + (d.counts.alert ?? 0), 0) ?? 0);
  useEffect(() => { if (!useMonitoringStore.getState().state) void useMonitoringStore.getState().load(); }, []);

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

  // Ctrl/Cmd + \ expands or collapses the sidebar, like the toggle next to the logo.
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key === "\\") { e.preventDefault(); useShellStore.getState().toggleSidebarCollapsed(); } };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  const compactClose = () => { if (window.matchMedia?.(`(max-width: ${PANEL_BREAKPOINT - 1}px)`).matches) toggleSidebar(); };
  const go = (fn: () => void) => () => { fn(); compactClose(); };

  return (
    <div className={cn("flex h-dvh min-h-0 bg-background text-foreground", className)}>
      <ResponsivePanel open={sidebarOpen} onOpenChange={(open) => open !== sidebarOpen && toggleSidebar()} side="left" label={sidebarLabel}>
        <aside aria-label={sidebarLabel} id="app-sidebar" data-slot="app-sidebar" data-collapsed={collapsed} className="group/sb flex h-full min-h-0 w-full flex-col gap-1 border-r bg-sidebar px-2 pt-2 pb-2 animate-in fade-in-0 slide-in-from-left-2 duration-200 lg:w-[272px] lg:shrink-0 lg:transition-[width] lg:duration-200 lg:ease-out lg:data-[collapsed=true]:w-16">
          <div className="flex items-center justify-between px-1 pb-1 lg:group-data-[collapsed=true]/sb:flex-col lg:group-data-[collapsed=true]/sb:gap-1.5">
            <Button variant="ghost" className="h-8 gap-2 px-1.5" onClick={go(() => setWorkspace("conversation"))} aria-label="Seleric home">
              <span className="grid size-6 place-items-center rounded-[7px] bg-foreground text-[13px] font-semibold text-background">S</span>
              <strong className={cn("font-serif text-xl font-medium tracking-tight", RAIL_HIDE)}>Seleric</strong>
            </Button>
            <Button variant="ghost" size="icon-sm" className="hidden lg:inline-flex" aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed} aria-controls="app-sidebar" title={`${collapsed ? "Expand" : "Collapse"} sidebar (Ctrl+\\)`} onClick={toggleCollapsed}><SidebarToggleIcon collapsed={collapsed} /></Button>
            <Button variant="ghost" size="icon-sm" className="lg:hidden" aria-label="Close navigation" onClick={toggleSidebar}><XIcon /></Button>
          </div>
          <NavItem icon={<PlusIcon />} label="New chat" current={workspace === "conversation" && !selectedThreadId} onClick={go(() => { setWorkspace("conversation"); void createThread(); })} />
          <NavItem icon={<SearchIcon />} label="Search" onClick={() => fire(APP_EVENTS.search)} trailing={<Kbd>⌘K</Kbd>} />
          <nav aria-label="Workspace" className="flex flex-col gap-0.5">
            <NavItem icon={<ActivityIcon />} label="Monitoring" current={workspace === "ops"} onClick={go(() => openOps("dashboard"))} trailing={alerts > 0 ? <span className="font-medium text-destructive tabular-nums" aria-label={`${alerts} need attention`}>{alerts}</span> : undefined} badge={alerts > 0 ? <span aria-hidden="true" className="absolute top-0.5 right-1 hidden min-w-4 rounded-full bg-[var(--danger-soft)] px-1 text-center text-[10px] leading-4 font-semibold text-destructive lg:group-data-[collapsed=true]/sb:block">{alerts}</span> : undefined} />
            <NavItem icon={<OfficeIcon />} label="Office" current={workspace === "office"} onClick={go(() => setWorkspace("office"))} />
          </nav>
          <div className={cn("mt-2 flex min-h-0 flex-1 flex-col", RAIL_HIDE)}>{sidebarBody}</div>
          <div className="hidden flex-1 lg:group-data-[collapsed=true]/sb:block" aria-hidden="true" />
          <div className="border-t pt-2"><SettingsMenu /></div>
        </aside>
      </ResponsivePanel>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="z-30 flex h-12 shrink-0 items-center gap-1.5 overflow-x-clip px-3 max-md:px-2">
          {/* Hidden on desktop while the sidebar is open (its own toggle collapses it); it shows below desktop width, and whenever the sidebar has been closed, so it can always be reopened. */}
          <Button variant="ghost" size="icon" className={cn(sidebarOpen && "lg:hidden")} aria-label={toggleLabel} aria-expanded={sidebarOpen} onClick={toggleSidebar} title={toggleLabel}><MenuIcon /></Button>
          {header}
        </header>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">{children}</div>
      </div>
    </div>
  );
}
