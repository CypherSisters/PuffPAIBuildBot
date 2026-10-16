/**
 * "What leaves your computer": every outside service, its switch, and the
 * log of every request BuildBot made. Every call goes to the same-origin
 * /api/pai proxy, which attaches this device's paired identity; the backend
 * builds every sentence shown here (the browser never writes its own copy of
 * what will leave), and switching ON needs the one-time yes the backend hands
 * out for exactly that switch on exactly this device.
 */

const API_URL = "/api/pai";

/** Service ids the backend uses. Stricter than the backend on purpose. */
const SERVICE_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export type OutsideService = {
  id: string;
  number: number;
  name: string;
  for: string;
  what_leaves: string;
  websites: string[];
  state: "on" | "off" | "not added" | "not switchable";
  state_line: string;
  can_switch: boolean;
  switch_to: "on" | "off" | null;
  why_cannot_switch: string;
  turned_on_by: string;
  turned_on_at: string;
  /** The whole "Switched on by you, today." sentence from the backend, or
   *  "" (a service on only because BuildBot comes that way says so in its
   *  state line). */
  turned_on_line?: string;
  last_used: string;
  today: { sent: number; stopped: number };
  how_to_switch: string;
};

export type OutsidePage = {
  title: string;
  line: string;
  not_seen_line: string;
  not_seen: string[];
  home_network_line: string;
  log_line: string;
  /** Plain words about the switch list itself (e.g. it was damaged), or "". */
  notice?: string;
  /** True when the owner may hide that note. Hiding it switches nothing on. */
  notice_can_ok?: boolean;
  services: OutsideService[];
};

export type OutsideLogRow = {
  time: string;
  service: string;
  website: string;
  what: string;
  result: string;
  outcome: "done" | "refused" | "failed";
  line: string;
};

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
  if (!SERVICE_ID.test(id)) throw new Error("There is no outside service by that name.");
  return id;
}

export function getOutside(): Promise<OutsidePage> {
  return call<OutsidePage>("GET", "/outside");
}

export function getOutsideLog(hours = 24): Promise<{ rows: OutsideLogRow[] }> {
  const h = Math.max(1, Math.min(720, Math.round(hours)));
  return call<{ rows: OutsideLogRow[] }>("GET", `/outside/log?hours=${h}`);
}

export function switchOff(id: string): Promise<{ ok: boolean }> {
  return call<{ ok: boolean }>("POST", `/outside/${checkedId(id)}/off`);
}

/** The owner saw the "switch list was damaged" note. Switches nothing on. */
export function hideNotice(): Promise<{ ok: boolean }> {
  return call<{ ok: boolean }>("POST", "/outside/notice/ok");
}

/** Step one of switching on: the backend's own sentence and a one-time yes. */
export function askToSwitchOn(
  id: string,
): Promise<{ ticket: string; sentence: string; expires_in: number }> {
  return call("POST", `/outside/${checkedId(id)}/confirm`);
}

/** Step two: the owner said yes to that sentence. */
export function switchOn(id: string, ticket: string): Promise<{ ok: boolean }> {
  return call<{ ok: boolean }>("POST", `/outside/${checkedId(id)}/on`, { ticket });
}
