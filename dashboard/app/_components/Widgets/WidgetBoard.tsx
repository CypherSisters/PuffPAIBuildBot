"use client";

/**
 * Registry-driven widgets (data/dashboard_widgets.json), which the chat
 * agent manages via the dashboard_add_widget / dashboard_remove_widget
 * tools. Adding a new widget TYPE = one renderer component here + one
 * catalog entry in puffpai/dashboard_widgets.py. Nothing else.
 *
 * Not its own board: useWidgets() + renderWidget() feed
 * the dashboard's unified TileGrid, so registry widgets are dragged and
 * resized exactly like the built-in tiles.
 *
 * Styled with the design tile pattern (DesignTile shell,
 * MsIcon + Fredoka headers, JBMono stats).
 */

import { useEffect, useState } from "react";
import MsIcon from "../MsIcon";
import DesignTile, {
  FREDOKA,
  JBM,
  MicroLabel,
  MicroStat,
  NUNITO,
} from "./DesignTile";
import { useAppData } from "@/app/_lib/hooks/useAppData";
import AppFeedWidget from "./AppFeedWidget";

// Same-origin proxy path only, never an env-supplied base: such a
// base can point the browser past the /api/pai proxy and so past
// the pairing gate, the path allowlist and the cross-site check.
const API_BASE = "/api/pai";
const API_KEY = "";
const POLL_MS = 30_000;

function authHeaders(): Record<string, string> {
  return API_KEY ? { Authorization: `Bearer ${API_KEY}` } : {};
}

export interface Widget {
  id: string;
  type: string;
  props: Record<string, any>;
}

const noteStyle = {
  fontFamily: NUNITO,
  fontWeight: 500,
  fontSize: 13,
  color: "var(--text3)",
} as const;

/* ── News widget ─────────────────────────────────────────────────────────── */

interface NewsItem {
  title: string;
  link: string;
  published: string;
}

function NewsWidget({ widget }: { widget: Widget }) {
  const [items, setItems] = useState<NewsItem[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch(`${API_BASE}/dashboard/widgets/${widget.id}/data`, {
        headers: authHeaders(),
        cache: "no-store",
        signal: AbortSignal.timeout(15000),
      })
        .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        .then((d) => {
          if (!cancelled && Array.isArray(d?.items)) {
            setItems(d.items);
            setError(false);
          }
        })
        .catch(() => {
          if (!cancelled) setError(true);
        });
    };
    load();
    const iv = setInterval(load, 5 * 60_000);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, [widget.id]);

  return (
    <DesignTile
      icon="newspaper"
      iconColor="var(--sky)"
      title={widget.props.title ?? "News"}
      img="/design-cloud-1.jpg"
      bodyClassName="min-h-0 flex-1 overflow-y-auto"
    >
      {error && items.length === 0 && (
        <p style={noteStyle}>Feed unavailable right now.</p>
      )}
      <ul className="space-y-2">
        {items.map((it, i) => (
          <li key={i} className="leading-snug">
            <a
              href={it.link}
              target="_blank"
              rel="noreferrer"
              className="transition hover:opacity-70"
              style={{
                fontFamily: NUNITO,
                fontWeight: 600,
                fontSize: 13,
                color: "var(--text2)",
              }}
            >
              {it.title}
            </a>
          </li>
        ))}
      </ul>
    </DesignTile>
  );
}

/* ── Calendar widget ─────────────────────────────────────────────────────── */

