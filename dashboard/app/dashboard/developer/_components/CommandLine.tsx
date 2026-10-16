"use client";

/**
 * The command line: a real shell on this computer (PowerShell on Windows,
 * bash elsewhere), one session per open command line, so the folder and
 * anything set earlier carry over. What it prints shows as it comes.
 *
 * The shell starts on the first command (developerService: openShell), in
 * the bot's install folder with puffpai ready. Ctrl+C (or Stop) stops the
 * running command; "clear" or "cls" empties the screen; Up and Down bring
 * back earlier commands (kept in memory only, never in the browser's
 * storage). Leaving the page ends the shell; the bot also ends one left
 * alone for 30 minutes.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { JBM, NUNITO } from "@/app/dashboard/apps/_components/ui";
import {
  closeShell,
  getTerminal,
  openShell,
  readShell,
  stopCommand,
  typeLine,
  type TerminalPage,
} from "../developerService";

type Part = { kind: "in" | "out" | "bad" | "note"; text: string };

const MAX_HISTORY = 100;
const MAX_CHARS = 200_000;
const CLEAR_WORDS = new Set(["clear", "cls", "clear-host"]);

const SCREEN: React.CSSProperties = {
  background: "#0e0c16",
  color: "#e8e6f0",
  fontFamily: JBM,
  fontSize: 13,
  lineHeight: 1.5,
  borderRadius: 14,
  border: "1px solid rgba(255,255,255,.08)",
  padding: "12px 14px",
  overflowY: "auto",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
};

const COLORS: Record<Part["kind"], string> = {
  in: "#9be7a8",
  out: "#e8e6f0",
  bad: "#ff9a9a",
  note: "#b9b4cc",
};

/** Earlier commands, kept in memory for this tab only. */
const earlier: string[] = [];

function trim(parts: Part[]): Part[] {
  let total = parts.reduce((n, p) => n + p.text.length, 0);
  const out = [...parts];
  while (total > MAX_CHARS && out.length > 1) total -= out.shift()!.text.length;
  return out;
}

