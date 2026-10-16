"use client";

/**
 * /dashboard/documents — the legacy /ingest pipeline: upload a recipe, a school
 * flyer or study notes, let the appliance classify it and (optionally) file it
 * into the apps.
 *
 * DATA SOURCE VERIFIED. Every route this page calls exists in this build's
 * backend (src/puffpai/api_server.py): POST /ingest/upload, GET /ingest/list,
 * GET /ingest/search, DELETE /ingest/document/<id>, POST /ingest/reindex and
 * GET /ingest/stats. Nothing here is aimed at a route that is not served — and
 * where a call does fail, the error is shown rather than smoothed over.
 *
 * (This is a different pipeline from /dashboard/knowledge's "My Documents" tab,
 * which reads /knowledge. Both are real; this one is the older of the two.)
 *
 * Converted off the admin template (SectionMain, SectionTitleLineWithButton,
 * CardBox, Button, Icon+mdi, gray/slate `dark:` pairs, #5b4ade hexes) into the
 * design-match shell. The native `confirm()` before a delete is replaced by an
 * in-page confirmation panel, so the destructive step looks like the rest of the
 * appliance instead of a browser chrome dialog.
 */

import React, {
  useState,
  useEffect,
  useCallback,
  useRef,
  type CSSProperties,
} from "react";
import MsIcon from "@/app/_components/MsIcon";
import { uploadMyFiles, uploadSummary } from "@/app/_lib/services/myDataService";

// Same-origin proxy path only, never an env-supplied base: such a
// base can point the browser past the /api/pai proxy and so past
// the pairing gate, the path allowlist and the cross-site check.
const API_BASE = "/api/pai";

/* ── shell vocabulary ───────────────────────────────────────────────── */

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";
const JBMONO = "var(--font-jbmono), monospace";
const RED = "#FF5C7A";

const STRIPES =
  "repeating-linear-gradient(135deg, var(--chip) 0 8px, transparent 8px 16px)";

const microLabel: CSSProperties = {
  fontFamily: JBMONO,
  fontWeight: 700,
  fontSize: 11,
  letterSpacing: ".14em",
  color: "var(--text3)",
  textTransform: "uppercase",
};

const panelBase: CSSProperties = {
  borderRadius: 28,
  border: "1px solid var(--line)",
  background: "var(--card)",
  backdropFilter: "blur(18px)",
  WebkitBackdropFilter: "blur(18px)",
  padding: "22px 26px",
  boxShadow: "var(--shadow)",
};

const bodyText: CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 500,
  fontSize: 16,
  lineHeight: 1.5,
  color: "var(--text2)",
};

const fieldStyle: CSSProperties = {
  width: "100%",
  borderRadius: 14,
  border: "1px solid var(--line)",
  background: "var(--card2)",
  color: "var(--text)",
  fontFamily: NUNITO,
  fontWeight: 600,
  fontSize: 15,
  padding: "11px 14px",
  outline: "none",
};

const tintOf = (color: string, pct: number) =>
  `color-mix(in srgb, ${color} ${pct}%, transparent)`;

function PillButton({
  label,
  icon,
  onClick,
  disabled,
  tone = "primary",
  title,
}: {
  label?: string;
  icon: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: "primary" | "quiet" | "danger";
  title?: string;
}) {
  const bg =
    tone === "primary"
      ? "linear-gradient(140deg,var(--pink),var(--magenta))"
      : tone === "danger"
        ? tintOf(RED, 16)
        : "var(--card2)";
  const fg = tone === "primary" ? "#fff" : tone === "danger" ? RED : "var(--text2)";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={label ?? title}
      className="inline-flex cursor-pointer items-center justify-center gap-2 transition-transform hover:-translate-y-0.5 disabled:cursor-default disabled:opacity-50"
      style={{
        height: 42,
        padding: label ? "0 20px" : "0 12px",
        borderRadius: 99,
        border: tone === "primary" ? "1px solid transparent" : "1px solid var(--line)",
        background: bg,
        color: fg,
        fontFamily: NUNITO,
        fontWeight: 700,
        fontSize: 15,
      }}
    >
      <MsIcon name={icon} size={19} />
      {label}
    </button>
  );
}

