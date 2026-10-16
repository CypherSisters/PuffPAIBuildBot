"use client";

/* ═══════════════════════════════════════════════════════════════
   MY DOCUMENTS — the user's ACTUAL uploads.

   The Library tab surfaces uploads on a shelf next to ZIM packs; this
   tab is the dedicated home for "everything you've sent Puff". It reads
   the same canonical /knowledge pipeline (getLibraries) and filters down
   to the rows that are genuine user uploads — never the offline ZIM
   knowledge packs.

   Self-contained: fetches its own data, owns its own upload / view /
   delete / search state. Deletes are WRITES and need a paired device;
   an unpaired browser gets a friendly "pair to delete" line, not a
   raw 401.
   ═══════════════════════════════════════════════════════════════ */

import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  type CSSProperties,
} from "react";
import MsIcon from "../../_components/MsIcon";
import {
  getLibraries,
  deleteLibrary,
  uploadDocument,
  librarySourceEmbedUrl,
  getLibrarySourceText,
} from "../../_lib/services/knowledgeService";
import type { KnowledgeLibrary } from "../../_interfaces/api";
import { zoneBadge } from "./libraryMeta";
import { usePendingSuggestions } from "../../_lib/hooks/usePendingSuggestions";

/* ── shell vocabulary: glass panel + pill button, replacing the admin
   template components this file used to import ── */

const NUNITO = "var(--font-nunito), sans-serif";

const panelBase: CSSProperties = {
  borderRadius: 28,
  border: "1px solid var(--line)",
  background: "var(--card)",
  backdropFilter: "blur(18px)",
  WebkitBackdropFilter: "blur(18px)",
  padding: "20px 24px",
  boxShadow: "var(--shadow)",
};

function Panel({
  children,
  className,
  hoverable,
  style,
}: {
  children: React.ReactNode;
  className?: string;
  hoverable?: boolean;
  style?: CSSProperties;
}) {
  return (
    <div
      className={`${hoverable ? "transition-transform hover:-translate-y-0.5" : ""} ${className ?? ""}`}
      style={{ ...panelBase, ...style }}
    >
      {children}
    </div>
  );
}

function PillButton({
  label,
  icon,
  onClick,
  disabled,
  tone = "primary",
}: {
  label: string;
  icon?: string;
  onClick: () => void;
  disabled?: boolean;
  tone?: "primary" | "quiet" | "danger";
}) {
  const bg =
    tone === "primary"
      ? "linear-gradient(140deg,var(--pink),var(--magenta))"
      : tone === "danger"
        ? "color-mix(in srgb, var(--puff-red) 16%, transparent)"
        : "var(--card2)";
  const fg =
    tone === "primary" ? "#fff" : tone === "danger" ? "var(--puff-red)" : "var(--text2)";
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex cursor-pointer items-center gap-2 transition-transform hover:-translate-y-0.5 disabled:cursor-default disabled:opacity-50"
      style={{
        height: 40,
        padding: "0 18px",
        borderRadius: 99,
        border: tone === "primary" ? "1px solid transparent" : "1px solid var(--line)",
        background: bg,
        color: fg,
        fontFamily: NUNITO,
        fontWeight: 700,
        fontSize: 14,
      }}
    >
      {icon && <MsIcon name={icon} size={17} />}
      {label}
    </button>
  );
}

/* ── Which /knowledge rows are genuine user uploads ───────────────
   A doc is a user upload when is_upload, or content_type "uploads",
   or it carries an uploaded_at timestamp. ZIM knowledge packs are
   never uploads — exclude source "zim" and the kiwix_* collections. */
function isUserUpload(lib: KnowledgeLibrary): boolean {
  if (lib.source === "zim") return false;
  if ((lib.collection || "").toLowerCase().startsWith("kiwix_")) return false;
  return (
    lib.is_upload === true ||
    lib.content_type === "uploads" ||
    !!lib.uploaded_at
  );
}

/* Epoch ms for sorting, 0 when neither date parses. */
function uploadedTime(lib: KnowledgeLibrary): number {
  const raw = lib.uploaded_at || lib.added || "";
  const t = Date.parse(raw);
  return Number.isFinite(t) ? t : 0;
}

