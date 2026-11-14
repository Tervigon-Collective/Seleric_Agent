import type { ReactNode } from "react";
import { Drawer, DrawerContent, DrawerTitle } from "@/components/ui/drawer";
import { useIsMobile } from "@/hooks/use-mobile";

/** Matches the stylesheet's existing ≤1023px cut-over for side panels. */
export const PANEL_BREAKPOINT = 1024;

/** A side panel: inline on wide screens, a Drawer below the breakpoint. */
export function ResponsivePanel({ open, onOpenChange, side, label, children }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  side: "left" | "right";
  label: string;
  children: ReactNode;
}) {
  const compact = useIsMobile(PANEL_BREAKPOINT);
  if (!compact) return open ? <>{children}</> : null;
  return (
    <Drawer open={open} onOpenChange={onOpenChange} direction={side}>
      <DrawerContent aria-describedby={undefined}>
        <DrawerTitle className="sr-only">{label}</DrawerTitle>
        {children}
      </DrawerContent>
    </Drawer>
  );
}
