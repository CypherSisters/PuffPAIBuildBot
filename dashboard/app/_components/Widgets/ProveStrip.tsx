"use client";

/**
 * Proof it works — what the build's own checks found, on the page the owner
 * lands on.
 *
 * An owner built a space bot and the wizard said "Setup complete. Your bot is
 * ready" over a persona with a reserved id, no way into the menu, two knowledge
 * packs that were never downloaded, and start-up commands that did not exist on
 * her machine. Prove catches those classes; this strip is where she is told,
 * in her words, before she goes looking.
 *
 * Three voices, one strip, quietest last:
 *  - something FAILED → lead with it and the one command that fixes it;
 *  - only unfinished things → one quiet line and the way to Knowledge;
 *  - never proved against a running bot → one line naming the command.
 * Everything passed and the live half ran → this renders NOTHING. A green badge
 * for "no news" is how a dashboard teaches an owner to stop reading it.
 *
 * It claims nothing about a browser: the owner-seen line is whatever the owner
 * answered at the terminal, including "unproven".
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import MsIcon from "../MsIcon";
import { FREDOKA, JBM, NUNITO } from "./DesignTile";
import { getBuildProve, type ProveRecord } from "@/app/_lib/services/buildService";

const RUN_COMMAND = "puffpai prove";

export default function ProveStrip() {
  const [record, setRecord] = useState<ProveRecord | null>(null);

  useEffect(() => {
    let cancelled = false;
    getBuildProve().then((data) => {
      if (!cancelled) setRecord(data);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Backend silent, or nothing recorded yet before the first wizard run: say
  // nothing. A strip that appears because a fetch failed is noise.
  if (!record) return null;

  const failed = (record.checks ?? []).filter((c) => c.result === "fail");
  const liveFailed = (record.live?.checks ?? []).filter((c) => c.result === "fail");
  const problems = [...failed, ...liveFailed];
  const gaps = record.gaps ?? [];
  const provedLive = Boolean(record.live?.ran);

  if (record.ran && problems.length === 0 && gaps.length === 0 && provedLive) {
    return null;
  }

  const tone =
    problems.length > 0 ? "var(--magenta)" : gaps.length > 0 ? "var(--amber, #d9a441)" : "var(--text3)";

  return (
    <div
      className="relative mb-3 flex-none overflow-hidden rounded-[28px] border backdrop-blur-[18px]"
      style={{
        borderColor: problems.length > 0 ? tone : "var(--line)",
        background: "var(--card)",
        boxShadow: "var(--shadow)",
        padding: problems.length > 0 ? "14px 18px" : "10px 18px",
      }}
    >
      <div className="flex min-w-0 items-center gap-3">
        <MsIcon
          name={problems.length > 0 ? "error_outline" : "task_alt"}
          size={21}
          style={{ color: tone }}
        />
        <span
          className="truncate"
          style={{ font: "600 16px " + FREDOKA, color: "var(--text)" }}
        >
          Proof it works
        </span>
        <span
          className="min-w-0 flex-1 truncate"
          style={{ font: "500 13px " + NUNITO, color: "var(--text3)" }}
        >
          {problems.length > 0
            ? `${problems.length} thing${problems.length === 1 ? "" : "s"} did not check out`
            : gaps.length > 0
              ? `${gaps.length} thing${gaps.length === 1 ? "" : "s"} not finished`
              : record.summary || "Not proven yet"}
        </span>
        {gaps.length > 0 && problems.length === 0 && (
          <Link
            href="/dashboard/knowledge"
            className="flex-none whitespace-nowrap rounded-full px-3 py-1.5 transition hover:opacity-85"
            style={{
              background: "var(--chip)",
              font: "600 12px " + NUNITO,
              color: "var(--text2)",
            }}
          >
            Knowledge
          </Link>
        )}
      </div>

      {/* What failed, in plain words, with the one command that answers it. */}
      {problems.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1.5">
          {problems.map((check) => (
            <li
              key={check.id}
              style={{ font: "500 13px " + NUNITO, color: "var(--text2)" }}
            >
              {check.reason}
            </li>
          ))}
        </ul>
      )}

      {problems.length > 0 && gaps.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {gaps
            .filter((gap) => gap.fix_command)
            .map((gap) => (
              <span
                key={gap.fix_command}
                className="truncate rounded-full px-2.5 py-1"
                style={{
                  background: "var(--chip)",
                  font: "600 11px " + JBM,
                  letterSpacing: "0.04em",
                  color: "var(--text2)",
                  maxWidth: 420,
                }}
                title={gap.why}
              >
                {gap.fix_command}
              </span>
            ))}
        </div>
      )}

      {/* Never asked of a running bot. One line, the command, no claim. The
          words are the end screen's own (prove.RUN_LINE) when the build's
          checks ran, so the two pages agree (acceptance A_wedding item 18). */}
      {!provedLive && (
        <div
          className="mt-2"
          style={{ font: "500 12px " + NUNITO, color: "var(--text3)" }}
        >
          {record.ran ? "Once it is running, ask it to prove itself: " : "Not proven yet: run "}
          <span style={{ font: "600 12px " + JBM, color: "var(--text2)" }}>
            {RUN_COMMAND}
          </span>
          {record.ran ? "" : " once your bot is started."}
        </div>
      )}

      {/* The owner's own answer about the menu, never this page's guess. */}
      {provedLive && record.live?.owner === "owner_refuted" && (
        <div
          className="mt-2"
          style={{ font: "500 12px " + NUNITO, color: "var(--text3)" }}
        >
          You told {RUN_COMMAND} you could not see your bot in the left menu.
        </div>
      )}
    </div>
  );
}
