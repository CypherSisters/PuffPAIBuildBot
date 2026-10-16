"use client";

/**
 * An MCP App screen (SEP-1865, MCP Apps) on a tool server's page.
 *
 * A tool can name a `ui://` resource on its own server; the backend reads it
 * in the same session as the call (src/puffpai/mcp_client.py,
 * call_tool_with_ui) and hands the HTML here. It is someone else's code, so
 * it is shown locked down:
 *
 *  - `sandbox="allow-scripts"` and NOT allow-same-origin: the page runs in an
 *    opaque origin. It cannot read this dashboard, its cookies or its storage,
 *    cannot open windows, submit forms or move the top page.
 *  - It runs inside the locked host document /mcp-app-host
 *    (app/mcp-app-host/route.ts, the spec's sandbox proxy), never directly in
 *    this page. That document's own response policy (no network, frame-src
 *    'none', sandbox) is inherited by the screen's srcdoc frame, so the lock
 *    is the same however this page was reached: a client-side navigation
 *    keeps the first page's policy, which used to let a screen navigate its
 *    frame to an address the dashboard allows.
 *  - The screen's HTML also gets a Content-Security-Policy meta tag first,
 *    before any of its own markup (a second copy of the same rule). The
 *    server's own `ui.csp` domains are NOT granted (stricter than the spec
 *    requires; the spec lets a host restrict further).
 *  - Messages are taken only from this frame's own window, and only the
 *    JSON-RPC shapes below are answered.
 *
 * The bridge (postMessage, JSON-RPC 2.0), the minimum of the spec:
 *  - `ui/initialize` (and plain `initialize`) -> host info and context;
 *  - after `ui/notifications/initialized`: `ui/notifications/tool-input` with
 *    the arguments, then `ui/notifications/tool-result` with the answer;
 *  - `ui/notifications/size-changed` -> the frame's height (within limits);
 *  - `tools/call` -> a Yes/No card on this page first, every time; after her
 *    yes, the call goes through the page's own Run route (only tools visible
 *    to apps, the server's own switch still applies), and the answer goes
 *    back to the screen; after a no, the screen is told no;
 *  - `ping` -> {}.
 * Everything else (open a link, send a chat message, read other resources,
 * change display mode) is answered "not supported here". The host document
 * says `ui/notifications/sandbox-proxy-ready`; this page answers once with
 * `ui/notifications/sandbox-resource-ready` carrying the HTML.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { NUNITO, Pill } from "@/app/dashboard/apps/_components/ui";
import { runThingTool, type JsonSchema } from "@/app/_lib/services/myThingsService";

const PROTOCOL = "2026-01-26";
/** The locked host document (app/mcp-app-host/route.ts). */
const HOST = "/mcp-app-host";
const MIN_HEIGHT = 80;
const MAX_HEIGHT = 900;

/** No network, no plugins, no frames, nothing sent anywhere. */
export const APP_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
  "img-src data: blob:; media-src data: blob:; font-src data:; connect-src 'none'; " +
  "frame-src 'none'; child-src 'none'; worker-src 'none'; object-src 'none'; " +
  "manifest-src 'none'; base-uri 'none'; form-action 'none'";

/** The HTML with the policy first. A leading doctype is put back in front so
 *  the page keeps standards mode; the meta tag then opens <head> itself, so
 *  nothing of the page's own (not even a script before its <head>) can run
 *  before the policy is in force. */
export function lockedDocument(html: string): string {
  const body = html.replace(/^﻿?\s*(<!--[\s\S]*?-->\s*)*<!doctype[^>]*>/i, "");
  return (
    "<!DOCTYPE html>" +
    `<meta http-equiv="Content-Security-Policy" content="${APP_CSP}">` +
    '<meta name="referrer" content="no-referrer">' +
    body
  );
}

type Rpc = { jsonrpc?: string; id?: string | number; method?: string; params?: unknown };

type Asked = { id: string | number; name: string; args: Record<string, unknown> };

