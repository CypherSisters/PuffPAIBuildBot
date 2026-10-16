"use client";

/**
 * The thin bar above a browser app: Back, what the bot can do here, outside
 * services, folder, and removing the app.
 *
 * Everything the app or its actions say is rendered as plain text (React
 * text nodes), never as HTML or markdown. The action words come from
 * `GET /webapps/<id>/abilities` (Toolsmith-compiled, Drawbridge-checked).
 *
 * Switching something ON is always two taps: the first POST answers with the
 * backend's own words and a single-use ticket bound to this paired device;
 * the owner reads the words, and only "Switch on" / "Give it" sends the ticket
 * back. Switching a site off or taking a folder back is one tap.
 *
 * Removing posts to `POST /integrations/<id>/remove` after one plain question,
 * then loads the app's goodbye page (`/__puffpai/bye`, which answers with
 * Clear-Site-Data) in a hidden frame so this browser forgets what the app kept
 * here too. The page's frame-src lets this page frame only that one address.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiGet, apiPostRaw } from "@/app/_lib/api";
import { APP_DATA_CHANGED, checkedOrigin } from "./WebAppFrame";

type Panel = "abilities" | "outside" | "folder" | "remove" | null;

type SiteRow = { host: string; for: string; state: "on" | "off" | "asked"; words: string[] };
type Question = { ticket: string; words: string[]; buttons: string[] };
type Reply = {
  ok?: boolean;
  needs_yes?: boolean;
  ticket?: string;
  words?: unknown;
  buttons?: unknown;
  message?: unknown;
  line?: unknown;
};

const barStyle = {
  display: "flex",
  flexWrap: "wrap" as const,
  gap: 8,
  alignItems: "center",
  margin: "4px 0 10px",
};

const buttonStyle = {
  border: "1px solid var(--line)",
  borderRadius: 999,
  padding: "6px 12px",
  background: "var(--chip)",
  color: "var(--text)",
  fontSize: 13,
  cursor: "pointer",
};

const panelStyle = { margin: "0 0 10px" };

/** Only plain strings from the backend, a bounded number of them. */
function lines(value: unknown, max = 4): string[] {
  return Array.isArray(value)
    ? value.filter((x): x is string => typeof x === "string").map((x) => x.slice(0, 300)).slice(0, max)
    : [];
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.slice(0, 300) : null;
}

function question(reply: Reply | null): Question | null {
  if (!reply || !reply.needs_yes || typeof reply.ticket !== "string" || !reply.ticket) return null;
  const words = lines(reply.words);
  const buttons = lines(reply.buttons, 2);
  return words.length && buttons.length === 2 ? { ticket: reply.ticket, words, buttons } : null;
}

/** Wait for the hidden goodbye frame to load (or give up after 4 seconds). */
function sayGoodbye(origin: string): Promise<void> {
  return new Promise((done) => {
    const frame = document.createElement("iframe");
    frame.setAttribute("sandbox", "allow-same-origin");
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.setAttribute("aria-hidden", "true");
    frame.style.display = "none";
    const finish = () => {
      frame.remove();
      done();
    };
    const timer = window.setTimeout(finish, 4000);
    frame.onload = () => {
      window.clearTimeout(timer);
      finish();
    };
    frame.src = origin + "/__puffpai/bye";
    document.body.appendChild(frame);
  });
}

