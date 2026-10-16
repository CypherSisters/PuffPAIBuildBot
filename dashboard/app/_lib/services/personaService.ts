/**
 * The owner's own apps — made, previewed, listed and deleted from the browser.
 *
 * `puffpai persona new` has written a persona from the terminal since the
 * scaffold landed. These four calls are the same thing for somebody who never
 * opens one, and every one of them goes to the SAME backend module, so the two
 * paths cannot end up validating different names or writing different files.
 *
 * WHY THE PREVIEW IS A FETCH AND NOT A FUNCTION
 * --------------------------------------------
 * The builder page shows the words the bot will understand before the owner
 * commits to them. Deriving that list here — lowercasing the name, splitting
 * the goal, dropping stopwords — would be a second implementation of rules that
 * live in Python, and the first time the two drifted the page would be showing
 * words the installed manifest does not carry. One truth, one round trip.
 *
 * The API key never reaches the browser: every call goes through /api/pai,
 * which attaches it server-side (see app/_lib/server/proxyAllowlist.ts, where
 * the "persona" prefix is listed deliberately).
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import { ApiError, apiDelete, apiGet, apiPatch, apiPost } from "../api";

/** What `POST /persona/preview` and the `persona` block of a scaffold reply
 *  both carry. The paths are absolute, on the box, and are shown so an owner
 *  knows which two files are theirs to edit afterwards. */
export interface PersonaPreview {
  id: string;
  name: string;
  goal: string;
  blurb: string;
  /** Data keys: `harvest log` typed becomes `harvest_log` stored. */
  lists: string[];
  /** The same keys as the owner said them, for anything user-facing. */
  list_labels: string[];
  /** The words the router will match on once this is installed. */
  keywords: string[];
  examples: string[];
  says: string[];
  route: string;
  surfaces: string[];
  app_path: string;
  pack_path: string;
  /** Something is already installed under this id. Not a refusal on its own —
   *  the page says so and stops offering Create. */
  taken: boolean;
}

/** One app the owner added, with what can be said to it. */
export interface OwnerApp {
  id: string;
  name: string;
  blurb: string;
  /** The goal the persona was scaffolded from. Absent on older backends, which
   *  is why the dashboard still falls back to stripping the blurb prefix. */
  goal?: string;
  route: string;
  lists: string[];
  list_labels: string[];
  /** One sentence that demonstrably works, e.g. "add tomatoes to the plantings
   *  list". Empty when the app keeps no lists. */
  try: string;
  /** True for a scaffolded persona with no list and no saved row. The Apps
   *  page and the home launcher leave it out. Absent on older backends. */
  empty?: boolean;
  /** True for the bot's own page (the place the build made for itself), not
   *  an app the owner added: it offers no "Remove this app". */
  own?: boolean;
}

export interface PersonaRecord {
  id: string;
  display_name: string;
  blurb: string;
  apps: string[];
  missing_apps: string[];
}

export interface PersonaListReply {
  personas: PersonaRecord[];
  apps: OwnerApp[];
  user_apps_dir: string;
  user_packs_dir: string;
}

export interface ScaffoldRequest {
  /** Optional: derived from the name when absent. */
  id?: string;
  name: string;
  goal?: string;
  lists?: string[];
}

export interface ScaffoldReply {
  written: boolean;
  persona: PersonaPreview & { installed: boolean; pack_installed: boolean };
  apps: string[];
  rescanned: boolean;
}

/** What went wrong, in the backend's own words. The refusals here are written
 *  for the owner ("That is 9 lists; 8 is as many as one scaffolded app
 *  carries"), so they are shown verbatim rather than translated. */
export function personaProblem(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return "The box did not answer, so nothing was written.";
}

/** The manifest that WOULD be written. Writes nothing. */
export function previewPersona(req: ScaffoldRequest): Promise<PersonaPreview> {
  return apiPost<PersonaPreview>("/persona/preview", req);
}

/** Write it, and rescan so it is installed before this promise resolves. */
export function scaffoldPersona(req: ScaffoldRequest): Promise<ScaffoldReply> {
  return apiPost<ScaffoldReply>("/persona/scaffold", req);
}

export function listPersonas(): Promise<PersonaListReply> {
  return apiGet<PersonaListReply>("/persona/list");
}

/** An edit to one of the owner's own apps. Every field is optional, and an
 *  absent one means "leave it alone". */
export interface PersonaPatch {
  /** The display name. The id never changes: it is the folder, the store key and
   *  the route, and renaming those would orphan the data. */
  name?: string;
  /** Lists to ADD. Nothing is ever dropped by an edit — removing a list would
   *  take its contents with it, so that is a delete-and-rebuild, not a rename. */
  add_lists?: string[];
  /** {old: new} — a list keeps its contents and changes its label. */
  rename_lists?: Record<string, string>;
}

/** What a successful PATCH answers with: the app as it now is, plus the lines
 *  the CLI prints for the same edit. */
export interface PersonaPatchReply {
  edited: boolean;
  persona: PersonaPreview & { renamed?: Record<string, string>; added?: string[] };
  summary?: string[];
}

/** True when the backend does not have this route at all.
 *
 *  Not an error to report: this dashboard ships against builds whose backend may
 *  predate the persona editor, and the honest response is to hide the controls
 *  rather than offer a button that fails. Two statuses mean it, because Flask
 *  answers each differently: 404 when nothing is routed at that path, and 405
 *  when the path exists for other verbs (DELETE /persona/<id> has shipped for
 *  longer) but not for PATCH. A 400 is the backend refusing THIS edit in words
 *  written for the owner, and is shown. */
export function notInThisBuild(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 405);
}

/**
 * Rename an app, add lists to it, or relabel the lists it keeps.
 *
 * Owner personas only: the backend refuses anything outside the owner's own apps
 * directory, the same way DELETE /persona/<id> does. Nothing is ever dropped, so
 * the worst an accepted edit can do is add a label.
 */
export function patchPersona(id: string, patch: PersonaPatch): Promise<PersonaPatchReply> {
  return apiPatch<PersonaPatchReply>(`/persona/${encodeURIComponent(id)}`, patch);
}

/**
 * Delete one persona the owner made, its pack and its store.
 *
 * The id is sent twice on purpose: once in the path as the target, once in the
 * body as the confirmation the owner typed. The backend refuses when they do
 * not match, and refuses again for anything that is not in the owner's own
 * apps directory — a shipped app is removed by changing the build.
 */
export function deletePersona(id: string, confirm: string): Promise<{ removed: boolean }> {
  return apiDelete<{ removed: boolean }>(`/persona/${encodeURIComponent(id)}`, {
    confirm,
  });
}
