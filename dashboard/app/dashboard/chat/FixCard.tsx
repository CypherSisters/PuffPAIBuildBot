"use client";

/**
 * "Fix it now": something the bot needed was switched off or not saved yet
 * (src/puffpai/fix_api.py). One card in the reply bubble, with one button:
 *
 *  - switch_on: "Switch on Weather forecast now?" and the server's own
 *    sentence (what leaves, where it goes). The tap is her yes, spent the
 *    same way as the switch on "What leaves your computer".
 *  - setting: "Enter your town now?" with a small input (and a rough
 *    position when this bot keeps everything on this computer).
 *  - key: "Add your <name> key now?" with the Sign-up keys page. A key is
 *    never typed into the chat.
 *
 * After it worked, the chat asks her last question again (onFixed).
 *
 * Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
 */

import React, { useState } from "react";
import Link from "next/link";
import type { FixCardData } from "@/app/_interfaces/api";
import { applyFix } from "@/app/_lib/services/chatService";
import { safeLink } from "@/app/_lib/safeLink";

const NUNITO = "var(--font-nunito), sans-serif";

const button = {
  font: `700 14px ${NUNITO}`,
  color: "#fff",
  background: "linear-gradient(140deg, var(--pink), var(--magenta))",
  border: "none",
  borderRadius: 99,
  padding: "6px 18px",
} as const;

const field = {
  font: `600 14px ${NUNITO}`,
  color: "var(--text)",
  background: "var(--card2)",
  border: "1px solid var(--line)",
  borderRadius: 10,
  padding: "6px 10px",
  minWidth: 0,
} as const;

export default function FixCard({ fix, onFixed }: { fix: FixCardData; onFixed?: () => void }) {
  const [state, setState] = useState<"ask" | "busy" | "done">("ask");
  const [line, setLine] = useState("");
  const [town, setTown] = useState("");
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [position, setPosition] = useState(fix.position);

  const text = { font: `600 13px ${NUNITO}`, color: "var(--text2)" } as const;

  async function go() {
    if (state !== "ask") return;
    if (fix.kind === "setting" && !town.trim()) return;
    setState("busy");
    const got = await applyFix(fix, { town: town.trim(), latitude: lat.trim(), longitude: lon.trim() });
    setLine(got.line);
    if (got.ok) {
      setState("done");
      if (got.again) onFixed?.();
    } else {
      if (got.position) setPosition(true);
      setState("ask");
    }
  }

  const link = fix.kind === "key" ? safeLink(fix.get_url) : "";

  return (
    <div
      role="group"
      aria-label={fix.label}
      className="mt-1 flex flex-col gap-2"
      style={{ padding: "12px 14px", borderRadius: 16, background: "var(--card)", border: "1px solid var(--line)" }}
    >
      <div style={{ font: `700 15px ${NUNITO}`, color: "var(--text)" }}>{fix.label}</div>
      {fix.detail && <div style={text}>{fix.detail}</div>}

      {fix.kind === "key" && (
        <div className="flex flex-wrap items-center gap-3" style={text}>
          {link && (
            <a href={link} target="_blank" rel="noreferrer noopener" style={{ color: "var(--sky)" }}>
              Get a key
            </a>
          )}
          <Link href="/dashboard/keys" style={{ color: "var(--sky)" }}>
            Put it in on the Sign-up keys page
          </Link>
        </div>
      )}

      {fix.kind === "setting" && state !== "done" && (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void go();
          }}
        >
          <input
            aria-label="Your town"
            placeholder="Your town"
            value={town}
            maxLength={100}
            onChange={(e) => setTown(e.target.value)}
            style={{ ...field, flex: "1 1 160px" }}
          />
          {position && (
            <>
              <input
                aria-label="Latitude"
                placeholder="Latitude"
                inputMode="decimal"
                value={lat}
                onChange={(e) => setLat(e.target.value)}
                style={{ ...field, width: 100 }}
              />
              <input
                aria-label="Longitude"
                placeholder="Longitude"
                inputMode="decimal"
                value={lon}
                onChange={(e) => setLon(e.target.value)}
                style={{ ...field, width: 100 }}
              />
            </>
          )}
          <button type="submit" disabled={state === "busy" || !town.trim()} className="cursor-pointer disabled:opacity-50" style={button}>
            Save it
          </button>
        </form>
      )}

      {fix.kind === "switch_on" && state !== "done" && (
        <div className="flex gap-2">
          <button type="button" disabled={state === "busy"} onClick={() => void go()} className="cursor-pointer disabled:opacity-50" style={button}>
            Switch it on
          </button>
        </div>
      )}

      {fix.kind === "assemble_yes" && state !== "done" && (
        <div className="flex gap-2">
          <button type="button" disabled={state === "busy"} onClick={() => void go()} className="cursor-pointer disabled:opacity-50" style={button}>
            Start
          </button>
        </div>
      )}

      {state === "busy" && <div style={text}>Working on it...</div>}
      {line && (
        <div role="status" style={text}>
          {line}
        </div>
      )}
    </div>
  );
}
