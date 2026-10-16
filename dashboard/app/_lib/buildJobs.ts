/**
 * The bot's jobs, as GET /build/status sends them (src/puffpai/build/, one
 * record in <state>/build/build.json). Pure: no fetch, no React, so the home
 * page, the job page, the wizard handoff, the agent team page and the proof
 * script (scripts/build-jobs-proof.tsx) all read the answer the same way.
 *
 * ONE SOURCE OF WORDS. Every status and route label shown comes from the
 * answer's own `words` block. The small table below is only what an answer
 * with no `words` falls back to, so a page never shows a bare enum.
 *
 * Every field is read loosely: a missing or unknown value becomes a safe
 * default rather than breaking a page.
 *
 * puff puff pass
 */

import { fromYourGitHub } from "./services/githubService";

export const JOB_STATUSES = [
  "planned",
  "needs_setup",
  "waiting_yes",
  "queued",
  "building",
  "working",
  "not_yet",
  "nothing_found",
] as const;
export type JobStatus = (typeof JOB_STATUSES)[number];

export const JOB_ROUTES = ["A", "B", "C", "D"] as const;
export type JobRoute = (typeof JOB_ROUTES)[number];

export const SETUP_KINDS = ["program", "package", "key", "setting", "address", "switch"] as const;
export type SetupKind = (typeof SETUP_KINDS)[number];

/** Rows only she can do (a value she types). Everything else on the list,
 *  programs, packages and the outside sites it shows, comes with the one Yes
 *  (the backend's build/setup.py OWNER_INPUT). */
export const HER_KINDS: readonly SetupKind[] = ["key", "setting"];
/** Rows the one Yes switches on: shown on the list, done by the Yes. One the
 *  Yes could not switch on stays on the list with its button. */
export const YES_SITE_KINDS: readonly SetupKind[] = ["address", "switch"];
/** Pick classes a job answers from (a book, a guide) rather than connects. */
export const ANSWER_CLASSES = ["TEXT", "GUIDE"];

export interface JobPick {
  ref: string;
  class: string;
  score: number;
  why: string;
}

/** One part a job uses. The answer may send only the ref; the pages fill in
 *  the rest from the thing it names. */
export interface Ingredient {
  ref: string;
  name?: string;
  /** Its kind in plain words ("live card", "tool server"). */
  kind?: string;
  /** Drawbridge's check of it, when the answer carries one. */
  check?: { result: "pass" | "fail" | "unknown"; why: string };
  /** It came from the owner's own GitHub (shown as "From your GitHub"). */
  mine?: boolean;
}

export interface Job {
  id: string;
  name: string;
  does: string;
  action: string;
  subject: string[];
  output: string;
  needs: string[];
  ask: string;
  route: JobRoute;
  pick: JobPick | null;
  ingredients: Ingredient[];
  view: string;
  page: string;
  status: JobStatus;
  reason: string;
  /** A screen of its own on this computer (127.0.0.1), when the answer has one. */
  screen?: string;
  /** False when that app's own page refuses to be shown in a frame
   *  (X-Frame-Options or frame-ancestors): it opens in its own window. */
  screenFramable?: boolean;
  /** The app a route A job really uses (the pick, or the backup used when
   *  the pick could not start on this computer). */
  appUsed?: {
    ref: string;
    name: string;
    madeFrom?: string;
    notes?: string[];
    /** A desktop program (no page): opened in its own window. */
    program?: { id: string; path: string };
  };
  /** What Toolsmith installed or switched on beyond the app's own folder
   *  (a program, a Docker image or container, an admin change), each with
   *  how to undo it ("no automatic undo: ..." when there is none). */
  beyond?: BeyondItem[];
  /** The apps that already gave up for this job, each with why (the next
   *  try starts after them). */
  tried?: { ref: string; name: string; why: string }[];
  /** The apps tried before a Check again, each with why (shown as "Tried before"). */
  triedBefore?: { ref: string; name: string; why: string }[];
  /** The app Toolsmith is trying for this job right now: since when (epoch
   *  seconds) and, with the cloud model, its cost for this job so far. */
  trying?: { name: string; since: number; costUsd?: number; waiting?: string };
  /** Code Toolsmith makes an app from (her project first), by name. */
  code?: { ref: string; name: string }[];
  /** What she asked this job to do, when its sources give something else
   *  ("does" then says what they really give). */
  wanted?: string;
  /** The model Toolsmith built it with: "local" (on this computer) or the
   *  Anthropic model id; absent when none was recorded. */
  builtWith?: string;
}

