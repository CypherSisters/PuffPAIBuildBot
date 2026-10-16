// The Developer area's reads, and its command line. All through the paired proxy
// (/api/pai), which carries the backend key; the browser holds none.
//
//   GET /developer/cli/<page>  the commands for one page, in the backend's words
//   GET /developer/vault       where the vault lives and what is there
//   GET /developer/brain       the model, its address, Home Assistant (never a password)
//   GET /developer/terminal    the shell's name and the command line's lines
//   POST /developer/terminal/open|input|output|interrupt|close   a shell session
//
// Changes are made through the routes the existing pages already use, with the
// same yes those pages ask for.
import { apiGet } from "@/app/_lib/api";
import type { PrivacyConsent } from "../_lib/consentWords";

export type Shell = "windows" | "posix";
export type DevPage = "vault" | "brain" | "bridge";

export interface CliCommand {
  what: string;
  windows: string;
  posix: string;
  /** What to put in place of a <word> before pressing Enter. */
  fill?: string;
  /** What the command asks for when it runs (a secret is never in the line). */
  asks?: string;
}

export interface CliPanel {
  page: DevPage;
  box: Shell;
  folder: string;
  open_terminal: Record<Shell, string>;
  go_to_folder: Partial<Record<Shell, string>>;
  go_to_folder_line: string;
  /** The folder step in words, for the other kind of computer, where this
   *  computer's path would not work. Older backends do not send it. */
  go_to_folder_said?: Partial<Record<Shell, string>>;
  commands: CliCommand[];
  no_command_yet: string[];
  secrets_line: string;
}

export interface VaultPlace {
  folder: string;
  folder_there: boolean;
  drive_there: boolean;
  made: boolean | null;
  line: string;
  state: "open" | "locked" | "missing" | "unknown";
  waiting: number | null;
}

export interface BrainPlace {
  model: string;
  saved_model: string;
  address: string;
  address_from: string;
  model_file: string;
  home_assistant: { set_up: boolean; address: string };
  only_ollama_line: string;
  restart_line: string;
}

export interface PrivacyState extends PrivacyConsent {
  lines?: string[];
  vault?: "open" | "locked" | "missing" | "unknown";
  outside_services_on?: number | null;
  waiting_in_vault?: number | null;
}

async function orNull<T>(p: Promise<T>): Promise<T | null> {
  try {
    return await p;
  } catch {
    return null;
  }
}

export const getCli = (page: DevPage) =>
  orNull(apiGet<CliPanel>(`/developer/cli/${page}`, { cache: "no-store" }));
export const getVaultPlace = () =>
  orNull(apiGet<VaultPlace>("/developer/vault", { cache: "no-store" }));
export const getBrainPlace = () =>
  orNull(apiGet<BrainPlace>("/developer/brain", { cache: "no-store" }));
export const getPrivacy = () =>
  orNull(apiGet<PrivacyState>("/system/privacy", { cache: "no-store" }));

/** The internet setting, in the words the owner chose it by. */
export function internetWords(runEgress: string | undefined | null): string {
  switch (runEgress) {
    case "sealed":
      return "No internet";
    case "island":
      return "Home network, plus BuildBot's own tools";
    case "enumerated":
      return "Only the services you switched on";
    default:
      return "Not set yet";
  }
}

/** The vault's state, in plain words. */
export function vaultWords(state: string | undefined | null): string {
  switch (state) {
    case "locked":
      return "Locked";
    case "open":
      return "Open";
    case "missing":
      return "Not made yet";
    case "not_plugged_in":
      return "Its drive is not plugged in";
    default:
      return "Could not be read";
  }
}

// ── The command line (a real shell on this computer, /developer/terminal/*) ──
// The browser sends what she typed and the session's id; the backend holds
// the shell. Every route needs this paired device (the proxy adds the key),
// and a session answers only the device that opened it.

export interface TerminalExample {
  command: string;
  what: string;
}

export interface TerminalPage {
  shell: string;
  line: string;
  off: boolean;
  off_line: string;
  examples: TerminalExample[];
  window_line: string;
}

export interface TerminalOpened {
  id: string;
  cwd: string;
  shell: string;
}

export interface TerminalOutput {
  text: string;
  next: number;
  busy: boolean;
  alive: boolean;
  cwd: string;
  ended: string;
}

type Reply<T> = { ok: true; data: T } | { ok: false; message: string; code?: string };

async function send<T>(path: string, body: unknown, keepalive = false): Promise<Reply<T>> {
  try {
    const res = await fetch(`/api/pai${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      keepalive,
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data && data.ok !== false) return { ok: true, data: data as T };
    return {
      ok: false,
      code: data && data.code,
      message: (data && data.message) || "That did not work.",
    };
  } catch {
    return { ok: false, message: "No answer from your bot." };
  }
}

export const getTerminal = () =>
  orNull(apiGet<TerminalPage>("/developer/terminal", { cache: "no-store" }));
export const openShell = () => send<TerminalOpened>("/developer/terminal/open", {});
export const typeLine = (id: string, line: string) =>
  send<{ ok: true }>("/developer/terminal/input", { id, line: line.slice(0, 8000) });
export const readShell = (id: string, since: number, wait = true) =>
  send<TerminalOutput>("/developer/terminal/output", { id, since, wait });
export const stopCommand = (id: string) => send<{ ok: true }>("/developer/terminal/interrupt", { id });
export const closeShell = (id: string) => send<{ ok: true }>("/developer/terminal/close", { id }, true);
