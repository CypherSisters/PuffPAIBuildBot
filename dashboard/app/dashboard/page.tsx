"use client";

/**
 * /dashboard: the home page of THIS bot.
 *
 * One board of tiles: Chat, Build jobs (JobsHome), Find more, and one tile per
 * installed thing (GET /installed: apps, skills, tool servers, command line
 * tools; a running app opens in a new tab). The household tiles of older
 * builds (weather, cameras, meals, ...) start in the hidden tray and can be
 * brought back.
 *
 * How the board looks lives in ONE file on the backend,
 * <state>/dashboard_layout.json (GET/PUT /dashboard/layout,
 * src/puffpai/dashboard_widgets.py): tile order, sizes, hidden, pinned and
 * her names. Arrange mode (the topbar's "puffpai:toggle-arrange" event)
 * drags, resizes, hides, pins and renames; the brain does the same from chat
 * (arrange_dashboard). This browser keeps a copy only for when it is offline.
 */

import { useEffect, useCallback, useState, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import MsIcon from "../_components/MsIcon";

import { useAppDispatch, useAppSelector } from "@/app/_stores/hooks";
import { fetchSystemStatus } from "@/app/_stores/systemSlice";
import { fetchChatHistory } from "@/app/_stores/chatSlice";
import { useAppData } from "@/app/_lib/hooks/useAppData";
import { useBuildSelf } from "@/app/_lib/hooks/useBuildSelf";
import { usePersonaApps } from "@/app/_lib/hooks/usePersonaApps";
import { type OwnerApp } from "@/app/_lib/services/personaService";
import PersonaCard from "@/app/_components/Widgets/PersonaCard";
import { stashChatQuery } from "@/app/_lib/chatHandoff";
import FrontPage from "@/app/_components/Front/FrontPage";
import JobsHome from "@/app/_components/Jobs/JobsHome";
import {
  ChatTile,
  FindMoreTile,
  InstalledTile,
  PlainTile,
  ThingTile,
} from "@/app/_components/Home/HomeTiles";
import { reportSeen, saveLayout, useDashLayout } from "@/app/_lib/hooks/useDashLayout";
import {
  defaultLayout,
  getInstalled,
  type InstalledThing,
} from "@/app/_lib/services/layoutService";
import { listThings, type Thing } from "@/app/_lib/services/myThingsService";
import { assistantName } from "@/app/_lib/surfaceMap";
import PersonaHeader from "@/app/_components/Persona/PersonaHeader";
import { getFront, type FrontPageData } from "@/app/_lib/services/frontService";
import {
  personaLoaded,
  widgetLoaded,
  widgetTypeLoaded,
} from "@/app/_lib/surfaceMap";
import {
  getHADevices,
  type HADevice,
} from "@/app/_lib/services/haService";
import {
  addDashboardCamera,
  removeDashboardCamera,
  getConsentRequests,
  onConsentChanged,
  type ConsentRequest,
} from "@/app/_lib/services/consentService";
import ConsentRequestsBanner, {
  ConsentRequestRow,
} from "@/app/_components/Consent/ConsentRequests";
import TileGrid, { type TileDef } from "@/app/_components/TileGrid";
import {
  useWidgets,
  renderWidget,
} from "@/app/_components/Widgets/WidgetBoard";
import DesignTile, {
  FREDOKA,
  NUNITO,
  JBM,
  STRIPES,
  MicroStat,
} from "@/app/_components/Widgets/DesignTile";
import AnnouncementsCard from "@/app/_components/Widgets/AnnouncementsCard";
import WeatherTile from "@/app/_components/Widgets/WeatherTile";
import JetsonTile from "@/app/_components/Widgets/JetsonTile";
import SmartHomeTile from "@/app/_components/Widgets/SmartHomeTile";
import RemindersTile, {
  type ReminderRow,
} from "@/app/_components/Widgets/RemindersTile";
import { useHaConfirmation } from "@/app/_lib/hooks/useHaConfirmation";
import HaConfirmDialog from "@/app/_components/Confirm/HaConfirmDialog";
import { expandEvents } from "@/app/_lib/recurrence";
import { conversationTitle } from "@/app/_lib/chatTitle";
import { playAlarmSound, showBrowserNotification } from "@/app/_lib/audioUnlock";

const REFRESH_INTERVAL_MS = 30_000;
/** Tray and tile names for the built-in tiles (her own names win). */
const TILE_NAMES: Record<string, string> = {
  chat: "Chat",
  jobs: "Build jobs",
  "find-more": "Find more",
  weather: "Weather",
  jetson: "This computer",
  "smart-home": "Smart home",
  cameras: "Cameras",
  schedule: "Schedule",
  reminders: "Reminders",
  front: "About your bot",
  persona: "Your bot's own card",
  meals: "Meals",
  workout: "Workout",
};

export default function DashboardPage() {
  const dispatch = useAppDispatch();
  const router = useRouter();
  const { status: systemStatus } = useAppSelector((state) => state.system);
  const { conversations } = useAppSelector((state) => state.chat);

  /* ── Arrange mode (topbar toggles it via a window event) ── */
  const [arranging, setArranging] = useState(false);
  const { layout } = useDashLayout();
  const tl = layout.tiles;
  const resetLayout = useCallback(() => {
    saveLayout({ tiles: defaultLayout().tiles });
  }, []);

  useEffect(() => {
    const onToggle = () => setArranging((a) => !a);
    window.addEventListener("puffpai:toggle-arrange", onToggle);
    return () => window.removeEventListener("puffpai:toggle-arrange", onToggle);
  }, []);
  useEffect(() => {
    window.dispatchEvent(
      new CustomEvent("puffpai:arrange-state", { detail: { arranging } })
    );
  }, [arranging]);

  /* ── Hide, show, pin and rename: all saved to the one layout file ── */
  const hideTile = useCallback(
    (id: string) =>
      saveLayout({
        tiles: {
          ...tl,
          hidden: [...tl.hidden.filter((x) => x !== id), id],
          pinned: tl.pinned.filter((x) => x !== id),
        },
      }),
    [tl],
  );
  const unhideTile = useCallback(
    (id: string) => saveLayout({ tiles: { ...tl, hidden: tl.hidden.filter((x) => x !== id) } }),
    [tl],
  );
  const togglePin = useCallback(
    (id: string) =>
      saveLayout({
        tiles: {
          ...tl,
          pinned: tl.pinned.includes(id) ? tl.pinned.filter((x) => x !== id) : [...tl.pinned, id],
        },
      }),
    [tl],
  );
  const renameTile = useCallback(
    (id: string, name: string) => {
      const names = { ...tl.names };
      const clean = name.trim().slice(0, 40);
      if (clean) names[id] = clean;
      else delete names[id];
      saveLayout({ tiles: { ...tl, names } });
    },
    [tl],
  );
  const arrangeTiles = useCallback(
    (order: string[], sizes: Record<string, { w: number; h: number }>) => {
      const rest = tl.order.filter((x) => !order.includes(x));
      saveLayout({
        tiles: {
          ...tl,
          order: [...order, ...rest],
          sizes: { ...tl.sizes, ...sizes },
          pinned: order.filter((x) => tl.pinned.includes(x)),
        },
      });
    },
    [tl],
  );

  /* ── What is installed on this bot (GET /installed), and the things she
        put on her home page from chat (GET /things, on_home) ── */
  const [installed, setInstalled] = useState<InstalledThing[]>([]);
  const [homeThings, setHomeThings] = useState<Thing[]>([]);
  const loadInstalled = useCallback(() => {
    void getInstalled().then(setInstalled);
    void listThings().then((r) => {
      if (r.ok && Array.isArray(r.data)) setHomeThings(r.data.filter((t) => t.on_home));
    });
  }, []);
  useEffect(() => {
    loadInstalled();
    const interval = setInterval(loadInstalled, REFRESH_INTERVAL_MS);
    window.addEventListener("puffpai:things-changed", loadInstalled);
    return () => {
      clearInterval(interval);
      window.removeEventListener("puffpai:things-changed", loadInstalled);
    };
  }, [loadInstalled]);

  // Registry widgets (news feeds, …) join the same grid as the built-ins.
  const widgets = useWidgets();

  // Live data — auto-polls every 2s for chat-driven updates
  const { data: calendarData } = useAppData<any>("calendar");
  const { data: alarmData } = useAppData<any>("alarmflow");

  // Tick every second so timer countdowns refresh smoothly
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  // Today's events (from real calendar) — expanding any recurring events
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const endOfToday = new Date();
  endOfToday.setHours(23, 59, 59, 999);
  const seenEventKeys = new Set<string>();
  const todayEvents = expandEvents(
    calendarData?.events || [],
    startOfToday,
    endOfToday
  )
    .filter((e) => {
      const k = `${e.title}|${e.start}`;
      if (seenEventKeys.has(k)) return false;
      seenEventKeys.add(k);
      return true;
    })
    .slice(0, 4);

  // Pending tasks (real)
  const pendingTasks = (calendarData?.tasks || [])
    .filter((t: any) => t.status !== "completed")
    .slice(0, 4);

  // Active timers/alarms — LIVE remaining from ts + tot, soonest first
  const nowMs = Date.now();
  void tick; // referenced so React keeps re-rendering
  const activeAlarms = (alarmData?.items || [])
    .filter((i: any) => {
      if (i.status === "stopped") return false;
      const liveRem = computeLiveRemaining(i, nowMs);
      return liveRem > -300; // still show up to 5 min past expiry ("ringing")
    })
    .map((i: any) => ({
      ...i,
      _liveRem: computeLiveRemaining(i, nowMs),
    }))
    .sort((a: any, b: any) => (a._liveRem ?? 0) - (b._liveRem ?? 0))
    .slice(0, 6);

  // Ring the chime + notify once when a timer/alarm CROSSES into expiry
  // while this page is mounted (already-expired items are seeded silently).
  const notifiedRef = useRef<Set<string>>(new Set());
  const chimeArmedRef = useRef(false);
  useEffect(() => {
    const items = alarmData?.items || [];
    if (!items.length && !chimeArmedRef.current) return;
    const now = Date.now();
    if (!chimeArmedRef.current) {
      for (const item of items) {
        if (item.status !== "stopped" && computeLiveRemaining(item, now) <= 0) {
          notifiedRef.current.add(item.id);
        }
      }
      chimeArmedRef.current = true;
      return;
    }
    for (const item of items) {
      if (item.status === "stopped") continue;
      if (notifiedRef.current.has(item.id)) continue;
      if (computeLiveRemaining(item, now) <= 0) {
        notifiedRef.current.add(item.id);
        playAlarmSound(item.sound);
        showBrowserNotification(
          item.mode === "timer"
            ? `Timer done: ${item.desc || "Timer"}`
            : `Alarm: ${item.desc || "Alarm"}`
        );
      }
    }
  }, [alarmData, tick]);

  const refresh = useCallback(() => {
    dispatch(fetchSystemStatus());
    dispatch(fetchChatHistory());
  }, [dispatch]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refresh]);

  /* ── The front page: what THIS build is, has and can answer (GET /front).
        Re-read on the same beat as the status, so a book that finishes
        learning or a card switched on shows up without a reload. ── */
  const [front, setFront] = useState<FrontPageData | null>(null);
  const [frontFailed, setFrontFailed] = useState(false);
  const loadFront = useCallback(() => {
    getFront()
      .then((data) => {
        setFront(data);
        setFrontFailed(false);
      })
      .catch(() => setFrontFailed(true));
  }, []);
  useEffect(() => {
    loadFront();
    const interval = setInterval(loadFront, REFRESH_INTERVAL_MS * 2);
    return () => clearInterval(interval);
  }, [loadFront]);
  const askInChat = useCallback(
    (text: string) => {
      if (stashChatQuery(text)) router.push("/dashboard/chat");
    },
    [router],
  );


  // Reminders rows: soonest alarms first, then open tasks.
  const reminderRows: ReminderRow[] = [
    ...activeAlarms.map((item: any) => {
      const liveRem = item._liveRem ?? item.rem ?? 0;
      return {
        id: item.id,
        label:
          item.mode === "timer"
            ? `${item.desc || "Timer"} · ${formatRemaining(liveRem)}`
            : `${item.desc || "Alarm"} · ${formatAlarmTime(item)}`,
        ringing: item.status !== "stopped" && liveRem <= 0,
      };
    }),
    ...pendingTasks.map((task: any) => ({
      id: `task-${task.id}`,
      label: task.title || "Untitled task",
    })),
  ];

  // Recent conversations
  const recentChats =
    conversations.length > 0
      ? conversations.slice(0, 3).map((c, i) => ({
          title: conversationTitle(c),
          preview:
            c.messages.length > 0
              ? c.messages[c.messages.length - 1].content.slice(0, 80) +
                (c.messages[c.messages.length - 1].content.length > 80
                  ? "..."
                  : "")
              : "No messages",
          date: formatRelativeTime(c.created_at),
          color: ["var(--pink)", "var(--sky)", "var(--mint)"][i % 3],
          href: `/dashboard/chat?id=${c.id}`,
        }))
      : [
          {
            title: "Getting started",
            preview: "Welcome to PuffPAI. Your private AI is ready.",
            date: "",
            color: "var(--pink)",
            href: "/dashboard/chat",
          },
        ];

  /* ── What this build carries ──
     GET /build/self says which surfaces the interview loaded and which apps
     `apps/` actually holds. Both gates FAIL OPEN: while the fetch is in
     flight, and forever if the backend is unreachable, `build` is null and
     every tile and chip below stays on screen. A stranger whose backend
     hiccups gets the whole dashboard, never a blank one. */
  const { build } = useBuildSelf();

  /** The apps the owner made, and the one sentence each of them answers.
   *
   *  A separate call from /build/self on purpose: that endpoint serves an app's
   *  id, name, blurb, route, icon and surfaces, and deliberately not its
   *  vocabulary. What a persona's LISTS are called is the owner's own data out of
   *  the owner's own manifest, and GET /persona/list is where it is served.
   *  Empty on a build where nobody has scaffolded anything, which is the common
   *  case, so nothing below changes for such a build. Failure is silence. */
  //  One hook, shared with the rail (see _lib/hooks/usePersonaApps.ts): both
  //  need the same answer and two copies of the fetch is two chances to
  //  disagree about whether a bot exists. It refetches when this browser
  //  installs or deletes one, so the launcher does not need a reload.
  const { apps: personaApps } = usePersonaApps();
  //  Gated on each persona's own surface (`persona.<id>`, put on the allowlist
  //  when it was scaffolded), so the card, the launcher chip and the rail entry
  //  all answer to one rule — and all fail open together while /build/self is
  //  in flight, the same as every other gate on this page.
  const ownerApps: OwnerApp[] = (personaApps ?? []).filter((app) =>
    personaLoaded(build, app),
  );

  /** The persona this home page leads with: the first one the owner made.
   *  Undefined on every stock build, and then nothing below it renders. */
  const personaApp = ownerApps[0];

  /* ── Tile board (12-col, rowHeight 72; the board flows tiles in her
        saved order, so only w/h of these defaults count) ── */
  const botName = assistantName(build);
  const nameOf = (id: string, fallback?: string) =>
    tl.names[id] || fallback || TILE_NAMES[id] || id;
  const installedNames = new Set(installed.map((t) => t.name.toLowerCase()));
  /* A downloaded book is one tile: its knowledge tile from /installed, never
     also a book card from /things (their names may differ a little). */
  const plainName = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const knowledgeNames = installed
    .filter((t) => t.kind === "knowledge")
    .map((t) => plainName(t.name))
    .filter(Boolean);
  const sameBook = (name: string) => {
    const n = plainName(name);
    return !!n && knowledgeNames.some((k) => k === n || k.includes(n) || n.includes(k));
  };
  const allTiles: TileDef[] = [
    {
      id: "chat",
      lg: { x: 0, y: 0, w: 5, h: 4, minW: 3, minH: 2 },
      node: (
        <ChatTile
          title={nameOf("chat")}
          botName={botName}
          onAsk={askInChat}
          recent={recentChats
            .filter((c) => c.date)
            .map((c) => ({ title: c.title, date: c.date, href: c.href }))}
        />
      ),
    },
    {
      id: "jobs",
      lg: { x: 5, y: 0, w: 7, h: 5, minW: 4, minH: 3 },
      node: (
        <PlainTile title={nameOf("jobs")} icon="construction">
          <JobsHome botName={botName} />
        </PlainTile>
      ),
    },
    {
      id: "find-more",
      lg: { x: 0, y: 4, w: 5, h: 2, minW: 2, minH: 2 },
      node: <FindMoreTile title={nameOf("find-more")} />,
    },
    /* One tile per installed thing; a new install gets its tile by itself. */
    ...installed.map(
      (t): TileDef => ({
        id: `${t.kind}:${t.id}`,
        lg: { x: 0, y: 6, w: 3, h: 2, minW: 2, minH: 2 },
        node: <InstalledTile thing={t} title={nameOf(`${t.kind}:${t.id}`, t.name)} />,
      }),
    ),
    ...homeThings
      .filter((t) => !installedNames.has(t.name.toLowerCase()))
      .filter((t) => !(t.kind === "book" && sameBook(t.name)))
      .map(
        (t): TileDef => ({
          id: `thing:${t.id}`,
          lg: { x: 0, y: 6, w: 3, h: 2, minW: 2, minH: 2 },
          node: (
            <ThingTile
              title={nameOf(`thing:${t.id}`, t.name)}
              icon={t.icon}
              blurb={t.blurb}
              href={t.page}
            />
          ),
        }),
      ),
    /* The household tiles of older builds: hidden on a fresh layout. */
    {
      id: "weather",
      lg: { x: 4, y: 0, w: 3, h: 4, minW: 2, minH: 2 },
      node: <WeatherTile title={tl.names.weather} />,
    },
    {
      id: "jetson",
      lg: { x: 7, y: 0, w: 3, h: 4, minW: 2, minH: 2 },
      node: <JetsonTile title={nameOf("jetson")} />,
    },
    {
      id: "smart-home",
      lg: { x: 0, y: 4, w: 3, h: 3, minW: 2, minH: 2 },
      node: <SmartHomeTile title={nameOf("smart-home")} />,
    },
    {
      id: "cameras",
      // Widest tile in band B by owner request — a security feed at w3 was
      // too small to read at kiosk distance.
      lg: { x: 5, y: 4, w: 4, h: 3, minW: 2, minH: 2 },
      node: (
        <DesignTile
          icon="videocam"
          iconColor="var(--lav)"
          title={nameOf("cameras")}
          img="/design-cloud-4.jpg"
          stat={<MicroStat color="var(--pink)">● LIVE</MicroStat>}
          bodyClassName="min-h-0 flex-1"
        >
          <CamerasPanel />
        </DesignTile>
      ),
    },
    {
      id: "schedule",
      lg: { x: 3, y: 4, w: 2, h: 3, minW: 2, minH: 2 },
      node: (
        <DesignTile
          icon="calendar_month"
          iconColor="var(--sky)"
          title={nameOf("schedule")}
          img="/design-cloud-2.jpg"
          href="/dashboard/apps/calendar"
          stat={
            <MicroStat>
              {todayEvents.length} TODAY
            </MicroStat>
          }
        >
          {todayEvents.length === 0 ? (
            <EmptyNote>No events scheduled for today.</EmptyNote>
          ) : (
            <div className="flex flex-col gap-2.5">
              {todayEvents.map((event: any, idx: number) => {
                const dots = [
                  "var(--sky)",
                  "var(--lav)",
                  "var(--pink)",
                  "var(--mint)",
                ];
                const time = event.start
                  ? new Date(event.start).toLocaleTimeString([], {
                      hour: "numeric",
                      minute: "2-digit",
                    })
                  : "All day";
                const rowKey =
                  event._occurrenceKey || `${event.id}-${event.start}-${idx}`;
                return (
                  <div key={rowKey} className="flex items-center gap-3">
                    <span
                      className="h-2 w-2 flex-none rounded-full"
                      style={{ background: dots[idx % dots.length] }}
                    />
                    <span
                      className="w-[62px] flex-none"
                      style={{
                        fontFamily: JBM,
                        fontWeight: 600,
                        fontSize: 12,
                        color: "var(--text3)",
                      }}
                    >
                      {time}
                    </span>
                    <span
                      className="truncate"
                      style={{
                        fontFamily: NUNITO,
                        fontWeight: 600,
                        fontSize: 15,
                        color: "var(--text)",
                      }}
                    >
                      {event.title || "Untitled"}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </DesignTile>
      ),
    },
    {
      id: "reminders",
      lg: { x: 9, y: 4, w: 3, h: 3, minW: 2, minH: 2 },
      node: <RemindersTile items={reminderRows} title={nameOf("reminders")} />,
    },
    {
      id: "meals",
      lg: { x: 8, y: 7, w: 2, h: 3, minW: 2, minH: 2 },
      node: (
        <DesignTile
          icon="restaurant"
          iconColor="var(--mint)"
          title={nameOf("meals")}
          img="/design-cloud-5.jpg"
          href="/dashboard/apps/freshtable"
        >
          <MealsPanel />
        </DesignTile>
      ),
    },
    {
      id: "workout",
      lg: { x: 10, y: 7, w: 2, h: 3, minW: 2, minH: 2 },
      node: (
        <DesignTile
          icon="fitness_center"
          iconColor="var(--lemon)"
          title={nameOf("workout")}
          img="/design-cloud-2.jpg"
          href="/dashboard/apps/fitfam"
        >
          <WorkoutPanel />
        </DesignTile>
      ),
    },
    /* Registry widgets — added/removed via chat ("add NPR news to my
       dashboard"). New ones drop in at the bottom of the board.

       Surface-gated by TYPE, not id: the ids here are backend-assigned
       instance ids, so `widgetLoaded` (which keys off the dashboard's own tile
       ids) can never see them. Without this a media tile lands on a build with
       no media hub. Fails open like every other gate. */
    ...widgets
      .filter((w) => widgetTypeLoaded(build, w.type))
      .map(
        (w, i): TileDef => ({
          id: w.id,
          lg: {
            x: (i % 3) * 4,
            y: 10 + Math.floor(i / 3) * 2,
            w: 4,
            h: 2,
          },
          node: renderWidget(w),
        })
      ),
  ];

  // A tile whose surface this build never loaded is not hidden, it is
  // ABSENT: a build with no kitchen must not be able to put a meal planner
  // back. Fails open: unknown/untrimmed build keeps every tile.
  const carriedTiles = allTiles.filter((t) => widgetLoaded(build, t.id));
  const isHidden = (id: string) => tl.hidden.includes(id);
  // Her order: pinned first (in pin order), then the saved order, then
  // anything new in the order it came.
  const rank = (id: string) => {
    const pin = tl.pinned.indexOf(id);
    if (pin >= 0) return pin - 10_000;
    const at = tl.order.indexOf(id);
    return at >= 0 ? at : 5_000;
  };
  const visibleTiles = carriedTiles
    .map((t, i) => ({ t, i }))
    .filter(({ t }) => !isHidden(t.id))
    .sort((a, b) => rank(a.t.id) - rank(b.t.id) || a.i - b.i)
    .map(({ t }) => t);
  const hiddenTiles = carriedTiles.filter((t) => isHidden(t.id));
  // The two cards above the board are hidden and shown the same way.
  const showFront = !isHidden("front");
  const showPersona = Boolean(personaApp) && !isHidden("persona");
  const hiddenCards = [
    ...(showFront ? [] : ["front"]),
    ...(personaApp && !showPersona ? ["persona"] : []),
  ];
  const trayName = (id: string) =>
    nameOf(
      id,
      widgets.find((w) => w.id === id)?.props?.title ??
        installed.find((t) => `${t.kind}:${t.id}` === id)?.name ??
        homeThings.find((t) => `thing:${t.id}` === id)?.name,
    );

  // What the page shows, by name, so the brain can find a tile she names.
  const seenKey = [
    ...carriedTiles.map((t) => t.id),
    ...(personaApp ? ["persona"] : []),
    "front",
  ]
    .map((id) => `${id}=${trayName(id)}`)
    .join("\n");
  useEffect(() => {
    const seen: Record<string, string> = {};
    for (const pair of seenKey.split("\n")) {
      const at = pair.indexOf("=");
      if (at > 0) seen[pair.slice(0, at)] = pair.slice(at + 1);
    }
    reportSeen("tiles", seen);
  }, [seenKey]);

  return (
    <section
      className="flex min-h-0 flex-col"
      style={{
        animation: "slide-up 0.3s ease-out",
        padding: "clamp(10px, 1.1vw, 16px)",
      }}
    >
      {/* The one greeting: the bot as itself (its icon, name, kind and hello).
           Its wake-up, once per build, lives in the dashboard layout. */}
      <PersonaHeader />

      {showFront && (
        <FrontPage
          front={front}
          failed={frontFailed}
          onRetry={() => {
            loadFront();
            refresh();
          }}
          onAsk={askInChat}
        />
      )}

      {/* Drawbridge consent queue — above the arrangeable board on purpose:
           TileGrid drops never-seen tiles at the BOTTOM of a saved layout,
           so a consent tile would sink out of sight. Renders nothing when
           the queue is empty. */}
      <ConsentRequestsBanner />

      {/* "Proof it works" lives on the Device page now: the home page starts
           nearly blank. */}

      {/* Messages the bot sent on its own, and its one open offer ("Would you
           like a daily update?"). Outside the board for the same reason as the
           consent queue. Renders nothing when there is neither. */}
      <AnnouncementsCard />

      {/* ═══════ THE BOT THE OWNER BUILT ═══════
           Above the arrangeable board, and outside it, for the same reason the
           consent queue is: a never-seen tile sinks to the bottom of a saved
           layout, and the one thing this build exists to be must not be
           something you scroll for. Renders nothing when no persona was
           scaffolded, which is every stock build. On a TRIMMED build — a space
           bot whose allowlist drew most tiles away — it opens up to carry the
           lists and the way into Knowledge, because then it is the page. */}
      {personaApp && showPersona && (
        <PersonaCard
          app={personaApp}
          lead={Boolean(build?.surfaces?.trimmed)}
          prompts={(front?.try_asking ?? []).map((q) => q.text)}
          onAsk={askInChat}
        />
      )}

      {/* ═══════ MAIN BOARD ═══════
           flex-1 + min-h-0 makes this the leftover slot; TileGrid measures it
           and sizes its rows to fill it exactly (kiosk fit). */}
      {visibleTiles.length > 0 && (
      <div className="min-h-0 flex-none" style={{ height: "clamp(480px, 74vh, 940px)" }}>
        <TileGrid
          editing={arranging}
          onRemoveTile={hideTile}
          onArrange={arrangeTiles}
          editBar={(id) => (
            <TileEditBar
              name={trayName(id)}
              pinned={tl.pinned.includes(id)}
              onPin={() => togglePin(id)}
              onRename={(name) => renameTile(id, name)}
            />
          )}
          tiles={visibleTiles}
          sizes={tl.sizes}
          fitHeight
        />
      </div>
      )}

      {/* Arrange tray (design 791-803) */}
      {arranging && (
        <div
          className="mt-3 flex flex-none flex-wrap items-center gap-3.5 rounded-3xl border border-dashed px-5 py-3"
          style={{ borderColor: "var(--pink)", background: "var(--card2)" }}
        >
          <div
            style={{
              fontFamily: NUNITO,
              fontWeight: 500,
              fontSize: 15,
              color: "var(--text2)",
            }}
          >
            {/* The brain does all of this too (arrange_dashboard) and writes
                the same layout file, so the hint is true. */}
            Drag to move, drag a corner to resize, or ask in chat.
            {hiddenTiles.length + hiddenCards.length > 0 ? " Hidden:" : ""}
          </div>
          {hiddenCards.map((id) => (
            <TrayPill key={id} icon="add" onClick={() => unhideTile(id)}>
              {trayName(id)}
            </TrayPill>
          ))}
          {hiddenTiles.map((t) => (
            <TrayPill key={t.id} icon="add" onClick={() => unhideTile(t.id)}>
              {trayName(t.id)}
            </TrayPill>
          ))}
          <div className="flex-1" />
          <TrayPill icon="sync" onClick={refresh}>
            Sync
          </TrayPill>
          <TrayPill icon="restart_alt" onClick={resetLayout}>
            Reset layout
          </TrayPill>
          <button
            type="button"
            onClick={() => setArranging(false)}
            className="flex h-11 cursor-pointer items-center gap-2 rounded-full border-0 px-5 transition hover:opacity-90"
            style={{
              background: "linear-gradient(140deg, var(--pink), var(--magenta))",
              color: "#fff",
              fontFamily: NUNITO,
              fontWeight: 700,
              fontSize: 15,
            }}
          >
            <MsIcon name="check_circle" size={19} />
            Done
          </button>
        </div>
      )}
    </section>
  );
}

/* ═══════════════ HELPERS ═══════════════ */

function formatRelativeTime(isoString: string): string {
  try {
    const then = new Date(isoString).getTime();
    const now = Date.now();
    const diffSec = Math.floor((now - then) / 1000);
    if (diffSec < 60) return "Just now";
    if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
    if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
    return `${Math.floor(diffSec / 86400)}d ago`;
  } catch {
    return "";
  }
}

/**
 * Live remaining seconds for a timer/alarm from its stored creation
 * timestamp + duration, so countdowns stay honest between polls.
 */
function computeLiveRemaining(item: any, nowMs: number): number {
  if (!item) return 0;
  if (item.mode === "alarm" && item.targetMs) {
    return Math.max(0, Math.floor((item.targetMs - nowMs) / 1000));
  }
  if (item.mode === "timer" && item.ts && item.tot) {
    const expiryMs = item.ts + item.tot * 1000;
    return Math.max(-9999, Math.floor((expiryMs - nowMs) / 1000));
  }
  return item.rem || 0;
}

function formatRemaining(seconds: number): string {
  if (seconds == null || isNaN(seconds)) return "";
  if (seconds <= 0) return "ringing!";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatAlarmTime(item: any): string {
  if (item.targetMs) {
    return new Date(item.targetMs).toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
    });
  }
  return formatRemaining(item._liveRem ?? item.rem ?? 0);
}

/* ═══════════════ SUB-COMPONENTS ═══════════════ */

function EmptyNote({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontFamily: NUNITO,
        fontWeight: 500,
        fontSize: 14,
        color: "var(--text3)",
      }}
    >
      {children}
    </div>
  );
}

function TrayPill({
  icon,
  onClick,
  children,
}: {
  icon: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex h-11 cursor-pointer items-center gap-2 rounded-full border px-[18px] transition hover:opacity-80"
      style={{
        borderColor: "var(--line)",
        background: "var(--card)",
        color: "var(--text)",
        fontFamily: NUNITO,
        fontWeight: 700,
        fontSize: 15,
      }}
    >
      <MsIcon name={icon} size={19} />
      {children}
    </button>
  );
}

/** Arrange mode, on one tile: pin it to the top, or give it her own name. */
function TileEditBar({
  name,
  pinned,
  onPin,
  onRename,
}: {
  name: string;
  pinned: boolean;
  onPin: () => void;
  onRename: (name: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(name);
  const pill: React.CSSProperties = {
    fontFamily: JBM,
    fontWeight: 700,
    fontSize: 10,
    letterSpacing: "0.12em",
    textTransform: "uppercase",
    color: "var(--pink)",
    background: "var(--card)",
    border: "1px solid var(--line)",
    borderRadius: 999,
    padding: "4px 9px",
    cursor: "pointer",
  };
  if (editing) {
    return (
      <form
        className="flex items-center gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          onRename(text);
          setEditing(false);
        }}
      >
        <input
          aria-label="Tile name"
          autoFocus
          value={text}
          maxLength={40}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => setEditing(false)}
          style={{ ...pill, textTransform: "none", letterSpacing: 0, fontSize: 12, color: "var(--text)", width: 150 }}
        />
      </form>
    );
  }
  return (
    <>
      <button type="button" style={pill} onClick={onPin} aria-pressed={pinned}>
        {pinned ? "Pinned" : "Pin"}
      </button>
      <button
        type="button"
        style={pill}
        onClick={() => {
          setText(name);
          setEditing(true);
        }}
      >
        Rename
      </button>
    </>
  );
}

// Snapshot refresh interval. Keep gentle: aggressive polling keeps battery
// cameras (Garden Shed) awake and drains them, and the Reolink hub throttles.
const CAMERA_POLL_MS = 5000;

/* ═══════════════ Live meals panel (FreshTable meal plan) ═══════════════ */
function MealsPanel() {
  const { data } = useAppData<any>("freshtable");
  const dayName = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"][new Date().getDay()];
  const todays: any[] = (data?.mealPlan ?? [])
    .filter((e: any) => e.day === dayName)
    .sort((a: any, b: any) =>
      ["breakfast", "lunch", "dinner", "snack"].indexOf(a.meal) -
      ["breakfast", "lunch", "dinner", "snack"].indexOf(b.meal));
  const order = data?.groceryOrder;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden">
        {todays.length === 0 ? (
          <EmptyNote>
            No meals planned for today. Say{" "}
            <span style={{ color: "var(--pink)", fontWeight: 700 }}>
              &ldquo;plan this week&rsquo;s meals&rdquo;
            </span>{" "}
            in chat.
          </EmptyNote>
        ) : (
          todays.slice(0, 4).map((e) => (
            <div key={e.id} className="flex items-baseline justify-between gap-3">
              <span
                style={{
                  fontFamily: JBM,
                  fontWeight: 700,
                  fontSize: 10,
                  letterSpacing: "0.1em",
                  color: "var(--text3)",
                  textTransform: "uppercase",
                }}
              >
                {e.meal}
              </span>
              <span
                className="min-w-0 flex-1 truncate text-right"
                style={{
                  fontFamily: NUNITO,
                  fontWeight: 600,
                  fontSize: 15,
                  color: "var(--text)",
                }}
              >
                {e.dish}
              </span>
            </div>
          ))
        )}
      </div>
      {order?.item_count > 0 && (
        <div
          className="mt-2 flex flex-none items-center justify-between rounded-xl px-3 py-2"
          style={{ background: "var(--chip)" }}
        >
          <span
            style={{
              fontFamily: NUNITO,
              fontWeight: 600,
              fontSize: 13,
              color: "var(--text2)",
            }}
          >
            Grocery order draft
          </span>
          <span
            style={{
              fontFamily: JBM,
              fontWeight: 700,
              fontSize: 12,
              color: "var(--pink)",
            }}
          >
            {order.item_count} items
          </span>
        </div>
      )}
    </div>
  );
}

/* ═══════════════ Per-user workout panel (offline library) ═══════════════ */
interface WorkoutProgram {
  user: string;
  users: string[];
  program: string;
  days_per_week: number;
  session: { name: string; duration_min: number; exercises: { name: string; sets: number; reps: string }[] } | null;
}

function WorkoutPanel() {
  const [selected, setSelected] = useState<string>("");
  const [data, setData] = useState<WorkoutProgram | null>(null);
  // Three states, not two: waiting, answered, and "this build has no program
  // library behind /fitness/program". The third one used to render as four
  // invented family members (Alex, Sam, Jordan, Riley) and an ellipsis, which
  // read as real people with a workout loading. Nobody by those names exists.
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    const saved = typeof window !== "undefined" ? localStorage.getItem("puffpai_workout_user") : null;
    if (saved) setSelected(saved);
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch(`${CAM_API_BASE}/fitness/program${selected ? `?user=${encodeURIComponent(selected)}` : ""}`, {
      signal: AbortSignal.timeout(10000),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled) return;
        if (d?.session) {
          setData(d);
          setUnavailable(false);
        } else {
          // 404 (route trimmed out of this build) and 200-with-no-session (the
          // workout library is empty) mean the same thing on screen.
          setUnavailable(true);
        }
      })
      .catch(() => {
        if (!cancelled) setUnavailable(true);
      });
    return () => { cancelled = true; };
  }, [selected]);

  const pick = (u: string) => {
    setSelected(u);
    try { localStorage.setItem("puffpai_workout_user", u); } catch {}
  };

  if (unavailable && !data) {
    return (
      <div className="flex h-full min-h-0 items-center">
        <p
          style={{
            fontFamily: NUNITO,
            fontWeight: 600,
            fontSize: 13,
            lineHeight: 1.4,
            color: "var(--text2)",
          }}
        >
          No workout program on this build yet.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 overflow-hidden">
      {/* Family member pills — only the people the backend actually returned. */}
      <div className="flex flex-none flex-wrap gap-1.5">
        {(data?.users ?? []).map((u) => {
          const active = (data?.user ?? "") === u;
          return (
            <button
              key={u}
              onClick={(e) => { e.preventDefault(); e.stopPropagation(); pick(u); }}
              className="cursor-pointer rounded-full border-0 px-3 py-1 transition"
              style={{
                background: active
                  ? "linear-gradient(140deg, var(--pink), var(--magenta))"
                  : "var(--chip)",
                color: active ? "#fff" : "var(--text2)",
                fontFamily: NUNITO,
                fontWeight: 700,
                fontSize: 11,
              }}
            >
              {u}
            </button>
          );
        })}
      </div>
      <div
        className="flex-none rounded-2xl px-3.5 py-2.5"
        style={{ background: "var(--card2)" }}
      >
        <p
          style={{
            fontFamily: NUNITO,
            fontWeight: 700,
            fontSize: 15,
            color: "var(--text)",
          }}
        >
          {data?.session?.name ?? "…"}
        </p>
        <p
          style={{
            fontFamily: JBM,
            fontWeight: 600,
            fontSize: 11,
            color: "var(--text3)",
          }}
        >
          {data?.session ? `${data.session.duration_min} MIN · ${data.program.toUpperCase()}` : ""}
        </p>
      </div>
      <div className="min-h-0 flex-1 space-y-1 overflow-hidden">
        {(data?.session?.exercises ?? []).slice(0, 4).map((ex) => (
          <div key={ex.name} className="flex items-center justify-between">
            <span
              className="truncate"
              style={{
                fontFamily: NUNITO,
                fontWeight: 600,
                fontSize: 13,
                color: "var(--text2)",
              }}
            >
              {ex.name}
            </span>
            <span
              style={{
                fontFamily: JBM,
                fontWeight: 600,
                fontSize: 12,
                color: "var(--mint)",
              }}
            >
              {ex.sets} × {ex.reps}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ═══════════════ Dynamic camera tiles (registry-driven) ═══════════════
 * Tiles come from the PuffPAI backend registry (data/dashboard_cameras.json),
 * which the chat agent (Cortex→Bridge→Drawbridge) manages. Shows no tiles if
 * the backend is unreachable. */
// Same-origin proxy path only, never an env-supplied base: such a
// base can point the browser past the /api/pai proxy and so past
// the pairing gate, the path allowlist and the cross-site check.
const CAM_API_BASE = "/api/pai";

interface CameraTile {
  entity_id: string;
  label: string;
}

function CamerasPanel() {
  const [tiles, setTiles] = useState<CameraTile[]>([]);
  /** True once the registry answered — the fallback tile isn't removable. */
  const [fromRegistry, setFromRegistry] = useState(false);

  const load = useCallback(() => {
    return fetch(`${CAM_API_BASE}/dashboard/cameras`, {
      signal: AbortSignal.timeout(8000),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (Array.isArray(d?.cameras) && d.cameras.length > 0) {
          setTiles(d.cameras);
          setFromRegistry(true);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, REFRESH_INTERVAL_MS);
    // Approving a consent request elsewhere can add a tile — pick it up now
    // rather than up to 30s later.
    const off = onConsentChanged(load);
    return () => {
      clearInterval(iv);
      off();
    };
  }, [load]);

  const removeTile = useCallback(
    async (entityId: string) => {
      await removeDashboardCamera(entityId);
      setTiles((prev) => prev.filter((t) => t.entity_id !== entityId));
      load();
    },
    [load]
  );

  const body =
    tiles.length === 1 ? (
      <CameraFeed
        label={tiles[0].label}
        status="live"
        entityId={tiles[0].entity_id}
        fill
        onRemove={fromRegistry ? () => removeTile(tiles[0].entity_id) : undefined}
      />
    ) : (
      // Stacked full-width tiles with staggered probes so the hub never sees
      // simultaneous stream requests.
      <div className="flex h-full flex-col gap-2 overflow-y-auto">
        {tiles.map((t, i) => (
          <CameraFeed
            key={t.entity_id}
            label={t.label}
            status="live"
            entityId={t.entity_id}
            startDelayMs={i * 5000}
            onRemove={fromRegistry ? () => removeTile(t.entity_id) : undefined}
          />
        ))}
      </div>
    );

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="min-h-0 flex-1">{body}</div>
      <AddCameraControl
        existing={tiles.map((t) => t.entity_id)}
        onAdded={load}
      />
    </div>
  );
}

/* ── Add a camera tile ──────────────────────────────────────────────
 * POST /dashboard/cameras runs Bridge -> Drawbridge. A missing per-device
 * consent comes back 403 having FILED a consent request — that is the
 * designed flow, so it's shown as "Permission needed" with the new request
 * inline, not as an error. */
function AddCameraControl({
  existing,
  onAdded,
}: {
  existing: string[];
  onAdded: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [candidates, setCandidates] = useState<HADevice[] | null>(null);
  const [choice, setChoice] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<ConsentRequest | null>(null);

  // Candidate cameras come from the HA bridge the dashboard already talks to.
  useEffect(() => {
    if (!open || candidates !== null) return;
    getHADevices("camera")
      .then((d) => setCandidates(Array.isArray(d?.devices) ? d.devices : []))
      .catch(() => setCandidates([]));
  }, [open, candidates]);

  const available = (candidates ?? []).filter(
    (d) => d.entity_id.startsWith("camera.") && !existing.includes(d.entity_id)
  );
  const useTypedInput = candidates !== null && available.length === 0;
  const entityId = useTypedInput ? typed.trim() : choice;
  const typedValid = /^camera\.[a-z0-9_]+$/.test(typed.trim());
  const canSubmit = !busy && (useTypedInput ? typedValid : !!choice);

  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setNotice(null);
    setError(null);
    setPending(null);
    try {
      const res = await addDashboardCamera(entityId);
      if (res.kind === "added") {
        setNotice(res.message);
        setChoice("");
        setTyped("");
        setCandidates(null);
        onAdded();
      } else if (res.kind === "needs_permission") {
        // The backend's 403 text is written for the chat agent, not the owner.
        setNotice("Permission needed. Drawbridge asked you about this camera.");
        const reqs = await getConsentRequests().catch((): ConsentRequest[] => []);
        setPending(reqs.find((r) => r.entity_id === entityId) ?? null);
      } else {
        setError(res.message);
      }
    } catch (e: any) {
      setError(e?.message || "Could not reach the backend.");
    } finally {
      setBusy(false);
    }
  };

  const smallFont = {
    fontFamily: NUNITO,
    fontWeight: 600,
    fontSize: 12,
  } as const;

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex cursor-pointer items-center justify-center gap-1.5 rounded-full border py-1.5 transition hover:opacity-80"
        style={{
          ...smallFont,
          borderColor: "var(--line)",
          color: "var(--text3)",
          background: "transparent",
        }}
      >
        <MsIcon name="add" size={15} />
        Add camera
      </button>
    );
  }

  return (
    <div
      className="rounded-2xl border p-2.5"
      style={{ borderColor: "var(--line)", background: "var(--card2)" }}
    >
      <div className="flex items-center gap-2">
        {useTypedInput ? (
          <input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder="camera.entity_id"
            spellCheck={false}
            className="min-w-0 flex-1 rounded-full border px-3 py-1.5 outline-none"
            style={{
              fontFamily: JBM,
              fontSize: 11,
              borderColor: "var(--line)",
              background: "var(--card)",
              color: "var(--text)",
            }}
          />
        ) : (
          <select
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            className="min-w-0 flex-1 rounded-full border px-3 py-1.5 outline-none"
            style={{
              ...smallFont,
              borderColor: "var(--line)",
              background: "var(--card)",
              color: "var(--text)",
            }}
          >
            <option value="">
              {candidates === null ? "Loading cameras…" : "Choose a camera…"}
            </option>
            {available.map((d) => (
              <option key={d.entity_id} value={d.entity_id}>
                {d.friendly_name || d.entity_id}
              </option>
            ))}
          </select>
        )}
        <button
          type="button"
          onClick={submit}
          disabled={!canSubmit}
          className="cursor-pointer rounded-full border-0 px-3.5 py-1.5 transition hover:opacity-90 disabled:opacity-40"
          style={{
            ...smallFont,
            background: "linear-gradient(140deg, var(--pink), var(--magenta))",
            color: "#fff",
          }}
        >
          {busy ? "Adding…" : "Add"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="cursor-pointer border-0 bg-transparent px-1 transition hover:opacity-70"
          style={{ color: "var(--text3)" }}
          aria-label="Close"
        >
          <MsIcon name="close" size={16} />
        </button>
      </div>
      {useTypedInput && typed.trim() !== "" && !typedValid && (
        <p className="mt-1.5" style={{ ...smallFont, fontSize: 11, color: "var(--text3)" }}>
          Entity ids look like <span style={{ fontFamily: JBM }}>camera.front_door</span>.
        </p>
      )}
      {notice && (
        <p className="mt-2" style={{ ...smallFont, color: "var(--lemon)" }}>
          {notice}
        </p>
      )}
      {error && (
        <p className="mt-2" style={{ ...smallFont, color: "var(--puff-red)" }}>
          {error}
        </p>
      )}
      {pending && (
        <div className="mt-2">
          <ConsentRequestRow
            request={pending}
            onResolved={() => {
              setPending(null);
              setNotice(null);
              setCandidates(null);
              onAdded();
            }}
          />
        </div>
      )}
    </div>
  );
}

function CameraFeed({
  label,
  entityId,
  status = "offline",
  fill = false,
  startDelayMs = 0,
  onRemove,
}: {
  label: string;
  entityId?: string;
  status?: "live" | "offline";
  fill?: boolean;
  /** Stagger probes so multiple tiles don't hit the Reolink hub at once */
  startDelayMs?: number;
  /** Omit to hide the remove control (e.g. the offline fallback tile) */
  onRemove?: () => void;
}) {
  const [imgSrc, setImgSrc] = useState<string | null>(null);
  const [streamFailed, setStreamFailed] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const isLive = status === "live" && !!entityId;

  /* Turning a camera off is safety-critical: the backend refuses it with a 409
   * unless the request carries a human-confirmation ticket. This hook wraps the
   * attempt — on a 409 it opens the confirm dialog rather than surfacing an
   * error, and a 403 from the Drawbridge consent gate routes to the existing
   * consent surface instead. */
  const [powerNotice, setPowerNotice] = useState<string | null>(null);
  const confirmPower = useHaConfirmation({
    onResult: (r) => {
      if (r.kind === "ok") setPowerNotice(null);
      else if (r.kind === "failed") setPowerNotice(r.message);
    },
    onConsentNeeded: () =>
      setPowerNotice("Permission needed. Say yes to this camera in Consent requests."),
  });

  // Poll HA camera proxy for snapshots. Battery cams sleep — long probe
  // timeout, staggered starts, and a failed stream retries every 30s.
  useEffect(() => {
    if (!isLive || !entityId) return;
    let cancelled = false;
    let poll: ReturnType<typeof setInterval> | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const fetchSnapshot = () => {
      setImgSrc(`/api/camera/${entityId}?t=${Date.now()}`);
    };

    const probe = () => {
      fetch(`/api/camera/${entityId}`, { signal: AbortSignal.timeout(20000) })
        .then((r) => {
          if (cancelled) return;
          if (r.ok) {
            setStreamFailed(false);
            fetchSnapshot();
            if (poll) clearInterval(poll);
            poll = setInterval(() => {
              if (!cancelled) fetchSnapshot();
            }, CAMERA_POLL_MS);
          } else {
            setStreamFailed(true);
            retry = setTimeout(probe, 30_000);
          }
        })
        .catch(() => {
          if (!cancelled) {
            setStreamFailed(true);
            retry = setTimeout(probe, 30_000);
          }
        });
    };

    const kickoff = setTimeout(probe, startDelayMs);
    return () => {
      cancelled = true;
      clearTimeout(kickoff);
      if (retry) clearTimeout(retry);
      if (poll) clearInterval(poll);
    };
  }, [isLive, entityId, startDelayMs]);

  const showStream = isLive && imgSrc && !streamFailed;

  return (
    <div
      className={`group relative overflow-hidden rounded-[14px] border ${fill ? "h-full" : ""}`}
      style={{
        borderColor: "var(--line)",
        background: showStream ? "var(--puff-surface-deep)" : "transparent",
      }}
    >
      {showStream && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={imgSrc}
          alt={label}
          className={`${fill ? "h-full w-full" : "aspect-video w-full"} object-cover`}
          onError={() => setStreamFailed(true)}
        />
      )}
      {!showStream && (
        /* Design's striped placeholder for offline/unavailable feeds */
        <div
          className={`grid place-items-end ${fill ? "h-full" : "aspect-video"}`}
          style={{ background: STRIPES, padding: 8 }}
        >
          <span
            className="justify-self-start"
            style={{
              fontFamily: JBM,
              fontWeight: 600,
              fontSize: 11,
              letterSpacing: "0.08em",
              color: "var(--text3)",
              textTransform: "uppercase",
            }}
          >
            {isLive ? (streamFailed ? "Stream unavailable" : "Connecting…") : "Offline"}
          </span>
        </div>
      )}
      {/* Caption chip + REC status */}
      {showStream && (
        <div className="absolute inset-x-0 bottom-0 flex items-end justify-between bg-gradient-to-t from-black/60 to-transparent px-2 pb-1.5 pt-4">
          <span
            style={{
              fontFamily: JBM,
              fontWeight: 600,
              fontSize: 11,
              letterSpacing: "0.08em",
              color: "rgba(255,255,255,0.85)",
              textTransform: "uppercase",
            }}
          >
            {label}
          </span>
          {isLive && (
            <span
              className="flex items-center gap-1"
              style={{
                fontFamily: JBM,
                fontWeight: 700,
                fontSize: 9,
                color: "var(--pink)",
                textTransform: "uppercase",
              }}
            >
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ background: "var(--pink)" }}
              />
              REC
            </span>
          )}
        </div>
      )}
      {/* Stop recording. Never fires blind: the backend refuses without a
        * confirmation ticket, and HaConfirmDialog is where one is obtained. */}
      {isLive && entityId && (
        <div className="absolute left-1.5 top-1.5 z-10 flex flex-col items-start gap-1">
          <button
            type="button"
            onClick={() =>
              void confirmPower.run({
                kind: "control",
                entityId,
                action: "turn_off",
              })
            }
            title={`Turn off ${label}, you will be asked first`}
            aria-label={`Turn off ${label}`}
            className="cursor-pointer rounded-md border-0 bg-black/50 p-1 text-white/40 opacity-0 backdrop-blur-sm transition group-hover:opacity-100"
          >
            <MsIcon name="videocam_off" size={13} />
          </button>
          {powerNotice && (
            <span
              className="max-w-[12rem] rounded bg-black/80 px-1.5 py-0.5 backdrop-blur-sm"
              style={{
                fontFamily: NUNITO,
                fontSize: 9,
                lineHeight: 1.3,
                color: "var(--lemon)",
              }}
            >
              {powerNotice}
            </span>
          )}
        </div>
      )}
      <HaConfirmDialog controller={confirmPower} />
      {/* Remove this tile — two-step so a stray click can't drop a camera */}
      {onRemove && (
        <div className="absolute right-1.5 top-1.5 z-10">
          {confirmRemove ? (
            <div className="flex items-center gap-1 rounded-md bg-black/80 px-1.5 py-1 backdrop-blur-sm">
              <span style={{ fontFamily: NUNITO, fontSize: 9, color: "rgba(255,255,255,0.7)" }}>
                Remove tile?
              </span>
              <button
                type="button"
                onClick={() => {
                  setConfirmRemove(false);
                  onRemove();
                }}
                className="cursor-pointer rounded border-0 bg-transparent px-1 uppercase"
                style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 9, color: "var(--pink)" }}
              >
                Yes
              </button>
              <button
                type="button"
                onClick={() => setConfirmRemove(false)}
                className="cursor-pointer rounded border-0 bg-transparent px-1 uppercase"
                style={{ fontFamily: NUNITO, fontSize: 9, color: "rgba(255,255,255,0.5)" }}
              >
                No
              </button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => setConfirmRemove(true)}
              title={`Remove ${label} from the dashboard`}
              aria-label={`Remove ${label} from the dashboard`}
              className="cursor-pointer rounded-md border-0 bg-black/50 p-1 text-white/40 opacity-0 backdrop-blur-sm transition group-hover:opacity-100"
            >
              <MsIcon name="close" size={13} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}
