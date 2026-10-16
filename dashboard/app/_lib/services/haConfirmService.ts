/**
 * Human-confirmation tickets for safety-critical Home Assistant actions.
 *
 * The backend refuses any request that would actuate the always-confirm class
 * (camera, cover, lock, alarm_control_panel, siren, valve, water_heater) with a
 * **409 + proposal** unless the body carries a single-use, action-bound,
 * 90-second ticket minted by `POST /ha/confirm`. Without this module the
 * dashboard has no way to obtain one, so those actions are impossible from the
 * UI rather than merely gated.
 *
 * Three rules this file exists to enforce:
 *
 * 1. **The ticket kind comes from the endpoint, never from anything a user or
 *    an agent typed.** `REQUEST_SHAPES` below is the single table that maps a
 *    request kind to both its endpoint and its ticket binding, so the two
 *    cannot drift apart. A caller picks a kind by writing a literal in source.
 * 2. **A 409 is a decision point, not an error.** `attemptHaRequest` classifies
 *    it as its own outcome so no caller can mistake it for a failure and retry.
 *    An auto-retry on a 409 would burn tickets one per loop.
 * 3. **A ticket is body-only, one use, then dropped.** Nothing here caches,
 *    persists, logs, or URL-encodes one, and no error string ever interpolates
 *    a ticket value.
 *
 * puff puff pass
 */

import { apiPostRaw } from "../api";

/* ── Ticket kinds ──────────────────────────────────────────────── */

export type HaConfirmKind =
  | "control"
  | "service"
  | "scene"
  | "automation"
  | "automation_run";

/** What `POST /ha/confirm` binds a ticket to. Derived, never user-supplied. */
export interface HaTicketBinding {
  kind: HaConfirmKind;
  /** For `automation` this is the config *alias*, not an entity id. */
  entity_id: string | null;
  action: string;
}

/* ── Proposals ─────────────────────────────────────────────────── */

/**
 * The proposal echoed by a **409 refusal**. Shape differs from the mint's:
 * it names the service rather than a bound action, and its `domains` are the
 * ones the bridge actually resolved (including a scene's members and an
 * automation's stored actions, which the mint endpoint cannot see).
 */
export interface HaRefusalProposal {
  service?: string;
  entity_id?: string | null;
  domains?: string[];
  [key: string]: unknown;
}

/**
 * The proposal echoed by a **mint**. This is the server stating what it will
 * authorise, and it is the value the dialog must render — showing the app's own
 * copy of the request back to the owner would prove nothing.
 */
export interface HaTicketProposal {
  kind: HaConfirmKind;
  entity_id: string | null;
  action: string;
  domains: string[];
  safety_critical: boolean | null;
  safety_critical_domains: string[];
  note?: string;
}

export interface HaMintResult {
  /** Opaque, single-use, 90s. Held in a ref and dropped — never stored. */
  ticket: string;
  ticket_id: string;
  expires_in: number;
  single_use: boolean;
  proposal: HaTicketProposal;
  message: string;
}

/* ── Requests ──────────────────────────────────────────────────── */

/**
 * An intended Home Assistant request, in the form a caller writes it.
 *
 * The `kind` discriminant is a source literal, and `REQUEST_SHAPES` turns it
 * into an endpoint *and* a ticket binding together. That is what keeps
 * "which endpoint am I calling" and "which kind am I minting" from diverging.
 */
export type HaRequest =
  | {
      kind: "control";
      entityId: string;
      action: "turn_on" | "turn_off" | "toggle";
      data?: Record<string, unknown>;
    }
  | {
      kind: "service";
      domain: string;
      service: string;
      /** Bound into the ticket by the backend, so it must match the body. */
      entityId?: string;
      data?: Record<string, unknown>;
    }
  | { kind: "scene"; entityId: string }
  | { kind: "automation"; alias: string; config: Record<string, unknown> }
  | {
      kind: "automation_run";
      entityId: string;
      action: "trigger" | "enable" | "disable";
    };

interface HaRequestShape {
  endpoint: string;
  binding: HaTicketBinding;
  /** Request body. `ticket` is spread in only at redeem time. */
  body: Record<string, unknown>;
}

/**
 * Endpoint + ticket binding for a request, resolved together from one table.
 *
 * `automation_run` splits across two endpoints (`/trigger` and `/toggle`) but
 * shares one ticket kind, and a ticket bound to `enable` does not redeem for
 * `disable` — the action string carries that distinction.
 */
