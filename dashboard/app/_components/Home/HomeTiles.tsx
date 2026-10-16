"use client";

/**
 * The home page tiles that show THIS bot: one per installed thing (GET
 * /installed), plus Chat and Find more. Build jobs is JobsHome.
 *
 * An installed app that runs opens in a new browser tab, never inside a
 * frame here.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { useState } from "react";
import Link from "next/link";
import AppLink from "@/app/dashboard/apps/_lib/AppLink";
import DesignTile, { JBM, MicroStat, NUNITO } from "@/app/_components/Widgets/DesignTile";
import type { InstalledThing } from "@/app/_lib/services/layoutService";

const KIND_WORDS: Record<InstalledThing["kind"], string> = {
  app: "APP",
  skill: "SKILL",
  tool_server: "TOOL",
  cli: "COMMAND",
  knowledge: "KNOWLEDGE",
};

const STATUS: Record<InstalledThing["status"], { word: string; color: string }> = {
  running: { word: "Running", color: "var(--mint, var(--sky))" },
  stopped: { word: "Stopped", color: "var(--lemon)" },
  ready: { word: "Ready", color: "var(--sky)" },
};

const KIND_ICON: Record<InstalledThing["kind"], string> = {
  app: "web",
  skill: "auto_awesome",
  tool_server: "build",
  cli: "terminal",
  knowledge: "menu_book",
};

const line: React.CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontWeight: 500,
  fontSize: 13,
  lineHeight: 1.35,
  color: "var(--text2)",
};

const openBtn: React.CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 800,
  fontSize: 13,
  color: "var(--bg)",
  background: "var(--sky)",
  borderRadius: 999,
  padding: "5px 14px",
  textDecoration: "none",
  whiteSpace: "nowrap",
};

export function InstalledTile({ thing, title }: { thing: InstalledThing; title: string }) {
  const st = STATUS[thing.status];
  const open = thing.kind === "app" && thing.status === "running" && thing.url;
  return (
    <DesignTile
      icon={KIND_ICON[thing.kind]}
      iconColor="var(--lav)"
      title={title}
      stat={<MicroStat>{KIND_WORDS[thing.kind]}</MicroStat>}
    >
      <div className="flex h-full min-h-0 flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 flex-none rounded-full" style={{ background: st.color }} />
          <span style={{ fontFamily: JBM, fontWeight: 600, fontSize: 11, color: "var(--text3)" }}>
            {st.word.toUpperCase()}
          </span>
        </div>
        {thing.what ? <p className="line-clamp-2" style={line}>{thing.what}</p> : null}
        <div className="mt-auto flex items-center gap-3">
          {open ? (
            <a href={thing.url} target="_blank" rel="noopener noreferrer" style={openBtn}>
              Open
            </a>
          ) : null}
          {thing.source_url ? (
            <a
              href={thing.source_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ fontFamily: JBM, fontSize: 11, color: "var(--text3)" }}
            >
              project
            </a>
          ) : null}
        </div>
      </div>
    </DesignTile>
  );
}

/** Something she put on her home page from chat or the things list
 *  (GET /things, on_home), opening its own page here. */
export function ThingTile({
  title,
  icon,
  blurb,
  href,
}: {
  title: string;
  icon: string;
  blurb: string;
  href: string;
}) {
  return (
    // An app's own page is a full page load (AppLink), like every app link.
    <AppLink href={href} className="block h-full">
      <DesignTile title={title} stat={<span aria-hidden style={{ fontSize: 20 }}>{icon}</span>}>
        <p className="line-clamp-3" style={line}>{blurb}</p>
      </DesignTile>
    </AppLink>
  );
}

export function ChatTile({
  title,
  botName,
  onAsk,
  recent,
}: {
  title: string;
  botName: string;
  onAsk: (text: string) => void;
  recent: { title: string; date: string; href: string }[];
}) {
  const [text, setText] = useState("");
  return (
    <DesignTile
      icon="chat_bubble"
      iconColor="var(--pink)"
      title={title}
      stat={
        <Link href="/dashboard/chat">
          <MicroStat>ALL CHATS →</MicroStat>
        </Link>
      }
    >
      <div className="flex h-full min-h-0 flex-col gap-2.5">
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (text.trim()) onAsk(text.trim());
          }}
        >
          <input
            aria-label={`Ask ${botName}`}
            placeholder={`Ask ${botName} anything`}
            value={text}
            maxLength={1000}
            onChange={(e) => setText(e.target.value)}
            className="min-w-0 flex-1"
            style={{
              background: "var(--card2)",
              border: "1px solid var(--line)",
              borderRadius: 999,
              padding: "9px 16px",
              color: "var(--text)",
              fontFamily: NUNITO,
              fontSize: 15,
            }}
          />
          <button
            type="submit"
            disabled={!text.trim()}
            className="cursor-pointer rounded-full border-0 transition hover:opacity-90 disabled:cursor-default disabled:opacity-50"
            style={{
              padding: "0 18px",
              background: "linear-gradient(140deg, var(--pink), var(--magenta))",
              color: "#fff",
              fontFamily: NUNITO,
              fontWeight: 700,
              fontSize: 15,
            }}
          >
            Ask
          </button>
        </form>
        <div className="flex min-h-0 flex-col overflow-y-auto">
          {recent.map((c) => (
            <Link
              key={c.href}
              href={c.href}
              className="flex items-baseline gap-3 border-t py-1.5 transition hover:opacity-80"
              style={{ borderColor: "var(--line)" }}
            >
              <span className="min-w-0 flex-1 truncate" style={{ fontFamily: NUNITO, fontWeight: 600, fontSize: 14, color: "var(--text)" }}>
                {c.title}
              </span>
              <span style={{ fontFamily: JBM, fontWeight: 600, fontSize: 10, color: "var(--text3)" }}>{c.date}</span>
            </Link>
          ))}
        </div>
      </div>
    </DesignTile>
  );
}

export function FindMoreTile({ title }: { title: string }) {
  return (
    <DesignTile icon="travel_explore" iconColor="var(--pink)" title={title} href="/dashboard/add">
      <p style={line}>Apps, skills and tools other people built, from GitHub and the skill and tool lists.</p>
    </DesignTile>
  );
}

/** A frame for a tile whose body brings no tile chrome of its own. */
export function PlainTile({
  title,
  icon,
  stat,
  children,
}: {
  title: string;
  icon: string;
  stat?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <DesignTile icon={icon} iconColor="var(--sky)" title={title} stat={stat} bodyClassName="min-h-0 flex-1 overflow-y-auto">
      {children}
    </DesignTile>
  );
}
