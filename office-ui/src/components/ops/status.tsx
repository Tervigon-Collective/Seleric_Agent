import { cn } from "cn";
import type { MetricStatus } from "../../api/monitoring";
import { STATUS_LABEL } from "./format";

const TONE: Record<MetricStatus, string> = {
  alert: "bg-[var(--danger-soft)] text-[var(--danger)]",
  watch: "bg-[var(--warning-soft)] text-[var(--warning)]",
  good: "bg-[var(--success-soft)] text-[var(--success)]",
  ok: "bg-muted text-muted-foreground",
  no_data: "bg-muted text-muted-foreground",
};

/** The verdict is always written out, so colour is never the only signal. */
export function StatusBadge({ status }: { status: MetricStatus }) {
  return <span data-slot="badge" className={cn("inline-flex h-6 w-fit shrink-0 items-center rounded-full px-2.5 text-xs font-medium whitespace-nowrap", TONE[status])}>{STATUS_LABEL[status]}</span>;
}
