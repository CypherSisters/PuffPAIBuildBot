"use client";

/**
 * A live-data app BuildBot made and has not installed: one card with what it
 * would show, where it comes from, what leaves the computer, and Drawbridge's
 * checklist - each line OK, Problem or Not sure, with no overall score. One
 * "Yes, add it" installs it AND lets it use that one service (the owner just
 * read both); "No thanks" removes the draft. Renders nothing when there is no
 * draft by this name.
 */

import { useCallback, useEffect, useState } from "react";
import Button from "@/app/_components/Button";
import FeedCardBody, { CreditLine } from "@/app/_components/Feed/FeedCardBody";
import { notifyBuildChanged } from "@/app/_lib/services/buildService";
import {
  REVIEW_WORDS,
  discardDraft,
  getDraft,
  installDraft,
  type FeedDraft,
} from "@/app/_lib/services/feedService";
import { JBM, NUNITO, Panel, SectionBar } from "./ui";

const RESULT_COLOR = { pass: "var(--mint)", fail: "var(--puff-red)", unknown: "var(--lemon)" };

export default function FeedDraftCard({
  draftId,
  onInstalled,
}: {
  draftId: string;
  onInstalled?: () => void;
}) {
  const [draft, setDraft] = useState<FeedDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDraft(draftId)
      .then((d) => { if (!cancelled) setDraft(d); })
      .catch(() => { if (!cancelled) setDraft(null); });
    return () => { cancelled = true; };
  }, [draftId]);

  const decide = useCallback(async (yes: boolean) => {
    setBusy(true);
    setProblem(null);
    try {
      if (yes) {
        await installDraft(draftId, true);
        notifyBuildChanged();
        setDone("Added. It will fetch its first data in a moment.");
        onInstalled?.();
      } else {
        await discardDraft(draftId);
        setDone("Removed. Nothing was added.");
      }
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }, [draftId, onInstalled]);

  if (!draft || draft.status !== "waiting") return null;
  const service = draft.service;

  return (
    <>
      <SectionBar title="Ready to add" />
      <Panel>
        <p style={{ margin: 0, fontFamily: NUNITO, fontWeight: 700, fontSize: 16,
                    color: "var(--text)" }}>
          {draft.name}: live data BuildBot found for your bot
        </p>
        <p style={{ margin: "4px 0 10px", fontFamily: NUNITO, fontSize: 13,
                    color: "var(--text2)" }}>
          This is what it showed when BuildBot looked once. It checks again about
          every {draft.refresh_minutes} minutes.
        </p>
        {draft.preview && (
          <FeedCardBody
            appId={draft.id}
            card={draft.preview}
            pictureNote={draft.preview_has_picture
              ? "A picture will show here once it is added."
              : undefined}
          />
        )}
        <CreditLine credit={draft.credit} />
      </Panel>

      <Panel>
        {service && (
          <p style={{ margin: 0, fontFamily: NUNITO, fontWeight: 700, fontSize: 14,
                      color: "var(--text)" }}>
            Outside website it would use: {service.name} ({service.host}
            {service.image_host ? `, pictures from ${service.image_host}` : ""})
          </p>
        )}
        <p style={{ margin: "4px 0 0", fontFamily: NUNITO, fontSize: 13,
                    color: "var(--text2)" }}>
          What leaves your computer: {draft.what_leaves}
        </p>
        <p style={{ margin: "2px 0 12px", fontFamily: NUNITO, fontSize: 13,
                    color: "var(--text2)" }}>
          License: {draft.license}
        </p>

        <p style={{ margin: "0 0 6px", fontFamily: JBM, fontSize: 11,
                    fontWeight: 700, letterSpacing: ".12em",
                    textTransform: "uppercase", color: "var(--text3)" }}>
          Safety check
        </p>
        <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
          {draft.review.map((row) => (
            <li key={row.check} className="flex gap-2 py-0.5"
                style={{ fontFamily: NUNITO, fontSize: 13 }}>
              <span style={{ minWidth: 64, fontWeight: 700,
                             color: RESULT_COLOR[row.result] }}>
                {REVIEW_WORDS[row.result]}
              </span>
              <span style={{ color: "var(--text2)" }}>
                <b style={{ color: "var(--text)" }}>{row.check}.</b> {row.why}
              </span>
            </li>
          ))}
        </ul>

        {done ? (
          <p className="mt-3 text-sm" style={{ color: "var(--mint)" }}>{done}</p>
        ) : (
          <div className="mt-4 flex flex-wrap gap-2">
            <Button label="Yes, add it" color="info" small roundedFull
                    disabled={busy} onClick={() => decide(true)} />
            <Button label="No thanks" color="lightDark" small roundedFull
                    disabled={busy} onClick={() => decide(false)} />
          </div>
        )}
        {problem && <p className="mt-2 text-sm text-[var(--puff-red)]">{problem}</p>}
      </Panel>
    </>
  );
}
