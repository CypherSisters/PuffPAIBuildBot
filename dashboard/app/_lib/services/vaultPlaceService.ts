// Where the vault lives. All through the paired proxy
// (/api/pai), which carries the backend key; the browser holds none.
//
//   GET  /vault/location         where it lives now, and the one question
//   POST /vault/location/check   would this folder do? (changes nothing)
//   GET  /vault/location/browse  the drives, or the folders inside one
//   POST /vault/location         here | folder | existing (that vault's PIN)
//   POST /vault/move             move it (the PIN)
//
// Changes need a paired device. A PIN only ever travels in a POST body, is
// held in the calling component's state only, and is cleared once sent.
import { apiPostRaw } from "@/app/_lib/api";

export type PlaceChoice = "here" | "folder" | "existing";

export interface PlaceChoiceWords {
  value: PlaceChoice;
  label: string;
  help: string;
}

export interface VaultLocation {
  ok: boolean;
  folder: string;
  drive_there: boolean;
  made: boolean | null;
  line: string;
  default_folder: string;
  is_default: boolean;
  network: boolean;
  network_line: string;
  question: string;
  choices: PlaceChoiceWords[];
  encrypted_drive_line: string;
  not_a_vault_line: string;
  inbox: string;
  paired: boolean;
}

export interface PlaceReply {
  ok: boolean;
  code?: string;
  message?: string;
  /** A bare drive: the folder offered on it. */
  suggestion?: string;
  folder?: string;
  network?: boolean;
  lines?: string[];
  retry_after?: number;
}

export interface BrowseRow {
  name: string;
  path: string;
  vault?: boolean;
  network?: boolean;
}

export interface BrowseReply {
  ok: boolean;
  message?: string;
  folder: string;
  parent: string;
  drives: BrowseRow[];
  folders: BrowseRow[];
  home: string;
  more: boolean;
  vault?: boolean;
  network?: boolean;
}

const BASE = "/api/pai";

async function getRaw<T>(path: string): Promise<{ ok: boolean; status: number; data: T | null }> {
  try {
    const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
    const data = (await res.json().catch(() => null)) as T | null;
    return { ok: res.ok, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: null };
  }
}

const NOT_ANSWERING: PlaceReply = {
  ok: false,
  message: "Your bot did not answer, so nothing was changed.",
};

async function post(path: string, body: Record<string, unknown>): Promise<PlaceReply> {
  try {
    const reply = await apiPostRaw<PlaceReply>(path, body);
    return reply.data ?? { ...NOT_ANSWERING, ok: false };
  } catch {
    return NOT_ANSWERING;
  }
}

export async function getVaultLocation(): Promise<VaultLocation | null> {
  const reply = await getRaw<VaultLocation>("/vault/location");
  return reply.ok ? reply.data : null;
}

export async function browseFolders(folder?: string): Promise<BrowseReply | { ok: false; message: string }> {
  const query = folder ? `?folder=${encodeURIComponent(folder)}` : "";
  const reply = await getRaw<BrowseReply & { message?: string }>(`/vault/location/browse${query}`);
  if (reply.data && reply.ok) return reply.data;
  return { ok: false, message: reply.data?.message || NOT_ANSWERING.message! };
}

export function setVaultLocation(args: {
  choice: PlaceChoice;
  folder?: string;
  makeSubfolder?: boolean;
  pin?: string;
  currentPin?: string;
}): Promise<PlaceReply> {
  const body: Record<string, unknown> = { choice: args.choice, make_subfolder: !!args.makeSubfolder };
  if (args.folder) body.folder = args.folder;
  if (args.pin) body.pin = args.pin;
  if (args.currentPin) body.current_pin = args.currentPin;
  return post("/vault/location", body);
}

export const moveVault = (folder: string, pin: string, makeSubfolder = false) =>
  post("/vault/move", { folder, pin, make_subfolder: makeSubfolder });

/** A network folder, as typed: \\server\share or //server/share. */
export function looksLikeNetwork(folder: string): boolean {
  const text = folder.trim();
  return text.startsWith("\\\\") || text.startsWith("//");
}
