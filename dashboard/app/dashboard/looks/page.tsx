"use client";

/**
 * Looks: "make it look like outer space".
 *
 * The owner describes a look; the backend finds some (free look lists, the
 * bot's brain if asked, or the colours the owner named) and writes every
 * colour and every sentence. Each result is a small sample card wearing its
 * own colours on that card only. "Try it on" wears it on this page without
 * saving; "Keep it" asks the backend to use it (by id only) and saves it for
 * this browser; "Go back" returns to what was in use. Undo is one step.
 * The background row (Clouds / Stars / Plain / Soft colour) changes only the
 * background of the look in use, at once, and is undone the same way. The
 * background she picks is remembered (chosen_background): every look she then
 * tries on or keeps wears it, so "Keep it" never brings the clouds back.
 *
 * The "Trying" bar uses fixed colours on purpose, so its buttons stay readable
 * whatever look is being tried.
 */

import { useCallback, useEffect, useState } from "react";
import Button from "@/app/_components/Button";
import {
  cleanBoth,
  cleanVars,
  lookSwitchedOff,
  saveLook,
  wearLook,
  type LookBothModes,
} from "@/app/_lib/looks";
import {
  chooseBackground,
  findLooks,
  getLooks,
  wornVars,
  resetLook,
  undoLook,
  chooseLook as keepLook,
  chosenLabel,
  type FoundLook,
  type LookSummary,
  type LooksState,
} from "@/app/_lib/services/looksService";
import {
  AppHeader,
  AppPage,
  NUNITO,
  Panel,
  SectionBar,
  inputStyle,
  wellStyle,
} from "@/app/dashboard/apps/_components/ui";

const small: React.CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontSize: 13,
  lineHeight: 1.45,
  color: "var(--text2)",
};

function modeNow(): "day" | "night" {
  if (typeof document === "undefined") return "night";
  return document.documentElement.getAttribute("data-puff-theme") === "day" ? "day" : "night";
}

function Swatch({ colours }: { colours: string[] }) {
  return (
    <div className="flex gap-1" aria-hidden>
      {colours.filter((c) => /^#[0-9a-f]{6}$/.test(c)).map((c, i) => (
        <span
          key={`${c}-${i}`}
          style={{
            width: 18,
            height: 18,
            borderRadius: 6,
            background: c,
            border: "1px solid rgba(128,128,128,.35)",
          }}
        />
      ))}
    </div>
  );
}

/** A sample card wearing the look's own colours, on this card only. */
function LookTile({
  look,
  busy,
  trying,
  onTry,
}: {
  look: FoundLook;
  busy: boolean;
  trying: boolean;
  onTry: () => void;
}) {
  const vars = cleanVars(look.vars[modeNow()]) as React.CSSProperties;
  return (
    <div
      style={{
        ...vars,
        borderRadius: 18,
        border: "1px solid var(--line)",
        background: "linear-gradient(160deg, var(--bg) 0%, var(--bg2) 100%)",
        padding: 12,
        display: "grid",
        gap: 8,
      }}
    >
      <div
        style={{
          borderRadius: "var(--puff-radius-card, 16px)",
          background: "var(--card)",
          border: "1px solid var(--line)",
          padding: "10px 12px",
          display: "grid",
          gap: 6,
        }}
      >
        <div className="flex items-center gap-2">
          <strong style={{ fontFamily: NUNITO, fontSize: 15, color: "var(--text)" }}>
            {look.name}
          </strong>
          <div className="flex-1" />
          <Swatch colours={look.swatch[modeNow()]} />
        </div>
        <p style={{ ...small, color: "var(--text2)" }}>{look.colours_line}</p>
        <p style={{ ...small, color: "var(--text3)", fontSize: 12 }}>
          {look.why} {look.background_line}
        </p>
        {look.background_note && (
          <p style={{ ...small, color: "var(--text2)", fontSize: 12 }}>{look.background_note}</p>
        )}
        <div className="flex items-center gap-2">
          <span
            style={{
              background: "var(--pink)",
              color: "var(--puff-on-accent)",
              borderRadius: "var(--puff-radius-button, 999px)",
              padding: "2px 10px",
              fontSize: 12,
              fontWeight: 700,
            }}
          >
            Sample button
          </span>
          <span style={{ color: "var(--pink)", fontSize: 12, fontWeight: 700 }}>
            Coloured words
          </span>
        </div>
      </div>
      {look.one_version && (
        <p style={{ ...small, fontSize: 12, color: "var(--text2)" }}>
          It has one set of colours, used day and night.
        </p>
      )}
      {look.adjusted.map((note) => (
        <p key={note} style={{ ...small, fontSize: 12, color: "var(--text2)" }}>
          {note}
        </p>
      ))}
      <div>
        <Button
          label={trying ? "Trying it now" : "Try it on"}
          color="info"
          small
          roundedFull
          disabled={busy || trying}
          onClick={onTry}
        />
      </div>
    </div>
  );
}

