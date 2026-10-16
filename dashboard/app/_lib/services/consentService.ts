/**
 * Drawbridge consent requests + the dashboard camera registry.
 *
 * Drawbridge is the security agent's name everywhere a person can read it; the
 * backend ids and routes it answers on are still `/sentinel/*`, and this file
 * uses those verbatim.
 *
 * Drawbridge does not dead-end a blocked privacy-sensitive action: it files a
 * consent request and waits for the owner to say yes or no. Until now nothing
 * in the dashboard read that queue, so requests sat invisible. These helpers
 * back the consent surface and the add/remove camera controls.
 *
 * NOTE: this is a DIFFERENT queue from `/devices/pending` (network-discovered
 * Home Assistant devices) used by the Home Assistant "Devices" tab.
 *
 * puff puff pass
 */

import { apiGet, apiPost, apiDelete, apiPostRaw } from "../api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ConsentRequest {
  entity_id: string;
  label: string;
  action: string;
  reason: string;
  status: string;
  created_at: string;
  requested_by: string;
  context?: Record<string, any>;
}

export interface CameraTile {
  entity_id: string;
  label: string;
  added_at?: string;
  added_by?: string;
}

/** Outcome of an add-camera attempt, already classified for the UI. */
export type AddCameraResult =
  | { kind: "added"; message: string; cameras: CameraTile[] }
  /** 403: Drawbridge filed a consent request — the designed flow, not a failure. */
  | { kind: "needs_permission"; message: string; cameras: CameraTile[] }
  /** Anything else the backend refused (unknown entity, already added, HA down). */
  | { kind: "rejected"; message: string; cameras: CameraTile[] };

// ---------------------------------------------------------------------------
// Consent requests
// ---------------------------------------------------------------------------

/** Open consent requests. Pass `all` to include already-resolved ones. */
export async function getConsentRequests(all = false): Promise<ConsentRequest[]> {
  const data = await apiGet<{ requests?: ConsentRequest[] }>(
    `/sentinel/consent/requests${all ? "?all=1" : ""}`
  );
  return Array.isArray(data?.requests) ? data.requests : [];
}

/**
 * Approve or deny an open request.
 * On approval of a `dashboard_add_camera` request the backend completes the
 * add itself and returns the human-readable outcome in `message`.
 */
export async function resolveConsentRequest(
  entityId: string,
  approve: boolean,
  note?: string
): Promise<{ ok: boolean; message: string | null; request?: ConsentRequest }> {
  return apiPost(
    `/sentinel/consent/requests/${encodeURIComponent(entityId)}`,
    note ? { approve, note } : { approve }
  );
}

// ---------------------------------------------------------------------------
// Entity domains — grouping for a large first-run queue
// ---------------------------------------------------------------------------

/** `light.kitchen` -> `light`. Anything unparseable groups under `other`. */
export function entityDomain(entityId: string): string {
  const dot = entityId.indexOf(".");
  return dot > 0 ? entityId.slice(0, dot) : "other";
}

/**
 * Human names for the domains a first-run consent pass actually produces.
 * Unknown domains fall back to the raw domain, de-underscored and capitalized,
 * rather than being hidden behind a vague "Other".
 */
const DOMAIN_NAMES: Record<string, [singular: string, plural: string]> = {
  light: ["light", "lights"],
  switch: ["switch", "switches"],
  media_player: ["media player", "media players"],
  climate: ["thermostat", "thermostats"],
  camera: ["camera", "cameras"],
  cover: ["cover", "covers"],
  fan: ["fan", "fans"],
  lock: ["lock", "locks"],
  vacuum: ["vacuum", "vacuums"],
  humidifier: ["humidifier", "humidifiers"],
  water_heater: ["water heater", "water heaters"],
  scene: ["scene", "scenes"],
  script: ["script", "scripts"],
  sensor: ["sensor", "sensors"],
  binary_sensor: ["sensor", "sensors"],
};

/** Lowercase noun for a domain, e.g. `domainNoun("light", 5)` -> "lights". */
export function domainNoun(domain: string, count: number): string {
  const known = DOMAIN_NAMES[domain];
  if (known) return count === 1 ? known[0] : known[1];
  const raw = domain.replace(/_/g, " ");
  return count === 1 ? raw : `${raw}s`;
}

/** Group heading, e.g. "Media players". */
export function domainLabel(domain: string, count = 2): string {
  const noun = domainNoun(domain, count);
  return noun.charAt(0).toUpperCase() + noun.slice(1);
}

export interface ConsentGroup {
  domain: string;
  label: string;
  requests: ConsentRequest[];
}

/** Group open requests by entity domain, biggest group first (then A-Z). */
export function groupConsentRequests(requests: ConsentRequest[]): ConsentGroup[] {
  const byDomain = new Map<string, ConsentRequest[]>();
  for (const r of requests) {
    const d = entityDomain(r.entity_id);
    const bucket = byDomain.get(d);
    if (bucket) bucket.push(r);
    else byDomain.set(d, [r]);
  }
  return Array.from(byDomain.entries())
    .map(([domain, rs]) => ({
      domain,
      label: domainLabel(domain, rs.length),
      requests: rs,
    }))
    .sort(
      (a, b) =>
        b.requests.length - a.requests.length || a.domain.localeCompare(b.domain)
    );
}

