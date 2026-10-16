/**
 * Your first apps: the apps BuildBot ships with, off until you turn them on.
 *
 * Backend (src/puffpai/first_apps_api.py, paired device only):
 *   GET  /apps/first        {ok, apps: [{id, name, blurb, route, icon, on}]}
 *   POST /apps/first/turn   {id, on} -> {ok, app, line}
 *
 * Turning one on is local only: nothing is downloaded, nothing leaves this
 * computer. After a change the sidebar and the Apps page read the build again
 * (notifyBuildChanged).
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import { API_URL } from "../api";
import { notifyBuildChanged } from "./buildService";

export interface FirstApp {
  id: string;
  name: string;
  blurb: string;
  route: string;
  icon: string;
  on: boolean;
}

/** The section's heading and its one line, worded once. */
export const FIRST_APPS_TITLE = "Your first apps";
export const FIRST_APPS_LINE =
  "These came with BuildBot and start off. Turn on the ones you want; turning one on stays on this computer.";

const ID = /^[a-z][a-z0-9_]{1,31}$/;

function text(v: unknown, max = 200): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

/** The answer of GET /apps/first, checked field by field. Pure. */
export function readFirstApps(data: unknown): FirstApp[] {
  const apps = data && typeof data === "object" ? (data as { apps?: unknown }).apps : null;
  if (!Array.isArray(apps)) return [];
  return apps
    .filter((a): a is Record<string, unknown> => Boolean(a) && typeof a === "object")
    .map((a) => ({
      id: text(a.id, 32),
      name: text(a.name, 64) || text(a.id, 32),
      blurb: text(a.blurb),
      route: text(a.route),
      icon: text(a.icon, 64),
      on: a.on === true,
    }))
    .filter((a) => ID.test(a.id));
}

/** The button's words for one app. Pure. */
export function turnWords(app: FirstApp): string {
  return app.on ? "Turn off" : "Turn on";
}

/** What GET /apps/first said: the list, or that this browser is not paired
 *  (403). Null: no answer at all. */
export type FirstAppsAnswer = { apps: FirstApp[]; unpaired: boolean } | null;

/** The Apps page's line when no app is on yet, true to what this browser can
 *  do from here. Pure. */
export function noAppsLine(answer: FirstAppsAnswer): string {
  if (answer?.unpaired) return "No apps are on yet. Pair this device to turn on your first apps.";
  if (answer && answer.apps.length > 0) {
    return "No apps are on yet. Turn on one of your first apps below, or make your own with New.";
  }
  return "No apps are on yet. Make your own with New.";
}

export async function getFirstApps(): Promise<FirstAppsAnswer> {
  try {
    const res = await fetch(`${API_URL}/apps/first`, { cache: "no-store" });
    if (res.status === 401 || res.status === 403) return { apps: [], unpaired: true };
    if (!res.ok) return null;
    return { apps: readFirstApps(await res.json()), unpaired: false };
  } catch {
    return null;
  }
}

export async function turnFirstApp(id: string, on: boolean): Promise<{ ok: boolean; line: string }> {
  try {
    const res = await fetch(`${API_URL}/apps/first/turn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id.slice(0, 32), on }),
      cache: "no-store",
    });
    let said: Record<string, unknown> = {};
    try {
      said = (await res.json()) as Record<string, unknown>;
    } catch {
      /* empty body */
    }
    const ok = res.ok && said.ok !== false;
    if (ok) notifyBuildChanged();
    return { ok, line: text(said.line) || text(said.message) };
  } catch {
    return { ok: false, line: "No answer from this computer." };
  }
}