export function describeRequest(req: HaRequest): HaRequestShape {
  switch (req.kind) {
    case "control":
      return {
        endpoint: "/ha/control",
        binding: { kind: "control", entity_id: req.entityId, action: req.action },
        body: { entity_id: req.entityId, action: req.action, ...(req.data ?? {}) },
      };
    case "service":
      return {
        endpoint: `/ha/service/${req.domain}/${req.service}`,
        binding: {
          kind: "service",
          entity_id: req.entityId ?? null,
          action: `${req.domain}.${req.service}`,
        },
        body: {
          ...(req.entityId ? { entity_id: req.entityId } : {}),
          ...(req.data ?? {}),
        },
      };
    case "scene":
      // Took no body before tickets existed; it takes `{ticket}` now.
      return {
        endpoint: `/ha/scenes/${encodeURIComponent(req.entityId)}/activate`,
        binding: { kind: "scene", entity_id: req.entityId, action: "activate" },
        body: {},
      };
    case "automation":
      return {
        endpoint: "/ha/automations",
        binding: { kind: "automation", entity_id: req.alias, action: "create" },
        body: { ...req.config },
      };
    case "automation_run": {
      const path =
        req.action === "trigger"
          ? `/ha/automations/${encodeURIComponent(req.entityId)}/trigger`
          : `/ha/automations/${encodeURIComponent(req.entityId)}/toggle`;
      return {
        endpoint: path,
        binding: {
          kind: "automation_run",
          entity_id: req.entityId,
          action: req.action,
        },
        body: req.action === "trigger" ? {} : { enable: req.action === "enable" },
      };
    }
  }
}

/* ── Outcomes ──────────────────────────────────────────────────── */

export type HaAttempt =
  | { kind: "ok"; data: unknown; message: string }
  /** 409 — a decision point. Not an error; do not toast it red, do not retry. */
  | {
      kind: "needs_confirmation";
      message: string;
      proposal: HaRefusalProposal;
      confirmEndpoint: string;
    }
  /** 403 — the Drawbridge consent gate. A different flow entirely. */
  | { kind: "needs_consent"; message: string }
  | { kind: "failed"; message: string; status: number };

/**
 * Run a request once, optionally redeeming a ticket.
 *
 * `ticket` goes into the body and nowhere else — not the URL, not a header, not
 * a stored copy. On return the caller's reference is the only one left, and the
 * caller is expected to drop it: the backend has consumed it either way.
 */
export async function attemptHaRequest(
  req: HaRequest,
  ticket?: string,
): Promise<HaAttempt> {
  const { endpoint, body } = describeRequest(req);
  const payload = ticket ? { ...body, ticket } : body;

  let status = 0;
  let ok = false;
  let data: Record<string, unknown> | null = null;
  try {
    const res = await apiPostRaw<Record<string, unknown>>(endpoint, payload);
    status = res.status;
    ok = res.ok;
    data = res.data;
  } catch {
    return { kind: "failed", message: "Could not reach PuffPAI.", status: 0 };
  }

  const message =
    (typeof data?.message === "string" && data.message) ||
    (typeof data?.error === "string" && data.error) ||
    "";

  if (status === 409 && data?.needs_confirmation) {
    return {
      kind: "needs_confirmation",
      message: message || "This action needs a human confirmation.",
      proposal: (data.proposal as HaRefusalProposal) ?? {},
      confirmEndpoint:
        typeof data.confirm_endpoint === "string" ? data.confirm_endpoint : "/ha/confirm",
    };
  }
  if (status === 403) {
    return {
      kind: "needs_consent",
      message: message || "This device has not been approved yet.",
    };
  }
  if (!ok || data?.success === false) {
    return { kind: "failed", message: message || `Request failed (${status}).`, status };
  }
  return { kind: "ok", data, message };
}

/**
 * Mint a ticket for a binding. **Does not actuate** — the endpoint makes no
 * call to Home Assistant at all, so opening the dialog cannot touch the house.
 *
 * NOT IN THIS BUILD, and the gate is LEFT UP ANYWAY.
 *
 * POST /ha/confirm is a HomeBot-era route; this build's backend does not carry
 * it, so minting fails and the confirmation dialog cannot be satisfied. The
 * tempting fix — drop the ticket step and call /ha/control directly — is the
 * wrong one: it would silently widen what a browser on the LAN can do to
 * somebody's house, on the strength of a missing route. So the gate stays and
 * this fails CLOSED. Nothing actuates.
 *
 * The message below says which piece is absent, because "request failed (404)"
 * on a dialog about turning off a bedroom light reads like the light is broken.
 * Wiring the gate up properly is a backend job and a security review, not a
 * dashboard edit.
 */
export async function mintHaTicket(
  binding: HaTicketBinding,
): Promise<{ ok: true; result: HaMintResult } | { ok: false; message: string }> {
  try {
    const { status, ok, data } = await apiPostRaw<Record<string, unknown>>("/ha/confirm", {
      kind: binding.kind,
      entity_id: binding.entity_id,
      action: binding.action,
    });
    if (!ok || typeof data?.ticket !== "string") {
      const message =
        (typeof data?.error === "string" && data.error) ||
        (typeof data?.message === "string" && data.message) ||
        (status === 404
          ? "This build has no confirmation gate, so nothing was sent to the house. Nothing changed."
          : `Could not get a confirmation (${status}).`);
      return { ok: false, message };
    }
    return { ok: true, result: data as unknown as HaMintResult };
  } catch {
    return { ok: false, message: "Could not reach PuffPAI." };
  }
}

