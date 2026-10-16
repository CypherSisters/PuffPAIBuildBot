/**
 * "Add to <bot>": the one find -> check -> yes loop after the build.
 *
 * Every call goes to the same-origin /api/pai proxy, which attaches this
 * device's paired identity. The page sends only the words the owner typed
 * and item ids from its own search; the backend builds every sentence shown
 * (the browser never writes its own copy of what will leave), and a yes is a
 * one-time ticket the backend hands out for exactly the lines ticked, on
 * exactly this device.
 */

const API_URL = "/api/pai";

/** Stricter than the backend on purpose. */
const SESSION_ID = /^add_[0-9a-f]{16}$/;
const ITEM_ID = /^[a-z][a-z0-9_]*:[A-Za-z0-9_.:/-]{1,160}$/;
const SERVICE_ID = /^card-[a-z0-9][a-z0-9_-]{0,63}$/;
const ENTRY_ID = /^a_[0-9a-f]{8}$/;

export type AddResult = {
  id: string;
  n: number | null;
  label: string;
  status: "added" | "added_off" | "working" | "held" | "refused";
  line: string;
  placed?: boolean;
  progress?: string[];
  switch_on?: { service: string };
};

export type AddLine = {
  n: number | null;
  id: string;
  label: string;
  detail: string;
  size_words: string;
  from: string;
  can_add: boolean;
  result: AddResult | null;
  /** "Put it on my home page": offered for things with something to show. */
  home?: { offer: boolean; default: boolean };
  /** The job this line helps with, when her words became jobs. */
  need?: string | null;
  /** Where it comes from; `origin.yours` (or `from_your_github`) marks the
   *  owner's own GitHub projects. */
  origin?: { yours?: boolean } & Record<string, unknown>;
  from_your_github?: boolean;
};

/** One job read from her words, for grouping what was found. */
export type AddJob = { id: string; name: string };

export type AddGroup = { section: string; heading: string; items: AddLine[] };

export type AddFinds = {
  session: string;
  title: string;
  words: string;
  searched_line: string;
  notes: string[];
  searched_online: boolean;
  online: { possible: boolean; sends: string[]; line: string };
  groups: AddGroup[];
  jobs?: AddJob[];
  choose_line: string;
  nothing_found: string[];
  progress?: Record<string, string[]>;
};

export type AddedRow = {
  n: number;
  entry: string;
  label: string;
  added_on: string;
  from: string;
  websites: string[];
  home?: { can: boolean; on: boolean };
};

export type Looking = {
  active: boolean;
  line: string;
  libraries: { library: string; state: string; line: string }[];
};

/** The server gives up on the libraries after about 12 s; the page waits longer. */
const FIND_WAIT_MS = 30000;

async function call<T>(method: string, url: string, body?: unknown, waitMs?: number): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: waitMs ? AbortSignal.timeout(waitMs) : undefined,
    });
  } catch (err) {
    if (err instanceof DOMException && (err.name === "TimeoutError" || err.name === "AbortError")) {
      throw new Error("That took too long, so it was stopped. Try again.");
    }
    throw err;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = (data && (data.message as string)) || "That did not work.";
    throw new Error(message);
  }
  return data as T;
}

function sid(id: string): string {
  if (!SESSION_ID.test(id)) throw new Error("That list of finds has run out. Search again.");
  return id;
}

function ids(list: string[]): string[] {
  if (!list.length || list.some((i) => !ITEM_ID.test(i))) {
    throw new Error("Pick at least one thing to add.");
  }
  return list;
}

export function getAddPage(): Promise<{ title: string; bot_name: string; line: string; paired: boolean }> {
  return call("GET", `${API_URL}/add`);
}

export function findToAdd(words: string, online = false): Promise<AddFinds> {
  return call("POST", `${API_URL}/add/find`, { words: words.slice(0, 200), online }, FIND_WAIT_MS);
}

/** While "Look online too" runs: which libraries have answered so far. */
export function getLooking(): Promise<Looking> {
  return call("GET", `${API_URL}/add/looking`);
}

export function readFinds(id: string): Promise<AddFinds> {
  return call("GET", `${API_URL}/add/${sid(id)}`);
}

/** Step one of a yes: the backend's sentence and a one-time ticket. */
export function askToAdd(
  id: string,
  picked: string[],
): Promise<{ ticket: string; sentence: string; expires_in: number }> {
  return call("POST", `${API_URL}/add/${sid(id)}/confirm`, { ids: ids(picked) });
}

/** Step two: the owner said yes to that sentence. */
export function addThese(
  id: string,
  picked: string[],
  ticket: string,
  home: string[] = [],
): Promise<{ results: AddResult[] }> {
  const onHome = home.filter((i) => picked.includes(i));
  return call("POST", `${API_URL}/add/${sid(id)}/approve`, { ids: ids(picked), ticket, home: onHome });
}

export function askToSwitchOn(service: string): Promise<{ ticket: string; sentence: string }> {
  if (!SERVICE_ID.test(service)) throw new Error("There is no card by that name.");
  return call("POST", `${API_URL}/add/switch-on/${service}/confirm`);
}

export function switchOn(service: string, ticket: string): Promise<{ line: string }> {
  if (!SERVICE_ID.test(service)) throw new Error("There is no card by that name.");
  return call("POST", `${API_URL}/add/switch-on/${service}`, { ticket });
}

export function getAdded(): Promise<{ rows: AddedRow[] }> {
  return call("GET", `${API_URL}/add/added`);
}

/** Put an addition on the home page, or take it off (nothing leaves the computer). */
export function setOnHome(entry: string, on: boolean): Promise<{ line: string }> {
  if (!ENTRY_ID.test(entry)) throw new Error("There is nothing added under that name.");
  return call("POST", `${API_URL}/add/added/${entry}/home`, { on });
}

export function takeOut(entry: string): Promise<{ line: string }> {
  if (!ENTRY_ID.test(entry)) throw new Error("There is nothing added under that name.");
  return call("POST", `${API_URL}/add/added/${entry}/remove`);
}

export function getNotes(): Promise<{ notes: { at: string; terms: string[] }[]; line: string }> {
  return call("GET", `${API_URL}/add/notes`);
}

export function clearNotes(): Promise<{ cleared: number }> {
  return call("POST", `${API_URL}/add/notes/clear`);
}
