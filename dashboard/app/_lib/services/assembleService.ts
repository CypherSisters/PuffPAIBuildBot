/**
 * The Assemble step: BuildBot puts parts (outside services, streams,
 * libraries, small connecting code) together into one working app per bot,
 * and proves each job it should do with a real ask.
 *
 * Backend: src/puffpai/assemble/api.py, same-origin through the /api/pai
 * proxy (paired device only):
 *
 *   GET  /assemble/status   what the team is doing now, and the plan
 *   POST /assemble/run      put it together (again); { thing?: id }
 *   GET  /assemble/report   each assembled app and each job: working or not yet
 *   GET  /assemble/yes      the Start cards of apps waiting for her yes
 *   POST /assemble/yes      her Start on one card { app_id, sha }
 *
 * Every sentence shown comes from the backend; this file only moves ids and
 * words, and reads each answer loosely so a missing field shows as nothing
 * rather than breaking a page.
 *
 * puff puff pass
 */

import type { FixCardData } from "../../_interfaces/api";
const BASE = "/api/pai";

/** What BuildBot does for one job of the bot. */
export type AssembleHow = "use_as_is" | "assemble" | "not_found";

export interface AssembleLine {
  /** Majordomo, Toolsmith, Drawbridge, or another agent's shown name. */
  agent: string;
  text: string;
  at?: string;
  app?: string;
}

export interface AssemblePlanRow {
  cap: string;
  name: string;
  how: AssembleHow;
  reason?: string;
  /** Find ids (finding/api.py) this job uses as they are or as parts. */
  finds?: string[];
  thing?: string;
}

export interface AssembleStatus {
  running: boolean;
  phase: string;
  lines: AssembleLine[];
  plan: AssemblePlanRow[];
}

export interface AssembleCap {
  cap: string;
  name: string;
  state: "working" | "not_yet";
  reason?: string;
  ask?: string;
  /** One tap that would fix it (a source to switch on), from BuildBot. */
  card?: FixCardData;
}

export interface AssembledApp {
  id: string;
  title: string;
  /** The thing id the app lives under on the dashboard. */
  thing?: string;
  checked_at?: string;
  caps: AssembleCap[];
}

export interface Reply<T> {
  ok: boolean;
  status: number;
  data: T;
}

async function req<T>(path: string, method = "GET", body?: unknown): Promise<Reply<T>> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
    let data: unknown = {};
    try {
      data = await res.json();
    } catch {
      /* empty body */
    }
    return { ok: res.ok, status: res.status, data: data as T };
  } catch {
    return { ok: false, status: 0, data: {} as T };
  }
}

const text = (v: unknown): string => (typeof v === "string" ? v : "");
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" ? (v as Record<string, unknown>) : {});
const HOWS: AssembleHow[] = ["use_as_is", "assemble", "not_found"];

/** The plain label the Add page and the team page show for each way. */
export const HOW_WORDS: Record<AssembleHow, string> = {
  use_as_is: "Use as is",
  assemble: "Parts BuildBot puts together",
  not_found: "Nothing found yet",
};

/** Agent ids the backend may send, as the names shown (sentinel is Drawbridge). */
const AGENT_NAMES: Record<string, string> = {
  majordomo: "Majordomo",
  toolsmith: "Toolsmith",
  sentinel: "Drawbridge",
  drawbridge: "Drawbridge",
};

function agentName(v: unknown): string {
  const t = text(v);
  return AGENT_NAMES[t.toLowerCase()] || t || "Majordomo";
}

export function readStatus(raw: unknown): AssembleStatus {
  const d = obj(raw);
  return {
    running: d.running === true,
    phase: text(d.phase),
    lines: list(d.lines)
      .map(obj)
      .map((l) => ({ agent: agentName(l.agent), text: text(l.text), at: text(l.at) || undefined, app: text(l.app) || undefined }))
      .filter((l) => l.text),
    plan: list(d.plan)
      .map(obj)
      .map((p) => ({
        cap: text(p.cap),
        name: text(p.name) || text(p.cap),
        how: (HOWS.includes(p.how as AssembleHow) ? p.how : "not_found") as AssembleHow,
        reason: text(p.reason) || undefined,
        finds: list(p.finds).filter((f): f is string => typeof f === "string"),
        thing: text(p.thing) || undefined,
      }))
      .filter((p) => p.cap),
  };
}

const CARD_KINDS: FixCardData["kind"][] = ["switch_on", "setting", "key", "assemble_yes"];

