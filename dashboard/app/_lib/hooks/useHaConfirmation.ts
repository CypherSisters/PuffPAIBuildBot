"use client";

/**
 * The confirmation flow any safety-critical Home Assistant action wraps itself in.
 *
 * Usage from a surface:
 *
 * ```tsx
 * const confirm = useHaConfirmation({
 *   onResult: (r) => { if (r.kind === "ok") refresh(); },
 *   onConsentNeeded: (msg) => setConsentNotice(msg),
 * });
 *
 * <button onClick={() => confirm.run({ kind: "control", entityId, action: "turn_off" })} />
 * <HaConfirmDialog controller={confirm} />
 * ```
 *
 * `run` fires the request once. If the backend allows it, that is the end of it.
 * If the backend answers **409 needs_confirmation**, the dialog opens and a
 * ticket is minted immediately — minting makes no call to Home Assistant, so
 * opening the dialog cannot touch the house. The dialog renders the *mint's*
 * echoed proposal, counts the ticket down, and redeems it on confirm.
 *
 * Ticket handling, deliberately:
 *
 * * The ticket lives in a `useRef`, never in React state, never in Redux, never
 *   in localStorage, never in a query string, and is never logged or put in an
 *   error message. State holds only the ticket *id* and the proposal, which are
 *   not credentials.
 * * It is cleared the moment it is spent, expires, is superseded by an
 *   amendment, or the dialog closes.
 * * A 409 never triggers a retry. Retrying on 409 would mint and burn a ticket
 *   per loop, which is the worst thing this frontend could do.
 *
 * puff puff pass
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  attemptHaRequest,
  describeRequest,
  mintHaTicket,
  type HaAttempt,
  type HaRefusalProposal,
  type HaRequest,
  type HaTicketProposal,
} from "../services/haConfirmService";

export type HaConfirmPhase =
  | "idle"
  | "running"
  | "minting"
  | "ready"
  | "expired"
  | "redeeming"
  | "done"
  | "error";

export interface HaConfirmState {
  phase: HaConfirmPhase;
  /** The request as this app understands it. Shown only to flag a mismatch. */
  request: HaRequest | null;
  /** Proposal from the 409. Authoritative for `domains` on automation kinds. */
  refusal: HaRefusalProposal | null;
  /** Why the backend refused, verbatim. */
  refusalMessage: string;
  /** Proposal echoed by the mint — what the server says it will authorise. */
  proposal: HaTicketProposal | null;
  ticketId: string | null;
  secondsLeft: number;
  message: string;
}

const IDLE: HaConfirmState = {
  phase: "idle",
  request: null,
  refusal: null,
  refusalMessage: "",
  proposal: null,
  ticketId: null,
  secondsLeft: 0,
  message: "",
};

export interface UseHaConfirmationOptions {
  /** Fires for every terminal outcome, confirmed or not. */
  onResult?: (result: HaAttempt) => void;
  /** 403 from the Drawbridge consent gate — a different flow, not this one. */
  onConsentNeeded?: (message: string) => void;
}

export interface HaConfirmController {
  state: HaConfirmState;
  /** Attempt an action. Opens the dialog only on a 409. */
  run: (req: HaRequest) => Promise<HaAttempt>;
  /** Redeem the held ticket. No-op unless a live ticket is in hand. */
  confirm: () => Promise<void>;
  /** Close and discard. Safe at any phase. */
  cancel: () => void;
  /**
   * Change the target or action inside the dialog. Discards the held ticket
   * and mints a new one — a ticket is bound to one triple, and presenting a
   * stale one to a changed action burns it for nothing.
   */
  amend: (req: HaRequest) => Promise<void>;
}

