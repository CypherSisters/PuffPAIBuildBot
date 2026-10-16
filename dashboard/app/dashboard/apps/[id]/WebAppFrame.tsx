"use client";

/**
 * A browser app Toolsmith added, shown in a locked frame.
 *
 * The frame's address is never taken from a manifest or the app: the backend
 * computes it (`GET /webapps/<id>/origin`) and this component accepts it only
 * when it is exactly `http://<this id>.localhost:<port>`. The page's own
 * Content-Security-Policy (middleware `webAppFrameSrc`) lets this page frame
 * that one address and nothing else.
 *
 * Frame rules (measured): `allow-same-origin` is needed or the
 * app's storage throws, and it is safe because the app's address differs from
 * the dashboard's. No popups, no top navigation, no camera, microphone or
 * location (`allow=""`), and no referrer.
 *
 * Messages from the app: exactly one is heard, and only from this frame's own
 * window at this frame's own address: `{puffpai: "not-saved"}`, which the shim
 * sends when a change the owner made while her bot was using the app could
 * not be saved. It shows a fixed sentence (never words from the app). Every
 * other message is ignored; opening outside links for an app is not in this
 * round.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import { useEffect, useRef, useState } from "react";
import { apiGet } from "@/app/_lib/api";

export const WEB_APP_ID = /^[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}$/;
export const FRAME_SANDBOX = "allow-scripts allow-same-origin allow-forms allow-modals allow-downloads";
export const NOT_SAVED =
  "Your bot changed this app while you were working in it, so your last change was not saved. Please make it again.";

/** The backend's answer, accepted only in the one shape it can take. */
export function checkedOrigin(id: string, origin: unknown): string | null {
  if (!WEB_APP_ID.test(id) || typeof origin !== "string") return null;
  const m = /^http:\/\/([a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8})\.localhost:([0-9]{2,5})$/.exec(origin);
  if (!m || m[1] !== id) return null;
  const port = Number(m[2]);
  return port >= 1024 && port <= 65535 ? origin : null;
}

/** True only for the one message this page listens to, from its own frame. */
export function isNotSaved(
  ev: { origin: string; source: unknown; data: unknown },
  origin: string | null,
  frameWindow: unknown,
): boolean {
  if (!origin || ev.origin !== origin || !frameWindow || ev.source !== frameWindow) return false;
  const d = ev.data as { puffpai?: unknown } | null;
  return !!d && typeof d === "object" && Object.keys(d).length === 1 && d.puffpai === "not-saved";
}

/**
 * True (and the page is loaded again as a whole) when this page was reached
 * by a client-side move from another page: the Content-Security-Policy in
 * force is still that other page's, which names no app, so the frame would
 * show "This content is blocked". A full load gets this page's own policy,
 * which names this one app (middleware `webAppFrameSrc`). Links into an app
 * are full loads already (`AppLink`); this catches every other way in. A
 * reload that did not land on this page (a redirect) is never tried again
 * within 10 seconds, so it can never loop; one that landed clears the mark.
 */
export function reloadIfMovedHere(): boolean {
  if (typeof window === "undefined" || typeof performance === "undefined") return false;
  const nav = performance.getEntriesByType?.("navigation")?.[0] as PerformanceNavigationTiming | undefined;
  if (!nav || typeof nav.name !== "string") return false;
  let loaded = "";
  try {
    loaded = new URL(nav.name).pathname.replace(/\/$/, "");
  } catch {
    return false;
  }
  const here = window.location.pathname.replace(/\/$/, "");
  const key = `puffpai-app-reload:${here}`;
  if (loaded === here) {
    try {
      window.sessionStorage.removeItem(key);
    } catch {
      // nothing kept
    }
    return false;
  }
  try {
    const last = Number(window.sessionStorage.getItem(key) || 0);
    if (Date.now() - last < 10_000) return false;
    window.sessionStorage.setItem(key, String(Date.now()));
  } catch {
    // no session storage: reload anyway, the next load is a full one
  }
  window.location.replace(window.location.href);
  return true;
}

/** Sent by the app's header when the bot's copy of the app's data changed
 * (examples cleared, or that undone): the frame loads the app again. */
export const APP_DATA_CHANGED = "puffpai:app-data-changed";

export default function WebAppFrame({ id, title }: { id: string; title: string }) {
  const [src, setSrc] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [notSaved, setNotSaved] = useState(false);
  const frameRef = useRef<HTMLIFrameElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setSrc(null);
    setProblem(null);
    if (reloadIfMovedHere()) return;
    (async () => {
      try {
        const reply = await apiGet<{ origin?: unknown }>(`/webapps/${id}/origin`);
        const origin = checkedOrigin(id, reply?.origin);
        if (cancelled) return;
        if (origin) setSrc(origin + "/");
        else setProblem("This app is not set up on your bot.");
      } catch {
        if (!cancelled) setProblem("Your bot did not answer, so the app cannot open right now.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    const again = () => {
      const f = frameRef.current;
      if (f && src) f.src = src;          // setting src loads the app again
    };
    window.addEventListener(APP_DATA_CHANGED, again);
    return () => window.removeEventListener(APP_DATA_CHANGED, again);
  }, [src]);

  useEffect(() => {
    const origin = src ? src.replace(/\/$/, "") : null;
    const onMessage = (ev: MessageEvent) => {
      if (isNotSaved(ev, origin, frameRef.current?.contentWindow ?? null)) setNotSaved(true);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [src]);

  if (problem) {
    return (
      <p style={{ color: "var(--text2)", padding: 16 }} role="status">
        {problem}
      </p>
    );
  }
  if (!src) {
    return (
      <p style={{ color: "var(--text3)", padding: 16 }} role="status">
        Opening the app.
      </p>
    );
  }
  return (
    <div>
      {notSaved && (
        <p role="status" style={{ margin: "0 0 8px", color: "var(--text2)" }}>
          {NOT_SAVED}{" "}
          <button
            type="button"
            onClick={() => setNotSaved(false)}
            style={{ border: "1px solid var(--line)", borderRadius: 999, padding: "2px 10px",
                     background: "var(--chip)", color: "var(--text)", fontSize: 12, cursor: "pointer" }}
          >
            OK
          </button>
        </p>
      )}
      <iframe
        ref={frameRef}
        src={src}
        title={title}
        sandbox={FRAME_SANDBOX}
        allow=""
        referrerPolicy="no-referrer"
        style={{
          width: "100%",
          height: "calc(100vh - 150px)",
          minHeight: 480,
          border: "1px solid var(--line)",
          borderRadius: 12,
          background: "#fff",
        }}
      />
    </div>
  );
}
