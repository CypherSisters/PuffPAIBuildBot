// The wizard as cards: the browser half.
//
// The CLI and this dashboard are two renderers of ONE card set. No question is
// written here: a question lives in the skill that proposed it, and these types
// mirror `wizard_card/v1` (src/puffpai/wizard/cards.py) field for field so a
// renderer that has drifted from the card fails at the type level rather than
// in somebody's build.
//
// Three routes, same-origin through /api/pai, which injects this box's API key
// server-side; nothing key-shaped ships to the browser. `wizard` is on the
// proxy allowlist.
//
// SECRETS. A card never carries a value for a `secret_entry` field (the backend
// refuses one that does), and an answer to one is redacted before it is logged
// or returned. Nothing in this file stores an answer.

const BASE = "/api/pai";

export const FIELD_KINDS = [
  "choice",
  "multi_choice",
  "text",
  "toggle",
  "number",
  "secret_entry",
  "info",
] as const;
export type FieldKind = (typeof FIELD_KINDS)[number];

/** Every status a row on the review screen may be in (cards.py STATUSES). */
export type CardStatus = "ready" | "needs_you" | "gap" | "blocked" | "not_built";

/** How honestly a cost is known. `unknown` is never summed into a total. */
export type EstimateBasis = "measured" | "calibrated" | "unknown";

/** The four things that make a row consequential. */
export type ConsequentialReason =
  | "heavy_bytes"
  | "destructive_rerun"
  | "runtime_egress"
  | "vault_pin_unset";

export interface CardOption {
  value: string;
  label: string;
}

/** One field of a card. Which optional keys are present depends on `kind`:
 *  choice/multi_choice carry options, free_text and pattern; text carries
 *  max_length and pattern; number carries min/max; secret_entry carries
 *  max_length and empty strings for current/proposed; info carries text only. */
export interface CardField {
  id: string;
  kind: FieldKind;
  label: string;
  help: string;
  current?: string | string[] | boolean | number | null;
  proposed?: string | string[] | boolean | number | null;
  options?: CardOption[];
  pattern?: string;
  max_length?: number;
  min?: number;
  max?: number;
  free_text?: boolean;
  /** Sample answers for a text field (the goal's three sample goals); a tap
   *  puts one in the box. */
  examples?: string[];
}

export interface CardEstimate {
  bytes: number | null;
  seconds: number | null;
  basis: EstimateBasis;
}

/** A gap record. `fix_command` is rendered by the backend from a
 *  registered command id — a command string is NEVER assembled in the browser. */
export interface CardGap {
  skill: string;
  what: string;
  why: string;
  impact: string;
  fix: Record<string, unknown>;
  fix_command: string;
  blocking: boolean;
  recorded_at: string;
}

export interface WizardCard {
  schema: string;
  skill: string;
  section: string;
  /** Which question of a skill that asks more than one (Secure: posture,
   *  vault, bridge). Empty for a skill with a single card. */
  part: string;
  title: string;
  decided: string;
  status: CardStatus;
  consequential: boolean;
  consequential_reasons: ConsequentialReason[];
  estimate: CardEstimate;
  fields: CardField[];
  gaps: CardGap[];
}

/** A skill with no card at all. The backend names it and gives it the owner's
 *  words; this file does not keep its own list of the twelve. */
export interface PlainSkill {
  skill: string;
  title: string;
}

/** One row of what POST /wizard/run did. `applied` false with a `why` is the
 *  server refusing to accept something on the owner's behalf. */
export interface RunStep {
  card: string;
  outcome: string;
  applied: boolean;
  why: string;
  verified?: Record<string, unknown>;
}

export interface RunReply {
  success?: boolean;
  message?: string;
  results?: RunStep[];
  refusals?: string[];
  gaps?: CardGap[];
  stale?: string[];
}

export interface ApplyReply {
  success?: boolean;
  /** The backend's message on a refusal (400/404). Shown beside the field. */
  message?: string;
  /** Which field the refusal was about, decided by the backend against the
   *  card it proposed. `null` means the message is about the card as a whole. */
  field?: string | null;
  skill?: string;
  outcome?: string;
  card?: WizardCard;
  changed?: Record<string, unknown>;
  stale?: string[];
  verified?: Record<string, unknown>;
  gaps?: CardGap[];
}

/** One card a stopped run already settled, as the record remembers it. */
export interface ResumeDecided {
  entry?: string;
  card?: string;
  title?: string;
}

/** A setup that stopped part-way and is still worth continuing.
 *
 *  `offer: false` carries `why` — `""` for "there is no record at all", or the
 *  backend's word for a record it set aside (`unreadable`, `finished`,
 *  `too_old`, `nothing_done`). Read-only: nothing is verified and nothing is
 *  written until the owner answers. */
