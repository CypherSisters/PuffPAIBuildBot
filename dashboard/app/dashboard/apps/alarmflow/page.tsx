"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  mdiTimerOutline,
  mdiAlarmCheck,
  mdiDelete,
  mdiPlay,
  mdiPause,
  mdiStop,
  mdiBellRing,
  mdiPlus,
  mdiClockOutline,
  mdiVolumeHigh,
  mdiCheckCircle,
  mdiOpenInNew,
  mdiAlarmSnooze,
  mdiRepeat,
  mdiLightningBoltOutline,
} from "@mdi/js";
import Button from "@/app/_components/Button";
import Icon from "@/app/_components/Icon";
import { useAppData } from "@/app/_lib/hooks/useAppData";

import { playAlarmSound, showBrowserNotification } from "@/app/_lib/audioUnlock";
import { AppHeader, AppPage, Panel, SectionBar } from "../_components/ui";

/**
 * Compute live remaining seconds from the item's creation time + duration.
 * This decouples display from local state, so polling can refresh the
 * server payload without resetting countdowns.
 */
function computeLiveRemaining(item: any, nowMs: number): number {
  if (!item) return 0;
  // Paused timers freeze: the backend nulls `ts` and pins `rem`.
  if (item.status === "paused") return item.rem || 0;
  if (item.mode === "alarm" && item.targetMs) {
    return Math.floor((item.targetMs - nowMs) / 1000);
  }
  if (item.mode === "timer" && item.ts && item.tot) {
    const expiryMs = item.ts + item.tot * 1000;
    return Math.floor((expiryMs - nowMs) / 1000);
  }
  return item.rem || 0;
}

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type ItemMode = "timer" | "alarm";
type ItemStatus = "running" | "paused" | "ringing" | "stopped";
type SoundChoice = "chime" | "alert" | "gentle" | "bell" | "none";
type RepeatChoice = "once" | "daily" | "weekdays" | "weekends" | "weekly";

interface AlarmItem {
  id: string;
  mode: ItemMode;
  desc: string;
  tot: number;
  rem: number;
  sound: SoundChoice;
  status: ItemStatus;
  source: string;
  ts: number | null;
  targetMs: number | null;
  /** Recurrence — absent on pre-v2 items, meaning "once". */
  repeat?: RepeatChoice;
  /** Times snoozed since it last rang; reset on dismiss/reschedule. */
  snoozed?: number;
}

