"use client";

/**
 * "Add a tool": the three ways to give the bot something new, in plain
 * words, on the Developer page and on Find more.
 *
 *  1. Find one: the Find more search (skill shelves, GitHub, tool lists).
 *  2. Paste a link: a link to one project (GitHub, Codeberg, npm, PyPI,
 *     ClawHub, skills.sh) goes to the same Find more search, which shows
 *     that project with its Add button and asks before adding it. Any other
 *     link (a tool server's address, a docs page) goes to "Add my own tool"
 *     on the Developer Brain page, which also asks first.
 *  3. For developers: the command line on the Developer page.
 *
 * Nothing is added from here: every way ends at a page that says what it is
 * and asks for a yes.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import MsIcon from "@/app/_components/MsIcon";
import { FREDOKA, NUNITO, inputStyle, panelStyle } from "@/app/dashboard/apps/_components/ui";

/** Sites whose links name one project the Find more search can show. */
const FIND_HOSTS = new Set([
  "github.com",
  "www.github.com",
  "codeberg.org",
  "www.npmjs.com",
  "npmjs.com",
  "pypi.org",
  "clawhub.ai",
  "skills.sh",
]);

/** Fired on Find more with the words (or link) to look for. */
export const FIND_EVENT = "puffpai:find";

const small: React.CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontSize: 13,
  lineHeight: 1.45,
  color: "var(--text2)",
};

const button: React.CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 700,
  fontSize: 13,
  padding: "6px 14px",
  borderRadius: 999,
  border: "1px solid var(--line)",
  background: "var(--card2)",
  color: "var(--text)",
  cursor: "pointer",
  textDecoration: "none",
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  justifySelf: "start",
};

function Way({ n, icon, title, line, children }: { n: number; icon: string; title: string; line: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gap: 6, alignContent: "start" }} data-add-way={n}>
      <div className="flex items-center" style={{ gap: 8 }}>
        <MsIcon name={icon} size={22} style={{ color: "var(--pink)" }} />
        <strong style={{ fontFamily: NUNITO, fontSize: 15, color: "var(--text)" }}>
          {n}. {title}
        </strong>
      </div>
      <p style={small}>{line}</p>
      {children}
    </div>
  );
}

export default function AddATool({ where }: { where: "developer" | "find" }) {
  const router = useRouter();
  const [link, setLink] = useState("");
  const [problem, setProblem] = useState("");

  function lookAt(words: string) {
    if (where === "find") {
      window.dispatchEvent(new CustomEvent(FIND_EVENT, { detail: words }));
      document.getElementById("find-skills")?.scrollIntoView({ behavior: "smooth" });
    } else {
      router.push(`/dashboard/add?find=${encodeURIComponent(words)}`);
    }
  }

  function paste(e: React.FormEvent) {
    e.preventDefault();
    const text = link.trim();
    let url: URL;
    try {
      url = new URL(text);
    } catch {
      setProblem("That is not a link. A link starts with https://");
      return;
    }
    if (url.protocol !== "https:" && url.protocol !== "http:") {
      setProblem("That is not a link. A link starts with https://");
      return;
    }
    setProblem("");
    if (FIND_HOSTS.has(url.hostname.toLowerCase())) {
      lookAt(text.slice(0, 300));
    } else {
      router.push(`/dashboard/developer/brain?tool=${encodeURIComponent(text.slice(0, 500))}#add-own-tool`);
    }
  }

  return (
    <section
      id="add-a-tool"
      style={{ ...panelStyle, display: "grid", gap: 14 }}
      data-add-a-tool={where}
      data-walk={where === "find" ? "add-search" : undefined}
    >
      <div style={{ display: "grid", gap: 4 }}>
        <h2 style={{ margin: 0, fontFamily: FREDOKA, fontWeight: 600, fontSize: 22, color: "var(--text)" }}>
          Add a tool
        </h2>
        <p style={small}>
          Three ways to give your bot a new skill, tool or app. Each one shows you what it is and asks you before
          anything is added.
        </p>
      </div>
      <div className="grid" style={{ gap: 18, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
        <Way
          n={1}
          icon="search"
          title="Find one"
          line="Say what you want your bot to do. It looks on GitHub, the skill shelves and the tool lists, and shows what fits."
        >
          {where === "find" ? (
            <button
              type="button"
              style={button}
              onClick={() => document.getElementById("find-skills")?.scrollIntoView({ behavior: "smooth" })}
            >
              Use the search below
            </button>
          ) : (
            <Link href="/dashboard/add" style={button}>
              Go to Find more <MsIcon name="arrow_forward" size={16} />
            </Link>
          )}
        </Way>
        <Way
          n={2}
          icon="link"
          title="Paste a link"
          line="Have a link to a GitHub project, a skill or a tool server? Paste it, and your bot shows you what it is first."
        >
          <form className="flex flex-wrap" style={{ gap: 6 }} onSubmit={paste}>
            <input
              aria-label="Paste a link to a tool"
              placeholder="https://github.com/..."
              value={link}
              maxLength={500}
              onChange={(e) => setLink(e.target.value)}
              style={{ ...inputStyle, flex: "1 1 160px", width: "auto" }}
            />
            <button type="submit" style={button} disabled={!link.trim()}>
              Look
            </button>
          </form>
          {problem && <p style={{ ...small, color: "var(--text)" }}>{problem}</p>}
        </Way>
        <Way
          n={3}
          icon="terminal"
          title="For developers: the command line"
          line="A full command line on the Developer page, for people who know commands."
        >
          {where === "developer" ? (
            <button
              type="button"
              style={button}
              onClick={() => document.getElementById("command-line")?.scrollIntoView({ behavior: "smooth" })}
            >
              Open the command line
            </button>
          ) : (
            <Link href="/dashboard/developer#command-line" style={button}>
              Open the command line <MsIcon name="arrow_forward" size={16} />
            </Link>
          )}
        </Way>
      </div>
    </section>
  );
}