// ---------------------------------------------------------------------------
// Bulk resolution
// ---------------------------------------------------------------------------

export interface ConsentBulkFailure {
  entity_id: string;
  label: string;
  message: string;
}

export interface ConsentBulkResult {
  approved: boolean;
  /** Entities the backend actually recorded a verdict for. */
  succeeded: string[];
  /** Entities whose request was already gone (resolved elsewhere, 404). */
  skipped: string[];
  failed: ConsentBulkFailure[];
}

/**
 * How many resolve calls are in flight at once.
 *
 * The appliance runs an LLM and (on first boot) a knowledge index build on the
 * same box, and every resolve rewrites the whole consent JSON file under a
 * process-wide lock — 43 parallel POSTs would queue on that lock anyway while
 * stealing CPU from everything else. Four keeps a 43-device pass under a few
 * seconds without a visible thread spike.
 */
export const CONSENT_BULK_CONCURRENCY = 4;

/**
 * Resolve many requests with bounded concurrency.
 *
 * The backend has no bulk route: `POST /sentinel/consent/requests/<entity_id>`
 * resolves ONE entity, and it keys on entity_id alone (not entity + action), so
 * targets are de-duplicated by entity here. If an entity somehow has two open
 * requests, this resolves one and the other reappears on the next refresh
 * rather than being silently double-resolved by a racing pair of calls.
 *
 * Never rejects: transport and backend errors are collected per entity so a
 * partial failure cannot read as success.
 */
export async function resolveConsentRequestsBulk(
  requests: ConsentRequest[],
  approve: boolean,
  opts: {
    concurrency?: number;
    onProgress?: (done: number, total: number) => void;
  } = {}
): Promise<ConsentBulkResult> {
  const seen = new Set<string>();
  const targets = requests.filter((r) => {
    if (seen.has(r.entity_id)) return false;
    seen.add(r.entity_id);
    return true;
  });

  const result: ConsentBulkResult = {
    approved: approve,
    succeeded: [],
    skipped: [],
    failed: [],
  };
  const total = targets.length;
  if (total === 0) return result;

  let cursor = 0;
  let done = 0;

  const worker = async () => {
    while (cursor < total) {
      const req = targets[cursor++];
      try {
        const { status, ok, data } = await apiPostRaw<{
          ok?: boolean;
          error?: string;
        }>(`/sentinel/consent/requests/${encodeURIComponent(req.entity_id)}`, {
          approve,
        });
        if (ok && data?.ok !== false) {
          result.succeeded.push(req.entity_id);
        } else if (status === 404) {
          // Queue moved under us — already resolved somewhere else. Not a
          // failure, and not something to make the owner retry.
          result.skipped.push(req.entity_id);
        } else {
          result.failed.push({
            entity_id: req.entity_id,
            label: req.label || req.entity_id,
            message: data?.error || `Request failed (${status}).`,
          });
        }
      } catch (e: any) {
        result.failed.push({
          entity_id: req.entity_id,
          label: req.label || req.entity_id,
          message: e?.message || "Could not reach PuffPAI.",
        });
      }
      done += 1;
      opts.onProgress?.(done, total);
    }
  };

  const lanes = Math.max(1, Math.min(opts.concurrency ?? CONSENT_BULK_CONCURRENCY, total));
  await Promise.all(Array.from({ length: lanes }, worker));
  return result;
}

// ---------------------------------------------------------------------------
// Dashboard camera registry
// ---------------------------------------------------------------------------

/**
 * Add a camera tile. Routes through Bridge -> Drawbridge, so a missing consent
 * comes back as 403 + a filed consent request rather than a hard error.
 */
export async function addDashboardCamera(
  entityId: string,
  label?: string
): Promise<AddCameraResult> {
  const { status, ok, data } = await apiPostRaw<{
    ok?: boolean;
    message?: string;
    cameras?: CameraTile[];
    error?: string;
  }>("/dashboard/cameras", label ? { entity_id: entityId, label } : { entity_id: entityId });

  const cameras = Array.isArray(data?.cameras) ? data!.cameras! : [];
  const message = data?.message || data?.error || `Request failed (${status}).`;

  if (ok && data?.ok) return { kind: "added", message, cameras };
  if (status === 403) return { kind: "needs_permission", message, cameras };
  return { kind: "rejected", message, cameras };
}

export async function removeDashboardCamera(
  entityId: string
): Promise<{ ok: boolean; cameras?: CameraTile[] }> {
  return apiDelete(`/dashboard/cameras/${encodeURIComponent(entityId)}`);
}

// ---------------------------------------------------------------------------
// Change notification
// ---------------------------------------------------------------------------

/**
 * Minimal pub/sub so a verdict given in one place (e.g. the consent banner)
 * refreshes the others (e.g. the camera tiles) without lifting state through
 * the whole dashboard tree.
 */
const _listeners = new Set<() => void>();

export function onConsentChanged(cb: () => void): () => void {
  _listeners.add(cb);
  return () => {
    _listeners.delete(cb);
  };
}

export function emitConsentChanged(): void {
  _listeners.forEach((cb) => {
    try {
      cb();
    } catch {
      /* a bad listener must never break the user path */
    }
  });
}
