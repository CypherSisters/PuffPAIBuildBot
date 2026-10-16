"use client";

/**
 * /dashboard/things/<id>: the page one of your bot's things gets.
 *
 * Toolsmith made its recipe when it was installed (or on the first visit):
 * a title, an icon, one plain line, 3 or 4 "Try it" requests and what it can
 * do. What the page shows depends on the kind:
 *
 *  - skill: "Try it" buttons that send the request through the chat's
 *    stream with this skill loaded and show the answer right here, with the
 *    chat's own steps and Yes/No questions (a skill's script runs after her
 *    yes), an "Ask" box that keeps the page's last few turns as a small
 *    talk (sent with each ask, like the chat), and what it can do;
 *  - tool server: each tool as a card with a form built from its input
 *    schema and a Run button that shows the result, plus "Ask"; a tool with
 *    an MCP App screen also shows that screen in a locked frame
 *    (McpAppFrame.tsx: no network, no same-origin, a yes on this page before
 *    each tool call it asks for);
 *  - app: a way into the app on its own page (its locked frame);
 *  - an app BuildBot put together (assemble/): each job it should do,
 *    working or not yet and why, Start/Stop/Open, "Use it here" (the app's
 *    own page on 127.0.0.1 in a frame) and "Put it together again";
 *  - data: a simple table per list;
 *  - book: "Ask", and the Knowledge page;
 *  - a skill whose command started a screen on this computer (127.0.0.1)
 *    gets an Open button, and pictures, sound and video it made in its own
 *    folder are shown in a small gallery and player (read-only);
 *  - feed (a live card): its newest items with their links, "Try it" and
 *    "Ask", a "Look up" box only when its service can search (one plain
 *    subject, out through its own outside service), Remove, and its own page;
 *  - a skill that needs programs or keys (read from its SKILL.md) says so in
 *    one plain line, with "Put the key in my vault" for each key it asks
 *    for: the key is kept for that skill only and handed to its commands,
 *    one at a time, after her yes. Keys its text names count too, each with
 *    a "Get a key" link. "Install what's missing" lists exactly what one yes
 *    installs (packages, programs on BuildBot's own list, sibling skills from
 *    the same publisher), each checked by Drawbridge first (src/puffpai/deps.py).
 *
 * Every page: "On my home page", "Remove", where it came from, and which of
 * the bot's jobs it covers (finding/capabilities.py).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  AppPage,
  FREDOKA,
  inputStyle,
  NUNITO,
  Panel,
  Pill,
  SectionBar,
} from "@/app/dashboard/apps/_components/ui";
import { approveAsk, fixCardForApp, streamThingAsk, type ChatAsk } from "@/app/_lib/services/chatService";
import type { FixCardData, MissingCardData } from "@/app/_interfaces/api";
import FixCard from "@/app/dashboard/chat/FixCard";
import { itemLink, safeLink } from "@/app/_lib/safeLink";
import { localScreen } from "@/app/_lib/localScreen";
import ToolSteps from "@/app/dashboard/chat/ToolSteps";
import McpAppFrame from "./McpAppFrame";
import AssembleYesCards from "@/app/_components/Assemble/AssembleYesCards";
import { currentMode } from "@/app/_lib/looks";
import { useJobRedirect } from "@/app/_lib/hooks/useJobRedirect";
import {
  appForThing,
  getAssembleReport,
  runAssemble,
  type AssembledApp,
} from "@/app/_lib/services/assembleService";
import {
  getThing,
  getThingMedia,
  lookupThing,
  thingMediaUrl,
  removeThing,
  runThingTool,
  setThingHome,
  putThingKey,
  removeThingKey,
  hostAction,
  hostLogs,
  installMissing,
  type JsonSchema,
  type ThingHost,
  type ThingMedia,
  type ThingFeedItem,
  type ThingNeeds,
  type ThingPage,
  type ThingTool,
} from "@/app/_lib/services/myThingsService";

const small: React.CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontSize: 14,
  lineHeight: 1.5,
  color: "var(--text2)",
};

const NO_ANSWER = "No answer from this computer.";

/** A model's answer as plain lines: headings bold, list marks kept, no stars. */
function Answer({ text }: { text: string }) {
  return (
    <div style={{ display: "grid", gap: 4 }}>
      {text.split("\n").map((raw, i) => {
        const line = plainText(raw.replace(/\*\*(.+?)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1"));
        if (!line.trim()) return <div key={i} style={{ height: 4 }} />;
        const head = /^#{1,4}\s+(.*)$/.exec(line);
        return (
          <p
            key={i}
            style={{
              ...small,
              color: "var(--text)",
              fontWeight: head ? 800 : 500,
              paddingLeft: /^\s*([-*]|\d+[.)])\s/.test(line) ? 10 : 0,
            }}
          >
            {head ? head[1] : line.replace(/^(\s*)[-*]\s/, "$1• ")}
          </p>
        );
      })}
    </div>
  );
}

/** One ask on this page and what came back. */
type Turn = {
  asked: string;
  answer: string;
  progress: string[];
  question?: ChatAsk & { answered?: boolean };
  opens?: string[];
  missing?: MissingCardData;
  done: boolean;
};

/** How many turns of this page's talk are kept, shown and sent with an ask. */
const KEEP_TURNS = 4;

function AskBox({ id, name, tryIt }: { id: string; name: string; tryIt: string[] }) {
  const [text, setText] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);

  /** Change the newest turn (the one being answered). */
  const setLast = (change: (t: Turn) => Turn) =>
    setTurns((prev) => (prev.length ? [...prev.slice(0, -1), change(prev[prev.length - 1])] : prev));

  function ask(said: string) {
    const q = said.trim();
    if (!q || busy) return;
    // The page's talk so far goes with the ask, like the chat's, so a
    // follow-up ("Yes, run it.") keeps its meaning.
    const history = turns
      .filter((t) => t.done && t.answer && t.answer !== NO_ANSWER)
      .map((t) => ({ user: t.asked, assistant: t.answer }));
    setBusy(true);
    setText("");
    setTurns((prev) => [...prev, { asked: q, answer: "", progress: [], done: false }].slice(-KEEP_TURNS));
    let got = "";
    let problem = "";
    streamThingAsk(
      id,
      q,
      history.slice(-KEEP_TURNS),
      (chunk) => {
        got += chunk;
        setLast((t) => ({ ...t, answer: got }));
      },
      () => {
        setBusy(false);
        setLast((t) => ({ ...t, answer: got || problem || NO_ANSWER, done: true }));
      },
      {
        onProgress: (line) => setLast((t) => ({ ...t, progress: [...t.progress, line] })),
        onAsk: (a) => setLast((t) => ({ ...t, question: a })),
        onAnswered: (askId, yes) =>
          setLast((t) => (t.question && t.question.id === askId ? { ...t, question: { ...t.question, answered: yes } } : t)),
        onOpen: (urls) => setLast((t) => ({ ...t, opens: Array.from(new Set([...(t.opens || []), ...urls])).slice(0, 3) })),
        onMissing: (card) => setLast((t) => ({ ...t, missing: card })),
        onReplace: (whole) => {
          got = whole;
          setLast((t) => ({ ...t, answer: whole }));
        },
        onProblem: (message) => {
          problem = message;
        },
      },
    );
  }

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {tryIt.length > 0 && (
        <>
          <SectionBar title="Try it" />
          <div className="flex flex-wrap gap-2">
            {tryIt.map((t) => (
              <Pill key={t} tone="accent" disabled={busy} onClick={() => ask(t)}>
                {t}
              </Pill>
            ))}
          </div>
        </>
      )}
      <SectionBar title={`Ask ${name}`} />
      {turns.length > 0 && (
        <Panel>
          <div style={{ display: "grid", gap: 14 }}>
            {turns.map((t, i) => {
              const last = i === turns.length - 1;
              return (
                <div key={i}>
                  <p style={{ ...small, fontWeight: 700, marginBottom: 8 }}>{t.asked}</p>
                  <ToolSteps
                    progress={t.progress}
                    ask={t.question}
                    opens={t.opens}
                    missing={t.missing}
                    onAnswer={(askId, yes) => {
                      void approveAsk(askId, yes).then((ok) =>
                        setTurns((prev) =>
                          prev.map((x) =>
                            x.question && x.question.id === askId
                              ? { ...x, question: { ...x.question, answered: ok ? yes : false } }
                              : x,
                          ),
                        ),
                      );
                    }}
                  />
                  <div role={last ? "status" : undefined} aria-live={last ? "polite" : undefined}>
                    {t.answer ? (
                      <Answer text={t.answer} />
                    ) : last && busy && !t.question ? (
                      <p style={small}>Thinking... this can take a minute.</p>
                    ) : null}
                  </div>
                </div>
              );
            })}
          </div>
          {!busy && (
            <button
              type="button"
              onClick={() => setTurns([])}
              style={{ ...small, fontSize: 12, marginTop: 10, background: "none", border: 0, padding: 0, color: "var(--sky)", cursor: "pointer" }}
            >
              Start over
            </button>
          )}
        </Panel>
      )}
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          ask(text);
        }}
      >
        <input
          aria-label={`Ask ${name}`}
          placeholder={`Ask ${name} anything`}
          value={text}
          maxLength={1000}
          onChange={(e) => setText(e.target.value)}
          style={{ ...inputStyle, flex: "1 1 280px" }}
        />
        <Pill type="submit" tone="accent" disabled={busy || !text.trim()}>
          Ask
        </Pill>
      </form>
    </div>
  );
}

