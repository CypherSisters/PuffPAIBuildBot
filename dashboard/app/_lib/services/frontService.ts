/**
 * The front page: what THIS build is, has and can answer (GET /front).
 *
 * The backend writes every sentence (src/puffpai/front_page.py) and checks
 * every "Try asking" question against this build before it is sent. The
 * browser only lays it out. Links are the dashboard's own pages; anything the
 * backend sends that is not one is dropped here (stricter than the backend,
 * never looser).
 */

import { apiGet } from "@/app/_lib/api";

export type FrontQuestion = { text: string; why: string; from: string };
export type FrontCard = {
  app_id: string;
  name: string;
  status: "on" | "off" | "problem";
  line: string;
  credit: string;
  href: string;
  /** "image", "value", "list" or "gallery": sizes the card's home tile. */
  kind?: string;
};
export type FrontBook = { name: string; stage: string; lines: string[] };
/** Something Toolsmith added: an app, a list, pictures or the mood board. */
export type FrontApp = {
  id: string;
  name: string;
  line: string;
  /** "" when it has no page of its own (pictures, the mood board). */
  href: string;
  kind: string;
  /** What a phone or another screen shows instead (apps open only on the
   *  computer BuildBot runs on). */
  elsewhere: string;
  ask_folder?: boolean;
  ask_label?: string;
  themes?: { name: string; colours: string[] }[];
};
export type FrontFinish = { line: string; label: string; post: string } | null;
/** The way to the Home Assistant voice key page (never the key itself). */
export type FrontVoiceKey = { label: string; line: string; href: string; on: boolean } | null;
export type FrontPageData = {
  name: string;
  purpose: string;
  hello: string;
  dateline: string;
  talk: { voice: boolean; line: string; href: string };
  today: {
    said: { time: string; text: string; title: string }[];
    next: { what: string; when: string }[];
    offer: { id: string; question: string; detail: string } | null;
  };
  cards: FrontCard[];
  /** What Toolsmith added (src/puffpai/front_page.py app_tiles). */
  apps?: FrontApp[];
  /** The Finish card when things wait for the locked box. */
  finish?: FrontFinish;
  /** The way to the Home Assistant voice key page (src/puffpai/front_page.py voice_key_link). */
  voice_key?: FrontVoiceKey;
  try_asking: FrontQuestion[];
  knows: { books: FrontBook[]; line: string; href: string; more_label: string };
  not_connected: { thing: string; line: string }[];
  add: { label: string; href: string; line: string };
  // consent_line, count and on come from the backend's one consent summary
  // (privacy_explainer.consent_summary), the same the sidebar card reads.
  outside: {
    label: string;
    href: string;
    line: string;
    consent_line?: string;
    count?: number | null;
    on?: string[];
  };
  looks: { label: string; href: string; line: string };
  keys: { label: string; href: string; line: string };
  /** What the owner put on the home page, in order (src/puffpai/home_placement.py):
   *  live cards, updates and looks. Only `kind: "card"` rows order the grid. */
  placed?: (string | { kind?: unknown; app_id?: unknown; id?: unknown })[];
  /** Cards added from the Add page but left off the home page (own page only). */
  off_home?: string[];
};

/** Only the dashboard's own pages, never an address from anywhere else. */
const OWN_PAGE = /^\/dashboard(?:\/[a-z0-9_-]{1,64})*\/?$/;

export function safeHref(href: unknown, fallback = "/dashboard"): string {
  return typeof href === "string" && OWN_PAGE.test(href) ? href : fallback;
}

/** A colour swatch is a plain #rrggbb value, nothing else (it goes into a style). */
const SWATCH = /^#[0-9a-f]{6}$/;
/** The only thing the Finish card may post to. */
const FINISH_POST = "/box/finish";
/** The only page the voice key link may open. */
const VOICE_KEY_HREF = "/dashboard/keys/voice";
const ID = /^[a-z0-9][a-z0-9-]{0,39}$/;

function text(v: unknown, max: number): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

export function cleanApps(apps: unknown): FrontApp[] {
  if (!Array.isArray(apps)) return [];
  const out: FrontApp[] = [];
  for (const raw of apps.slice(0, 60)) {
    if (!raw || typeof raw !== "object") continue;
    const a = raw as Record<string, unknown>;
    const id = text(a.id, 40);
    if (!ID.test(id)) continue;
    const themes = Array.isArray(a.themes)
      ? a.themes.slice(0, 6).flatMap((t) => {
          if (!t || typeof t !== "object") return [];
          const th = t as Record<string, unknown>;
          const colours = Array.isArray(th.colours)
            ? th.colours.filter((c): c is string => typeof c === "string" && SWATCH.test(c)).slice(0, 5)
            : [];
          return [{ name: text(th.name, 60), colours }];
        })
      : undefined;
    out.push({
      id,
      name: text(a.name, 60),
      line: text(a.line, 240),
      href: typeof a.href === "string" && a.href ? safeHref(a.href, "") : "",
      kind: text(a.kind, 20),
      elsewhere: text(a.elsewhere, 120),
      ask_folder: a.ask_folder === true,
      ask_label: text(a.ask_label, 60),
      themes,
    });
  }
  return out;
}

export function cleanFinish(finish: unknown): FrontFinish {
  if (!finish || typeof finish !== "object") return null;
  const f = finish as Record<string, unknown>;
  if (f.post !== FINISH_POST) return null;
  return { line: text(f.line, 160), label: text(f.label, 40) || "Finish", post: FINISH_POST };
}

export function cleanVoiceKey(link: unknown): FrontVoiceKey {
  if (!link || typeof link !== "object") return null;
  const v = link as Record<string, unknown>;
  if (v.href !== VOICE_KEY_HREF) return null;
  const label = text(v.label, 60);
  const line = text(v.line, 160);
  if (!label || !line) return null;
  return { label, line, href: VOICE_KEY_HREF, on: v.on === true };
}

export async function getFront(): Promise<FrontPageData> {
  const data = await apiGet<FrontPageData>("/front", { cache: "no-store" });
  return {
    ...data,
    apps: cleanApps(data.apps),
    finish: cleanFinish(data.finish),
    voice_key: cleanVoiceKey(data.voice_key),
    talk: { ...data.talk, href: safeHref(data.talk?.href, "/dashboard/chat") },
    cards: (data.cards ?? []).map((c) => ({
      ...c,
      href: safeHref(c.href, "/dashboard/apps"),
    })),
    knows: { ...data.knows, href: safeHref(data.knows?.href, "/dashboard/knowledge") },
    add: { ...data.add, href: safeHref(data.add?.href, "/dashboard/add") },
    outside: { ...data.outside, href: safeHref(data.outside?.href, "/dashboard/outside") },
    looks: { ...data.looks, href: safeHref(data.looks?.href, "/dashboard/looks") },
    keys: { ...data.keys, href: safeHref(data.keys?.href, "/dashboard/keys") },
    try_asking: (data.try_asking ?? []).filter(
      (q) => typeof q?.text === "string" && q.text.length > 0 && q.text.length <= 120,
    ),
  };
}
