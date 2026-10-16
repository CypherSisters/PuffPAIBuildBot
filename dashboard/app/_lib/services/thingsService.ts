/**
 * Finding things other people built, and installing what she picks.
 *
 * The wizard's "Finding things for your bot" screen and the Add page call the
 * same backend routes (src/puffpai/finding/api.py), same-origin through the
 * /api/pai proxy. Every sentence shown comes from the backend; this file only
 * moves ids and words. Find ids travel in request bodies, never in an address.
 */

import type { MissingCardData } from "@/app/_interfaces/api";

const BASE = "/api/pai";

export type ThingType =
  | "skill"
  | "mcp"
  | "browser_app"
  | "data"
  | "server_app"
  | "cli"
  | "library"
  | "book";

export interface Find {
  id: string;
  site: string;
  site_name: string;
  type: ThingType;
  type_words: string;
  name: string;
  by: string;
  what: string;
  url: string;
  popularity_words: string;
  changed_words: string;
  licence_words: string;
  /** Its maker gave no licence at all (never a refusal: her call). */
  no_licence?: boolean;
  verdict: string;
  verdict_words: string;
  can: "install" | "add_mcp" | "integrate" | "look" | "add_book";
  can_words: string;
  can_trade: boolean;
  line: string;
  /** Books: how big the download is, in plain words. */
  size_words?: string;
  /** Books: at most two small free ones that clearly fit start ticked. */
  ticked?: boolean;
}

export interface FoundType {
  type: ThingType;
  type_words: string;
  finds: Find[];
}

export interface FoundJob {
  id: string;
  name: string;
  words: string;
  types: FoundType[];
  count: number;
  notes: string[];
}

export interface FindResult {
  jobs: FoundJob[];
  notes: string[];
  count: number;
  jobs_line?: string;
}

export interface InstallResult {
  results: { id: string; status: string; line: string; entry?: string; page?: string }[];
}

export interface JobView<T> {
  job: string;
  kind: string;
  lines: string[];
  done: boolean;
  error: string;
  result: T | null;
}

export interface Preview {
  ok: boolean;
  id: string;
  name: string;
  type_words: string;
  lines: string[];
  verdict_words: string;
  can: string;
  can_words: string;
  refuse: boolean;
  question: string;
  line?: string;
  /** An app that needs a program this computer does not have: the same
   *  "Install what's missing" card as chat (src/puffpai/deps.py). */
  missing?: MissingCardData;
}

export interface InstalledRow {
  entry: string;
  /** The find it came from, so Find more can mark it "Already in your bot". */
  id: string;
  name: string;
  type: string;
  type_words: string;
  from: string;
  pinned: string;
  installed_at: string;
  line: string;
}

/** One job the bot must be able to do, as the wizard worked it out. */
export interface WizardCapability {
  id: string;
  name: string;
  does: string;
  covered: boolean;
  line: string;
}

export interface WizardFind {
  title: string;
  intro: string;
  find: JobView<FindResult>;
  /** Once the search is done: what the bot must be able to do. */
  capabilities?: WizardCapability[];
  install: JobView<InstallResult> | null;
  settled: boolean;
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
      body: body ? JSON.stringify(body) : undefined,
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

const JOB_ID = /^fj_[0-9a-f]{12}$/;
const ENTRY_ID = /^t_[0-9a-f]{10}$/;

/** cap: one of the bot's capabilities ("Find something for this"): what is
 *  found is tagged with it, so the thing covers it once added. */
export function startFind(words: string, kinds?: string[], cap?: string) {
  return req<{ success: boolean; job: string; message?: string }>("/add/things/find", "POST", {
    words,
    kinds,
    ...(cap ? { cap } : {}),
  });
}

export function readJob<T>(job: string) {
  if (!JOB_ID.test(job)) return Promise.resolve({ ok: false, status: 404, data: {} as JobView<T> });
  return req<JobView<T>>(`/add/things/jobs/${job}`);
}

export function previewThing(id: string) {
  return req<Preview>("/add/things/preview", "POST", { id });
}

export function installThings(ids: string[]) {
  return req<{ success: boolean; job: string }>("/add/things/install", "POST", { ids });
}

export function getInstalled() {
  return req<{ installed: InstalledRow[] }>("/add/things/installed");
}

export function undoInstalled(entry: string) {
  if (!ENTRY_ID.test(entry)) return Promise.resolve({ ok: false, status: 404, data: { line: "" } });
  return req<{ ok: boolean; line: string }>(`/add/things/installed/${entry}/undo`, "POST", {});
}

export function getWizardFind() {
  return req<WizardFind>("/wizard/find");
}

export function wizardInstall(ids: string[]) {
  return req<{ install: JobView<InstallResult> }>("/wizard/find", "POST", { install: ids });
}

export function wizardFindDone() {
  return req<{ settled: boolean; message?: string }>("/wizard/find", "POST", { done: true });
}

/** Ask a job until it is done, handing over each view as it changes. */
export async function followJob<T>(
  job: string,
  onView: (view: JobView<T>) => void,
  everyMs = 700,
): Promise<JobView<T> | null> {
  for (;;) {
    const reply = await readJob<T>(job);
    if (!reply.ok) return null;
    onView(reply.data);
    if (reply.data.done) return reply.data;
    await new Promise((resolve) => setTimeout(resolve, everyMs));
  }
}
