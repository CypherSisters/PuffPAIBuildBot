/**
 * Sign-up keys: every call goes to the same-origin /api/pai proxy, which
 * attaches this device's paired identity. The backend builds every sentence
 * shown here; switching a key on needs the one-time yes the backend hands out
 * for exactly that key on exactly this device.
 *
 * A pasted key travels ONCE, in the body of one POST, and is never kept by
 * this module: no storage, no cache, no URL, no log. No reply ever holds it.
 */

const API_URL = "/api/pai";

/** Key ids the backend uses. Stricter than the backend on purpose. */
const KEY_ID = /^[a-z][a-z0-9_]{1,31}$/;

export type WalkThrough = {
  line: string;
  signup_url: string;
  steps: string[];
  how_long: string;
  cost_note: string;
  personal_use_only: boolean;
  card_required: boolean;
  label?: string;
};

export type SignUpKey = {
  id: string;
  name: string;
  for: string;
  state:
    | "works"
    | "missing"
    | "locked"
    | "stopped_working"
    | "paused_limit"
    | "switched_off"
    | "unknown_count";
  state_line: string;
  card_required: boolean;
  personal_use_only: boolean;
  cost_note: string;
  background: boolean;
  background_possible: boolean;
  uses_this_month: number | null;
  cap: number | null;
  uses_line: string;
  checked_at: string;
  walk_through: WalkThrough;
};

export type KeysPage = {
  title: string;
  line: string;
  vault_open: boolean;
  vault_set_up: boolean;
  keys: SignUpKey[];
};

type Result = { ok: boolean; message?: string; notes?: string[] };

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
      (data && ((data.message as string) || (data.error as string))) ||
      "That did not work.";
    throw new Error(message);
  }
  return data as T;
}

function checkedId(id: string): string {
  if (!KEY_ID.test(id)) throw new Error("There is no sign-up key by that name.");
  return id;
}

/** Only a plain https address from the backend's checked list is linked. */
export function safeSignupUrl(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) return null;
    if (parsed.port && parsed.port !== "443") return null;
    return parsed.toString();
  } catch {
    return null;
  }
}

export function getKeys(): Promise<KeysPage> {
  return call<KeysPage>("GET", "/keys");
}

/** Step one: the backend's own sentence and a one-time yes for this device. */
export function askToAdd(
  id: string,
  background: boolean,
): Promise<{ ticket: string; sentence: string; expires_in: number }> {
  return call("POST", `/keys/${checkedId(id)}/confirm`, { background });
}

/** Step two: the owner said yes to that sentence. The key goes in the body. */
export function addKey(
  id: string,
  value: string,
  ticket: string,
  background: boolean,
): Promise<Result> {
  return call<Result>("POST", `/keys/${checkedId(id)}`, { value, ticket, background });
}

export function checkKey(id: string): Promise<Result> {
  return call<Result>("POST", `/keys/${checkedId(id)}/check`);
}

export function removeKey(id: string): Promise<Result> {
  return call<Result>("POST", `/keys/${checkedId(id)}/remove`);
}

export function askToKeepWorkingWhenLocked(
  id: string,
): Promise<{ ticket: string; sentence: string; expires_in: number }> {
  return call("POST", `/keys/${checkedId(id)}/confirm`, { only_locked: true });
}

export function setKeepWorkingWhenLocked(
  id: string,
  on: boolean,
  ticket?: string,
): Promise<Result> {
  return call<Result>("POST", `/keys/${checkedId(id)}/locked`, { on, ticket });
}