export default function AppHeader({ id, name }: { id: string; name: string }) {
  const router = useRouter();
  const [open, setOpen] = useState<Panel>(null);
  const [abilities, setAbilities] = useState<string[] | null>(null);
  const [sites, setSites] = useState<SiteRow[] | null>(null);
  const [asking, setAsking] = useState<{ host: string; q: Question } | null>(null);
  const [folderPath, setFolderPath] = useState("");
  const [folderQ, setFolderQ] = useState<Question | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [examples, setExamples] = useState<string | null>(null);
  const [cleared, setCleared] = useState<{ line: string; version: number | null } | null>(null);

  // "This app came with 18 example entries of its own": the backend's own
  // fixed words (never the app's), shown only while some are still there
  useEffect(() => {
    let cancelled = false;
    apiGet<{ line?: unknown }>(`/webapps/${id}/examples`)
      .then((got) => {
        if (!cancelled) setExamples(typeof got?.line === "string" && got.line ? got.line.slice(0, 160) : null);
      })
      .catch(() => {
        if (!cancelled) setExamples(null);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  // One tap takes the app's own example entries out of your bot's copy
  // (never one you added or changed); Undo puts them back. The frame is
  // loaded again either way, so the app shows what is kept.
  const clearExamples = useCallback(async () => {
    setBusy(true);
    try {
      const { data } = await apiPostRaw<{ ok?: boolean; line?: unknown; version?: unknown; header?: unknown }>(
        `/webapps/${id}/examples/clear`, {});
      const line = typeof data?.line === "string" ? data.line.slice(0, 160) : "That did not work just now.";
      const version = data?.ok && typeof data?.version === "number" ? data.version : null;
      setCleared({ line, version });
      if (version !== null) window.dispatchEvent(new Event(APP_DATA_CHANGED));
      setExamples(typeof data?.header === "string" && data.header ? data.header.slice(0, 160) : null);
    } catch {
      setCleared({ line: "Your bot did not answer just now.", version: null });
    } finally {
      setBusy(false);
    }
  }, [id]);

  const undoClear = useCallback(async () => {
    if (!cleared || cleared.version === null) return;
    setBusy(true);
    try {
      const { data } = await apiPostRaw<{ line?: unknown }>(`/webapps/${id}/undo`, { version: cleared.version });
      setCleared({ line: typeof data?.line === "string" ? data.line.slice(0, 160) : "That did not work just now.",
                   version: null });
      window.dispatchEvent(new Event(APP_DATA_CHANGED));
      const got = await apiGet<{ line?: unknown }>(`/webapps/${id}/examples`);
      setExamples(typeof got?.line === "string" && got.line ? got.line.slice(0, 160) : null);
    } catch {
      setCleared({ line: "Your bot did not answer just now.", version: null });
    } finally {
      setBusy(false);
    }
  }, [id, cleared]);

  const toggle = useCallback((panel: Panel) => {
    setNote(null);
    setAsking(null);
    setFolderQ(null);
    setOpen((cur) => (cur === panel ? null : panel));
  }, []);

  const showAbilities = useCallback(async () => {
    toggle("abilities");
    try {
      const list = await apiGet<unknown>(`/webapps/${id}/abilities`);
      setAbilities(lines(list, 40));
    } catch {
      setAbilities(null);
      setNote("Your bot did not answer just now.");
    }
  }, [id, toggle]);

  const loadSites = useCallback(async () => {
    try {
      const list = await apiGet<unknown>(`/webapps/${id}/hosts`);
      const rows = Array.isArray(list)
        ? list
            .filter(
              (r): r is SiteRow =>
                !!r && typeof r.host === "string" && ["on", "off", "asked"].includes(r.state),
            )
            .map((r) => ({ host: r.host.slice(0, 253), for: String(r.for || "").slice(0, 80),
                           state: r.state, words: lines(r.words) }))
            .slice(0, 10)
        : [];
      setSites(rows);
    } catch {
      setSites(null);
      setNote("Your bot did not answer just now.");
    }
  }, [id]);

  const showSites = useCallback(async () => {
    toggle("outside");
    await loadSites();
  }, [toggle, loadSites]);

  const switchSite = useCallback(
    async (host: string, on: boolean, ticket?: string) => {
      setBusy(true);
      setNote(null);
      try {
        const { data } = await apiPostRaw<Reply>(`/webapps/${id}/hosts`, ticket ? { host, on, ticket } : { host, on });
        const q = on && !ticket ? question(data) : null;
        if (q) {
          setAsking({ host, q });
          return;
        }
        setAsking(null);
        if (!data?.ok) setNote(text(data?.message) ?? "That did not change anything.");
        await loadSites();
      } catch {
        setNote("Your bot did not answer just now.");
      } finally {
        setBusy(false);
      }
    },
    [id, loadSites],
  );

  const giveFolder = useCallback(
    async (ticket?: string) => {
      setBusy(true);
      setNote(null);
      try {
        const body = ticket ? { path: folderPath, mode: "ro", ticket } : { path: folderPath, mode: "ro" };
        const { data } = await apiPostRaw<Reply>(`/webapps/${id}/folder`, body);
        const q = ticket ? null : question(data);
        if (q) {
          setFolderQ(q);
          return;
        }
        setFolderQ(null);
        setNote(text(data?.line) ?? text(data?.message) ?? "That did not change anything.");
      } catch {
        setNote("Your bot did not answer just now.");
      } finally {
        setBusy(false);
      }
    },
    [id, folderPath],
  );

  const takeFolderBack = useCallback(async () => {
    setBusy(true);
    try {
      const { data } = await apiPostRaw<Reply>(`/webapps/${id}/folder`, { path: "" });
      setNote(text(data?.line) ?? text(data?.message) ?? "That did not change anything.");
    } catch {
      setNote("Your bot did not answer just now.");
    } finally {
      setBusy(false);
    }
  }, [id]);

  const remove = useCallback(async () => {
    setBusy(true);
    setNote(null);
    try {
      // the address first: after removing, the backend no longer names it
      let origin: string | null = null;
      try {
        const reply = await apiGet<{ origin?: unknown }>(`/webapps/${id}/origin`);
        origin = checkedOrigin(id, reply?.origin);
      } catch {
        origin = null;
      }
      const { ok } = await apiPostRaw(`/integrations/${id}/remove`, {});
      if (!ok) {
        setNote("Removing this app from here is not in this build yet.");
        return;
      }
      if (origin) await sayGoodbye(origin);
      router.push("/dashboard");
    } catch {
      setNote("Removing this app from here is not in this build yet.");
    } finally {
      setBusy(false);
    }
  }, [id, router]);

  return (
    <div>
      <div style={barStyle}>
        <button type="button" style={buttonStyle} onClick={() => router.back()}>
          Back
        </button>
        <strong style={{ fontSize: 16, marginRight: 8 }}>{name}</strong>
        <button type="button" style={buttonStyle} onClick={showAbilities}>
          What your bot can do here
        </button>
        <button type="button" style={buttonStyle} onClick={showSites}>
          Outside services
        </button>
        <button type="button" style={buttonStyle} onClick={() => toggle("folder")}>
          Folder
        </button>
        <button type="button" style={buttonStyle} onClick={() => toggle("remove")}>
          Remove this app
        </button>
      </div>
      {examples && (
        <p style={{ margin: "0 0 10px" }}>
          {examples}{" "}
          <button type="button" style={buttonStyle} disabled={busy} onClick={clearExamples}>
            Clear the examples
          </button>
        </p>
      )}
      {cleared && (
        <p role="status" style={{ margin: "0 0 10px" }}>
          {cleared.line}{" "}
          {cleared.version !== null && (
            <button type="button" style={buttonStyle} disabled={busy} onClick={undoClear}>
              Undo
            </button>
          )}
        </p>
      )}
      {open === "abilities" && (
        <div role="region" aria-label="What your bot can do here" style={panelStyle}>
          {abilities && abilities.length > 0 ? (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {abilities.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : abilities ? (
            <p style={{ margin: 0 }}>Your bot can open this app for you, but cannot use it by itself yet.</p>
          ) : null}
        </div>
      )}
      {open === "outside" && (
        <div role="region" aria-label="Outside services" style={panelStyle}>
          {asking ? (
            <div>
              {asking.q.words.map((w) => (
                <p key={w} style={{ margin: "0 0 4px" }}>
                  {w}
                </p>
              ))}
              <button
                type="button"
                style={buttonStyle}
                disabled={busy}
                onClick={() => switchSite(asking.host, true, asking.q.ticket)}
              >
                {asking.q.buttons[0]}
              </button>{" "}
              <button type="button" style={buttonStyle} disabled={busy} onClick={() => switchSite(asking.host, false)}>
                {asking.q.buttons[1]}
              </button>
            </div>
          ) : sites && sites.length > 0 ? (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {sites.map((s) => (
                <li key={s.host} style={{ marginBottom: 4 }}>
                  {s.words[0] ?? s.host} {s.state === "on" ? "Switched on." : "Switched off."}{" "}
                  <button
                    type="button"
                    style={buttonStyle}
                    disabled={busy}
                    onClick={() => switchSite(s.host, s.state !== "on")}
                  >
                    {s.state === "on" ? "Switch off" : "Switch on"}
                  </button>
                </li>
              ))}
            </ul>
          ) : sites ? (
            <p style={{ margin: 0 }}>This app has no outside services. Its web requests to the internet are stopped.</p>
          ) : null}
        </div>
      )}
      {open === "folder" && (
        <div role="region" aria-label="Folder" style={panelStyle}>
          {folderQ ? (
            <div>
              {folderQ.words.map((w) => (
                <p key={w} style={{ margin: "0 0 4px" }}>
                  {w}
                </p>
              ))}
              <button type="button" style={buttonStyle} disabled={busy} onClick={() => giveFolder(folderQ.ticket)}>
                {folderQ.buttons[0]}
              </button>{" "}
              <button type="button" style={buttonStyle} disabled={busy} onClick={() => setFolderQ(null)}>
                {folderQ.buttons[1]}
              </button>
            </div>
          ) : (
            <div>
              <p style={{ margin: "0 0 6px" }}>
                Your bot can give this app one of your folders to read, like your pictures. Never your vault.
              </p>
              <input
                type="text"
                value={folderPath}
                onChange={(e) => setFolderPath(e.target.value.slice(0, 1024))}
                placeholder="The folder's full path, for example your Pictures folder"
                aria-label="The folder's full path"
                style={{ width: "min(520px, 100%)", marginRight: 8 }}
              />
              <button type="button" style={buttonStyle} disabled={busy || !folderPath.trim()} onClick={() => giveFolder()}>
                Give it
              </button>{" "}
              <button type="button" style={buttonStyle} disabled={busy} onClick={takeFolderBack}>
                Take it back
              </button>
            </div>
          )}
        </div>
      )}
      {open === "remove" && (
        <div style={panelStyle}>
          <p style={{ margin: "0 0 6px" }}>Remove {name} and its data from your bot?</p>
          <button type="button" style={buttonStyle} disabled={busy} onClick={remove}>
            Remove it
          </button>{" "}
          <button type="button" style={buttonStyle} onClick={() => toggle(null)}>
            Keep it
          </button>
        </div>
      )}
      {note && (
        <p role="status" style={{ margin: "0 0 10px", color: "var(--text2)" }}>
          {note}
        </p>
      )}
    </div>
  );
}