/** A job's fix card from the report, or undefined (words and ids only). */
function readCard(raw: unknown): FixCardData | undefined {
  const c = obj(raw);
  const kind = text(c.kind) as FixCardData["kind"];
  if (!CARD_KINDS.includes(kind) || !text(c.label)) return undefined;
  return { ...(c as unknown as FixCardData), kind, label: text(c.label), service: text(c.service), name: text(c.name) };
}

export function readReport(raw: unknown): AssembledApp[] {
  const d = obj(raw);
  const apps = Array.isArray(raw) ? raw : list(d.apps);
  return apps
    .map(obj)
    .map((a) => ({
      id: text(a.id),
      title: text(a.title) || text(a.id),
      thing: text(a.thing) || undefined,
      checked_at: text(a.checked_at) || undefined,
      caps: list(a.caps)
        .map(obj)
        .map((c) => ({
          cap: text(c.cap),
          name: text(c.name) || text(c.cap),
          state: (c.state === "working" ? "working" : "not_yet") as AssembleCap["state"],
          reason: text(c.reason) || undefined,
          ask: text(c.ask) || undefined,
          card: readCard(c.card),
        })),
    }))
    .filter((a) => a.id);
}

export async function getAssembleStatus(): Promise<Reply<AssembleStatus>> {
  const r = await req<unknown>("/assemble/status");
  return { ...r, data: readStatus(r.data) };
}

export async function getAssembleReport(): Promise<Reply<AssembledApp[]>> {
  const r = await req<unknown>("/assemble/report");
  return { ...r, data: r.ok ? readReport(r.data) : [] };
}

/** The assembled app that lives under this thing, or null. */
export function appForThing(apps: AssembledApp[], thingId: string): AssembledApp | null {
  return apps.find((a) => a.thing === thingId) || apps.find((a) => a.id === thingId) || null;
}

const THING_ID = /^[a-z0-9][a-z0-9_-]{0,100}$/;

/** "Put it together again": the whole bot, or the one app under a thing. */
export function runAssemble(thing?: string) {
  if (thing !== undefined && !THING_ID.test(thing)) {
    return Promise.resolve({ ok: false, status: 404, data: { ok: false, message: "" } } as Reply<{ ok: boolean; message?: string }>);
  }
  return req<{ ok: boolean; message?: string }>("/assemble/run", "POST", thing ? { thing } : {});
}

/** How a find is used, from the plan: the job it serves and the way. */
export function howForFind(plan: AssemblePlanRow[], findId: string): AssemblePlanRow | null {
  return plan.find((p) => (p.finds || []).includes(findId)) || null;
}

/** One outside code part on a Start card: where it comes from, its language. */
export interface YesPackage {
  name: string;
  origin: string;
  language: string;
}

/** An assembled app's Start card (backend: src/puffpai/assemble/consent.py).
 *  Nothing of the app runs until she taps Start. */
export interface YesCard {
  app_id: string;
  title: string;
  sites: string[];
  packages: YesPackage[];
  /** The backend's own words, shown as they are. */
  lines: string[];
  button: string;
  /** The card she is shown; a changed card is refused and shown again. */
  sha: string;
}

export function readYesCards(raw: unknown): YesCard[] {
  return list(obj(raw).cards)
    .map(obj)
    .map((c) => ({
      app_id: text(c.app_id),
      title: text(c.title) || text(c.app_id),
      sites: list(c.sites).filter((s): s is string => typeof s === "string"),
      packages: list(c.packages)
        .map(obj)
        .map((p) => ({ name: text(p.name), origin: text(p.origin), language: text(p.language) })),
      lines: list(c.lines).filter((s): s is string => typeof s === "string"),
      button: text(c.button) || "Start",
      sha: text(c.sha),
    }))
    .filter((c) => c.app_id && c.lines.length);
}

const APP_ID = /^[a-z][a-z0-9_]{1,31}$/;

/** The apps waiting for her yes (all, or the one under a thing). */
export async function getYesCards(appId?: string): Promise<YesCard[]> {
  if (appId !== undefined && !APP_ID.test(appId)) return [];
  const r = await req<unknown>(`/assemble/yes${appId ? `?app=${encodeURIComponent(appId)}` : ""}`);
  return r.ok ? readYesCards(r.data) : [];
}

/** Her Start on one card. */
export async function sayYes(card: YesCard): Promise<{ ok: boolean; line: string }> {
  if (!APP_ID.test(card.app_id)) return { ok: false, line: "" };
  const r = await req<{ ok?: boolean; line?: string; message?: string }>("/assemble/yes", "POST", {
    app_id: card.app_id,
    sha: card.sha,
  });
  return { ok: r.ok && r.data.ok === true, line: text(r.data.line) || text(r.data.message) };
}
