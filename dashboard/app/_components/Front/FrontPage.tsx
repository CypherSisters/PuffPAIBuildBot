"use client";

/**
 * The front page of one bot: set like a small newspaper about it.
 *
 * Masthead (its name, what it is for in the owner's words, a dateline), then
 * its live cards as a grid of tiles (BotAppsGrid: pictures big, numbers small,
 * each with its credit line and a way to its full page), then two columns: on the left the things to DO with it -- questions this build
 * has checked it can answer, its live cards, what it cannot do yet -- and on
 * the right what it HAS -- what comes next today, the books it knows, where to
 * add more and what leaves the computer.
 *
 * Every sentence is the backend's (GET /front); nothing here writes its own
 * copy of what the bot can do. Colours are the theme's variables only, so a
 * look the owner picks reaches this page too. Dense on purpose: no hero, no
 * waveform, no empty-state art.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import Link from "next/link";
import type { ReactNode } from "react";
import MsIcon from "../MsIcon";
import BotAppsGrid from "./BotAppsGrid";
import { FREDOKA, JBM, NUNITO } from "../Widgets/DesignTile";
import type { FrontPageData } from "@/app/_lib/services/frontService";

type Props = {
  front: FrontPageData | null;
  /** Set when GET /front failed: the page says so plainly, with a retry. */
  failed?: boolean;
  onRetry?: () => void;
  /** Send a question to chat, exactly as written. */
  onAsk: (text: string) => void;
};

const KICKER = {
  font: `700 11px ${JBM}`,
  letterSpacing: "0.14em",
  textTransform: "uppercase" as const,
  color: "var(--text3)",
  margin: 0,
};
const BODY = { font: `500 14px/1.45 ${NUNITO}`, color: "var(--text2)", margin: 0 };
const SMALL = { font: `500 12px/1.4 ${NUNITO}`, color: "var(--text3)", margin: 0 };

function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="min-w-0" style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
      <div className="mb-2 flex items-baseline justify-between gap-3">
        <h2 style={KICKER}>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

