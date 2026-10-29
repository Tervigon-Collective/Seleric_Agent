import React from "react";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";

/**
 * Isolation boundaries: with no boundary, a single malformed event payload
 * (activity stream, error path, refresh rehydrate) unmounts the entire React
 * root — every icon and panel disappears. These keep a crashing panel
 * contained so the rest of the app stays usable.
 */
export class PanelErrorBoundary extends React.Component<
  { name: string; children: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: unknown): void {
    console.error(`[seleric] ${this.props.name} panel crashed and was contained`, error);
  }

  render(): React.ReactNode {
    if (this.state.failed) {
      return (
        <Alert data-slot="panel-crash" variant="destructive" role="alert" className="m-3 w-auto">
          <AlertDescription className="flex flex-col items-start gap-2">
            <p className="m-0">This panel hit a problem and was contained — the rest of Seleric still works.</p>
            <Button type="button" variant="outline" size="sm" onClick={() => window.location.reload()}>
              Reload app
            </Button>
          </AlertDescription>
        </Alert>
      );
    }
    return this.props.children;
  }
}

export class AppErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: unknown): void {
    console.error("[seleric] app shell crashed", error);
  }

  render(): React.ReactNode {
    if (this.state.failed) {
      return (
        <Empty data-slot="app-crash" role="alert" className="h-screen">
          <EmptyHeader>
            <EmptyMedia variant="icon">S</EmptyMedia>
            <EmptyTitle>Something went wrong</EmptyTitle>
            <EmptyDescription>Seleric hit an unexpected problem. Reloading usually clears it.</EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button type="button" onClick={() => window.location.reload()}>Reload Seleric</Button>
          </EmptyContent>
        </Empty>
      );
    }
    return this.props.children;
  }
}