interface AlarmFlowData {
  items: AlarmItem[];
  last_updated?: string;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const SOUNDS: { value: SoundChoice; label: string }[] = [
  { value: "chime", label: "Chime" },
  { value: "alert", label: "Alert" },
  { value: "gentle", label: "Gentle" },
  { value: "bell", label: "Bell" },
  { value: "none", label: "None" },
];

const REPEATS: { value: RepeatChoice; label: string }[] = [
  { value: "once", label: "Once" },
  { value: "daily", label: "Every day" },
  { value: "weekdays", label: "Weekdays" },
  { value: "weekends", label: "Weekends" },
  { value: "weekly", label: "Weekly" },
];

/** One-tap timer presets, in minutes. */
const TIMER_PRESETS = [1, 3, 5, 10, 15, 30, 60];

function presetLabel(min: number): string {
  return min >= 60 ? `${min / 60}h` : `${min}m`;
}

function padTwo(n: number): string {
  return n.toString().padStart(2, "0");
}

function formatCountdown(seconds: number): string {
  if (seconds < 0) seconds = 0;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${padTwo(h)}:${padTwo(m)}:${padTwo(s)}`;
}

function formatAlarmTime(targetMs: number | null): string {
  if (!targetMs) return "--:--";
  const d = new Date(targetMs);
  let hour = d.getHours();
  const minute = d.getMinutes();
  const ampm = hour >= 12 ? "PM" : "AM";
  hour = hour % 12 || 12;
  return `${hour}:${padTwo(minute)} ${ampm}`;
}

function statusColor(status: ItemStatus): string {
  switch (status) {
    case "running":
      return "bg-[color-mix(in_srgb,var(--mint)_15%,transparent)] text-[var(--mint)]";
    case "paused":
      return "bg-[color-mix(in_srgb,var(--lemon)_15%,transparent)] text-[var(--lemon)]";
    case "ringing":
      return "bg-[color-mix(in_srgb,var(--puff-red)_15%,transparent)] text-[var(--puff-red)]";
    case "stopped":
      return "bg-[var(--chip)] text-[var(--text2)]";
  }
}

function statusLabel(status: ItemStatus): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

/* ------------------------------------------------------------------ */
/*  Sub-components                                                     */
/* ------------------------------------------------------------------ */

function TabButton({
  active,
  label,
  icon,
  onClick,
}: {
  active: boolean;
  label: string;
  icon: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        "flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-semibold transition-all duration-200",
        active
          ? "bg-gradient-to-r from-[var(--pink)] to-[var(--magenta)] text-(--puff-on-accent) shadow-md"
          : "bg-[var(--card2)] text-[var(--text2)] hover:bg-[var(--card)]",
      ].join(" ")}
    >
      <Icon path={icon} size="18" />
      {label}
    </button>
  );
}

function StatusBadge({ status }: { status: ItemStatus }) {
  return (
    <span
      className={[
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        statusColor(status),
      ].join(" ")}
    >
      {status === "ringing" && <Icon path={mdiBellRing} size="12" />}
      {statusLabel(status)}
    </span>
  );
}

function SoundLabel({ sound }: { sound: SoundChoice }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs text-[var(--text3)]">
      <Icon path={mdiVolumeHigh} size="12" />
      {sound.charAt(0).toUpperCase() + sound.slice(1)}
    </span>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-[var(--sky)]/10">
        <Icon path={mdiClockOutline} size="40" className="text-[var(--sky)]" />
      </div>
      <h3 className="text-lg font-semibold text-[var(--text2)]">
        No timers or alarms yet
      </h3>
      <p className="mt-1 text-sm text-[var(--text2)]">
        Create your first timer or alarm above to get started.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Item row                                                           */
/* ------------------------------------------------------------------ */

function RepeatBadge({ repeat }: { repeat?: RepeatChoice }) {
  if (!repeat || repeat === "once") return null;
  const label = REPEATS.find((r) => r.value === repeat)?.label ?? repeat;
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-[var(--sky)]/10 px-2 py-0.5 text-xs font-medium text-[var(--sky)]">
      <Icon path={mdiRepeat} size="12" />
      {label}
    </span>
  );
}

function AlarmItemRow({
  item,
  onDismiss,
  onDelete,
  onSnooze,
  onPause,
  onResume,
}: {
  item: AlarmItem;
  onDismiss: (id: string) => void;
  onDelete: (id: string) => void;
  onSnooze: (id: string) => void;
  onPause: (id: string) => void;
  onResume: (id: string) => void;
}) {
  const isTimer = item.mode === "timer";
  const isActive = item.status === "running" || item.status === "ringing";
  const isDismissable = item.status === "ringing";

  return (
    <div
      className={[
        "glass-card gloss-highlight flex items-center gap-4 rounded-2xl p-4 transition-all duration-200",
        // puff-live: a ringing row must keep pulsing even when the idle/kiosk
        // motion governor has frozen the ambient scene (css/_theme.css).
        item.status === "ringing" ? "ring-2 ring-[color-mix(in_srgb,var(--puff-red)_40%,transparent)] animate-pulse puff-live" : "",
        item.status === "stopped" ? "opacity-60" : "",
      ].join(" ")}
    >
      {/* Icon */}
      <div
        className={[
          "flex h-12 w-12 flex-shrink-0 items-center justify-center rounded-xl",
          isTimer
            ? "bg-[var(--sky)]/15 text-[var(--sky)]"
            : "bg-[var(--text2)]/15 text-[var(--text2)]",
        ].join(" ")}
      >
        <Icon path={isTimer ? mdiTimerOutline : mdiAlarmCheck} size="24" />
      </div>

      {/* Info */}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="truncate font-semibold text-[var(--text)]">
            {item.desc || (isTimer ? "Timer" : "Alarm")}
          </h4>
          <StatusBadge status={item.status} />
          <RepeatBadge repeat={item.repeat} />
          {(item.snoozed ?? 0) > 0 && (
            <span className="inline-flex items-center gap-1 rounded-full bg-[color-mix(in_srgb,var(--lemon)_10%,transparent)] px-2 py-0.5 text-xs font-medium text-[var(--lemon)]">
              <Icon path={mdiAlarmSnooze} size="12" />
              snoozed ×{item.snoozed}
            </span>
          )}
        </div>
        <div className="mt-1 flex items-center gap-3">
          <span className="font-mono text-lg font-bold text-[var(--text2)]">
            {isTimer ? formatCountdown(item.rem) : formatAlarmTime(item.targetMs)}
          </span>
          <SoundLabel sound={item.sound} />
          <span className="text-xs text-[var(--text3)]">
            {isTimer ? "timer" : "alarm"}
          </span>
        </div>
      </div>

      {/* Actions */}
      <div className="flex flex-shrink-0 items-center gap-2">
        {isDismissable && (
          <>
            <Button
              icon={mdiAlarmSnooze}
              color="warning"
              small
              roundedFull
              onClick={() => onSnooze(item.id)}
            />
            <Button
              icon={mdiCheckCircle}
              color="success"
              small
              roundedFull
              onClick={() => onDismiss(item.id)}
            />
          </>
        )}
        {isTimer && item.status === "running" && (
          <Button
            icon={mdiPause}
            color="info"
            small
            roundedFull
            onClick={() => onPause(item.id)}
          />
        )}
        {isTimer && item.status === "paused" && (
          <Button
            icon={mdiPlay}
            color="success"
            small
            roundedFull
            onClick={() => onResume(item.id)}
          />
        )}
        {isActive && !isDismissable && (
          <Button
            icon={mdiStop}
            color="warning"
            small
            roundedFull
            onClick={() => onDismiss(item.id)}
          />
        )}
        <Button
          icon={mdiDelete}
          color="danger"
          small
          roundedFull
          onClick={() => onDelete(item.id)}
        />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main page                                                          */
/* ------------------------------------------------------------------ */

export default function AlarmFlowPage() {
  const { data, loading, error, runAction, reload } =
    useAppData<AlarmFlowData>("alarmflow");

  const [tab, setTab] = useState<"timer" | "alarm">("timer");

  // Timer form
  const [timerDesc, setTimerDesc] = useState("");
  const [timerHours, setTimerHours] = useState(0);
  const [timerMinutes, setTimerMinutes] = useState(5);
  const [timerSeconds, setTimerSeconds] = useState(0);
  const [timerSound, setTimerSound] = useState<SoundChoice>("chime");

  // Alarm form
  const [alarmDesc, setAlarmDesc] = useState("");
  const [alarmHour, setAlarmHour] = useState(8);
  const [alarmMinute, setAlarmMinute] = useState(0);
  const [alarmAmPm, setAlarmAmPm] = useState<"AM" | "PM">("AM");
  const [alarmSound, setAlarmSound] = useState<SoundChoice>("bell");
  const [alarmRepeat, setAlarmRepeat] = useState<RepeatChoice>("once");

  const [submitting, setSubmitting] = useState(false);

  // Tick counter to force re-render every second so live countdowns
  // stay smooth without mutating the server-backed items list.
  const [, forceTick] = useState(0);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const firedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    tickRef.current = setInterval(() => forceTick((t) => t + 1), 1000);
    return () => {
      if (tickRef.current) clearInterval(tickRef.current);
    };
  }, []);

  // Compute the live items list from server data + current time.
  // We never mutate `items` locally — every render derives the truth.
  const nowMs = Date.now();
  const items: AlarmItem[] = (data?.items || []).map((raw: AlarmItem) => {
    const liveRem = computeLiveRemaining(raw, nowMs);
    let status: ItemStatus = raw.status;
    // Auto-promote running items to "ringing" when live remaining hits 0
    if (status === "running" && liveRem <= 0) {
      status = "ringing";
      // Fire sound + notification ONCE per item id
      if (!firedRef.current.has(raw.id)) {
        firedRef.current.add(raw.id);
        playAlarmSound(raw.sound);
        showBrowserNotification(
          raw.mode === "timer"
            ? `Timer done: ${raw.desc || "Timer"}`
            : `Alarm: ${raw.desc || "Alarm"}`
        );
      }
    }
    return { ...raw, rem: Math.max(0, liveRem), status };
  });

  // Request notification permission once on mount
  useEffect(() => {
    if (typeof window !== "undefined" && "Notification" in window) {
      if (Notification.permission === "default") {
        Notification.requestPermission().catch(() => {});
      }
    }
  }, []);

  /* ---------- Actions ---------- */

  const handleCreateTimer = useCallback(async () => {
    if (timerHours === 0 && timerMinutes === 0 && timerSeconds === 0) return;
    setSubmitting(true);
    try {
      await runAction("alarm_create_timer", {
        desc: timerDesc || "Timer",
        hours: timerHours,
        minutes: timerMinutes,
        seconds: timerSeconds,
        sound: timerSound,
      });
      setTimerDesc("");
      setTimerHours(0);
      setTimerMinutes(5);
      setTimerSeconds(0);
    } catch {
      // handled by hook
    } finally {
      setSubmitting(false);
    }
  }, [timerDesc, timerHours, timerMinutes, timerSeconds, timerSound, runAction]);

  const handleCreateAlarm = useCallback(async () => {
    setSubmitting(true);
    try {
      let hour24 = alarmHour;
      if (alarmAmPm === "PM" && alarmHour !== 12) hour24 += 12;
      if (alarmAmPm === "AM" && alarmHour === 12) hour24 = 0;
      await runAction("alarm_create_alarm", {
        desc: alarmDesc || "Alarm",
        hour: hour24,
        minute: alarmMinute,
        sound: alarmSound,
        repeat: alarmRepeat,
      });
      setAlarmDesc("");
      setAlarmHour(8);
      setAlarmMinute(0);
      setAlarmRepeat("once");
    } catch {
      // handled by hook
    } finally {
      setSubmitting(false);
    }
  }, [alarmDesc, alarmHour, alarmMinute, alarmAmPm, alarmSound, alarmRepeat, runAction]);

  const handlePreset = useCallback(
    async (minutes: number) => {
      setSubmitting(true);
      try {
        await runAction("alarm_create_timer", {
          desc: `${presetLabel(minutes)} timer`,
          minutes,
          sound: timerSound,
        });
      } catch {
        // handled by hook
      } finally {
        setSubmitting(false);
      }
    },
    [runAction, timerSound]
  );

  const handleSnooze = useCallback(
    async (itemId: string) => {
      // Let it re-ring after the snooze: clear the once-only notification
      // latch so the chime fires again in 9 minutes.
      firedRef.current.delete(itemId);
      try {
        await runAction("alarm_snooze", { item_id: itemId, minutes: 9 });
      } catch {
        // handled by hook
      }
    },
    [runAction]
  );

  const handlePause = useCallback(
    async (itemId: string) => {
      try {
        await runAction("alarm_pause", { item_id: itemId });
      } catch {
        // handled by hook
      }
    },
    [runAction]
  );

  const handleResume = useCallback(
    async (itemId: string) => {
      firedRef.current.delete(itemId);
      try {
        await runAction("alarm_resume", { item_id: itemId });
      } catch {
        // handled by hook
      }
    },
    [runAction]
  );

  const handleDismiss = useCallback(
    async (itemId: string) => {
      // Repeating alarms reschedule on dismiss — clear the notification
      // latch so the NEXT occurrence rings too.
      firedRef.current.delete(itemId);
      try {
        await runAction("alarm_dismiss", { item_id: itemId });
      } catch {
        // handled by hook
      }
    },
    [runAction]
  );

  const handleDelete = useCallback(
    async (itemId: string) => {
      // Mark as fired to suppress any further notifications for this item
      firedRef.current.add(itemId);
      try {
        await runAction("alarm_delete", { item_id: itemId });
      } catch {
        // Polling will refresh on next tick
        reload();
      }
    },
    [runAction, reload]
  );

  const handleDismissAll = useCallback(async () => {
    firedRef.current.clear();
    try {
      await runAction("alarm_dismiss_all");
    } catch {
      // handled by hook
    }
  }, [runAction]);

  /* ---------- Derived ---------- */

  const ringingCount = items.filter((i) => i.status === "ringing").length;
  const activeCount = items.filter(
    (i) => i.status === "running" || i.status === "paused"
  ).length;

  /* ---------- Shared input classes ---------- */

  const inputClass = [
    "w-full rounded-xl border border-[var(--line)] bg-[var(--card)] px-3 py-2.5 text-sm",
    "text-[var(--text)] placeholder-[var(--text3)]",
    "focus:border-[var(--sky)] focus:outline-none focus:ring-2 focus:ring-[var(--sky)]/30",
    "",
    "/20",
    "transition-colors duration-150",
  ].join(" ");

  const selectClass = inputClass;

  const numberInputClass = [
    "w-full rounded-xl border border-[var(--line)] bg-[var(--card)] px-3 py-2.5 text-center text-sm font-mono",
    "text-[var(--text)]",
    "focus:border-[var(--sky)] focus:outline-none focus:ring-2 focus:ring-[var(--sky)]/30",
    "",
    "/20",
    "transition-colors duration-150",
  ].join(" ");

  /* ---------- Render ---------- */

  return (
    <AppPage>
      {/* Header */}
      <AppHeader title="AlarmFlow">
        <div className="flex gap-2">
          <Button
            icon={mdiOpenInNew}
            label="Open AlarmFlow"
            color="white"
            small
            roundedFull
            href="/dashboard/apps/alarmflow/manage"
          />
          <Button
            href="/dashboard"
            label="Back"
            color="whiteDark"
            small
            roundedFull
          />
        </div>
      </AppHeader>

      {/* Ringing takeover banner — impossible to miss, snooze/dismiss right
          here without hunting through the list */}
      {ringingCount > 0 && (
        <div className="puff-live mb-6 animate-pulse rounded-2xl bg-gradient-to-r from-[color-mix(in_srgb,var(--puff-red)_30%,transparent)] to-[color-mix(in_srgb,var(--pink)_30%,transparent)] p-5 text-(--puff-text-strong) shadow-xl">
          <div className="flex items-center gap-3">
            <Icon path={mdiBellRing} size="32" />
            <div className="min-w-0 flex-1">
              <p className="text-lg font-bold">
                {items
                  .filter((i) => i.status === "ringing")
                  .map((i) => i.desc || (i.mode === "timer" ? "Timer" : "Alarm"))
                  .join(" · ")}
              </p>
              <p className="text-sm text-(--text2)">
                {ringingCount === 1 ? "is going off" : `${ringingCount} going off`}
              </p>
            </div>
            <div className="flex flex-shrink-0 gap-2">
              {ringingCount === 1 && (
                <button
                  type="button"
                  onClick={() =>
                    handleSnooze(items.find((i) => i.status === "ringing")!.id)
                  }
                  className="rounded-xl bg-[var(--card2)] px-4 py-2 text-sm font-semibold backdrop-blur-sm transition hover:bg-[var(--card2)]"
                >
                  Snooze 9m
                </button>
              )}
              <button
                type="button"
                onClick={handleDismissAll}
                className="rounded-xl bg-(--card) px-4 py-2 text-sm font-bold text-[var(--puff-red)] transition hover:bg-[color-mix(in_srgb,var(--puff-red)_16%,transparent)]"
              >
                Dismiss
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Stats strip */}
      <div className="mb-6 flex flex-wrap gap-3">
        <div className="glass-card gloss-highlight flex items-center gap-2 rounded-xl px-4 py-2">
          <Icon path={mdiPlay} size="16" className="text-[var(--mint)]" />
          <span className="text-sm font-medium text-[var(--text)]">
            {activeCount} active
          </span>
        </div>
        {ringingCount > 0 && (
          <div className="glass-card gloss-highlight flex items-center gap-2 rounded-xl px-4 py-2 ring-2 ring-[color-mix(in_srgb,var(--puff-red)_40%,transparent)]">
            <Icon path={mdiBellRing} size="16" className="text-[var(--puff-red)]" />
            <span className="text-sm font-semibold text-[var(--puff-red)]">
              {ringingCount} ringing
            </span>
            <button
              type="button"
              onClick={handleDismissAll}
              className="ml-1 rounded-lg bg-[color-mix(in_srgb,var(--puff-red)_15%,transparent)] px-2 py-0.5 text-xs font-medium text-[var(--puff-red)] hover:bg-[color-mix(in_srgb,var(--puff-red)_25%,transparent)]"
            >
              Dismiss All
            </button>
          </div>
        )}
      </div>

      {/* Create New Section */}
      <Panel className="mb-6">
        <div className="p-5">
          {/* Tabs */}
          <div className="mb-5 flex gap-2">
            <TabButton
              active={tab === "timer"}
              label="Timer"
              icon={mdiTimerOutline}
              onClick={() => setTab("timer")}
            />
            <TabButton
              active={tab === "alarm"}
              label="Alarm"
              icon={mdiAlarmCheck}
              onClick={() => setTab("alarm")}
            />
          </div>

          {/* Timer form */}
          {tab === "timer" && (
            <div className="space-y-4">
              {/* One-tap presets */}
              <div>
                <label className="mb-1 flex items-center gap-1 text-xs font-medium uppercase tracking-wide text-[var(--text2)]">
                  <Icon path={mdiLightningBoltOutline} size="14" />
                  Quick start
                </label>
                <div className="flex flex-wrap gap-2">
                  {TIMER_PRESETS.map((m) => (
                    <button
                      key={m}
                      type="button"
                      disabled={submitting}
                      onClick={() => handlePreset(m)}
                      className="rounded-xl border border-[var(--sky)]/30 bg-[var(--sky)]/10 px-4 py-2 font-mono text-sm font-semibold text-[var(--sky)] transition hover:bg-[var(--sky)]/25 disabled:opacity-50"
                    >
                      {presetLabel(m)}
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text2)]">
                  Description
                </label>
                <input
                  type="text"
                  placeholder="e.g. Pizza timer"
                  value={timerDesc}
                  onChange={(e) => setTimerDesc(e.target.value)}
                  className={inputClass}
                />
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text2)]">
                    Hours
                  </label>
                  <input
                    type="number"
                    min={0}
                    max={23}
                    value={timerHours}
                    onChange={(e) => setTimerHours(Math.max(0, Math.min(23, parseInt(e.target.value) || 0)))}
                    className={numberInputClass}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text2)]">
                    Minutes
                  </label>
                  <input
                    type="number"
                    min={0}
                    max={59}
                    value={timerMinutes}
                    onChange={(e) => setTimerMinutes(Math.max(0, Math.min(59, parseInt(e.target.value) || 0)))}
                    className={numberInputClass}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text2)]">
                    Seconds
                  </label>
                  <input
                    type="number"
                    min={0}
                    max={59}
                    value={timerSeconds}
                    onChange={(e) => setTimerSeconds(Math.max(0, Math.min(59, parseInt(e.target.value) || 0)))}
                    className={numberInputClass}
                  />
                </div>
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text2)]">
                  Sound
                </label>
                <select
                  value={timerSound}
                  onChange={(e) => setTimerSound(e.target.value as SoundChoice)}
                  className={selectClass}
                >
                  {SOUNDS.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </div>

              <Button
                label={submitting ? "Creating..." : "Create Timer"}
                icon={mdiPlus}
                color="info"
                roundedFull
                disabled={submitting || (timerHours === 0 && timerMinutes === 0 && timerSeconds === 0)}
                onClick={handleCreateTimer}
                className="w-full justify-center"
              />
            </div>
          )}

          {/* Alarm form */}
          {tab === "alarm" && (
            <div className="space-y-4">
              <div>
                <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text2)]">
                  Description
                </label>
                <input
                  type="text"
                  placeholder="e.g. Wake up"
                  value={alarmDesc}
                  onChange={(e) => setAlarmDesc(e.target.value)}
                  className={inputClass}
                />
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text2)]">
                    Hour
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={12}
                    value={alarmHour}
                    onChange={(e) => setAlarmHour(Math.max(1, Math.min(12, parseInt(e.target.value) || 1)))}
                    className={numberInputClass}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text2)]">
                    Minute
                  </label>
                  <input
                    type="number"
                    min={0}
                    max={59}
                    value={alarmMinute}
                    onChange={(e) => setAlarmMinute(Math.max(0, Math.min(59, parseInt(e.target.value) || 0)))}
                    className={numberInputClass}
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text2)]">
                    AM / PM
                  </label>
                  <select
                    value={alarmAmPm}
                    onChange={(e) => setAlarmAmPm(e.target.value as "AM" | "PM")}
                    className={selectClass}
                  >
                    <option value="AM">AM</option>
                    <option value="PM">PM</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text2)]">
                    Sound
                  </label>
                  <select
                    value={alarmSound}
                    onChange={(e) => setAlarmSound(e.target.value as SoundChoice)}
                    className={selectClass}
                  >
                    {SOUNDS.map((s) => (
                      <option key={s.value} value={s.value}>
                        {s.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-[var(--text2)]">
                    Repeat
                  </label>
                  <select
                    value={alarmRepeat}
                    onChange={(e) => setAlarmRepeat(e.target.value as RepeatChoice)}
                    className={selectClass}
                  >
                    {REPEATS.map((r) => (
                      <option key={r.value} value={r.value}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <Button
                label={submitting ? "Creating..." : "Create Alarm"}
                icon={mdiPlus}
                color="info"
                roundedFull
                disabled={submitting}
                onClick={handleCreateAlarm}
                className="w-full justify-center"
              />
            </div>
          )}
        </div>
      </Panel>

      {/* Items list */}
      <SectionBar title="Active Timers & Alarms">
        <Button
          label="Refresh"
          color="whiteDark"
          small
          roundedFull
          onClick={() => reload()}
        />
      </SectionBar>

      {loading && !items.length ? (
        <Panel>
          <div className="flex items-center justify-center py-12">
            <div className="h-8 w-8 animate-spin rounded-full border-4 border-[var(--sky)] border-t-transparent" />
            <span className="ml-3 text-sm text-[var(--text2)]">Loading...</span>
          </div>
        </Panel>
      ) : error && !items.length ? (
        <Panel>
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <p className="text-sm text-[var(--puff-red)]">{error}</p>
            <p className="mt-1 text-xs text-[var(--text3)]">
              Make sure the PuffPAI backend is running.
            </p>
          </div>
        </Panel>
      ) : items.length === 0 ? (
        <Panel>
          <EmptyState />
        </Panel>
      ) : (
        <div className="space-y-3">
          {/* Ringing items first, then running, paused, stopped */}
          {[...items]
            .sort((a, b) => {
              const order: Record<ItemStatus, number> = {
                ringing: 0,
                running: 1,
                paused: 2,
                stopped: 3,
              };
              // Same status: soonest-to-fire first
              return (
                order[a.status] - order[b.status] || (a.rem ?? 0) - (b.rem ?? 0)
              );
            })
            .map((item) => (
              <AlarmItemRow
                key={item.id}
                item={item}
                onDismiss={handleDismiss}
                onDelete={handleDelete}
                onSnooze={handleSnooze}
                onPause={handlePause}
                onResume={handleResume}
              />
            ))}
        </div>
      )}
    </AppPage>
  );
}
