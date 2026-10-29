import { Badge } from "@/components/ui/badge";
import type { MetricStatus } from "../../api/monitoring";
import { STATUS_LABEL } from "./format";

/** Left accent per verdict. The verdict is always written out too (see StatusBadge), so colour is never the only signal. */
export const STATUS_ACCENT: Record<string, string> = {
  alert: "border-l-4 border-l-destructive",
  watch: "border-l-4 border-l-[var(--warning)]",
  good: "border-l-4 border-l-[var(--success)]",
};

export function StatusBadge({ status }: { status: MetricStatus }) {
  const variant = status === "alert" ? "destructive" : status === "watch" ? "secondary" : "outline";
  return <Badge variant={variant}>{STATUS_LABEL[status]}</Badge>;
}
