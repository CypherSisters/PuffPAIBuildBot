/**
 * Shared timer/alarm time math (the dashboard, AlarmFlow page, widget, and
 * FloatingPuff all need the same answer to "how long until this fires?").
 */

export interface AlarmishItem {
  id: string;
  mode: "timer" | "alarm";
  desc: string;
  status: string;
  sound?: string;
  ts?: number | null;
  tot?: number;
  rem?: number;
  targetMs?: number | null;
}

/** Live seconds remaining; negative once it has fired. */
export function computeLiveRemaining(item: AlarmishItem, nowMs: number): number {
  if (!item) return 0;
  // Paused timers freeze: the backend pins `rem` and nulls `ts`.
  if (item.status === "paused") return item.rem || 0;
  if (item.mode === "alarm" && item.targetMs) {
    return Math.floor((item.targetMs - nowMs) / 1000);
  }
  if (item.mode === "timer" && item.ts && item.tot) {
    return Math.floor((item.ts + item.tot * 1000 - nowMs) / 1000);
  }
  return item.rem || 0;
}

/** Items that are ringing right now (fired within the last 5 minutes and
 * not dismissed) — the same window the dashboard treats as "still going". */
export function ringingItems(items: AlarmishItem[], nowMs: number): AlarmishItem[] {
  return (items || []).filter((i) => {
    if (i.status === "stopped" || i.status === "paused") return false;
    const rem = computeLiveRemaining(i, nowMs);
    return rem <= 0 && rem > -300;
  });
}
