"use client";

import { useEffect, useState, type CSSProperties } from "react";
import MsIcon from "../../_components/MsIcon";
import {
  librarySourceEmbedUrl,
  getLibrarySourceText,
} from "../../_lib/services/knowledgeService";
import type { KnowledgeLibrary } from "../../_interfaces/api";

/* ═══════════════════════════════════════════════════════════════
   SOURCE VIEW — original material for user-uploaded documents.
   PDFs render in the browser-native viewer via an embedded frame;
   everything else (or a document with no retained original) shows
   the text reconstructed from the indexed chunks.

   Converted off CardBox/Icon+mdi onto the shell's glass panel and
   Material Symbols; the fetch logic and every state are unchanged.
   ═══════════════════════════════════════════════════════════════ */

const NUNITO = "var(--font-nunito), sans-serif";
const JBMONO = "var(--font-jbmono), monospace";

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

/** Download link, styled as a quiet pill like the rest of the shell. */
function DownloadLink({ url }: { url: string }) {
  return (
    <a
      href={url}
      download
      className="inline-flex items-center gap-2 no-underline transition-transform hover:-translate-y-0.5"
      style={{
        height: 38,
        padding: "0 18px",
        borderRadius: 99,
        background: "var(--card2)",
        border: "1px solid var(--line)",
        color: "var(--text2)",
        fontFamily: NUNITO,
        fontWeight: 700,
        fontSize: 14,
      }}
    >
      <MsIcon name="download" size={18} />
      Download
    </a>
  );
}

export default function SourceView({ library }: { library: KnowledgeLibrary }) {
  const contentType = (library.source_content_type || "").toLowerCase();
  const isPdf = !!library.source_available && contentType.includes("pdf");
  // Images render as an actual <img>, not reconstructed text — so "show me my
  // photo / grandma's recipe" (usually a photo) displays the picture itself.
  const isImage = !!library.source_available && contentType.startsWith("image/");

  const [text, setText] = useState<string | null>(null);
  const [note, setNote] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(!isPdf && !isImage);
  const [failed, setFailed] = useState<boolean>(false);

  useEffect(() => {
    if (isPdf || isImage) return; // embedded directly — no text fetch needed
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
  }, [isPdf, isImage, library.collection]);

  // ── Image: show the actual picture ───────────────────────────────
  if (isImage) {
    const url = librarySourceEmbedUrl(library.collection);
    return (
      <div style={panelBase}>
        <div
          className="flex items-center justify-center overflow-hidden"
          style={{ borderRadius: 20, border: "1px solid var(--line)", background: "#000" }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={library.name}
            className="max-h-[75vh] w-auto max-w-full object-contain"
          />
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <span style={microLabel}>Your original image</span>
          <DownloadLink url={url} />
        </div>
      </div>
    );
  }

  // ── PDF: embedded browser-native viewer ──────────────────────────
  if (isPdf) {
    const url = librarySourceEmbedUrl(library.collection);
    return (
      <div style={panelBase}>
        <div
          className="overflow-hidden"
          style={{ borderRadius: 20, border: "1px solid var(--line)", background: "#000" }}
        >
          <iframe src={url} title={library.name} className="h-[75vh] w-full" />
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <span style={microLabel}>Your original document</span>
          <DownloadLink url={url} />
        </div>
      </div>
    );
  }

  // ── Loading ───────────────────────────────────────────────────────
  if (loading) {
    return (
      <div style={panelBase}>
        <div className="flex items-center justify-center gap-3 py-12">
          <MsIcon
            name="progress_activity"
            size={24}
            className="puff-motion animate-pulse"
            style={{ color: "var(--pink)" }}
          />
          <span style={microLabel}>Loading document</span>
        </div>
      </div>
    );
  }

  // ── Failed ────────────────────────────────────────────────────────
  if (failed || text === null) {
    return (
      <div style={panelBase}>
        <div
          className="flex flex-col items-center gap-3 py-8 text-center"
          style={{ borderRadius: 18, background: STRIPES }}
        >
          <MsIcon name="description" size={34} style={{ color: "var(--lemon)" }} />
          <span style={{ ...microLabel, fontSize: 12 }}>
            No original kept for this one. The indexed text is all there is
          </span>
        </div>
      </div>
    );
  }

  // ── Reconstructed text ────────────────────────────────────────────
  return (
    <div style={panelBase}>
      {note && (
        <div
          className="mb-4 flex items-start gap-2.5"
          style={{
            padding: "12px 16px",
            borderRadius: 16,
            background: "var(--card2)",
            border: "1px solid var(--line)",
          }}
        >
          <MsIcon
            name="info"
            size={18}
            style={{ color: "var(--text3)", flex: "none", marginTop: 1 }}
          />
          <p
            style={{
              margin: 0,
              fontFamily: NUNITO,
              fontWeight: 500,
              fontSize: 14,
              lineHeight: 1.5,
              color: "var(--text2)",
            }}
          >
            {note}
          </p>
        </div>
      )}
      {text.trim() ? (
        <div
          className="kb-article-content whitespace-pre-wrap"
          style={{
            fontFamily: NUNITO,
            fontWeight: 500,
            fontSize: 15,
            lineHeight: 1.65,
            color: "var(--text2)",
          }}
        >
          {text}
        </div>
      ) : (
        <p className="py-8 text-center" style={{ ...microLabel, fontSize: 12 }}>
          No readable text in this document
        </p>
      )}
    </div>
  );
}
