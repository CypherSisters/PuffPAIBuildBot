/**
 * GitHub: connecting the owner's account, so the bot searches better and can
 * use her own projects. Every call goes through the same-origin /api/pai
 * proxy (prefix "github" in proxyAllowlist.ts).
 *
 * THE TOKEN NEVER STAYS IN THE BROWSER. It is sent once in a POST body and
 * the backend keeps it in the vault. Nothing here writes it to localStorage,
 * sessionStorage, a cookie, the address or the console.
 */

const BASE = "/api/pai";

export interface GitHubStatus {
  connected: boolean;
  login: string;
  name: string;
  avatar_url: string;
  scopes: string[];
  method: "token" | "device" | "";
  device_available: boolean;
  can_read_private: boolean;
  vault_locked: boolean;
  /** False only when this computer has no vault yet (an older backend: true). */
  vault_set_up: boolean;
  /** Permissions the token has beyond what the bot needs, when any. */
  extra_scopes: string[];
  /** GitHub's SEARCH limit (what a search spends), per rate_window. */
  rate: { limit: number; remaining: number } | null;
  rate_window: "minute" | "hour";
}

/** What POST /github/disconnect sends back. */
export interface Disconnected {
  ok?: boolean;
  line?: string;
  revoke_url?: string;
  message?: string;
  error?: string;
}

export interface DeviceCode {
  user_code: string;
  verification_uri: string;
  interval: number;
  expires_in: number;
}

export interface TokenHelp {
  url: string;
  steps: string[];
}

export interface GitHubReply<T> {
  ok: boolean;
  status: number;
  data: T;
}

async function req<T>(path: string, method = "GET", body?: unknown): Promise<GitHubReply<T>> {
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
      /* empty / non-JSON body */
    }
    return { ok: res.ok, status: res.status, data: data as T };
  } catch {
    return { ok: false, status: 0, data: {} as T };
  }
}

const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((s): s is string => typeof s === "string" && s.length > 0) : [];

/** The status answer, read defensively (an older backend sends less). */
export function readStatus(raw: unknown): GitHubStatus {
  const d = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const r = (d.rate && typeof d.rate === "object" ? d.rate : null) as Record<string, unknown> | null;
  const method = d.method === "token" || d.method === "device" ? d.method : "";
  return {
    connected: d.connected === true,
    login: typeof d.login === "string" ? d.login : "",
    name: typeof d.name === "string" ? d.name : "",
    avatar_url: typeof d.avatar_url === "string" ? d.avatar_url : "",
    scopes: strings(d.scopes),
    method,
    device_available: d.device_available === true,
    can_read_private: d.can_read_private === true,
    vault_locked: d.vault_locked === true,
    vault_set_up: d.vault_set_up !== false,
    extra_scopes: strings(d.extra_scopes),
    rate:
      r && typeof r.remaining === "number" && typeof r.limit === "number"
        ? { limit: r.limit, remaining: r.remaining }
        : null,
    rate_window: d.rate_window === "minute" ? "minute" : "hour",
  };
}

export async function getGitHubStatus(): Promise<GitHubReply<GitHubStatus>> {
  const r = await req<unknown>("/github/status");
  return { ...r, data: readStatus(r.data) };
}

/** Sends the token once. The caller clears its field straight after. */
export function sendGitHubToken(token: string): Promise<GitHubReply<{ message?: string; error?: string }>> {
  return req("/github/token", "POST", { token });
}

export function startGitHubDevice(): Promise<GitHubReply<DeviceCode & { message?: string; error?: string }>> {
  return req("/github/device/start", "POST", {});
}

export function pollGitHubDevice(): Promise<
  GitHubReply<{ connected?: boolean; pending?: boolean; message?: string; error?: string }>
> {
  return req("/github/device/poll", "POST", {});
}

export function disconnectGitHub(): Promise<GitHubReply<Disconnected>> {
  return req("/github/disconnect", "POST", {});
}

export function getGitHubTokenHelp(): Promise<GitHubReply<TokenHelp>> {
  return req<TokenHelp>("/github/token-help");
}

/** The message a refusal carries, or a plain fallback. */
export function refusalWords(data: { message?: string; error?: string } | undefined, status: number): string {
  if (status === 0) return "No answer from this computer. Nothing was changed.";
  return data?.message || data?.error || "GitHub did not accept that. Nothing was changed.";
}

/**
 * True when a find or a part came from the owner's own GitHub. The backend
 * marks it on the item's `origin` ({yours: true}) or as `from_your_github`;
 * anything else (or nothing) is false.
 */
export function fromYourGitHub(raw: unknown): boolean {
  if (!raw || typeof raw !== "object") return false;
  const d = raw as Record<string, unknown>;
  if (d.from_your_github === true) return true;
  const o = d.origin;
  return !!o && typeof o === "object" && (o as Record<string, unknown>).yours === true;
}
