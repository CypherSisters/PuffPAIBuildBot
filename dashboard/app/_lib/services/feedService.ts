/**
 * Live cards (an app's feed) and the drafts BuildBot made, as the backend
 * describes them. Every call goes to the same-origin /api/pai proxy; the
 * browser never talks to an outside website. Pictures are fetched and checked
 * by the backend and served from it, so the only image address this module
 * will ever build is the backend's own, for a strictly-shaped local name.
 */

const API_URL = "/api/pai";

export type FeedItem = { title: string; detail?: string; image?: string; link?: string };

export type FeedCardData = {
  kind: "image" | "value" | "list" | "gallery";
  title: string;
  image?: string;
  headline?: string;
  caption?: string;
  image_credit?: string;
  label?: string;
  unit?: string;
  value?: string;
  items?: FeedItem[];
};

export type FeedChoice = {
  name: string;
  label: string;
  options: string[];
  picked: string | null;
};

export type FeedState = {
  app_id: string;
  name: string;
  status: string;
  message: string;
  card: FeedCardData | null;
  fetched_at: string | null;
  age_minutes: number | null;
  last_error: string;
  refresh_minutes: number;
  credit: string;
  license: string;
  service: { name: string; host: string; image_host?: string | null };
  allowed: boolean;
  service_state: string;
  service_message: string;
  how_to_fix: string | null;
  what_leaves: string;
  choices: FeedChoice[];
};

export type ReviewRow = {
  check: string;
  result: "pass" | "fail" | "unknown";
  why: string;
};

export type FeedDraft = {
  id: string;
  name: string;
  status: string;
  service: { name: string; host: string; image_host?: string | null } | null;
  credit: string;
  license: string;
  kind: string;
  what_leaves: string;
  review: ReviewRow[];
  preview: FeedCardData | null;
  preview_has_picture: boolean;
  refresh_minutes: number;
};

const LOCAL_IMAGE = /^[0-9a-f]{64}\.(?:jpg|png|gif|webp)$/;
const APP_ID = /^[a-z][a-z0-9_]{1,31}$/;

/** The backend's own address for a checked picture, or null. Stricter than
 *  the backend on purpose: anything not shaped like a stored name is dropped. */
export function feedImageUrl(appId: string, name: string | undefined): string | null {
  if (!name || !LOCAL_IMAGE.test(name) || !APP_ID.test(appId)) return null;
  return `${API_URL}/apps/${appId}/feed/image/${name}`;
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T | null> {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 404) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      (data && ((data.error as string) || (data.message as string))) || "That did not work.",
    );
  }
  return data as T;
}

export function getFeed(appId: string): Promise<FeedState | null> {
  if (!APP_ID.test(appId)) return Promise.resolve(null);
  return call<FeedState>("GET", `/apps/${appId}/feed`);
}

export function refreshFeed(appId: string): Promise<FeedState | null> {
  return call<FeedState>("POST", `/apps/${appId}/feed/refresh`);
}

export async function setFeedAllowed(appId: string, allowed: boolean) {
  if (!APP_ID.test(appId)) throw new Error("That app has no outside service.");
  if (!allowed) {
    return call<{ state: string; message: string; how_to_fix: string | null }>(
      "POST",
      `/apps/${appId}/feed/deny`,
    );
  }
  // Switching an outside service ON needs a one-time yes from the backend,
  // bound to this device and to exactly this card's service. The card has
  // already shown the backend's own "What leaves your computer" line for it.
  const asked = await call<{ ticket: string }>("POST", `/outside/card-${appId}/confirm`);
  return call<{ state: string; message: string; how_to_fix: string | null }>(
    "POST",
    `/apps/${appId}/feed/allow`,
    { ticket: asked?.ticket ?? "" },
  );
}

export function pickFeedChoice(appId: string, name: string, value: string) {
  return call<FeedState>("POST", `/apps/${appId}/feed/choice`, { name, value });
}

export function getDraft(draftId: string): Promise<FeedDraft | null> {
  if (!APP_ID.test(draftId)) return Promise.resolve(null);
  return call<FeedDraft>("GET", `/apps/feed-drafts/${draftId}`);
}

export async function installDraft(draftId: string, allow: boolean) {
  if (!APP_ID.test(draftId)) throw new Error("That draft is not here any more.");
  // Adding it WITH its outside website needs a one-time yes from the backend,
  // bound to this device and to exactly this draft (same rule as the card's
  // own "Yes" and the "What leaves your computer" page).
  let ticket = "";
  if (allow) {
    const asked = await call<{ ticket: string }>(
      "POST",
      `/apps/feed-drafts/${draftId}/confirm`,
    );
    ticket = asked?.ticket ?? "";
  }
  return call<{ installed: boolean; service: { state: string; message: string } }>(
    "POST",
    `/apps/feed-drafts/${draftId}/install`,
    allow ? { allow, ticket } : { allow },
  );
}

export function discardDraft(draftId: string) {
  return call<{ removed: boolean }>("DELETE", `/apps/feed-drafts/${draftId}`);
}

/** "just now", "12 minutes ago", "3 hours ago" - plain, never a timestamp. */
export function agePhrase(minutes: number | null): string {
  if (minutes === null || minutes === undefined) return "";
  if (minutes < 2) return "just now";
  if (minutes < 120) return `${minutes} minutes ago`;
  if (minutes < 48 * 60) return `${Math.floor(minutes / 60)} hours ago`;
  return `${Math.floor(minutes / (60 * 24))} days ago`;
}

export const REVIEW_WORDS: Record<ReviewRow["result"], string> = {
  pass: "OK",
  fail: "Problem",
  unknown: "Not sure",
};