export interface ResumeOffer {
  offer?: boolean;
  why?: string;
  started_at?: number;
  age_s?: number;
  decided?: ResumeDecided[];
  stopped_entry?: string;
  stopped_card?: string;
  stopped_title?: string;
}

export interface ResumeReply {
  success?: boolean;
  message?: string;
  choice?: string;
  resumed?: boolean;
  archived?: boolean;
  /** Blocking cards whose verify did not hold against this machine, so they
   *  will be asked again. Continuing is never "trust the file". */
  reasked?: string[];
  why?: string;
}

export interface StateReply {
  schema?: string;
  found?: boolean;
  started_at?: string | null;
  /** The spec's fixed order, carried explicitly: JSON key order is no promise. */
  order?: string[];
  skills?: Record<string, unknown>;
  entries?: Record<string, unknown>;
  log?: Record<string, unknown>[];
  gaps?: CardGap[];
  stale?: string[];
  next_entry?: string | null;
  /** Whether a stopped setup is there to continue. Rides on this route rather
   *  than one of its own: it is the same question the log answers. */
  resume?: ResumeOffer;
}

export interface Reply<T> {
  ok: boolean;
  status: number;
  data: T;
}

async function req<T>(path: string, method = "GET", body?: unknown): Promise<Reply<T>> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
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
    // Backend down before the proxy even answered. A 0 so callers can say so
    // rather than hang.
    return { ok: false, status: 0, data: {} as T };
  }
}

/** Apply one card's answers. An empty `answers` means "accept the proposal".
 *  `card` is the id from `order` — `discover`, or `secure:vault`. */
export function applyWizardCard(
  card: string,
  answers: Record<string, unknown> = {},
): Promise<Reply<ApplyReply>> {
  return req<ApplyReply>("/wizard/apply", "POST", { skill: card, answers });
}

/** The run log, summarised: one row per skill, the gaps, what is stale — and
 *  whether a stopped setup is there to continue. */
export function getWizardState(): Promise<Reply<StateReply>> {
  return req<StateReply>("/wizard/state");
}

/** Continue a stopped setup, or set it aside and start over.
 *
 *  The SERVER decides what continuing may skip: every blocking card the record
 *  calls done is verified against the machine again, and the vault is checked
 *  on disk whatever the record says. What comes back in `reasked` is which
 *  cards did not hold and will be asked again. */
export function resumeWizard(choice: "continue" | "restart"): Promise<Reply<ResumeReply>> {
  return req<ResumeReply>("/wizard/resume", "POST", { choice });
}

// ── The conversation ──────────────────────────────────────────────────────────
//
// The same questions the terminal asks, in the same order, then ONE summary and
// one "Looks good". Every word below comes from the backend (wizard/wording.py
// and wizard/proposal.py); the browser draws it and keeps no list of its own.

/** Where the conversation is. The backend decides; the page follows. */
export type FlowStep =
  | "first_look"
  | "goal"
  | "identity"
  | "find"
  | "needs"
  | "summary"
  | "hub"
  | "vault"
  | "build"
  | "building"
  | "done";

export interface FlowQuestion {
  /** `purpose:goal`, `purpose:identity` — the console asks these, in order. */
  id: string;
  card: WizardCard;
  applied: boolean;
}

export interface BuildStatus {
  running: boolean;
  result?: RunReply & { complete?: boolean; outstanding?: string[] } | null;
  error?: string;
  /** What the build is doing, line by line, as it happens. */
  lines?: string[];
  /** After the build: what waits for her answer. */
  waiting?: BuildWaiting[];
  /** "Go on without them for now". */
  go_on?: string;
  /** Toolsmith waits for the vault: the Building screen shows a PIN box. */
  vault_wait?: string;
}

export interface FlowReply {
  schema?: string;
  next?: FlowStep;
  /** `standard` or `simple` (a builder under 13). Only the wording changes. */
  wording?: string;
  name?: string;
  questions?: FlowQuestion[];
  approved?: boolean;
  /** The Home Assistant card, when the owner put the hub on the plan. */
  hub?: WizardCard | null;
  vault?: {
    ready: boolean;
    title: string;
    what: string;
    pin: string;
    pin_help: string;
    again: string;
    again_help: string;
    /** "Where should your vault live?" and its three answers.
     *  Older backends do not send it; the PIN is then asked straight away. */
    where?: {
      question: string;
      choices: { value: "here" | "folder" | "existing"; label: string; help: string }[];
      encrypted_drive_line: string;
      network_line: string;
      not_a_vault_line: string;
    };
  };
  /** Whether the browser's "Do you have a GitHub account?" was answered.
   *  Not a step: `next` is unchanged by it. */
  github?: { asked: boolean };
  /** "Who should do Toolsmith's installs?" was answered (after GitHub). */
  toolsmith?: { asked: boolean };
  /** "Now building <name>. Big downloads can take a while." */
  building_line?: string;
  building?: BuildStatus;
  handoff?: WizardCard | null;
  refusals?: string[];
}

