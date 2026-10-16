"use client";

/**
 * Knowledge Base — the bookshelf, the per-library viewer, and global search.
 * Converted off the admin template (SectionMain, SectionTitleLineWithButton,
 * CardBox, CardBoxWidget, Button, Icon+mdi, slate/cyan utility colours) into the
 * design-match shell. The kb-* bookshelf classes in css/_bookshelf.css are KEPT
 * on purpose — the book spines are part of the design, not template scaffolding.
 *
 * FULL-TEXT SEARCH WAS REMOVED, NOT RESTYLED.
 *
 * This page used to run a third search pass against `/knowledge/search/fulltext`
 * and offered a per-library "full text" mode gated on `library.fts_ready`.
 * Neither exists in this build: the backend serves exactly two search routes,
 * /knowledge/search (semantic/RAG) and /knowledge/search/titles, and it never
 * emits an `fts_ready` field at all. So:
 *
 *   - the global pass 404'd on every keystroke and was swallowed by
 *     Promise.allSettled, costing a request per search and returning nothing;
 *   - `fts_ready` was always undefined, so the per-library toggle never fired —
 *     but where the UI DID claim it ("Full-text searchable" pills, "Search the
 *     full text of N indexed libraries", a "Searchable" counter that could only
 *     ever read 0), it promised a capability that is not here;
 *   - worst, the library Search tab rendered "No matches in this library" for a
 *     query it had never successfully run.
 *
 * Titles and semantic search both work and are what this page now offers, with
 * one inline note saying plainly that body-text search is not in this build. The
 * "Searchable" stat is replaced by the real indexed-chunk total.
 */

import { useState, useEffect, useCallback, useRef, type CSSProperties } from "react";
import MsIcon from "../../_components/MsIcon";
import PairDeviceCard from "../../_components/PairDeviceCard";
import {
  getLibraries,
  searchKnowledge,
  searchTitles,
  uploadDocument,
  deleteLibrary,
  browseLibrary,
  getArticle,
} from "../../_lib/services/knowledgeService";
import type { TitleSearchResult } from "../../_lib/services/knowledgeService";
import type { KnowledgeLibrary, LibraryArticle, ArticleContent } from "../../_interfaces/api";
import SourceView from "./SourceView";
// The build record's side of the story: sources chosen, downloaded, indexed.
import BuildSources from "./BuildSources";
import FindMore from "./FindMore";
import LearningCard from "./LearningCard";
import { zoneBadge, chunkCount, formatChunks, formatSize } from "./libraryMeta";
import { containerMaxW } from "../../_lib/config";
// "My Documents" tab — the user's real /knowledge uploads (canonical
// pipeline). The legacy app/dashboard/documents/page.tsx (/ingest backend)
// is left intact but no longer surfaced here.
import MyDocuments from "./MyDocuments";
// Your files: the data folder, linked folders, many files or a whole folder.
import MyData from "./MyData";

/* ═══════════════════════════════════════════════════════════════
   SHELL VOCABULARY
   ═══════════════════════════════════════════════════════════════ */

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

/** A tint of a theme token — safe for `var(--x)` values, unlike `${hex}15`. */
const tint = (color: string, pct: number) =>
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
        ? tint(RED, 16)
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

function PageHeading({
  icon,
  title,
  sub,
  children,
}: {
  icon: string;
  title: string;
  sub?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end gap-4">
      <div className="min-w-0">
        <div className="flex items-center gap-2.5">
          <MsIcon name={icon} size={26} style={{ color: "var(--pink)" }} />
          <h1
            style={{
              margin: 0,
              fontFamily: FREDOKA,
              fontWeight: 600,
              fontSize: "clamp(26px,2.4vw,40px)",
              letterSpacing: "-.7px",
              color: "var(--text)",
            }}
          >
            {title}
          </h1>
        </div>
        {sub && <p style={{ ...bodyText, margin: "6px 0 0", fontSize: 18 }}>{sub}</p>}
      </div>
      <div style={{ flex: 1 }} />
      {children}
    </div>
  );
}

function StatTile({
  label,
  value,
  note,
  icon,
  color,
}: {
  label: string;
  value: string;
  note: string;
  icon: string;
  color: string;
}) {
  return (
    <div
      style={{ ...panelBase, borderRadius: 26 }}
      className="flex items-center gap-4"
    >
      <span
        className="flex shrink-0 items-center justify-center"
        style={{
          width: 48,
          height: 48,
          borderRadius: 16,
          background: tint(color, 16),
          color,
        }}
      >
        <MsIcon name={icon} size={26} />
      </span>
      <div className="min-w-0">
        <div style={microLabel}>{label}</div>
        <div
          style={{
            fontFamily: FREDOKA,
            fontWeight: 600,
            fontSize: 26,
            lineHeight: 1.2,
            color: "var(--text)",
          }}
        >
          {value}
        </div>
        <div style={{ fontFamily: JBMONO, fontWeight: 600, fontSize: 12, color: "var(--text3)" }}>
          {note}
        </div>
      </div>
    </div>
  );
}

/** Section header inside the page body (below the H1). */
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

/** Results-group header: icon, name, count. */
function ResultsHead({
  icon,
  title,
  count,
}: {
  icon: string;
  title: string;
  count: string;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2.5">
      <MsIcon name={icon} size={18} style={{ color: "var(--pink)" }} />
      <span
        style={{
          fontFamily: FREDOKA,
          fontWeight: 600,
          fontSize: 19,
          color: "var(--text)",
        }}
      >
        {title}
      </span>
      <span style={microLabel}>{count}</span>
    </div>
  );
}

/**
 * The one place the missing capability is named. Shown next to search inputs
 * rather than as a result state, so it reads as "this build does not do that"
 * instead of "your query found nothing".
 */
