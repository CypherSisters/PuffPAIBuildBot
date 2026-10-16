/**
 * The welcome tour (src/puffpai/tour.py): show it?, its sentences, the looks
 * that fit the bot's theme, and keeping one. The bot's things and their
 * "on my home page" switch come from myThingsService (GET /things). Same-origin through the /api/pai proxy. Every
 * sentence comes from the backend; this file moves ids and ticks only.
 */

import type { LooksResult, LookSummary } from "./looksService";

const API_URL = "/api/pai";

const BACKGROUND = /^(?:clouds|stars|plain|soft|petals|confetti)$/;
const LOOK_ID = /^(?:[a-z]{2,20}:[a-z0-9][a-z0-9-]{0,39}|made:[0-9a-f]{12})$/;

export type TourWords = {
  show: boolean;
  /** A bot has been built (Setup offers "Show me around again"). */
  built: boolean;
  again: boolean;
  bot_name: string;
  kind: string;
  /** The bot's own intro from its personality ("" when it has none yet). */
  intro: string;
  things_title: string;
  things_line: string;
  things_none: string;
  looks_title: string;
  looks_line: string;
  more_title: string;
  more_line: string;
  /** The Find more box's example, from this bot's goal or kind. */
  find_example: string;
  done_title: string;
  done_line: string;
  /** Offered at the end of the tour: the walk around the pages. */
  walk_offer?: string;
  /** The walk around the pages, one short step each (tour.walk). */
  walk?: WalkStep[];
};

/** One step of the walk: the page it opens ("" stays), what it points at.
 *  A "spot" step rings the control marked data-walk="<spot>" on that page. */
export type WalkStep = {
  id: string;
  href: string;
  target: "rail" | "private" | "page" | "spot";
  spot?: string;
  title: string;
  line: string;
};

/** Fired on window to start the walk around the pages (Settings, the tour). */
export const WALK_EVENT = "puffpai:walk";

export function startWalk() {
  window.dispatchEvent(new Event(WALK_EVENT));
}

/** The Find more box's example when the bot's own one is not known. */
export const FIND_EXAMPLE = "For example: track something, plan something, learn something";

export type TourLook = LookSummary & { background_label: string };

async function call<T>(method: string, path: string, body?: unknown): Promise<{ ok: boolean; status: number; data: T }> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = (await res.json().catch(() => ({}))) as T;
    return { ok: res.ok, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: {} as T };
  }
}

export async function getTour(): Promise<TourWords | null> {
  const r = await call<TourWords>("GET", "/tour");
  return r.ok ? r.data : null;
}

export async function getTourLooks(): Promise<TourLook[]> {
  const r = await call<{ looks?: TourLook[] }>("GET", "/tour/looks");
  return r.ok ? r.data.looks || [] : [];
}

export async function keepTourLook(id: string, background: string): Promise<LooksResult> {
  if (!LOOK_ID.test(id) || !BACKGROUND.test(background)) {
    throw new Error("That is not a look this bot knows.");
  }
  const r = await call<LooksResult>("POST", "/tour/look", { id, background });
  if (!r.ok) throw new Error((r.data && r.data.message) || "That did not work.");
  return r.data;
}

export async function tourDone(): Promise<void> {
  await call("POST", "/tour/done");
}

export async function tourAgain(): Promise<boolean> {
  const r = await call<{ ok?: boolean }>("POST", "/tour/again");
  return r.ok;
}