function SubHeading({ icon, children }: { icon: string; children: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-center gap-2.5">
      <MsIcon name={icon} size={21} style={{ color: "var(--pink)" }} />
      <div
        style={{
          fontFamily: FREDOKA,
          fontWeight: 600,
          fontSize: 23,
          color: "var(--text)",
        }}
      >
        {children}
      </div>
    </div>
  );
}

/* ── types ──────────────────────────────────────────────────────────── */

interface IngestedDoc {
  id: string;
  filename: string;
  classification: string;
  indexed_at: string;
  word_count: number;
}

interface IngestStats {
  document_count: number;
  chunk_count: number;
  indexed_document_count: number;
  fully_indexed: boolean;
}

interface IngestionResult {
  success: boolean;
  file_id?: string;
  filename?: string;
  file_type?: string;
  classification?: string;
  text_length?: number;
  structured_data?: Record<string, unknown>;
  actions_taken?: string[];
  error?: string;
}

interface SearchHit {
  id: string;
  filename: string;
  classification: string;
  snippet: string;
}

const CLASSIFICATION_META: Record<
  string,
  { label: string; icon: string; color: string }
> = {
  recipe: { label: "Recipe", icon: "restaurant", color: "var(--pink)" },
  event: { label: "Event", icon: "event", color: "var(--sky)" },
  schedule: { label: "Schedule", icon: "calendar_month", color: "var(--sky)" },
  study: { label: "Study material", icon: "menu_book", color: "var(--lav)" },
  shopping_list: {
    label: "Shopping list",
    icon: "shopping_cart",
    color: "var(--mint)",
  },
  note: { label: "Note", icon: "sticky_note_2", color: "var(--lemon)" },
  unknown: { label: "Unclassified", icon: "description", color: "var(--text3)" },
};

/** Material Symbols ligature name for a filename's extension. */
function fileTypeIcon(filename: string): string {
  const ext = filename.toLowerCase().split(".").pop() || "";
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff"].includes(ext))
    return "image";
  if (ext === "pdf") return "picture_as_pdf";
  if (["doc", "docx"].includes(ext)) return "description";
  if (["xls", "xlsx", "csv"].includes(ext)) return "table_chart";
  return "description";
}