export default function FrontPage({ front, failed = false, onRetry, onAsk }: Props) {
  if (failed && !front) {
    return (
      <div className="mb-3 rounded-3xl border px-5 py-4" style={{ borderColor: "var(--line)", background: "var(--card)" }}>
        <p style={{ ...BODY, color: "var(--text)", fontWeight: 700 }}>Your bot is not answering right now.</p>
        <p style={BODY}>Start it again on the computer it runs on, then try again.</p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 cursor-pointer rounded-full border px-4 py-1.5"
            style={{ borderColor: "var(--line)", background: "var(--chip)", color: "var(--text)", font: `700 13px ${NUNITO}` }}
          >
            Try again
          </button>
        )}
      </div>
    );
  }
  if (!front) {
    return <p style={{ ...SMALL, padding: "8px 4px" }}>Reading what your bot has...</p>;
  }

  const upcoming = front.today.next;
  const said = front.today.said;

  return (
    <article
      className="mb-3 rounded-[28px] border"
      style={{
        borderColor: "var(--line)",
        background: "var(--card)",
        boxShadow: "var(--shadow)",
        padding: "16px clamp(16px, 2vw, 28px) 18px",
      }}
      aria-label={`${front.name}, the front page`}
    >
      {/* ── Masthead ── */}
      <header className="min-w-0" style={{ paddingBottom: 10 }}>
        <p style={KICKER}>{front.dateline}</p>
        <h1
          className="truncate"
          style={{ margin: "4px 0 0", font: `600 clamp(28px, 2.6vw, 40px)/1.05 ${FREDOKA}`, letterSpacing: "-0.5px", color: "var(--text)" }}
        >
          {front.name}
        </h1>
        {front.purpose && (
          <p style={{ ...BODY, fontSize: 16, color: "var(--text2)", marginTop: 4 }}>{front.purpose}</p>
        )}
        {front.hello && (
          <p style={{ ...BODY, fontStyle: "italic", marginTop: 6 }}>&ldquo;{front.hello}&rdquo;</p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Link
            href={front.talk.href}
            className="inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 transition hover:opacity-85"
            style={{ background: "var(--chip)", color: "var(--text)", font: `700 13px ${NUNITO}` }}
          >
            <MsIcon name={front.talk.voice ? "mic" : "chat"} size={17} />
            {front.talk.line}
          </Link>
        </div>
      </header>

      {/* ── What Toolsmith added (apps, the mood board) and its live cards, as
           tiles. Always rendered: a build with apps and no cards still shows
           its apps. The grid renders nothing when there is neither. ── */}
      <BotAppsGrid front={front} />

      <div className="grid gap-x-8 gap-y-4 lg:grid-cols-12">
        {/* ── Left: things to do with it ── */}
        <div className="flex min-w-0 flex-col gap-4 lg:col-span-7">
          <Section title="Try asking">
            <ol className="m-0 flex list-none flex-col gap-1 p-0">
              {front.try_asking.map((q, i) => (
                <li key={q.text}>
                  <button
                    type="button"
                    onClick={() => onAsk(q.text)}
                    className="flex w-full cursor-pointer items-baseline gap-3 rounded-xl border-0 px-2 py-1.5 text-left transition hover:opacity-85"
                    style={{ background: "transparent", color: "var(--text)" }}
                    title={q.from ? `From ${q.from}` : undefined}
                  >
                    <span style={{ font: `700 11px ${JBM}`, color: "var(--text3)", minWidth: 16 }}>{i + 1}</span>
                    <span style={{ font: `600 15px ${NUNITO}` }}>{q.text}</span>
                    {q.from && <span className="truncate" style={SMALL}>{q.from}</span>}
                  </button>
                </li>
              ))}
            </ol>
          </Section>

          {front.not_connected.length > 0 && (
            <Section title="Not connected yet">
              <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
                {front.not_connected.map((n) => (
                  <li key={n.thing} style={BODY}>{n.line}</li>
                ))}
              </ul>
            </Section>
          )}
        </div>

        {/* ── Right: what it has ── */}
        <div className="flex min-w-0 flex-col gap-4 lg:col-span-5">
          {(upcoming.length > 0 || said.length > 0) && (
            <Section title="Today">
              <ul className="m-0 flex list-none flex-col gap-1 p-0">
                {said.map((s, i) => (
                  <li key={`s${i}`} className="flex gap-2" style={BODY}>
                    <span style={{ font: `600 12px ${JBM}`, color: "var(--text3)", minWidth: 62 }}>{s.time}</span>
                    <span className="min-w-0">{s.title ? `${s.title}: ` : ""}{s.text}</span>
                  </li>
                ))}
                {upcoming.map((n, i) => (
                  <li key={`n${i}`} style={BODY}>
                    <span style={{ color: "var(--text)" }}>{n.what}</span>, {n.when}
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <Section
            title={`What ${front.name} knows`}
            aside={
              <Link href={front.knows.href} style={{ font: `700 12px ${NUNITO}`, color: "var(--text2)" }}>
                {front.knows.more_label}
              </Link>
            }
          >
            <p style={{ ...BODY, color: "var(--text)" }}>{front.knows.line}</p>
            <ul className="m-0 mt-1 flex list-none flex-col gap-1 p-0">
              {front.knows.books.map((b) => (
                <li key={b.name} style={SMALL}>
                  {b.lines.join(" ")}
                </li>
              ))}
            </ul>
          </Section>

          <Section title={front.add.label}>
            <p style={BODY}>{front.add.line}</p>
            <Link
              href={front.add.href}
              className="mt-2 inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 transition hover:opacity-85"
              style={{ background: "var(--chip)", color: "var(--text)", font: `700 13px ${NUNITO}` }}
            >
              <MsIcon name="add" size={17} />
              {front.add.label}
            </Link>
          </Section>

          <Section title={front.outside.label}>
            {front.outside.consent_line && (
              <p data-consent-line style={{ ...BODY, color: "var(--text)", fontWeight: 700 }}>
                {front.outside.consent_line}
              </p>
            )}
            <p
              style={BODY}
              data-outside-count={typeof front.outside.count === "number" ? front.outside.count : "unknown"}
            >
              {front.outside.line}
            </p>
            <Link href={front.outside.href} style={{ font: `700 12px ${NUNITO}`, color: "var(--text2)" }}>
              Open {front.outside.label.toLowerCase()}
            </Link>
          </Section>

          {[front.looks, front.keys].filter((x) => x && x.label).map((x) => (
            <Section key={x.href} title={x.label}>
              <p style={BODY}>{x.line}</p>
              <Link href={x.href} style={{ font: `700 12px ${NUNITO}`, color: "var(--text2)" }}>
                Open {x.label.toLowerCase()}
              </Link>
            </Section>
          ))}
        </div>
      </div>
    </article>
  );
}
