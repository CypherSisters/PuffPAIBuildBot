"use client";

/**
 * Home Assistant voice key.
 *
 * One plain page in the key walkthrough: the key Home Assistant uses so voice
 * can read the owner's apps and make changes she can undo (never removing).
 * Every sentence comes from the backend (`GET /integrations/voice-key`). The
 * key is shown only here, only to a paired browser, never put in an address,
 * never stored by this page and never logged.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "@/app/_lib/api";

type VoicePage = {
  ok: boolean;
  title: string;
  line: string;
  what: string;
  steps: string[];
  on: boolean;
  key: string;
  make_label: string;
  off_label: string;
  message: string;
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

function clean(page: unknown): VoicePage | null {
  if (!page || typeof page !== "object") return null;
  const p = page as Record<string, unknown>;
  const text = (v: unknown) => (typeof v === "string" ? v : "");
  return {
    ok: p.ok === true,
    title: text(p.title),
    line: text(p.line),
    what: text(p.what),
    steps: Array.isArray(p.steps) ? p.steps.filter((s): s is string => typeof s === "string").slice(0, 6) : [],
    on: p.on === true,
    key: text(p.key),
    make_label: text(p.make_label),
    off_label: text(p.off_label),
    message: text(p.message),
  };
}

export default function VoiceKeyPage() {
  const [page, setPage] = useState<VoicePage | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiGet<unknown>("/integrations/voice-key", { cache: "no-store" })
      .then((p) => setPage(clean(p)))
      .catch(() => setNote("This page opens only in a browser that is paired with your bot."));
  }, []);

  const act = useCallback(async (what: "new" | "off") => {
    setBusy(true);
    setNote(null);
    try {
      const p = clean(await apiPost<unknown>(`/integrations/voice-key/${what}`, {}));
      setPage(p);
      if (p?.message) setNote(p.message);
    } catch {
      setNote("Your bot did not answer just now. Nothing was changed.");
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <main style={{ maxWidth: 680, margin: "0 auto", padding: "16px 12px" }}>
      <h1 style={{ fontSize: 22, margin: "4px 0 8px" }}>{page?.title || "Home Assistant voice"}</h1>
      {page && <p style={{ margin: "0 0 8px" }}>{page.line}</p>}
      {page && <p style={{ margin: "0 0 12px", opacity: 0.85 }}>{page.what}</p>}
      {page?.on && page.key && (
        <div style={{ margin: "8px 0 12px" }}>
          <code
            style={{
              display: "block",
              padding: "10px 12px",
              border: "1px solid var(--line)",
              borderRadius: 8,
              wordBreak: "break-all",
              userSelect: "all",
            }}
          >
            {page.key}
          </code>
        </div>
      )}
      {page && page.steps.length > 0 && (
        <ol style={{ margin: "0 0 12px 18px" }}>
          {page.steps.map((s) => (
            <li key={s} style={{ margin: "4px 0" }}>
              {s}
            </li>
          ))}
        </ol>
      )}
      {page && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" style={buttonStyle} disabled={busy} onClick={() => act("new")}>
            {page.make_label}
          </button>
          {page.on && (
            <button type="button" style={buttonStyle} disabled={busy} onClick={() => act("off")}>
              {page.off_label}
            </button>
          )}
        </div>
      )}
      {note && <p style={{ marginTop: 12 }}>{note}</p>}
    </main>
  );
}
