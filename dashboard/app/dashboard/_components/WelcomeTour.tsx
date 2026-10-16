"use client";

/**
 * The welcome tour: the first time the dashboard opens after a build, and
 * again from Setup ("Show me around again"). The bot speaks for itself here
 * (its name, kind and intro come from its personality).
 *
 * The bot wakes up FIRST: while the wake-up shows (WakeUp, a new build or
 * ?wake=1), the tour waits; its "Show me around" starts the tour.
 *
 *   1. "Here is what I just learned": a card per thing with a "put on my home
 *      page" tick (all ticked the first time).
 *   2. "Let's pick my look": 4 to 6 looks that fit the bot's theme, each in
 *      a background. Tapping one wears it on the page (live preview); "Keep
 *      it" saves the look and its background, so the background stays.
 *   3. "Want more?": the Find more search.
 *   Then the home page with her tiles, or first a short walk around the
 *   pages ("Show me around the pages", Walkthrough.tsx).
 *
 * Every sentence comes from the backend (src/puffpai/tour.py).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Button from "@/app/_components/Button";
import MsIcon from "@/app/_components/MsIcon";
import { cleanBoth, cleanVars, lookSwitchedOff, saveLook, wearLook, type LookBothModes } from "@/app/_lib/looks";
import { getLooks, wornVars } from "@/app/_lib/services/looksService";
import { getBotPersona } from "@/app/_lib/services/botPersonaService";
import { WAKE_DONE, asked as wakeAsked } from "@/app/_components/Persona/WakeUp";
import { listThings, setThingHome, type Thing } from "@/app/_lib/services/myThingsService";
import {
  FIND_EXAMPLE,
  getTour,
  getTourLooks,
  keepTourLook,
  startWalk,
  tourDone,
  type TourLook,
  type TourWords,
} from "@/app/_lib/services/tourService";
import { NUNITO, inputStyle } from "@/app/dashboard/apps/_components/ui";

const KIND_ICON: Record<string, string> = {
  skill: "auto_awesome",
  tool_server: "build",
  app: "apps",
  data: "table_chart",
  book: "menu_book",
};

const small: React.CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontSize: 14,
  lineHeight: 1.45,
  color: "var(--text2)",
};

const title: React.CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontSize: 22,
  fontWeight: 800,
  color: "var(--text)",
};

function modeNow(): "day" | "night" {
  if (typeof document === "undefined") return "night";
  return document.documentElement.getAttribute("data-puff-theme") === "day" ? "day" : "night";
}

function ThingIcon({ thing }: { thing: Thing }) {
  const icon = (thing.icon || "").trim();
  if (/^[a-z_]{2,40}$/.test(icon)) return <MsIcon name={icon} size={26} />;
  if (icon && icon.length <= 4) return <span style={{ fontSize: 24 }} aria-hidden>{icon}</span>;
  return <MsIcon name={KIND_ICON[thing.kind] || "extension"} size={26} />;
}

function Dots({ step }: { step: number }) {
  return (
    <div className="flex gap-2" aria-label={`Step ${step + 1} of 3`}>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          style={{
            width: 10,
            height: 10,
            borderRadius: 999,
            background: i === step ? "var(--pink)" : "var(--line)",
          }}
        />
      ))}
    </div>
  );
}

export default function WelcomeTour() {
  const router = useRouter();
  const [words, setWords] = useState<TourWords | null>(null);
  const [step, setStep] = useState(0);
  const [things, setThings] = useState<Thing[]>([]);
  const [ticks, setTicks] = useState<Record<string, boolean>>({});
  const [looks, setLooks] = useState<TourLook[]>([]);
  const [trying, setTrying] = useState<TourLook | null>(null);
  const [kept, setKept] = useState<string>("");
  const [said, setSaid] = useState("");
  const [busy, setBusy] = useState(false);
  const [find, setFind] = useState("");
  // Started from the wake-up, which just said the intro: do not repeat it.
  const [afterWake, setAfterWake] = useState(false);
  const before = useRef<LookBothModes | null>(null);

  useEffect(() => {
    let cancelled = false;
    // *force*: "Show me around" on the wake-up starts it even when the tour
    // was already seen for this build.
    const open = async (force: boolean) => {
      const got = await getTour();
      if (cancelled || !got || !(got.show || force)) return;
      const r = await listThings();
      const mine = r.ok && Array.isArray(r.data) ? r.data.filter((t) => t && typeof t.id === "string") : [];
      if (cancelled) return;
      setThings(mine);
      // All ticked the first time; "Show me around again" starts from her choice.
      const first = got.show && !got.again;
      setTicks(Object.fromEntries(mine.map((t) => [t.id, first ? true : !!t.on_home])));
      setAfterWake(force);
      setStep(0);
      setWords(got);
    };
    const onWake = () => void open(true);
    window.addEventListener(WAKE_DONE, onWake);
    void (async () => {
      // While the bot is waking up, wait for its "Show me around".
      if (wakeAsked()) return;
      const me = await getBotPersona();
      if (cancelled || me?.wake) return;
      await open(false);
    })();
    return () => {
      cancelled = true;
      window.removeEventListener(WAKE_DONE, onWake);
    };
  }, []);

  const goBack = useCallback(() => {
    if (trying) wearLook(lookSwitchedOff() ? null : before.current);
    setTrying(null);
  }, [trying]);

  async function saveTicks() {
    await Promise.all(
      things
        .filter((t) => !!ticks[t.id] !== !!t.on_home)
        .map((t) => setThingHome(t.id, !!ticks[t.id])),
    );
    window.dispatchEvent(new Event("puffpai:things-changed"));
  }

  async function toLooks() {
    setBusy(true);
    await saveTicks();
    try {
      before.current = wornVars(await getLooks());
    } catch {
      before.current = null;
    }
    setLooks(await getTourLooks());
    setStep(1);
    setBusy(false);
  }

  function tryOn(look: TourLook) {
    setTrying(look);
    setSaid("");
    wearLook(cleanBoth(look.vars));
  }

  async function keep() {
    if (!trying) return;
    setBusy(true);
    try {
      const got = await keepTourLook(trying.id, trying.background);
      const both = wornVars(got);
      saveLook(both);
      if (!lookSwitchedOff()) wearLook(both);
      before.current = both;
      setKept(trying.id);
      setTrying(null);
      setSaid(got.message);
    } catch (err) {
      goBack();
      setSaid(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  async function finish(to: string) {
    goBack();
    setBusy(true);
    await tourDone();
    setWords(null);
    setBusy(false);
    router.push(to);
  }

  if (!words) return null;
  // The walk around the pages, offered at the end (Walkthrough runs it).
  const canWalk = !!words.walk_offer && (words.walk?.length ?? 0) > 0;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${words.bot_name} shows you around`}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 80,
        display: "grid",
        placeItems: "center",
        padding: 16,
        background: "rgba(10,6,20,.55)",
        backdropFilter: "blur(4px)",
      }}
    >
      <div
        style={{
          width: "min(860px, 100%)",
          maxHeight: "calc(100dvh - 32px)",
          overflowY: "auto",
          background: "var(--card)",
          color: "var(--text)",
          border: "1px solid var(--line)",
          borderRadius: 22,
          padding: 22,
          display: "grid",
          gap: 14,
          boxShadow: "0 18px 50px rgba(0,0,0,.4)",
        }}
      >
        <div className="flex items-center gap-3">
          <Dots step={step} />
          <div className="flex-1" />
          <button
            type="button"
            onClick={() => void finish("/dashboard")}
            disabled={busy}
            style={{ ...small, background: "none", border: 0, cursor: "pointer", fontWeight: 700 }}
          >
            Skip the tour
          </button>
        </div>

        {step === 0 && (
          <>
            {words.intro && !afterWake && <p style={{ ...small, color: "var(--text)" }}>{words.intro}</p>}
            <h2 style={title}>{words.things_title}</h2>
            <p style={small}>{things.length ? words.things_line : words.things_none}</p>
            <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))" }}>
              {things.map((t) => (
                <div
                  key={t.id}
                  style={{
                    border: "1px solid var(--line)",
                    borderRadius: 16,
                    padding: 12,
                    background: "var(--card2)",
                    display: "grid",
                    gap: 6,
                  }}
                >
                  <div className="flex items-center gap-2">
                    <ThingIcon thing={t} />
                    <strong style={{ fontFamily: NUNITO, fontSize: 15 }}>{t.name}</strong>
                  </div>
                  {t.blurb && <p style={{ ...small, fontSize: 13 }}>{t.blurb}</p>}
                  <label className="flex items-center gap-2" style={{ ...small, fontSize: 13, color: "var(--text)" }}>
                    <input
                      type="checkbox"
                      checked={!!ticks[t.id]}
                      onChange={(e) => setTicks((old) => ({ ...old, [t.id]: e.target.checked }))}
                    />
                    Put on my home page
                  </label>
                </div>
              ))}
            </div>
            <div className="flex justify-end">
              <Button label="Next" color="info" roundedFull disabled={busy} onClick={() => void toLooks()} />
            </div>
          </>
        )}

        {step === 1 && (
          <>
            <h2 style={title}>{words.looks_title}</h2>
            <p style={small}>{words.looks_line} Tap one to see it.</p>
            <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))" }}>
              {looks.map((look) => {
                const on = trying?.id === look.id || (!trying && kept === look.id);
                const vars = cleanVars(look.vars[modeNow()]) as React.CSSProperties;
                return (
                  <button
                    key={look.id}
                    type="button"
                    disabled={busy}
                    onClick={() => tryOn(look)}
                    aria-pressed={on}
                    style={{
                      ...vars,
                      textAlign: "left",
                      cursor: "pointer",
                      borderRadius: 16,
                      padding: 10,
                      border: on ? "3px solid var(--pink)" : "1px solid var(--line)",
                      background: "linear-gradient(160deg, var(--bg) 0%, var(--bg2) 100%)",
                      display: "grid",
                      gap: 6,
                    }}
                  >
                    <div
                      style={{
                        borderRadius: 12,
                        background: "var(--card)",
                        border: "1px solid var(--line)",
                        padding: "8px 10px",
                        display: "grid",
                        gap: 4,
                      }}
                    >
                      <strong style={{ fontFamily: NUNITO, fontSize: 15, color: "var(--text)" }}>{look.name}</strong>
                      <span style={{ fontFamily: NUNITO, fontSize: 12, color: "var(--text2)" }}>
                        {look.background_label} background
                      </span>
                      <span
                        style={{
                          justifySelf: "start",
                          background: "var(--pink)",
                          color: "var(--puff-on-accent)",
                          borderRadius: 999,
                          padding: "2px 10px",
                          fontSize: 12,
                          fontWeight: 700,
                        }}
                      >
                        {kept === look.id && !trying ? "Yours" : "Sample button"}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
            {said && <p style={{ ...small, color: "var(--text)" }}>{said}</p>}
            <div className="flex flex-wrap items-center justify-end gap-2">
              {trying && (
                <>
                  <span style={small}>Trying &ldquo;{trying.name}&rdquo;.</span>
                  <Button label="Keep it" color="info" roundedFull disabled={busy} onClick={() => void keep()} />
                  <Button label="Go back" color="lightDark" roundedFull disabled={busy} onClick={goBack} />
                </>
              )}
              {!trying && (
                <Button
                  label={kept ? "Next" : "Skip for now"}
                  color={kept ? "info" : "lightDark"}
                  roundedFull
                  disabled={busy}
                  onClick={() => setStep(2)}
                />
              )}
            </div>
          </>
        )}

        {step === 2 && (
          <>
            <h2 style={title}>{words.more_title}</h2>
            <p style={small}>{words.more_line}</p>
            <form
              className="flex flex-wrap gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (find.trim()) void finish(`/dashboard/add?find=${encodeURIComponent(find.trim().slice(0, 200))}`);
              }}
            >
              <input
                aria-label={`What should ${words.bot_name} be able to do?`}
                placeholder={words.find_example || FIND_EXAMPLE}
                value={find}
                maxLength={200}
                onChange={(e) => setFind(e.target.value)}
                style={{ ...inputStyle, flex: "1 1 280px", width: "auto" }}
              />
              <Button label="Find more" color="info" roundedFull disabled={busy || !find.trim()} type="submit" />
            </form>
            <p style={small}>{words.done_line}</p>
            {canWalk && <p style={{ ...small, color: "var(--text)" }}>{words.walk_offer}</p>}
            <div className="flex flex-wrap justify-end gap-2">
              {canWalk && (
                <Button label="Show me around the pages" color="info" roundedFull disabled={busy}
                        onClick={() => void finish("/dashboard").then(startWalk)} />
              )}
              <Button label="Go to my home page" color={canWalk ? "lightDark" : "info"} roundedFull disabled={busy}
                      onClick={() => void finish("/dashboard")} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