export interface BeyondItem {
  kind: string;
  what: string;
  undo: string;
  admin: boolean;
}

export interface SetupRow {
  id: string;
  kind: SetupKind;
  label: string;
  detail: string;
  job_ids: string[];
  done: boolean;
  /** Her one Yes does this row (letting the bot go online). */
  by_yes?: boolean;
  /** A town she typed, kept until it can be looked up (right after the Yes). */
  pending?: string;
  /** The town she typed on a row that is not done yet (kept, shown back). */
  town?: string;
  /** The town could not be looked up: its rough position is asked for too. */
  position?: boolean;
  /** What the backend said about this row last. */
  said?: string;
  /** The sites a switch row reaches (a data source's row also covers the
   *  picture sites its results point to; its detail says so). */
  hosts?: string[];
}

/** What a setup row's own control sends: a key or a town as text, or the
 *  town with its rough position. */
export type SetupValue = string | { town: string; latitude?: string; longitude?: string };

export interface DroppedFind {
  ref: string;
  name: string;
  why: string;
}

export interface BuildLine {
  agent: string;
  text: string;
}

export interface BuildWords {
  status: Record<JobStatus, string>;
  route: Record<JobRoute, string>;
  /** The shared view in words ("gallery" -> "picture gallery"). */
  view: Record<string, string>;
  /** A job whose pick is a book or a guide: "Answers from <name>". */
  answers: string;
}

/** One part a job uses, by name (GET /build/status `parts`). */
export interface PartName {
  ref: string;
  name: string;
  class: string;
  kind: string;
  /** Thing ids that are this part (a live card made for it): ingredients of
   *  its job only, with no page, tile or sidebar entry of their own. */
  things: string[];
  /** It came from the owner's own GitHub. */
  mine?: boolean;
  /** Its own project page (https), when it has one. */
  url?: string;
}

/** What her one Yes did with one outside site the list showed. */
export interface YesSite {
  row: string;
  host: string;
  state: "on" | "when_built" | "not_on";
  reason: string;
}

/** One book or library for the goal on the Yes (build/knowledge.py):
 *  ticked lines download after her Yes, one at a time. */
export interface KnowledgeItem {
  id: string;
  title: string;
  site: string;
  sizeWords: string;
  ticked: boolean;
  consent: string[];
  status: "" | "downloading" | "added" | "failed";
  reason: string;
}

const KNOWLEDGE_STATES = ["", "downloading", "added", "failed"] as const;

export interface BuildStatus {
  /** False while the bot's start-up work runs; `line` says so. */
  ready: boolean;
  line: string;
  /** The bot's name, when it has one. */
  bot: string;
  jobs: Job[];
  setup: SetupRow[];
  parts: PartName[];
  /** sha: the list she is shown; her Yes sends it back. */
  yes: { given: boolean; at: string; sha: string };
  sites: YesSite[];
  dropped: DroppedFind[];
  /** Books for the goal, each a tickable line on the Yes. */
  knowledge?: KnowledgeItem[];
  /** She picked Anthropic for Toolsmith but its key is not saved: the build
   *  waits, with this line and Open Settings / Use this computer. */
  toolsmithHold?: string;
  /** Toolsmith waits for the vault (or its key is not ready while a job
   *  needs it): "Unlock your vault to let Toolsmith go on." with a PIN box. */
  vaultWait?: string;
  running: boolean;
  lines: BuildLine[];
  words: BuildWords;
}

/** Only for an answer that sent no words: the same as the backend's
 *  build/schema.py WORDS. howLine fills <view> (and {pick}, {view} and
 *  {sources} if a template has them). */
