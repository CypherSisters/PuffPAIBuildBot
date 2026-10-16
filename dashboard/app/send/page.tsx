"use client";

/**
 * /send — "Send to Puff".
 *
 * A standalone, mobile-first drop point for the household: open it on a phone
 * (or a laptop), pick a photo/PDF/manual, and it lands in the knowledge index.
 *
 * Two gates, in order:
 *   1. Device pairing. The `puff_device` cookie is HttpOnly, so the page can
 *      never read it — pairing state comes from GET /api/pair/status. A QR
 *      deep link may carry ?code=XXXX-XXXX, which only PREFILLS the field:
 *      redeeming is always an explicit tap that POSTs (see app/api/pair).
 *   2. Upload. POST /api/pai/knowledge/upload — the authenticated catch-all
 *      proxy — with NO credentials from the client; the server attaches the
 *      backend API key. XHR rather than fetch(), purely for upload progress.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import MsIcon from "../_components/MsIcon";
import { usePendingSuggestions } from "../_lib/hooks/usePendingSuggestions";
import { appAction } from "../_lib/services/appService";
import {
  acceptSuggestion,
  dismissSuggestion,
  isResolved,
  matchesUpload,
  suggestionToIngestResult,
  suggestionToRecipeResult,
  type Suggestion,
} from "../_lib/suggestions";
import type { IngestResult } from "../dashboard/apps/calendar/ImportEvents";
import type { RecipeResult } from "../dashboard/apps/freshtable/ImportRecipes";

/** The dashboard's review-and-confirm modal, reused verbatim on the phone. */
const ImportEvents = dynamic(
  () => import("../dashboard/apps/calendar/ImportEvents"),
  { ssr: false },
);
const ImportRecipes = dynamic(
  () => import("../dashboard/apps/freshtable/ImportRecipes"),
  { ssr: false },
);

/** How long to wait for the classifier before giving up quietly. */
const ECHO_WINDOW_MS = 90_000;

/* ── zone semantics: identical to the knowledge page's upload area ────── */
type Zone = 1 | 2; // 1 = Personal Vault (PIN-gated), 2 = General Knowledge

const MAX_BYTES = 50 * 1024 * 1024; // matches the proxy's 413 ceiling

/** Backend-supported document types, plus anything a phone camera produces. */
const FILE_ACCEPT =
  ".pdf,.docx,.doc,.txt,.md,.csv,.tsv,.xlsx,.xls,.pptx,.rtf,.odt,.json," +
  ".html,.xml,.yaml,.yml,.log,.heic,.heif," +
  ".jpg,.jpeg,.png,.gif,.bmp,.tiff,.tif,.webp";

/* ── pairing-code formatting: XXXX-XXXX while typing. The server normalises
      case/dashes/spaces anyway, so this is cosmetic, never validation. ──── */
function formatCode(raw: string): string {
  const clean = raw.replace(/[^a-zA-Z0-9]/g, "").toUpperCase().slice(0, 8);
  return clean.length > 4 ? `${clean.slice(0, 4)}-${clean.slice(4)}` : clean;
}

type Phase = "checking" | "pairing" | "ready";
type PairError = null | "invalid" | "cooldown" | "cross_origin" | "network";
type Send =
  | { kind: "idle" }
  | { kind: "uploading"; name: string; pct: number }
  | { kind: "done"; name: string }
  | { kind: "failed"; name: string; message: string; retryable: boolean };

