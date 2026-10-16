"use client";

/**
 * ImportRecipes — "Puff read a recipe off a photo; save the ones you want."
 *
 * The mirror of ImportEvents for FreshTable. It opens CONTROLLED only: a Puff
 * suggestion whose recipe candidates were already extracted by the backend is
 * passed in as `initialResult`, and the modal shows a review checklist. Each
 * kept recipe is written through the normal fresh_create_recipe app action,
 * one at a time, AFTER the user ticks it and presses save.
 *
 * Same trust model as the calendar import: an LLM's reading of a recipe card
 * never lands in FreshTable without eyes on it.
 */

import { useCallback, useState } from "react";
import {
  mdiSilverwareForkKnife,
  mdiClose,
  mdiRobotOutline,
  mdiCheckCircle,
  mdiClockOutline,
  mdiAccountGroup,
} from "@mdi/js";
import Button from "../../../_components/Button";
import Icon from "../../../_components/Icon";

export interface RecipeCandidate {
  name: string;
  ingredients: string;
  instructions: string;
  yield?: string | null;
  prep_time?: string | null;
}

export interface RecipeResult {
  success: boolean;
  message?: string;
  method?: "llm" | "none";
  recipes?: RecipeCandidate[];
  notes?: string[];
}

/** First slice of a long field, for the review card preview. */
function snippet(text: string, max: number): string {
  const t = (text || "").replace(/\s+/g, " ").trim();
  return t.length > max ? t.slice(0, max).trimEnd() + "…" : t;
}

export default function ImportRecipes({
  runAction,
  onDone,
  initialResult,
  initialLabel,
  onClose,
}: {
  runAction: (action: string, params?: Record<string, unknown>) => Promise<unknown>;
  onDone?: () => void;
  /** Candidates extracted elsewhere (a Puff suggestion) — opens at review. */
  initialResult: RecipeResult;
  /** Header caption (the document's name). */
  initialLabel?: string;
  /** The parent owns mounting, so closing is its call. */
  onClose?: () => void;
}) {
  const [phase, setPhase] = useState<"review" | "adding" | "done">("review");
  const [result] = useState<RecipeResult>(initialResult);
  const [keep, setKeep] = useState<boolean[]>(() =>
    (initialResult.recipes ?? []).map(() => true)
  );
  const [added, setAdded] = useState(0);
  const fileName = initialLabel ?? "";

  const close = useCallback(() => onClose?.(), [onClose]);

  const saveSelected = useCallback(async () => {
    if (!result.recipes) return;
    setPhase("adding");
    let ok = 0;
    for (let i = 0; i < result.recipes.length; i++) {
      if (!keep[i]) continue;
      const r = result.recipes[i];
      try {
        await runAction("fresh_create_recipe", {
          name: r.name,
          ingredients: r.ingredients,
          instructions: r.instructions ?? "",
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
  const recipes = result.recipes ?? [];

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-[var(--puff-surface-deep)] shadow-2xl backdrop-blur-[18px]">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[var(--line)] px-5 py-3">
          <div className="flex min-w-0 items-center gap-2">
            <Icon path={mdiSilverwareForkKnife} size="20" className="text-[var(--mint)]" />
            <h3 className="truncate font-semibold text-[var(--text)]">
              Save recipes{fileName ? `: ${fileName}` : ""}
            </h3>
          </div>
          <button
            onClick={close}
            className="rounded p-1 text-[var(--text3)] transition hover:text-[var(--text2)]"
            aria-label="Close"
          >
            <Icon path={mdiClose} size="18" />
          </button>
        </div>

        {/* Body */}
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {phase === "done" ? (
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <Icon path={mdiCheckCircle} size="40" className="text-[var(--mint)]" />
              <p className="text-sm font-semibold text-[var(--text)]">
                Saved {added} recipe{added === 1 ? "" : "s"} to FreshTable.
              </p>
            </div>
          ) : (
            <>
              {result.method === "llm" && (
                <div className="mb-3 flex items-center gap-2 rounded-lg bg-[color-mix(in_srgb,var(--lemon)_10%,transparent)] px-3 py-2 text-xs text-[var(--lemon)]">
                  <Icon path={mdiRobotOutline} size="16" />
                  Read by the AI. Check the ingredients before saving.
                </div>
              )}
              {(result.notes ?? [])
                .filter((n) => !n.startsWith("These were read"))
                .map((n, i) => (
                  <p key={i} className="mb-2 text-xs text-[var(--text2)]">
                    {n}
                  </p>
                ))}

              {recipes.length === 0 ? (
                <p className="py-8 text-center text-sm text-[var(--text2)]">
                  No recipe found in this file.
                </p>
              ) : (
                <ul className="space-y-2">
                  {recipes.map((r, i) => (
                    <li
                      key={i}
                      className={`flex items-start gap-3 rounded-xl border p-3 transition ${
                        keep[i]
                          ? "border-[var(--mint)]/40 bg-[var(--mint)]/5"
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
                        className="mt-1 h-4 w-4 accent-[var(--mint)]"
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-[var(--text)]">
                          {r.name}
                        </p>
                        <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-[var(--text2)]">
                          {r.yield && (
                            <span className="inline-flex items-center gap-0.5">
                              <Icon path={mdiAccountGroup} size="12" />
                              {r.yield}
                            </span>
                          )}
                          {r.prep_time && (
                            <span className="inline-flex items-center gap-0.5">
                              <Icon path={mdiClockOutline} size="12" />
                              {r.prep_time}
                            </span>
                          )}
                        </p>
                        <p className="mt-1 text-xs text-[var(--text)]">
                          {snippet(r.ingredients, 140)}
                        </p>
                        {r.instructions && (
                          <p className="mt-1 text-xs text-[var(--text2)]">
                            {snippet(r.instructions, 120)}
                          </p>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between border-t border-[var(--line)] px-5 py-3">
          {phase === "done" ? (
            <Button label="Close" color="success" small roundedFull onClick={close} />
          ) : (
            <>
              <span className="text-xs text-[var(--text3)]">
                {phase === "adding"
                  ? `Saving… ${added}/${keptCount}`
                  : `${keptCount} of ${recipes.length} selected`}
              </span>
              <div className="flex gap-2">
                <Button
                  label="Cancel"
                  color="whiteDark"
                  small
                  roundedFull
                  onClick={close}
                />
                <Button
                  label={
                    phase === "adding"
                      ? "Saving…"
                      : `Save ${keptCount} recipe${keptCount === 1 ? "" : "s"}`
                  }
                  color="success"
                  small
                  roundedFull
                  disabled={phase === "adding" || keptCount === 0}
                  onClick={saveSelected}
                />
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
