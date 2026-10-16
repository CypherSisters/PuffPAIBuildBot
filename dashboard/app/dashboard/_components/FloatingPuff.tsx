"use client";

/**
 * FloatingPuff — Puff as a persistent companion on every dashboard page.
 *
 * A draggable bubble that lives in the layout (like JellyfinFrame), so she
 * survives route changes. Tap = expand into a voice panel (the same
 * AvatarTile loop the dashboard tile uses: mic → Vosk → Cortex → Piper with
 * lipsync). Drag = move her; she snaps to the nearest side edge and the spot
 * persists per device.
 *
 * Once the panel has been opened it stays MOUNTED (hidden when collapsed) so
 * the conversation id and engine survive collapse/expand.
 *
 * Hidden on /dashboard/avatar and /dashboard/talk — those pages own the stage.
 *
 * DOCKED MODE (default): the topbar reserves an empty 80px slot marked
 * [data-puff-dock] between the search pill and the clock. Puff parks there in
 * a compact variant so she covers no content on any page. Drag her out (past
 * DOCK_SNAP_PX) to free-float; drop her back near the slot — or double-click
 * her anywhere — to re-dock. The choice persists in `puffpai_float_docked`.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { usePathname } from "next/navigation";
import dynamic from "next/dynamic";
import { mdiClose, mdiBellRing, mdiAlarmSnooze, mdiCheckCircle } from "@mdi/js";
import Icon from "../../_components/Icon";
import MsIcon from "../../_components/MsIcon";
import { useAppSelector } from "@/app/_stores/hooks";
import { useAppData } from "@/app/_lib/hooks/useAppData";
import { usePendingSuggestions } from "@/app/_lib/hooks/usePendingSuggestions";
import { useBuildSelf } from "@/app/_lib/hooks/useBuildSelf";
import { assistantName } from "@/app/_lib/surfaceMap";
import { appAction } from "@/app/_lib/services/appService";
import {
  acceptSuggestion,
  dismissSuggestion,
  isResolved,
  suggestionToIngestResult,
  suggestionToRecipeResult,
  type Suggestion,
} from "@/app/_lib/suggestions";
import type { IngestResult } from "../apps/calendar/ImportEvents";
import type { RecipeResult } from "../apps/freshtable/ImportRecipes";
import {
  ringingItems,
  type AlarmishItem,
} from "@/app/_lib/alarmTime";
import PuffCloudAvatar from "../../_components/Avatar/PuffCloudAvatar";
import type { AvatarEngineHandle } from "../../_components/Avatar/engines/types";

const AvatarTile = dynamic(() => import("../../_components/Avatar/AvatarTile"), {
  ssr: false,
  loading: () => (
    <div className="h-72 animate-pulse rounded-xl bg-[#12121f]" />
  ),
});

/** Only pulled in when a suggestion is actually accepted. */
const ImportEvents = dynamic(() => import("../apps/calendar/ImportEvents"), {
  ssr: false,
});
const ImportRecipes = dynamic(() => import("../apps/freshtable/ImportRecipes"), {
  ssr: false,
});

const EDGE_PAD = 12;
const POS_KEY = "puffpai_float_pos";
const SIZE_KEY = "puffpai_float_size";
const DOCK_KEY = "puffpai_float_docked";
/** Suggestion ids Puff has already said out loud — a reload must not repeat. */
const SPOKEN_KEY = "puffpai_suggestions_spoken";
const SPOKEN_CAP = 40;
// S / M / L bubble diameters; M default. Picked from the panel header.
const SIZES = [96, 136, 184] as const;
/** Release this close to the dock slot (px, rect edge → bubble centre) docks her. */
const DOCK_SNAP_PX = 80;
/** Breathing room inside the topbar so the bubble never touches its edges. */
const DOCK_INSET = 14;
/** Voice-panel width — mirrored here so the docked anchor can keep it on-screen. */
const PANEL_W = 340;

type Pos = { x: number; y: number };
type Rect = { x: number; y: number; w: number; h: number };

/** Her own spot when nobody has moved her: the bottom-right corner, above
 *  the chat box, clear of every page's heading and first cards. Worked out
 *  from the window each time, so a window that starts small and then grows
 *  (a fresh browser, a headless one) never leaves her in the middle of a
 *  page, on top of the greeting and the Vault card. */
