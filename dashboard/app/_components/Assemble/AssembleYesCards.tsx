"use client";

/**
 * One Start card per app BuildBot put together that waits for her yes
 * (src/puffpai/assemble/consent.py). Shown on the wizard's last screen and on
 * the app's thing page; the chat shows the same card (FixCard, kind
 * assemble_yes). Nothing of an app is fetched, written, run or started until
 * she taps Start. Every word comes from the backend.
 *
 * puff puff pass
 */

import { useCallback, useEffect, useState } from "react";
import { getYesCards, sayYes, type YesCard } from "@/app/_lib/services/assembleService";

const NUNITO = "var(--font-nunito), sans-serif";
const text = { font: `600 13px ${NUNITO}`, color: "var(--text2)", margin: 0 } as const;
const button = {
  font: `700 14px ${NUNITO}`,
  color: "#fff",
  background: "linear-gradient(140deg, var(--pink), var(--magenta))",
  border: "none",
  borderRadius: 99,
  padding: "6px 18px",
} as const;

function OneCard({ card, onDone }: { card: YesCard; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [line, setLine] = useState("");
  const [done, setDone] = useState(false);

  async function start() {
    if (busy || done) return;
    setBusy(true);
    const got = await sayYes(card);
    setBusy(false);
    setLine(got.line);
    if (got.ok) {
      setDone(true);
      onDone();
    }
  }

  return (
    <div
      role="group"
      aria-label={card.lines[0]}
      className="flex flex-col gap-2"
      style={{ padding: "12px 14px", borderRadius: 16, background: "var(--card)", border: "1px solid var(--line)" }}
    >
      <div style={{ font: `700 15px ${NUNITO}`, color: "var(--text)" }}>{card.lines[0]}</div>
      {card.lines.slice(1).map((l, i) => (
        <p key={i} style={text}>
          {l}
        </p>
      ))}
      {!done && (
        <div className="flex gap-2">
          <button type="button" disabled={busy} onClick={() => void start()} className="cursor-pointer disabled:opacity-50" style={button}>
            {card.button}
          </button>
        </div>
      )}
      {line && (
        <p role="status" style={text}>
          {line}
        </p>
      )}
    </div>
  );
}

export default function AssembleYesCards({ appId, onYes }: { appId?: string; onYes?: () => void }) {
  const [cards, setCards] = useState<YesCard[]>([]);
  const load = useCallback(async () => setCards(await getYesCards(appId)), [appId]);
  useEffect(() => {
    void load();
  }, [load]);
  if (!cards.length) return null;
  return (
    <section className="flex flex-col gap-3" style={{ marginTop: 14 }}>
      {cards.map((c) => (
        <OneCard key={`${c.app_id}-${c.sha}`} card={c} onDone={() => onYes?.()} />
      ))}
    </section>
  );
}