export const FALLBACK_WORDS: BuildWords = {
  status: {
    planned: "Planned",
    needs_setup: "Needs setup",
    waiting_yes: "Waiting for your yes",
    queued: "Waiting its turn",
    building: "Being built",
    working: "Working",
    not_yet: "Not yet",
    nothing_found: "Nothing found yet",
  },
  route: {
    A: "Uses it as it is",
    B: "Connects a tool",
    C: "Puts together a <view>",
    D: "Nothing found yet",
  },
  view: {
    gallery: "picture gallery",
    chart: "chart",
    map: "map",
    player: "player",
    list: "list",
    form: "form",
    app: "its own screen",
  },
  answers: "Answers from <name>",
};

/** The colour each status is drawn in (a theme variable). */
export const STATUS_TONE: Record<JobStatus, string> = {
  planned: "--text3",
  needs_setup: "--lemon",
  waiting_yes: "--lemon",
  queued: "--sky",
  building: "--sky",
  working: "--mint",
  not_yet: "--lemon",
  nothing_found: "--text3",
};

const text = (v: unknown): string => (typeof v === "string" ? v.trim() : "");
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const strings = (v: unknown): string[] => list(v).map(text).filter(Boolean);
const oneOf = <T extends string>(all: readonly T[], v: unknown, dflt: T): T =>
  (all as readonly string[]).includes(v as string) ? (v as T) : dflt;

/** Agent ids the backend may send, as the names shown (sentinel is Drawbridge). */
const AGENT_NAMES: Record<string, string> = {
  majordomo: "Majordomo",
  toolsmith: "Toolsmith",
  sentinel: "Drawbridge",
  drawbridge: "Drawbridge",
};

export function agentName(v: unknown): string {
  const t = text(v);
  return AGENT_NAMES[t.toLowerCase()] || t || "Majordomo";
}

function readWords(raw: unknown): BuildWords {
  const w = obj(raw);
  const status = obj(w.status);
  const route = obj(w.route);
  const view = obj(w.view);
  const out: BuildWords = {
    status: { ...FALLBACK_WORDS.status },
    route: { ...FALLBACK_WORDS.route },
    view: { ...FALLBACK_WORDS.view },
    answers: text(w.answers) || FALLBACK_WORDS.answers,
  };
  for (const s of JOB_STATUSES) if (text(status[s])) out.status[s] = text(status[s]);
  for (const r of JOB_ROUTES) if (text(route[r])) out.route[r] = text(route[r]);
  for (const [k, v] of Object.entries(view)) if (text(v)) out.view[k] = text(v);
  return out;
}

/** Drawbridge's check: the backend sends {ok, reason}; {result, why} is
 *  read too. */
function readCheck(raw: unknown): Ingredient["check"] {
  if (typeof raw === "string" && raw.trim()) return { result: "unknown", why: raw.trim() };
  const c = obj(raw);
  const result =
    typeof c.ok === "boolean"
      ? c.ok
        ? "pass"
        : "fail"
      : oneOf(["pass", "fail", "unknown"] as const, c.result, "unknown");
  const why = text(c.reason) || text(c.why) || text(c.text);
  return typeof c.ok === "boolean" || c.result || why ? { result, why } : undefined;
}

function readIngredient(raw: unknown): Ingredient | null {
  if (typeof raw === "string") return raw.trim() ? { ref: raw.trim() } : null;
  const i = obj(raw);
  const ref = text(i.ref) || text(i.id);
  if (!ref) return null;
  return {
    ref,
    name: text(i.name) || undefined,
    kind: text(i.kind_words) || text(i.kind) || undefined,
    check: readCheck(i.check ?? i.drawbridge),
    mine: fromYourGitHub(i) || undefined,
  };
}

