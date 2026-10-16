"use client";

/**
 * The walk around the pages: after the welcome tour ("Show me around the
 * pages") and from Settings ("Show me around"). One short step per part of
 * the dashboard: it opens that page and rings its item in the sidebar (or
 * the YOUR PRIVATE STUFF card for the Vault, Brain and Bridge, or one
 * control on the page, marked data-walk="<spot>"), with Back, Next and Skip. Every sentence comes from the backend (tour.walk), said for
 * this bot by name, so it fits any bot.
 *
 * On a small screen the sidebar is hidden; the step card then shows without
 * a ring. A spot that is not on the page (yet, or at all: the vault is
 * already open, say) rings the sidebar item for the page instead; the ring
 * is placed again every 300 ms, so it moves to the spot once the page draws it.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { NUNITO } from "@/app/dashboard/apps/_components/ui";
import { WALK_EVENT, getTour, type WalkStep } from "@/app/_lib/services/tourService";

type Box = { top: number; left: number; width: number; height: number };

const CARD_W = 340;
/** About how tall a step card is, for finding room around the ring. */
const CARD_H = 230;
const PAD = 6;

function railOf(href: string): Element | null {
  return href ? document.querySelector(`aside a[href="${href}"]`) : null;
}

function targetOf(step: WalkStep): Element | null {
  if (step.target === "private") return document.querySelector("aside [data-private-card]");
  if (step.target === "rail") return railOf(step.href);
  if (step.target === "spot") {
    const spot = step.spot ? document.querySelector(`[data-walk="${CSS.escape(step.spot)}"]`) : null;
    return boxOf(spot) ? spot : railOf(step.href);
  }
  return null;
}

function boxOf(el: Element | null): Box | null {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  // Off screen (the hidden drawer on a small screen) or not drawn.
  if (r.width === 0 || r.height === 0 || r.right <= 0 || r.left >= window.innerWidth) return null;
  // A tall section (a whole panel) is ringed at its top only, so the step
  // card has room under it instead of covering it.
  const height = Math.min(r.height, Math.round(window.innerHeight * 0.3));
  return { top: r.top - PAD, left: r.left - PAD, width: r.width + PAD * 2, height: height + PAD * 2 };
}

/** Where the step card goes: beside the ring (right, then left), else under
 * or over it, never on top of it; low in the middle when there is no ring or
 * no room around it. */
function placeCard(box: Box | null): React.CSSProperties {
  const middle: React.CSSProperties = { bottom: 24, left: "50%", transform: "translateX(-50%)" };
  if (!box) return middle;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  // Still scrolling into view: wait in the middle.
  if (box.top >= vh || box.top + box.height <= 0) return middle;
  const w = Math.min(CARD_W, vw - 24);
  const gap = 14;
  const top = Math.max(12, Math.min(box.top - 10, vh - CARD_H - 12));
  if (box.left + box.width + gap + w <= vw - 12) return { top, left: box.left + box.width + gap };
  if (box.left - gap - w >= 12) return { top, left: box.left - gap - w };
  const left = Math.max(12, Math.min(box.left, vw - w - 12));
  if (box.top + box.height + gap + CARD_H <= vh - 12) return { top: box.top + box.height + gap, left };
  if (box.top - gap - CARD_H >= 12) return { top: box.top - gap - CARD_H, left };
  return middle;
}

const smallBtn: React.CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 700,
  fontSize: 13,
  padding: "6px 14px",
  borderRadius: 999,
  border: "1px solid var(--line)",
  background: "var(--card2)",
  color: "var(--text)",
  cursor: "pointer",
};

