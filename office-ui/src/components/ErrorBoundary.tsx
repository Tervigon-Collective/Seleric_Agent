import React from "react";

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
        <div className="panel-crash" role="alert">
          <p>This panel hit a problem and was contained — the rest of Seleric still works.</p>
          <button type="button" onClick={() => window.location.reload()}>
            Reload app
          </button>
        </div>
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
        <div className="app-crash" role="alert">
          <span className="brand-mark">S</span>
          <h1>Something went wrong</h1>
          <p>Seleric hit an unexpected problem. Reloading usually clears it.</p>
          <button type="button" onClick={() => window.location.reload()}>
            Reload Seleric
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
