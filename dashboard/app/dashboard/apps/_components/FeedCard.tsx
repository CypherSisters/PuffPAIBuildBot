"use client";

/**
 * An installed app's live card on its generic page. Renders nothing when the
 * app has no live data (the backend answers 404), so every other app's page
 * is unchanged.
 *
 * What it shows, and in what order: the card (or the plain reason there is
 * none), the credit line - always - and the one outside service this app may
 * use, with what leaves the computer and the owner's allow/stop control. The
 * browser only ever calls this box; the box does the fetching.
 */

import { useCallback, useEffect, useState } from "react";
import Button from "@/app/_components/Button";
import FeedCardBody, { CreditLine } from "@/app/_components/Feed/FeedCardBody";
import {
  agePhrase,
  getFeed,
  pickFeedChoice,
  refreshFeed,
  setFeedAllowed,
  type FeedState,
} from "@/app/_lib/services/feedService";
import { NUNITO, Panel, SectionBar } from "./ui";

export default function FeedCard({ appId }: { appId: string }) {
  const [state, setState] = useState<FeedState | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setState(await getFeed(appId));
      setProblem(null);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "The live card did not load.");
    }
  }, [appId]);

  useEffect(() => {
    load();
  }, [load]);

  const act = useCallback(
    async (work: () => Promise<unknown>) => {
      setBusy(true);
      try {
        await work();
        await load();
      } catch (err) {
        setProblem(err instanceof Error ? err.message : "That did not work.");
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  if (!state) {
    return problem ? (
      <Panel accent="--puff-red">
        <span>{problem}</span>
      </Panel>
    ) : null;
  }

  const age = agePhrase(state.age_minutes);
  const needsYes = !state.allowed && ["not_asked", "said_no"].includes(state.service_state);

  return (
    <>
      <SectionBar title="Live">
        {state.allowed && (
          <span style={{ fontFamily: NUNITO, fontSize: 12, color: "var(--text3)" }}>
            {age ? `Updated ${age}` : "Not fetched yet"}
          </span>
        )}
        {state.allowed && (
          <Button label="Check now" color="lightDark" small roundedFull
                  disabled={busy} onClick={() => act(() => refreshFeed(appId))} />
        )}
      </SectionBar>

      <Panel>
        {state.card ? (
          <FeedCardBody appId={appId} card={state.card} />
        ) : (
          <p style={{ margin: 0, fontFamily: NUNITO, fontSize: 14,
                      color: "var(--text2)" }}>
            {state.message}
          </p>
        )}
        {state.card && state.last_error && (
          <p style={{ margin: "8px 0 0", fontFamily: NUNITO, fontSize: 12,
                      color: "var(--lemon)" }}>
            The last check did not work: {state.last_error}
          </p>
        )}
        <CreditLine credit={state.credit} />
        <p style={{ margin: "2px 0 0", fontFamily: NUNITO, fontSize: 12,
                    color: "var(--text3)" }}>
          License: {state.license}
        </p>

        {state.choices.map((choice) => (
          <label key={choice.name} className="mt-3 flex items-center gap-2 text-sm"
                 style={{ fontFamily: NUNITO, color: "var(--text2)" }}>
            {choice.label}
            <select
              value={choice.picked ?? ""}
              disabled={busy}
              onChange={(event) =>
                act(() => pickFeedChoice(appId, choice.name, event.target.value))
              }
              className="rounded-lg border border-[var(--line)] bg-[var(--card2)] px-2 py-1 text-[var(--text)]"
            >
              {choice.options.map((option) => (
                <option key={option} value={option}>{option}</option>
              ))}
            </select>
          </label>
        ))}
      </Panel>

      <Panel accent={state.allowed ? undefined : "--lemon"}>
        <p style={{ margin: 0, fontFamily: NUNITO, fontWeight: 700, fontSize: 14,
                    color: "var(--text)" }}>
          Outside website it uses: {state.service.name} ({state.service.host}
          {state.service.image_host ? `, pictures from ${state.service.image_host}` : ""})
        </p>
        <p style={{ margin: "4px 0 0", fontFamily: NUNITO, fontSize: 13,
                    color: "var(--text2)" }}>
          What leaves your computer: {state.what_leaves}
        </p>
        {!state.allowed && (
          <p style={{ margin: "6px 0 0", fontFamily: NUNITO, fontSize: 13,
                      color: "var(--text2)" }}>
            {state.service_message}
            {state.how_to_fix && !needsYes ? ` To change that, run: ${state.how_to_fix}` : ""}
          </p>
        )}
        <div className="mt-3 flex gap-2">
          {needsYes && (
            <Button label={`Yes, let it use ${state.service.name}`} color="info"
                    small roundedFull disabled={busy}
                    onClick={() => act(() => setFeedAllowed(appId, true))} />
          )}
          {state.allowed && (
            <Button label="Stop using it" color="lightDark" small roundedFull
                    disabled={busy}
                    onClick={() => act(() => setFeedAllowed(appId, false))} />
          )}
        </div>
        {problem && (
          <p className="mt-2 text-sm text-[var(--puff-red)]">{problem}</p>
        )}
      </Panel>
    </>
  );
}
