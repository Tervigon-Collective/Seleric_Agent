import { useEffect } from "react";
import { MoonIcon, SunIcon } from "../icons";
import { useMonitoringStore } from "../../stores/monitoring";
import { useShellStore, type Workspace } from "../../stores/shell";
import { MonitoringConfig } from "./MonitoringConfig";
import { OpsDashboard } from "./OpsDashboard";
import { WorkspaceSwitch } from "./WorkspaceSwitch";

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

  const jump = (prefix: string, id: string) => document.getElementById(`${prefix}-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  const prefix = page === "dashboard" ? "ops" : "mon";

  return (
    <div className="app-shell ops-shell">
      <header className="shell-header">
        <button className="brand-button" onClick={() => onSwitch("conversation")} aria-label="Seleric home"><span className="brand-mark">S</span><strong>Seleric</strong></button>
        <WorkspaceSwitch current="ops" onChange={onSwitch} />
        <span className="header-spacer" />
        <button className="icon-btn" onClick={onToggleTheme} aria-label={`Use ${dark ? "light" : "dark"} theme`} title="Theme">
          {dark ? <SunIcon /> : <MoonIcon />}
        </button>
      </header>
      <div className="ops-body">
        <nav className="ops-side" aria-label="Ops">
          <div className="ops-pages" role="group" aria-label="Page">
            <button className={page === "dashboard" ? "active" : ""} aria-current={page === "dashboard" ? "page" : undefined} onClick={() => openOps("dashboard")}>Dashboard</button>
            <button className={page === "monitoring" ? "active" : ""} aria-current={page === "monitoring" ? "page" : undefined} onClick={() => openOps("monitoring")}>Monitoring</button>
          </div>
          <div className="ops-side-label">Domains</div>
          <div className="ops-domains">
            {state?.domains.map((d) => {
              const alerts = d.counts.alert ?? 0;
              return (
                <button key={d.id} className="ops-jump" onClick={() => jump(prefix, d.id)}>
                  <span>{d.title}</span>
                  {alerts > 0 && <span className="ops-badge" aria-label={`${alerts} need attention`}>{alerts}</span>}
                </button>
              );
            })}
          </div>
        </nav>
        <main className="ops-main">
          <h2 className="ops-title">{page === "dashboard" ? "Ops dashboard" : "Monitoring"}</h2>
          {page === "dashboard" ? <OpsDashboard /> : <MonitoringConfig />}
        </main>
      </div>
    </div>
  );
}
