"use client";

/**
 * Drawbridge consent requests — the owner-facing approve/deny surface.
 * (Drawbridge is the display name; `sentinel` is the agent id the API uses.)
 *
 * Drawbridge blocks privacy-sensitive actions (putting a camera on the
 * dashboard, for one) and files a consent request instead of failing. Nothing
 * used to read that queue, so requests were invisible. This renders them where
 * the owner lands, with the two buttons that resolve them.
 *
 * Deliberately quiet: an empty queue renders nothing at all, and a pending
 * request is amber/informational, never a red alarm — it's a question, not a
 * fault.
 *
 * A first-run consent pass files one request per controllable entity, so the
 * queue can open at 40+. Past GROUP_THRESHOLD the rows collapse into device-type
 * groups with bulk verdicts. The asymmetry is on purpose: bulk DENY is one
 * click because denying is the safe direction, while "Approve everything" has
 * to be confirmed with a sentence saying what it actually grants. Per-row
 * Approve/Deny never goes away.
 *
 * puff puff pass
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  mdiShieldAccountOutline,
  mdiCheck,
  mdiClose,
  mdiChevronRight,
  mdiChevronDown,
} from "@mdi/js";
import Icon from "../Icon";
import {
  getConsentRequests,
  resolveConsentRequest,
  resolveConsentRequestsBulk,
  groupConsentRequests,
  domainNoun,
  emitConsentChanged,
  onConsentChanged,
  type ConsentRequest,
  type ConsentBulkResult,
} from "@/app/_lib/services/consentService";

const POLL_MS = 30_000;

/* ── Copy helpers ──────────────────────────────────────────────── */

const ACTION_COPY: Record<string, string> = {
  dashboard_add_camera: "Show this camera on the dashboard",
};

function actionLabel(action: string): string {
  if (ACTION_COPY[action]) return ACTION_COPY[action];
  // Fall back to a readable version of whatever Drawbridge filed.
  return action.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

function relativeTime(iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const mins = Math.floor((Date.now() - then) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

/* ── Data hook ─────────────────────────────────────────────────── */

export function useConsentRequests() {
  const [requests, setRequests] = useState<ConsentRequest[]>([]);
  const [loadFailed, setLoadFailed] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setRequests(await getConsentRequests());
      setLoadFailed(false);
    } catch {
      // Backend offline is already reported by the page-level banner; don't
      // add a second scary message here.
      setLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, POLL_MS);
    const off = onConsentChanged(refresh);
    return () => {
      clearInterval(iv);
      off();
    };
  }, [refresh]);

  return { requests, loadFailed, refresh };
}

/* ── One request ───────────────────────────────────────────────── */

