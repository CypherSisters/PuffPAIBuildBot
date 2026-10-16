/**
 * Looks: how the dashboard looks. Every call goes to the same-origin /api/pai
 * proxy (prefix "looks"). The backend writes every colour and every sentence;
 * the browser re-checks the colours it is handed (app/_lib/looks.ts) and sends
 * back only a look's id, never colours.
 */

import { cleanBoth, type LookBothModes } from "../looks";

const API_URL = "/api/pai";

const BACKGROUND = /^(?:clouds|stars|plain|soft|petals|confetti)$/;

/** Look ids the backend uses. Stricter than the backend on purpose. */
const LOOK_ID = /^(?:[a-z]{2,20}:[a-z0-9][a-z0-9-]{0,39}|made:[0-9a-f]{12})$/;

export type LookCheck = { check: string; result: string; why: string };

export type LookSummary = {
  id: string;
  name: string;
  from_line: string;
  colours_line: string;
  one_version: boolean;
  background: string;
  background_line: string;
  swatch: { day: string[]; night: string[] };
  vars: LookBothModes;
  adjusted: string[];
  checks: LookCheck[];
};

export type FoundLook = LookSummary & {
  kind: "list" | "brain_pick" | "made";
  default_on: boolean;
  why: string;
  /** Set when this look cannot wear the background she picked (plain words). */
  background_note?: string;
};

export type LooksState = {
  current: LookSummary | null;
  /** What the page wears, background included (null: the bot's own look). */
  vars?: LookBothModes | null;
  background?: string;
  /**
   * The background she picked herself (null: none). The backend keeps it and
   * dresses every look she tries on or keeps in it, so try-on equals keep.
   */
  chosen_background?: string | null;
  background_line?: string;
  backgrounds?: { id: string; label: string }[];
  can_undo: boolean;
  history: { id: string; name: string }[];
  pages_line: string;
  pages_own: string;
};

export type LooksResult = LooksState & { ok: boolean; message: string };

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message =
      (data && ((data.message as string) || (data.note as string))) || "That did not work.";
    throw new Error(message);
  }
  return data as T;
}

/** What the page should wear for *state* (the look plus its background). */
export function wornVars(state: LooksState | null): LookBothModes | null {
  if (!state) return null;
  if (state.vars !== undefined) return state.vars ? cleanBoth(state.vars) : null;
  return state.current ? cleanBoth(state.current.vars) : null;
}

export function getLooks(): Promise<LooksState> {
  return call<LooksState>("GET", "/looks");
}

export function findLooks(
  words: string,
  brain: boolean,
): Promise<{ looks: FoundLook[]; note: string; chosen_background?: string | null }> {
  return call("POST", "/looks/find", { words: words.slice(0, 200), brain });
}

/** The label of her chosen background ("Stars"), or "" when she has none. */
export function chosenLabel(state: LooksState | null): string {
  const id = state && state.chosen_background;
  if (!id || !BACKGROUND.test(id)) return "";
  const row = (state.backgrounds || []).find((b) => b.id === id);
  return row ? row.label : "";
}

export function chooseLook(id: string): Promise<LooksResult> {
  if (!LOOK_ID.test(id)) return Promise.reject(new Error("That is not a look this bot knows."));
  return call<LooksResult>("POST", "/looks/use", { id });
}

export function chooseBackground(background: string): Promise<LooksResult> {
  if (!BACKGROUND.test(background)) return Promise.reject(new Error("That is not a background this bot has."));
  return call<LooksResult>("POST", "/looks/background", { background });
}

export function undoLook(): Promise<LooksResult> {
  return call<LooksResult>("POST", "/looks/undo");
}

export function resetLook(): Promise<LooksResult> {
  return call<LooksResult>("POST", "/looks/reset");
}
