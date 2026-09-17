import type { Workspace } from "./stores/shell";

export interface AppRoute {
  workspace: Workspace;
  threadId: string | null;
  missionId: string | null;
}

export function readRoute(url = new URL(location.href)): AppRoute {
  return {
    workspace: url.searchParams.get("office") === "1" ? "office" : "conversation",
    threadId: url.searchParams.get("thread"),
    missionId: url.searchParams.get("mission"),
  };
}

export function writeRoute(route: Partial<AppRoute>, replace = false): void {
  const url = new URL(location.href);
  if (route.workspace) {
    if (route.workspace === "office") url.searchParams.set("office", "1");
    else url.searchParams.delete("office");
  }
  if ("threadId" in route) {
    if (route.threadId) url.searchParams.set("thread", route.threadId);
    else url.searchParams.delete("thread");
  }
  if ("missionId" in route) {
    if (route.missionId) url.searchParams.set("mission", route.missionId);
    else url.searchParams.delete("mission");
  }
  const next = `${url.pathname}${url.search}${url.hash}`;
  if (next === `${location.pathname}${location.search}${location.hash}`) return;
  history[replace ? "replaceState" : "pushState"]({}, "", next);
}