export function ConsentRequestRow({
  request,
  onResolved,
}: {
  request: ConsentRequest;
  /** Called after a successful verdict, with the backend's message (if any). */
  onResolved?: (approved: boolean, message: string | null) => void;
}) {
  const [busy, setBusy] = useState<null | "approve" | "deny">(null);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);

  const decide = async (approve: boolean) => {
    setBusy(approve ? "approve" : "deny");
    setError(null);
    try {
      const res = await resolveConsentRequest(request.entity_id, approve);
      const message =
        res.message ||
        (approve ? "Approved." : "Denied. Nothing was added.");
      setOutcome(message);
      onResolved?.(approve, res.message ?? null);
      // Refresh every consent-aware surface (this list, the camera tiles).
      emitConsentChanged();
    } catch (e: any) {
      setError(e?.message || "Could not record that. Try again.");
    } finally {
      setBusy(null);
    }
  };

  if (outcome) {
    return (
      <div className="rounded-lg border border-(--line) bg-(--card)/[0.02] px-3 py-2 text-xs text-[#8899aa]">
        {outcome}
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-[#ff9500]/20 bg-[#ff9500]/[0.04] px-3 py-2.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-(--puff-text-strong)">
            {actionLabel(request.action)}
          </p>
          <p className="mt-0.5 truncate text-xs text-[#8899aa]">
            {request.label}{" "}
            <span className="font-mono text-[10px] text-[#8899aa]/60">
              {request.entity_id}
            </span>
          </p>
          <p className="mt-1 text-[11px] text-[#8899aa]/70">
            {request.reason}
            {request.created_at ? ` · asked ${relativeTime(request.created_at)}` : ""}
          </p>
        </div>
        <div className="flex flex-shrink-0 gap-2">
          <button
            type="button"
            onClick={() => decide(true)}
            disabled={busy !== null}
            className="flex items-center gap-1 rounded-md border border-[#39ff14]/30 bg-[#39ff14]/10 px-2.5 py-1 text-[11px] font-medium text-[#39ff14] transition hover:bg-[#39ff14]/20 disabled:opacity-40"
          >
            <Icon path={mdiCheck} size="12" />
            {busy === "approve" ? "Approving…" : "Approve"}
          </button>
          <button
            type="button"
            onClick={() => decide(false)}
            disabled={busy !== null}
            className="flex items-center gap-1 rounded-md border border-[#8899aa]/25 px-2.5 py-1 text-[11px] font-medium text-[#8899aa] transition hover:border-[#8899aa]/50 hover:text-(--puff-text-strong) disabled:opacity-40"
          >
            <Icon path={mdiClose} size="12" />
            {busy === "deny" ? "Denying…" : "Deny"}
          </button>
        </div>
      </div>
      {error && (
        <p className="mt-2 text-[11px] text-[#ff2d6f]">{error}</p>
      )}
    </div>
  );
}

/* ── Bulk plumbing ─────────────────────────────────────────────── */

/** Above this many open requests, one-row-at-a-time stops being usable. */
const GROUP_THRESHOLD = 6;

interface BulkRun {
  approve: boolean;
  /** What the run covers, for the progress line ("13 media players"). */
  scope: string;
  done: number;
  total: number;
}

const BTN_APPROVE =
  "flex items-center gap-1 rounded-md border border-[#39ff14]/30 bg-[#39ff14]/10 px-2.5 py-1 text-[11px] font-medium text-[#39ff14] transition hover:bg-[#39ff14]/20 disabled:opacity-40";
const BTN_DENY =
  "flex items-center gap-1 rounded-md border border-[#8899aa]/25 px-2.5 py-1 text-[11px] font-medium text-[#8899aa] transition hover:border-[#8899aa]/50 hover:text-(--puff-text-strong) disabled:opacity-40";

/* ── One domain group ──────────────────────────────────────────── */

function ConsentGroup({
  domain,
  label,
  requests,
  open,
  onToggle,
  onBulk,
  disabled,
}: {
  domain: string;
  label: string;
  requests: ConsentRequest[];
  open: boolean;
  onToggle: () => void;
  onBulk: (requests: ConsentRequest[], approve: boolean, scope: string) => void;
  disabled: boolean;
}) {
  const n = requests.length;
  const noun = domainNoun(domain, n);
  const scope = `${n} ${noun}`;

  return (
    <div className="rounded-lg border border-(--line) bg-(--card)/[0.02]">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
        >
          <span className="text-[#8899aa]">
            <Icon path={open ? mdiChevronDown : mdiChevronRight} size="14" />
          </span>
          <span className="text-xs font-medium text-(--puff-text-strong)">{label}</span>
          <span className="rounded-full border border-[#ff9500]/30 px-1.5 text-[10px] text-[#ff9500]">
            {n}
          </span>
        </button>
        <div className="flex flex-shrink-0 gap-2">
          <button
            type="button"
            disabled={disabled}
            onClick={() => onBulk(requests, true, scope)}
            className={BTN_APPROVE}
          >
            <Icon path={mdiCheck} size="12" />
            Approve all {scope}
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onBulk(requests, false, scope)}
            className={BTN_DENY}
          >
            <Icon path={mdiClose} size="12" />
            Deny all {n}
          </button>
        </div>
      </div>
      {open && (
        <div className="space-y-2 border-t border-(--line) px-3 py-2">
          {requests.map((r) => (
            <ConsentRequestRow key={`${r.entity_id}:${r.action}`} request={r} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Outcome of a bulk run ─────────────────────────────────────── */

function BulkOutcome({
  result,
  onDismiss,
}: {
  result: ConsentBulkResult;
  onDismiss: () => void;
}) {
  const ok = result.succeeded.length;
  const verb = result.approved ? "Approved" : "Denied";
  const clean = result.failed.length === 0;

  return (
    <div
      className={`rounded-lg border px-3 py-2.5 ${
        clean
          ? "border-(--line) bg-(--card)/[0.02]"
          : "border-[#ff2d6f]/25 bg-[#ff2d6f]/[0.04]"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs text-(--puff-text-strong)">
            {verb} {ok}
            {result.failed.length > 0
              ? ` of ${ok + result.failed.length}`
              : ""}
            .
            {clean && result.approved
              ? " You can revoke any of them later."
              : ""}
          </p>
          {result.skipped.length > 0 && (
            <p className="mt-1 text-[11px] text-[#8899aa]/70">
              {result.skipped.length} had already been answered elsewhere.
            </p>
          )}
          {result.failed.length > 0 && (
            <>
              <p className="mt-1.5 text-[11px] text-[#ff2d6f]">
                {result.failed.length} could not be recorded. They are still in
                the list. Try them again.
              </p>
              <ul className="mt-1 space-y-0.5">
                {result.failed.map((f) => (
                  <li key={f.entity_id} className="text-[11px] text-[#8899aa]">
                    <span className="font-mono text-[10px] text-[#8899aa]/60">
                      {f.entity_id}
                    </span>{" "}
                    : {f.message}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
        <button
          type="button"
          onClick={onDismiss}
          className="flex-shrink-0 text-[11px] text-[#8899aa] transition hover:text-(--puff-text-strong)"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

/* ── Banner (renders nothing when the queue is empty) ──────────── */

export default function ConsentRequestsBanner() {
  const { requests, loadFailed } = useConsentRequests();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [bulk, setBulk] = useState<BulkRun | null>(null);
  const [result, setResult] = useState<ConsentBulkResult | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const runBulk = useCallback(
    async (targets: ConsentRequest[], approve: boolean, scope: string) => {
      if (targets.length === 0) return;
      setConfirmAll(false);
      setResult(null);
      setBulk({ approve, scope, done: 0, total: targets.length });
      // Awaited here but nothing else in the dashboard waits on it: the rest of
      // the page keeps rendering and polling while this drains.
      const res = await resolveConsentRequestsBulk(targets, approve, {
        onProgress: (done, total) => {
          if (alive.current) setBulk((b) => (b ? { ...b, done, total } : b));
        },
      });
      // Tell the other consent-aware surfaces (this list, the camera tiles)
      // even if we've unmounted — the verdicts landed either way.
      emitConsentChanged();
      if (!alive.current) return;
      setBulk(null);
      setResult(res);
    },
    []
  );

  // Nothing to show and nothing to report: stay out of the way entirely.
  if (loadFailed) return null;
  if (requests.length === 0 && !bulk && !result) return null;

  const grouped = requests.length > GROUP_THRESHOLD;
  const groups = grouped ? groupConsentRequests(requests) : [];
  const busy = bulk !== null;

  const toggle = (domain: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(domain)) next.delete(domain);
      else next.add(domain);
      return next;
    });

  return (
    <div className="mb-4 rounded-xl border border-[#ff9500]/25 bg-[#ff9500]/[0.05] p-4">
      <div className="mb-1.5 flex items-center gap-2">
        <span style={{ color: "var(--puff-gold)" }}>
          <Icon path={mdiShieldAccountOutline} size="16" />
        </span>
        <h3 className="text-sm font-semibold text-(--puff-text-strong)">
          Drawbridge needs your permission
        </h3>
        {requests.length > 0 && (
          <span className="rounded-full border border-[#ff9500]/30 px-1.5 text-[10px] text-[#ff9500]">
            {requests.length}
          </span>
        )}
      </div>
      <p className="mb-2.5 text-[11px] text-[#8899aa]">
        PuffPAI asks before it can control a device. Approve the ones you want
        it to manage. You can change any answer later.
      </p>

      {bulk && (
        <div className="mb-2.5 rounded-lg border border-(--line) bg-(--card)/[0.02] px-3 py-2 text-xs text-[#8899aa]">
          {bulk.approve ? "Approving" : "Denying"} {bulk.scope}: {bulk.done} of{" "}
          {bulk.total}…
        </div>
      )}

      {result && !bulk && (
        <div className="mb-2.5">
          <BulkOutcome result={result} onDismiss={() => setResult(null)} />
        </div>
      )}

      {grouped && (
        <div className="mb-2.5 flex flex-wrap items-center gap-2 rounded-lg border border-(--line) bg-(--card)/[0.02] px-3 py-2">
          {confirmAll ? (
            <>
              <p className="min-w-0 flex-1 text-[11px] text-(--puff-text-strong)">
                This lets PuffPAI control all {requests.length} devices. You can
                revoke any of them later.
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  runBulk(requests, true, `all ${requests.length} devices`)
                }
                className={BTN_APPROVE}
              >
                <Icon path={mdiCheck} size="12" />
                Yes, approve all {requests.length}
              </button>
              <button
                type="button"
                onClick={() => setConfirmAll(false)}
                className={BTN_DENY}
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              <p className="min-w-0 flex-1 text-[11px] text-[#8899aa]">
                {requests.length} requests, grouped by device type.
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={() => setConfirmAll(true)}
                className={BTN_APPROVE}
              >
                <Icon path={mdiCheck} size="12" />
                Approve everything
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  runBulk(requests, false, `all ${requests.length} requests`)
                }
                className={BTN_DENY}
              >
                <Icon path={mdiClose} size="12" />
                Deny everything
              </button>
            </>
          )}
        </div>
      )}

      <div className="space-y-2">
        {grouped
          ? groups.map((g) => (
              <ConsentGroup
                key={g.domain}
                domain={g.domain}
                label={g.label}
                requests={g.requests}
                open={expanded.has(g.domain)}
                onToggle={() => toggle(g.domain)}
                onBulk={runBulk}
                disabled={busy}
              />
            ))
          : requests.map((r) => (
              <ConsentRequestRow key={`${r.entity_id}:${r.action}`} request={r} />
            ))}
      </div>
    </div>
  );
}
