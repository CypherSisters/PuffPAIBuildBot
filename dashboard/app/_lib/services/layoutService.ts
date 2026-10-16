/**
 * How the owner arranged her home page and sidebar, and what is installed.
 *
 *   GET /dashboard/layout   the one layout file (<state>/dashboard_layout.json,
 *                           src/puffpai/dashboard_widgets.py), defaults when none
 *   PUT /dashboard/layout   each section sent (tiles, sidebar, seen) replaces
 *                           the saved one
 *   GET /installed          every app, skill, tool server and command line
 *                           tool on this bot (falls back to GET /build/apps)
 *
 * The browser keeps the last layout in localStorage only as an offline cache.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { apiGet, apiPut } from "../api";
import { getRunningApps } from "./buildService";

export interface LayoutSection {
  order: string[];
  hidden: string[];
  pinned: string[];
  names: Record<string, string>;
  sizes?: Record<string, { w: number; h: number }>;
}

export interface DashLayout {
  version: number;
  tiles: LayoutSection & { sizes: Record<string, { w: number; h: number }> };
  sidebar: LayoutSection;
  seen: { tiles: Record<string, string>; sidebar: Record<string, string> };
  updated_at?: string;
  updated_by?: string;
}

/** Mirrors default_layout() in dashboard_widgets.py, for a page that has
 *  never reached the backend. */
export const STARTS_HIDDEN = [
  "weather", "jetson", "smart-home", "cameras", "schedule", "reminders",
  "meals", "workout", "front", "persona",
];

export function defaultLayout(): DashLayout {
  return {
    version: 1,
    tiles: { order: ["chat", "jobs", "find-more"], sizes: {}, hidden: [...STARTS_HIDDEN], pinned: [], names: {} },
    sidebar: { order: [], hidden: [], pinned: [], names: {} },
    seen: { tiles: {}, sidebar: {} },
  };
}

const CACHE_KEY = "puffpai_layout_cache_v1";

export function cachedLayout(): DashLayout | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && parsed.tiles && parsed.sidebar ? (parsed as DashLayout) : null;
  } catch {
    return null;
  }
}

function cache(layout: DashLayout) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(layout));
  } catch {
    /* private mode: no offline copy */
  }
}

export async function getLayout(): Promise<DashLayout | null> {
  try {
    const r = await apiGet<{ layout?: DashLayout }>("/dashboard/layout", { cache: "no-store" });
    if (r?.layout?.tiles && r.layout.sidebar) {
      cache(r.layout);
      return r.layout;
    }
  } catch {
    /* offline: the caller keeps the cached copy */
  }
  return null;
}

export async function putLayout(patch: Partial<Pick<DashLayout, "tiles" | "sidebar" | "seen">>): Promise<DashLayout | null> {
  try {
    const r = await apiPut<{ layout?: DashLayout }>("/dashboard/layout", patch);
    if (r?.layout?.tiles) {
      cache(r.layout);
      return r.layout;
    }
  } catch {
    /* offline: kept in the cache until the next save reaches the backend */
  }
  return null;
}

export { cache as cacheLayout };

/** One thing on this bot (GET /installed). */
export interface InstalledThing {
  id: string;
  kind: "app" | "skill" | "tool_server" | "cli" | "knowledge";
  name: string;
  what: string;
  status: "running" | "stopped" | "ready";
  url: string;
  source_url: string;
  brain: string;
}

const KINDS = new Set(["app", "skill", "tool_server", "cli", "knowledge"]);
const STATUSES = new Set(["running", "stopped", "ready"]);
const LOCAL_URL = /^http:\/\/(127\.0\.0\.1|localhost):\d+(\/|$)/;

/** Everything installed. GET /installed when this backend has it, else the
 *  apps Toolsmith runs (GET /build/apps) and nothing else. */
export async function getInstalled(): Promise<InstalledThing[]> {
  try {
    const r = await apiGet<{ items?: unknown }>("/installed", { cache: "no-store" });
    if (Array.isArray(r?.items)) {
      return r.items
        .filter((x): x is Record<string, unknown> => !!x && typeof x === "object")
        .map((x) => ({
          id: String(x.id ?? ""),
          kind: (KINDS.has(String(x.kind)) ? x.kind : "app") as InstalledThing["kind"],
          name: String(x.name ?? x.id ?? ""),
          what: String(x.what ?? ""),
          status: (STATUSES.has(String(x.status)) ? x.status : "ready") as InstalledThing["status"],
          url: LOCAL_URL.test(String(x.url ?? "")) ? String(x.url) : "",
          source_url: /^https:\/\//.test(String(x.source_url ?? "")) ? String(x.source_url) : "",
          brain: String(x.brain ?? ""),
        }))
        .filter((x) => x.id);
    }
  } catch {
    /* no /installed on this backend: fall back below */
  }
  const apps = await getRunningApps();
  return apps.map((a) => ({
    id: a.id,
    kind: "app" as const,
    name: a.name || a.id,
    what: "",
    status: a.running ? ("running" as const) : ("stopped" as const),
    url: a.url,
    source_url: "",
    brain: "",
  }));
}
