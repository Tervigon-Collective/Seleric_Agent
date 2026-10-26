import { create } from "zustand";

/** The date axis a chat's figures are counted on; the backend passes it to every metric query (Core `date_axis`). */
export type DateAxis = "order_date" | "event_date";

export const DATE_AXES: { value: DateAxis; label: string; hint: string }[] = [
  { value: "order_date", label: "Order date", hint: "Orders placed in the period; returns and cancels stay with their order" },
  { value: "event_date", label: "Event date", hint: "Returns, cancels and RTOs on the day they happened" },
];

const KEY = "seleric.dateAxis";
const isAxis = (v: unknown): v is DateAxis => DATE_AXES.some((a) => a.value === v);

function load(): DateAxis {
  try {
    const stored = window.localStorage.getItem(KEY);
    if (isAxis(stored)) return stored;
  } catch { /* storage blocked: the default applies */ }
  return "order_date";
}

interface DateAxisState {
  axis: DateAxis;
  setAxis: (axis: DateAxis) => void;
}

export const useDateAxisStore = create<DateAxisState>((set) => ({
  axis: load(),
  setAxis: (axis) => {
    try { window.localStorage.setItem(KEY, axis); } catch { /* not persisted */ }
    set({ axis });
  },
}));

export const getDateAxis = (): DateAxis => useDateAxisStore.getState().axis;
