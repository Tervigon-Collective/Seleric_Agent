import { useEffect } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AppFrame } from "../AppFrame";
import { PANEL_BREAKPOINT } from "../ResponsivePanel";
import { useMonitoringStore } from "../../stores/monitoring";
import { useShellStore } from "../../stores/shell";
import { MonitoringConfig } from "./MonitoringConfig";
import { OpsDashboard } from "./OpsDashboard";

const POLL_MS = 60_000;

export function OpsWorkspace(_props: { dark?: boolean } = {}) {
  const page = useShellStore((s) => s.opsPage);
  const state = useMonitoringStore((s) => s.state);
  const load = useMonitoringStore((s) => s.load);

  useEffect(() => {
    void load();
    // Re-read while the tab is visible; the server only recomputes on its own schedule, so this is cheap.
    const id = window.setInterval(() => { if (!document.hidden) void load(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  const prefix = page === "dashboard" ? "ops" : "mon";
  const jump = (id: string) => {
    if (window.matchMedia?.(`(max-width: ${PANEL_BREAKPOINT - 1}px)`).matches) useShellStore.getState().toggleSidebar();
    document.getElementById(`${prefix}-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const domains = (
    <nav aria-label="Ops" className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
      <div className="px-2.5 pt-2 pb-1 text-xs font-medium text-muted-foreground">Domains</div>
      {state?.domains.map((d) => {
        const alerts = d.counts.alert ?? 0;
        return (
          <Button key={d.id} variant="ghost" className="h-8 justify-between rounded-lg px-2.5 text-left text-[13.5px] font-normal whitespace-normal" onClick={() => jump(d.id)}>
            <span className="truncate">{d.title}</span>
            {alerts > 0 && <Badge variant="destructive" aria-label={`${alerts} need attention`}>{alerts}</Badge>}
          </Button>
        );
      })}
    </nav>
  );

  return (
    <AppFrame
      workspace="ops"
      sidebarLabel="Ops navigation"
      toggleLabel="Toggle navigation"
      sidebarBody={domains}
      header={<span className="min-w-0 flex-1 truncate px-1 text-sm text-muted-foreground">{page === "dashboard" ? "Ops dashboard" : "Monitoring"}</span>}
    >
      <main className="min-h-0 flex-1 overflow-y-auto scroll-smooth">
        <div className="mx-auto w-full max-w-4xl px-8 pt-4 pb-16 max-md:px-4 animate-in fade-in-0 slide-in-from-bottom-1 duration-200" key={page}>
          <h2 className="m-0 mb-6 text-2xl font-semibold tracking-tight max-md:text-xl">{page === "dashboard" ? "Ops dashboard" : "Monitoring"}</h2>
          {page === "dashboard" ? <OpsDashboard /> : <MonitoringConfig />}
        </div>
      </main>
    </AppFrame>
  );
}