export function useHaConfirmation(
  opts: UseHaConfirmationOptions = {},
): HaConfirmController {
  const [state, setState] = useState<HaConfirmState>(IDLE);

  /** The only copy of the secret. Cleared on spend, expiry, amend and close. */
  const ticketRef = useRef<string | null>(null);
  const deadlineRef = useRef<number>(0);
  const aliveRef = useRef(true);
  const onResultRef = useRef(opts.onResult);
  const onConsentRef = useRef(opts.onConsentNeeded);
  onResultRef.current = opts.onResult;
  onConsentRef.current = opts.onConsentNeeded;

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      ticketRef.current = null;
    };
  }, []);

  const dropTicket = useCallback(() => {
    ticketRef.current = null;
    deadlineRef.current = 0;
  }, []);

  /* ── Countdown ───────────────────────────────────────────────── */

  useEffect(() => {
    if (state.phase !== "ready") return;
    const tick = () => {
      const left = Math.max(0, Math.ceil((deadlineRef.current - Date.now()) / 1000));
      if (left <= 0) {
        // Expiry is client-side too: drop the value rather than let a dead
        // ticket sit in memory waiting to be presented.
        ticketRef.current = null;
        setState((s) =>
          s.phase === "ready" ? { ...s, phase: "expired", secondsLeft: 0 } : s,
        );
        return;
      }
      setState((s) => (s.phase === "ready" ? { ...s, secondsLeft: left } : s));
    };
    tick();
    const iv = setInterval(tick, 1000);
    return () => clearInterval(iv);
  }, [state.phase]);

  /* ── Mint ────────────────────────────────────────────────────── */

  const mintFor = useCallback(
    async (req: HaRequest) => {
      dropTicket();
      setState((s) => ({
        ...s,
        phase: "minting",
        request: req,
        proposal: null,
        ticketId: null,
        secondsLeft: 0,
        message: "",
      }));

      const { binding } = describeRequest(req);
      const minted = await mintHaTicket(binding);
      if (!aliveRef.current) return;

      if (!minted.ok) {
        setState((s) => ({ ...s, phase: "error", message: minted.message }));
        return;
      }

      const { ticket, ...safe } = minted.result;
      ticketRef.current = ticket;
      const ttl = Number(safe.expires_in) > 0 ? Number(safe.expires_in) : 90;
      deadlineRef.current = Date.now() + ttl * 1000;

      setState((s) => ({
        ...s,
        phase: "ready",
        proposal: safe.proposal,
        ticketId: safe.ticket_id ?? null,
        secondsLeft: ttl,
        message: "",
      }));
    },
    [dropTicket],
  );

  /* ── Run ─────────────────────────────────────────────────────── */

  const run = useCallback(
    async (req: HaRequest): Promise<HaAttempt> => {
      dropTicket();
      setState({ ...IDLE, phase: "running", request: req });

      const result = await attemptHaRequest(req);
      if (!aliveRef.current) return result;

      if (result.kind === "needs_confirmation") {
        // A decision point, not a failure: open the dialog, mint once, and do
        // not retry the original call.
        setState({
          ...IDLE,
          phase: "minting",
          request: req,
          refusal: result.proposal,
          refusalMessage: result.message,
        });
        await mintFor(req);
        return result;
      }

      if (result.kind === "needs_consent") {
        setState(IDLE);
        onConsentRef.current?.(result.message);
        onResultRef.current?.(result);
        return result;
      }

      setState(IDLE);
      onResultRef.current?.(result);
      return result;
    },
    [dropTicket, mintFor],
  );

  /* ── Confirm ─────────────────────────────────────────────────── */

  const confirm = useCallback(async () => {
    const ticket = ticketRef.current;
    const req = state.request;
    if (!ticket || !req || state.phase !== "ready") return;

    // Spend it: the backend consumes on first presentation regardless of the
    // outcome, so this reference must not outlive the call.
    dropTicket();
    setState((s) => ({ ...s, phase: "redeeming" }));

    const result = await attemptHaRequest(req, ticket);
    if (!aliveRef.current) return;

    if (result.kind === "ok") {
      setState((s) => ({ ...s, phase: "done", message: result.message }));
      onResultRef.current?.(result);
      return;
    }
    if (result.kind === "needs_consent") {
      setState(IDLE);
      onConsentRef.current?.(result.message);
      onResultRef.current?.(result);
      return;
    }
    // A second 409 means the ticket did not satisfy the gate (expired between
    // the countdown and the round trip, or bound to something else). Say so and
    // stop — no automatic re-mint, no retry.
    const message =
      result.kind === "needs_confirmation"
        ? "That confirmation was not accepted. Nothing happened. Start again."
        : result.message;
    setState((s) => ({ ...s, phase: "error", message }));
    onResultRef.current?.(result);
  }, [dropTicket, state.request, state.phase]);

  /* ── Amend / cancel ──────────────────────────────────────────── */

  const amend = useCallback(
    async (req: HaRequest) => {
      await mintFor(req);
    },
    [mintFor],
  );

  const cancel = useCallback(() => {
    dropTicket();
    setState(IDLE);
  }, [dropTicket]);

  return { state, run, confirm, cancel, amend };
}