/* ── Plain language ────────────────────────────────────────────── */

const DOMAIN_NOUN: Record<string, string> = {
  camera: "camera",
  cover: "cover",
  lock: "lock",
  alarm_control_panel: "alarm panel",
  siren: "siren",
  valve: "valve",
  water_heater: "water heater",
  light: "light",
  switch: "switch",
  climate: "thermostat",
  media_player: "media player",
  fan: "fan",
  automation: "automation",
  scene: "scene",
  script: "script",
};

/** `camera.laundry_room_door` -> `{ domain: "camera", name: "Laundry Room Door" }` */
export function splitEntityId(entityId: string | null | undefined): {
  domain: string | null;
  name: string;
} {
  const raw = String(entityId ?? "").trim();
  if (!raw) return { domain: null, name: "" };
  const dot = raw.indexOf(".");
  if (dot < 0) return { domain: null, name: raw };
  const domain = raw.slice(0, dot);
  const slug = raw.slice(dot + 1);
  const name = slug
    .split(/[_-]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
  return { domain, name: name || slug };
}

/**
 * "the Laundry Room Door camera", built from the **server's** entity id.
 *
 * An `automation` ticket is bound to a config alias rather than an entity id,
 * so it is shown verbatim — humanising it would invent structure that is not
 * there.
 */
export function targetPhrase(
  entityId: string | null | undefined,
  kind?: HaConfirmKind,
): string {
  if (!entityId) return "this target";
  if (kind === "automation") return `the automation “${entityId}”`;
  const { domain, name } = splitEntityId(entityId);
  const noun = domain ? DOMAIN_NOUN[domain] : null;
  if (!name) return "this target";
  return noun ? `the ${name} ${noun}` : `${name}`;
}

const VERB_PHRASE: Record<string, string> = {
  turn_off: "Turn OFF",
  turn_on: "Turn ON",
  toggle: "Toggle",
  activate: "Activate",
  create: "Create",
  trigger: "Run",
  enable: "Enable",
  disable: "Disable",
  open: "Open",
  close: "Close",
  lock: "Lock",
  unlock: "Unlock",
};

/**
 * The headline sentence, assembled from the server's echoed `action` and
 * `entity_id`. For a `service` ticket the action is a full `domain.service`, so
 * the verb comes from the service half and the qualified name is still shown in
 * the detail rows below the headline.
 */
export function actionSentence(proposal: {
  kind?: HaConfirmKind;
  entity_id?: string | null;
  action?: string;
}): string {
  const kind = proposal.kind;
  const action = String(proposal.action ?? "").trim().toLowerCase();
  const target = targetPhrase(proposal.entity_id, kind);
  const verbKey = action.includes(".") ? action.split(".")[1] : action;
  const verb = VERB_PHRASE[verbKey];

  if (kind === "automation") return `Create ${target}.`;
  if (!verb) return `Run ${action || "this action"} on ${target}.`;
  return `${verb} ${target}.`;
}

/**
 * What is at stake, keyed on the domain the *server* named.
 *
 * Each entry answers "what changes in the physical world", not "are you sure".
 * The whole point of the dialog is that the owner can catch a mismatch between
 * what they meant and what the server is about to authorise, and a generic
 * confirm prompt gives them nothing to catch it with.
 */
function stakeForDomain(domain: string, action: string): string | null {
  const verb = action.includes(".") ? action.split(".")[1] : action;
  const off = verb === "turn_off" || verb === "disable" || verb === "close";
  const on = verb === "turn_on" || verb === "enable" || verb === "open";

  switch (domain) {
    case "camera":
      if (off) return "It stops recording, and stays stopped until you turn it back on.";
      if (on) return "It starts recording again.";
      return "This changes whether it is recording.";
    case "cover":
      return "This moves a physical barrier: a door, gate, blind or shutter. Make sure nothing and nobody is in its path.";
    case "lock":
      if (verb === "unlock" || off)
        return "This unlocks a door. Anyone on the other side can walk in until it is locked again.";
      if (verb === "lock" || on)
        return "This locks a door. Anyone without a key stays out, including you.";
      return "This changes whether a door is locked.";
    case "alarm_control_panel":
      return "This changes whether the alarm system is armed.";
    case "valve":
      return "This opens or closes a valve carrying water or gas.";
    case "water_heater":
      return "This changes the water heater. Water can be made hot enough to scald.";
    case "siren":
      return "This changes an audible alarm output.";
    default:
      return null;
  }
}

/**
 * One stake line per safety-critical domain in the proposal, in the order the
 * server listed them. Empty when nothing safety-critical was named — in which
 * case the dialog says so plainly instead of inventing a consequence.
 */
export function stakeLines(domains: string[] | undefined, action: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const d of domains ?? []) {
    if (seen.has(d)) continue;
    seen.add(d);
    const line = stakeForDomain(d, String(action ?? "").toLowerCase());
    if (line) out.push(line);
  }
  return out;
}

export { DOMAIN_NOUN };