export default function Walkthrough() {
  const router = useRouter();
  const [steps, setSteps] = useState<WalkStep[]>([]);
  const [at, setAt] = useState(-1);
  const [box, setBox] = useState<Box | null>(null);

  useEffect(() => {
    const start = async () => {
      const got = await getTour();
      const list = got?.walk || [];
      if (!list.length) return;
      setSteps(list);
      setAt(0);
    };
    const onWalk = () => void start();
    window.addEventListener(WALK_EVENT, onWalk);
    return () => window.removeEventListener(WALK_EVENT, onWalk);
  }, []);

  const step = at >= 0 ? steps[at] : null;

  // Open the step's page.
  useEffect(() => {
    if (step?.href) router.push(step.href);
  }, [step, router]);

  // Keep the ring on its item while the page changes, scrolls or resizes.
  useEffect(() => {
    if (!step) return;
    // A spot may be lower down the page: bring it into view once, when it shows.
    let shown = false;
    const place = () => {
      const el = targetOf(step);
      if (!shown && el && step.target === "spot" && el.matches("[data-walk]")) {
        shown = true;
        // A tall section from its top (its ring is its top part), else centred.
        const tall = el.getBoundingClientRect().height > window.innerHeight * 0.3;
        el.scrollIntoView({ block: tall ? "start" : "center", behavior: "smooth" });
      }
      setBox(boxOf(el));
    };
    place();
    const timer = setInterval(place, 300);
    window.addEventListener("resize", place);
    return () => {
      clearInterval(timer);
      window.removeEventListener("resize", place);
    };
  }, [step]);

  const end = useCallback(() => {
    setAt(-1);
    setBox(null);
  }, []);

  useEffect(() => {
    if (!step) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") end();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, end]);

  if (!step) return null;

  const last = at === steps.length - 1;
  const cardStyle = placeCard(box);

  return (
    <div aria-live="polite" data-walk-step={step.id}>
      {box ? (
        <div
          aria-hidden
          style={{
            position: "fixed",
            zIndex: 85,
            top: box.top,
            left: box.left,
            width: box.width,
            height: box.height,
            borderRadius: 16,
            border: "3px solid var(--pink)",
            boxShadow: "0 0 0 9999px rgba(10,6,20,.45)",
            pointerEvents: "none",
            transition: "all .25s ease",
          }}
        />
      ) : (
        <div
          aria-hidden
          style={{ position: "fixed", inset: 0, zIndex: 85, background: "rgba(10,6,20,.25)", pointerEvents: "none" }}
        />
      )}
      <div
        role="dialog"
        aria-label={step.title}
        style={{
          position: "fixed",
          zIndex: 86,
          width: `min(${CARD_W}px, calc(100vw - 24px))`,
          // Solid: the see-through card colour over the page colour, so the
          // page under it never shows through the words.
          background: "linear-gradient(var(--card), var(--card)), var(--bg)",
          color: "var(--text)",
          border: "1px solid var(--line)",
          borderRadius: 18,
          padding: 16,
          display: "grid",
          gap: 8,
          boxShadow: "0 18px 50px rgba(0,0,0,.4)",
          ...cardStyle,
        }}
      >
        <div className="flex items-center" style={{ gap: 8 }}>
          <span style={{ fontFamily: NUNITO, fontSize: 12, fontWeight: 700, color: "var(--text3)" }}>
            {at + 1} of {steps.length}
          </span>
          <div className="flex-1" />
          <button
            type="button"
            onClick={end}
            style={{ background: "none", border: 0, cursor: "pointer", fontFamily: NUNITO, fontSize: 13, fontWeight: 700, color: "var(--text2)" }}
          >
            Skip
          </button>
        </div>
        <strong style={{ fontFamily: NUNITO, fontSize: 18 }}>{step.title}</strong>
        <p style={{ margin: 0, fontFamily: NUNITO, fontSize: 14, lineHeight: 1.45, color: "var(--text2)" }}>{step.line}</p>
        <div className="flex justify-end" style={{ gap: 8, marginTop: 4 }}>
          {at > 0 && (
            <button type="button" style={smallBtn} onClick={() => setAt(at - 1)}>
              Back
            </button>
          )}
          <button
            type="button"
            style={{ ...smallBtn, background: "linear-gradient(140deg, var(--pink), var(--magenta))", color: "#fff", border: 0 }}
            onClick={() => (last ? end() : setAt(at + 1))}
            autoFocus
          >
            {last ? "Done" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
