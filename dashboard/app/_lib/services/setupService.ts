// Setup / onboarding service — talks to the backend /setup/* endpoints through
// the same-origin proxy (/api/pai). The proxy injects the server-side API key,
// so no key ever ships to the browser. Every call returns { ok, status, data }
// so callers can react to validation errors (400/409) and a down backend (502)
// without exceptions.
import { isBackendDown as backendDown, type StatusReply } from "../setupGate";

/** Same-origin proxy, relative by design: no backend host ships in the bundle,
 *  and a LAN phone, the kiosk and the box itself all resolve it identically.
 *  NOT `api.ts`'s API_URL — that one defaults to an absolute http://localhost:8000
 *  and would bypass the proxy (and therefore the server-side key) here. */
const BASE = "/api/pai";

export interface SetupStatus {
  ollama: {
    reachable: boolean | null;
    models: string[];
    selected: string;
    url: string;
  };
  vault: { initialized: boolean };
  /** `problem`: a plain line when a kept token cannot be opened now, else "". */
  ha: { configured: boolean; url: string; problem?: string };
  knowledge: { zim_dir: string; auto_index: boolean };
  complete: boolean;
}

export interface Reply<T = any> {
  ok: boolean;
  status: number;
  data: T;
}

/** The proxy attaches the backend key; the browser sends none. */
function authHeaders(): Record<string, string> {
  return { "Content-Type": "application/json" };
}

/** How long the first-run status check may hang before we call the backend
 *  down. Without this, a proxy that accepts the socket and never answers leaves
 *  the wizard on its splash screen with no way out. */
export const STATUS_TIMEOUT_MS = 10_000;

async function req<T = any>(
  path: string,
  method: string = "GET",
  body?: unknown,
  timeoutMs?: number,
): Promise<Reply<T>> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers: authHeaders(),
      body: body ? JSON.stringify(body) : undefined,
      cache: "no-store",
      signal: timeoutMs ? AbortSignal.timeout(timeoutMs) : undefined,
    });
    let data: any = {};
    try {
      data = await res.json();
    } catch {
      /* empty / non-JSON body */
    }
    return { ok: res.ok, status: res.status, data };
  } catch {
    // Network-level failure or timeout (dev server up, backend down before the
    // proxy even responds). Surface as a 0 so callers treat it as "backend
    // down" rather than waiting forever.
    return { ok: false, status: 0, data: {} as T };
  }
}

/** Fetch setup status. Pass probeOllama=false for a fast check (no network
 *  round-trip to Ollama) — used by the first-run gate. */
export function getSetupStatus(probeOllama = true): Promise<Reply<SetupStatus>> {
  return req<SetupStatus>(
    `/setup/status?ollama=${probeOllama ? "1" : "0"}`,
    "GET",
    undefined,
    STATUS_TIMEOUT_MS,
  );
}

export function saveOllama(model: string, url?: string): Promise<Reply> {
  return req("/setup/ollama", "POST", url ? { model, url } : { model });
}

/** What POST /setup/vault sends back. `recovery_code` is shown exactly once,
 *  here, and is stored nowhere — the caller must make the owner write it down.
 *  `recovery_code_notice` is the backend's own wording; do not restate it. */
export interface VaultCreated {
  success?: boolean;
  initialized?: boolean;
  recovery_issued?: boolean;
  recovery_code?: string;
  recovery_code_notice?: string;
  already_initialized?: boolean;
  message?: string;
}

export function createVault(password: string): Promise<Reply<VaultCreated>> {
  return req<VaultCreated>("/setup/vault", "POST", { password });
}

// ── BuildBot wizard: brain menu ───

export interface Benchmark {
  silicon_label?: string;
  classify_p50_s?: number;
  plan_p50_s?: number;
  chat_range_s?: string;
  /** False when the measurement is NOT from this machine's own board.
   *
   *  History: the backend attached a benchmark by hardware CLASS, so every box
   *  with a discrete GPU was told "measured on Jetson AGX Orin 64GB: routes a
   *  request in ~1.32s" — a real number from somebody else's silicon, printed as
   *  if it were this machine's. `model_catalog._benchmark_for` now applies the
   *  board-identity check too and returns None when it fails, so on this build a
   *  benchmark that ARRIVES is already this board's.
   *
   *  So the UI's rule is "false is authoritative, absent means the backend
   *  gated it by omission": an explicit `false` never prints a figure, which is
   *  the flag a backend can use to send the number for context without claiming
   *  it. Requiring `true` instead would throw away a genuine measurement on the
   *  hardware it was taken on, which is the other half of being honest. */
  board_matches?: boolean;
}

export interface BrainOption {
  id: string;
  label: string;
  params: string;
  disk_gb: number | null;
  fit: "comfortable" | "tight";
  installed: boolean;
  benchmark: Benchmark | null;
  benchmark_note: string | null;
  /** A caveat about running THIS model on THIS machine, in the backend's words:
   *  the VRAM one ("19 GB of weights against 8 GB of VRAM — the rest runs on the
   *  CPU"). `vram_note` is what fit_menu sends; the other two spellings are
   *  accepted because this type is read by pages that also talk to older
   *  backends, and a caveat is the last thing to drop on a spelling. */
  vram_note?: string | null;
  note?: string | null;
  gpu_note?: string | null;
}

export interface BrainMenu {
  hardware_class: string;
  total_ram_gb: number | null;
  options: BrainOption[];
  too_big: { id: string; label: string; min_ram_gb: number }[];
  recommended: { id: string; label: string; reason: string; fit: string; installed: boolean; disk_gb: number | null } | null;
  message: string | null;
}

/** Brain menu, fitted RAM-first to this hardware (or a stated correction). */
export function getBrainMenu(statedRam?: number): Promise<Reply<{ menu: BrainMenu; summary: string[] }>> {
  return req("/setup/brain-menu", "POST", statedRam ? { stated_ram: statedRam } : {});
}

/** True when the reply indicates the backend itself is unreachable (the proxy
 *  502s, or the fetch failed outright). Distinguishes "backend down" from a
 *  normal validation error so the UI can show the right guidance. */
export function isBackendDown(reply: Reply): boolean {
  return backendDown(reply as StatusReply);
}

export { classifySetupReply, type SetupProblem } from "../setupGate";