/** One line of the summary (wizard/proposal.py ProposalItem, plus `on`). */
export interface SummaryItem {
  id: string;
  section: string;
  label: string;
  detail: string;
  size_bytes: number | null;
  outside_host: string | null;
  default_on: boolean;
  needs_yes: boolean;
  apply: { kind: string; ref: string };
  on: boolean;
  /** What the line says when it is off: "Not included (you can add it): ...
   *  - about 301 MB, free for personal use only". The terminal prints the
   *  same words (wizard/proposal.py off_line). */
  off_line?: string;
  /** Extras, display only (proposal.EXTRA_KEYS): the job this line
   *  is for, why it fits, whether it only might help, which other lines it
   *  needs (the locked box), and the facts shown about it. The browser sends
   *  ticks only; nothing here is sent back. */
  need?: string;
  why?: string;
  might_help?: boolean;
  requires?: string[];
  facts?: Record<string, unknown>;
}

export interface SummarySection {
  id: string;
  heading: string;
  items: SummaryItem[];
  empty: string;
  /** Statements that are not switches, said under the section. */
  notes?: string[];
}

/** One group of "What it will do": a job the owner kept, its lines, and its
 *  notes (wizard/proposal.py _job_groups). */
export interface SummaryJobGroup {
  id: string;
  heading: string;
  items: SummaryItem[];
  notes: string[];
  /** 'Did not get to look for "<job>" this time. ...', or "". */
  not_reached: string;
}

export interface SummaryChoice {
  value: string;
  label: string;
  size_bytes?: number | null;
}

export interface Summary {
  schema?: string;
  name: string;
  goal: string;
  mode: string;
  internet: boolean | null;
  title: string;
  intro: string;
  checked?: string[];
  /** "What it will do", when the needs step ran; "" otherwise. */
  jobs_heading?: string;
  jobs?: SummaryJobGroup[];
  /** "Similar projects also have (tick to add):" and its lines. */
  similar?: { heading: string; rows: SummarySimilarRow[] };
  sections: SummarySection[];
  reach: { current: string; label: string; choices: SummaryChoice[] };
  model_choices: SummaryChoice[];
  /** "Do not download a brain now", in the builder's wording. */
  brain_off_label?: string;
  total_bytes: number;
  hosts: string[];
  lines: { total?: string; leaves?: string; reach?: string };
  actions: SummaryChoice[];
  approved: boolean;
  /** The home town, asked once on the first switched-on line that needs it
   *  (the weather). `ask` false: no field (wizard/proposal.py town_question). */
  town?: SummaryTown;
  notes?: string[];
  success?: boolean;
  message?: string;
}

export interface SummaryTown {
  ask: boolean;
  item: string;
  /** Where the typed town is looked up (named on the "What leaves" line). */
  host?: string;
  question: string;
  help: string;
  skip: string;
  placeholder: string;
  value: string;
}

/** What "Change something" sends: items switched on or off, another brain,
 *  or a different answer to how the bot uses the internet. `town` is the home
 *  town typed on the weather line; empty is a skip. */
export interface SummaryChanges {
  on: Record<string, boolean>;
  model?: string;
  reach?: string;
  town?: string;
}

/** The first screen (wizard/first_look.py): what this computer has, and the
 *  thinking model that fits it. */
export interface FirstLookChoice {
  value: string;
  name: string;
  label: string;
  good_for: string;
  size_bytes: number | null;
  recommended: boolean;
}

export interface FirstLook {
  schema?: string;
  title: string;
  intro: string;
  found: { label: string; text: string }[];
  recommend: { value: string; line: string; why: string; good_for: string };
  none_line: string;
  others_heading: string;
  choices: FirstLookChoice[];
  too_big: string;
  current: string;
  chosen: boolean;
  actions: SummaryChoice[];
  success?: boolean;
  message?: string;
}

/** Draw the first screen. Checks this computer the first time, as the
 *  terminal does. */
export function getFirstLook(): Promise<Reply<FirstLook>> {
  return req<FirstLook>("/wizard/first-look", "POST", {});
}

/** "Use this one" (no model: the recommendation) or a pick under "Change". */
export function chooseFirstLook(
  model?: string,
): Promise<Reply<{ success?: boolean; message?: string; model?: string | null }>> {
  return req("/wizard/first-look", "POST", { choose: true, model: model ?? null });
}

/** The GitHub question answered, so it is not asked again. Records only
 *  that it was asked; connecting goes through /github/*. */
export function chooseGitHub(
  choice: "connected" | "skipped",
): Promise<Reply<{ success?: boolean; message?: string }>> {
  return req("/wizard/github", "POST", { choice });
}

