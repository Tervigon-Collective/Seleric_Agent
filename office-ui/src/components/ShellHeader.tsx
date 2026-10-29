import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "cn";
import type { Workspace } from "../stores/shell";
import { WorkspaceSwitch } from "./ops/WorkspaceSwitch";
import { ThemeSwitch } from "./ThemeSwitch";

/** The one app bar: same height, brand and workspace switch in every workspace. Each workspace fills `leading` and the middle. */
export function ShellHeader({ workspace, onSwitch, dark, onToggleTheme, leading, className, children }: {
  workspace: Workspace;
  onSwitch: (w: Workspace) => void;
  dark: boolean;
  onToggleTheme: () => void;
  leading?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <header className={cn("z-30 flex h-[52px] shrink-0 items-center gap-2 overflow-x-clip border-b bg-background px-3 max-md:px-2", className)}>
      <span className="inline-flex min-w-8 shrink-0">{leading}</span>
      <Button variant="ghost" size="sm" className="max-sm:hidden" onClick={() => onSwitch("conversation")} aria-label="Seleric home">
        <span className="grid size-6 place-items-center rounded-md bg-foreground text-xs font-bold text-background">S</span>
        <strong className="max-md:hidden">Seleric</strong>
      </Button>
      <WorkspaceSwitch current={workspace} onChange={onSwitch} />
      {children}
      <ThemeSwitch dark={dark} onToggle={onToggleTheme} />
    </header>
  );
}