function defaultPos(size: number): Pos {
  if (typeof window === "undefined") return { x: 0, y: 0 };
  return {
    x: window.innerWidth - size - EDGE_PAD - 8,
    y: window.innerHeight - size - 96,
  };
}

/** True when the owner dropped her somewhere herself (a saved spot). */
function placedByOwner(): boolean {
  try {
    return Boolean(localStorage.getItem(POS_KEY));
  } catch {
    return false;
  }
}

function clampPos(p: Pos, size: number): Pos {
  if (typeof window === "undefined") return p;
  return {
    x: Math.min(Math.max(p.x, EDGE_PAD), window.innerWidth - size - EDGE_PAD),
    y: Math.min(Math.max(p.y, 64), window.innerHeight - size - EDGE_PAD),
  };
}

export default function FloatingPuff() {
  const pathname = usePathname();
  const speaking = useAppSelector((s) => s.avatar.speaking);
  const listening = useAppSelector((s) => s.avatar.isListening);
  const visible = useAppSelector((s) => s.avatar.visible);

  // ── Alarm awareness: Puff announces what's ringing, on any page ──
  const bubbleRef = useRef<AvatarEngineHandle>(null);
  const { data: alarmData, runAction: alarmAction } =
    useAppData<{ items: AlarmishItem[] }>("alarmflow");
  const announcedRef = useRef<Set<string>>(new Set());
  const armedRef = useRef(false);
  const [ringTick, setRingTick] = useState(0);

  // Re-derive ringing once a second (a timer can cross zero between polls).
  useEffect(() => {
    const iv = setInterval(() => setRingTick((t) => t + 1), 1000);
    return () => clearInterval(iv);
  }, []);
  void ringTick;

  const ringing = ringingItems(alarmData?.items ?? [], Date.now());

  // Announce NEW ringers out loud through her own mouth. Items already
  // ringing when she mounts are seeded silently — same contract as the
  // dashboard chime: a reload must not re-announce leftovers.
  useEffect(() => {
    if (!armedRef.current) {
      ringing.forEach((r) => announcedRef.current.add(r.id));
      armedRef.current = true;
      return;
    }
    for (const item of ringing) {
      if (announcedRef.current.has(item.id)) continue;
      announcedRef.current.add(item.id);
      const line =
        item.mode === "timer"
          ? `${item.desc || "Your timer"} is done!`
          : `${item.desc || "Alarm"}, it's time!`;
      void bubbleRef.current?.speakText(line);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ringing.map((r) => r.id).join("|")]);

  const snoozeRinger = useCallback(
    (id: string) => {
      // Clear the latch so the post-snooze ring announces again.
      announcedRef.current.delete(id);
      void alarmAction("alarm_snooze", { item_id: id, minutes: 9 });
    },
    [alarmAction]
  );

  const dismissRinger = useCallback(
    (id: string) => {
      announcedRef.current.delete(id);
      void alarmAction("alarm_dismiss", { item_id: id });
    },
    [alarmAction]
  );

  /* ── Proactive suggestions ────────────────────────────────────────────
     Something arrived by /send, the backend classified it, and Puff has an
     offer to make. She raises it once, out loud, and the popover carries the
     only two answers: look at it, or file it away. Accepting a calendar
     import opens the SAME review-and-confirm modal a manual file import
     opens — nothing is ever written to the calendar from here. */
  const { suggestions } = usePendingSuggestions();

  /** Whose voice this is. The PRODUCT is still PuffPAI — the wordmark, the
   *  installer and the docs do not change — but the label under the bubble is
   *  the thing SPEAKING, and on a build the owner named Infinity that is
   *  Infinity. Falls back to the product name when nothing was named. */
  const { build } = useBuildSelf();
  const botName = assistantName(build);

  const [resolvedIds, setResolvedIds] = useState<string[]>([]);
  const [accepting, setAccepting] = useState(false);
  /** Set when the server refused/failed to resolve — the card must stay put. */
  const [suggestionError, setSuggestionError] = useState<string | null>(null);
  const [importing, setImporting] = useState<{
    label: string;
    result: IngestResult;
  } | null>(null);
  const [importingRecipe, setImportingRecipe] = useState<{
    label: string;
    result: RecipeResult;
  } | null>(null);

  const spokenRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    try {
      const raw = localStorage.getItem(SPOKEN_KEY);
      if (raw)
        (JSON.parse(raw) as string[]).forEach((id) => spokenRef.current.add(id));
    } catch {
      /* private mode — she may repeat herself once, no worse */
    }
  }, []);

  // One at a time, oldest first. A ringing alarm outranks any suggestion:
  // hold it back entirely (unspoken, so it still gets its moment later).
  const suggestion =
    ringing.length > 0
      ? null
      : (suggestions.find((s) => !resolvedIds.includes(s.id)) ?? null);

  useEffect(() => {
    if (!suggestion) return;
    if (spokenRef.current.has(suggestion.id)) return;
    spokenRef.current.add(suggestion.id);
    try {
      const capped = Array.from(spokenRef.current).slice(-SPOKEN_CAP);
      spokenRef.current = new Set(capped);
      localStorage.setItem(SPOKEN_KEY, JSON.stringify(capped));
    } catch {
      /* private mode */
    }
    void bubbleRef.current?.speakText(suggestion.message);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suggestion?.id]);

  const calRunAction = useCallback(
    (action: string, params?: Record<string, unknown>) =>
      appAction("calendar", action, params),
    []
  );

  const freshRunAction = useCallback(
    (action: string, params?: Record<string, unknown>) =>
      appAction("freshtable", action, params),
    []
  );

  const resolveSuggestion = useCallback((id: string) => {
    setResolvedIds((r) => (r.includes(id) ? r : [...r, id]));
  }, []);

  /* Hide the card only when the SERVER resolved it. A silent local hide was
     the old behaviour and it lied: a 401 looked exactly like success, and the
     same suggestion came back on the next poll with no explanation. */
  const showSuggestion = useCallback(
    async (s: Suggestion) => {
      setAccepting(true);
      setSuggestionError(null);
      const res = await acceptSuggestion(s.id);
      setAccepting(false);
      if (!isResolved(res.status)) {
        setSuggestionError("Couldn’t save that. Tap to try again.");
        return;
      }
      resolveSuggestion(s.id);
      if (res.status === "ok" && res.suggestion) {
        const record = res.suggestion;
        if (record.action?.type === "recipe_import") {
          setImportingRecipe({
            label: record.doc_name || s.doc_name,
            result: suggestionToRecipeResult(record),
          });
        } else {
          setImporting({
            label: record.doc_name || s.doc_name,
            result: suggestionToIngestResult(record),
          });
        }
      }
    },
    [resolveSuggestion]
  );

  const fileSuggestion = useCallback(
    async (s: Suggestion) => {
      setSuggestionError(null);
      const res = await dismissSuggestion(s.id);
      if (!isResolved(res.status)) {
        setSuggestionError("Couldn’t file that. Tap to try again.");
        return;
      }
      resolveSuggestion(s.id);
    },
    [resolveSuggestion]
  );

  // A different suggestion surfacing clears a stale error line.
  useEffect(() => {
    setSuggestionError(null);
  }, [suggestion?.id]);

  const [pos, setPos] = useState<Pos | null>(null);
  const [open, setOpen] = useState(false);
  const [everOpened, setEverOpened] = useState(false);
  const [size, setSize] = useState<number>(SIZES[1]);

  /* ── Topbar dock ──────────────────────────────────────────────────────
     The slot is a plain empty element in the NavBar, so its rect is the whole
     contract. It's static geometry (no grid to chase): measure on mount, on
     window resize, and on the slot's own resize; a 2s interval covers the
     odd reflow (sidebar drawer, font swap) without any coupling. */
  const [dockRect, setDockRect] = useState<Rect | null>(null);
  const dockRectRef = useRef<Rect | null>(null);
  const [docked, setDocked] = useState(true);
  /** True from the first move of a drag until release — the drag always wins
   *  over the dock so she can be pulled out. */
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    dockRectRef.current = dockRect;
  }, [dockRect]);

  useEffect(() => {
    try {
      setDocked(localStorage.getItem(DOCK_KEY) !== "0");
    } catch {
      /* private mode — dock by default */
    }
  }, []);

  useEffect(() => {
    let ro: ResizeObserver | null = null;
    let observed: Element | null = null;
    const measure = () => {
      const el = document.querySelector("[data-puff-dock]");
      // A display:none slot (small screens) reports a zero rect — treat that
      // as "no dock here" so she free-floats instead of collapsing to a dot.
      const r = el?.getBoundingClientRect();
      const next: Rect | null =
        r && r.width >= 24 && r.height >= 24
          ? { x: r.x, y: r.y, w: r.width, h: r.height }
          : null;
      setDockRect((prev) => {
        if (!prev && !next) return prev;
        if (
          prev &&
          next &&
          prev.x === next.x &&
          prev.y === next.y &&
          prev.w === next.w &&
          prev.h === next.h
        )
          return prev;
        return next;
      });
      if (el && el !== observed && ro) {
        if (observed) ro.unobserve(observed);
        ro.observe(el);
        observed = el;
      }
    };
    ro = new ResizeObserver(measure);
    measure();
    const iv = setInterval(measure, 2000);
    window.addEventListener("resize", measure);
    return () => {
      clearInterval(iv);
      window.removeEventListener("resize", measure);
      ro?.disconnect();
    };
  }, [pathname]);

  // Docked geometry: as big as the slot allows, centred in it. The hero
  // pocket is generous (~180-250px) so she reads like the design's floating
  // Puff; anything under ~110px (e.g. a future cramped slot) counts as
  // "compact" and drops the label pill + pulls the rings in.
  const dockSize = dockRect
    ? Math.max(40, Math.min(dockRect.w, dockRect.h - DOCK_INSET))
    : 0;
  const compactDock = dockSize > 0 && dockSize < 110;
  const dockPos: Pos | null = dockRect
    ? {
        x: dockRect.x + (dockRect.w - dockSize) / 2,
        y: dockRect.y + (dockRect.h - dockSize) / 2,
      }
    : null;
  const isDocked = docked && !!dockPos && !dragging;

  const pickSize = useCallback((s: number) => {
    setSize(s);
    setPos((p) =>
      placedByOwner() ? (p ? clampPos(p, s) : p) : clampPos(defaultPos(s), s),
    );
    try {
      localStorage.setItem(SIZE_KEY, String(s));
    } catch {
      /* private mode */
    }
  }, []);

  // Drag bookkeeping in refs — no re-render per pointermove except setPos.
  const dragRef = useRef<{
    startX: number;
    startY: number;
    origX: number;
    origY: number;
    moved: boolean;
  } | null>(null);

  useEffect(() => {
    let startSize: number = SIZES[1];
    try {
      const rawSize = Number(localStorage.getItem(SIZE_KEY));
      if (SIZES.includes(rawSize as (typeof SIZES)[number])) startSize = rawSize;
    } catch {
      /* fresh start */
    }
    setSize(startSize);
    let initial: Pos = defaultPos(startSize);
    try {
      const raw = localStorage.getItem(POS_KEY);
      if (raw) initial = JSON.parse(raw);
    } catch {
      /* fresh start */
    }
    setPos(clampPos(initial, startSize));
    const onResize = () =>
      setPos((p) =>
        placedByOwner()
          ? p ? clampPos(p, sizeRef.current) : p
          : clampPos(defaultPos(sizeRef.current), sizeRef.current),
      );
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Resize listener reads the live size without re-subscribing.
  const sizeRef = useRef(size);
  useEffect(() => {
    sizeRef.current = size;
  }, [size]);

  // Live mirror of the free-float position — the release handler needs it
  // synchronously (a setPos updater is not the place for side effects).
  const posRef = useRef<Pos | null>(null);
  useEffect(() => {
    posRef.current = pos;
  }, [pos]);

  /** Timestamp of the last tap — the double-tap re-dock gesture. */
  const lastTapRef = useRef(0);

  const dockNow = useCallback(() => {
    setDocked(true);
    try {
      localStorage.setItem(DOCK_KEY, "1");
      // A stale manual spot would fight the dock on the next undock.
      localStorage.removeItem(POS_KEY);
    } catch {
      /* private mode */
    }
  }, []);

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      // Starting from the dock, the drag begins where she's actually drawn —
      // but at her free-float size, so she doesn't jump on the first move.
      const start =
        isDocked && dockPos
          ? {
              x: dockPos.x + (dockSize - sizeRef.current) / 2,
              y: dockPos.y + (dockSize - sizeRef.current) / 2,
            }
          : pos;
      if (!start) return;
      (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
      dragRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        origX: start.x,
        origY: start.y,
        moved: false,
      };
    },
    [pos, isDocked, dockPos, dockSize]
  );

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (Math.abs(dx) > 6 || Math.abs(dy) > 6) d.moved = true;
    if (d.moved) {
      setDragging(true);
      const next = clampPos(
        { x: d.origX + dx, y: d.origY + dy },
        sizeRef.current
      );
      posRef.current = next;
      setPos(next);
    }
  }, []);

  const onPointerUp = useCallback(() => {
    const d = dragRef.current;
    dragRef.current = null;
    setDragging(false);
    if (!d) return;
    if (!d.moved) {
      const now = Date.now();
      // Double-tap = "go park yourself". Detected here rather than via
      // ondblclick because this screen is a touchscreen, where dblclick is
      // unreliable. The second tap also undoes the first tap's panel toggle,
      // so a double-tap only re-docks.
      if (now - lastTapRef.current < 500) {
        lastTapRef.current = 0;
        setOpen((o) => !o);
        dockNow();
        return;
      }
      lastTapRef.current = now;
      setOpen((o) => !o);
      setEverOpened(true);
      return;
    }
    const p = posRef.current;
    if (!p) return;
    const s = sizeRef.current;
    const cx = p.x + s / 2;
    const cy = p.y + s / 2;
    const r = dockRectRef.current;
    if (r) {
      // Distance from the bubble's centre to the dock rect (0 when inside).
      const gapX = Math.max(r.x - cx, 0, cx - (r.x + r.w));
      const gapY = Math.max(r.y - cy, 0, cy - (r.y + r.h));
      if (Math.hypot(gapX, gapY) <= DOCK_SNAP_PX) {
        dockNow();
        return;
      }
    }
    // Dropped away from the dock: free-float. Snap to the nearest side edge.
    setDocked(false);
    try {
      localStorage.setItem(DOCK_KEY, "0");
    } catch {
      /* private mode */
    }
    const snapLeft = cx < window.innerWidth / 2;
    const snapped = clampPos(
      { x: snapLeft ? EDGE_PAD : window.innerWidth - s - EDGE_PAD, y: p.y },
      s
    );
    posRef.current = snapped;
    setPos(snapped);
    try {
      localStorage.setItem(POS_KEY, JSON.stringify(snapped));
    } catch {
      /* private mode */
    }
  }, [dockNow]);

  // Cycle S → M → L → S from the resize handle on the bubble itself.
  const cycleSize = useCallback(() => {
    const idx = SIZES.indexOf(sizeRef.current as (typeof SIZES)[number]);
    pickSize(SIZES[(idx + 1) % SIZES.length]);
  }, [pickSize]);

  // The full avatar page owns the stage — don't double-mount her there.
  // The talk page is a dedicated voice stage too.
  // `visible` is the user's avatar on/off switch in settings.
  if (
    pathname === "/dashboard/avatar" ||
    pathname === "/dashboard/talk" ||
    !pos ||
    !visible
  )
    return null;

  const isRinging = ringing.length > 0;

  // Where she's actually drawn this frame: the dock slot, or her saved spot.
  const rPos = isDocked && dockPos ? dockPos : pos;
  const rSize = isDocked && dockPos ? dockSize : size;
  const vw = typeof window !== "undefined" ? window.innerWidth : 0;
  const onLeftHalf = rPos.x + rSize / 2 < vw / 2;

  /** Docked pop-outs hang below the topbar, centred on her but kept on-screen. */
  const belowDock = (width: number) => ({
    top: (dockRect ? dockRect.y + dockRect.h : 0) + 10,
    left: Math.min(
      Math.max(12, rPos.x + rSize / 2 - width / 2),
      Math.max(12, vw - width - 12)
    ),
  });

  return (
    <>
      {/* Bubble */}
      <div
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onDoubleClick={dockNow}
        role="button"
        aria-label={open ? "Collapse Puff" : "Talk to Puff"}
        title={
          isDocked
            ? "Talk to Puff (drag her out to move her)"
            : open
              ? "Collapse Puff"
              : "Talk to Puff (double-click to park her in her spot)"
        }
        data-puff-docked={isDocked ? "1" : "0"}
        className="pointer-events-none fixed z-[70] cursor-grab touch-none select-none transition-transform duration-150 hover:scale-105 active:cursor-grabbing active:scale-95"
        style={{ left: rPos.x, top: rPos.y, width: rSize, height: rSize }}
      >
        {/* Pulse rings from the design: pink inner + sky outer, staggered.
            Docked, they're pulled in so they stay clear of the search pill
            and the clock either side of the slot. */}
        <div
          className="pointer-events-none absolute rounded-full border-2"
          style={{
            inset: isDocked && compactDock ? "-4%" : "-6%",
            borderColor: "var(--pink)",
            animation: "puffPulse 3s ease-out infinite",
          }}
        />
        <div
          className="pointer-events-none absolute rounded-full border-2"
          style={{
            inset: isDocked && compactDock ? "-11%" : "-18%",
            borderColor: "var(--sky)",
            animation: "puffPulse 3s ease-out .9s infinite",
          }}
        />
        {/* Radial glow behind the bubble */}
        <div
          className="pointer-events-none absolute rounded-full"
          style={{
            inset: isDocked && compactDock ? "-14%" : "-24%",
            background: "radial-gradient(circle, var(--glow1), transparent 70%)",
          }}
        />
        {/* Alarm state: red glow ring while something is ringing */}
        {isRinging && (
          <div className="pointer-events-none absolute -inset-1 animate-pulse rounded-full shadow-[0_0_30px_rgba(255,45,60,0.75)]" />
        )}
        {/* The one part of the layer that takes a click or a drag: the round
            cloud itself. The square around it, the glow, the rings and the
            name pill let clicks through to the page underneath. */}
        <div
          data-puff-hit="1"
          className="pointer-events-auto relative h-full w-full overflow-visible rounded-full"
        >
          {/* The SVG engine stays mounted for audio + the speak handle, but
              the visible skin is the real hand-drawn Puff from the design. */}
          <PuffCloudAvatar
            ref={bubbleRef}
            avatarUrl=""
            className="pointer-events-none h-full w-full [&_svg]:invisible"
          />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/puffpai-logo.png"
            alt=""
            className="pointer-events-none absolute inset-0 h-full w-full object-contain"
            style={{
              animation: speaking
                ? "puffTalk .46s ease-in-out infinite"
                : "puffFloat 6s ease-in-out infinite",
              filter: "drop-shadow(0 10px 26px var(--glow1))",
            }}
          />
        </div>
        {/* Docked: the pill would collide with the clock, so her status
            collapses to a single live dot on the bubble's edge. */}
        {isDocked && (
          <span
            className="pointer-events-none absolute h-2.5 w-2.5 rounded-full"
            style={{
              right: "2%",
              bottom: "6%",
              background: speaking
                ? "var(--pink)"
                : listening
                  ? "var(--sky)"
                  : "var(--mint)",
              boxShadow: "0 0 0 2px var(--card)",
              animation: "puffBlink 3s infinite",
            }}
          />
        )}
        {/* Label pill: blink dot + wordmark + live state. Anchored to the
            screen-inner side so it never clips when snapped to an edge. */}
        {(!isDocked || !compactDock) && (
        <div
          className="pointer-events-none absolute flex items-center gap-2 whitespace-nowrap rounded-full px-4 py-2"
          style={{
            bottom: "-12%",
            // 44px inset on the handle side keeps the pill clear of the
            // resize handle at the bubble's bottom-right corner.
            ...(onLeftHalf ? { left: 0 } : { right: 44 }),
            background: "var(--card)",
            border: "1px solid var(--line)",
            backdropFilter: "blur(14px)",
            WebkitBackdropFilter: "blur(14px)",
          }}
        >
          <span
            className="h-2 w-2 rounded-full"
            style={{
              background: "var(--mint)",
              animation: "puffBlink 3s infinite",
            }}
          />
          <span
            style={{
              font: "700 14px var(--font-nunito), sans-serif",
              color: "var(--text)",
            }}
          >
            {botName}
          </span>
          <span
            style={{
              font: "500 13px var(--font-nunito), sans-serif",
              color: "var(--text3)",
            }}
          >
            {speaking ? "speaking" : listening ? "listening" : "idle"}
          </span>
        </div>
        )}
        {/* Resize handle: cycles S / M / L. Docked, the slot dictates her
            size, so the control would lie — hide it. */}
        {!isDocked && (
        <button
          type="button"
          aria-label="Resize Puff"
          title="Resize Puff (S / M / L)"
          onPointerDown={(e) => e.stopPropagation()}
          onPointerUp={(e) => e.stopPropagation()}
          onClick={cycleSize}
          className="pointer-events-auto absolute grid h-9 w-9 cursor-pointer place-items-center rounded-full transition hover:scale-110"
          style={{
            right: "2%",
            bottom: "2%",
            background: "var(--card)",
            border: "1px solid var(--line)",
            backdropFilter: "blur(14px)",
            WebkitBackdropFilter: "blur(14px)",
            color: "var(--text2)",
          }}
        >
          <MsIcon name="open_in_full" size={19} className="pointer-events-none" />
        </button>
        )}
        {isRinging && (
          <span className="absolute -right-1 -top-1 flex h-6 w-6 items-center justify-center rounded-full bg-(--puff-red) text-(--puff-on-accent) shadow-lg">
            <Icon path={mdiBellRing} size="14" />
          </span>
        )}
      </div>

      {/* Docked, the wordmark pill is gone — so while she's actually talking
          or hearing, a small state pill drops below the topbar and leaves
          again. Never while idle: the topbar stays quiet by default. */}
      {isDocked && (speaking || listening) && (
        <div
          className="pointer-events-none fixed z-[71] flex items-center gap-2 whitespace-nowrap rounded-full px-3 py-1.5"
          style={{
            ...belowDock(132),
            background: "var(--card)",
            border: "1px solid var(--line)",
            backdropFilter: "blur(14px)",
            WebkitBackdropFilter: "blur(14px)",
          }}
        >
          <span
            className="h-2 w-2 rounded-full"
            style={{
              background: speaking ? "var(--pink)" : "var(--sky)",
              animation: "puffBlink 3s infinite",
            }}
          />
          <span
            style={{
              font: "600 12px var(--font-nunito), sans-serif",
              color: "var(--text2)",
            }}
          >
            {speaking ? "speaking" : "listening"}
          </span>
        </div>
      )}

      {/* Ringing popover — snooze/dismiss right at the bubble, any page */}
      {isRinging && (
        <div
          className="fixed z-[71] w-60"
          style={
            isDocked
              ? belowDock(240)
              : {
                  top: Math.max(64, rPos.y - 8 - 64),
                  ...(onLeftHalf
                    ? { left: rPos.x }
                    : { right: vw - rPos.x - rSize }),
                }
          }
        >
          <div className="rounded-xl border border-(--puff-red)/40 bg-[#1a0a10]/95 p-2 shadow-2xl backdrop-blur-md">
            {ringing.slice(0, 3).map((item) => (
              <div key={item.id} className="flex items-center gap-2 px-1 py-1">
                <span className="min-w-0 flex-1 truncate text-xs font-semibold text-(--puff-text-strong)">
                  {item.desc || (item.mode === "timer" ? "Timer" : "Alarm")}
                </span>
                <button
                  onClick={() => snoozeRinger(item.id)}
                  title="Snooze 9 minutes"
                  className="flex items-center gap-1 rounded-lg bg-(--lemon)/20 px-2 py-1 text-[11px] font-semibold text-(--lemon) transition hover:bg-(--lemon)/35"
                >
                  <Icon path={mdiAlarmSnooze} size="12" />
                  9m
                </button>
                <button
                  onClick={() => dismissRinger(item.id)}
                  title="Dismiss"
                  className="flex items-center gap-1 rounded-lg bg-(--mint)/20 px-2 py-1 text-[11px] font-semibold text-(--mint) transition hover:bg-(--mint)/35"
                >
                  <Icon path={mdiCheckCircle} size="12" />
                  OK
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Suggestion popover — same anchor as the ring banner, calmer skin.
          Two answers only; "Show me" opens the review modal, never the
          calendar. Hidden while the import modal is up. */}
      {suggestion && !importing && !importingRecipe && (
        <div
          className="fixed z-[71] w-72 max-w-[calc(100vw-24px)]"
          style={
            isDocked
              ? belowDock(288)
              : {
                  top: Math.max(64, rPos.y - 8 - 96),
                  ...(onLeftHalf
                    ? { left: rPos.x }
                    : { right: vw - rPos.x - rSize }),
                }
          }
        >
          <div
            className="rounded-2xl p-3 shadow-2xl"
            style={{
              background: "var(--card)",
              border: "1px solid var(--line)",
              backdropFilter: "blur(18px)",
              WebkitBackdropFilter: "blur(18px)",
            }}
          >
            <p
              style={{
                font: "500 13px/1.45 var(--font-nunito), sans-serif",
                color: "var(--text)",
              }}
            >
              {suggestion.message}
            </p>
            {/* Honest failure: the card stays, and says why it stayed. */}
            {suggestionError && (
              <p
                role="alert"
                className="mt-2"
                style={{
                  font: "500 12px/1.4 var(--font-nunito), sans-serif",
                  color: "var(--text3)",
                }}
              >
                {suggestionError}
              </p>
            )}
            <div className="mt-2.5 flex items-center justify-end gap-2">
              {suggestion.action?.type === "calendar_import" ||
              suggestion.action?.type === "recipe_import" ? (
                <>
                  <button
                    onClick={() => void fileSuggestion(suggestion)}
                    style={{
                      padding: "6px 12px",
                      borderRadius: 12,
                      border: "1px solid var(--line)",
                      background: "var(--card2)",
                      color: "var(--text2)",
                      font: "600 12px var(--font-nunito), sans-serif",
                    }}
                  >
                    Just file it
                  </button>
                  <button
                    onClick={() => void showSuggestion(suggestion)}
                    disabled={accepting}
                    className="transition disabled:opacity-50"
                    style={{
                      padding: "6px 14px",
                      borderRadius: 12,
                      border: "1px solid transparent",
                      background:
                        "linear-gradient(135deg, var(--pink), var(--magenta))",
                      color: "#fff",
                      font: "600 12px var(--font-nunito), sans-serif",
                    }}
                  >
                    {accepting ? "Opening…" : "Show me"}
                  </button>
                </>
              ) : (
                <button
                  onClick={() => void fileSuggestion(suggestion)}
                  style={{
                    padding: "6px 14px",
                    borderRadius: 12,
                    border: "1px solid var(--line)",
                    background: "var(--card2)",
                    color: "var(--text)",
                    font: "600 12px var(--font-nunito), sans-serif",
                  }}
                >
                  OK
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Accepted import — the ordinary preview-confirm modal, per event. */}
      {importing && (
        <ImportEvents
          runAction={calRunAction}
          initialResult={importing.result}
          initialLabel={importing.label}
          onClose={() => setImporting(null)}
        />
      )}

      {/* Accepted recipe import — same preview-confirm, per recipe. */}
      {importingRecipe && (
        <ImportRecipes
          runAction={freshRunAction}
          initialResult={importingRecipe.result}
          initialLabel={importingRecipe.label}
          onClose={() => setImportingRecipe(null)}
        />
      )}

      {/* Voice panel — mounted after first open, hidden (not unmounted) on
          collapse so the conversation and engine survive. */}
      {everOpened && (
        <div
          className={`fixed z-[69] w-[340px] max-w-[calc(100vw-24px)] ${open ? "" : "pointer-events-none invisible"}`}
          style={
            isDocked
              ? // Hangs off the topbar under her, clamped so the right edge
                // of the screen can't clip it.
                belowDock(PANEL_W)
              : {
                  top: Math.min(
                    rPos.y,
                    typeof window !== "undefined"
                      ? window.innerHeight - 480
                      : rPos.y
                  ),
                  ...(onLeftHalf
                    ? { left: rPos.x + rSize + 10 }
                    : { right: vw - rPos.x + 10 }),
                }
          }
        >
          <div className="cyber-card overflow-hidden rounded-2xl bg-[#0a0a18]/95 shadow-2xl backdrop-blur-md">
            <div className="flex items-center justify-between px-3 py-2">
              <span className="text-xs font-semibold uppercase tracking-widest text-[#b44dff]">
                Puff
              </span>
              <div className="flex items-center gap-1">
                {/* Bubble size: S / M / L — only meaningful free-floating;
                    docked, the topbar slot sets her size. */}
                {!isDocked &&
                  SIZES.map((s, i) => (
                  <button
                    key={s}
                    onClick={() => pickSize(s)}
                    aria-label={`Puff size ${["small", "medium", "large"][i]}`}
                    className={`rounded px-1.5 py-0.5 text-[10px] font-semibold transition ${
                      size === s
                        ? "bg-[#b44dff]/20 text-[#b44dff]"
                        : "text-[#8899aa] hover:text-(--puff-text-strong)"
                    }`}
                  >
                    {["S", "M", "L"][i]}
                  </button>
                ))}
                <button
                  onClick={() => setOpen(false)}
                  aria-label="Collapse Puff"
                  className="ml-1 rounded p-1 text-[#8899aa] transition hover:text-(--puff-text-strong)"
                >
                  <Icon path={mdiClose} size="16" />
                </button>
              </div>
            </div>
            <AvatarTile />
          </div>
        </div>
      )}
    </>
  );
}
