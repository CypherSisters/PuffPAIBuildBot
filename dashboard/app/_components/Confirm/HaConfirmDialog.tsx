"use client";

/**
 * The one screen where a person authorises a safety-critical house action.
 *
 * Everything above the buttons is the **server's** words: the proposal echoed
 * by `POST /ha/confirm`, which is the server stating what the ticket it just
 * minted will authorise. Rendering the app's own copy of the request instead
 * would be theatre — a mismatch between what the owner meant and what the
 * backend is about to do is exactly what this screen exists to expose, and it
 * can only be caught by showing the far side's version.
 *
 * Not an error dialog. A 409 is a question, so this is amber and blue, never
 * the red of a fault, and it never appears on its own — only when something the
 * owner clicked needs one more deliberate step.
 *
 * puff puff pass
 */

import { mdiAlertOutline, mdiCheck, mdiClose, mdiTimerSandComplete } from "@mdi/js";
import Icon from "../Icon";
import type { HaConfirmController } from "@/app/_lib/hooks/useHaConfirmation";
import {
  actionSentence,
  describeRequest,
  stakeLines,
  type HaRequest,
} from "@/app/_lib/services/haConfirmService";

/** Alternative verbs offered inline for a `control` request. */
const CONTROL_VERBS: Array<{ action: "turn_off" | "turn_on" | "toggle"; label: string }> = [
  { action: "turn_off", label: "Turn off" },
  { action: "turn_on", label: "Turn on" },
  { action: "toggle", label: "Toggle" },
];

