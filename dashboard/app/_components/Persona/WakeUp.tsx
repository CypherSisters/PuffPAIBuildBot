"use client";

/**
 * The wake-up moment: the first time the dashboard opens after a build, the
 * new bot appears (its icon pops in), then says its intro in its own words,
 * typed out, with "Show me around", which starts the welcome tour
 * (/dashboard?tour=1; without a tour that is simply the home page), and
 * "Not now" (or Escape), which closes it without the tour.
 *
 * Shown once per build: GET /bot/persona says `wake: true` until
 * POST /bot/persona/woke. Setup's "Meet my bot again" opens /dashboard?wake=1.
 * Mounted in the dashboard layout, so it shows on whichever dashboard page
 * opens first after a build. The bot wakes up FIRST: the welcome tour waits
 * while this shows, and "Show me around" fires WAKE_DONE, which starts it.
 */

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import MsIcon from "../MsIcon";
import { useBotAccent } from "./useBotAccent";
import {
  getBotPersona,
  markBotWoke,
  type BotPersona,
} from "@/app/_lib/services/botPersonaService";

export const TOUR_HREF = "/dashboard?tour=1";
/** Fired on window when "Show me around" is pressed: the tour starts. */
export const WAKE_DONE = "puffpai:wake-done";
const TYPE_MS = 28;
const APPEAR_MS = 900;

export function asked(): boolean {
  try {
    return new URLSearchParams(window.location.search).get("wake") === "1";
  } catch {
    return false;
  }
}

function quiet(): boolean {
  try {
    return (
      document.documentElement.classList.contains("puff-no-motion") ||
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  } catch {
    return false;
  }
}

export default function WakeUp() {
  const router = useRouter();
  const pathname = usePathname();
  const [p, setP] = useState<BotPersona | null>(null);
  const [typed, setTyped] = useState(0);
  const checked = useRef(false);
  const accent = useBotAccent(p?.accent);

  // Once when the dashboard opens, and again when a page asks (?wake=1).
  useEffect(() => {
    if (checked.current && !asked()) return;
    let cancelled = false;
    checked.current = true;
    getBotPersona().then((got) => {
      if (!cancelled && got && (got.wake || asked())) setP(got);
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  // Its intro, typed out after it appears.
  useEffect(() => {
    if (!p) return;
    const text = p.intro || "";
    if (quiet()) {
      const id = window.setTimeout(() => setTyped(text.length), 0);
      return () => window.clearTimeout(id);
    }
    let n = 0;
    let timer = 0;
    const start = window.setTimeout(() => {
      timer = window.setInterval(() => {
        n += 1;
        setTyped(n);
        if (n >= text.length) window.clearInterval(timer);
      }, TYPE_MS);
    }, APPEAR_MS);
    return () => {
      window.clearTimeout(start);
      window.clearInterval(timer);
    };
  }, [p]);

  // Escape closes it, the same as "Not now".
  useEffect(() => {
    if (!p) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        markBotWoke();
        setP(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [p]);

  if (!p) return null;
  const intro = p.intro || "";
  const done = typed >= intro.length;

  const showMeAround = () => {
    markBotWoke();
    setP(null);
    window.dispatchEvent(new CustomEvent(WAKE_DONE));
    router.push(TOUR_HREF);
  };

  const notNow = () => {
    markBotWoke();
    setP(null);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${p.name} wakes up`}
      className="fixed inset-0 z-[90] flex items-center justify-center"
      style={{
        background: "color-mix(in srgb, var(--bg, #000) 72%, transparent)",
        backdropFilter: "blur(10px)",
        WebkitBackdropFilter: "blur(10px)",
        animation: "overlay-fade-in 0.4s ease-out",
        padding: 20,
      }}
    >
      <style>{`
        @keyframes bot-wake-pop {
          0% { transform: scale(0.2) rotate(-12deg); opacity: 0; }
          60% { transform: scale(1.15) rotate(4deg); opacity: 1; }
          100% { transform: scale(1) rotate(0); opacity: 1; }
        }
        @keyframes bot-wake-glow {
          0%, 100% { box-shadow: 0 0 0 0 color-mix(in srgb, ${accent} 33%, transparent); }
          50% { box-shadow: 0 0 0 18px transparent; }
        }
        @keyframes bot-wake-caret { 50% { opacity: 0; } }
      `}</style>
      <div
        className="flex w-full flex-col items-center text-center"
        style={{
          maxWidth: 560,
          gap: 18,
          padding: "clamp(22px,3vw,40px)",
          borderRadius: 28,
          background: "var(--card, rgba(255,255,255,0.9))",
          border: `2px solid ${accent}`,
        }}
      >
        <span
          className="flex items-center justify-center"
          style={{
            width: 112,
            height: 112,
            borderRadius: 36,
            background: `color-mix(in srgb, ${accent} 24%, transparent)`,
            color: accent,
            animation: `bot-wake-pop ${APPEAR_MS}ms cubic-bezier(.2,.9,.3,1.3) both, bot-wake-glow 2.4s ease-in-out ${APPEAR_MS}ms infinite`,
          }}
        >
          <MsIcon name={p.icon || "smart_toy"} size={68} fill />
        </span>
        <div
          style={{
            fontFamily: "var(--font-fredoka), sans-serif",
            fontWeight: 600,
            fontSize: "clamp(26px,2.6vw,38px)",
            color: "var(--text)",
          }}
        >
          {p.name}
        </div>
        <p
          aria-live="polite"
          style={{
            margin: 0,
            minHeight: "5.5em",
            fontFamily: "var(--font-nunito), sans-serif",
            fontWeight: 600,
            fontSize: 18,
            lineHeight: 1.5,
            color: "var(--text2)",
          }}
        >
          {intro.slice(0, typed)}
          {!done && (
            <span style={{ animation: "bot-wake-caret 0.8s steps(1) infinite" }}>|</span>
          )}
        </p>
        <button
          type="button"
          onClick={showMeAround}
          className="flex items-center gap-2 transition-transform hover:-translate-y-0.5"
          style={{
            height: 54,
            padding: "0 28px",
            borderRadius: 99,
            border: "none",
            background: accent,
            color: "#fff",
            fontFamily: "var(--font-nunito), sans-serif",
            fontWeight: 800,
            fontSize: 17,
            cursor: "pointer",
            opacity: done ? 1 : 0.85,
          }}
        >
          <MsIcon name="explore" size={22} />
          Show me around
        </button>
        <button
          type="button"
          onClick={notNow}
          style={{
            height: 40,
            padding: "0 20px",
            borderRadius: 99,
            border: "none",
            background: "transparent",
            color: "var(--text2)",
            fontFamily: "var(--font-nunito), sans-serif",
            fontWeight: 700,
            fontSize: 15,
            cursor: "pointer",
          }}
        >
          Not now
        </button>
      </div>
    </div>
  );
}
