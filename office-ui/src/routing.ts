import type { OpsPage, Workspace } from "./stores/shell";

export interface AppRoute {
  workspace: Workspace;
  threadId: string | null;
  missionId: string | null;
  opsPage: OpsPage;
}

const OPS_PAGES = new Set(["dashboard", "monitoring", "notifications", "live"]);

export function readRoute(url = new URL(location.href)): AppRoute {
  const ops = url.searchParams.get("ops");
  // Legacy deep link: ?notifications=1 → Monitoring / Notifications tab.
  const legacyNotify = url.searchParams.get("notifications") === "1";
  return {
    workspace: legacyNotify || url.searchParams.has("ops")
      ? "ops"
      : url.searchParams.get("office") === "1" ? "office" : url.searchParams.has("bots") ? "bots" : "conversation",
    threadId: url.searchParams.get("thread"),
    missionId: url.searchParams.get("mission"),
    opsPage: legacyNotify ? "notifications"
      : ops && OPS_PAGES.has(ops) ? ops as AppRoute["opsPage"] : "dashboard",
  };
}

export function writeRoute(route: Partial<AppRoute>, replace = false): void {
  const url = new URL(location.href);
  if (route.workspace) {
    url.searchParams.delete("notifications"); // legacy only for inbound deep links
    if (route.workspace === "office") url.searchParams.set("office", "1");
    else url.searchParams.delete("office");
    if (route.workspace === "ops") url.searchParams.set("ops", route.opsPage ?? "dashboard");
    else url.searchParams.delete("ops");
    if (route.workspace === "bots") url.searchParams.set("bots", "1");
    else url.searchParams.delete("bots");
  }
  if ("threadId" in route) {
    if (route.threadId) url.searchParams.set("thread", route.threadId);
    else url.searchParams.delete("thread");
  }
  if ("missionId" in route) {
    if (route.missionId) url.searchParams.set("mission", route.missionId);
    else url.searchParams.delete("mission");
  }
  if (!url.searchParams.has("ops")) url.hash = ""; // #ops-<domain>-<metric> anchors belong to the Ops page; a chat thread carries none
  const next = `${url.pathname}${url.search}${url.hash}`;
  if (next === `${location.pathname}${location.search}${location.hash}`) return;
  history[replace ? "replaceState" : "pushState"]({}, "", next);
}