export function readJob(raw: unknown): Job | null {
  const j = obj(raw);
  const id = text(j.id);
  if (!id) return null;
  const p = obj(j.pick);
  const pick: JobPick | null = text(p.ref)
    ? { ref: text(p.ref), class: text(p.class), score: Number(p.score) || 0, why: text(p.why) }
    : null;
  return {
    id,
    name: text(j.name) || id,
    does: text(j.does),
    action: text(j.action),
    subject: strings(j.subject),
    output: text(j.output),
    needs: strings(j.needs),
    ask: text(j.ask),
    route: oneOf(JOB_ROUTES, j.route, "D"),
    pick,
    ingredients: list(j.ingredients)
      .map(readIngredient)
      .filter((i): i is Ingredient => i !== null),
    view: text(j.view),
    page: text(j.page) || `job-${id}`,
    status: oneOf(JOB_STATUSES, j.status, "planned"),
    reason: text(j.reason),
    screen: text(j.screen) || undefined,
    screenFramable: j.screen_framable === false ? false : undefined,
    appUsed: text(obj(j.app_used).name) && text(obj(j.app_used).ref)
      ? {
          ref: text(obj(j.app_used).ref),
          name: text(obj(j.app_used).name),
          madeFrom: text(obj(j.app_used).made_from) || undefined,
          program:
            text(obj(obj(j.app_used).program).id) || text(obj(obj(j.app_used).program).path)
              ? { id: text(obj(obj(j.app_used).program).id), path: text(obj(obj(j.app_used).program).path) }
              : undefined,
          notes: Array.isArray(obj(j.app_used).notes)
            ? (obj(j.app_used).notes as unknown[]).map(text).filter(Boolean)
            : undefined,
        }
      : undefined,
    wanted: text(j.wanted) && text(j.wanted) !== text(j.does) ? text(j.wanted) : undefined,
    builtWith: text(j.built_with) || undefined,
    beyond: readBeyond(j.beyond_done),
    tried: readTried(j.tried),
    triedBefore: readTried(j.tried_before),
    trying: readTrying(j.trying),
    code: readCode(j.code),
  };
}

function readCode(raw: unknown): Job["code"] {
  const out = list(raw)
    .map(obj)
    .filter((c) => text(c.ref))
    .slice(0, 5)
    .map((c) => ({ ref: text(c.ref), name: text(c.name) || text(c.ref) }));
  return out.length ? out : undefined;
}

function readTrying(raw: unknown): Job["trying"] {
  const t = obj(raw);
  const name = text(t.name);
  const since = typeof t.since === "number" && Number.isFinite(t.since) ? t.since : 0;
  if (!name || !since) return undefined;
  const cost = typeof t.cost_usd === "number" && Number.isFinite(t.cost_usd) ? t.cost_usd : undefined;
  const waiting = text(t.waiting) || undefined;
  return { name, since, ...(cost !== undefined ? { costUsd: cost } : {}), ...(waiting ? { waiting } : {}) };
}

/** "Trying: ground-station, started 3 min ago. Cloud model: about $0.02 so
 *  far." for the app being tried now, else "". */
export function tryingLine(job: Job, nowMs: number = Date.now()): string {
  const t = job.trying;
  if (!t) return "";
  const min = Math.max(0, Math.floor((nowMs / 1000 - t.since) / 60));
  const ago = min < 1 ? "just now" : `${min} min ago`;
  const cost = t.costUsd !== undefined ? ` Cloud model: about $${t.costUsd.toFixed(2)} so far.` : "";
  return `Trying: ${t.name}, started ${ago}.${cost}`;
}

function readTried(raw: unknown): Job["tried"] {
  const out = list(raw)
    .map(obj)
    .filter((t) => text(t.ref))
    .slice(0, 20)
    .map((t) => ({ ref: text(t.ref), name: text(t.name) || text(t.ref), why: text(t.why) }));
  return out.length ? out : undefined;
}

function readBeyond(raw: unknown): BeyondItem[] | undefined {
  const out = list(raw)
    .map(obj)
    .filter((i) => text(i.what))
    .slice(0, 60)
    .map((i) => ({ kind: text(i.kind), what: text(i.what), undo: text(i.undo), admin: i.admin === true }));
  return out.length ? out : undefined;
}

