"use client";

/**
 * ImportEvents — "load a file, watch the calendar fill in."
 *
 * Flow: pick a file → backend parses it into CANDIDATES (.ics/.csv are
 * deterministic; PDFs/docs/freeform go through LLM extraction) → the user
 * reviews a checklist and confirms → each kept event is added through the
 * normal cal_add_event action. AI guesses never touch the calendar without
 * eyes on them.
 *
 * It also opens CONTROLLED: pass `initialResult` (a Puff suggestion whose
 * candidates were already extracted) and it skips straight to the same review
 * screen, with no file picker of its own. Same checkboxes, same confirm button
 * — a proactive suggestion gets no shortcut a manual import doesn't have.
 *
 * NOT IN THIS BUILD: the file picker.
 *
 * Picking a file needs POST /apps/calendar/ingest to turn it into candidates,
 * and BuildBot's backend does not carry that route — the ICS/CSV/LLM extractor
 * is a HomeBot-era addition to the calendar app. So the button is replaced by
 * one line of text saying so, rather than a button that posts into a 404 and
 * then blames the file.
 *
 * The CONTROLLED path is untouched and still works, because it needs none of
 * it: the candidates arrive already extracted, and confirming them goes through
 * `cal_add_event` on /apps/<name>/action, which this backend does have. The
 * review screen, the checkboxes and the confirm are all still real.
 */

import { useCallback, useState } from "react";
import {
  mdiFileUploadOutline,
  mdiClose,
  mdiRepeat,
  mdiMapMarker,
  mdiRobotOutline,
  mdiCheckCircle,
} from "@mdi/js";
import Button from "../../../_components/Button";
import Icon from "../../../_components/Icon";

export interface Candidate {
  title: string;
  start_iso: string;
  end_iso: string;
  cat: string;
  location: string | null;
  recurrence: string | null;
}

