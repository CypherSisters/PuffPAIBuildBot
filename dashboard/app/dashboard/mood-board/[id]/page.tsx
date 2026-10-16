"use client";

/**
 * The Mood board's own page: the colour themes found in her pictures, and the
 * box to choose (or change) the pictures folder. Opened from its tile on the
 * home page and from the Apps page.
 *
 * Everything shown comes from GET /front (the same tile the home page draws),
 * so the two cannot disagree. Pictures never leave her folder; only colour
 * names and #rrggbb swatches come back.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import MoodBoardFolder from "@/app/_components/Front/MoodBoardFolder";
import { FREDOKA, NUNITO } from "@/app/_components/Widgets/DesignTile";
import { isThisComputer } from "@/app/_lib/cardGrid";
import { getFront, type FrontApp } from "@/app/_lib/services/frontService";

const small = { margin: 0, font: `500 14px/1.5 ${NUNITO}`, color: "var(--text2)" };

export default function MoodBoardPage() {
  const params = useParams<{ id: string }>();
  const id = String(params?.id ?? "");
  const [board, setBoard] = useState<FrontApp | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "missing" | "failed">("loading");
  const [here, setHere] = useState(true);

  const load = useCallback(async () => {
    try {
      const front = await getFront();
      const found = (front.apps ?? []).find((a) => a.id === id && a.kind === "folder_palette") ?? null;
      setBoard(found);
      setState(found ? "ready" : "missing");
    } catch {
      setState("failed");
    }
  }, [id]);

  useEffect(() => {
    setHere(isThisComputer(window.location.hostname));
    void load();
  }, [load]);

  const themes = board?.themes ?? [];
  return (
    <section className="mx-auto w-full max-w-3xl" style={{ padding: "clamp(10px, 1.1vw, 16px)" }}>
      <Link href="/dashboard" style={{ ...small, fontSize: 13, textDecoration: "underline" }}>Back to the home page</Link>
      {state === "loading" && <p style={{ ...small, marginTop: 12 }}>Getting the Mood board from your bot.</p>}
      {state === "failed" && (
        <p style={{ ...small, marginTop: 12 }}>Your bot did not answer, so the Mood board cannot be shown right now.</p>
      )}
      {state === "missing" && (
        <p style={{ ...small, marginTop: 12 }}>This bot has no Mood board with that name. It may have been removed.</p>
      )}
      {board && (
        <div className="mt-3 rounded-[22px] border"
          style={{ borderColor: "var(--line)", background: "var(--card)", padding: "16px 18px" }}>
          <h1 style={{ margin: 0, font: `600 26px ${FREDOKA}`, color: "var(--text)" }}>{board.name}</h1>
          <p style={{ ...small, marginTop: 4 }}>{board.line}</p>
          {themes.length > 0 ? (
            <ul className="m-0 mt-3 grid list-none gap-2 p-0" aria-label="Colour themes from your pictures">
              {themes.map((t) => (
                <li key={t.name} className="flex items-center gap-3">
                  <span className="flex flex-none gap-1">
                    {t.colours.map((c) => (
                      <span key={c} className="inline-block h-6 w-6 rounded-full border"
                        style={{ background: c, borderColor: "var(--line)" }} />
                    ))}
                  </span>
                  <span style={{ font: `600 14px ${NUNITO}`, color: "var(--text)" }}>{t.name}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p style={{ ...small, marginTop: 10 }}>
              No colour themes yet. Choose a pictures folder and your bot looks at the pictures in it.
              It only reads them; it never changes or moves them.
            </p>
          )}
          {here ? (
            <MoodBoardFolder
              appId={board.id}
              label={board.ask_folder ? board.ask_label || "Choose a pictures folder" : "Choose another pictures folder"}
              onChosen={() => void load()}
            />
          ) : (
            <p style={{ ...small, marginTop: 10 }}>{board.elsewhere}</p>
          )}
        </div>
      )}
    </section>
  );
}