function CalendarWidget({ widget }: { widget: Widget }) {
  const { data } = useAppData<any>("calendar");
  const daysAhead = Number(widget.props.days_ahead ?? 7);

  const now = new Date();
  const horizon = new Date(now.getTime() + daysAhead * 86400_000);
  const events: any[] = Array.isArray(data?.events)
    ? data.events
        .filter((e: any) => {
          const start = new Date(e.start);
          return !isNaN(start.getTime()) && start >= now && start <= horizon;
        })
        .sort((a: any, b: any) => a.start.localeCompare(b.start))
        .slice(0, 6)
    : [];
  const pendingTasks: number = Array.isArray(data?.tasks)
    ? data.tasks.filter((t: any) => t.status === "pending").length
    : 0;

  return (
    <DesignTile
      icon="calendar_month"
      iconColor="var(--lemon)"
      title={widget.props.title ?? "Upcoming"}
      img="/design-cloud-2.jpg"
      stat={
        <MicroStat>
          NEXT {daysAhead}D · {pendingTasks} OPEN
        </MicroStat>
      }
      bodyClassName="min-h-0 flex-1 overflow-y-auto"
    >
      {events.length === 0 ? (
        <p style={noteStyle}>Nothing scheduled.</p>
      ) : (
        <ul className="space-y-2">
          {events.map((e) => (
            <li key={e.id} className="flex items-baseline gap-2.5">
              <span
                className="shrink-0"
                style={{
                  fontFamily: JBM,
                  fontWeight: 600,
                  fontSize: 12,
                  color: "var(--sky)",
                }}
              >
                {new Date(e.start).toLocaleDateString(undefined, {
                  month: "short",
                  day: "numeric",
                })}
              </span>
              <span
                style={{
                  fontFamily: NUNITO,
                  fontWeight: 600,
                  fontSize: 13,
                  color: "var(--text)",
                }}
              >
                {e.title}
              </span>
            </li>
          ))}
        </ul>
      )}
    </DesignTile>
  );
}

/* ── AlarmFlow widget (timers & alarms — live, from app_data) ────────────── */

interface AlarmflowItem {
  id: string;
  mode: "timer" | "alarm";
  desc: string;
  status: string;
  ts?: number;
  tot?: number;
  rem?: number;
  targetMs?: number;
}

// Live remaining seconds from ts + tot (mirrors the main dashboard page).
function alarmLiveRemaining(item: AlarmflowItem, nowMs: number): number {
  if (item.mode === "alarm" && item.targetMs) {
    return Math.max(0, Math.floor((item.targetMs - nowMs) / 1000));
  }
  if (item.mode === "timer" && item.ts && item.tot) {
    return Math.floor((item.ts + item.tot * 1000 - nowMs) / 1000);
  }
  return item.rem ?? 0;
}

function fmtCountdown(seconds: number): string {
  if (seconds <= 0) return "ringing!";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  if (h > 0) return `${h}:${pad(m)}:${pad(s)}`;
  return `${m}:${pad(s)}`;
}

