"use client";

/**
 * The bot's live cards on its home page, as a dense grid of tiles: pictures
 * get room (a gallery shows several), lists are tall enough to read, numbers
 * are compact. Like the app tiles on a HomeBot home page, but made only of
 * what THIS bot has.
 *
 * Order is the owner's placement list, then the order the cards were added
 * (app/_lib/cardGrid.ts). Each tile reads its card from the backend
 * (GET /apps/<id>/feed through the paired proxy); pictures come only from the
 * backend's own picture route. Every tile shows its credit line and opens the
 * card's full page. Colours are the theme's variables only, so a look the
 * owner picks recolours it.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import Link from "next/link";
import AppLink from "@/app/dashboard/apps/_lib/AppLink";
import { useEffect, useState } from "react";
import FeedCardBody from "../Feed/FeedCardBody";
import { useBuildStatus } from "@/app/_lib/hooks/useBuildStatus";
import { feedThingId, placedThings } from "@/app/_lib/buildJobs";
import MoodBoardFolder from "./MoodBoardFolder";
import { FREDOKA, JBM, NUNITO } from "../Widgets/DesignTile";
import {
  FEED_SOON_MS, TILE_PICTURES, feedPollMs, isThisComputer, orderCards, tileClasses, tileSize,
} from "@/app/_lib/cardGrid";
import { apiPost } from "@/app/_lib/api";
import type { FrontApp, FrontCard, FrontPageData } from "@/app/_lib/services/frontService";
import { agePhrase, getFeed, type FeedState } from "@/app/_lib/services/feedService";

const MICRO = {
  font: `700 10px ${JBM}`,
  letterSpacing: "0.1em",
  textTransform: "uppercase" as const,
  color: "var(--text3)",
};

function CardTile({ card }: { card: FrontCard }) {
  const [state, setState] = useState<FeedState | null>(null);

  useEffect(() => {
    if (card.status === "off") return undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let soon = 0;
    // A card switched on a moment ago has its first fetch running on the box:
    // read it again within seconds until it has data (feedPollMs), then slowly.
    const load = () => {
      let got: FeedState | null = null;
      getFeed(card.app_id)
        .then((s) => {
          got = s;
          if (!cancelled) setState(s);
        })
        .catch(() => undefined)
        .finally(() => {
          if (cancelled) return;
          const wait = feedPollMs(got, soon);
          if (wait === FEED_SOON_MS) soon += 1;
          timer = setTimeout(load, wait);
        });
    };
    load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [card.app_id, card.status]);

  const shown = state?.card ?? null;
  const size = tileSize(shown?.kind ?? card.kind);
  const age = state?.age_minutes != null ? agePhrase(state.age_minutes) : "";
  const dot =
    card.status === "on" ? "var(--mint)" : card.status === "problem" ? "var(--puff-red)" : "var(--text3)";

  return (
    <li className={`min-w-0 ${tileClasses(size)}`}>
      <AppLink
        href={card.href}
        className="flex h-full min-h-0 flex-col rounded-[22px] border transition hover:opacity-90"
        style={{ borderColor: "var(--line)", background: "var(--card2)", padding: "12px 14px 10px" }}
        aria-label={`${card.name}, open its page`}
      >
        <div className="mb-2 flex min-w-0 items-center gap-2">
          <span className="inline-block h-1.5 w-1.5 flex-none rounded-full" style={{ background: dot }} />
          <span className="truncate" style={{ font: `600 16px ${FREDOKA}`, color: "var(--text)" }}>
            {card.name}
          </span>
          <span className="flex-1" />
          {age && <span className="flex-none" style={MICRO}>{age}</span>}
        </div>
        <div className="min-h-0 flex-1 overflow-hidden">
          {shown ? (
            <FeedCardBody appId={card.app_id} card={shown} compact pictureLimit={TILE_PICTURES} />
          ) : (
            <p style={{ margin: 0, font: `500 13px/1.45 ${NUNITO}`, color: "var(--text2)" }}>
              {state?.message || card.line}
            </p>
          )}
        </div>
        <div className="mt-2 flex items-baseline gap-2" style={{ borderTop: "1px solid var(--line)", paddingTop: 6 }}>
          <span className="min-w-0 flex-1 truncate" style={{ font: `600 11px ${NUNITO}`, color: "var(--text3)" }}>
            {state?.credit || card.credit}
          </span>
          <span className="flex-none" style={MICRO}>Open</span>
        </div>
      </AppLink>
    </li>
  );
}

const tileStyle = {
  borderColor: "var(--line)",
  background: "var(--card2)",
  padding: "12px 14px 10px",
};

/** One thing Toolsmith added. On this computer it opens the app; on a phone
 *  or another screen it says where to open it. */