export default function SendPage() {
  const [phase, setPhase] = useState<Phase>("checking");

  // --- pairing state ---
  const [code, setCode] = useState("");
  const [pairing, setPairing] = useState(false);
  const [pairError, setPairError] = useState<PairError>(null);
  const [cooldown, setCooldown] = useState(0);

  // --- send state ---
  const [zone, setZone] = useState<Zone>(2);
  const [send, setSend] = useState<Send>({ kind: "idle" });
  const lastFile = useRef<File | null>(null);

  const cameraRef = useRef<HTMLInputElement>(null);
  const galleryRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  /* ── boot: pairing status + optional ?code= prefill ─────────────────── */
  useEffect(() => {
    const prefill = new URLSearchParams(window.location.search).get("code");
    if (prefill) setCode(formatCode(prefill));

    let live = true;
    fetch("/api/pair/status", { cache: "no-store" })
      .then((r) => r.json())
      .then((d: { paired?: boolean }) => {
        if (live) setPhase(d?.paired ? "ready" : "pairing");
      })
      .catch(() => {
        if (live) setPhase("pairing");
      });
    return () => {
      live = false;
    };
  }, []);

  /* ── 429 cooldown ticker ────────────────────────────────────────────── */
  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const submitPair = useCallback(async () => {
    if (pairing || cooldown > 0 || !code.trim()) return;
    setPairing(true);
    setPairError(null);
    try {
      const res = await fetch("/api/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      if (res.ok) {
        setPhase("ready");
        setCode("");
      } else if (res.status === 429) {
        const wait = Number(res.headers.get("Retry-After")) || 60;
        setCooldown(wait);
        setPairError("cooldown");
      } else if (res.status === 403) {
        setPairError("cross_origin");
      } else {
        setPairError("invalid");
      }
    } catch {
      setPairError("network");
    } finally {
      setPairing(false);
    }
  }, [code, cooldown, pairing]);

  /* ── upload ─────────────────────────────────────────────────────────── */
  const upload = useCallback(
    (file: File) => {
      lastFile.current = file;

      if (file.size > MAX_BYTES) {
        setSend({
          kind: "failed",
          name: file.name,
          message: "Too big. 50 MB max.",
          retryable: false,
        });
        return;
      }

      setSend({ kind: "uploading", name: file.name, pct: 0 });

      const body = new FormData();
      body.append("file", file);
      body.append("zone", String(zone));

      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/pai/knowledge/upload");

      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable) return;
        const pct = Math.round((e.loaded / e.total) * 100);
        setSend({ kind: "uploading", name: file.name, pct });
      };

      xhr.onload = () => {
        if (xhr.status === 401) {
          // Cookie expired or this device was never paired — back to the gate.
          setSend({ kind: "idle" });
          setPhase("pairing");
          return;
        }
        if (xhr.status === 413) {
          setSend({
            kind: "failed",
            name: file.name,
            message: "Too big. 50 MB max.",
            retryable: false,
          });
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          let ok = true;
          try {
            const parsed = JSON.parse(xhr.responseText) as { success?: boolean };
            if (parsed && parsed.success === false) ok = false;
          } catch {
            /* non-JSON 2xx: treat as accepted */
          }
          if (ok) {
            setSend({ kind: "done", name: file.name });
            return;
          }
          setSend({
            kind: "failed",
            name: file.name,
            message: "Puff couldn't read that file.",
            retryable: true,
          });
          return;
        }
        setSend({
          kind: "failed",
          name: file.name,
          message:
            xhr.status === 502
              ? "Puff's brain isn't answering."
              : `Upload failed (${xhr.status}).`,
          retryable: true,
        });
      };

      xhr.onerror = () =>
        setSend({
          kind: "failed",
          name: file.name,
          message: "Lost the connection.",
          retryable: true,
        });

      xhr.send(body);
    },
    [zone],
  );

  /* ── Puff's echo ─────────────────────────────────────────────────────
     Classification runs after the upload lands, so the answer arrives
     seconds — sometimes a cold minute — later. Watch the pending queue for a
     suggestion about THIS file, then offer it right here. Accepting a
     calendar import opens the same per-event confirm list the dashboard
     uses; nothing is written without ticking the boxes. */
  const doneName = send.kind === "done" ? send.name : null;
  const [watching, setWatching] = useState(false);
  const [echo, setEcho] = useState<Suggestion | null>(null);
  const [accepting, setAccepting] = useState(false);
  /** Set when the server refused/failed to resolve — the offer must stay put. */
  const [echoError, setEchoError] = useState<string | null>(null);
  const [importing, setImporting] = useState<{
    label: string;
    result: IngestResult;
  } | null>(null);
  const [importingRecipe, setImportingRecipe] = useState<{
    label: string;
    result: RecipeResult;
  } | null>(null);

  useEffect(() => {
    if (!doneName) return;
    setEcho(null);
    setEchoError(null);
    setWatching(true);
    const t = setTimeout(() => setWatching(false), ECHO_WINDOW_MS);
    return () => clearTimeout(t);
  }, [doneName]);

  const { suggestions } = usePendingSuggestions(watching);

  useEffect(() => {
    if (!watching || !doneName) return;
    const hit = suggestions.find((s) => matchesUpload(s, doneName));
    if (hit) {
      setEcho(hit);
      setWatching(false);
    }
  }, [suggestions, watching, doneName]);

  const calRunAction = useCallback(
    (action: string, params?: Record<string, unknown>) =>
      appAction("calendar", action, params),
    [],
  );

  const freshRunAction = useCallback(
    (action: string, params?: Record<string, unknown>) =>
      appAction("freshtable", action, params),
    [],
  );

  /* The offer is only cleared when the SERVER resolved it. Previously both
     answers cleared it locally and swallowed the response, so a refused call
     looked exactly like a successful one. */
  const showEcho = useCallback(async () => {
    if (!echo) return;
    setAccepting(true);
    setEchoError(null);
    const res = await acceptSuggestion(echo.id);
    setAccepting(false);
    if (!isResolved(res.status)) {
      if (res.status === "unpaired") setPhase("pairing");
      setEchoError("Couldn’t save that. Tap to retry.");
      return;
    }
    setEcho(null);
    if (res.status === "ok" && res.suggestion) {
      const record = res.suggestion;
      if (record.action?.type === "recipe_import") {
        setImportingRecipe({
          label: record.doc_name || echo.doc_name,
          result: suggestionToRecipeResult(record),
        });
      } else {
        setImporting({
          label: record.doc_name || echo.doc_name,
          result: suggestionToIngestResult(record),
        });
      }
    }
  }, [echo]);

  const fileEcho = useCallback(async () => {
    if (!echo) return;
    setEchoError(null);
    const res = await dismissSuggestion(echo.id);
    if (!isResolved(res.status)) {
      if (res.status === "unpaired") setPhase("pairing");
      setEchoError("Couldn’t file that. Tap to retry.");
      return;
    }
    setEcho(null);
  }, [echo]);

  const pick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file
    if (file) upload(file);
  };

  const busy = send.kind === "uploading";

  /* ── shared surfaces ────────────────────────────────────────────────── */
  const card: React.CSSProperties = {
    background: "var(--card)",
    border: "1px solid var(--line)",
    borderRadius: 24,
    boxShadow: "var(--shadow)",
    backdropFilter: "blur(18px)",
    WebkitBackdropFilter: "blur(18px)",
  };

  return (
    <div className="mx-auto w-full max-w-lg">
      {/* Masthead */}
      <header className="mb-4 flex items-baseline justify-between px-1">
        <h1
          className="text-2xl sm:text-3xl"
          style={{
            fontFamily: "var(--font-fredoka)",
            fontWeight: 600,
            color: "var(--text)",
            letterSpacing: "-0.01em",
          }}
        >
          Send to Puff
        </h1>
        <span
          className="text-[11px] uppercase tracking-[0.18em]"
          style={{ color: "var(--text3)", fontFamily: "var(--font-jbmono)" }}
        >
          {phase === "ready" ? "paired" : phase === "pairing" ? "locked" : "…"}
        </span>
      </header>

      {phase === "checking" && (
        <div style={card} className="h-40 animate-pulse" aria-busy="true" />
      )}

      {/* ── Pairing gate ─────────────────────────────────────────────── */}
      {phase === "pairing" && (
        <section style={card} className="p-5">
          <p className="text-sm" style={{ color: "var(--text2)" }}>
            Enter the household code shown on Puff&apos;s screen.
          </p>

          <input
            value={code}
            onChange={(e) => {
              setCode(formatCode(e.target.value));
              setPairError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitPair();
            }}
            inputMode="text"
            autoCapitalize="characters"
            autoCorrect="off"
            spellCheck={false}
            autoComplete="off"
            placeholder="XXXX-XXXX"
            aria-label="Household pairing code"
            aria-invalid={pairError === "invalid"}
            className="mt-4 w-full text-center outline-none"
            style={{
              fontFamily: "var(--font-jbmono)",
              fontSize: 26,
              letterSpacing: "0.18em",
              padding: "14px 10px",
              borderRadius: 16,
              background: "var(--card2)",
              color: "var(--text)",
              border: `1px solid ${
                pairError === "invalid" ? "var(--puff-red)" : "var(--line)"
              }`,
            }}
          />

          <button
            onClick={submitPair}
            disabled={pairing || cooldown > 0 || code.length < 8}
            className="mt-3 w-full transition disabled:opacity-45"
            style={{
              padding: "15px 12px",
              borderRadius: 16,
              border: "1px solid transparent",
              background:
                "linear-gradient(135deg, var(--pink), var(--magenta))",
              color: "#fff",
              fontFamily: "var(--font-fredoka)",
              fontSize: 17,
              fontWeight: 600,
            }}
          >
            {cooldown > 0
              ? `Wait ${cooldown}s`
              : pairing
                ? "Pairing…"
                : "Pair this device"}
          </button>

          {pairError && (
            <p
              role="alert"
              className="mt-3 text-center text-sm"
              style={{ color: "var(--puff-red)" }}
            >
              {pairError === "invalid" && "Wrong code."}
              {pairError === "cooldown" && "Too many tries."}
              {pairError === "cross_origin" && "Open this page from Puff’s own address."}
              {pairError === "network" && "Can’t reach Puff."}
            </p>
          )}
        </section>
      )}

      {/* ── Send UI ──────────────────────────────────────────────────── */}
      {phase === "ready" && (
        <>
          {/* Zone toggle — same semantics as the knowledge page */}
          <section style={card} className="mb-3 p-1.5">
            <div className="grid grid-cols-2 gap-1.5">
              {(
                [
                  { z: 2 as Zone, label: "General knowledge", accent: "var(--sky)" },
                  { z: 1 as Zone, label: "Personal vault", accent: "var(--lav)" },
                ] as const
              ).map(({ z, label, accent }) => {
                const on = zone === z;
                return (
                  <button
                    key={z}
                    onClick={() => setZone(z)}
                    aria-pressed={on}
                    className="transition"
                    style={{
                      padding: "10px 8px",
                      borderRadius: 18,
                      fontSize: 13,
                      fontWeight: 600,
                      border: `1px solid ${on ? accent : "transparent"}`,
                      background: on
                        ? `color-mix(in srgb, ${accent} 16%, transparent)`
                        : "transparent",
                      color: on ? accent : "var(--text2)",
                    }}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </section>

          {/* Three ways in */}
          <section style={card} className="overflow-hidden">
            {(
              [
                { icon: "photo_camera", label: "Take a photo", ref: cameraRef },
                { icon: "image", label: "Photo library", ref: galleryRef },
                { icon: "description", label: "Choose a file", ref: fileRef },
              ] as const
            ).map(({ icon, label, ref }, i) => (
              <button
                key={label}
                onClick={() => ref.current?.click()}
                disabled={busy}
                className="flex w-full items-center gap-4 px-5 py-5 text-left transition disabled:opacity-40"
                style={{
                  borderTop: i === 0 ? undefined : "1px solid var(--line)",
                  color: "var(--text)",
                }}
              >
                <MsIcon name={icon} size={26} style={{ color: "var(--pink)" }} />
                <span style={{ fontFamily: "var(--font-fredoka)", fontSize: 17 }}>
                  {label}
                </span>
                <MsIcon
                  name="chevron_right"
                  size={20}
                  style={{ marginLeft: "auto", color: "var(--text3)" }}
                />
              </button>
            ))}
          </section>

          {/* Hidden pickers */}
          <input
            ref={cameraRef}
            type="file"
            accept="image/*"
            capture="environment"
            className="hidden"
            onChange={pick}
          />
          <input
            ref={galleryRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={pick}
          />
          <input
            ref={fileRef}
            type="file"
            accept={FILE_ACCEPT}
            className="hidden"
            onChange={pick}
          />

          {/* Status */}
          {send.kind !== "idle" && (
            <section style={card} className="mt-3 p-5" aria-live="polite">
              <div className="flex items-baseline justify-between gap-3">
                <span
                  className="truncate text-sm"
                  style={{ color: "var(--text)" }}
                  title={send.name}
                >
                  {send.name}
                </span>
                {send.kind === "uploading" && (
                  <span
                    className="text-xs"
                    style={{
                      color: "var(--text3)",
                      fontFamily: "var(--font-jbmono)",
                    }}
                  >
                    {send.pct}%
                  </span>
                )}
              </div>

              {send.kind === "uploading" && (
                <div
                  className="mt-3 h-1.5 w-full overflow-hidden rounded-full"
                  style={{ background: "var(--chip)" }}
                  role="progressbar"
                  aria-valuenow={send.pct}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div
                    className="h-full rounded-full transition-[width]"
                    style={{
                      width: `${send.pct}%`,
                      background:
                        "linear-gradient(90deg, var(--sky), var(--pink))",
                    }}
                  />
                </div>
              )}

              {send.kind === "done" && (
                <p className="mt-2 text-sm" style={{ color: "var(--mint)" }}>
                  Puff has it. Indexing now.
                </p>
              )}

              {/* Reading shimmer: present only while there's still a chance
                  of an answer, and it leaves without comment. */}
              {send.kind === "done" && watching && !echo && (
                <p
                  className="mt-1 animate-pulse text-xs"
                  style={{ color: "var(--text3)" }}
                >
                  Puff is reading it…
                </p>
              )}

              {send.kind === "failed" && (
                <div className="mt-2 flex items-center justify-between gap-3">
                  <p className="text-sm" style={{ color: "var(--puff-red)" }}>
                    {send.message}
                  </p>
                  {send.retryable && (
                    <button
                      onClick={() => lastFile.current && upload(lastFile.current)}
                      className="shrink-0"
                      style={{
                        padding: "7px 14px",
                        borderRadius: 12,
                        border: "1px solid var(--line)",
                        background: "var(--card2)",
                        color: "var(--text)",
                        fontSize: 13,
                        fontWeight: 600,
                      }}
                    >
                      Retry
                    </button>
                  )}
                </div>
              )}
            </section>
          )}

          {/* Puff's offer about what just landed */}
          {echo && (
            <section style={card} className="mt-3 p-5" aria-live="polite">
              <p className="text-sm" style={{ color: "var(--text)" }}>
                {echo.message}
              </p>
              {/* Honest failure: the offer stays, and says why it stayed. */}
              {echoError && (
                <p
                  role="alert"
                  className="mt-2 text-xs"
                  style={{ color: "var(--text3)" }}
                >
                  {echoError}
                </p>
              )}
              <div className="mt-4 flex items-center justify-end gap-2">
                {echo.action?.type === "calendar_import" ||
                echo.action?.type === "recipe_import" ? (
                  <>
                    <button
                      onClick={() => void fileEcho()}
                      style={{
                        padding: "9px 16px",
                        borderRadius: 14,
                        border: "1px solid var(--line)",
                        background: "var(--card2)",
                        color: "var(--text2)",
                        fontSize: 14,
                        fontWeight: 600,
                      }}
                    >
                      Just file it
                    </button>
                    <button
                      onClick={() => void showEcho()}
                      disabled={accepting}
                      className="transition disabled:opacity-45"
                      style={{
                        padding: "9px 18px",
                        borderRadius: 14,
                        border: "1px solid transparent",
                        background:
                          "linear-gradient(135deg, var(--pink), var(--magenta))",
                        color: "#fff",
                        fontFamily: "var(--font-fredoka)",
                        fontSize: 15,
                        fontWeight: 600,
                      }}
                    >
                      {accepting ? "Opening…" : "Show me"}
                    </button>
                  </>
                ) : (
                  <button
                    onClick={() => void fileEcho()}
                    style={{
                      padding: "9px 18px",
                      borderRadius: 14,
                      border: "1px solid var(--line)",
                      background: "var(--card2)",
                      color: "var(--text)",
                      fontSize: 14,
                      fontWeight: 600,
                    }}
                  >
                    OK
                  </button>
                )}
              </div>
            </section>
          )}

          {/* Accepted: the dashboard's own per-event confirm list */}
          {importing && (
            <ImportEvents
              runAction={calRunAction}
              initialResult={importing.result}
              initialLabel={importing.label}
              onClose={() => setImporting(null)}
            />
          )}

          {/* Accepted: the per-recipe confirm list */}
          {importingRecipe && (
            <ImportRecipes
              runAction={freshRunAction}
              initialResult={importingRecipe.result}
              initialLabel={importingRecipe.label}
              onClose={() => setImportingRecipe(null)}
            />
          )}

          {/* No iPhone Share-Sheet panel. It minted an upload-only bearer
              token at /api/pair/device-token, and this build's pairing stack
              has no such handler — so the panel listed zero tokens and its
              Create button posted into a 404. Uploading from this page works;
              the Apple Shortcut path needs a token endpoint first. */}
        </>
      )}
    </div>
  );
}