export default function LooksPage() {
  const [state, setState] = useState<LooksState | null>(null);
  const [words, setWords] = useState("");
  const [brain, setBrain] = useState(false);
  const [found, setFound] = useState<FoundLook[]>([]);
  const [note, setNote] = useState("");
  const [trying, setTrying] = useState<FoundLook | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const inUse = useCallback((s: LooksState | null): LookBothModes | null => wornVars(s), []);

  const load = useCallback(async () => {
    try {
      const s = await getLooks();
      setState(s);
      return s;
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "This page did not load.");
      return null;
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const settle = useCallback(
    (s: LooksState | null, said: string) => {
      if (s) {
        setState(s);
        const both = inUse(s);
        saveLook(both);
        if (!lookSwitchedOff()) wearLook(both);
      }
      setTrying(null);
      setMessage(said);
    },
    [inUse],
  );

  const onFind = async () => {
    if (!words.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      const got = await findLooks(words, brain);
      setFound(got.looks || []);
      setNote(got.note || "");
    } catch (err) {
      setFound([]);
      setNote(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  const onTry = (look: FoundLook) => {
    setTrying(look);
    wearLook(cleanBoth(look.vars));
  };

  const onGoBack = () => {
    wearLook(lookSwitchedOff() ? null : inUse(state));
    setTrying(null);
  };

  const onKeep = async () => {
    if (!trying) return;
    setBusy(true);
    try {
      const got = await keepLook(trying.id);
      settle(got, got.message);
    } catch (err) {
      onGoBack();
      setMessage(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  const onBackground = async (id: string) => {
    setBusy(true);
    try {
      const got = await chooseBackground(id);
      settle(got, got.message);
      if (found.length && words.trim()) {
        // The looks found before wear the old background: dress them again,
        // so trying one on shows exactly what keeping it gives.
        const again = await findLooks(words, brain);
        setFound(again.looks || []);
        setNote(again.note || "");
      }
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  const onUndo = async () => {
    setBusy(true);
    try {
      const got = await undoLook();
      settle(got, got.message);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  const onReset = async () => {
    setBusy(true);
    try {
      const got = await resetLook();
      settle(got, got.message);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  };

  const current: LookSummary | null = state ? state.current : null;

  return (
    <AppPage style={{ paddingBottom: trying ? 96 : undefined }}>
      <AppHeader
        title="Looks"
        lede="Say how you would like your bot to look. Nothing changes until you pick one."
      />

      <Panel>
        <div style={{ display: "grid", gap: 8 }}>
          {current ? (
            <>
              <div className="flex flex-wrap items-center gap-3">
                <strong style={{ fontFamily: NUNITO, fontSize: 15, color: "var(--text)" }}>
                  Your bot uses the look &ldquo;{current.name}&rdquo;
                </strong>
                <Swatch colours={current.swatch[modeNow()]} />
              </div>
              <p style={small}>{current.colours_line}. {current.from_line}</p>
            </>
          ) : (
            <strong style={{ fontFamily: NUNITO, fontSize: 15, color: "var(--text)" }}>
              Your bot uses its own look.
            </strong>
          )}
          {state && state.backgrounds && (
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Background">
              <span style={{ ...small, color: "var(--text)", fontWeight: 700 }}>Background:</span>
              {state.backgrounds.map((b) => {
                const on = state.background === b.id;
                return (
                  <button
                    key={b.id}
                    type="button"
                    aria-pressed={on}
                    disabled={busy || !!trying}
                    onClick={() => !on && onBackground(b.id)}
                    style={{
                      fontFamily: NUNITO,
                      fontSize: 13,
                      fontWeight: 700,
                      padding: "3px 12px",
                      borderRadius: "var(--puff-radius-button, 999px)",
                      border: "1px solid var(--line)",
                      background: on ? "var(--pink)" : "var(--card2)",
                      color: on ? "var(--puff-on-accent)" : "var(--text)",
                      cursor: on ? "default" : "pointer",
                    }}
                  >
                    {b.label}
                  </button>
                );
              })}
            </div>
          )}
          {chosenLabel(state) && (
            <p style={small}>
              Every look you try on or keep wears the {chosenLabel(state).toLowerCase()} background.
            </p>
          )}
          {state && <p style={small}>{state.pages_line} {state.pages_own}</p>}
          <div className="flex flex-wrap gap-2">
            {state && state.can_undo && (
              <Button label="Undo last change" color="lightDark" small roundedFull
                      disabled={busy} onClick={onUndo} />
            )}
            {(current || (state && state.background && state.background !== "clouds")) && (
              <Button label="Back to the bot's own look" color="lightDark" small roundedFull
                      disabled={busy} onClick={onReset} />
            )}
          </div>
          {message && <p style={{ ...small, color: "var(--text)" }}>{message}</p>}
        </div>
      </Panel>

      <SectionBar title="Find a look" />
      <div style={{ ...wellStyle, display: "grid", gap: 8 }}>
        <label htmlFor="look-words" style={small}>
          Describe a look, like &ldquo;outer space&rdquo;, &ldquo;cozy yarn shop&rdquo; or
          &ldquo;clean and dark&rdquo;.
        </label>
        <div className="flex flex-wrap gap-2">
          <input
            id="look-words"
            value={words}
            maxLength={200}
            onChange={(e) => setWords(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onFind();
            }}
            style={{ ...inputStyle, flex: "1 1 260px", width: "auto" }}
          />
          <Button label="Find looks" color="info" roundedFull disabled={busy || !words.trim()}
                  onClick={onFind} />
        </div>
        <label className="flex items-center gap-2" style={small}>
          <input type="checkbox" checked={brain} onChange={(e) => setBrain(e.target.checked)} />
          Also let the bot&apos;s brain pick one and make one from your words (takes a little longer)
        </label>
      </div>

      {note && <p style={small}>{note}</p>}
      {found.length > 0 && (
        <div
          style={{
            display: "grid",
            gap: 12,
            gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
          }}
        >
          {found.map((look) => (
            <LookTile
              key={look.id}
              look={look}
              busy={busy}
              trying={!!trying && trying.id === look.id}
              onTry={() => onTry(look)}
            />
          ))}
        </div>
      )}

      {trying && (
        <div
          role="status"
          style={{
            position: "fixed",
            left: "50%",
            bottom: 18,
            transform: "translateX(-50%)",
            zIndex: 60,
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "10px 14px",
            borderRadius: 999,
            background: "#150C24",
            color: "#F7EEF8",
            border: "1px solid rgba(255,255,255,.25)",
            boxShadow: "0 12px 30px rgba(0,0,0,.45)",
            fontFamily: NUNITO,
            fontSize: 14,
          }}
        >
          <span>Trying &ldquo;{trying.name}&rdquo;.</span>
          <button
            type="button"
            disabled={busy}
            onClick={onKeep}
            style={{
              background: "#F7EEF8",
              color: "#150C24",
              borderRadius: 999,
              padding: "4px 14px",
              fontWeight: 700,
            }}
          >
            Keep it
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onGoBack}
            style={{
              background: "transparent",
              color: "#F7EEF8",
              border: "1px solid rgba(247,238,248,.6)",
              borderRadius: 999,
              padding: "4px 14px",
              fontWeight: 700,
            }}
          >
            Go back
          </button>
        </div>
      )}
    </AppPage>
  );
}