function NoFulltextNote() {
  return (
    <div
      className="mt-3 flex items-start gap-2.5"
      style={{
        padding: "12px 16px",
        borderRadius: 16,
        background: STRIPES,
        border: "1px solid var(--line)",
      }}
    >
      <MsIcon name="info" size={18} style={{ color: "var(--text3)", flex: "none" }} />
      <span style={{ ...microLabel, textTransform: "none", letterSpacing: 0, fontSize: 12 }}>
        Full-text index not in this build, so searches match article titles and
        meaning, not every word of every page.
      </span>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   TYPES & CONSTANTS
   ═══════════════════════════════════════════════════════════════ */

type SearchResult = {
  text: string;
  score: number;
  source: string;
  zone: 1 | 2;
  library?: string;
};

type ViewMode = "shelves" | "viewer";
type ViewerTab = "browse" | "search" | "about";

type CategoryKey =
  | "encyclopedia"
  | "video"
  | "qa"
  | "medical"
  | "reference"
  | "survival"
  | "science"
  | "travel"
  | "books"
  | "uploads";

/* Category colours are THEME TOKENS, not hexes: the book spines inherit them
   through --book-color in css/_bookshelf.css, which reads them with
   var()/color-mix and so follows the day/night palette. */
const CATEGORIES: Record<
  CategoryKey,
  { label: string; color: string; icon: string; description: string }
> = {
  encyclopedia: {
    label: "Encyclopedias",
    color: "var(--sky)",
    icon: "public",
    description: "Wikipedia, Wikisource, and other encyclopedic references",
  },
  video: {
    label: "Video Library",
    color: "var(--pink)",
    icon: "play_circle",
    description: "TED Talks and video content. Watch and listen offline",
  },
  qa: {
    label: "Q&A Forums",
    color: "var(--mint)",
    icon: "forum",
    description: "Stack Exchange communities and expert answers",
  },
  medical: {
    label: "Medical",
    color: "var(--magenta)",
    icon: "medical_services",
    description: "Medical encyclopedias, treatment guides, and health references",
  },
  reference: {
    label: "Reference",
    color: "var(--lemon)",
    icon: "info",
    description: "Emergency preparedness, maps, and technical documentation",
  },
  survival: {
    label: "Survival & Preparedness",
    color: "var(--lemon)",
    icon: "verified_user",
    description: "Prepper guides, water treatment, and self-sufficiency resources",
  },
  science: {
    label: "Science & Education",
    color: "var(--lav)",
    icon: "science",
    description: "Physics, biology, history, and academic courses",
  },
  travel: {
    label: "Travel",
    color: "var(--mint)",
    icon: "flight",
    description: "Travel guides, maps, and destination information",
  },
  books: {
    label: "Books & Collections",
    color: "var(--lav)",
    icon: "library_books",
    description: "Interactive books, specialty collections, and unique resources",
  },
  uploads: {
    label: "Your Documents",
    color: "var(--text2)",
    icon: "description",
    description: "Files you've uploaded and indexed into the knowledge base",
  },
};

/* ═══════════════════════════════════════════════════════════════
   HELPERS
   ═══════════════════════════════════════════════════════════════ */

function categorizeLibrary(lib: KnowledgeLibrary): CategoryKey {
  const n = lib.name.toLowerCase();
  const c = (lib.collection || "").toLowerCase();

  // TED talks → video
  if (n.startsWith("ted ") || c.includes("_ted_") || c.startsWith("ted_")) return "video";

  // Medical (must come BEFORE qa & science to catch "Medical Sciences Q&A" and "Medicine LibreTexts")
  if (
    n.includes("wikem") ||
    n.includes("mdwiki") ||
    n.includes("medical") ||
    n.includes("medicine") ||
    c.includes("_medicine_") ||
    c.includes("_med_") ||
    c.includes("medicalsciences")
  )
    return "medical";

  // Stack Exchange / Q&A
  if (n.includes("q&a") || c.includes("stackexchange")) return "qa";

  // Survival / Prepper
  if (
    n.includes("prepper") ||
    n.includes("water treatment") ||
    n.includes("ready.gov")
  )
    return "survival";

  // Science & Education
  if (
    n.includes("physics") ||
    n.includes("biology") ||
    n.includes("history") ||
    n.includes("courses") ||
    c.includes("libretexts")
  )
    return "science";

  // Travel
  if (n.includes("wikivoyage")) return "travel";

  // Books & special
  if (
    n.includes("prunelle") ||
    n.includes("knots") ||
    n.includes("incognito")
  )
    return "books";

  // User uploads (has uploaded_at or zone field in raw data)
  if (lib.added && !c.startsWith("kiwix_")) return "uploads";

  // Reference (OpenStreetMap, MediaWiki, XKCD)
  if (
    n.includes("openstreetmap") ||
    n.includes("mediawiki") ||
    n.includes("xkcd") ||
    n.includes("explain")
  )
    return "reference";

  // Encyclopedias — Wikipedia, RationalWiki, Wikispecies, Wikisource, WikiSummaries
  if (
    n.includes("wikipedia") ||
    n.includes("rationalwiki") ||
    n.includes("wikispecies") ||
    n.includes("wikisource") ||
    n.includes("wikisummaries") ||
    n.includes("wiki")
  )
    return "encyclopedia";

  return "reference";
}

/** Find the shelf entry a search result belongs to. Title results key on
 *  collection; ZIM rows carry both a collection and a library_id. */
function findLibrary(
  libraries: KnowledgeLibrary[],
  id: string,
): KnowledgeLibrary | undefined {
  if (!id) return undefined;
  return libraries.find((l) => l.library_id === id || l.collection === id);
}

/* ═══════════════════════════════════════════════════════════════
   SUB-COMPONENTS
   ═══════════════════════════════════════════════════════════════ */

function BookCard({
  lib,
  category,
  onClick,
}: {
  lib: KnowledgeLibrary;
  category: CategoryKey;
  onClick: () => void;
}) {
  const cat = CATEGORIES[category];
  const zone = zoneBadge(lib.zone);
  const chunks = chunkCount(lib);
  const size = formatSize(lib.size);
  return (
    <div
      className="kb-book"
      style={{ "--book-color": cat.color } as React.CSSProperties}
      onClick={onClick}
      title={`Open ${lib.name}`}
    >
      <div className="kb-book-inner">
        <div className="kb-book-icon">
          <MsIcon name={cat.icon} size={28} />
        </div>
        <div className="kb-book-title">{lib.name}</div>
        <div className="kb-book-meta">
          <span className={`kb-zone-pill ${zone.cls}`}>{zone.short}</span>
          {chunks > 0 ? (
            <span className="kb-book-chunks">{formatChunks(chunks)} chunks</span>
          ) : size ? (
            <span className="kb-book-chunks">{size}</span>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function ShelfRow({
  category,
  libraries,
  onBookClick,
}: {
  category: CategoryKey;
  libraries: KnowledgeLibrary[];
  onBookClick: (lib: KnowledgeLibrary) => void;
}) {
  const cat = CATEGORIES[category];
  if (libraries.length === 0) return null;

  return (
    <div className="kb-shelf">
      <div
        className="kb-shelf-label"
        style={{ "--shelf-color": cat.color, color: cat.color } as React.CSSProperties}
      >
        <span className="kb-shelf-label-dot" />
        <MsIcon name={cat.icon} size={18} />
        {cat.label}
        <span
          style={{
            color: "var(--text3)",
            fontWeight: 400,
            fontSize: "0.75rem",
            letterSpacing: 0,
          }}
        >
          · {libraries.length} {libraries.length === 1 ? "volume" : "volumes"}
        </span>
      </div>
      <div className="kb-books-row">
        {libraries.map((lib) => (
          <BookCard
            key={lib.collection || lib.name}
            lib={lib}
            category={category}
            onClick={() => onBookClick(lib)}
          />
        ))}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   LIBRARY VIEWER COMPONENT
   ═══════════════════════════════════════════════════════════════ */

function LibraryViewer({
  library,
  category,
  onClose,
  initialArticleUrl,
}: {
  library: KnowledgeLibrary;
  category: CategoryKey;
  onClose: () => void;
  /** Article to open straight away — set when arriving from a search result. */
  initialArticleUrl?: string | null;
}) {
  const cat = CATEGORIES[category];
  const isVideo = category === "video";
  const isUpload = category === "uploads" || library.is_upload === true;
  const zone = zoneBadge(library.zone);
  const chunks = chunkCount(library);
  const sizeLabel = formatSize(library.size);

  const [tab, setTab] = useState<ViewerTab>("browse");
  const [articles, setArticles] = useState<LibraryArticle[]>([]);
  const [articlesLoading, setArticlesLoading] = useState(true);
  const [articlesError, setArticlesError] = useState<string | null>(null);
  const [articleSearch, setArticleSearch] = useState("");
  const [selectedArticle, setSelectedArticle] = useState<ArticleContent | null>(null);
  const [articleLoading, setArticleLoading] = useState(false);
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [totalArticles, setTotalArticles] = useState(0);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch articles
  const fetchArticles = useCallback(
    async (p: number = 0, search?: string) => {
      setArticlesLoading(true);
      setArticlesError(null);
      try {
        const res = await browseLibrary(library.collection, p, 50, search);
        setArticles(res.articles);
        setHasMore(res.has_more);
        setTotalArticles(res.total);
        setPage(p);
      } catch (err) {
        setArticlesError(
          err instanceof Error
            ? err.message
            : "Could not load articles. The browsing API may not be available yet."
        );
      } finally {
        setArticlesLoading(false);
      }
    },
    [library.collection]
  );

  useEffect(() => {
    // Uploads render their source material, not ZIM articles — skip the browse fetch.
    if (isUpload) {
      setArticlesLoading(false);
      return;
    }
    fetchArticles(0);
  }, [fetchArticles, isUpload]);

  // Debounced search within library
  useEffect(() => {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    if (tab !== "browse" || isUpload) return;

    searchTimerRef.current = setTimeout(() => {
      fetchArticles(0, articleSearch || undefined);
    }, 400);

    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [articleSearch]);

  // Load article content by url — shared by the browse list, the in-library
  // search tab, and deep links arriving from the global search results.
  const openArticleUrl = useCallback(
    async (url: string) => {
      if (!url) return;
      setTab("browse");
      setArticleLoading(true);
      try {
        const content = await getArticle(library.collection, url);
        if (content) setSelectedArticle(content);
      } finally {
        setArticleLoading(false);
      }
    },
    [library.collection]
  );

  const openArticle = (article: LibraryArticle) => openArticleUrl(article.url);

  useEffect(() => {
    if (initialArticleUrl) openArticleUrl(initialArticleUrl);
  }, [initialArticleUrl, openArticleUrl]);

  const tabButton = (key: ViewerTab, icon: string, label: string) => {
    const on = tab === key;
    return (
      <button
        key={key}
        type="button"
        onClick={() => setTab(key)}
        aria-pressed={on}
        className="inline-flex cursor-pointer items-center gap-2 transition-transform hover:-translate-y-0.5"
        style={{
          height: 42,
          padding: "0 20px",
          borderRadius: 99,
          border: on ? "1px solid transparent" : "1px solid var(--line)",
          background: on
            ? "linear-gradient(140deg,var(--pink),var(--magenta))"
            : "var(--card2)",
          color: on ? "#fff" : "var(--text2)",
          fontFamily: NUNITO,
          fontWeight: 700,
          fontSize: 15,
        }}
      >
        <MsIcon name={icon} size={18} />
        {label}
      </button>
    );
  };

  return (
    <div className="kb-viewer">
      {/* Header */}
      <div className="mb-5">
        <PillButton label="Back to library" icon="arrow_back" tone="quiet" onClick={onClose} />
      </div>

      <div style={{ ...panelBase, marginBottom: 20 }}>
        <div className="flex flex-wrap items-start gap-4">
          <div
            className="flex shrink-0 items-center justify-center"
            style={{
              width: 56,
              height: 56,
              borderRadius: 18,
              background: tint(cat.color, 15),
              border: `1px solid ${tint(cat.color, 30)}`,
              color: cat.color,
            }}
          >
            <MsIcon name={cat.icon} size={28} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <h2
                style={{
                  margin: 0,
                  fontFamily: FREDOKA,
                  fontWeight: 600,
                  fontSize: 26,
                  color: "var(--text)",
                }}
              >
                {library.name}
              </h2>
              <PillButton
                label="New window"
                icon="open_in_new"
                tone="quiet"
                title="Open in new window"
                onClick={() => {
                  const url = `/dashboard/knowledge/viewer?collection=${encodeURIComponent(library.collection)}&name=${encodeURIComponent(library.name)}&category=${encodeURIComponent(category)}`;
                  window.open(url, "_blank", "noopener");
                }}
              />
            </div>
            <p style={{ ...bodyText, margin: "6px 0 0" }}>{cat.description}</p>
            <div className="mt-3 flex flex-wrap gap-2.5">
              <span className={`kb-zone-pill ${zone.cls}`}>{zone.long}</span>
              {chunks > 0 && (
                <span
                  className="kb-zone-pill"
                  style={{
                    background: "var(--chip)",
                    color: "var(--text2)",
                    border: "1px solid var(--line)",
                  }}
                >
                  {chunks.toLocaleString()} chunks indexed
                </span>
              )}
              {isVideo && (
                <span
                  className="kb-zone-pill"
                  style={{
                    background: tint("var(--pink)", 15),
                    color: "var(--pink)",
                    border: `1px solid ${tint("var(--pink)", 25)}`,
                  }}
                >
                  Video content
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="mb-5 flex flex-wrap gap-2.5">
        {tabButton(
          "browse",
          isUpload ? "description" : isVideo ? "play_circle" : "format_list_bulleted",
          isUpload ? "Document" : isVideo ? "Watch" : "Browse"
        )}
        {tabButton("search", "search", "Search")}
        {tabButton("about", "info", "About")}
      </div>

      {/* Tab Content */}
      {tab === "browse" && isUpload && <SourceView library={library} />}
      {tab === "browse" && !isUpload && (
        <div style={panelBase}>
          {/* Search within library */}
          <div className="mb-4">
            <input
              type="text"
              value={articleSearch}
              onChange={(e) => setArticleSearch(e.target.value)}
              placeholder={`Search titles within ${library.name}…`}
              className="kb-viewer-search"
            />
          </div>

          {selectedArticle ? (
            // Article view
            <div>
              <div className="mb-4">
                <PillButton
                  label="Back to articles"
                  icon="arrow_back"
                  tone="quiet"
                  onClick={() => setSelectedArticle(null)}
                />
              </div>
              <h3
                style={{
                  margin: "0 0 16px",
                  fontFamily: FREDOKA,
                  fontWeight: 600,
                  fontSize: 23,
                  color: "var(--text)",
                }}
              >
                {selectedArticle.title}
              </h3>
              {/* For video libraries, render a native video player + description */}
              {isVideo && selectedArticle.html ? (() => {
                // Extract video source URL from the HTML (proxy or direct backend paths)
                const videoMatch = selectedArticle.html.match(/src=["']([^"']*?\.webm)["']/i)
                  || selectedArticle.html.match(/src=["']([^"']*?\.mp4)["']/i)
                  || selectedArticle.html.match(/src=["']([^"']*?\.ogv)["']/i);
                const posterMatch = selectedArticle.html.match(/(?:poster=["']|<img[^>]*src=["'])([^"']*?thumbnail[^"']*?)["']/i)
                  || selectedArticle.html.match(/poster=["']([^"']*?)["']/i);
                const videoUrl = videoMatch?.[1] || null;
                const posterUrl = posterMatch?.[1] || null;

                // Extract subtitle tracks
                const subtitleMatches = Array.from(selectedArticle.html.matchAll(/src=["']([^"']*?subs_(\w+)\.vtt[^"']*?)["']/gi));

                // Extract description text (strip HTML tags)
                const descMatch = selectedArticle.html.match(/<div[^>]*class="[^"]*talk-description[^"]*"[^>]*>([\s\S]*?)<\/div>/i)
                  || selectedArticle.html.match(/<p[^>]*>([\s\S]*?)<\/p>/i);
                const descHtml = descMatch?.[1] || '';
                const descText = descHtml.replace(/<[^>]*>/g, '').trim();

                return (
                  <div>
                    {videoUrl ? (
                      <div
                        className="mb-6 overflow-hidden"
                        style={{ borderRadius: 20, background: "#000" }}
                      >
                        <video
                          controls
                          preload="metadata"
                          poster={posterUrl || undefined}
                          className="w-full"
                          style={{ maxHeight: '500px' }}
                          crossOrigin="anonymous"
                        >
                          <source src={videoUrl} type="video/webm" />
                          {subtitleMatches.map(([, url, lang]) => (
                            <track
                              key={lang}
                              kind="subtitles"
                              src={url}
                              srcLang={lang}
                              label={lang.toUpperCase()}
                            />
                          ))}
                          Your browser does not support the video tag.
                        </video>
                      </div>
                    ) : (
                      <div
                        className="mb-6 flex flex-col items-center gap-2 text-center"
                        style={{
                          padding: 26,
                          borderRadius: 20,
                          background: STRIPES,
                          border: "1px solid var(--line)",
                        }}
                      >
                        <MsIcon name="play_circle" size={44} style={{ color: "var(--pink)" }} />
                        <span style={{ ...microLabel, fontSize: 12 }}>
                          Video present, but this browser has no decoder for it
                        </span>
                      </div>
                    )}
                    {descText && <p style={bodyText}>{descText}</p>}
                  </div>
                );
              })() : (
                <div
                  className="kb-article-content"
                  dangerouslySetInnerHTML={{ __html: selectedArticle.html }}
                />
              )}
            </div>
          ) : articlesLoading ? (
            <div className="flex items-center justify-center gap-3 py-12">
              <MsIcon
                name="progress_activity"
                size={24}
                className="puff-motion animate-pulse"
                style={{ color: "var(--pink)" }}
              />
              <span style={microLabel}>Loading articles</span>
            </div>
          ) : articlesError ? (
            <div
              className="flex flex-col items-center gap-2 py-8 text-center"
              style={{ borderRadius: 18, background: STRIPES }}
            >
              <MsIcon name="info" size={34} style={{ color: "var(--lemon)" }} />
              <p style={{ ...bodyText, margin: 0 }}>
                Direct browsing is not available for this library.
              </p>
              <span style={{ ...microLabel, fontSize: 11 }}>
                Use Search, or just ask Puff. She can read this pack
              </span>
            </div>
          ) : articles.length === 0 ? (
            <div
              className="flex flex-col items-center gap-2 py-8 text-center"
              style={{ borderRadius: 18, background: STRIPES }}
            >
              <MsIcon name="inbox" size={34} style={{ color: "var(--text3)" }} />
              <span style={{ ...microLabel, fontSize: 12 }}>
                {articleSearch ? "No titles match that" : "No browsable articles"}
              </span>
            </div>
          ) : (
            <div>
              <div style={{ ...microLabel, marginBottom: 12 }}>
                Showing {articles.length} of {totalArticles.toLocaleString()} articles
              </div>
              <div className="flex flex-col gap-2">
                {articles.map((article) => (
                  <div
                    key={article.index}
                    className="kb-article-item"
                    onClick={() => openArticle(article)}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <MsIcon
                          name={isVideo ? "play_circle" : "description"}
                          size={18}
                          style={{
                            color: isVideo ? "var(--pink)" : "var(--sky)",
                            flex: "none",
                          }}
                        />
                        <span
                          style={{
                            fontFamily: NUNITO,
                            fontWeight: 600,
                            fontSize: 15,
                            color: "var(--text)",
                          }}
                        >
                          {article.title}
                        </span>
                      </div>
                      <MsIcon
                        name="chevron_right"
                        size={18}
                        style={{ color: "var(--text3)", flex: "none" }}
                      />
                    </div>
                  </div>
                ))}
              </div>

              {/* Pagination */}
              <div className="mt-5 flex items-center justify-center gap-3">
                {page > 0 && (
                  <PillButton
                    label="Previous"
                    icon="chevron_left"
                    tone="quiet"
                    onClick={() => fetchArticles(page - 1, articleSearch || undefined)}
                  />
                )}
                {hasMore && (
                  <PillButton
                    label="Next page"
                    icon="chevron_right"
                    tone="quiet"
                    onClick={() => fetchArticles(page + 1, articleSearch || undefined)}
                  />
                )}
              </div>
            </div>
          )}

          {articleLoading && (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center"
              style={{ background: "rgba(0,0,0,.5)", backdropFilter: "blur(6px)" }}
            >
              <div
                className="flex items-center gap-3"
                style={{
                  padding: "18px 26px",
                  borderRadius: 99,
                  background: "var(--puff-surface-deep)",
                  border: "1px solid var(--line)",
                  boxShadow: "var(--shadow)",
                }}
              >
                <MsIcon
                  name="progress_activity"
                  size={20}
                  className="puff-motion animate-pulse"
                  style={{ color: "var(--pink)" }}
                />
                <span
                  style={{
                    fontFamily: NUNITO,
                    fontWeight: 700,
                    fontSize: 15,
                    color: "var(--text)",
                  }}
                >
                  Loading article…
                </span>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "search" && (
        <LibrarySearchTab library={library} />
      )}

      {tab === "about" && (
        <div style={panelBase}>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {[
              { label: "Library name", value: library.name },
              { label: "Collection ID", value: library.collection, mono: true },
              { label: "Kept in", value: zone.long },
              {
                label: "Search",
                value:
                  chunks > 0
                    ? `${chunks.toLocaleString()} chunks indexed for meaning-based search`
                    : "Title browsing only",
              },
              ...(sizeLabel ? [{ label: "Size", value: sizeLabel }] : []),
            ].map((row) => (
              <div key={row.label}>
                <div style={microLabel}>{row.label}</div>
                <div
                  style={{
                    marginTop: 4,
                    fontFamily: row.mono ? JBMONO : NUNITO,
                    fontWeight: 600,
                    fontSize: row.mono ? 14 : 16,
                    color: "var(--text)",
                  }}
                >
                  {row.value}
                </div>
              </div>
            ))}
            <div>
              <div style={microLabel}>Category</div>
              <div
                className="mt-1 flex items-center gap-2"
                style={{
                  fontFamily: NUNITO,
                  fontWeight: 600,
                  fontSize: 16,
                  color: cat.color,
                }}
              >
                <MsIcon name={cat.icon} size={18} />
                {cat.label}
              </div>
            </div>
          </div>

          <div
            className="mt-5"
            style={{
              padding: "16px 18px",
              borderRadius: 20,
              background: "var(--card2)",
              border: "1px solid var(--line)",
            }}
          >
            <div style={microLabel}>What Puff can do with it</div>
            <p style={{ ...bodyText, margin: "6px 0 0" }}>
              Puff can read this pack when you ask her something, cite where the
              answer came from, and
              {isVideo
                ? " offer to play a related talk."
                : " show you the article it came from."}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Search tab within the library viewer. One pass: the cross-library semantic
 * search, narrowed to this library. The old "use the library's own full-text
 * shard when one exists" branch is gone — no shard, no route, and it rendered
 * "No matches in this library" for a query that had 404'd (see the file header).
 */
function LibrarySearchTab({ library }: { library: KnowledgeLibrary }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (!query.trim()) {
      setResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    timerRef.current = setTimeout(async () => {
      try {
        const res = await searchKnowledge(query);
        // Filter results to this library
        const filtered = res.filter(
          (r: SearchResult) =>
            r.library?.toLowerCase() === library.name.toLowerCase() ||
            r.source?.toLowerCase() === library.name.toLowerCase()
        );
        setResults(filtered.length > 0 ? filtered : res.slice(0, 5));
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 400);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [query, library.name]);

  const empty = !searching && query.trim() && results.length === 0;

  return (
    <div style={panelBase}>
      <div className="flex items-center gap-3">
        <MsIcon name="search" size={20} style={{ color: "var(--text3)", flex: "none" }} />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Ask about anything in ${library.name}…`}
          className="kb-viewer-search"
        />
        {searching && (
          <MsIcon
            name="progress_activity"
            size={18}
            className="puff-motion animate-pulse"
            style={{ color: "var(--pink)", flex: "none" }}
          />
        )}
      </div>
      <NoFulltextNote />

      {!query.trim() && (
        <div
          className="mt-4 flex flex-col items-center gap-2 py-8 text-center"
          style={{ borderRadius: 18, background: STRIPES }}
        >
          <MsIcon name="search" size={34} style={{ color: "var(--text3)" }} />
          <span style={{ ...microLabel, fontSize: 12 }}>
            Search by meaning: Puff finds the passage, not just the word
          </span>
        </div>
      )}

      {empty && (
        <div className="mt-4 py-8 text-center">
          <span style={{ ...microLabel, fontSize: 12 }}>No matches in this library</span>
        </div>
      )}

      {results.length > 0 && (
        <div className="mt-4 flex flex-col gap-3">
          {results.map((result, idx) => (
            <div
              key={idx}
              style={{
                padding: 16,
                borderRadius: 20,
                background: "var(--card2)",
                border: "1px solid var(--line)",
              }}
            >
              <div style={{ ...microLabel, marginBottom: 6 }}>
                {result.library || result.source || "Knowledge base"}
              </div>
              <p style={{ ...bodyText, margin: 0, fontSize: 15 }}>{result.text}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   ABSORPTION TABS — design language pill row (Library | My Documents)
   ═══════════════════════════════════════════════════════════════ */

function AbsorbTabs<T extends string>({
  tabs,
  active,
  onSelect,
}: {
  tabs: { key: T; label: string }[];
  active: T;
  onSelect: (key: T) => void;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-center gap-3">
      {tabs.map((t) => {
        const isActive = t.key === active;
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => onSelect(t.key)}
            className="rounded-full px-6 py-3 transition hover:scale-[1.02]"
            style={
              isActive
                ? {
                    background:
                      "linear-gradient(140deg, var(--pink), var(--magenta))",
                    color: "#fff",
                    font: "700 15px var(--font-nunito), sans-serif",
                    boxShadow: "0 10px 26px var(--glow1)",
                    border: "1px solid transparent",
                  }
                : {
                    background: "var(--card2)",
                    border: "1px solid var(--line)",
                    color: "var(--text2)",
                    font: "700 15px var(--font-nunito), sans-serif",
                    backdropFilter: "blur(14px)",
                    WebkitBackdropFilter: "blur(14px)",
                  }
            }
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   MAIN PAGE COMPONENT
   ═══════════════════════════════════════════════════════════════ */

const pagePad =
  "clamp(16px,2.4vh,26px) clamp(18px,2vw,36px) clamp(18px,2.8vh,30px)";

export default function KnowledgePage() {
  // Absorption tab: Library (this page) | My Documents (embedded page)
  const [pageTab, setPageTab] = useState<"library" | "documents">("library");

  // Deep link: /dashboard/knowledge?tab=documents. Read after mount (not via
  // useSearchParams) to keep the page prerender/Suspense-free.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("tab") === "documents") {
      setPageTab("documents");
    }
  }, []);
  // --- Data state ---
  const [libraries, setLibraries] = useState<KnowledgeLibrary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // --- View state ---
  const [viewMode, setViewMode] = useState<ViewMode>("shelves");
  const [selectedLibrary, setSelectedLibrary] = useState<KnowledgeLibrary | null>(null);
  // Article the viewer should jump straight to (set from a search result).
  const [pendingArticle, setPendingArticle] = useState<string | null>(null);

  // --- Search state ---
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [titleResults, setTitleResults] = useState<TitleSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // --- Upload state ---
  const [showUploadArea, setShowUploadArea] = useState(false);
  const [uploadZone, setUploadZone] = useState<1 | 2>(2);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // --- Delete confirmation state ---
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeLibrary | null>(null);
  const [deleting, setDeleting] = useState(false);

  // --- Toast state ---
  const [toast, setToast] = useState<{
    message: string;
    type: "success" | "error";
  } | null>(null);

  const showToast = (message: string, type: "success" | "error") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  // --- Fetch libraries ---
  const fetchLibraries = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getLibraries();
      setLibraries(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Knowledge service unavailable");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLibraries();
  }, [fetchLibraries]);

  // --- Debounced global search ---
  // Two independent passes, each allowed to fail on its own: title match across
  // ZIM packs, and RAG semantic. (A third pass over article BODIES used to run
  // here against /knowledge/search/fulltext, which this backend does not serve —
  // see the file header.)
  useEffect(() => {
    if (searchTimer.current) clearTimeout(searchTimer.current);

    if (!searchQuery.trim()) {
      setSearchResults([]);
      setTitleResults([]);
      setSearching(false);
      return;
    }

    setSearching(true);
    searchTimer.current = setTimeout(async () => {
      const [titles, rag] = await Promise.allSettled([
        searchTitles(searchQuery, 50),
        searchKnowledge(searchQuery),
      ]);

      setTitleResults(titles.status === "fulfilled" ? titles.value.results : []);
      setSearchResults(rag.status === "fulfilled" ? rag.value : []);
      setSearching(false);
    }, 400);

    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
    };
  }, [searchQuery]);

  // --- Upload handler ---
  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;

    const file = files[0];
    const ext = file.name.split(".").pop()?.toLowerCase();
    const allowed = [
      "txt", "md", "pdf", "json", "csv", "tsv",
      "docx", "doc", "pptx", "rtf", "xlsx", "xls",
      "html", "xml", "yaml", "yml",
      "jpg", "jpeg", "png", "gif", "bmp", "tiff", "tif", "webp",
      "py", "js", "ts", "log", "ini", "cfg", "toml",
    ];
    if (!ext || !allowed.includes(ext)) {
      showToast(
        `Unsupported file type .${ext}. Supported: documents, spreadsheets, images, code files`,
        "error"
      );
      return;
    }

    setUploading(true);
    setUploadProgress(`Indexing ${file.name}...`);
    try {
      const res = await uploadDocument(file, uploadZone);
      if (res.success) {
        showToast(
          `"${file.name}" indexed into ${uploadZone === 1 ? "Vault" : "General Knowledge"}`,
          "success"
        );
        setShowUploadArea(false);
        await fetchLibraries();
      } else {
        showToast("Upload failed. Please try again.", "error");
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Upload failed", "error");
    } finally {
      setUploading(false);
      setUploadProgress(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  // --- Delete handler ---
  const handleDelete = async (lib: KnowledgeLibrary) => {
    setDeleting(true);
    try {
      const res = await deleteLibrary(lib.name);
      if (res.success) {
        setLibraries((prev) => prev.filter((l) => l.name !== lib.name));
        showToast(`Removed "${lib.name}" from library`, "success");
      } else {
        showToast("Delete failed. Please try again.", "error");
      }
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Delete failed", "error");
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  };

  // --- Drop handlers ---
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  };
  const handleDragLeave = () => setDragOver(false);
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    handleUpload(e.dataTransfer.files);
  };

  // --- Open library ---
  const openLibrary = (lib: KnowledgeLibrary) => {
    setPendingArticle(null);
    setSelectedLibrary(lib);
    setViewMode("viewer");
  };

  /** Open a search hit: its library if we know it, otherwise a stand-in row
   *  built from the hit itself so the article still resolves. */
  const openResult = (libraryId: string, libraryName: string, articleUrl?: string) => {
    const lib =
      findLibrary(libraries, libraryId) ||
      ({
        name: libraryName || libraryId,
        collection: libraryId,
        library_id: libraryId,
        zone: "knowledge",
      } as KnowledgeLibrary);
    setPendingArticle(articleUrl || null);
    setSelectedLibrary(lib);
    setViewMode("viewer");
  };

  const closeViewer = () => {
    setViewMode("shelves");
    setSelectedLibrary(null);
    setPendingArticle(null);
  };

  // --- Derived data ---
  const totalChunks = libraries.reduce((sum, lib) => sum + chunkCount(lib), 0);
  const categoryCount = () =>
    Object.values(shelves).filter((s) => s.length > 0).length;

  // Group libraries by category
  const shelves: Record<CategoryKey, KnowledgeLibrary[]> = {
    encyclopedia: [],
    video: [],
    qa: [],
    medical: [],
    reference: [],
    survival: [],
    science: [],
    travel: [],
    books: [],
    uploads: [],
  };
  libraries.forEach((lib) => {
    const cat = categorizeLibrary(lib);
    shelves[cat].push(lib);
  });

  // Order categories for display (non-empty first)
  const shelfOrder: CategoryKey[] = [
    "encyclopedia",
    "video",
    "qa",
    "medical",
    "science",
    "reference",
    "survival",
    "travel",
    "books",
    "uploads",
  ];

  // --- Skeleton loader ---
  const SkeletonShelf = () => (
    <div className="mb-8">
      <div
        className="mb-3"
        style={{ height: 16, width: 160, borderRadius: 8, background: STRIPES }}
      />
      <div className="flex gap-4">
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            style={{ height: 208, width: 160, borderRadius: 14, background: STRIPES }}
          />
        ))}
      </div>
    </div>
  );

  const toastNode = toast && (
    <div
      className="fixed right-4 top-4 z-50 flex items-center gap-2"
      style={{
        padding: "14px 20px",
        borderRadius: 99,
        background:
          toast.type === "success"
            ? "linear-gradient(140deg,var(--mint),var(--sky))"
            : `linear-gradient(140deg,${RED},var(--magenta))`,
        color: "#fff",
        fontFamily: NUNITO,
        fontWeight: 700,
        fontSize: 15,
        boxShadow: "var(--shadow)",
      }}
    >
      <MsIcon name={toast.type === "success" ? "check_circle" : "error"} size={20} />
      {toast.message}
    </div>
  );

  const absorbTabs = (
    <AbsorbTabs
      tabs={[
        { key: "library" as const, label: "Library" },
        { key: "documents" as const, label: "My Documents" },
      ]}
      active={pageTab}
      onSelect={(key) => setPageTab(key)}
    />
  );

  /* ═══════════════════════════════════════════════════════════════
     RENDER — MY DOCUMENTS (absorbed page, rendered in place)
     ═══════════════════════════════════════════════════════════════ */

  if (pageTab === "documents") {
    return (
      <div className={`px-6 pt-6 ${containerMaxW}`}>
        {absorbTabs}
        <MyDocuments />
      </div>
    );
  }

  /* ═══════════════════════════════════════════════════════════════
     RENDER — LIBRARY VIEWER
     ═══════════════════════════════════════════════════════════════ */

  if (viewMode === "viewer" && selectedLibrary) {
    return (
      <section style={{ padding: pagePad }}>
        {toastNode}
        <LibraryViewer
          key={selectedLibrary.collection || selectedLibrary.name}
          library={selectedLibrary}
          category={categorizeLibrary(selectedLibrary)}
          onClose={closeViewer}
          initialArticleUrl={pendingArticle}
        />
      </section>
    );
  }

  /* ═══════════════════════════════════════════════════════════════
     RENDER — BOOKSHELF VIEW
     ═══════════════════════════════════════════════════════════════ */

  return (
    <section style={{ padding: pagePad }}>
      {toastNode}

      {/* Delete Confirmation Modal */}
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
              Remove from library
            </h3>
            <p style={{ ...bodyText, margin: "0 0 18px" }}>
              Remove <b style={{ color: "var(--text)" }}>{deleteTarget.name}</b> and
              everything indexed from it? This cannot be undone.
            </p>
            <div className="flex flex-wrap justify-end gap-3">
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
          </div>
        </div>
      )}

      {/* Absorption tabs: Library | My Documents */}
      {absorbTabs}

      {/* Page Header */}
      <PageHeading
        icon="menu_book"
        title="Knowledge"
        sub="Offline packs, and whatever you have added yourself."
      >
        <div className="flex flex-wrap gap-3">
          <PillButton
            label="Add to library"
            icon="upload"
            onClick={() => setShowUploadArea(!showUploadArea)}
          />
          <PillButton icon="refresh" tone="quiet" title="Refresh" onClick={fetchLibraries} />
        </div>
      </PageHeading>

      {/* Upload Area */}
      {showUploadArea && (
        <div style={{ ...panelBase, marginBottom: 20 }}>
          <div className="mb-4 flex items-center justify-between gap-3">
            <SubHeading icon="cloud_upload">Add a document</SubHeading>
            <button
              type="button"
              onClick={() => setShowUploadArea(false)}
              aria-label="Close"
              className="cursor-pointer"
              style={{ background: "none", border: "none", color: "var(--text3)", padding: 4 }}
            >
              <MsIcon name="close" size={22} />
            </button>
          </div>

          {/* Zone Selector */}
          <div className="mb-4">
            <div style={{ ...microLabel, marginBottom: 8 }}>Where should this live?</div>
            <div className="flex flex-wrap gap-3">
              {[
                {
                  zone: 2 as const,
                  title: "General knowledge",
                  sub: "Open to every question",
                  color: "var(--sky)",
                },
                {
                  zone: 1 as const,
                  title: "Personal vault",
                  sub: "PIN-gated and encrypted",
                  color: "var(--magenta)",
                },
              ].map((z) => {
                const on = uploadZone === z.zone;
                return (
                  <button
                    key={z.zone}
                    type="button"
                    onClick={() => setUploadZone(z.zone)}
                    aria-pressed={on}
                    className="flex-1 cursor-pointer text-center"
                    style={{
                      minWidth: 200,
                      padding: "14px 16px",
                      borderRadius: 20,
                      border: on ? `2px solid ${z.color}` : "1px solid var(--line)",
                      background: on ? tint(z.color, 12) : "var(--card2)",
                    }}
                  >
                    <div
                      style={{
                        fontFamily: NUNITO,
                        fontWeight: 700,
                        fontSize: 16,
                        color: on ? z.color : "var(--text)",
                      }}
                    >
                      {z.title}
                    </div>
                    <div style={{ ...microLabel, marginTop: 4, fontSize: 10 }}>{z.sub}</div>
                  </button>
                );
              })}
            </div>
            <p style={{ ...microLabel, marginTop: 10, textTransform: "none", letterSpacing: 0, fontSize: 12 }}>
              Puff looks for personal details (passwords, SSNs, medical records)
              and will suggest the vault when she finds them.
            </p>
          </div>

          {/* Drop Zone */}
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className="relative flex cursor-pointer flex-col items-center justify-center gap-2"
            style={{
              padding: 34,
              borderRadius: 24,
              border: `2px dashed ${dragOver ? "var(--pink)" : "var(--line)"}`,
              background: dragOver ? tint("var(--pink)", 8) : STRIPES,
              transition: "border-color .2s ease, background .2s ease",
            }}
          >
            <MsIcon
              name="cloud_upload"
              size={44}
              style={{ color: dragOver ? "var(--pink)" : "var(--text3)" }}
            />
            {uploading ? (
              <div className="text-center">
                <div
                  className="mb-2 flex items-center justify-center gap-2"
                  style={{
                    fontFamily: NUNITO,
                    fontWeight: 700,
                    fontSize: 15,
                    color: "var(--pink)",
                  }}
                >
                  <MsIcon
                    name="progress_activity"
                    size={18}
                    className="puff-motion animate-pulse"
                  />
                  {uploadProgress}
                </div>
                <div
                  className="mx-auto overflow-hidden"
                  style={{ height: 8, width: 190, borderRadius: 99, background: "var(--chip)" }}
                >
                  <div
                    className="animate-pulse h-full"
                    style={{
                      width: "60%",
                      borderRadius: 99,
                      background: "linear-gradient(90deg,var(--pink),var(--magenta))",
                    }}
                  />
                </div>
              </div>
            ) : (
              <>
                <span
                  style={{
                    fontFamily: NUNITO,
                    fontWeight: 700,
                    fontSize: 16,
                    color: "var(--text)",
                  }}
                >
                  Drop a file here, or tap to browse
                </span>
                <span style={{ ...microLabel, fontSize: 11 }}>
                  PDF · Word · Excel · slides · images · code
                </span>
              </>
            )}
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              accept=".txt,.md,.pdf,.json,.csv,.tsv,.docx,.doc,.pptx,.rtf,.xlsx,.xls,.html,.xml,.yaml,.yml,.jpg,.jpeg,.png,.gif,.bmp,.tiff,.tif,.webp,.py,.js,.ts,.log,.ini,.cfg,.toml"
              onChange={(e) => handleUpload(e.target.files)}
              disabled={uploading}
            />
          </div>

          {/* Phone route into the same pipeline: QR → /send?code=... */}
          <PairDeviceCard />
        </div>
      )}

      {/* Stats Row */}
      <div className="mb-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
        {loading ? (
          [1, 2, 3].map((i) => (
            <div key={i} style={{ ...panelBase, borderRadius: 26 }}>
              <div style={{ height: 70, borderRadius: 18, background: STRIPES }} />
            </div>
          ))
        ) : (
          <>
            <StatTile
              label="Packs"
              value={String(libraries.length)}
              note={libraries.length === 1 ? "on the shelf" : "on the shelves"}
              icon="menu_book"
              color="var(--sky)"
            />
            <StatTile
              label="Indexed"
              value={totalChunks > 0 ? formatChunks(totalChunks) : "0"}
              note={totalChunks > 0 ? "passages Puff can cite" : "nothing indexed yet"}
              icon="database"
              color="var(--mint)"
            />
            <StatTile
              label="Shelves"
              value={String(categoryCount())}
              note="categories in use"
              icon="category"
              color="var(--lemon)"
            />
          </>
        )}
      </div>

      {/* Global Search */}
      <div style={{ ...panelBase, marginBottom: 20 }}>
        <div className="flex items-center gap-3">
          <MsIcon name="search" size={20} style={{ color: "var(--text3)", flex: "none" }} />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search titles, or ask by meaning…"
            /* .kb-viewer-search is remapped to the theme glass in
               css/_theme.css; a bare input here inherits main.css's
               `.dark input[type="text"]` navy box, which is illegible in day. */
            className="kb-viewer-search"
          />
          {searching && (
            <MsIcon
              name="progress_activity"
              size={18}
              className="puff-motion animate-pulse"
              style={{ color: "var(--pink)", flex: "none" }}
            />
          )}
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery("")}
              aria-label="Clear search"
              className="cursor-pointer"
              style={{
                background: "none",
                border: "none",
                color: "var(--text3)",
                padding: 2,
                flex: "none",
              }}
            >
              <MsIcon name="close" size={18} />
            </button>
          )}
        </div>
        <NoFulltextNote />
      </div>

      {/* Titles */}
      {titleResults.length > 0 && (
        <div style={{ ...panelBase, marginBottom: 20 }}>
          <ResultsHead
            icon="search"
            title="Titles"
            count={`${titleResults.length} article${titleResults.length !== 1 ? "s" : ""} named for this`}
          />
          <div className="flex flex-col gap-1.5">
            {titleResults.map((result, idx) => {
              const isVid = result.library_name.toLowerCase().startsWith("ted ") ||
                result.collection.includes("_ted_") ||
                result.collection.startsWith("ted_");
              return (
                <button
                  key={`${result.collection}-${result.url}-${idx}`}
                  type="button"
                  onClick={() =>
                    openResult(result.collection, result.library_name, result.url)
                  }
                  className="flex w-full cursor-pointer items-center gap-3 text-left transition-transform hover:-translate-y-0.5"
                  style={{
                    padding: "12px 14px",
                    borderRadius: 18,
                    background: "var(--card2)",
                    border: "1px solid var(--line)",
                  }}
                >
                  <MsIcon
                    name={isVid ? "play_circle" : "description"}
                    size={19}
                    style={{ color: isVid ? "var(--pink)" : "var(--sky)", flex: "none" }}
                  />
                  <div className="min-w-0 flex-1">
                    <div
                      className="truncate"
                      style={{
                        fontFamily: NUNITO,
                        fontWeight: 700,
                        fontSize: 15,
                        color: "var(--text)",
                      }}
                    >
                      {result.title}
                    </div>
                    <div className="truncate" style={{ ...microLabel, marginTop: 2 }}>
                      {result.library_name}
                    </div>
                  </div>
                  <MsIcon
                    name="chevron_right"
                    size={18}
                    style={{ color: "var(--text3)", flex: "none" }}
                  />
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Semantic / RAG matches */}
      {searchResults.length > 0 && (
        <div style={{ ...panelBase, marginBottom: 20 }}>
          <ResultsHead
            icon="database"
            title="By meaning"
            count={`${searchResults.length} passage${searchResults.length !== 1 ? "s" : ""}`}
          />
          <div className="flex flex-col gap-3">
            {searchResults.map((result, idx) => (
              <div
                key={idx}
                style={{
                  padding: 16,
                  borderRadius: 20,
                  background: "var(--card2)",
                  border: "1px solid var(--line)",
                }}
              >
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className={`kb-zone-pill ${zoneBadge(result.zone).cls}`}>
                      {zoneBadge(result.zone).short}
                    </span>
                    <span style={microLabel}>{result.source || result.library}</span>
                  </div>
                  {result.score != null && (
                    <span
                      style={{
                        padding: "4px 12px",
                        borderRadius: 99,
                        background: tint("var(--mint)", 16),
                        color: "var(--mint)",
                        fontFamily: JBMONO,
                        fontWeight: 700,
                        fontSize: 11,
                      }}
                    >
                      {(result.score * 100).toFixed(0)}% match
                    </span>
                  )}
                </div>
                <p style={{ ...bodyText, margin: 0, fontSize: 15 }}>{result.text}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Nothing matched anywhere */}
      {!searching &&
        searchQuery.trim().length > 1 &&
        titleResults.length === 0 &&
        searchResults.length === 0 && (
          <div style={{ ...panelBase, marginBottom: 20 }}>
            <div className="py-5 text-center">
              <p style={{ ...bodyText, margin: 0 }}>
                Nothing matched &ldquo;{searchQuery}&rdquo;.
              </p>
              <p style={{ ...microLabel, marginTop: 8, fontSize: 11 }}>
                Titles and meaning were both searched. Article bodies are not
                indexed in this build
              </p>
            </div>
          </div>
        )}

      {/* Dual-Zone Explainer */}
      <div className="mb-5 grid grid-cols-1 gap-4 md:grid-cols-2">
        {[
          {
            title: "General knowledge",
            color: "var(--sky)",
            icon: "menu_book",
            body: "Offline encyclopedias, documentation and reference material. Open to every question, with no personal data attached.",
          },
          {
            title: "Your locked vault",
            color: "var(--magenta)",
            icon: "shield_lock",
            body: "Your own files and notes. Encrypted at rest and only readable while you have unlocked the vault with your PIN.",
          },
        ].map((z) => (
          <div
            key={z.title}
            style={{
              ...panelBase,
              borderRadius: 26,
              background: `linear-gradient(140deg, ${tint(z.color, 14)}, transparent)`,
            }}
          >
            <div className="mb-1.5 flex items-center gap-2.5">
              <MsIcon name={z.icon} size={21} style={{ color: z.color }} />
              <div
                style={{
                  fontFamily: NUNITO,
                  fontWeight: 700,
                  fontSize: 18,
                  color: "var(--text)",
                }}
              >
                {z.title}
              </div>
            </div>
            <p style={{ ...bodyText, margin: 0 }}>{z.body}</p>
          </div>
        ))}
      </div>

      {/* Error State */}
      {error && (
        <div style={{ ...panelBase, marginBottom: 20 }}>
          <div className="flex flex-col items-center justify-center gap-3 py-8 text-center">
            <MsIcon name="error" size={44} style={{ color: RED }} />
            <div
              style={{
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: 21,
                color: "var(--text)",
              }}
            >
              Knowledge service unavailable
            </div>
            <p style={{ ...bodyText, margin: 0 }}>{error}</p>
            <PillButton label="Retry" icon="refresh" onClick={fetchLibraries} />
          </div>
        </div>
      )}

      {/* ═══ THE BOOKSHELF ═══ */}
      {!error && (
        <>
          {/* Why the shelf looks the way it does: chosen, downloaded, indexed. */}
          {!loading && <MyData />}
          {!loading && <LearningCard />}
          {!loading && <BuildSources libraryCount={libraries.length} />}
          {!loading && <FindMore />}

          <SubHeading icon="menu_book">The library</SubHeading>

          {loading ? (
            <>
              <SkeletonShelf />
              <SkeletonShelf />
            </>
          ) : libraries.length === 0 ? (
            <div style={panelBase}>
              <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
                <MsIcon name="inbox" size={44} style={{ color: "var(--text3)" }} />
                <div
                  style={{
                    fontFamily: FREDOKA,
                    fontWeight: 600,
                    fontSize: 21,
                    color: "var(--text)",
                  }}
                >
                  Your library is empty
                </div>
                <p style={{ ...bodyText, margin: 0 }}>
                  Add a document to put the first book on the shelf.
                </p>
                <PillButton
                  label="Add your first book"
                  icon="upload"
                  onClick={() => setShowUploadArea(true)}
                />
              </div>
            </div>
          ) : (
            <div>
              {shelfOrder.map((catKey) => {
                const libs = shelves[catKey];
                if (libs.length === 0) return null;
                return (
                  <ShelfRow
                    key={catKey}
                    category={catKey}
                    libraries={libs}
                    onBookClick={openLibrary}
                  />
                );
              })}

              {/* Compact list view for quick management */}
              <div className="mt-8">
                <div className="mb-3 flex items-center gap-2" style={microLabel}>
                  <MsIcon name="format_list_bulleted" size={15} />
                  All packs: quick manage
                </div>
                <div style={{ ...panelBase, padding: "14px 18px" }}>
                  <div className="flex flex-col gap-1.5">
                    {libraries.map((lib) => {
                      const cat = categorizeLibrary(lib);
                      const catInfo = CATEGORIES[cat];
                      return (
                        <div
                          key={lib.collection || lib.name}
                          className="flex flex-wrap items-center justify-between gap-3"
                          style={{
                            padding: "11px 14px",
                            borderRadius: 18,
                            background: "var(--card2)",
                            border: "1px solid var(--line)",
                          }}
                        >
                          <div className="flex min-w-0 items-center gap-3">
                            <MsIcon
                              name={catInfo.icon}
                              size={18}
                              style={{ color: catInfo.color, flex: "none" }}
                            />
                            <span
                              className="truncate"
                              style={{
                                fontFamily: NUNITO,
                                fontWeight: 700,
                                fontSize: 15,
                                color: "var(--text)",
                              }}
                            >
                              {lib.name}
                            </span>
                            <span className={`kb-zone-pill ${zoneBadge(lib.zone).cls}`}>
                              {zoneBadge(lib.zone).short}
                            </span>
                          </div>
                          <div className="flex items-center gap-3">
                            <span style={microLabel}>
                              {chunkCount(lib) > 0
                                ? `${formatChunks(chunkCount(lib))} chunks`
                                : formatSize(lib.size)}
                            </span>
                            <button
                              type="button"
                              onClick={() => openLibrary(lib)}
                              title="Open"
                              aria-label={`Open ${lib.name}`}
                              className="cursor-pointer"
                              style={{
                                background: "none",
                                border: "none",
                                color: "var(--text3)",
                                padding: 2,
                              }}
                            >
                              <MsIcon name="open_in_new" size={18} />
                            </button>
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                setDeleteTarget(lib);
                              }}
                              title="Remove"
                              aria-label={`Remove ${lib.name}`}
                              className="cursor-pointer"
                              style={{
                                background: "none",
                                border: "none",
                                color: "var(--text3)",
                                padding: 2,
                              }}
                            >
                              <MsIcon name="delete" size={18} />
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