function readPart(raw: unknown): PartName | null {
  const p = obj(raw);
  const ref = text(p.ref);
  if (!ref) return null;
  return {
    ref,
    name: text(p.name) || ref,
    class: text(p.class),
    kind: text(p.kind),
    things: strings(p.things).filter((t) => THING_ID.test(t)),
    mine: fromYourGitHub(p) || undefined,
    url: /^https:\/\//.test(text(p.url)) ? text(p.url) : undefined,
  };
}

const SITE_STATES = ["on", "when_built", "not_on"] as const;

export function readBuildStatus(raw: unknown): BuildStatus {
  const d = obj(raw);
  const y = obj(d.yes);
  const parts = list(d.parts)
    .map(readPart)
    .filter((p): p is PartName => p !== null);
  const byRef = new Map(parts.map((p) => [p.ref, p]));
  // Each part's name and kind come with the status: no call per job.
  const named = (j: Job): Job => ({
    ...j,
    ingredients: j.ingredients.map((i) => {
      const p = byRef.get(i.ref);
      return p
        ? { ...i, name: i.name || p.name, kind: i.kind || p.kind || undefined, mine: i.mine || p.mine || undefined }
        : i;
    }),
  });
  return {
    // An older backend sends no `ready`: it is ready.
    ready: d.ready !== false,
    line: text(d.line),
    bot: text(d.bot),
    jobs: list(d.jobs)
      .map(readJob)
      .filter((j): j is Job => j !== null)
      .map(named),
    parts,
    sites: list(d.sites)
      .map(obj)
      .map((s) => ({
        row: text(s.row),
        host: text(s.host),
        state: oneOf(SITE_STATES, s.state, "not_on"),
        reason: text(s.reason),
      }))
      .filter((s) => s.row),
    setup: list(d.setup)
      .map(obj)
      .map((r) => ({
        id: text(r.id),
        kind: oneOf(SETUP_KINDS, r.kind, "setting"),
        label: text(r.label) || text(r.id),
        detail: text(r.detail),
        job_ids: strings(r.job_ids),
        done: r.done === true,
        ...(r.by_yes === true ? { by_yes: true } : {}),
        ...(text(r.pending) ? { pending: text(r.pending) } : {}),
        ...(text(r.town) ? { town: text(r.town) } : {}),
        ...(r.position === true ? { position: true } : {}),
        ...(text(r.said) ? { said: text(r.said) } : {}),
      }))
      .filter((r) => r.id),
    yes: { given: y.given === true, at: text(y.at), sha: /^[0-9a-f]{64}$/.test(text(y.sha)) ? text(y.sha) : "" },
    dropped: list(d.dropped)
      .map(obj)
      .map((x) => ({ ref: text(x.ref), name: text(x.name) || text(x.ref), why: text(x.why) }))
      .filter((x) => x.ref),
    knowledge: list(d.knowledge)
      .map(obj)
      .map((k) => ({
        id: text(k.id),
        title: text(k.title) || text(k.id),
        site: text(k.site),
        sizeWords: text(k.size_words),
        ticked: k.ticked === true,
        consent: list(k.consent).map(text).filter(Boolean),
        status: oneOf(KNOWLEDGE_STATES, k.status, ""),
        reason: text(k.reason),
      }))
      .filter((k) => k.id),
    toolsmithHold: text(d.toolsmith_hold),
    vaultWait: text(d.vault_wait),
    running: d.running === true,
    lines: list(d.lines)
      .map(obj)
      .map((l) => ({ agent: agentName(l.agent), text: text(l.text) }))
      .filter((l) => l.text),
    words: readWords(d.words),
  };
}

/** One job from GET /build/job/<id>: { job, parts: [{ref, name, class,
 *  kind, check}] }. Each part's words go onto the job's ingredient. */
export function readJobReply(raw: unknown): Job | null {
  const d = obj(raw);
  const job = readJob(d.job && typeof d.job === "object" ? d.job : d);
  if (!job) return null;
  const parts = new Map<string, Ingredient>();
  for (const raw of list(d.parts)) {
    const i = readIngredient(raw);
    if (i) parts.set(i.ref, i);
  }
  job.ingredients = job.ingredients.map((i) => ({ ...parts.get(i.ref), ...stripEmpty(i) }));
  return job;
}