export default function HaConfirmDialog({
  controller,
}: {
  controller: HaConfirmController;
}) {
  const { state, confirm, cancel, amend } = controller;
  if (state.phase === "idle" || state.phase === "running") return null;

  const { proposal, refusal, request } = state;

  // The mint cannot see inside a scene, a script, or an automation's stored
  // actions — it makes no call to Home Assistant, by design. When it says so
  // (via `note`), the 409's domain list is the complete one and this is where
  // it gets shown.
  const mintIncomplete = Boolean(proposal?.note);
  const domains =
    (mintIncomplete ? refusal?.domains : proposal?.domains)?.filter(Boolean) ?? [];
  const criticalDomains =
    proposal && !mintIncomplete && proposal.safety_critical_domains?.length
      ? proposal.safety_critical_domains
      : domains;

  const headline = proposal
    ? actionSentence(proposal)
    : "This action needs your confirmation.";
  const stakes = proposal ? stakeLines(criticalDomains, proposal.action) : [];

  // Did the server bind the ticket to what this app asked for? A difference is
  // the single most important thing on this screen.
  const expected = request ? describeRequest(request).binding : null;
  const mismatch =
    !!proposal &&
    !!expected &&
    (String(proposal.entity_id ?? "") !== String(expected.entity_id ?? "") ||
      String(proposal.action ?? "") !== String(expected.action ?? ""));

  const canAmend = request?.kind === "control" && state.phase !== "redeeming";

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Confirm a safety-critical action"
    >
      <div className="w-full max-w-md overflow-hidden rounded-lg border border-[#ff9500]/30 bg-[#06060e] shadow-[0_0_40px_rgba(0,0,0,0.8)]">
        {/* Header */}
        <div className="flex items-center gap-2 border-b border-(--line) bg-[#ff9500]/[0.06] px-4 py-2.5">
          <span className="text-[#ff9500]">
            <Icon path={mdiAlertOutline} size="16" />
          </span>
          <span className="text-[11px] font-semibold uppercase tracking-wider text-[#ff9500]">
            Needs a person
          </span>
          <button
            type="button"
            onClick={cancel}
            aria-label="Cancel"
            className="ml-auto rounded p-0.5 text-(--text3) transition hover:text-(--puff-text-strong)"
          >
            <Icon path={mdiClose} size="14" />
          </button>
        </div>

        <div className="px-4 py-3">
          {state.phase === "minting" && (
            <p className="py-4 text-center text-xs text-[#8899aa]">
              Asking PuffPAI what this would authorise…
            </p>
          )}

          {state.phase === "error" && (
            <>
              <p className="text-xs text-(--puff-text-strong)">{state.message}</p>
              <p className="mt-1 text-[11px] text-[#8899aa]">Nothing was changed.</p>
            </>
          )}

          {state.phase === "done" && (
            <>
              <p className="flex items-center gap-1.5 text-xs text-[#39ff14]">
                <Icon path={mdiCheck} size="14" />
                Done.
              </p>
              {state.message && (
                <p className="mt-1 text-[11px] text-[#8899aa]">{state.message}</p>
              )}
            </>
          )}

          {(state.phase === "ready" ||
            state.phase === "expired" ||
            state.phase === "redeeming") &&
            proposal && (
              <>
                {/* The server's sentence */}
                <p className="text-sm font-medium leading-snug text-(--puff-text-strong)">{headline}</p>

                {stakes.map((line) => (
                  <p key={line} className="mt-1.5 text-[11px] leading-relaxed text-[#ff9500]">
                    {line}
                  </p>
                ))}
                {stakes.length === 0 && (
                  <p className="mt-1.5 text-[11px] leading-relaxed text-[#8899aa]">
                    PuffPAI could not work out what this touches from the request alone, so
                    it is asking rather than guessing.
                  </p>
                )}

                {/* Verbatim echo — what the ticket is bound to */}
                <dl className="mt-3 space-y-1 rounded-md border border-(--line) bg-(--card)/[0.02] px-2.5 py-2 text-[10px]">
                  <Row label="Target" value={String(proposal.entity_id ?? "—")} mono />
                  <Row label="Action" value={String(proposal.action ?? "—")} mono />
                  <Row
                    label="Touches"
                    value={domains.length ? domains.join(", ") : "not determined"}
                    mono
                  />
                  {state.refusalMessage && (
                    <Row label="Refused because" value={state.refusalMessage} />
                  )}
                </dl>

                {mismatch && (
                  <p className="mt-2 rounded-md border border-[#ff2d6f]/40 bg-[#ff2d6f]/[0.06] px-2.5 py-2 text-[11px] leading-relaxed text-[#ff2d6f]">
                    This does not match what the dashboard asked for
                    {expected ? ` (${expected.entity_id ?? "—"} · ${expected.action})` : ""}.
                    Cancel unless you know why.
                  </p>
                )}

                {canAmend && (
                  <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    <span className="text-[10px] uppercase tracking-wider text-[#8899aa]/60">
                      Instead
                    </span>
                    {CONTROL_VERBS.filter(
                      (v) => request?.kind === "control" && v.action !== request.action,
                    ).map((v) => (
                      <button
                        key={v.action}
                        type="button"
                        onClick={() => {
                          if (request?.kind !== "control") return;
                          // Changing the action invalidates the held ticket, so
                          // discard and mint a fresh one bound to the new verb.
                          const next: HaRequest = { ...request, action: v.action };
                          void amend(next);
                        }}
                        className="rounded border border-(--line) px-1.5 py-0.5 text-[10px] text-[#8899aa] transition hover:border-[#8899aa]/50 hover:text-(--puff-text-strong)"
                      >
                        {v.label}
                      </button>
                    ))}
                  </div>
                )}
              </>
            )}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-2 border-t border-(--line) px-4 py-2.5">
          {state.phase === "ready" && (
            <>
              <span className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-[#8899aa]">
                <Icon path={mdiTimerSandComplete} size="12" />
                {state.secondsLeft}s
              </span>
              <div className="ml-auto flex gap-2">
                <button
                  type="button"
                  onClick={cancel}
                  className="rounded-md border border-[#8899aa]/25 px-2.5 py-1 text-[11px] font-medium text-[#8899aa] transition hover:border-[#8899aa]/50 hover:text-(--puff-text-strong)"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void confirm()}
                  className="flex items-center gap-1 rounded-md border border-[#39ff14]/30 bg-[#39ff14]/10 px-2.5 py-1 text-[11px] font-medium text-[#39ff14] transition hover:bg-[#39ff14]/20"
                >
                  <Icon path={mdiCheck} size="12" />
                  Yes, do it
                </button>
              </div>
            </>
          )}

          {state.phase === "expired" && (
            <>
              <span className="text-[11px] text-[#8899aa]">
                This confirmation expired. Start again.
              </span>
              <button
                type="button"
                onClick={cancel}
                className="ml-auto rounded-md border border-[#8899aa]/25 px-2.5 py-1 text-[11px] font-medium text-[#8899aa] transition hover:border-[#8899aa]/50 hover:text-(--puff-text-strong)"
              >
                Close
              </button>
            </>
          )}

          {state.phase === "redeeming" && (
            <span className="ml-auto text-[11px] text-[#8899aa]">Confirming…</span>
          )}

          {(state.phase === "done" || state.phase === "error") && (
            <button
              type="button"
              onClick={cancel}
              className="ml-auto rounded-md border border-[#8899aa]/25 px-2.5 py-1 text-[11px] font-medium text-[#8899aa] transition hover:border-[#8899aa]/50 hover:text-(--puff-text-strong)"
            >
              Close
            </button>
          )}

          {state.phase === "minting" && (
            <button
              type="button"
              onClick={cancel}
              className="ml-auto rounded-md border border-[#8899aa]/25 px-2.5 py-1 text-[11px] font-medium text-[#8899aa] transition hover:border-[#8899aa]/50 hover:text-(--puff-text-strong)"
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex gap-2">
      <dt className="w-24 flex-shrink-0 uppercase tracking-wider text-[#8899aa]/50">
        {label}
      </dt>
      <dd className={`min-w-0 flex-1 break-words text-[#8899aa] ${mono ? "font-mono" : ""}`}>
        {value}
      </dd>
    </div>
  );
}
