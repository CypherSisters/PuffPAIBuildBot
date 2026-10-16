"use client";

/**
 * "Install what's missing": a command failed because a program, a package
 * or a key is not here (src/puffpai/deps.py). The card lists, in plain
 * words, exactly what one Yes installs (each one checked by Drawbridge
 * first), what she installs herself, and the keys it needs with a "Get a
 * key" link and the way to its page, where the key goes into her vault.
 *
 * Shown in the chat's reply bubble and on a thing page's answers.
 *
 * Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
 */

import React, { useState } from "react";
import Link from "next/link";
import type { MissingCardData } from "@/app/_interfaces/api";
import { installMissing } from "@/app/_lib/services/myThingsService";
import { safeLink } from "@/app/_lib/safeLink";

const NUNITO = "var(--font-nunito), sans-serif";
const THING_ID = /^[a-z0-9][a-z0-9_-]{0,100}$/;

export default function MissingCard({ card }: { card: MissingCardData }) {
  const [state, setState] = useState<"ask" | "busy" | "done" | "no">("ask");
  const [lines, setLines] = useState<string[]>([]);

  async function yes() {
    if (state !== "ask" || !card.plan) return;
    setState("busy");
    const reply = await installMissing(card.plan);
    setLines(
      reply.status === 0
        ? ["No answer from this computer."]
        : reply.data.lines && reply.data.lines.length
          ? reply.data.lines
          : [reply.data.message || "That did not work."],
    );
    setState("done");
  }

  const page = THING_ID.test(card.thing) ? `/dashboard/things/${card.thing}` : "";
  const text = { font: `600 13px ${NUNITO}`, color: "var(--text2)" } as const;

  return (
    <div
      role="group"
      aria-label="Install what's missing"
      className="mt-1 flex flex-col gap-2"
      style={{ padding: "12px 14px", borderRadius: 16, background: "var(--card)", border: "1px solid var(--line)" }}
    >
      <div style={{ font: `700 15px ${NUNITO}`, color: "var(--text)" }}>Install what's missing</div>
      {card.line && <div style={text}>{card.line}</div>}
      {card.steps.length > 0 && (
        <ul style={{ ...text, margin: 0, paddingLeft: 18 }}>
          {card.steps.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}
      {card.manual.map((m) => (
        <div key={m} style={text}>
          {m}
        </div>
      ))}
      {card.keys.map((k) => {
        const link = safeLink(k.get_url);
        return (
          <div key={k.name} className="flex flex-wrap items-center gap-2" style={text}>
            <span>It needs your key {k.name}.</span>
            {link && (
              <a href={link} target="_blank" rel="noreferrer noopener" style={{ color: "var(--sky)" }}>
                Get a key
              </a>
            )}
            {page && (
              <Link href={page} style={{ color: "var(--sky)" }}>
                Put it in your vault on its page
              </Link>
            )}
          </div>
        );
      })}
      {card.plan && card.steps.length > 0 && state === "ask" && (
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => void yes()}
            className="cursor-pointer"
            style={{
              font: `700 14px ${NUNITO}`,
              color: "#fff",
              background: "linear-gradient(140deg, var(--pink), var(--magenta))",
              border: "none",
              borderRadius: 99,
              padding: "6px 18px",
            }}
          >
            Yes, install it
          </button>
          <button
            type="button"
            onClick={() => setState("no")}
            className="cursor-pointer"
            style={{
              font: `700 14px ${NUNITO}`,
              color: "var(--text2)",
              background: "var(--card2)",
              border: "1px solid var(--line)",
              borderRadius: 99,
              padding: "6px 18px",
            }}
          >
            Not now
          </button>
        </div>
      )}
      {state === "busy" && <div style={text}>Installing... this can take a few minutes.</div>}
      {state === "no" && <div style={text}>Nothing was installed.</div>}
      {state === "done" && (
        <div role="status" style={{ ...text, display: "grid", gap: 2 }}>
          {lines.map((l, i) => (
            <span key={`${i}-${l}`}>{l}</span>
          ))}
          <span>Ask again to try it now.</span>
        </div>
      )}
    </div>
  );
}