function stripEmpty(i: Ingredient): Ingredient {
  const out: Ingredient = { ref: i.ref };
  if (i.name) out.name = i.name;
  if (i.kind) out.kind = i.kind;
  if (i.check) out.check = i.check;
  if (i.mine) out.mine = true;
  return out;
}

// ── refs and things ─────────────────────────────────────────────────────────

const THING_KINDS = ["skill", "tool", "app", "data", "book", "feed", "assembled", "builtin"];
export const THING_ID = /^[a-z0-9][a-z0-9_-]{0,100}$/;

const slug = (v: string) =>
  v
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^[-_]+|[-_]+$/g, "");

/** The thing id a ref names, or "" when it does not look like one. The
 *  backend's refs: "assembled:<id>" (thing assembled-<id>), "app:<id>" (an
 *  app of hers; a live card is the thing feed-<id>, see refThingIds),
 *  "builtin:routine" (no thing), or a find's own id (matched by name on the
 *  job page). */
export function thingIdOf(ref: string): string {
  const r = (ref || "").trim().toLowerCase();
  const m = /^([a-z]+):(.+)$/.exec(r);
  const id = m && THING_KINDS.includes(m[1]) ? `${m[1]}-${slug(m[2])}` : r;
  return THING_ID.test(id) ? id : "";
}

/** Every thing id a ref may be: "app:x" is the live card feed-x when x is
 *  one, else the app. */
export function refThingIds(ref: string): string[] {
  const id = thingIdOf(ref);
  if (!id) return [];
  return id.startsWith("app-") ? [`feed-${id.slice(4)}`, id] : [id];
}

function jobRefsOf(j: Job): string[] {
  return [...(j.pick ? [j.pick.ref] : []), ...j.ingredients.map((i) => i.ref)];
}

/** Every thing id a ref of this answer is: its own, and the things its part
 *  names (a live card made for a website is that website's part). */
function thingsOfRef(status: BuildStatus, ref: string): string[] {
  const part = (status.parts || []).find((p) => p.ref === ref);
  return [...refThingIds(ref), ...(part ? part.things : [])];
}

/** The job that uses this thing (its pick or one of its parts), or null. */
export function jobForThing(status: BuildStatus | null, thingId: string): Job | null {
  if (!status || !thingId) return null;
  return status.jobs.find((j) => jobRefsOf(j).some((r) => thingsOfRef(status, r).includes(thingId))) || null;
}

/** Every thing the build has placed: in a job, or under "Not used". */
export function placedThings(status: BuildStatus | null): Set<string> {
  const out = new Set<string>();
  if (!status) return out;
  const add = (r: string) => thingsOfRef(status, r).forEach((id) => out.add(id));
  status.jobs.forEach((j) => jobRefsOf(j).forEach(add));
  status.dropped.forEach((d) => add(d.ref));
  return out;
}

/** The thing id of the live card an app page shows, for an app id (the
 *  backend's things._slug, then "feed-"). */
export function feedThingId(appId: string): string {
  const slug = (appId || "").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^[-_]+|[-_]+$/g, "");
  return slug ? `feed-${slug}` : "";
}

/** The owner's apps minus the live cards the build placed (in a job or under
 *  "Not used"): those get no rail entry of their own. */
export function withoutPlacedFeeds<T extends { id: string; own?: boolean }>(
  apps: T[] | null,
  status: BuildStatus | null,
): T[] | null {
  const placed = placedThings(status);
  if (!apps || !placed.size) return apps;
  // The bot's own page always keeps its entry.
  return apps.filter((a) => a.own === true || !placed.has(feedThingId(a.id)));
}

/** The job page's address. */
export function jobHref(id: string): string {
  return `/dashboard/jobs/${encodeURIComponent(id)}`;
}

// ── words ───────────────────────────────────────────────────────────────────

/** A job's label: "Building: <app>" while Toolsmith runs a try for it
 *  (that always wins), else its status word. */