function AlarmflowWidget({ widget }: { widget: Widget }) {
  const { data } = useAppData<any>("alarmflow");

  // Tick every second so countdowns refresh smoothly.
  const [, setTick] = useState(0);
  useEffect(() => {
    const iv = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(iv);
  }, []);

  const nowMs = Date.now();
  const active: (AlarmflowItem & { _rem: number })[] = (
    Array.isArray(data?.items) ? data.items : []
  )
    .filter((i: AlarmflowItem) => i.status !== "stopped")
    .map((i: AlarmflowItem) => ({ ...i, _rem: alarmLiveRemaining(i, nowMs) }))
    .filter((i: AlarmflowItem & { _rem: number }) => i._rem > -300)
    .sort(
      (a: AlarmflowItem, b: AlarmflowItem) => (b.ts || 0) - (a.ts || 0)
    )
    .slice(0, 6);

  return (
    <DesignTile
      icon="timer"
      iconColor="var(--mint)"
      title={widget.props.title ?? "Timers & Alarms"}
      img="/design-cloud-3.jpg"
      bodyClassName="min-h-0 flex-1 overflow-y-auto"
    >
      {active.length === 0 ? (
        <p style={noteStyle}>No active timers.</p>
      ) : (
        <ul className="space-y-2">
          {active.map((item) => {
            const ringing = item._rem <= 0;
            return (
              <li
                key={item.id}
                className={`flex items-center justify-between gap-2 ${
                  ringing ? "animate-pulse puff-live" : ""
                }`}
              >
                <span className="flex min-w-0 items-center gap-2">
                  <MsIcon
                    name={item.mode === "timer" ? "timer" : "alarm"}
                    size={15}
                    style={{
                      color:
                        item.mode === "timer" ? "var(--mint)" : "var(--lemon)",
                    }}
                  />
                  <span
                    className="truncate"
                    style={{
                      fontFamily: NUNITO,
                      fontWeight: 600,
                      fontSize: 13,
                      color: "var(--text)",
                    }}
                  >
                    {item.desc || (item.mode === "timer" ? "Timer" : "Alarm")}
                  </span>
                </span>
                <span
                  className="shrink-0"
                  style={{
                    fontFamily: JBM,
                    fontWeight: 700,
                    fontSize: 12,
                    color: ringing ? "var(--pink)" : "var(--sky)",
                  }}
                >
                  {item.mode === "alarm" && item.targetMs
                    ? new Date(item.targetMs).toLocaleTimeString([], {
                        hour: "numeric",
                        minute: "2-digit",
                      })
                    : fmtCountdown(item._rem)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </DesignTile>
  );
}

/* ── Now Playing widget (Jellyfin media Pi — LAN-only, Lockdown-safe) ────── */

interface NowPlayingSession {
  session_id: string;
  device: string;
  is_paused: boolean;
  position_seconds: number;
  volume_level: number | null;
  now_playing: {
    item_id: string;
    title: string;
    artist: string;
    album: string;
    duration_seconds: number;
    art_url: string | null;
  } | null;
}

function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function NowPlayingWidget({ widget }: { widget: Widget }) {
  const [sessions, setSessions] = useState<NowPlayingSession[]>([]);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () => {
    fetch(`${API_BASE}/dashboard/widgets/${widget.id}/data`, {
      headers: authHeaders(),
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((d) => {
        if (Array.isArray(d?.sessions)) {
          setSessions(d.sessions);
          setError(false);
        }
      })
      .catch(() => setError(true));
  };

  useEffect(() => {
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widget.id]);

  // Prefer the session that's actively playing something.
  const active =
    sessions.find((s) => s.now_playing && !s.is_paused) ??
    sessions.find((s) => s.now_playing) ??
    null;

  const control = (action: string, volume?: number) => {
    if (!active || busy) return;
    setBusy(true);
    fetch(`${API_BASE}/api/media/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({
        session_id: active.session_id,
        action,
        ...(volume !== undefined ? { volume } : {}),
      }),
      signal: AbortSignal.timeout(10000),
    })
      .catch(() => {})
      .finally(() => {
        setBusy(false);
        setTimeout(load, 400);
      });
  };

  const np = active?.now_playing ?? null;
  const pct =
    np && np.duration_seconds > 0
      ? Math.min(100, (100 * (active?.position_seconds ?? 0)) / np.duration_seconds)
      : 0;

  const roundBtn = {
    borderColor: "var(--line)",
    background: "var(--card2)",
    color: "var(--text)",
  } as const;

  return (
    <DesignTile
      img="/design-cloud-4.jpg"
      bodyClassName="min-h-0 flex-1"
    >
      <MicroLabel style={{ marginBottom: 8 }}>
        {widget.props.title ?? "Now playing"}
        {active ? ` · ${active.device}` : ""}
      </MicroLabel>

      {!np ? (
        <div className="flex h-full items-center">
          <p style={noteStyle}>
            {/* No voice hint. This tile is only drawn for a build that loaded a
                media.* surface (see SURFACES_BY_WIDGET_TYPE), and what that
                build answers to is the media agent's business, not a sentence
                typed here. An empty tile says empty and nothing more. */}
            {error ? "Media server unreachable." : "Nothing playing."}
          </p>
        </div>
      ) : (
        <div className="flex min-h-0 gap-3.5">
          <div
            className="h-24 w-24 shrink-0 overflow-hidden rounded-[20px] border"
            style={{ borderColor: "var(--line)", background: "var(--card2)" }}
          >
            {np.art_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={np.art_url} alt="" className="h-full w-full object-cover" />
            ) : (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src="/design-cloud-5.jpg"
                alt=""
                className="h-full w-full object-cover"
              />
            )}
          </div>

          <div className="flex min-w-0 flex-1 flex-col">
            <p
              className="truncate"
              style={{
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: 17,
                color: "var(--text)",
              }}
            >
              {np.title}
            </p>
            <p
              className="truncate"
              style={{
                fontFamily: NUNITO,
                fontWeight: 600,
                fontSize: 13,
                color: "var(--text2)",
              }}
            >
              {np.artist}
            </p>
            <p className="truncate" style={noteStyle}>
              {np.album}
            </p>

            <div className="mt-2">
              <div
                className="h-1.5 w-full overflow-hidden rounded-full"
                style={{ background: "var(--chip)" }}
              >
                <div
                  className="h-full rounded-full transition-all"
                  style={{ width: `${pct}%`, background: "var(--sky)" }}
                />
              </div>
              <div
                className="mt-0.5 flex justify-between"
                style={{
                  fontFamily: JBM,
                  fontWeight: 600,
                  fontSize: 9,
                  color: "var(--text3)",
                }}
              >
                <span>{fmtTime(active?.position_seconds ?? 0)}</span>
                <span>{fmtTime(np.duration_seconds)}</span>
              </div>
            </div>

            <div className="mt-auto flex items-center gap-2">
              <button
                onClick={() => control("previous")}
                disabled={busy}
                aria-label="Previous track"
                className="grid h-9 w-9 cursor-pointer place-items-center rounded-full border transition hover:opacity-80 disabled:opacity-40"
                style={roundBtn}
              >
                <MsIcon name="skip_previous" size={18} />
              </button>
              <button
                onClick={() => control(active?.is_paused ? "resume" : "pause")}
                disabled={busy}
                aria-label={active?.is_paused ? "Resume" : "Pause"}
                className="grid h-11 w-11 cursor-pointer place-items-center rounded-full border-0 transition hover:opacity-90 disabled:opacity-40"
                style={{
                  background:
                    "linear-gradient(140deg, var(--pink), var(--magenta))",
                  color: "#fff",
                }}
              >
                <MsIcon name={active?.is_paused ? "play_arrow" : "pause"} size={22} />
              </button>
              <button
                onClick={() => control("next")}
                disabled={busy}
                aria-label="Next track"
                className="grid h-9 w-9 cursor-pointer place-items-center rounded-full border transition hover:opacity-80 disabled:opacity-40"
                style={roundBtn}
              >
                <MsIcon name="skip_next" size={18} />
              </button>

              {active?.volume_level !== null && active?.volume_level !== undefined && (
                <div className="ml-auto flex items-center gap-1.5">
                  <MsIcon
                    name="volume_up"
                    size={14}
                    style={{ color: "var(--text3)" }}
                  />
                  <input
                    type="range"
                    min={0}
                    max={100}
                    defaultValue={active.volume_level}
                    onMouseUp={(e) => control("volume", Number((e.target as HTMLInputElement).value))}
                    onTouchEnd={(e) => control("volume", Number((e.target as HTMLInputElement).value))}
                    className="h-1 w-16"
                    style={{ accentColor: "var(--pink)" }}
                    aria-label="Volume"
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </DesignTile>
  );
}

/* ── Registry access for the unified board ───────────────────────────────── */

const RENDERERS: Record<string, (w: Widget) => React.ReactNode> = {
  news: (w) => <NewsWidget widget={w} />,
  calendar: (w) => <CalendarWidget widget={w} />,
  alarmflow: (w) => <AlarmflowWidget widget={w} />,
  "now-playing": (w) => <NowPlayingWidget widget={w} />,
  "app-feed": (w) => <AppFeedWidget widget={w} />,
};

/** Render one registry widget (caller guarantees the type via useWidgets). */
export function renderWidget(w: Widget): React.ReactNode {
  return RENDERERS[w.type]?.(w) ?? null;
}

/** Poll the backend widget registry; only returns types we can render. */
export function useWidgets(): Widget[] {
  const [widgets, setWidgets] = useState<Widget[]>([]);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      fetch(`${API_BASE}/dashboard/widgets`, {
        headers: authHeaders(),
        cache: "no-store",
        signal: AbortSignal.timeout(8000),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (!cancelled && Array.isArray(d?.widgets)) {
            setWidgets(d.widgets.filter((w: Widget) => RENDERERS[w.type]));
          }
        })
        .catch(() => {});
    };
    load();
    const iv = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(iv);
    };
  }, []);

  return widgets;
}
