/**
 * Proactive suggestions — the client half of "Send to Puff", phase 2.
 *
 * The backend classifies every upload in the background and queues a
 * suggestion ("I found 9 events — want them on the calendar?"). This module is
 * the shared transport: the dashboard's FloatingPuff and the standalone /send
 * page both poll through it.
 *
 * NOTHING here executes an action. `accept` only hands back the extracted
 * candidates so the normal preview-confirm modal can show them; the calendar
 * is still written one cal_add_event at a time, after a human ticks the boxes.
 *
 * PAIRING. At the default "knowledge" enforcement stage `pending` is a plain
 * read and stays open, and accept/dismiss are narrowly exempted as
 * read-equivalents (see app/_lib/server/pairing.ts — they only move a record in
 * the notification queue). At the "all" stage every call here answers 401
 * {"error":"pairing_required"} on an unpaired browser. That is a normal resting
 * state (a laptop that never paired), not an error — callers get
 * `status: "unpaired"`, and are expected to go quiet and back off for polling,
 * or to keep the card and say so for an action the user just took.
 */

import { API_URL } from "./api";
import type { IngestResult } from "../dashboard/apps/calendar/ImportEvents";
import type { RecipeResult } from "../dashboard/apps/freshtable/ImportRecipes";

export interface SuggestionCandidate {
  title: string;
  start_iso: string;
  end_iso: string;
  cat: string;
  location: string | null;
  recurrence: string | null;
}

export interface RecipeCandidate {
  name: string;
  ingredients: string;
  instructions: string;
  yield?: string | null;
  prep_time?: string | null;
}

export interface SuggestionAction {
  type: "calendar_import" | "recipe_import" | "none";
  params: {
    candidates?: SuggestionCandidate[];
    recipes?: RecipeCandidate[];
    notes?: string[];
    method?: string;
  };
}

export interface Suggestion {
  id: string;
  created_at: string;
  expires_at: string;
  doc_name: string;
  collection: string;
  zone: string;
  kind: string;
  confidence: number;
  summary: string;
  message: string;
  action: SuggestionAction;
  status: string;
}

export type PendingResult =
  | { status: "ok"; suggestions: Suggestion[] }
  /** 401 pairing_required — this device isn't paired. Stay silent. */
  | { status: "unpaired" }
  /** 404 — this build doesn't ship the route. Callers latch this and stop asking. */
  | { status: "not_found" }
  | { status: "error" };

const BASE = `${API_URL}/knowledge/suggestions`;

export async function fetchPendingSuggestions(): Promise<PendingResult> {
  try {
    const res = await fetch(`${BASE}/pending`, { cache: "no-store" });
    if (res.status === 401) return { status: "unpaired" };
    if (res.status === 404) return { status: "not_found" };
    if (!res.ok) return { status: "error" };
    const data = (await res.json()) as { suggestions?: Suggestion[] };
    return { status: "ok", suggestions: data?.suggestions ?? [] };
  } catch {
    return { status: "error" };
  }
}

/**
 * Outcome of accept/dismiss. These used to collapse to `null` / `void`, which
 * made a 401 indistinguishable from success: the UI hid the card, the server
 * never resolved anything, and the same suggestion reappeared on the next load
 * with no explanation. Callers must now branch.
 *
 *  ok       — the server resolved it. Hide the card.
 *  gone     — 404 / 409: already accepted, dismissed or expired. Also a genuine
 *             resolution as far as the UI is concerned — hide the card.
 *  unpaired — 401. The server did NOT resolve it. Keep the card.
 *  error    — network failure or any other non-2xx. Keep the card.
 */
export type ResolveStatus = "ok" | "gone" | "unpaired" | "error";

export type AcceptResult =
  /** `suggestion` is null only if a 2xx body arrived without the record. */
  | { status: "ok"; suggestion: Suggestion | null }
  | { status: "gone" | "unpaired" | "error" };

export type DismissResult = { status: ResolveStatus };

/** True when the server state actually changed (or was already changed). */
export function isResolved(status: ResolveStatus): boolean {
  return status === "ok" || status === "gone";
}

/** Map a response status to the non-ok outcomes. */
function failureFor(httpStatus: number): "gone" | "unpaired" | "error" {
  if (httpStatus === 401) return "unpaired";
  if (httpStatus === 404 || httpStatus === 409) return "gone";
  return "error";
}

/**
 * Accept — marks the suggestion resolved server-side and hands back the full
 * record so the caller can show its candidates in the preview-confirm modal.
 * Nothing is written to the calendar here.
 */
export async function acceptSuggestion(id: string): Promise<AcceptResult> {
  try {
    const res = await fetch(`${BASE}/${encodeURIComponent(id)}/accept`, {
      method: "POST",
    });
    if (!res.ok) return { status: failureFor(res.status) };
    const data = (await res.json().catch(() => null)) as {
      suggestion?: Suggestion;
    } | null;
    return { status: "ok", suggestion: data?.suggestion ?? null };
  } catch {
    return { status: "error" };
  }
}

export async function dismissSuggestion(id: string): Promise<DismissResult> {
  try {
    const res = await fetch(`${BASE}/${encodeURIComponent(id)}/dismiss`, {
      method: "POST",
    });
    return { status: res.ok ? "ok" : failureFor(res.status) };
  } catch {
    return { status: "error" };
  }
}

/**
 * Shape a suggestion's calendar payload into what ImportEvents already
 * consumes, so an accepted suggestion and a hand-picked file land in the
 * identical review-and-confirm screen.
 */
export function suggestionToIngestResult(s: Suggestion): IngestResult {
  const p = s.action?.params ?? {};
  return {
    success: true,
    method: (p.method as IngestResult["method"]) ?? "llm",
    events: p.candidates ?? [],
    notes: p.notes ?? [],
  };
}

/**
 * Shape a suggestion's recipe payload into what ImportRecipes consumes, so an
 * accepted recipe suggestion lands in the same review-and-confirm screen —
 * nothing is written to FreshTable until the user ticks and saves.
 */
export function suggestionToRecipeResult(s: Suggestion): RecipeResult {
  const p = s.action?.params ?? {};
  return {
    success: true,
    method: (p.method as RecipeResult["method"]) ?? "llm",
    recipes: p.recipes ?? [],
    notes: p.notes ?? [],
  };
}

/** Filename/collection comparison key: letters and digits only, no extension. */
export function nameKey(raw: string): string {
  return raw
    .replace(/\.[a-z0-9]{1,5}$/i, "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "");
}

/**
 * Loose match between an uploaded file and a queued suggestion. The backend
 * derives the collection name from the filename, but slugging rules differ
 * (and it may prettify doc_name), so compare on the normalised key and allow
 * containment either way — with a length floor so short names don't collide.
 */
export function matchesUpload(s: Suggestion, fileName: string): boolean {
  const file = nameKey(fileName);
  if (file.length < 4) return false;
  return [s.doc_name, s.collection].some((raw) => {
    const key = nameKey(raw ?? "");
    if (key.length < 4) return false;
    return key === file || key.includes(file) || file.includes(key);
  });
}
