import { useEffect, useState } from "react";
import { useIsMobile } from "@/hooks/use-mobile";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MenuIcon } from "../icons";
import { PANEL_BREAKPOINT, ResponsivePanel } from "../ResponsivePanel";
import { ShellHeader } from "../ShellHeader";
import { useMonitoringStore } from "../../stores/monitoring";
import { useShellStore, type Workspace } from "../../stores/shell";
import { MonitoringConfig } from "./MonitoringConfig";
import { OpsDashboard } from "./OpsDashboard";

const POLL_MS = 60_000;

export function OpsWorkspace({ dark, onToggleTheme, onSwitch }: {
  dark: boolean;
  onToggleTheme: () => void;
  onSwitch: (w: Workspace) => void;
}) {
  const page = useShellStore((s) => s.opsPage);
  const openOps = useShellStore((s) => s.openOps);
  const state = useMonitoringStore((s) => s.state);
  const load = useMonitoringStore((s) => s.load);

  useEffect(() => {
    void load();
    // Re-read while the tab is visible; the server only recomputes on its own schedule, so this is cheap.
    const id = window.setInterval(() => { if (!document.hidden) void load(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const compact = useIsMobile(PANEL_BREAKPOINT);
  const [navChoice, setNavChoice] = useState<boolean>();
  const navOpen = navChoice ?? !compact;
  const closeIfCompact = () => { if (compact) setNavChoice(false); };
  const jump = (prefix: string, id: string) => {
    closeIfCompact();
    document.getElementById(`${prefix}-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const open = (next: "dashboard" | "monitoring") => { closeIfCompact(); openOps(next); };
  const prefix = page === "dashboard" ? "ops" : "mon";

  const showRail = navOpen && !compact;
  return (
    <div className="flex h-dvh flex-col bg-background">
      <ShellHeader
        workspace="ops"
        onSwitch={onSwitch}
        dark={dark}
        onToggleTheme={onToggleTheme}
        leading={
          <Button variant="ghost" size="icon" aria-label="Toggle navigation" aria-expanded={navOpen} onClick={() => setNavChoice(!navOpen)} title="Toggle navigation"><MenuIcon /></Button>
        }
      >
        <span className="flex-1" />
      </ShellHeader>
      <div className={`grid min-h-0 flex-1 ${showRail ? "grid-cols-[220px_minmax(0,1fr)]" : "grid-cols-[minmax(0,1fr)]"}`}>
        <ResponsivePanel open={navOpen} onOpenChange={setNavChoice} side="left" label="Ops navigation">
          <nav aria-label="Ops" className="flex h-full min-w-0 flex-col gap-0.5 overflow-y-auto border-r bg-sidebar p-3">
            <div role="group" aria-label="Page" className="flex flex-col gap-0.5">
              <Button variant={page === "dashboard" ? "outline" : "ghost"} className="justify-start" aria-current={page === "dashboard" ? "page" : undefined} onClick={() => open("dashboard")}>Dashboard</Button>
              <Button variant={page === "monitoring" ? "outline" : "ghost"} className="justify-start" aria-current={page === "monitoring" ? "page" : undefined} onClick={() => open("monitoring")}>Monitoring</Button>
            </div>
            <div className="px-2.5 pt-3.5 pb-1 text-xs tracking-wide text-muted-foreground uppercase">Domains</div>
            <div className="flex flex-col gap-0.5">
              {state?.domains.map((d) => {
                const alerts = d.counts.alert ?? 0;
                return (
                  <Button key={d.id} variant="ghost" className="h-auto justify-between py-1.5 text-left whitespace-normal" onClick={() => jump(prefix, d.id)}>
                    <span>{d.title}</span>
                    {alerts > 0 && <Badge variant="destructive" aria-label={`${alerts} need attention`}>{alerts}</Badge>}
                  </Button>
                );
              })}
            </div>
          </nav>
        </ResponsivePanel>
        <main className="min-h-0 overflow-y-auto scroll-smooth px-6 pt-4 pb-12 max-lg:px-4">
          <h2 className="m-0 mb-4 text-xl font-medium max-md:text-lg">{page === "dashboard" ? "Ops dashboard" : "Monitoring"}</h2>
          {page === "dashboard" ? <OpsDashboard /> : <MonitoringConfig />}
        </main>
      </div>
    </div>
  );
}
