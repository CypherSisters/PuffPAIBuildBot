"use client";

import {
  useState,
  useEffect,
  useCallback,
  useRef,
  type CSSProperties,
} from "react";
import { useSearchParams } from "next/navigation";
import MsIcon from "../../../_components/MsIcon";
import {
  getLibraries,
  browseLibrary,
  getArticle,
} from "../../../_lib/services/knowledgeService";
import type { KnowledgeLibrary, LibraryArticle, ArticleContent } from "../../../_interfaces/api";
import { zoneBadge, chunkCount, formatSize } from "../libraryMeta";
import SourceView from "../SourceView";

/* -- shell vocabulary --
   This page opens in its OWN TAB, outside the dashboard rail, so it paints the
   theme scene itself rather than the admin template's flat near-black. */

const FREDOKA = "var(--font-fredoka), sans-serif";
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

function Panel({ children }: { children: React.ReactNode }) {
  return <div style={panelBase}>{children}</div>;
}

/* ═══════════════════════════════════════════════════════════════
   STANDALONE LIBRARY VIEWER
   Opens a single library in its own browser tab so the user
   can browse/watch while continuing to use the main UI.
   ═══════════════════════════════════════════════════════════════ */

type ViewerTab = "browse" | "search" | "about";

export default function StandaloneViewer() {
  const params = useSearchParams();
  const collection = params.get("collection") || "";
  const name = params.get("name") || "";
  const categoryParam = params.get("category") || "";
  // Deep link: ?article=<zim url> auto-opens one article/video (used by the
  // voice "show me X" overlay, which lands straight on the talk).
  const articleParam = params.get("article") || "";
  // ?kind=video is a backend hint that this collection is a video shelf, so
  // the viewer renders the native <video> even when the library name/collection
  // don't follow the "ted*" convention (future video sources).
  const kindParam = params.get("kind") || "";

  const [library, setLibrary] = useState<KnowledgeLibrary | null>(null);
  const [loading, setLoading] = useState(true);

  // Fetch the library info
  useEffect(() => {
    (async () => {
      try {
        const libs = await getLibraries();
        const found = libs.find((l) => l.collection === collection);
        if (found) {
          setLibrary(found);
        } else {
          // Create a minimal library object from URL params
          setLibrary({
            name: name || collection,
            collection,
            zone: 2,
            zone_label: "General Knowledge",
            chunk_count: 0,
            size: "",
            added: "",
            library_id: "",
          } as KnowledgeLibrary);
        }
      } catch {
        setLibrary({
          name: name || collection,
          collection,
          zone: 2,
          zone_label: "General Knowledge",
          chunk_count: 0,
          size: "",
          added: "",
          library_id: "",
        } as KnowledgeLibrary);
      } finally {
        setLoading(false);
      }
    })();
  }, [collection, name]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <MsIcon name="progress_activity" size={32} className="puff-motion animate-pulse" style={{ color: "var(--pink)" }} />
      </div>
    );
  }

  if (!library) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <span style={{ ...microLabel, fontSize: 13 }}>That library is not on this box</span>
      </div>
    );
  }

  // Video shelves: the backend's ?kind=video hint is authoritative; the name /
  // collection heuristics are the fallback. Real TED collections are named
  // ted_mul_<topic>_<date> (e.g. ted_mul_surveillance_2025-08), so a bare
  // "ted" prefix on the collection is what actually catches them — the old
  // startsWith("ted ") / includes("_ted_") predicates missed every one.
  const isVideo =
    kindParam === "video" ||
    library.name.toLowerCase().startsWith("ted") ||
    (library.collection || "").toLowerCase().startsWith("ted") ||
    (library.collection || "").toLowerCase().includes("_ted_");

  return (
    <div className="min-h-screen p-6">
      <div className="mx-auto max-w-5xl">
        {/* Header */}
        <div className="mb-6 flex items-center gap-3">
          <MsIcon name="menu_book" size={28} style={{ color: "var(--pink)" }} />
          <h1
            style={{
              margin: 0,
              fontFamily: FREDOKA,
              fontWeight: 600,
              fontSize: "clamp(24px,2.2vw,34px)",
              letterSpacing: "-.6px",
              color: "var(--text)",
            }}
          >
            {library.name}
          </h1>
          {isVideo && (
            <span
              style={{
                padding: "5px 14px",
                borderRadius: 99,
                background: "color-mix(in srgb, var(--pink) 16%, transparent)",
                border: "1px solid color-mix(in srgb, var(--pink) 30%, transparent)",
                color: "var(--pink)",
                fontFamily: JBMONO,
                fontWeight: 700,
                fontSize: 11,
                letterSpacing: ".1em",
                textTransform: "uppercase",
              }}
            >
              Video
            </span>
          )}
        </div>

        {/* Viewer */}
        <ViewerContent
          library={library}
          isVideo={isVideo}
          initialArticle={articleParam}
        />
      </div>
    </div>
  );
}