export function jobLabel(words: BuildWords, job: Job): string {
  if (job.trying && !job.trying.waiting) return `Building: ${job.trying.name}`;
  return statusWords(words, job.status);
}

/** The label for a status, from the answer's words. */
export function statusWords(words: BuildWords, status: JobStatus): string {
  return words.status[status] || FALLBACK_WORDS.status[status];
}

/** How BuildBot does this job, in one line: "Uses Guest List as it is",
 *  "Puts together a gallery from NASA EPIC Earth photos". `nameOf` turns a
 *  ref into the name shown. */
export function howLine(job: Job, words: BuildWords, nameOf: (ref: string) => string): string {
  const template = words.route[job.route] || FALLBACK_WORDS.route[job.route];
  const pickRef = job.pick?.ref || "";
  const pick =
    job.appUsed?.name ||
    (pickRef ? job.ingredients.find((i) => i.ref === pickRef)?.name || nameOf(pickRef) : "");
  // A book or a guide is not a tool it connects: it answers from it.
  if (job.route !== "D" && job.pick && ANSWER_CLASSES.includes(job.pick.class)) {
    const fallback = job.pick.class === "TEXT" ? "a book" : "a guide";
    return (words.answers || FALLBACK_WORDS.answers).replace("<name>", pick || fallback);
  }
  // An app BuildBot put together is the view itself, not one of its sources.
  const sources = job.ingredients
    .filter((i) => !thingIdOf(i.ref).startsWith("assembled-"))
    .map((i) => i.name || nameOf(i.ref))
    .filter(Boolean);
  const viewKey = job.view && !/^[a-z]+[-:]/.test(job.view) ? job.view : "";
  const view = (viewKey && words.view[viewKey]) || viewKey || "view";
  const filled = template
    .replace(/\{(pick|app|tool)\}/g, pick || sources[0] || "")
    .replace(/\{view\}|<view>/g, view)
    .replace(/\{sources\}/g, joinNames(sources.length ? sources : pick ? [pick] : []));
  const named = /\{(pick|app|tool|sources)\}/.test(template);
  let out = filled.replace(/\s+/g, " ").replace(/ from $/, "").trim();
  // The backend's words name no part ("Connects a tool"): say which.
  if (!named && job.route === "C" && sources.length) out = `${out} from ${joinNames(sources)}`;
  else if (!named && (job.route === "A" || job.route === "B") && pick) out = `${out}: ${pick}`;
  return out;
}

/** "a", "a and b", "a, b and c". */
export function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] || "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** A ref as words when nothing better is known: "feed-nasa_epic" -> "Nasa epic". */
export function refWords(ref: string): string {
  const words = (ref || "")
    .replace(/^[a-z]+[:-]/, "")
    .replace(/[_-]+/g, " ")
    .trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "";
}

/** Rows she still has to do herself. Programs, packages and the outside
 *  sites the list shows come with the one Yes; after it, a site the Yes
 *  could not switch on is hers again (`yesGiven`). */
export function openRows(setup: SetupRow[], yesGiven = false): SetupRow[] {
  return setup.filter(
    (r) =>
      !r.done &&
      ((HER_KINDS.includes(r.kind) && !r.by_yes && !(r.pending && !yesGiven)) ||
        (yesGiven && (YES_SITE_KINDS.includes(r.kind) || Boolean(r.by_yes)))),
  );
}

/** Is there anything her one Yes would build (a job that is not "nothing
 *  found", or a knowledge line still to add)? */
export function hasBuildable(status: BuildStatus): boolean {
  return status.jobs.some((j) => j.route !== "D") || (status.knowledge ?? []).some((k) => k.status !== "added");
}

/** The wizard's last screen title for a bot with jobs: it is not built
 *  before her Yes. "StargazerBot is ready to build", then "Building...", then
 *  "Built". */
export function handoffTitle(status: BuildStatus): string {
  if (!status.yes.given) return `${status.bot || "Your bot"} is ready to build`;
  const busy =
    status.running || status.jobs.some((j) => j.status === "building" || j.status === "queued" || j.status === "waiting_yes");
  return busy ? "Building..." : "Built";
}