export default function DocumentsPage() {
  const [docs, setDocs] = useState<IngestedDoc[]>([]);
  const [stats, setStats] = useState<IngestStats | null>(null);
  const [reindexing, setReindexing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [hint, setHint] = useState("");
  const [autoApply, setAutoApply] = useState(true);
  const [recentResult, setRecentResult] = useState<IngestionResult | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  // Which document is awaiting a delete confirmation (replaces window.confirm).
  const [deleteTarget, setDeleteTarget] = useState<IngestedDoc | null>(null);
  const [deleting, setDeleting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadStats = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/ingest/stats`);
      const data = await res.json();
      setStats(data);
    } catch {
      // ignore — stats are non-critical
    }
  }, []);

  const loadDocs = useCallback(async () => {
    try {
      setError(null);
      const res = await fetch(`${API_BASE}/ingest/list`);
      const data = await res.json();
      setDocs(data.documents || []);
      loadStats();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load documents");
    } finally {
      setLoading(false);
    }
  }, [loadStats]);

  const handleReindex = async () => {
    setReindexing(true);
    try {
      const res = await fetch(`${API_BASE}/ingest/reindex`, { method: "POST" });
      const data = await res.json();
      if (data.success) {
        await loadStats();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Reindex failed");
    } finally {
      setReindexing(false);
    }
  };

  useEffect(() => {
    loadDocs();
  }, [loadDocs]);

  const [folderNote, setFolderNote] = useState("");

  const handleUpload = async (file: File) => {
    setUploading(true);
    setError(null);
    setRecentResult(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      if (hint) formData.append("hint", hint);
      formData.append("auto_apply", autoApply ? "true" : "false");

      const res = await fetch(`${API_BASE}/ingest/upload`, {
        method: "POST",
        body: formData,
      });
      const result: IngestionResult = await res.json();
      setRecentResult(result);
      if (result.error) {
        setError(result.error);
      }
      // Refresh the list whether or not it was a "study" doc
      await loadDocs();
      setHint("");
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (doc: IngestedDoc) => {
    setDeleting(true);
    try {
      await fetch(`${API_BASE}/ingest/document/${doc.id}`, { method: "DELETE" });
      await loadDocs();
      setDeleteTarget(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearching(true);
    try {
      const res = await fetch(
        `${API_BASE}/ingest/search?q=${encodeURIComponent(searchQuery)}`
      );
      const data = await res.json();
      setSearchResults(data.results || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed");
    } finally {
      setSearching(false);
    }
  };

  return (
    <section
      style={{
        padding:
          "clamp(16px,2.4vh,26px) clamp(18px,2vw,36px) clamp(18px,2.8vh,30px)",
        display: "flex",
        flexDirection: "column",
        gap: "clamp(14px,2vh,22px)",
      }}
    >
      {/* ─── Delete confirmation ─────────────────────────────────── */}
      {deleteTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,.6)", backdropFilter: "blur(6px)" }}
        >
          <div
            style={{
              ...panelBase,
              background: "var(--puff-surface-deep)",
              maxWidth: 440,
              width: "100%",
            }}
          >
            <h3
              style={{
                margin: "0 0 8px",
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: 23,
                color: "var(--text)",
              }}
            >
              Delete this document?
            </h3>
            <p style={{ ...bodyText, margin: "0 0 18px" }}>
              <b style={{ color: "var(--text)" }}>{deleteTarget.filename}</b> and
              everything indexed from it will be removed. This cannot be undone.
            </p>
            <div className="flex flex-wrap justify-end gap-3">
              <PillButton
                label="Keep it"
                icon="close"
                tone="quiet"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
              />
              <PillButton
                label={deleting ? "Deleting…" : "Delete"}
                icon="delete"
                tone="danger"
                onClick={() => handleDelete(deleteTarget)}
                disabled={deleting}
              />
            </div>
          </div>
        </div>
      )}

      {/* ─── Header ──────────────────────────────────────────────── */}
      <div>
        <h1
          style={{
            margin: 0,
            fontFamily: FREDOKA,
            fontWeight: 600,
            fontSize: "clamp(28px,2.6vw,44px)",
            letterSpacing: "-.8px",
            color: "var(--text)",
          }}
        >
          Your documents
        </h1>
        <p style={{ ...bodyText, margin: "6px 0 0", fontSize: 19 }}>
          Send Puff a recipe, a school flyer, your study notes. She reads them
          and files them into your apps.
        </p>
      </div>

      {/* ─── Brain status ────────────────────────────────────────── */}
      {stats && (
        <div
          className="flex flex-wrap items-center gap-4"
          style={{
            ...panelBase,
            borderRadius: 24,
            padding: "16px 22px",
            background: `linear-gradient(140deg, ${tintOf("var(--lav)", 14)}, transparent)`,
          }}
        >
          <span
            className="flex shrink-0 items-center justify-center"
            style={{
              width: 44,
              height: 44,
              borderRadius: 14,
              background: tintOf("var(--lav)", 18),
              color: "var(--lav)",
            }}
          >
            <MsIcon name="psychology" size={24} />
          </span>
          <div className="min-w-0 flex-1">
            <div
              style={{
                fontFamily: NUNITO,
                fontWeight: 700,
                fontSize: 17,
                color: "var(--text)",
              }}
            >
              {stats.indexed_document_count > 0 ? (
                <>
                  {stats.indexed_document_count} document
                  {stats.indexed_document_count === 1 ? "" : "s"} Puff can read
                  <span style={{ ...microLabel, marginLeft: 10 }}>
                    {stats.chunk_count.toLocaleString()} passages
                  </span>
                </>
              ) : (
                "Nothing indexed yet"
              )}
            </div>
            <div style={{ ...bodyText, fontSize: 15 }}>
              {stats.fully_indexed
                ? "She can find these by meaning, not just by matching words."
                : `${stats.document_count - stats.indexed_document_count} still need indexing.`}
            </div>
          </div>
          {!stats.fully_indexed && stats.document_count > 0 && (
            <PillButton
              label={reindexing ? "Indexing…" : "Index them"}
              icon={reindexing ? "progress_activity" : "refresh"}
              onClick={handleReindex}
              disabled={reindexing}
            />
          )}
        </div>
      )}

      {/* ─── Upload ──────────────────────────────────────────────── */}
      <div style={panelBase}>
        <SubHeading icon="upload">Add a document</SubHeading>

        <div className="mb-4 grid gap-4 sm:grid-cols-2">
          <div>
            <div style={{ ...microLabel, marginBottom: 6 }}>
              What is it? (optional)
            </div>
            <input
              type="text"
              value={hint}
              onChange={(e) => setHint(e.target.value)}
              placeholder="Mum's lasagna, school play flyer…"
              style={fieldStyle}
            />
          </div>
          <div className="flex items-end">
            <button
              type="button"
              role="switch"
              aria-checked={autoApply}
              onClick={() => setAutoApply(!autoApply)}
              className="flex w-full cursor-pointer items-center gap-3"
              style={{
                padding: "11px 16px",
                borderRadius: 14,
                border: "1px solid var(--line)",
                background: autoApply ? tintOf("var(--pink)", 12) : "var(--card2)",
                textAlign: "left",
              }}
            >
              <MsIcon
                name={autoApply ? "check_circle" : "radio_button_unchecked"}
                size={22}
                style={{ color: autoApply ? "var(--pink)" : "var(--text3)", flex: "none" }}
              />
              <span
                style={{
                  fontFamily: NUNITO,
                  fontWeight: 700,
                  fontSize: 15,
                  color: "var(--text)",
                }}
              >
                File it into my apps automatically
              </span>
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept=".jpg,.jpeg,.png,.gif,.bmp,.webp,.tiff,.pdf,.docx,.doc,.txt,.md,.xlsx,.xls,.csv"
            multiple
            onChange={async (e) => {
              // Many at once: one after another, each through the same checks.
              const picked = Array.from(e.target.files ?? []);
              for (const file of picked) await handleUpload(file);
            }}
            disabled={uploading}
            className="block w-full text-sm file:mr-3 file:cursor-pointer file:rounded-full file:border-0 file:px-5 file:py-2.5"
            style={{
              fontFamily: NUNITO,
              fontWeight: 600,
              color: "var(--text2)",
            }}
          />
          <label style={{ ...microLabel, fontSize: 12, cursor: uploading ? "default" : "pointer" }}>
            Or add a whole folder
            <input
              type="file"
              multiple
              hidden
              disabled={uploading}
              ref={(el) => {
                if (el) el.setAttribute("webkitdirectory", "");
              }}
              onChange={async (e) => {
                const picked = Array.from(e.target.files ?? []);
                if (!picked.length) return;
                setUploading(true);
                setError(null);
                const reply = await uploadMyFiles(picked);
                setUploading(false);
                setFolderNote(uploadSummary(reply.results));
                if (!reply.ok) setError(reply.message ?? "Not everything was added.");
                e.target.value = "";
                await loadDocs();
              }}
            />
          </label>
          {folderNote && <span style={{ color: "var(--text2)", fontSize: 13 }}>{folderNote}</span>}
          {uploading && (
            <span className="flex items-center gap-2" style={{ ...microLabel, fontSize: 12 }}>
              <MsIcon
                name="progress_activity"
                size={18}
                className="puff-motion animate-pulse"
                style={{ color: "var(--pink)" }}
              />
              Reading it
            </span>
          )}
        </div>
        <p style={{ ...microLabel, marginTop: 10, fontSize: 11 }}>
          Images (OCR when Tesseract is installed) · PDF · DOCX · TXT · XLSX/CSV
          · up to 25 MB
        </p>

        {/* Recent upload result */}
        {recentResult && (
          <div
            className="mt-4 flex items-start gap-3"
            style={{
              padding: "16px 18px",
              borderRadius: 20,
              border: `1px solid ${tintOf(
                recentResult.success ? "var(--mint)" : RED,
                40
              )}`,
              background: tintOf(recentResult.success ? "var(--mint)" : RED, 10),
            }}
          >
            <MsIcon
              name={recentResult.success ? "check_circle" : "error"}
              size={20}
              style={{
                color: recentResult.success ? "var(--mint)" : RED,
                flex: "none",
                marginTop: 1,
              }}
            />
            <div className="min-w-0 flex-1">
              <div
                style={{
                  fontFamily: NUNITO,
                  fontWeight: 700,
                  fontSize: 16,
                  color: "var(--text)",
                }}
              >
                {recentResult.filename}
              </div>
              {recentResult.classification && (
                <div style={{ ...bodyText, fontSize: 14, marginTop: 2 }}>
                  Read as <b style={{ color: "var(--text)" }}>{recentResult.classification}</b>
                  {typeof recentResult.text_length === "number" &&
                    ` · ${recentResult.text_length.toLocaleString()} characters`}
                </div>
              )}
              {recentResult.actions_taken && recentResult.actions_taken.length > 0 && (
                <ul className="mt-2 flex flex-col gap-1" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                  {recentResult.actions_taken.map((a, i) => (
                    <li
                      key={i}
                      className="flex items-center gap-2"
                      style={{
                        fontFamily: NUNITO,
                        fontWeight: 600,
                        fontSize: 14,
                        color: "var(--mint)",
                      }}
                    >
                      <MsIcon name="check" size={16} />
                      {a}
                    </li>
                  ))}
                </ul>
              )}
              {recentResult.error && (
                <div
                  style={{
                    marginTop: 6,
                    fontFamily: NUNITO,
                    fontWeight: 600,
                    fontSize: 14,
                    color: RED,
                  }}
                >
                  {recentResult.error}
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={() => setRecentResult(null)}
              aria-label="Dismiss"
              className="cursor-pointer"
              style={{ background: "none", border: "none", color: "var(--text3)", padding: 2 }}
            >
              <MsIcon name="close" size={18} />
            </button>
          </div>
        )}
      </div>

      {/* ─── Search ──────────────────────────────────────────────── */}
      <div style={panelBase}>
        <SubHeading icon="search">Search what you have sent</SubHeading>
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            placeholder="Chicken recipe, history exam, school play…"
            style={{ ...fieldStyle, flex: 1, minWidth: 220 }}
          />
          <PillButton label="Search" icon="search" onClick={handleSearch} />
        </div>

        {searching && (
          <p style={{ ...microLabel, marginTop: 12, fontSize: 12 }}>Searching</p>
        )}

        {!searching && searchResults.length > 0 && (
          <div className="mt-4 flex flex-col gap-3">
            {searchResults.map((r) => (
              <div
                key={r.id}
                style={{
                  padding: 16,
                  borderRadius: 20,
                  background: "var(--card2)",
                  border: "1px solid var(--line)",
                }}
              >
                <div className="mb-1.5 flex flex-wrap items-center gap-2">
                  <MsIcon
                    name={fileTypeIcon(r.filename)}
                    size={18}
                    style={{ color: "var(--lav)", flex: "none" }}
                  />
                  <span
                    style={{
                      fontFamily: NUNITO,
                      fontWeight: 700,
                      fontSize: 15,
                      color: "var(--text)",
                    }}
                  >
                    {r.filename}
                  </span>
                  <span style={microLabel}>{r.classification}</span>
                </div>
                <p style={{ ...bodyText, margin: 0, fontSize: 15 }}>{r.snippet}</p>
              </div>
            ))}
          </div>
        )}

        {!searching && searchQuery && searchResults.length === 0 && (
          <p style={{ ...microLabel, marginTop: 12, fontSize: 12 }}>
            Nothing matched &ldquo;{searchQuery}&rdquo;
          </p>
        )}
      </div>

      {/* ─── Document List ───────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3">
        <SubHeading icon="folder_open">Everything indexed</SubHeading>
        <div style={{ flex: 1 }} />
        <div style={{ marginBottom: 16 }}>
          <PillButton label="Refresh" icon="refresh" tone="quiet" onClick={loadDocs} />
        </div>
      </div>

      {error && (
        <div
          className="flex items-center gap-3"
          style={{
            ...panelBase,
            padding: "16px 20px",
            border: `1px solid ${tintOf(RED, 40)}`,
            background: tintOf(RED, 12),
          }}
        >
          <MsIcon name="error" size={22} style={{ color: RED, flex: "none" }} />
          <span style={{ ...bodyText, color: "var(--text2)" }}>{error}</span>
        </div>
      )}

      {loading ? (
        <div style={panelBase}>
          <div className="flex items-center justify-center gap-3 py-10">
            <MsIcon
              name="progress_activity"
              size={30}
              className="puff-motion animate-pulse"
              style={{ color: "var(--pink)" }}
            />
            <span style={microLabel}>Loading your documents</span>
          </div>
        </div>
      ) : docs.length === 0 ? (
        <div style={panelBase}>
          <div
            className="flex flex-col items-center justify-center gap-3 py-12 text-center"
            style={{ borderRadius: 18, background: STRIPES }}
          >
            <MsIcon name="folder_open" size={44} style={{ color: "var(--text3)" }} />
            <div
              style={{
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: 21,
                color: "var(--text)",
              }}
            >
              Nothing here yet
            </div>
            <span style={{ ...microLabel, fontSize: 12 }}>
              Try a recipe, a flyer, or your study notes
            </span>
          </div>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {docs.map((doc) => {
            const meta =
              CLASSIFICATION_META[doc.classification] ||
              CLASSIFICATION_META.unknown;
            return (
              <div
                key={doc.id}
                style={{ ...panelBase, borderRadius: 26, padding: 20 }}
                className="transition-transform hover:-translate-y-0.5"
              >
                <div className="mb-3 flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-3">
                    <span
                      className="flex shrink-0 items-center justify-center"
                      style={{
                        width: 42,
                        height: 42,
                        borderRadius: 14,
                        background: tintOf(meta.color, 16),
                        color: meta.color,
                      }}
                    >
                      <MsIcon name={meta.icon} size={22} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div
                        className="truncate"
                        title={doc.filename}
                        style={{
                          fontFamily: NUNITO,
                          fontWeight: 700,
                          fontSize: 16,
                          color: "var(--text)",
                        }}
                      >
                        {doc.filename}
                      </div>
                      <div style={microLabel}>
                        {meta.label} · {doc.word_count} words
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDeleteTarget(doc)}
                    aria-label={`Delete ${doc.filename}`}
                    title="Delete"
                    className="cursor-pointer"
                    style={{
                      background: "none",
                      border: "none",
                      color: "var(--text3)",
                      padding: 2,
                      flex: "none",
                    }}
                  >
                    <MsIcon name="delete" size={19} />
                  </button>
                </div>
                <div style={{ ...microLabel, fontSize: 10 }}>
                  Indexed {new Date(doc.indexed_at).toLocaleDateString()}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