export default function CommandLine({ height = 260 }: { height?: number | string }) {
  const [page, setPage] = useState<TerminalPage | null>(null);
  const [parts, setParts] = useState<Part[]>([]);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [cwd, setCwd] = useState("");
  const [starting, setStarting] = useState(false);
  const sid = useRef<string | null>(null);
  const since = useRef(0);
  const polling = useRef(false);
  const alive = useRef(true);
  const at = useRef<number>(-1);
  const screen = useRef<HTMLDivElement | null>(null);
  const input = useRef<HTMLInputElement | null>(null);

  const say = useCallback((more: Part[]) => {
    setParts((old) => {
      const next = [...old];
      for (const p of more) {
        const last = next[next.length - 1];
        if (last && last.kind === p.kind && p.kind === "out") {
          next[next.length - 1] = { kind: "out", text: last.text + p.text };
        } else {
          next.push(p);
        }
      }
      return trim(next);
    });
  }, []);

  useEffect(() => {
    alive.current = true;
    void getTerminal().then((p) => {
      setPage(p);
      setParts([
        { kind: "note", text: `${p?.line || "A full command line on this computer."}\n` },
        ...(p?.off_line ? [{ kind: "bad" as const, text: `${p.off_line}\n` }] : []),
      ]);
    });
    return () => {
      alive.current = false;
      if (sid.current) void closeShell(sid.current);
      sid.current = null;
    };
  }, []);

  useEffect(() => {
    screen.current?.scrollTo({ top: screen.current.scrollHeight });
  }, [parts]);

  const poll = useCallback(async () => {
    if (polling.current) return;
    polling.current = true;
    try {
      while (alive.current && sid.current) {
        const id = sid.current;
        const got = await readShell(id, since.current);
        if (!alive.current || sid.current !== id) break;
        if (!got.ok) {
          sid.current = null;
          setBusy(false);
          say([{ kind: "note", text: `${got.message}\n` }]);
          break;
        }
        const d = got.data;
        since.current = d.next;
        if (d.text) say([{ kind: "out", text: d.text }]);
        setBusy(d.busy);
        if (d.cwd) setCwd(d.cwd);
        if (!d.alive) {
          sid.current = null;
          say([{ kind: "note", text: `${d.ended || "The command line ended."}\n` }]);
          break;
        }
        if (!d.busy && !d.text) await new Promise((r) => setTimeout(r, 2500));
      }
    } finally {
      polling.current = false;
    }
  }, [say]);

  async function ensureShell(): Promise<string | null> {
    if (sid.current) return sid.current;
    setStarting(true);
    const opened = await openShell();
    setStarting(false);
    if (!opened.ok) {
      say([{ kind: "bad", text: `${opened.message}\n` }]);
      return null;
    }
    sid.current = opened.data.id;
    since.current = 0;
    setCwd(opened.data.cwd);
    return opened.data.id;
  }

  async function run(line: string) {
    const text = line.replace(/\s+$/, "");
    setTyped("");
    at.current = -1;
    if (text.trim()) {
      const before = earlier.indexOf(text);
      if (before >= 0) earlier.splice(before, 1);
      earlier.push(text);
      if (earlier.length > MAX_HISTORY) earlier.splice(0, earlier.length - MAX_HISTORY);
    }
    if (!busy && CLEAR_WORDS.has(text.trim().toLowerCase())) {
      setParts([]);
      return;
    }
    say([{ kind: "in", text: `${busy ? "" : `${cwd || ""}> `}${text}\n` }]);
    const id = await ensureShell();
    if (!id) return;
    const sent = await typeLine(id, text);
    if (!sent.ok) {
      say([{ kind: "bad", text: `${sent.message}\n` }]);
      if (sent.code === "gone") sid.current = null;
      return;
    }
    setBusy(true);
    void poll();
  }

  async function stop() {
    if (!sid.current) return;
    const got = await stopCommand(sid.current);
    if (!got.ok) say([{ kind: "bad", text: `${got.message}\n` }]);
    void poll();
  }

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    const el = e.currentTarget;
    const selected = el.selectionStart !== el.selectionEnd;
    if (e.ctrlKey && (e.key === "c" || e.key === "C") && !selected) {
      e.preventDefault();
      if (busy) void stop();
      else {
        say([{ kind: "in", text: `${cwd || ""}> ${typed}^C\n` }]);
        setTyped("");
      }
      return;
    }
    if (e.ctrlKey && (e.key === "l" || e.key === "L")) {
      e.preventDefault();
      setParts([]);
      return;
    }
    const list = earlier;
    if (e.key === "ArrowUp" && list.length) {
      e.preventDefault();
      at.current = at.current < 0 ? list.length - 1 : Math.max(0, at.current - 1);
      setTyped(list[at.current]);
    } else if (e.key === "ArrowDown" && at.current >= 0) {
      e.preventDefault();
      at.current += 1;
      if (at.current >= list.length) {
        at.current = -1;
        setTyped("");
      } else {
        setTyped(list[at.current]);
      }
    }
  }

  const off = !!page?.off;

  return (
    <div style={{ display: "grid", gap: 8 }} data-command-line="">
      <div
        ref={screen}
        style={{ ...SCREEN, height }}
        onClick={() => {
          if (!window.getSelection()?.toString()) input.current?.focus();
        }}
        role="log"
        aria-live="polite"
        aria-label="What your commands printed"
      >
        {parts.map((p, i) => (
          <span key={i} style={{ color: COLORS[p.kind] }}>
            {p.text}
          </span>
        ))}
        <form
          className="flex items-center"
          style={{ gap: 8 }}
          onSubmit={(e) => {
            e.preventDefault();
            void run(typed);
          }}
        >
          <span style={{ color: COLORS.in, whiteSpace: "nowrap" }} aria-hidden>
            {busy ? "" : `${cwd ? `${cwd}` : ""}>`}
          </span>
          <input
            ref={input}
            aria-label="Type a command"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={onKey}
            disabled={off || starting}
            maxLength={8000}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            placeholder={starting ? "Starting..." : busy ? "Running. Ctrl+C stops it." : "puffpai where"}
            style={{
              flex: 1,
              minWidth: 80,
              background: "transparent",
              border: 0,
              outline: "none",
              color: "#fff",
              fontFamily: JBM,
              fontSize: 13,
            }}
          />
          {busy && (
            <button
              type="button"
              onClick={() => void stop()}
              style={{ fontFamily: NUNITO, fontWeight: 800, fontSize: 12, padding: "2px 12px", borderRadius: 999, border: "1px solid #ff9a9a", background: "transparent", color: "#ff9a9a", cursor: "pointer" }}
            >
              Stop
            </button>
          )}
        </form>
      </div>
      {page?.shell && (
        <p style={{ margin: 0, fontFamily: NUNITO, fontSize: 12, color: "var(--text3)" }}>
          {`Runs in ${page.shell}. Ctrl+C stops a command, clear empties the screen, Up and Down bring back earlier commands. `}
          {page.window_line}
        </p>
      )}
    </div>
  );
}
