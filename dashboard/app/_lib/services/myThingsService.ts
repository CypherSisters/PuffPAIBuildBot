/**
 * Your bot's things: everything it has (skills, tool servers, apps, data,
 * books), each with its own page at /dashboard/things/<id>.
 *
 * Backend: src/puffpai/things_api.py, same-origin through the /api/pai proxy
 * (paired device only). Every sentence shown comes from the backend or from
 * the page recipe Toolsmith made; this file only moves ids and words.
 */

const BASE = "/api/pai";

export type ThingKind = "skill" | "tool_server" | "app" | "data" | "book" | "feed";

export interface Thing {
  id: string;
  kind: ThingKind;
  name: string;
  icon: string;
  blurb: string;
  on_home: boolean;
  page: string;
}

export interface Recipe {
  title: string;
  icon: string;
  line: string;
  try: string[];
  can: string[];
  made_by: "words" | "brain";
}

export interface JsonSchema {
  type?: string | string[];
  description?: string;
  enum?: unknown[];
  default?: unknown;
  minimum?: number;
  maximum?: number;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
}

export interface ThingTool {
  name: string;
  title: string;
  description: string;
  input_schema: JsonSchema;
  /** It names an MCP App screen (a ui:// page on its server). */
  has_screen?: boolean;
}

/** What the Run button gets back. `ui` is an MCP App screen's HTML, shown
 *  only in McpAppFrame's locked frame; `app_result` is the answer as that
 *  screen receives it. */
export interface ToolRun {
  ok: boolean;
  result?: string;
  message?: string;
  ui?: { uri: string; html: string };
  app_result?: unknown;
}

export interface ThingTable {
  name: string;
  columns: string[];
  rows: Record<string, string>[];
  count: number;
}

export interface ThingFeedItem {
  title?: string;
  detail?: string;
  link?: string;
}

export interface ThingFeed {
  status: string;
  message: string;
  fetched_at: string | null;
  items: ThingFeedItem[];
  credit: string;
  allowed: boolean;
  service_message: string;
  how_to_fix: string;
  looks_up: boolean;
  tool: string;
}

export interface ThingPage {
  ok: boolean;
  id: string;
  /** An old address of a job's ingredient: the page it lives on now. */
  redirect?: string;
  kind: ThingKind;
  kind_words: string;
  name: string;
  icon: string;
  from: string;
  on_home: boolean;
  recipe: Recipe;
  improving: boolean;
  tools?: ThingTool[];
  tables?: ThingTable[];
  /** A live card: its newest items (links only on its own service). */
  feed?: ThingFeed;
  open?: string;
  /** A local screen (127.0.0.1) its command printed last. */
  screen?: string;
  /** Results of daily tasks that use this skill, newest first. */
  daily?: { label?: string; at?: string; day?: number; text: string }[];
  /** A skill: programs it needs that are missing, and keys it asks for. */
  needs?: ThingNeeds;
  /** An app that runs on this computer as its authors built it. */
  host?: ThingHost;
  /** The bot's capabilities this thing covers (finding/capabilities.py). */
  covers?: string[];
  message?: string;
}

/** An app on this computer: its state and where it opens (127.0.0.1 only). */
export interface ThingHost {
  state: "running" | "starting" | "stopped" | "set_up" | "missing";
  url: string;
  port?: number | null;
  can_run: boolean;
  wanted_on?: boolean;
  how_words?: string;
  why?: string;
  commands?: string[];
  tools?: string[];
  keys?: string[];
  note?: string;
}

/** One thing that covers a capability, and how it does it. */
export interface CapabilityThing {
  id: string;
  name: string;
  kind: ThingKind;
  how: string;
  page: string;
}

/** One job this bot was built for, and what does it (or nothing yet). */
export interface Capability {
  id: string;
  name: string;
  does: string;
  covered: boolean;
  things: CapabilityThing[];
  line: string;
  /** The words "Find something for this" searches with. */
  find: string;
}

export interface ThingLookup {
  ok: boolean;
  message: string;
  topic?: string;
  items?: ThingFeedItem[];
  credit?: string;
  how_to_fix?: string;
}

export interface ThingNeedKey {
  name: string;
  /** Kept in the vault for this skill (the value never comes back). */
  set: boolean;
  prompt: string;
  help: string;
  /** Where to get one: a link on its service's site, or the skill's homepage. */
  get_url?: string;
  /** "declared" (its settings) or "text" (only named in its text). */
  from?: string;
}

/** "Install what's missing": the exact steps one yes runs, and what she
 *  installs herself. `plan` is "" when nothing can be installed. */
export interface ThingInstall {
  plan: string;
  steps: string[];
  manual: string[];
}

