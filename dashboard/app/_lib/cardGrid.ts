/**
 * The home page's grid of the bot's live cards: which order, and how big.
 *
 * Pure functions, so the rules are proven without a browser
 * (scripts/card-grid-proof.ts).
 *
 * Order: the owner's placement list first (GET /front `placed`, written by the
 * add-more flow: app ids, or objects carrying `app_id`/`id`; an object whose
 * `kind` is not "card", an update or a look, never orders a card), then every
 * other card in the order the backend sends them (the order they were added),
 * except the ones in GET /front `off_home`: added from the Add page with
 * "Put it on my home page" unticked, so they stay on their own page.
 *
 * Size: pictures get room (a gallery or a picture spans two columns and two
 * rows), a list is tall enough to read (two rows), a number is compact.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

export type PlacedEntry =
  | string
  | { kind?: unknown; app_id?: unknown; id?: unknown }
  | null
  | undefined;

export type GridCard = { app_id: string; kind?: string };

export type TileSize = "wide" | "tall" | "small";

function placedId(entry: PlacedEntry): string | null {
  if (typeof entry === "string") return entry;
  if (entry && typeof entry === "object") {
    if (entry.kind !== undefined && entry.kind !== "card") return null;
    const id = typeof entry.app_id === "string" ? entry.app_id : entry.id;
    return typeof id === "string" ? id : null;
  }
  return null;
}

/** The cards in the owner's order. Unknown or repeated placements are skipped;
 *  a card that was never placed still shows, after the placed ones, unless it
 *  is in `offHome` (the owner left it off the home page). */
export function orderCards<T extends GridCard>(
  cards: readonly T[],
  placed?: readonly PlacedEntry[] | null,
  offHome?: readonly unknown[] | null,
): T[] {
  const off = new Set(Array.isArray(offHome) ? offHome.filter((x) => typeof x === "string") : []);
  const byId = new Map(cards.map((c) => [c.app_id, c]));
  const out: T[] = [];
  const used = new Set<string>();
  for (const entry of Array.isArray(placed) ? placed : []) {
    const id = placedId(entry);
    const card = id ? byId.get(id) : undefined;
    if (card && !used.has(card.app_id)) {
      out.push(card);
      used.add(card.app_id);
    }
  }
  for (const card of cards) {
    if (!used.has(card.app_id) && !off.has(card.app_id)) {
      out.push(card);
      used.add(card.app_id);
    }
  }
  return out;
}

export function tileSize(kind: string | undefined): TileSize {
  if (kind === "gallery" || kind === "image") return "wide";
  if (kind === "list") return "tall";
  return "small";
}

/** Tailwind classes for a tile of this size (one column on a phone). */
export function tileClasses(size: TileSize): string {
  if (size === "wide") return "sm:col-span-2 sm:row-span-2";
  if (size === "tall") return "sm:row-span-2";
  return "";
}

/** How many pictures a gallery shows on its home tile; the rest are on its page. */
export const TILE_PICTURES = 6;

/** A tile with data is read again every five minutes (the box fetches on its
 *  own schedule). */
export const FEED_POLL_MS = 5 * 60_000;
/** A card that is switched on but has nothing to show yet is read again soon:
 *  the box starts its first fetch the moment it is switched on, so the data
 *  arrives within seconds. At most FEED_SOON_TRIES times, then the slow beat. */
export const FEED_SOON_MS = 3_000;
export const FEED_SOON_TRIES = 20;

/** Is this page open on the computer BuildBot runs on? Apps open
 *  only there; on a phone or another screen an app tile says so instead. */
export function isThisComputer(hostname: string | null | undefined): boolean {
  const h = String(hostname ?? "").toLowerCase().replace(/^\[|\]$/g, "");
  return h === "localhost" || h === "127.0.0.1" || h === "::1";
}

export type PolledFeed = { allowed?: boolean; card?: unknown } | null | undefined;

/** How long a tile waits before reading its card again. `soonSoFar` is how
 *  many quick reads it has already made. A card that is not switched on waits
 *  the slow beat: nothing will arrive until the owner says yes. */
export function feedPollMs(state: PolledFeed, soonSoFar: number): number {
  const waiting = state === null || state === undefined || (state.allowed === true && !state.card);
  return waiting && soonSoFar < FEED_SOON_TRIES ? FEED_SOON_MS : FEED_POLL_MS;
}