/** "Who should do Toolsmith's installs?" answered. Records only that it was
 *  asked; the setting and the key go through /toolsmith/model. */
export function chooseToolsmith(
  choice: "local" | "cloud" | "skipped",
): Promise<Reply<{ success?: boolean; message?: string }>> {
  return req("/wizard/toolsmith", "POST", { choice });
}

export function getFlow(): Promise<Reply<FlowReply>> {
  return req<FlowReply>("/wizard/flow");
}

/** The plan with `changes` drawn in. Approves nothing. */
export function getSummary(changes?: SummaryChanges): Promise<Reply<Summary>> {
  return req<Summary>("/wizard/summary", "POST", { changes: changes ?? null });
}

/** "Looks good". The backend checks the changes against the plan first. */
export function approveSummary(
  changes?: SummaryChanges,
): Promise<Reply<{ success?: boolean; message?: string; approved?: boolean }>> {
  return req("/wizard/approve", "POST", { changes: changes ?? null });
}

export function skipVault(): Promise<Reply<{ success?: boolean }>> {
  return req("/wizard/vault/skip", "POST", {});
}

/** Start building the approved plan in the background. */
export function startBuild(): Promise<Reply<{ success?: boolean; message?: string }>> {
  return req("/wizard/build", "POST", {});
}

export function getBuild(): Promise<Reply<BuildStatus>> {
  return req<BuildStatus>("/wizard/build");
}

// ── "Finding out what a <kind> bot needs" ────────────────────────────────────
//
// GET /wizard/needs starts finding out (once per run) and says how far it has
// got; POST sends ticks, additions, allowed words, and "look" to start looking.
// Every word comes from the backend (wizard/wording.py); the page draws it.

export interface NeedsJob {
  id: string;
  name: string;
  does: string;
  source: string;
  ticked: boolean;
  /** "you asked", "most <kind> bots have this" or "you added". */
  chip: string;
}

export interface NeedsHeld {
  word: string;
  why: string;
  allowed: boolean;
}

export interface NeedsView {
  title: string;
  lines: string[];
  jobs: NeedsJob[];
  held: NeedsHeld[];
  private_left_out: string[];
  subject: { phrase: string; notes: string[] };
  sends: string[];
  helper: string;
  done: boolean;
  phase: "needs" | "parts";
  not_reached: string[];
  intro?: string;
  held_line?: string;
  add?: string;
  before_button?: string[];
  buttons?: SummaryChoice[];
  online?: boolean;
  /** "BuildBot's reading helper is not on this computer ... Get it now?",
   *  shown once when it is missing and could be downloaded; "" otherwise. */
  helper_offer?: string;
  /** "Get it" (value `get`) and "Not now" (value `not_now`). */
  helper_buttons?: SummaryChoice[];
  /** What happened after "Get it", in plain words. */
  helper_line?: string;
  success?: boolean;
  message?: string;
}

export interface NeedsChoice {
  ticked?: string[];
  /** Before looking: jobs in her own words. After looking: the name of a
   *  "Similar projects also have" line she ticked on the plan. */
  added?: string[];
  allowed_words?: string[];
  look?: "online" | "here";
  /** true: "Get it" (the reading helper); false: "Not now". */
  get_helper?: boolean;
}

/** One line of "Similar projects also have (tick to add):" on the plan. */
export interface SummarySimilarRow {
  name: string;
  seen_in: number;
  /** "<name>    seen in <N> similar projects". */
  line: string;
}

/** What the build left waiting for her (wizard/service.py build_waiting). */
export interface BuildWaiting {
  id: string;
  status: "held" | "waiting_box";
  name?: string;
  lines: string[];
  /** Held: "Add it anyway" / "Leave it out". */
  yes?: string;
  no?: string;
  /** The Linux feature line: how many things wait, and "Turn it on". */
  count?: number;
  button?: string;
}

/** A held item's answer, from this paired browser
 *  (POST /integrations/<id>/decide). */
export function decideIntegration(
  id: string,
  yes: boolean,
): Promise<Reply<{ ok?: boolean; status?: string; line?: string; lines?: string[]; message?: string }>> {
  return req(`/integrations/${encodeURIComponent(id)}/decide`, "POST", { yes });
}

/** Ask Windows to turn on its Linux feature (POST /box/install-wsl). A 404
 *  means this build cannot do it yet: the line stays, without the button. */
export function turnOnWsl(): Promise<Reply<{ started?: boolean; message?: string }>> {
  return req("/box/install-wsl", "POST", {});
}

export function getNeeds(): Promise<Reply<NeedsView>> {
  return req<NeedsView>("/wizard/needs");
}

export function chooseNeeds(choice: NeedsChoice): Promise<Reply<NeedsView>> {
  return req<NeedsView>("/wizard/needs", "POST", choice);
}