function AppTile({ app, here }: { app: FrontApp; here: boolean }) {
  // The Mood board has its own page AND a folder box on its tile, so the tile
  // cannot be one big link: everything above the folder box is.
  const ownPage = app.kind === "folder_palette";
  const body = (
    <>
      <div className="mb-2 flex min-w-0 items-center gap-2">
        <span className="truncate" style={{ font: `600 16px ${FREDOKA}`, color: "var(--text)" }}>
          {app.name}
        </span>
      </div>
      <p style={{ margin: 0, font: `500 13px/1.45 ${NUNITO}`, color: "var(--text2)" }}>{app.line}</p>
      {app.themes && app.themes.length > 0 && (
        <ul className="m-0 mt-2 list-none p-0">
          {app.themes.map((t) => (
            <li key={t.name} className="flex items-center gap-2" style={{ font: `500 12px ${NUNITO}` }}>
              {t.colours.map((c) => (
                <span key={c} className="inline-block h-3 w-3 rounded-full" style={{ background: c }} />
              ))}
              <span style={{ color: "var(--text2)" }}>{t.name}</span>
            </li>
          ))}
        </ul>
      )}
      {!here && (
        <p style={{ margin: "6px 0 0", font: `600 12px ${NUNITO}`, color: "var(--text3)" }}>{app.elsewhere}</p>
      )}
    </>
  );

  return (
    <li className="min-w-0">
      {app.href && here && !ownPage ? (
        <AppLink href={app.href} className="flex h-full flex-col rounded-[22px] border transition hover:opacity-90"
          style={tileStyle} aria-label={`${app.name}, open it`}>
          {body}
        </AppLink>
      ) : (
        <div className="flex h-full flex-col rounded-[22px] border" style={tileStyle}>
          {ownPage && app.href && here ? (
            <AppLink href={app.href} className="block transition hover:opacity-90" aria-label={`${app.name}, open it`}>
              {body}
              <span className="mt-2 block" style={MICRO}>Open</span>
            </AppLink>
          ) : body}
          {app.ask_folder && here && <MoodBoardFolder appId={app.id} label={app.ask_label} />}
        </div>
      )}
    </li>
  );
}

function FinishCard({ finish }: { finish: NonNullable<FrontPageData["finish"]> }) {
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    try {
      const got = await apiPost<{ line?: string; message?: string }>(finish.post, {});
      setNote(got?.line || got?.message || null);
    } catch {
      setNote("The locked box did not answer just now. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mb-3 flex flex-wrap items-center gap-3 rounded-[22px] border" style={tileStyle}>
      <span style={{ font: `600 14px ${NUNITO}`, color: "var(--text)" }}>{finish.line}</span>
      <button type="button" disabled={busy} onClick={run} className="rounded-full border px-3 py-1"
        style={{ borderColor: "var(--line)" }}>
        {finish.label}
      </button>
      {note && <span style={{ font: `500 12px ${NUNITO}` }}>{note}</span>}
    </div>
  );
}

function AppsSection({ front }: { front: FrontPageData }) {
  const [here, setHere] = useState(true);
  useEffect(() => {
    setHere(isThisComputer(typeof window !== "undefined" ? window.location.hostname : ""));
  }, []);
  const apps = front.apps ?? [];
  const voiceKey = front.voice_key ?? null;
  if (apps.length === 0 && !front.finish && !voiceKey) return null;
  return (
    <section className="mb-4 min-w-0" style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}
      aria-label={`What Toolsmith added to ${front.name}`}>
      {front.finish && <FinishCard finish={front.finish} />}
      {voiceKey && (
        <p className="mb-3" style={{ margin: "0 0 10px", font: `500 13px ${NUNITO}`, color: "var(--text)" }}>
          {voiceKey.line}{" "}
          <Link href={voiceKey.href} style={{ textDecoration: "underline" }}>{voiceKey.label}</Link>
        </p>
      )}
      {apps.length > 0 && (
        <>
          <div className="mb-2 flex items-baseline gap-3">
            <h2 style={{ ...MICRO, fontSize: 11, letterSpacing: "0.14em", margin: 0 }}>Apps on {front.name}</h2>
            <span style={MICRO}>{apps.length}</span>
          </div>
          <ul className="m-0 grid list-none gap-3 p-0"
            style={{ gridTemplateColumns: "repeat(auto-fill, minmax(min(240px, 100%), 1fr))" }}>
            {apps.map((a) => (
              <AppTile key={a.id} app={a} here={here} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export default function BotAppsGrid({ front }: { front: FrontPageData }) {
  // A live card that is a job's ingredient has no tile of its own: it is on
  // its job's page (GET /build/status; read once, no polling here).
  const { status } = useBuildStatus(false);
  const placed = placedThings(status);
  const cards = orderCards(front.cards ?? [], front.placed, front.off_home).filter(
    (c) => !placed.has(feedThingId(c.app_id)),
  );
  if (cards.length === 0) return <AppsSection front={front} />;
  return (
    <>
    <AppsSection front={front} />
    <section
      className="mb-4 min-w-0"
      style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}
      aria-label={`${front.name}'s live cards`}
    >
      <div className="mb-2 flex items-baseline gap-3">
        <h2 style={{ ...MICRO, fontSize: 11, letterSpacing: "0.14em", margin: 0 }}>
          Live on {front.name}
        </h2>
        <span style={MICRO}>{cards.length}</span>
      </div>
      <ul
        className="m-0 grid list-none gap-3 p-0"
        style={{
          gridTemplateColumns: "repeat(auto-fill, minmax(min(240px, 100%), 1fr))",
          gridAutoRows: "minmax(170px, auto)",
          gridAutoFlow: "dense",
        }}
      >
        {cards.map((c) => (
          <CardTile key={c.app_id} card={c} />
        ))}
      </ul>
    </section>
    </>
  );
}