function ViewerContent({
  library,
  isVideo,
  initialArticle,
}: {
  library: KnowledgeLibrary;
  isVideo: boolean;
  initialArticle?: string;
}) {
  const isUpload = library.is_upload === true;
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
      } catch (err: any) {
        setArticlesError(err.message || "Could not load articles.");
      } finally {
        setArticlesLoading(false);
      }
    },
    [library.collection]
  );

  useEffect(() => {
    if (isUpload) {
      setArticlesLoading(false);
      return;
    }
    fetchArticles(0);
  }, [fetchArticles, isUpload]);

  // Debounced search
  useEffect(() => {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    if (tab !== "browse" || isUpload) return;
    searchTimerRef.current = setTimeout(() => {
      fetchArticles(0, articleSearch || undefined);
    }, 400);
    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    };
  }, [articleSearch, tab, fetchArticles]);

  const openArticle = async (art: LibraryArticle) => {
    setArticleLoading(true);
    const content = await getArticle(library.collection, art.url);
    setArticleLoading(false);
    if (content) setSelectedArticle(content);
  };

  // Deep link: when ?article= is present, auto-open that one article/video
  // once (the voice "show me X" overlay lands straight on the talk).
  const deepLinkedRef = useRef(false);
  useEffect(() => {
    if (!initialArticle || deepLinkedRef.current || isUpload) return;
    deepLinkedRef.current = true;
    setArticleLoading(true);
    getArticle(library.collection, initialArticle, { fresh: true }).then((content) => {
      setArticleLoading(false);
      if (content) setSelectedArticle(content);
    });
  }, [initialArticle, isUpload, library.collection]);

  return (
    <div>
      {/* Tabs */}
      <div className="mb-4 flex gap-1 border-b border-(--line) pb-0">
        <button
          className={`kb-tab ${tab === "browse" ? "kb-tab-active" : ""}`}
          onClick={() => setTab("browse")}
        >
          <span className="flex items-center gap-2">
            <MsIcon
              name={isUpload ? "description" : isVideo ? "play_circle" : "format_list_bulleted"}
              size={16}
            />
            {isUpload ? "Document" : isVideo ? "Watch" : "Browse"}
          </span>
        </button>
        <button
          className={`kb-tab ${tab === "about" ? "kb-tab-active" : ""}`}
          onClick={() => setTab("about")}
        >
          <span className="flex items-center gap-2">
            <MsIcon name="info" size={16} />
            About
          </span>
        </button>
      </div>

      {/* Browse Tab */}
      {tab === "browse" && isUpload && <SourceView library={library} />}
      {tab === "browse" && !isUpload && (
        <Panel>
          <div className="mb-4">
            <input
              type="text"
              value={articleSearch}
              onChange={(e) => setArticleSearch(e.target.value)}
              placeholder={`Search within ${library.name}...`}
              className="kb-viewer-search"
            />
          </div>

          {selectedArticle ? (
            <div>
              <button
                onClick={() => setSelectedArticle(null)}
                className="mb-4 inline-flex cursor-pointer items-center gap-2 rounded-full transition-transform hover:-translate-y-0.5" style={{ height: 38, padding: "0 18px", background: "var(--card2)", border: "1px solid var(--line)", color: "var(--text2)", fontFamily: NUNITO, fontWeight: 700, fontSize: 14 }}
              >
                <MsIcon name="arrow_back" size={16} />
                Back to articles
              </button>
              <h3 className="mb-4 text-lg font-bold text-(--puff-text-strong)">
                {selectedArticle.title}
              </h3>
              {isVideo && selectedArticle.html ? (() => {
                const videoMatch = selectedArticle.html.match(/src=["']([^"']*?\.webm)["']/i)
                  || selectedArticle.html.match(/src=["']([^"']*?\.mp4)["']/i);
                const posterMatch = selectedArticle.html.match(/(?:poster=["']|<img[^>]*src=["'])([^"']*?thumbnail[^"']*?)["']/i);
                const videoUrl = videoMatch?.[1] || null;
                const posterUrl = posterMatch?.[1] || null;
                const subtitleMatches = Array.from(selectedArticle.html.matchAll(/src=["']([^"']*?subs_(\w+)\.vtt[^"']*?)["']/gi));
                const descMatch = selectedArticle.html.match(/<p[^>]*>([\s\S]*?)<\/p>/i);
                const descText = (descMatch?.[1] || '').replace(/<[^>]*>/g, '').trim();

                return (
                  <div>
                    {videoUrl ? (
                      <div className="mb-6 overflow-hidden rounded-lg bg-(--chip)">
                        <video controls autoPlay preload="metadata" poster={posterUrl || undefined} className="w-full" style={{ maxHeight: '500px' }} crossOrigin="anonymous">
                          <source src={videoUrl} type="video/webm" />
                          {subtitleMatches.map(([, url, lang]) => (
                            <track key={lang} kind="subtitles" src={url} srcLang={lang} label={lang.toUpperCase()} />
                          ))}
                        </video>
                      </div>
                    ) : (
                      <div
                        className="mb-6 flex flex-col items-center gap-2 text-center"
                        style={{ padding: 26, borderRadius: 20, background: STRIPES, border: "1px solid var(--line)" }}
                      >
                        <MsIcon name="play_circle" size={48} className="mx-auto mb-2" style={{ color: "var(--pink)" }} />
                        <span style={{ ...microLabel, fontSize: 12 }}>
                          Video present, but this browser has no decoder for it
                        </span>
                      </div>
                    )}
                    {descText && <p className="text-sm leading-relaxed text-(--text)">{descText}</p>}
                  </div>
                );
              })() : (
                <div className="kb-article-content" dangerouslySetInnerHTML={{ __html: selectedArticle.html }} />
              )}
            </div>
          ) : articlesLoading ? (
            <div className="flex items-center justify-center py-12">
              <MsIcon name="progress_activity" size={24} className="puff-motion animate-pulse" style={{ color: "var(--pink)" }} />
              <span className="ml-3 text-sm text-(--text2)">Loading articles...</span>
            </div>
          ) : articlesError ? (
            <div className="py-8 text-center">
              <p className="text-sm text-(--text)">Could not load articles for this library.</p>
            </div>
          ) : articles.length === 0 ? (
            <div className="py-8 text-center">
              <MsIcon name="inbox" size={36} className="mx-auto mb-3" style={{ color: "var(--text3)" }} />
              <p className="text-sm text-(--text2)">No articles found.</p>
            </div>
          ) : (
            <div>
              <p className="mb-3 text-xs text-(--text2)">
                Showing {articles.length} of {totalArticles.toLocaleString()} articles
              </p>
              <div className="divide-y divide-(--line)">
                {articles.map((art) => (
                  <button
                    key={art.url}
                    onClick={() => openArticle(art)}
                    disabled={articleLoading}
                    className="kb-article-item"
                  >
                    <span className="flex items-center gap-3">
                      <MsIcon
                        name={isVideo ? "play_circle" : "description"}
                        size={18}
                        style={{ color: isVideo ? "var(--pink)" : "var(--sky)" }}
                      />
                      <span className="text-left text-sm text-(--text)">{art.title}</span>
                    </span>
                    <MsIcon name="chevron_right" size={16} style={{ color: "var(--text3)" }} />
                  </button>
                ))}
              </div>
              {(hasMore || page > 0) && (
                <div className="mt-4 flex justify-center gap-3">
                  {page > 0 && (
                    <button onClick={() => fetchArticles(page - 1, articleSearch || undefined)} className="inline-flex cursor-pointer items-center gap-2 rounded-full transition-transform hover:-translate-y-0.5" style={{ height: 38, padding: "0 18px", background: "var(--card2)", border: "1px solid var(--line)", color: "var(--text2)", fontFamily: NUNITO, fontWeight: 700, fontSize: 14 }}>
                      Previous
                    </button>
                  )}
                  {hasMore && (
                    <button onClick={() => fetchArticles(page + 1, articleSearch || undefined)} className="inline-flex cursor-pointer items-center gap-2 rounded-full transition-transform hover:-translate-y-0.5" style={{ height: 38, padding: "0 18px", background: "var(--card2)", border: "1px solid var(--line)", color: "var(--text2)", fontFamily: NUNITO, fontWeight: 700, fontSize: 14 }}>
                      Next
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </Panel>
      )}

      {/* About Tab */}
      {tab === "about" && (
        <Panel>
          <h3
            style={{
              margin: "0 0 14px",
              fontFamily: FREDOKA,
              fontWeight: 600,
              fontSize: 21,
              color: "var(--text)",
            }}
          >
            Library details
          </h3>
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <dt style={microLabel}>Collection</dt>
            <dd style={{ fontFamily: JBMONO, fontWeight: 600, fontSize: 13, color: "var(--text)" }}>{library.collection}</dd>
            <dt style={microLabel}>Zone</dt>
            <dd style={{ fontFamily: NUNITO, fontWeight: 600, fontSize: 15, color: "var(--text)" }}>{zoneBadge(library.zone).long}</dd>
            {chunkCount(library) > 0 && (
              <>
                <dt style={microLabel}>Indexed Chunks</dt>
                <dd style={{ fontFamily: NUNITO, fontWeight: 600, fontSize: 15, color: "var(--text)" }}>{chunkCount(library).toLocaleString()}</dd>
              </>
            )}
            {formatSize(library.size) && (
              <>
                <dt style={microLabel}>Size</dt>
                <dd style={{ fontFamily: NUNITO, fontWeight: 600, fontSize: 15, color: "var(--text)" }}>{formatSize(library.size)}</dd>
              </>
            )}
            <dt style={microLabel}>Total Articles</dt>
            <dd style={{ fontFamily: NUNITO, fontWeight: 600, fontSize: 15, color: "var(--text)" }}>{totalArticles.toLocaleString()}</dd>
          </dl>
        </Panel>
      )}
    </div>
  );
}