export interface ThingNeeds {
  line: string;
  programs_missing: string[];
  keys: ThingNeedKey[];
  install?: ThingInstall;
}

export interface InstallReply {
  ok: boolean;
  message: string;
  lines?: string[];
  needs?: ThingNeeds;
}

export interface ThingMedia {
  name: string;
  kind: "image" | "audio" | "video";
  size: number;
  modified: number;
}

export interface Reply<T> {
  ok: boolean;
  status: number;
  data: T;
}

/** The backend's thing ids: kind, a dash, then where it lives. */
export const THING_ID = /^[a-z0-9][a-z0-9_-]{0,100}$/;

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

function bad<T>(data: T): Promise<Reply<T>> {
  return Promise.resolve({ ok: false, status: 404, data });
}

export function listThings() {
  return req<Thing[]>("/things");
}

/** What this bot can do: each capability and the things that cover it. */
export function getCapabilities() {
  return req<{ ok: boolean; capabilities: Capability[] }>("/things/capabilities");
}

/** A live card that can search looks one plain subject up on its own service. */
export function lookupThing(id: string, topic: string) {
  if (!THING_ID.test(id)) return bad({ ok: false, message: "" } as ThingLookup);
  return req<ThingLookup>(`/things/${id}/lookup`, "POST", { topic });
}

export function getThing(id: string) {
  if (!THING_ID.test(id)) return bad({ message: "" } as ThingPage);
  return req<ThingPage>(`/things/${id}`);
}

/** Pictures, sound and video the thing made in its own folder. */
export function getThingMedia(id: string) {
  if (!THING_ID.test(id)) return bad({ ok: false, files: [] as ThingMedia[] });
  return req<{ ok: boolean; files: ThingMedia[] }>(`/things/${id}/media`);
}

/** Where one of them is read from (read-only, checked by the backend). */
export function thingMediaUrl(id: string, name: string): string {
  return `${BASE}/things/${id}/media/file?name=${encodeURIComponent(name)}`;
}

export function setThingHome(id: string, on: boolean) {
  if (!THING_ID.test(id)) return bad({ message: "" });
  return req<{ ok: boolean; on_home?: boolean; message: string }>(`/things/${id}/home`, "POST", { on });
}

export function removeThing(id: string) {
  if (!THING_ID.test(id)) return bad({ ok: false, message: "" });
  return req<{ ok: boolean; message: string }>(`/things/${id}`, "DELETE");
}

/** fromApp: an MCP App screen asked for this call and she said yes on the page. */
export function runThingTool(id: string, tool: string, args: Record<string, unknown>, fromApp = false) {
  if (!THING_ID.test(id)) return bad({ ok: false, result: "", message: "" } as ToolRun);
  return req<ToolRun>(`/things/${id}/run`, "POST", {
    tool,
    args,
    ...(fromApp ? { from_app: true } : {}),
  });
}

/** "Put the key in my vault": kept for this skill only. The value goes in
 *  the body, once, and is never sent back. */
export function putThingKey(id: string, name: string, value: string) {
  if (!THING_ID.test(id)) return bad({ ok: false, message: "" } as { ok: boolean; message: string; needs?: ThingNeeds });
  return req<{ ok: boolean; message: string; needs?: ThingNeeds }>(`/things/${id}/key`, "POST", { name, value });
}

/** Start, stop or restart an app that runs on this computer. */
export function hostAction(id: string, action: "start" | "stop" | "restart") {
  if (!THING_ID.test(id)) return bad({ ok: false, message: "" } as { ok: boolean; message: string; host?: ThingHost });
  return req<{ ok: boolean; message: string; host?: ThingHost }>(`/things/${id}/host`, "POST", { action });
}

/** Its last log lines (secrets hidden by the backend). */
export function hostLogs(id: string, which: "run" | "install") {
  if (!THING_ID.test(id)) return bad({ ok: false, text: "" } as { ok: boolean; text: string });
  return req<{ ok: boolean; text: string }>(`/things/${id}/host/logs?which=${which}`);
}

/** Her one yes to an "Install what's missing" plan she was shown. */
export function installMissing(plan: string) {
  if (!/^[A-Za-z0-9_-]{8,40}$/.test(plan)) return bad({ ok: false, message: "" } as InstallReply);
  return req<InstallReply>("/things/install-missing", "POST", { plan_id: plan, yes: true });
}

export function removeThingKey(id: string, name: string) {
  if (!THING_ID.test(id)) return bad({ ok: false, message: "" } as { ok: boolean; message: string; needs?: ThingNeeds });
  return req<{ ok: boolean; message: string; needs?: ThingNeeds }>(`/things/${id}/key`, "DELETE", { name });
}