export default function McpAppFrame({
  thingId,
  title,
  html,
  tool,
  args,
  result,
}: {
  thingId: string;
  title: string;
  html: string;
  tool: { name: string; input_schema: JsonSchema };
  args: Record<string, unknown>;
  result: unknown;
}) {
  const frame = useRef<HTMLIFrameElement | null>(null);
  const [height, setHeight] = useState(320);
  const [asked, setAsked] = useState<Asked | null>(null);
  const waiting = useRef<Asked | null>(null);
  const [note, setNote] = useState("");
  /** The HTML is handed to the host document once per screen. */
  const handed = useRef(false);

  const send = useCallback((msg: Record<string, unknown>) => {
    // An opaque-origin frame can only be addressed with "*"; the window
    // itself is the address.
    frame.current?.contentWindow?.postMessage({ jsonrpc: "2.0", ...msg }, "*");
  }, []);

  const handOver = useCallback(() => {
    if (handed.current || !frame.current?.contentWindow) return;
    handed.current = true;
    send({ method: "ui/notifications/sandbox-resource-ready", params: { html: lockedDocument(html) } });
  }, [html, send]);

  useEffect(() => {
    handed.current = false;
  }, [html]);

  useEffect(() => {
    function onMessage(e: MessageEvent) {
      if (!frame.current || e.source !== frame.current.contentWindow) return;
      const m = e.data as Rpc;
      if (!m || typeof m !== "object" || m.jsonrpc !== "2.0" || typeof m.method !== "string") return;
      if (m.method.length > 100) return;
      const hasId = typeof m.id === "string" || typeof m.id === "number";
      const params = (m.params && typeof m.params === "object" ? m.params : {}) as Record<string, unknown>;
      const fail = (message: string, code = -32601) => hasId && send({ id: m.id, error: { code, message } });

      switch (m.method) {
        case "ui/notifications/sandbox-proxy-ready":
          handOver();
          return;
        case "ui/initialize":
        case "initialize":
          if (!hasId) return;
          send({
            id: m.id,
            result: {
              protocolVersion: PROTOCOL,
              hostInfo: { name: "BuildBot", version: "1" },
              hostCapabilities: { serverTools: {}, logging: {} },
              hostContext: {
                toolInfo: { tool: { name: tool.name, inputSchema: tool.input_schema } },
                theme: window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light",
                displayMode: "inline",
                availableDisplayModes: ["inline"],
                containerDimensions: { maxHeight: MAX_HEIGHT },
                locale: navigator.language,
                timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                platform: "web",
              },
            },
          });
          return;
        case "ui/notifications/initialized":
        case "notifications/initialized":
          send({ method: "ui/notifications/tool-input", params: { arguments: args } });
          send({ method: "ui/notifications/tool-result", params: result ?? { content: [] } });
          return;
        case "ui/notifications/size-changed": {
          const h = Number(params.height);
          if (Number.isFinite(h)) setHeight(Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, Math.ceil(h))));
          return;
        }
        case "ping":
          if (hasId) send({ id: m.id, result: {} });
          return;
        case "tools/call": {
          if (!hasId) return;
          const name = typeof params.name === "string" ? params.name : "";
          const a = params.arguments && typeof params.arguments === "object" && !Array.isArray(params.arguments)
            ? (params.arguments as Record<string, unknown>)
            : {};
          if (!name || name.length > 128) return fail("That is not a tool name.", -32602);
          // One question at a time; the screen cannot queue up yeses.
          if (waiting.current) return fail("Another question is waiting for the owner.", -32000);
          waiting.current = { id: m.id as string | number, name, args: a };
          setAsked(waiting.current);
          return;
        }
        default:
          if (m.method.startsWith("notifications/") || m.method.startsWith("ui/notifications/")) return;
          fail("Not supported here.");
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [args, handOver, result, send, tool]);

  // Tell the screen it is going away (the spec's teardown), best effort.
  useEffect(() => {
    const el = frame.current;
    return () => {
      el?.contentWindow?.postMessage({ jsonrpc: "2.0", id: "teardown", method: "ui/resource-teardown", params: {} }, "*");
    };
  }, [html]);

  async function answer(yes: boolean) {
    const q = waiting.current;
    waiting.current = null;
    setAsked(null);
    if (!q) return;
    if (!yes) {
      setNote("Okay, nothing was run.");
      send({ id: q.id, error: { code: -32000, message: "The owner said no." } });
      return;
    }
    setNote(`Running ${q.name}...`);
    const reply = await runThingTool(thingId, q.name, q.args, true);
    if (!reply.ok) {
      setNote(reply.data.message || "That did not work.");
      send({ id: q.id, error: { code: -32000, message: reply.data.message || "That did not work." } });
      return;
    }
    setNote("");
    send({
      id: q.id,
      result: reply.data.app_result ?? { content: [{ type: "text", text: reply.data.result || "" }] },
    });
  }

  const small: React.CSSProperties = { margin: 0, fontFamily: NUNITO, fontSize: 14, color: "var(--text2)" };
  return (
    <div style={{ marginTop: 10, display: "grid", gap: 8 }}>
      <iframe
        ref={frame}
        title={`${title}: its own screen`}
        sandbox="allow-scripts"
        src={HOST}
        key={html.length + ":" + html.slice(0, 64)}
        onLoad={handOver}
        referrerPolicy="no-referrer"
        style={{ width: "100%", height, border: "1px solid var(--line)", borderRadius: 12, background: "#fff" }}
      />
      <p style={{ ...small, fontSize: 12, color: "var(--text3)" }}>
        Its own screen, from the tool server. It cannot reach the internet or this dashboard.
      </p>
      {asked && (
        <div role="alertdialog" style={{ padding: 12, borderRadius: 12, border: "1px solid var(--line)", background: "var(--card2)" }}>
          <p style={{ ...small, fontWeight: 700, color: "var(--text)" }}>
            Its screen asks to use the tool {asked.name}. May it?
          </p>
          <pre style={{ ...small, fontSize: 12, whiteSpace: "pre-wrap", wordBreak: "break-word", margin: "6px 0" }}>
            {JSON.stringify(asked.args, null, 2).slice(0, 2000)}
          </pre>
          <div className="flex gap-2">
            <Pill tone="accent" onClick={() => void answer(true)}>
              Yes, run it
            </Pill>
            <Pill onClick={() => void answer(false)}>No</Pill>
          </div>
        </div>
      )}
      {note && (
        <p role="status" style={small}>
          {note}
        </p>
      )}
    </div>
  );
}
