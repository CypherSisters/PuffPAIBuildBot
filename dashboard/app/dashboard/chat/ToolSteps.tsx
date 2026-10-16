"use client";

/**
 * What the bot's tool loop is doing, inside its reply bubble: one plain line
 * per step ("Looking on GitHub ...", "Asked you before running ..."), and,
 * when it wants to run something, the exact command with Yes and No. The
 * loop waits for her tap (POST /chat/approve). A command that failed for want
 * of a program, package or key shows the "Install what's missing" card, and
 * something switched off or not saved yet shows a "fix it now" card.
 *
 * Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
 */

import React, { useState } from "react";
import MsIcon from "../../_components/MsIcon";
import type { ChatMessage } from "@/app/_interfaces/api";
import { localScreen } from "@/app/_lib/localScreen";
import MissingCard from "./MissingCard";
import FixCard from "./FixCard";

const NUNITO = "var(--font-nunito), sans-serif";
const JBM = "var(--font-jbmono), monospace";

type Ask = NonNullable<ChatMessage["ask"]>;

export default function ToolSteps({
  progress,
  ask,
  opens,
  missing,
  fixes,
  onFixed,
  onAnswer,
}: {
  progress?: string[];
  ask?: Ask;
  /** Local screens a command started; each gets an Open button. */
  opens?: string[];
  /** "Install what's missing" after a command failed for want of something. */
  missing?: ChatMessage["missing"];
  /** "Fix it now" cards: something switched off or not saved yet. */
  fixes?: ChatMessage["fixes"];
  /** A fix card worked: ask her last question again. */
  onFixed?: () => void;
  onAnswer: (id: string, yes: boolean) => void;
}) {
  // The question she already tapped, by id: a second question in the same
  // reply (a skill that runs two commands) gets live buttons again.
  const [sentFor, setSentFor] = useState<string | null>(null);
  const screens = (opens || []).map((u) => localScreen(u)).filter((u): u is string => !!u);
  if ((!progress || progress.length === 0) && !ask && screens.length === 0 && !missing && !fixes?.length) return null;
  const sending = !!ask && sentFor === ask.id;

  const answer = (yes: boolean) => {
    if (!ask || sending || ask.answered !== undefined) return;
    setSentFor(ask.id);
    onAnswer(ask.id, yes);
  };

  return (
    <div className="mb-2 flex flex-col gap-1" aria-live="polite">
      {(progress || []).map((line, i) => (
        <div
          key={`${i}-${line}`}
          className="flex items-center gap-2"
          style={{ font: `600 13px ${NUNITO}`, color: "var(--text3)" }}
        >
          <MsIcon name="build" size={14} />
          <span>{line}</span>
        </div>
      ))}
      {screens.map((url) => (
        <div key={url} className="flex flex-wrap items-center gap-2" style={{ font: `600 13px ${NUNITO}`, color: "var(--text2)" }}>
          <span>It has its own screen on this computer.</span>
          <a
            href={url}
            target="_blank"
            rel="noreferrer noopener"
            style={{
              font: `700 13px ${NUNITO}`,
              color: "#fff",
              background: "linear-gradient(140deg, var(--pink), var(--magenta))",
              borderRadius: 99,
              padding: "4px 14px",
              textDecoration: "none",
            }}
          >
            Open
          </a>
        </div>
      ))}
      {missing && <MissingCard card={missing} />}
      {(fixes || []).map((f) => (
        <FixCard key={`${f.kind}-${f.service || f.key || f.name}`} fix={f} onFixed={onFixed} />
      ))}
      {ask && (
        <div
          role="group"
          aria-label={ask.title}
          className="mt-1 flex flex-col gap-2"
          style={{
            padding: "12px 14px",
            borderRadius: 16,
            background: "var(--card)",
            border: "1px solid var(--line)",
          }}
        >
          <div style={{ font: `700 15px ${NUNITO}`, color: "var(--text)" }}>{ask.title}</div>
          {ask.command && (
            <code
              style={{
                font: `500 13px ${JBM}`,
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
                padding: "8px 10px",
                borderRadius: 10,
                background: "var(--chip)",
                color: "var(--text)",
              }}
            >
              {ask.command}
            </code>
          )}
          {ask.folder && (
            <div style={{ font: `600 12px ${NUNITO}`, color: "var(--text3)" }}>
              Folder: <span style={{ fontFamily: JBM }}>{ask.folder}</span>
            </div>
          )}
          {ask.detail && (
            <div style={{ font: `600 13px ${NUNITO}`, color: "var(--text2)" }}>{ask.detail}</div>
          )}
          {ask.answered === undefined ? (
            <div className="flex gap-2">
              <button
                type="button"
                disabled={sending}
                onClick={() => answer(true)}
                className="cursor-pointer"
                style={{
                  font: `700 14px ${NUNITO}`,
                  color: "#fff",
                  background: "linear-gradient(140deg, var(--pink), var(--magenta))",
                  border: "none",
                  borderRadius: 99,
                  padding: "6px 18px",
                }}
              >
                Yes, go ahead
              </button>
              <button
                type="button"
                disabled={sending}
                onClick={() => answer(false)}
                className="cursor-pointer"
                style={{
                  font: `700 14px ${NUNITO}`,
                  color: "var(--text2)",
                  background: "var(--card2)",
                  border: "1px solid var(--line)",
                  borderRadius: 99,
                  padding: "6px 18px",
                }}
              >
                No
              </button>
            </div>
          ) : (
            <div style={{ font: `600 13px ${NUNITO}`, color: "var(--text2)" }}>
              {ask.answered ? "You said yes." : "Nothing was run."}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