function fileExt(name: string): string {
  return (name || "").toLowerCase().split(".").pop() || "";
}

const IMAGE_EXT = ["jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp", "svg"];
const SHEET_EXT = ["xls", "xlsx", "csv", "tsv"];
const WORD_EXT = ["doc", "docx", "rtf", "odt"];

/** Broad type from the server's content type first, filename as a fallback. */
function docKind(lib: KnowledgeLibrary): "image" | "pdf" | "word" | "sheet" | "text" {
  const ct = (lib.source_content_type || "").toLowerCase();
  const ext = fileExt(lib.name);
  if (ct.includes("image") || IMAGE_EXT.includes(ext)) return "image";
  if (ct.includes("pdf") || ext === "pdf") return "pdf";
  if (
    ct.includes("word") ||
    ct.includes("msword") ||
    ct.includes("officedocument.wordprocessing") ||
    WORD_EXT.includes(ext)
  )
    return "word";
  if (
    ct.includes("sheet") ||
    ct.includes("excel") ||
    ct.includes("csv") ||
    SHEET_EXT.includes(ext)
  )
    return "sheet";
  return "text";
}

/** Material Symbols ligature name for a document kind. */
function kindIcon(kind: ReturnType<typeof docKind>): string {
  switch (kind) {
    case "image":
      return "image";
    case "pdf":
      return "picture_as_pdf";
    case "word":
      return "description";
    case "sheet":
      return "table_chart";
    default:
      return "description";
  }
}

