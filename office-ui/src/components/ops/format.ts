import type { MetricStatus, Shape } from "../../api/monitoring";

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const plain = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

/** A metric value in the unit the catalogue gives it: ratios as percent, INR with the rupee sign and Indian grouping. */
export function formatValue(value: number | null | undefined, unit: string | null, display: string | null = null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  const u = (unit ?? "").toLowerCase();
  const kind = display ?? (u === "ratio" ? "percent" : u === "inr" || u === "currency" ? "currency" : u === "seconds" ? "seconds" : null);
  if (kind === "percent") return `${plain.format(value * 100)}%`;
  if (kind === "multiple") return `${plain.format(value)}x`;
  if (kind === "currency") return `${value < 0 ? "−" : ""}₹${inr.format(Math.abs(value))}`;
  if (kind === "seconds") return `${plain.format(value)} s`;
  return Math.abs(value) >= 1000 ? inr.format(value) : plain.format(value);
}

/** How far a value is from where it should be, in the unit that fits its shape (never a percent of a negative base). */
export function formatMove(
  worseBy: number | null | undefined,
  unit: "%" | "pp" | "abs" | null | undefined,
  display: string | null = null,
): string {
  if (worseBy === null || worseBy === undefined || !unit) return "";
  const amount = Math.abs(worseBy);
  if (amount < 0.05) return "as expected";
  const sign = worseBy > 0 ? "worse" : "better";
  const abs = display === "currency" ? `₹${inr.format(amount)}` : inr.format(amount);
  const text = unit === "abs" ? abs : `${plain.format(amount)}${unit === "pp" ? " pp" : "%"}`;
  return `${text} ${sign} than expected`;
}

export const STATUS_LABEL: Record<MetricStatus, string> = {
  alert: "Needs attention",
  watch: "Watch",
  good: "Better than usual",
  ok: "Normal",
  no_data: "No verdict yet",
};

/** Unit the alert threshold is entered in, by metric shape. */
export function thresholdUnit(shape: Shape | null | undefined, catalogueUnit: string | null): string {
  if (shape === "ratio" || (catalogueUnit ?? "").toLowerCase() === "ratio") return "pp";
  if (shape === "signed") return "units";
  return "%";
}

export function ago(seconds: number): string {
  if (seconds < 90) return "just now";
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}