function kindOf(s: JsonSchema): string {
  const t = Array.isArray(s.type) ? s.type.find((x) => x !== "null") : s.type;
  return t || "string";
}

/** Her typed value, as the schema wants it (a number for a number field). */
function typed(s: JsonSchema, raw: string | boolean): unknown {
  if (typeof raw === "boolean") return raw;
  if (raw === "") return undefined;
  const k = kindOf(s);
  if (k === "integer" || k === "number") {
    const n = Number(raw);
    return Number.isFinite(n) ? n : raw;
  }
  if (k === "array" || k === "object") {
    try {
      return JSON.parse(raw);
    } catch {
      return k === "array" ? raw.split(",").map((x) => x.trim()).filter(Boolean) : raw;
    }
  }
  return raw;
}

/** "total_count" or "projectKey" as plain words: "Total count", "Project key". */
function keyWords(key: string): string {
  const words = key
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim()
    .toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** No long dashes on screen, whatever a tool sends back. */
function plainText(value: unknown): string {
  return String(value).replace(/\s*[\u2014\u2013]\s*/g, ", ");
}

const TITLE_KEYS = ["name", "title", "label", "headline"];

/** One value of a tool's answer: words, a link, or a nested list. */
function Value({ value, depth }: { value: unknown; depth: number }) {
  if (value === null || value === undefined || value === "") return <span>none</span>;
  if (typeof value === "boolean") return <span>{value ? "yes" : "no"}</span>;
  if (typeof value !== "object") {
    const text = plainText(value);
    return /^https?:\/\/\S+$/.test(text) ? (
      <a href={text} target="_blank" rel="noreferrer noopener" style={{ color: "var(--sky)", wordBreak: "break-all" }}>
        {text}
      </a>
    ) : (
      <span>{text}</span>
    );
  }
  return <Pretty data={value} depth={depth + 1} />;
}

/** A tool's answer in plain words: a list of cards for a list of things,
 *  "Key: value" lines for one thing. */
function Pretty({ data, depth = 0 }: { data: unknown; depth?: number }) {
  const indent = depth > 0 ? { paddingLeft: 12, borderLeft: "2px solid var(--line)" } : {};
  if (Array.isArray(data)) {
    if (data.length === 0) return <p style={small}>Nothing found.</p>;
    if (data.every((x) => typeof x !== "object" || x === null)) {
      return (
        <ul style={{ margin: 0, paddingLeft: 18, ...small, color: "var(--text)" }}>
          {data.map((x, i) => (
            <li key={i}>
              <Value value={x} depth={depth} />
            </li>
          ))}
        </ul>
      );
    }
    return (
      <div style={{ display: "grid", gap: 8, ...indent }}>
        {data.map((x, i) => (
          <div key={i} style={{ padding: "8px 10px", borderRadius: 10, background: "var(--card)", border: "1px solid var(--line)" }}>
            <Pretty data={x} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }
  if (data && typeof data === "object") {
    const entries = Object.entries(data as Record<string, unknown>);
    const titleKey = TITLE_KEYS.find((k) => typeof (data as Record<string, unknown>)[k] === "string");
    return (
      <div style={{ display: "grid", gap: 3, ...(depth > 1 ? indent : {}) }}>
        {titleKey && (
          <p style={{ ...small, fontWeight: 800, color: "var(--text)" }}>
            {plainText((data as Record<string, unknown>)[titleKey])}
          </p>
        )}
        {entries
          .filter(([k]) => k !== titleKey)
          .map(([k, v]) => (
            <div key={k} style={{ ...small, color: "var(--text)" }}>
              <span style={{ fontWeight: 700 }}>{keyWords(k)}: </span>
              <Value value={v} depth={depth} />
            </div>
          ))}
      </div>
    );
  }
  return <Answer text={plainText(data)} />;
}

/** The Run button's answer: plain words first, the raw text one tap away. */
function ToolResult({ text }: { text: string }) {
  const [raw, setRaw] = useState(false);
  let data: unknown = undefined;
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object") data = parsed;
  } catch {
    data = undefined;
  }
  return (
    <div
      role="status"
      style={{
        marginTop: 10,
        background: "var(--card2)",
        border: "1px solid var(--line)",
        borderRadius: 12,
        padding: 12,
        maxHeight: 420,
        overflow: "auto",
      }}
    >
      {raw ? (
        <pre style={{ ...small, margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", color: "var(--text)" }}>
          {text}
        </pre>
      ) : data !== undefined ? (
        <Pretty data={data} />
      ) : (
        <Answer text={plainText(text)} />
      )}
      {data !== undefined && (
        <button
          type="button"
          onClick={() => setRaw(!raw)}
          style={{ ...small, fontSize: 12, marginTop: 8, background: "none", border: 0, padding: 0, color: "var(--sky)", cursor: "pointer" }}
        >
          {raw ? "Show it in plain words" : "Show raw"}
        </button>
      )}
    </div>
  );
}

function ToolCard({ id, tool }: { id: string; tool: ThingTool }) {
  const props = tool.input_schema?.properties || {};
  const required = new Set(tool.input_schema?.required || []);
  const [values, setValues] = useState<Record<string, string | boolean>>({});
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const [screen, setScreen] = useState<{ html: string; args: Record<string, unknown>; result: unknown } | null>(null);

  async function run() {
    setBusy(true);
    setResult("");
    setScreen(null);
    const args: Record<string, unknown> = {};
    for (const [key, schema] of Object.entries(props)) {
      const v = typed(schema, values[key] ?? "");
      if (v !== undefined) args[key] = v;
    }
    const reply = await runThingTool(id, tool.name, args);
    setBusy(false);
    setResult(
      reply.ok ? reply.data.result || "" : reply.status === 0 ? NO_ANSWER : reply.data.message || "That did not work.",
    );
    if (reply.ok && reply.data.ui && typeof reply.data.ui.html === "string") {
      setScreen({ html: reply.data.ui.html, args, result: reply.data.app_result });
    }
  }

  return (
    <Panel>
      <h3 style={{ margin: 0, fontFamily: FREDOKA, fontWeight: 600, fontSize: 19, color: "var(--text)" }}>
        {tool.title}
      </h3>
      {tool.description && <p style={{ ...small, marginTop: 4 }}>{tool.description}</p>}
      <form
        style={{ display: "grid", gap: 8, marginTop: 10 }}
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
      >
        {Object.entries(props).map(([key, schema]) => {
          const label = `${key.replace(/_/g, " ")}${required.has(key) ? " (needed)" : ""}`;
          const k = kindOf(schema);
          const set = (v: string | boolean) => setValues((prev) => ({ ...prev, [key]: v }));
          return (
            <label key={key} style={{ display: "grid", gap: 3 }}>
              <span style={{ ...small, fontWeight: 700, color: "var(--text)" }}>{label}</span>
              {Array.isArray(schema.enum) ? (
                <select
                  value={String(values[key] ?? "")}
                  onChange={(e) => set(e.target.value)}
                  style={inputStyle}
                >
                  <option value="">Any</option>
                  {schema.enum.map((opt) => (
                    <option key={String(opt)} value={String(opt)}>
                      {String(opt)}
                    </option>
                  ))}
                </select>
              ) : k === "boolean" ? (
                <input
                  type="checkbox"
                  checked={Boolean(values[key])}
                  onChange={(e) => set(e.target.checked)}
                  style={{ width: 20, height: 20 }}
                />
              ) : (
                <input
                  type={k === "integer" || k === "number" ? "number" : "text"}
                  min={schema.minimum}
                  max={schema.maximum}
                  placeholder={
                    schema.default !== undefined ? `For example ${String(schema.default)}` : schema.description || ""
                  }
                  required={required.has(key)}
                  value={String(values[key] ?? "")}
                  onChange={(e) => set(e.target.value)}
                  style={inputStyle}
                />
              )}
              {schema.description && k !== "string" && <span style={{ ...small, fontSize: 12 }}>{schema.description}</span>}
            </label>
          );
        })}
        <div>
          <Pill type="submit" tone="accent" disabled={busy}>
            {busy ? "Running..." : "Run"}
          </Pill>
        </div>
      </form>
      {screen && (
        <McpAppFrame thingId={id} title={tool.title} html={screen.html} tool={tool} args={screen.args} result={screen.result} />
      )}
      {result && <ToolResult text={result} />}
    </Panel>
  );
}

/** What a skill needs: missing programs, and its keys with a way to put
 *  each one in the vault. The key box is a password field; the value is
 *  sent once and never shown again. */
/** An app that runs on this computer, as its authors built it: where it
 *  opens (its own page, 127.0.0.1 only), Start, Stop, Restart, its log. */
function HostPanel({ id, host, onChange }: { id: string; host: ThingHost; onChange: (h: ThingHost) => void }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const [log, setLog] = useState<{ which: "run" | "install"; text: string } | null>(null);
  const url = localScreen(host.url);

  async function act(action: "start" | "stop" | "restart") {
    setBusy(true);
    const reply = await hostAction(id, action);
    setBusy(false);
    setSaid(reply.status === 0 ? NO_ANSWER : plainText(reply.data.message || ""));
    if (reply.data.host) onChange(reply.data.host);
  }

  async function showLog(which: "run" | "install") {
    const reply = await hostLogs(id, which);
    setLog({ which, text: reply.status === 0 ? NO_ANSWER : reply.data.text || "Nothing in it yet." });
  }

  const state =
    host.state === "running"
      ? "It is running."
      : host.state === "starting"
        ? "It is starting."
        : host.state === "set_up"
          ? "It is set up on this computer."
          : host.state === "missing"
            ? "It is not set up on this computer any more."
            : "It is stopped.";
  return (
    <Panel accent={host.state === "running" ? "--mint" : undefined}>
      <SectionBar title="On this computer" />
      <p style={{ ...small, marginTop: 6 }}>
        {plainText(host.how_words ? `${host.how_words[0].toUpperCase()}${host.how_words.slice(1)}. ` : "")}
        {state} {host.why ? plainText(host.why) : ""}
      </p>
      {host.note && <p style={{ ...small, fontSize: 12, color: "var(--text3)" }}>{plainText(host.note)}</p>}
      <div className="flex flex-wrap items-center gap-2" style={{ marginTop: 10 }}>
        {url && (
          <a href={url} target="_blank" rel="noreferrer noopener" style={{ ...small, fontWeight: 700, color: "var(--sky)" }}>
            Open it
          </a>
        )}
        {host.can_run && host.state !== "running" && host.state !== "starting" && (
          <Pill tone="accent" disabled={busy} onClick={() => void act("start")}>
            Start
          </Pill>
        )}
        {host.can_run && (host.state === "running" || host.state === "starting") && (
          <>
            <Pill disabled={busy} onClick={() => void act("stop")}>
              Stop
            </Pill>
            <Pill disabled={busy} onClick={() => void act("restart")}>
              Restart
            </Pill>
          </>
        )}
        {host.can_run && <Pill onClick={() => void showLog("run")}>Its log</Pill>}
        <Pill onClick={() => void showLog("install")}>Its install log</Pill>
      </div>
      {said && <p style={{ ...small, marginTop: 8 }}>{said}</p>}
      {host.tools && host.tools.length > 0 && (
        <p style={{ ...small, marginTop: 8 }}>Its commands your bot can run: {host.tools.join(", ")}</p>
      )}
      {host.keys && host.keys.length > 0 && (
        <p style={{ ...small, marginTop: 8 }}>
          It asks for these keys: {host.keys.join(", ")}. A key you keep below goes only to this app, when it
          starts. It gets no other key.
        </p>
      )}
      {host.commands && host.commands.length > 0 && (
        <details style={{ marginTop: 8 }}>
          <summary style={{ ...small, cursor: "pointer" }}>The commands it runs</summary>
          <pre style={{ ...small, fontSize: 12, whiteSpace: "pre-wrap", marginTop: 6 }}>{host.commands.join("\n")}</pre>
        </details>
      )}
      {log && (
        <pre
          style={{
            ...small,
            fontSize: 12,
            whiteSpace: "pre-wrap",
            maxHeight: 320,
            overflow: "auto",
            marginTop: 8,
            padding: 8,
            background: "var(--card2)",
            borderRadius: 8,
          }}
        >
          {plainText(log.text)}
        </pre>
      )}
    </Panel>
  );
}

/** An app BuildBot put together from parts: is each job working, why not
 *  yet, and a way to start it, open it, use it right here and put it
 *  together again. Start/Stop go through the same host route as any app on
 *  this computer; the frame shows only the app's own 127.0.0.1 page. */
function AssembledPanel({
  id,
  app,
  host,
  onHost,
  onAgain,
}: {
  id: string;
  app: AssembledApp;
  host?: ThingHost;
  onHost: (h: ThingHost) => void;
  onAgain: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const [here, setHere] = useState(false);
  const [log, setLog] = useState("");
  const [fix, setFix] = useState<FixCardData | null>(null);
  const [frameKey, setFrameKey] = useState(0);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const url = localScreen(host?.url);
  const running = host?.state === "running" || host?.state === "starting";
  const working = app.caps.filter((c) => c.state === "working").length;

  // The app's page asks for a card (a switch to turn on, a key): only from
  // its own frame and address, only a kind and an id; the card's words come
  // from BuildBot (POST /fix/card) for a switch this app's sources use.
  useEffect(() => {
    if (!here || !url) return;
    let origin = "";
    try {
      origin = new URL(url).origin;
    } catch {
      return;
    }
    const onMessage = (ev: MessageEvent) => {
      if (ev.origin !== origin || !frameRef.current || ev.source !== frameRef.current.contentWindow) return;
      const d = ev.data;
      if (!d || typeof d !== "object" || d.puffpai !== "fix") return;
      const kind = String(d.kind || "");
      if (kind !== "switch_on" && kind !== "key") return;
      void fixCardForApp(app.id, {
        kind,
        service: String(d.service || "").slice(0, 64),
        name: String(d.name || "").slice(0, 40),
      }).then((card) => {
        setFix(card);
        if (!card) setSaid("There is nothing to switch on for that now.");
      });
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [here, url, app.id]);

  async function act(action: "start" | "stop") {
    setBusy(true);
    const reply = await hostAction(id, action);
    setBusy(false);
    setSaid(reply.status === 0 ? NO_ANSWER : plainText(reply.data.message || ""));
    if (reply.data.host) onHost(reply.data.host);
    if (action === "stop") setHere(false);
  }

  async function again() {
    setBusy(true);
    const reply = await runAssemble(id);
    setBusy(false);
    setSaid(
      reply.status === 0
        ? NO_ANSWER
        : plainText(reply.data.message || (reply.ok ? "BuildBot is putting it together again. The agent team page shows each step." : "That did not start.")),
    );
    if (reply.ok) onAgain();
  }

  const frameUrl = url ? `${url}${url.includes("?") ? "&" : "?"}theme=${currentMode()}` : "";
  return (
    <Panel accent={app.caps.length > 0 && working === app.caps.length ? "--mint" : "--lemon"}>
      <SectionBar title="Assembled app" />
      <p style={{ ...small, marginTop: 6 }}>
        {plainText(app.title)}: {working} of {app.caps.length} {app.caps.length === 1 ? "job" : "jobs"} working.
        {host ? (running ? " It is running." : " It is stopped.") : ""}
        {app.checked_at ? ` Last checked ${new Date(app.checked_at).toLocaleString()}.` : ""}
      </p>
      {app.caps.length > 0 && (
        <ul style={{ margin: "8px 0 0", padding: 0, listStyle: "none", display: "grid", gap: 4 }}>
          {app.caps.map((c) => (
            <li key={c.cap} className="flex flex-wrap items-baseline gap-2" style={{ ...small, color: "var(--text)" }}>
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 800,
                  textTransform: "uppercase",
                  letterSpacing: 0.4,
                  color: c.state === "working" ? "var(--mint)" : "var(--lemon)",
                  minWidth: 64,
                }}
              >
                {c.state === "working" ? "Working" : "Not yet"}
              </span>
              <span style={{ fontWeight: 700 }}>{plainText(c.name)}</span>
              {c.state !== "working" && c.reason && <span style={{ color: "var(--text2)" }}>{plainText(c.reason)}</span>}
              {c.state !== "working" && !c.card && /found nothing|nothing was found/i.test(c.reason || "") && (
                <Link
                  href={`/dashboard/add?find=${encodeURIComponent(c.name)}&cap=${encodeURIComponent(c.cap)}#find-skills`}
                  style={{ fontWeight: 700, color: "var(--sky)" }}
                >
                  Find more
                </Link>
              )}
              {c.state !== "working" && c.card && (
                <div style={{ flexBasis: "100%", marginTop: 4 }}>
                  <FixCard fix={c.card} onFixed={onAgain} />
                </div>
              )}
              {c.state === "working" && c.ask && (
                <span style={{ color: "var(--text3)", fontSize: 12 }}>Proved with: &quot;{plainText(c.ask)}&quot;</span>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2" style={{ marginTop: 10 }}>
        {host?.can_run && !running && (
          <Pill tone="accent" disabled={busy} onClick={() => void act("start")}>
            Start
          </Pill>
        )}
        {host?.can_run && running && (
          <Pill disabled={busy} onClick={() => void act("stop")}>
            Stop
          </Pill>
        )}
        {url && running && (
          <Pill tone={here ? "active" : "quiet"} onClick={() => setHere(!here)}>
            {here ? "Close it here" : "Use it here"}
          </Pill>
        )}
        {url && (
          <a href={url} target="_blank" rel="noreferrer noopener" style={{ ...small, fontWeight: 700, color: "var(--sky)" }}>
            Open
          </a>
        )}
        <Pill disabled={busy} onClick={() => void again()}>
          Put it together again
        </Pill>
        {host?.can_run && (
          <Pill
            onClick={() =>
              void hostLogs(id, "run").then((r) => setLog(r.status === 0 ? NO_ANSWER : r.data.text || "Nothing in it yet."))
            }
          >
            Its log
          </Pill>
        )}
      </div>
      {said && <p style={{ ...small, marginTop: 8 }}>{said}</p>}
      {log && (
        <pre
          style={{
            ...small,
            fontSize: 12,
            whiteSpace: "pre-wrap",
            maxHeight: 320,
            overflow: "auto",
            marginTop: 8,
            padding: 8,
            background: "var(--card2)",
            borderRadius: 8,
          }}
        >
          {plainText(log)}
        </pre>
      )}
      {fix && (
        <div style={{ marginTop: 10 }}>
          <FixCard
            fix={fix}
            onFixed={() => {
              setFix(null);
              setFrameKey((k) => k + 1);
            }}
          />
        </div>
      )}
      {here && frameUrl && (
        <iframe
          key={frameKey}
          ref={frameRef}
          src={frameUrl}
          title={plainText(app.title)}
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
          allow="autoplay; fullscreen; picture-in-picture"
          referrerPolicy="no-referrer"
          style={{
            width: "100%",
            height: "min(78vh, 900px)",
            marginTop: 10,
            border: "1px solid var(--line)",
            borderRadius: 14,
            background: "var(--card2)",
          }}
        />
      )}
    </Panel>
  );
}

function NeedsPanel({ id, needs, onChange }: { id: string; needs: ThingNeeds; onChange: (n: ThingNeeds) => void }) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [said, setSaid] = useState("");
  const [busy, setBusy] = useState("");

  async function keep(name: string) {
    const value = (values[name] || "").trim();
    if (!value) return;
    setBusy(name);
    const reply = await putThingKey(id, name, value);
    setBusy("");
    setValues((prev) => ({ ...prev, [name]: "" }));
    setSaid(reply.status === 0 ? NO_ANSWER : reply.data.message || "That did not work.");
    if (reply.ok && reply.data.needs) onChange(reply.data.needs);
  }

  async function forget(name: string) {
    setBusy(name);
    const reply = await removeThingKey(id, name);
    setBusy("");
    setSaid(reply.status === 0 ? NO_ANSWER : reply.data.message || "That did not work.");
    if (reply.ok && reply.data.needs) onChange(reply.data.needs);
  }

  /** The one yes: installs exactly the steps listed above it. */
  async function install(plan: string) {
    setBusy("install");
    setSaid("Installing... this can take a few minutes.");
    const reply = await installMissing(plan);
    setBusy("");
    const lines = reply.data.lines && reply.data.lines.length ? reply.data.lines.join(" ") : "";
    setSaid(reply.status === 0 ? NO_ANSWER : lines || reply.data.message || "That did not work.");
    if (reply.data.needs) onChange(reply.data.needs);
  }

  const plan = needs.install;
  const canInstall = !!plan && !!plan.plan && plan.steps.length > 0;

  return (
    <Panel accent={needs.programs_missing.length || canInstall || needs.keys.some((k) => !k.set) ? "--lemon" : undefined}>
      <SectionBar title="What it needs" />
      {needs.line && <p style={{ ...small, marginTop: 6 }}>{needs.line}</p>}
      {canInstall && (
        <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
          <p style={{ ...small, fontWeight: 700, color: "var(--text)" }}>One yes installs:</p>
          <ul style={{ ...small, margin: 0, paddingLeft: 18 }}>
            {plan!.steps.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          <div>
            <Pill tone="accent" disabled={busy === "install"} onClick={() => void install(plan!.plan)}>
              Install what's missing
            </Pill>
          </div>
        </div>
      )}
      {plan && plan.manual.length > 0 && (
        <div style={{ marginTop: 8, display: "grid", gap: 4 }}>
          {plan.manual.map((m) => (
            <p key={m} style={{ ...small, fontSize: 13 }}>
              {m}
            </p>
          ))}
        </div>
      )}
      {needs.keys.map((k) => (
        <form
          key={k.name}
          className="flex flex-wrap items-center gap-2"
          style={{ marginTop: 10 }}
          onSubmit={(e) => {
            e.preventDefault();
            void keep(k.name);
          }}
        >
          <span style={{ ...small, fontWeight: 700, color: "var(--text)", minWidth: 160 }}>
            {k.name}
            {k.set ? " (in your vault)" : ""}
          </span>
          {k.set ? (
            <Pill disabled={busy === k.name} onClick={() => void forget(k.name)}>
              Take it out of my vault
            </Pill>
          ) : (
            <>
              <input
                type="password"
                autoComplete="off"
                spellCheck={false}
                aria-label={`Your key ${k.name}`}
                placeholder={k.prompt || `Your key ${k.name}`}
                value={values[k.name] || ""}
                maxLength={4096}
                onChange={(e) => setValues((prev) => ({ ...prev, [k.name]: e.target.value }))}
                style={{ ...inputStyle, flex: "1 1 240px" }}
              />
              <Pill type="submit" tone="accent" disabled={busy === k.name || !(values[k.name] || "").trim()}>
                Put the key in my vault
              </Pill>
            </>
          )}
          {!k.set && safeLink(k.get_url) && (
            <a
              href={safeLink(k.get_url)}
              target="_blank"
              rel="noreferrer noopener"
              style={{ ...small, fontSize: 13, fontWeight: 700, color: "var(--sky)" }}
            >
              Get a key
            </a>
          )}
          {!k.set && k.from === "text" && (
            <p style={{ ...small, fontSize: 12, width: "100%" }}>Its text names this key; its settings do not.</p>
          )}
          {k.help && !k.set && <p style={{ ...small, fontSize: 12, width: "100%" }}>{plainText(k.help)}</p>}
        </form>
      ))}
      {said && (
        <p role="status" style={{ ...small, marginTop: 8 }}>
          {said}
        </p>
      )}
    </Panel>
  );
}

/** Pictures, sound and video the thing made in its own folder. */
/** A live card that can search: one plain subject, looked up on its own service. */
function FeedLookup({ id, name }: { id: string; name: string }) {
  const [topic, setTopic] = useState("");
  const [busy, setBusy] = useState(false);
  const [items, setItems] = useState<ThingFeedItem[] | null>(null);
  const [line, setLine] = useState("");

  async function look() {
    setBusy(true);
    setLine("");
    const reply = await lookupThing(id, topic.trim());
    setBusy(false);
    if (!reply.ok) {
      setItems(null);
      const fix = reply.data.how_to_fix ? ` To switch it on: ${reply.data.how_to_fix}` : "";
      setLine(reply.status === 0 ? NO_ANSWER : `${reply.data.message || "That did not work."}${fix}`);
      return;
    }
    setItems(reply.data.items || []);
    setLine(reply.data.message || "");
  }

  return (
    <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (topic.trim()) void look();
        }}
      >
        <input
          aria-label={`Look something up on ${name}`}
          placeholder={`Look something up on ${name}`}
          value={topic}
          maxLength={80}
          onChange={(e) => setTopic(e.target.value)}
          style={{ ...inputStyle, flex: "1 1 240px" }}
        />
        <Pill type="submit" tone="accent" disabled={busy || !topic.trim()}>
          Look up
        </Pill>
      </form>
      <p style={{ ...small, fontSize: 12 }}>Only a plain subject leaves this computer.</p>
      {items && items.length > 0 && (
        <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: 6 }}>
          {items.map((it, i) => (
            <li key={i} style={{ ...small, color: "var(--text)" }}>
              <span style={{ fontWeight: 700 }}>{plainText(it.title || "")}</span>
              {it.detail ? <span style={{ color: "var(--text2)" }}>{` (${plainText(it.detail)})`}</span> : null}
              {itemLink(it.link) && (
                <a
                  href={itemLink(it.link)}
                  target="_blank"
                  rel="noreferrer noopener"
                  style={{ marginLeft: 8, fontSize: 12, color: "var(--sky)" }}
                >
                  Open
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
      {line && <p style={small}>{line}</p>}
    </div>
  );
}

function MediaShelf({ id }: { id: string }) {
  const [files, setFiles] = useState<ThingMedia[]>([]);
  useEffect(() => {
    let alive = true;
    void getThingMedia(id).then((reply) => {
      if (alive && reply.ok) setFiles(reply.data.files || []);
    });
    return () => {
      alive = false;
    };
  }, [id]);
  if (files.length === 0) return null;
  return (
    <>
      <SectionBar title="What it made" />
      <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
        {files.map((f) => {
          const src = thingMediaUrl(id, f.name);
          return (
            <Panel key={f.name}>
              {f.kind === "image" ? (
                // eslint-disable-next-line @next/next/no-img-element -- a local file
                <img src={src} alt={f.name} style={{ width: "100%", borderRadius: 10 }} />
              ) : f.kind === "audio" ? (
                <audio controls preload="none" src={src} style={{ width: "100%" }} />
              ) : (
                <video controls preload="none" src={src} style={{ width: "100%", borderRadius: 10 }} />
              )}
              <p style={{ ...small, fontSize: 12, marginTop: 6, wordBreak: "break-all" }}>{f.name}</p>
            </Panel>
          );
        })}
      </div>
    </>
  );
}

export default function ThingPageView() {
  const params = useParams();
  const router = useRouter();
  const id = String((params as { id?: string })?.id || "");
  // A live card a job uses lives on that job's page now (one build foundation).
  useJobRedirect(id.startsWith("feed-") ? id : "");
  const [page, setPage] = useState<ThingPage | null>(null);
  const [problem, setProblem] = useState("");
  const [said, setSaid] = useState("");
  const [asking, setAsking] = useState(false);
  const [assembled, setAssembled] = useState<AssembledApp | null>(null);

  const loadAssembled = useCallback(async () => {
    const reply = await getAssembleReport();
    setAssembled(reply.ok ? appForThing(reply.data, id) : null);
  }, [id]);

  useEffect(() => {
    void loadAssembled();
  }, [loadAssembled]);

  const load = useCallback(async () => {
    const reply = await getThing(id);
    const moved = reply.ok ? reply.data.redirect : "";
    if (moved && moved.startsWith("/dashboard")) {
      // An old address of something a job uses: it lives on that job's page.
      router.replace(moved);
      return null;
    }
    if (reply.ok) {
      setPage(reply.data);
      setProblem("");
    } else {
      setProblem(reply.status === 0 ? NO_ANSWER : reply.data.message || "There is nothing by that name on your bot.");
    }
    return reply.ok ? reply.data : null;
  }, [id, router]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const first = await load();
      // Toolsmith is still writing friendlier words for this page: look once more.
      if (first?.improving) {
        await new Promise((r) => setTimeout(r, 20_000));
        if (alive) void load();
      }
    })();
    return () => {
      alive = false;
    };
  }, [load]);

  async function toggleHome() {
    if (!page) return;
    const reply = await setThingHome(page.id, !page.on_home);
    if (reply.ok) setPage({ ...page, on_home: !page.on_home });
    setSaid(reply.data.message || "");
  }

  async function remove() {
    if (!page) return;
    const reply = await removeThing(page.id);
    setAsking(false);
    if (reply.ok) {
      router.push("/dashboard");
      return;
    }
    setSaid(reply.status === 0 ? NO_ANSWER : reply.data.message || "It could not be taken out.");
  }

  if (problem && !page) {
    return (
      <AppPage>
        <Panel accent="--lemon">
          <p style={small}>{problem}</p>
        </Panel>
        <Link href="/dashboard" style={{ ...small, color: "var(--sky)" }}>
          Back to your home page
        </Link>
      </AppPage>
    );
  }
  if (!page) {
    return (
      <AppPage>
        <p style={small}>Opening...</p>
      </AppPage>
    );
  }

  const r = page.recipe;
  return (
    <AppPage>
      <div className="flex flex-wrap items-start gap-4">
        <div aria-hidden style={{ fontSize: 46, lineHeight: 1 }}>
          {page.icon}
        </div>
        <div className="min-w-0" style={{ flex: "1 1 320px" }}>
          <h1
            style={{
              margin: 0,
              fontFamily: FREDOKA,
              fontWeight: 600,
              fontSize: "clamp(26px,2.4vw,40px)",
              letterSpacing: "-.8px",
              lineHeight: 1.05,
              color: "var(--text)",
            }}
          >
            {r.title || page.name}
          </h1>
          <p style={{ ...small, fontSize: 16, marginTop: 6 }}>{r.line}</p>
          <p style={{ ...small, fontSize: 12, marginTop: 6, color: "var(--text3)" }}>
            {/^[aeiou]/i.test(page.kind_words) ? "An" : "A"} {page.kind_words} your bot has.
            {page.from ? (
              <>
                {" "}
                <a href={page.from} target="_blank" rel="noreferrer noopener" style={{ color: "var(--sky)" }}>
                  Where it came from
                </a>
              </>
            ) : null}
          </p>
          {page.covers && page.covers.length > 0 && (
            <p style={{ ...small, fontSize: 12, marginTop: 4 }}>
              Does this for your bot: {page.covers.join("; ")}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex cursor-pointer items-center gap-2" style={{ ...small, fontWeight: 700 }}>
            <input type="checkbox" checked={page.on_home} onChange={() => void toggleHome()} style={{ width: 18, height: 18 }} />
            On my home page
          </label>
          {page.kind !== "book" &&
            (asking ? (
              <>
                <Pill tone="accent" onClick={() => void remove()}>
                  Yes, remove it
                </Pill>
                <Pill onClick={() => setAsking(false)}>Keep it</Pill>
              </>
            ) : (
              <Pill onClick={() => setAsking(true)}>Remove</Pill>
            ))}
        </div>
      </div>
      {said && <p style={small}>{said}</p>}

      {assembled && <AssembleYesCards appId={assembled.id} onYes={() => setTimeout(() => void loadAssembled(), 15_000)} />}
      {assembled && (
        <AssembledPanel
          id={page.id}
          app={assembled}
          host={page.host}
          onHost={(host) => setPage({ ...page, host })}
          onAgain={() => setTimeout(() => void loadAssembled(), 15_000)}
        />
      )}

      {page.kind === "app" && page.host && !assembled && (
        <HostPanel id={page.id} host={page.host} onChange={(host) => setPage({ ...page, host })} />
      )}

      {page.kind === "app" && page.open && (
        <Panel>
          <p style={{ ...small, marginBottom: 10 }}>{page.name} opens on its own page.</p>
          <Pill tone="accent" onClick={() => router.push(page.open || "/dashboard")}>
            Open {page.name}
          </Pill>
        </Panel>
      )}

      {localScreen(page.screen) && (
        <Panel>
          <p style={{ ...small, marginBottom: 10 }}>
            It started its own screen on this computer last time. If it is still running, open it here.
          </p>
          <a
            href={localScreen(page.screen)}
            target="_blank"
            rel="noreferrer noopener"
            style={{ ...small, fontWeight: 700, color: "var(--sky)" }}
          >
            Open its screen
          </a>
        </Panel>
      )}

      {(page.kind === "skill" || (page.kind === "app" && page.host)) && page.needs && (
        <NeedsPanel id={page.id} needs={page.needs} onChange={(needs) => setPage({ ...page, needs })} />
      )}

      {page.kind === "skill" && <MediaShelf id={page.id} />}

      {page.daily && page.daily.length > 0 && (
        <>
          <SectionBar title="Its daily task" />
          {page.daily.map((d, i) => (
            <Panel key={`${d.at}-${i}`}>
              <p style={{ ...small, fontSize: 12, marginBottom: 6, color: "var(--text3)" }}>
                {d.label ? `${d.label}, ` : ""}day {d.day ?? ""}
                {d.at ? `, ${new Date(d.at).toLocaleString()}` : ""}
              </p>
              <Answer text={d.text} />
            </Panel>
          ))}
        </>
      )}

      {page.kind === "feed" && page.feed && (
        <Panel>
          <SectionBar title="Newest from it" />
          {page.feed.items.length > 0 ? (
            <ul style={{ margin: "8px 0 0", padding: 0, listStyle: "none", display: "grid", gap: 8 }}>
              {page.feed.items.map((it, i) => (
                <li key={i} style={{ ...small, color: "var(--text)" }}>
                  <span style={{ fontWeight: 700 }}>{plainText(it.title || "")}</span>
                  {it.detail ? <span style={{ color: "var(--text2)" }}>{` (${plainText(it.detail)})`}</span> : null}
                  {itemLink(it.link) && (
                    <a
                      href={itemLink(it.link)}
                      target="_blank"
                      rel="noreferrer noopener"
                      style={{ marginLeft: 8, fontSize: 12, color: "var(--sky)" }}
                    >
                      Open
                    </a>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p style={{ ...small, marginTop: 8 }}>{page.feed.message || "Nothing has been fetched yet."}</p>
          )}
          {!page.feed.allowed && page.feed.service_message && (
            <p style={{ ...small, marginTop: 8 }}>
              {page.feed.service_message}
              {page.feed.how_to_fix ? ` To switch it on: ${page.feed.how_to_fix}` : ""}
            </p>
          )}
          {page.feed.looks_up && <FeedLookup id={page.id} name={page.name} />}
          {page.feed.credit && <p style={{ ...small, fontSize: 12, marginTop: 6, color: "var(--text3)" }}>{page.feed.credit}</p>}
          {page.open && (
            <div style={{ marginTop: 10 }}>
              <Pill onClick={() => router.push(page.open || "/dashboard")}>Open its live card</Pill>
            </div>
          )}
        </Panel>
      )}

      {page.kind === "tool_server" && (
        <>
          <SectionBar title="Its tools" />
          <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}>
            {(page.tools || []).map((t) => (
              <ToolCard key={t.name} id={page.id} tool={t} />
            ))}
          </div>
        </>
      )}

      {page.kind === "data" &&
        (page.tables || []).map((t) => (
          <div key={t.name} style={{ display: "grid", gap: 6 }}>
            <SectionBar title={`${t.name} (${t.count})`} />
            <div style={{ overflowX: "auto" }}>
              <table style={{ borderCollapse: "collapse", width: "100%", ...small }}>
                <thead>
                  <tr>
                    {t.columns.map((c) => (
                      <th key={c} style={{ textAlign: "left", padding: "6px 8px", borderBottom: "1px solid var(--line)", color: "var(--text)" }}>
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {t.rows.map((row, i) => (
                    <tr key={i}>
                      {t.columns.map((c) => (
                        <td key={c} style={{ padding: "5px 8px", borderBottom: "1px solid var(--line)" }}>
                          {row[c]}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}

      {page.kind !== "app" && <AskBox id={page.id} name={page.name} tryIt={r.try || []} />}

      {page.kind === "book" && page.open && (
        <Link href={page.open} style={{ ...small, color: "var(--sky)" }}>
          See it on the Knowledge page
        </Link>
      )}

      {r.can && r.can.length > 0 && (
        <>
          <SectionBar title="What it can do" />
          <ul style={{ margin: 0, paddingLeft: 18, display: "grid", gap: 4 }}>
            {r.can.map((c) => (
              <li key={c} style={small}>
                {c}
              </li>
            ))}
          </ul>
        </>
      )}
    </AppPage>
  );
}