function formatDate(lib: KnowledgeLibrary): string {
  const raw = lib.uploaded_at || lib.added || "";
  if (!raw) return "";
  const t = Date.parse(raw);
  if (!Number.isFinite(t)) return "";
  return new Date(t).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/* Same allow-list the Library tab uses. */
const ALLOWED_EXT = [
  "txt", "md", "pdf", "json", "csv", "tsv",
  "docx", "doc", "pptx", "rtf", "xlsx", "xls",
  "html", "xml", "yaml", "yml",
  "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp",
  "py", "js", "ts", "log", "ini", "cfg", "toml",
];
const ACCEPT_ATTR = ALLOWED_EXT.map((e) => `.${e}`).join(",");

/* ═══════════════════════════════════════════════════════════════
   VIEW MODAL — shows the original a user sent.
   ═══════════════════════════════════════════════════════════════ */

function ViewModal({
  library,
  onClose,
}: {
  library: KnowledgeLibrary;
  onClose: () => void;
}) {
  const kind = docKind(library);
  const srcUrl = librarySourceEmbedUrl(library.collection);
  // Only image/pdf have a viewable original; if the backend says the source
  // isn't retained, fall back to reconstructed text.
  const hasSource = library.source_available !== false;
  const asImage = kind === "image" && hasSource;
  const asPdf = kind === "pdf" && hasSource;
  const asText = !asImage && !asPdf;

  const [text, setText] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(asText);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!asText) return;
    let active = true;
    setLoading(true);
    setFailed(false);
    getLibrarySourceText(library.collection).then((res) => {
      if (!active) return;
      if (res) {
        setText(res.text);
        setNote(res.note);
      } else {
        setFailed(true);
      }
      setLoading(false);
    });
    return () => {
      active = false;
    };
  }, [asText, library.collection]);

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" }}
      onClick={onClose}
    >
      <div
        className="glass-card gloss-highlight flex flex-col"
        style={{
          borderRadius: 20,
          width: "min(920px, 100%)",
          maxHeight: "90vh",
          overflow: "hidden",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          className="flex items-center gap-3 px-5 py-4"
          style={{ borderBottom: "1px solid var(--line)" }}
        >
          <span style={{ color: "var(--pink)" }}>
            <MsIcon name={kindIcon(kind)} size={22} />
          </span>
          <div className="min-w-0 flex-1">
            <div
              className="truncate text-sm font-semibold"
              style={{ color: "var(--text)", fontFamily: "var(--font-fredoka)" }}
              title={library.name}
            >
              {library.name}
            </div>
          </div>
          <a
            href={srcUrl}
            download
            className="flex items-center gap-1 text-xs"
            style={{ color: "var(--pink)" }}
            title="Download original"
          >
            <MsIcon name="download" size={14} />
            Download
          </a>
          <button onClick={onClose} title="Close" aria-label="Close">
            <MsIcon name="close" size={20} className="opacity-60 hover:opacity-100" />
          </button>
        </div>

        {/* Body */}
        <div className="overflow-auto p-4" style={{ background: "var(--card2)" }}>
          {asImage && (
            <ImageOrFallback
              src={srcUrl}
              alt={library.name}
              collection={library.collection}
            />
          )}

          {asPdf && (
            <div
              className="overflow-hidden"
              style={{ borderRadius: 12, background: "#000" }}
            >
              <iframe
                src={srcUrl}
                title={library.name}
                className="w-full"
                style={{ height: "72vh", border: "none" }}
              />
            </div>
          )}

          {asText && loading && (
            <div className="flex items-center justify-center py-12">
              <MsIcon name="progress_activity" size={22} className="animate-spin" style={{ color: "var(--pink)" }} />
              <span className="ml-3 text-sm" style={{ color: "var(--text2)" }}>
                Loading document…
              </span>
            </div>
          )}

          {asText && !loading && (failed || text === null) && (
            <div className="py-10 text-center">
              <MsIcon name="error" size={34} className="mx-auto mb-3"
                style={{ color: "var(--lemon)" }} />
              <p className="text-sm" style={{ color: "var(--text2)" }}>
                The original for this document isn&rsquo;t available to preview.
              </p>
            </div>
          )}

          {asText && !loading && !failed && text !== null && (
            <>
              {note && (
                <div
                  className="mb-4 rounded-lg p-3 text-xs"
                  style={{
                    background: "var(--chip)",
                    border: "1px solid var(--line)",
                    color: "var(--text2)",
                  }}
                >
                  {note}
                </div>
              )}
              {text.trim() ? (
                <div
                  className="whitespace-pre-wrap text-sm leading-relaxed"
                  style={{ color: "var(--text)" }}
                >
                  {text}
                </div>
              ) : (
                <p className="py-10 text-center text-sm" style={{ color: "var(--text3)" }}>
                  This document has no readable text content.
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/* Image with a graceful fallback: if the original file fails to load
   (source stripped, decode error), show a quiet message instead of a
   broken-image glyph. */
function ImageOrFallback({
  src,
  alt,
  collection,
}: {
  src: string;
  alt: string;
  collection: string;
}) {
  const [broken, setBroken] = useState(false);
  if (broken) {
    return (
      <div className="py-10 text-center">
        <MsIcon name="image" size={34} className="mx-auto mb-3"
          style={{ color: "var(--text3)" }} />
        <p className="text-sm" style={{ color: "var(--text2)" }}>
          Couldn&rsquo;t load this image.
        </p>
        <a
          href={src}
          download
          className="mt-2 inline-block text-xs"
          style={{ color: "var(--pink)" }}
        >
          Download {collection}
        </a>
      </div>
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      onError={() => setBroken(true)}
      style={{
        maxWidth: "100%",
        height: "auto",
        display: "block",
        margin: "0 auto",
        borderRadius: 12,
      }}
    />
  );
}

/* ═══════════════════════════════════════════════════════════════
   DOCUMENT CARD
   ═══════════════════════════════════════════════════════════════ */

function DocCard({
  lib,
  onView,
  onDelete,
}: {
  lib: KnowledgeLibrary;
  onView: () => void;
  onDelete: () => void;
}) {
  const kind = docKind(lib);
  const zone = zoneBadge(lib.zone);
  const date = formatDate(lib);
  const chunks = Number(lib.chunk_count);
  const hasChunks = Number.isFinite(chunks) && chunks > 0;

  return (
    <Panel hoverable>
      <div className="flex items-start gap-3">
        <div
          className="flex h-11 w-11 flex-shrink-0 items-center justify-center"
          style={{
            borderRadius: 12,
            background: "color-mix(in srgb, var(--pink) 14%, transparent)",
            border: "1px solid color-mix(in srgb, var(--pink) 26%, transparent)",
            color: "var(--pink)",
          }}
        >
          <MsIcon name={kindIcon(kind)} size={22} />
        </div>

        <div className="min-w-0 flex-1">
          <div
            className="truncate text-sm font-semibold"
            style={{ color: "var(--text)", fontFamily: "var(--font-fredoka)" }}
            title={lib.name}
          >
            {lib.name}
          </div>

          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs">
            {date && <span style={{ color: "var(--text2)" }}>{date}</span>}
            {date && hasChunks && (
              <span style={{ color: "var(--text3)" }}>·</span>
            )}
            {hasChunks && (
              <span style={{ color: "var(--text2)" }}>
                {chunks.toLocaleString()} passage{chunks === 1 ? "" : "s"} indexed
              </span>
            )}
            <span className={`kb-zone-pill ${zone.cls}`}>{zone.short}</span>
          </div>

          <div className="mt-3 flex items-center gap-2">
            <button
              onClick={onView}
              className="inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-semibold transition"
              style={{
                background: "var(--card2)",
                border: "1px solid var(--line)",
                color: "var(--text)",
              }}
            >
              <MsIcon name="visibility" size={14} />
              View
            </button>
            <button
              onClick={onDelete}
              className="inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs font-semibold transition"
              style={{
                background: "transparent",
                border: "1px solid var(--line)",
                color: "var(--puff-red)",
              }}
            >
              <MsIcon name="delete" size={14} />
              Delete
            </button>
          </div>
        </div>
      </div>
    </Panel>
  );
}

/* ═══════════════════════════════════════════════════════════════
   MAIN
   ═══════════════════════════════════════════════════════════════ */

export default function MyDocuments() {
  const [libraries, setLibraries] = useState<KnowledgeLibrary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [query, setQuery] = useState("");

  const [uploadZone, setUploadZone] = useState<1 | 2>(2);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [viewTarget, setViewTarget] = useState<KnowledgeLibrary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeLibrary | null>(null);
  const [deleting, setDeleting] = useState(false);

  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const showToast = (message: string, type: "success" | "error") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  // Read-only pending-suggestions strip. Self-scheduling, backs off + goes
  // silent on an unpaired device. FloatingPuff owns accept/dismiss — here we
  // only surface that Puff has a question.
  const { suggestions } = usePendingSuggestions(true);

  const fetchDocs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getLibraries();
      setLibraries(data);
    } catch (err: any) {
      setError(err?.message || "Knowledge service unavailable");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDocs();
  }, [fetchDocs]);

  const uploads = useMemo(
    () => libraries.filter(isUserUpload).sort((a, b) => uploadedTime(b) - uploadedTime(a)),
    [libraries]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return uploads;
    return uploads.filter((l) => l.name.toLowerCase().includes(q));
  }, [uploads, query]);

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    const file = files[0];
    const ext = fileExt(file.name);
    if (!ext || !ALLOWED_EXT.includes(ext)) {
      showToast(`Unsupported file type .${ext}.`, "error");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    setUploading(true);
    setUploadProgress(`Indexing ${file.name}…`);
    try {
      const res = await uploadDocument(file, uploadZone);
      if (res.success) {
        showToast(
          `"${file.name}" added to ${uploadZone === 1 ? "Vault" : "General"}`,
          "success"
        );
        await fetchDocs();
      } else {
        showToast("Upload failed. Please try again.", "error");
      }
    } catch (err: any) {
      showToast(err?.message || "Upload failed", "error");
    } finally {
      setUploading(false);
      setUploadProgress(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleDelete = async (lib: KnowledgeLibrary) => {
    setDeleting(true);
    try {
      const res = await deleteLibrary(lib.name);
      if (res.success) {
        setLibraries((prev) => prev.filter((l) => l.name !== lib.name));
        showToast(`Removed "${lib.name}"`, "success");
      } else {
        showToast("Delete failed. Please try again.", "error");
      }
      setDeleteTarget(null);
    } catch (err: any) {
      // apiDelete throws on non-2xx; an unpaired device answers 401
      // {"error":"pairing_required"}. Show the friendly pairing line.
      const msg = String(err?.message || "");
      if (msg.toLowerCase().includes("pair")) {
        showToast("Pair this device to delete documents.", "error");
      } else {
        showToast(msg || "Delete failed", "error");
      }
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="pb-10">
      {/* Toast */}
      {toast && (
        <div
          className="fixed top-4 right-4 z-[60] flex items-center gap-2 rounded-lg px-4 py-3 text-sm font-medium text-(--puff-text-strong) shadow-lg"
          style={{ background: toast.type === "success" ? "#16a34a" : "#dc2626" }}
        >
          <MsIcon name={toast.type === "success" ? "check_circle" : "error"} size={18} />
          {toast.message}
        </div>
      )}

      {/* View modal */}
      {viewTarget && (
        <ViewModal library={viewTarget} onClose={() => setViewTarget(null)} />
      )}

      {/* Delete confirm modal */}
      {deleteTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" }}
        >
          <Panel className="w-full max-w-md" style={{ background: "var(--puff-surface-deep)" }}>
            <h3
              className="mb-2 text-lg font-semibold"
              style={{ color: "var(--text)", fontFamily: "var(--font-fredoka)" }}
            >
              Remove document
            </h3>
            <p className="mb-4 text-sm" style={{ color: "var(--text2)" }}>
              Remove <strong style={{ color: "var(--text)" }}>{deleteTarget.name}</strong>{" "}
              and its indexed passages? This cannot be undone.
            </p>
            <div className="flex justify-end gap-2">
              <PillButton
                label="Cancel"
                icon="close"
                tone="quiet"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
              />
              <PillButton
                label={deleting ? "Removing…" : "Remove"}
                icon="delete"
                tone="danger"
                onClick={() => handleDelete(deleteTarget)}
                disabled={deleting}
              />
            </div>
          </Panel>
        </div>
      )}

      {/* ── Pending suggestions strip ── */}
      {suggestions.length > 0 && (
        <div
          className="mb-5 flex items-start gap-3 p-4"
          style={{
            borderRadius: 18,
            background: "color-mix(in srgb, var(--pink) 8%, var(--card2))",
            border: "1px solid color-mix(in srgb, var(--pink) 28%, var(--line))",
          }}
        >
          <span style={{ color: "var(--pink)" }} className="mt-0.5 flex-shrink-0">
            <MsIcon name="contact_support" size={20} />
          </span>
          <div className="min-w-0 flex-1">
            <div
              className="text-sm font-semibold"
              style={{ color: "var(--text)", fontFamily: "var(--font-fredoka)" }}
            >
              Puff has {suggestions.length === 1 ? "a question" : "questions"} about
              something you sent
            </div>
            <ul className="mt-1 space-y-0.5">
              {suggestions.slice(0, 3).map((s) => (
                <li key={s.id} className="truncate text-xs" style={{ color: "var(--text2)" }}>
                  {s.message}
                </li>
              ))}
            </ul>
            <div className="mt-1 text-xs" style={{ color: "var(--text3)" }}>
              Open Puff on the dashboard to review.
            </div>
          </div>
        </div>
      )}

      {/* ── Header ── */}
      <div className="mb-5">
        <h1
          className="flex items-center gap-2 text-2xl font-bold"
          style={{ color: "var(--text)", fontFamily: "var(--font-fredoka)" }}
        >
          <MsIcon name="description" size={26} style={{ color: "var(--pink)" }} />
          Your Documents
          {!loading && (
            <span
              className="rounded-full px-2.5 py-0.5 text-sm font-semibold"
              style={{ background: "var(--chip)", color: "var(--text2)" }}
            >
              {uploads.length}
            </span>
          )}
        </h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text2)" }}>
          Everything you&rsquo;ve sent Puff: photos, PDFs, notes. Indexed and searchable.
        </p>
      </div>

      {/* ── Add + search row ── */}
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="inline-flex items-center gap-1.5 rounded-full px-4 py-2 text-sm font-semibold text-(--puff-text-strong) transition hover:scale-[1.02] disabled:opacity-60"
            style={{
              background: "linear-gradient(140deg, var(--pink), var(--magenta))",
              boxShadow: "0 10px 26px var(--glow1)",
            }}
          >
            <MsIcon name={uploading ? "progress_activity" : "add"} size={16} className={uploading ? "puff-motion animate-pulse" : ""} />
            {uploading ? uploadProgress || "Uploading…" : "Add a document"}
          </button>

          {/* Zone toggle — mirrors the Library tab (General default = zone 2) */}
          <div
            className="inline-flex overflow-hidden"
            style={{ borderRadius: 9999, border: "1px solid var(--line)" }}
          >
            {([2, 1] as const).map((z) => {
              const on = uploadZone === z;
              return (
                <button
                  key={z}
                  onClick={() => setUploadZone(z)}
                  className="px-3 py-1.5 text-xs font-semibold transition"
                  style={{
                    background: on ? "var(--pink)" : "transparent",
                    color: on ? "#fff" : "var(--text2)",
                  }}
                  title={z === 1 ? "PIN-gated, encrypted" : "Open access"}
                >
                  {z === 1 ? "Vault" : "General"}
                </button>
              );
            })}
          </div>

          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            accept={ACCEPT_ATTR}
            onChange={(e) => handleUpload(e.target.files)}
            disabled={uploading}
          />
        </div>

        {/* Filter box */}
        {uploads.length > 0 && (
          <div className="flex items-center gap-2" style={{ minWidth: 220 }}>
            <MsIcon name="search" size={18} style={{ color: "var(--text3)" }} />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by name…"
              className="kb-viewer-search"
            />
            {query && (
              <button onClick={() => setQuery("")} title="Clear">
                <MsIcon name="close" size={16} className="opacity-60 hover:opacity-100" />
              </button>
            )}
          </div>
        )}
      </div>

      <p className="mb-6 -mt-3 text-xs" style={{ color: "var(--text3)" }}>
        <MsIcon name="smartphone" size={13} className="mr-1 inline-block align-[-2px]" />
        Or send from your phone at <span style={{ color: "var(--text2)" }}>/send</span>.
      </p>

      {/* ── States ── */}
      {loading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Panel key={i}>
              <div className="flex items-start gap-3">
                <div
                  className="h-11 w-11 flex-shrink-0 animate-pulse"
                  style={{ borderRadius: 12, background: "var(--chip)" }}
                />
                <div className="flex-1 space-y-2">
                  <div className="h-4 w-3/4 animate-pulse rounded" style={{ background: "var(--chip)" }} />
                  <div className="h-3 w-1/2 animate-pulse rounded" style={{ background: "var(--chip)" }} />
                  <div className="h-6 w-24 animate-pulse rounded-full" style={{ background: "var(--chip)" }} />
                </div>
              </div>
            </Panel>
          ))}
        </div>
      ) : error ? (
        <Panel>
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <MsIcon name="error" size={46} className="mb-3" style={{ color: "var(--puff-red)" }} />
            <h3 className="mb-1 font-semibold" style={{ color: "var(--text)" }}>
              Couldn&rsquo;t load your documents
            </h3>
            <p className="mb-4 text-sm" style={{ color: "var(--text2)" }}>{error}</p>
            <PillButton label="Retry" icon="refresh" onClick={fetchDocs} />
          </div>
        </Panel>
      ) : uploads.length === 0 ? (
        <Panel>
          <div className="flex flex-col items-center justify-center py-14 text-center">
            <MsIcon name="inbox" size={48} className="mb-3" style={{ color: "var(--text3)" }} />
            <h3 className="mb-1 font-semibold" style={{ color: "var(--text)" }}>
              No documents yet
            </h3>
            <p className="max-w-sm text-sm" style={{ color: "var(--text2)" }}>
              Send Puff a photo or file from your phone at{" "}
              <span style={{ color: "var(--text)" }}>/send</span>, or use{" "}
              <span style={{ color: "var(--text)" }}>Add a document</span> above.
            </p>
          </div>
        </Panel>
      ) : filtered.length === 0 ? (
        <Panel>
          <div className="py-10 text-center text-sm" style={{ color: "var(--text2)" }}>
            No documents match &ldquo;{query}&rdquo;.
          </div>
        </Panel>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((lib) => (
            <DocCard
              key={lib.collection || lib.name}
              lib={lib}
              onView={() => setViewTarget(lib)}
              onDelete={() => setDeleteTarget(lib)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
