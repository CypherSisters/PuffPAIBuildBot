"use client";

/**
 * Reminders tile (design 4b.7) — split out of the old combined
 * reminders-weather tile. Real alarms/timers + pending tasks, soonest
 * first, as rounded rows with pink/sky/mint dots cycling.
 */

import DesignTile, { NUNITO } from "./DesignTile";

export interface ReminderRow {
  id: string;
  label: string;
  ringing?: boolean;
}

const DOTS = ["var(--pink)", "var(--sky)", "var(--mint)"];

export default function RemindersTile({
  items,
  title = "Reminders",
}: {
  items: ReminderRow[];
  title?: string;
}) {
  return (
    <DesignTile
      icon="notifications"
      iconColor="var(--lemon)"
      title={title}
      img="/design-cloud-3.jpg"
      href="/dashboard/apps/calendar"
    >
      {items.length === 0 ? (
        <div
          style={{
            fontFamily: NUNITO,
            fontWeight: 500,
            fontSize: 14,
            color: "var(--text3)",
          }}
        >
          No active reminders. Ask Puff to create one.
        </div>
      ) : (
        <div className="flex flex-col gap-2.5 overflow-hidden">
          {items.slice(0, 4).map((item, i) => (
            <div
              key={item.id}
              className={`flex items-center gap-2.5 rounded-2xl px-3.5 py-3 ${item.ringing ? "animate-pulse puff-live" : ""}`}
              style={{
                background: "var(--card2)",
                fontFamily: NUNITO,
                fontWeight: 600,
                fontSize: 15,
                color: item.ringing ? "var(--pink)" : "var(--text)",
              }}
            >
              <span
                className="inline-block h-2 w-2 flex-none rounded-full"
                style={{ background: DOTS[i % DOTS.length] }}
              />
              <span className="truncate">{item.label}</span>
            </div>
          ))}
        </div>
      )}
    </DesignTile>
  );
}
