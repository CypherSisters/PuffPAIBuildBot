// What this build IS — the one call the workbench landing view makes.
//
// /system/status reports runtime health and knows nothing about the manifest;
// this endpoint answers the other question: which zones are set up, what brain
// this build runs, what crew the hardware affords, which surfaces were loaded.
// Read-only, behind the same app API key as the /setup/* routes (the proxy
// injects it server-side, so no key ships to the browser).
import { API_URL } from "../api";
import { readBuildStatus, readJobReply, THING_ID, type BuildStatus, type Job, type SetupValue } from "../buildJobs";

export interface BuildIdentity {
  name: string;
  identity: string;
  based_on: string;
  /** What the owner said this bot is for, first sentence. "" when unset. */
  purpose?: string;
  /** The chat's first line, in this bot's own name. Older backends omit it. */
  greeting?: string;
}

export interface BuildCapabilities {
  configured: boolean;
  trimmed: boolean;
  available: string[];
  unavailable: Record<string, string>;
  can_plan: boolean;
  can_reach_wan: boolean;
  summary: string[];
}

export interface SurfaceEntry {
  label: string;
  sensitive: boolean;
}

export interface BuildSurfaces {
  /** True only when this build actually has an allowlist. An EMPTY allowlist
   *  means "never trimmed", not "nothing allowed" — the backend derives this
   *  from self_knowledge and nothing here second-guesses it. */
  trimmed: boolean;
  allowlist: string[];
  reasons: Record<string, string>;
  derived_from_goal: boolean;
  owner_edited: boolean;
  taxonomy: Record<string, SurfaceEntry>;
}

export interface VaultZone {
  configured: boolean;
  initialized: boolean;
  sealed: boolean;
  method: string;
  recovery_issued: boolean;
  recovery_confirmed: boolean;
  recovery_escrow: string;
  summary: string[];
  /** Open right now (unlocked with the PIN). Absent on older backends. */
  unlocked?: boolean;
  /** Why the vault cannot be reached (its drive is not plugged in), or "". */
  missing?: string;
}

export interface BrainZone {
  configured: boolean;
  model: string | null;
  recommended: string | null;
  chosen_by: string | null;
  routing_tier: string | null;
  limits: string;
  can_plan: boolean;
  backend_status: string;
}

export interface BridgeZone {
  configured: boolean;
  posture: string;
  label: string;
  summary: string;
  costs: string;
  note: string;
  chosen_by: string;
  enforced_by: string;
  run_egress: string;
  run_egress_source: string;
  open_doors: string[];
  all_doors: string[];
}

export interface BuildCrew {
  configured: boolean;
  master_core: number | null;
  max_specialists: number;
  selected_specialists: string[];
  basis: string;
  note: string;
}

export interface BuildDevice {
  source: "manifest" | "live";
  hardware_class: string;
  total_ram_gb: number | null;
  ram_source: string;
  model_budget_gb: number | null;
  cpu_model: string | null;
  logical_cores: number | null;
  accelerator_kind: string;
  accelerator_name: string | null;
  vram_gb: number | null;
  summary: string[];
}

export interface BuildKnowledge {
  zim_dir: string;
  auto_index: boolean;
  declared_packs: string[];
  indexed_libraries: number;
  indexed_chunks: number;
  indexed_known: boolean;
  pending_packs: number;
  pending_known: boolean;
  indexing_now: string[];
}

/** One installed app, as `apps/<id>/app.json` declares it.
 *
 *  `name` and `blurb` are the app author's words, not ours. They are data:
 *  rendered as text, escaped by React like any other string. Never markup,
 *  never `dangerouslySetInnerHTML`, never concatenated into a prompt.
 *
 *  `icon` is an icon NAME, not an SVG path. It is looked up in the dashboard's
 *  own icon set; an unrecognised name falls back, so a manifest cannot put
 *  arbitrary path data on the screen. */
export interface InstalledApp {
  id: string;
  name: string;
  blurb: string;
  /** In-app route, or "" when the app declares none. */
  route: string;
  icon: string;
  surfaces: string[];
  order: number;
}