export interface IngestResult {
  success: boolean;
  message?: string;
  method?: "ics" | "csv" | "llm" | "none";
  events?: Candidate[];
  notes?: string[];
}

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleDateString([], {
    weekday: "short",
    month: "short",
    day: "numeric",
  }) +
    " " +
    d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export default function ImportEvents({
  runAction,
  onDone,
  initialResult,
  initialLabel,
  onClose,
}: {
  runAction: (action: string, params?: Record<string, unknown>) => Promise<unknown>;
  onDone?: () => void;
  /** Candidates extracted elsewhere (a Puff suggestion) — opens at review. */
  initialResult?: IngestResult;
  /** Header caption in controlled mode (the document's name). */
  initialLabel?: string;
  /** Controlled mode only: the parent owns mounting, so closing is its call. */
  onClose?: () => void;
}) {
  const controlled = !!initialResult;
  const [phase, setPhase] = useState<
    "idle" | "review" | "adding" | "done"
  >(controlled ? "review" : "idle");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<IngestResult | null>(initialResult ?? null);
  const [keep, setKeep] = useState<boolean[]>(() =>
    (initialResult?.events ?? []).map(() => true)
  );
  const [added, setAdded] = useState(0);
  // Only the controlled path can name a source now, and it names it once.
  const fileName = initialLabel ?? "";

  const reset = useCallback(() => {
    if (controlled) {
      onClose?.();
      return;
    }
    setPhase("idle");
    setError(null);
    setResult(null);
    setKeep([]);
    setAdded(0);
  }, [controlled, onClose]);

  // The uploader that used to live here is gone with its endpoint. Nothing
  // calls /apps/calendar/ingest any more; `error` below is kept because the
  // confirm path still has failures worth showing.
  const addSelected = useCallback(async () => {
    if (!result?.events) return;
    setPhase("adding");
    let ok = 0;
    for (let i = 0; i < result.events.length; i++) {
      if (!keep[i]) continue;
      const ev = result.events[i];
      try {
        await runAction("cal_add_event", {
          title: ev.title,
          start_iso: ev.start_iso,
          end_iso: ev.end_iso,
          cat: ev.cat,
          location: ev.location ?? undefined,
          recurrence: ev.recurrence ?? undefined,
        });
        ok++;
        setAdded(ok);
      } catch {
        // keep going — the summary reports how many landed
      }
    }
    setPhase("done");
    onDone?.();
  }, [result, keep, runAction, onDone]);

  const keptCount = keep.filter(Boolean).length;
  const open = phase !== "idle";

  return (
    <>
      {!controlled && (
        <span
          className="inline-flex items-center gap-1.5 text-xs"
          style={{ color: "var(--text3)" }}
          title="POST /apps/calendar/ingest is not part of this build's backend."
        >
          <Icon path={mdiFileUploadOutline} size="14" w="w-4" h="h-4" />
          Importing a file is not in this build, so add events here, or ask for
          them out loud.
        </span>
      )}

      {open && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-[var(--puff-surface-deep)] shadow-2xl backdrop-blur-[18px]">
            {/* Header */}
            <div className="flex items-center justify-between border-b border-[var(--line)] px-5 py-3">
              <div className="flex items-center gap-2">
                <Icon path={mdiFileUploadOutline} size="20" className="text-[var(--sky)]" />
                <h3 className="font-semibold text-[var(--text)]">
                  Import events: {fileName}
                </h3>
              </div>
              <button
                onClick={reset}
                className="rounded p-1 text-[var(--text3)] transition hover:text-[var(--text2)]"
                aria-label="Close import"
              >
                <Icon path={mdiClose} size="18" />
              </button>
            </div>

            {/* Body */}
            <div className="min-h-0 flex-1 overflow-y-auto p-5">
              {/* There is no "parsing" phase any more: the uploader and its
                  /apps/calendar/ingest endpoint are gone, so nothing can put
                  this component into a reading state. The spinner branch that
                  used to sit here was unreachable. */}
              {(phase === "review" || phase === "adding") && result && (
                <>
                  {result.method === "llm" && (
                    <div className="mb-3 flex items-center gap-2 rounded-lg bg-[color-mix(in_srgb,var(--lemon)_10%,transparent)] px-3 py-2 text-xs text-[var(--lemon)]">
                      <Icon path={mdiRobotOutline} size="16" />
                      These were read by the AI, so double-check dates before adding.
                    </div>
                  )}
                  {(result.notes ?? [])
                    .filter((n) => !n.startsWith("Events below"))
                    .map((n, i) => (
                      <p key={i} className="mb-2 text-xs text-[var(--text2)]">
                        {n}
                      </p>
                    ))}

                  {(result.events ?? []).length === 0 ? (
                    <p className="py-8 text-center text-sm text-[var(--text2)]">
                      No events found in this file.
                    </p>
                  ) : (
                    <ul className="space-y-2">
                      {(result.events ?? []).map((ev, i) => (
                        <li
                          key={i}
                          className={`flex items-center gap-3 rounded-xl border p-3 transition ${
                            keep[i]
                              ? "border-[var(--sky)]/40 bg-[var(--sky)]/5"
                              : "border-[var(--line)] opacity-50"
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={keep[i] ?? false}
                            disabled={phase === "adding"}
                            onChange={() =>
                              setKeep((k) => {
                                const next = [...k];
                                next[i] = !next[i];
                                return next;
                              })
                            }
                            className="h-4 w-4 accent-[var(--sky)]"
                          />
                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-semibold text-[var(--text)]">
                              {ev.title}
                            </p>
                            <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-[var(--text2)]">
                              <span className="font-mono">{fmtWhen(ev.start_iso)}</span>
                              <span className="rounded-full bg-[var(--chip)] px-2 py-0.5">
                                {ev.cat}
                              </span>
                              {ev.recurrence && (
                                <span className="inline-flex items-center gap-0.5">
                                  <Icon path={mdiRepeat} size="12" />
                                  {ev.recurrence}
                                </span>
                              )}
                              {ev.location && (
                                <span className="inline-flex items-center gap-0.5 truncate">
                                  <Icon path={mdiMapMarker} size="12" />
                                  {ev.location}
                                </span>
                              )}
                            </p>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              )}

              {phase === "done" && (
                <div className="flex flex-col items-center gap-3 py-10 text-center">
                  <Icon path={mdiCheckCircle} size="40" className="text-[var(--mint)]" />
                  <p className="text-sm font-semibold text-[var(--text)]">
                    Added {added} event{added === 1 ? "" : "s"} to the calendar.
                  </p>
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="flex items-center justify-between border-t border-[var(--line)] px-5 py-3">
              {phase === "done" ? (
                <Button label="Close" color="info" small roundedFull onClick={reset} />
              ) : (
                <>
                  <span className="text-xs text-[var(--text3)]">
                    {phase === "adding"
                      ? `Adding… ${added}/${keptCount}`
                      : `${keptCount} of ${result?.events?.length ?? 0} selected`}
                  </span>
                  <div className="flex gap-2">
                    <Button
                      label="Cancel"
                      color="whiteDark"
                      small
                      roundedFull
                      onClick={reset}
                    />
                    <Button
                      label={
                        phase === "adding"
                          ? "Adding…"
                          : `Add ${keptCount} event${keptCount === 1 ? "" : "s"}`
                      }
                      color="success"
                      small
                      roundedFull
                      disabled={phase === "adding" || keptCount === 0}
                      onClick={addSelected}
                    />
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