export interface BuildSelf {
  configured: boolean;
  schema: string;
  generated_at: number | null;
  build: BuildIdentity;
  capabilities: BuildCapabilities;
  surfaces: BuildSurfaces;
  zones: { vault: VaultZone; brain: BrainZone; bridge: BridgeZone };
  crew: BuildCrew;
  device: BuildDevice;
  knowledge: BuildKnowledge;
  /** The apps installed on this box, in the order they are consulted.
   *
   *  An empty array is a real answer: a build carrying no apps. Anything that
   *  is NOT an array — absent, null, an older backend — means "not known", and
   *  every reader shows the full built-in menu instead. See `installedApps`. */
  apps?: InstalledApp[] | null;
  /** True when this bot's weather forecast is switched on right now. The
   *  weather card is on the home page of any such bot, page list or not. */
  weather_on?: boolean;
}

/** Fetch this build's own description. Resolves to null when the backend is
 *  unreachable, so callers say "I cannot reach the backend" instead of drawing
 *  zeros that look like real data. */
export async function getBuildSelf(): Promise<BuildSelf | null> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  try {
    const res = await fetch(`${API_URL}/build/self`, {
      method: "GET",
      headers,
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as BuildSelf;
  } catch {
    return null;
  }
}

/** Which apps this build has, or null when that is not known.
 *
 *  Null is the fail-open answer and it has to stay one value, not a scatter of
 *  `?.` checks: backend unreachable, fetch not landed, an older backend that
 *  does not send the field, or a scan that could not be done all mean the same
 *  thing — show the built-in menu. Only a real array trims anything. */
export function installedApps(build: BuildSelf | null): InstalledApp[] | null {
  return Array.isArray(build?.apps) ? build.apps : null;
}

/** True when ANY of these surfaces is loaded — for a widget that several
 *  surfaces could justify (a reminders tile serves timers or a calendar). */
export function anySurfaceLoaded(
  build: BuildSelf | null,
  surfaces: string[],
): boolean {
  if (!build) return true;
  if (!build.surfaces.trimmed) return true;
  return surfaces.some((s) => build.surfaces.allowlist.includes(s));
}

// ── "the build changed" ──────────────────────────────────────────────────────
//
// /build/self is fetched once per mount (see hooks/useBuildSelf.ts) because a
// manifest changes when the owner runs the wizard, not every thirty seconds.
// Installing a persona from the dashboard breaks that assumption: the backend
// rescans, the app IS installed, and the sidebar rail — mounted in the layout
// above every page — is still holding the answer from before. Reloading the
// document would fix it and lose the page's state with it.
//
// So the one writer says so, and every reader refetches. Deliberately tiny: no
// store, no cache, no shared copy of the build that could go stale in its own
// right. Each subscriber does its own fetch, which is the same call it made on
// mount.

type BuildChangeListener = () => void;

const buildListeners = new Set<BuildChangeListener>();

/** Subscribe to "something installed or removed an app". Returns the
 *  unsubscribe, for an effect's cleanup. */
export function onBuildChanged(listener: BuildChangeListener): () => void {
  buildListeners.add(listener);
  return () => {
    buildListeners.delete(listener);
  };
}

/** Called after a write that changes what this build carries — scaffolding a
 *  persona, or deleting one. A listener that throws must not stop the others
 *  from hearing about it. */
export function notifyBuildChanged(): void {
  for (const listener of Array.from(buildListeners)) {
    try {
      listener();
    } catch {
      /* one bad subscriber is not the others' problem */
    }
  }
}

// ── Proof it works ───────────────────────────────────────────────────────────
//
// What the build's own checks found, and what the running bot answered when it
// was asked. Read-only; the record is written by the wizard (the static half)
// and by `puffpai prove` (the live half). A build that has never been proved
// answers `ran: false` with a message in those words — "not proven yet" is a
// thing the home page has to be able to say, rather than implying a pass.

export interface ProveCheck {
  id: string;
  title: string;
  /** "pass" | "fail" | "gap" | "not_applicable" */
  result: string;
  reason: string;
}

export interface ProveGap {
  what: string;
  why: string;
  impact: string;
  /** Rendered by the backend from a registered command id — never free text
   *  supplied by a package. */
  fix_command: string;
}

export interface ProveLive {
  ran: boolean;
  at: string | null;
  note: string;
  /** "owner_confirmed" | "owner_refuted" | "owner_unproven" */
  owner: string;
  dashboard_url: string;
  checks: ProveCheck[];
  transcript: { question: string; reply: string; verdict: string }[];
}

export interface ProveRecord {
  ran: boolean;
  verdict: string;
  static_at?: string | null;
  bot_name?: string;
  checks: ProveCheck[];
  gaps: ProveGap[];
  live: ProveLive | null;
  message?: string;
  /** What the build's own checks found, in the end screen's words ("Of 5
   *  checks: 5 passed."). Absent when nothing ran, and on older backends. */
  summary?: string;
  /** summary, then how to prove the rest: the end screen's whole line. */
  line?: string;
}

/** The last proof, or null when the backend did not answer. Null is not a
 *  pass: callers render nothing rather than claiming anything. */
export async function getBuildProve(): Promise<ProveRecord | null> {
  try {
    const res = await fetch(`${API_URL}/build/prove`, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as ProveRecord;
  } catch {
    return null;
  }
}

// ── The bot's jobs (src/puffpai/build/) ─────────────────────────────────────
//
//   GET  /build/status           every job, the ONE setup list, her Yes, what
//                                was not used, the team's lines and the words
//   GET  /build/job/<id>         one job
//   GET  /build/apps             the apps Toolsmith runs, and whether each answers now
//   POST /build/yes {sha}        her one Yes for the whole setup list she saw
//   POST /build/knowledge        {id, ticked} one knowledge line on the Yes
//   POST /build/check/<job_id>   check this job again
//   POST /build/project          a code project from the Add page (or a folder of her code, from
//                                the Developer page): its job, waiting for her Yes
//   POST /build/setup/<row_id>   do one setup row: key {value} (into the vault),
//                                setting {value}, switch {}, address {}
//
// The reading lives in ../buildJobs.ts (pure, so the proof script runs it).


export interface BuildReply<T> {
  ok: boolean;
  status: number;
  data: T;
  /** The backend's own sentence, when it sent one. */
  message: string;
}

async function buildCall(path: string, method = "GET", body?: unknown): Promise<BuildReply<unknown>> {
  try {
    const res = await fetch(`${API_URL}${path}`, {
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
    const said = data && typeof data === "object" ? (data as Record<string, unknown>) : {};
    const message = typeof said.message === "string" ? said.message : typeof said.line === "string" ? said.line : "";
    return { ok: res.ok && said.ok !== false, status: res.status, data, message };
  } catch {
    return { ok: false, status: 0, data: {}, message: "" };
  }
}

/** Every job and the setup list. `data` is null when there is no answer. */
export async function getBuildStatus(): Promise<BuildReply<BuildStatus | null>> {
  const r = await buildCall("/build/status");
  return { ...r, data: r.ok ? readBuildStatus(r.data) : null };
}

export async function getBuildJob(id: string): Promise<BuildReply<Job | null>> {
  if (!THING_ID.test(id)) return { ok: false, status: 404, data: null, message: "" };
  const r = await buildCall(`/build/job/${id}`);
  return { ...r, data: r.ok ? readJobReply(r.data) : null };
}

/** Her one "Yes, build all of this", for exactly the list she was shown
 *  (`sha` from GET /build/status). Without it the backend says no. */
export function sayBuildYes(sha: string): Promise<BuildReply<unknown>> {
  if (!/^[0-9a-f]{64}$/.test(sha)) return Promise.resolve({ ok: false, status: 409, data: {}, message: "" });
  return buildCall("/build/yes", "POST", { sha });
}

/** Tick or untick one knowledge line on the Yes (POST /build/knowledge):
 *  the list changes, so it needs her Yes again. */
export function tickKnowledge(id: string, ticked: boolean): Promise<BuildReply<unknown>> {
  return buildCall("/build/knowledge", "POST", { id: id.slice(0, 200), ticked });
}

/** One app Toolsmith runs on this computer (GET /build/apps). */
export interface RunningApp {
  id: string;
  name: string;
  url: string;
  running: boolean;
}

/** The apps Toolsmith runs, each with whether its page answers now. */
export async function getRunningApps(): Promise<RunningApp[]> {
  const r = await buildCall("/build/apps");
  const apps = r.ok && r.data && typeof r.data === "object" ? (r.data as { apps?: unknown }).apps : [];
  return (Array.isArray(apps) ? apps : [])
    .filter((a): a is Record<string, unknown> => !!a && typeof a === "object")
    .map((a) => ({
      id: String(a.id ?? ""),
      name: String(a.name ?? ""),
      url: String(a.url ?? ""),
      running: a.running === true,
    }))
    .filter((a) => /^http:\/\/(127\.0\.0\.1|localhost):\d+/.test(a.url));
}

/** Run this job again. Plain: Try next app (the apps that gave up stay
 *  skipped). *fromTop*: Check again, the same job from the top (her project
 *  first, then its pick and alternates, ones tried before too). */
export function checkJobAgain(id: string, fromTop = false): Promise<BuildReply<unknown>> {
  if (!THING_ID.test(id)) return Promise.resolve({ ok: false, status: 404, data: {}, message: "" });
  return buildCall(`/build/check/${id}`, "POST", fromTop ? { again: true } : {});
}

/** Find more for one job from a pasted GitHub project address: it becomes
 *  the job's first app and the changed list waits for her one Yes. */
export function addProjectLink(id: string, url: string, order: HerOrder = "first"): Promise<BuildReply<unknown>> {
  if (!THING_ID.test(id)) return Promise.resolve({ ok: false, status: 404, data: {}, message: "" });
  return buildCall(`/build/more/${id}`, "POST", { url: url.slice(0, 300), order });
}

/** Where a project she adds to a job goes: "first" (Run it first: a try
 *  running for that job ends and hers runs next) or "list" (Add to the
 *  list: after the job's apps not yet tried; nothing running stops). */
export type HerOrder = "first" | "list";

/** A code project from the Add page (POST /build/project). */
export interface ProjectAsk {
  /** A GitHub project address; "" when `path` is given. */
  url: string;
  /** A folder of her own code on this computer (the Developer page): run
   *  where it is. */
  path?: string;
  name: string;
  what: string;
  /** The job she was finding something for, when she came from one. */
  cap?: string;
}

/** Where a project would go: the job it clearly fits, or a new job's name. */
export async function whereProjectGoes(
  p: ProjectAsk,
): Promise<BuildReply<{ fits: { id: string; name: string } | null; new_name: string }>> {
  const r = await buildCall("/build/project", "POST", { ...p, url: p.url.slice(0, 300), ...(p.path ? { path: p.path.slice(0, 1000) } : {}), ask: true });
  const d = (r.data || {}) as { fits?: { id?: unknown; name?: unknown } | null; new_name?: unknown };
  const fits = d.fits && typeof d.fits.id === "string" ? { id: d.fits.id, name: String(d.fits.name ?? "") } : null;
  return { ...r, data: { fits, new_name: String(d.new_name ?? "") } };
}

/** Add it: to that job (`job` is its id) or "new" for one new job named
 *  after it. Either way it waits for her one Yes on the job page. */
export async function addProject(
  p: ProjectAsk,
  job: string,
  order: HerOrder = "first",
): Promise<BuildReply<{ line: string; page: string }>> {
  if (job !== "new" && !THING_ID.test(job)) {
    return { ok: false, status: 404, data: { line: "", page: "" }, message: "" };
  }
  const r = await buildCall("/build/project", "POST", { ...p, url: p.url.slice(0, 300), ...(p.path ? { path: p.path.slice(0, 1000) } : {}), job, order });
  const d = (r.data || {}) as { line?: unknown; page?: unknown };
  const page = typeof d.page === "string" && /^\/dashboard\/jobs\/[a-z0-9][a-z0-9_-]{0,100}$/.test(d.page) ? d.page : "";
  return { ...r, data: { line: String(d.line ?? ""), page } };
}

/** One setup row: a key or a setting sends { value }, the town with its
 *  rough position sends { town, latitude, longitude }; a switch or an
 *  address sends nothing. */
export function doSetupRow(id: string, value?: SetupValue): Promise<BuildReply<unknown>> {
  if (!THING_ID.test(id)) return Promise.resolve({ ok: false, status: 404, data: {}, message: "" });
  const body = value === undefined ? {} : typeof value === "string" ? { value } : { ...value };
  return buildCall(`/build/setup/${id}`, "POST", body);
}
