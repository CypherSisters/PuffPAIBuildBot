import { apiGet, apiDelete } from '../api';
import type { KnowledgeLibrary, LibraryArticle, ArticleContent } from '../../_interfaces/api';

export async function getLibraries(): Promise<KnowledgeLibrary[]> {
  const res = await apiGet<{ success: boolean; libraries: KnowledgeLibrary[] }>('/knowledge/libraries');
  return res.libraries || [];
}

/** One source or pack this build chose, offered or downloaded. */
export type BuildSourceStatus =
  | 'indexed'
  | 'fetched_not_indexed'
  | 'selected_not_downloaded'
  | 'offered';

export type BuildSource = {
  id: string;
  label: string;
  kind: string;
  /** 'documents' = bytes that can be indexed; 'live_source' = an address the
   *  bot reads, such as an API catalog. A live source is never counted as
   *  indexed documents. */
  form: 'documents' | 'live_source';
  status: BuildSourceStatus;
  domain: string;
  license: string;
  license_url: string;
  commercial_use: boolean;
  what_it_is: string;
  source_url: string;
  size_mb: number | null;
  size_label: string;
  selected_by: string[];
  command: string;
  note: string;
  filename?: string;
  bytes?: number;
  fetched_at?: string;
};

/** Read-only: what the wizard chose against what is actually on this disk.
 *  Downloads nothing — a not-downloaded source comes back with the command. */
export async function getBuildSources(): Promise<BuildSource[]> {
  try {
    const res = await apiGet<{ success: boolean; items: BuildSource[] }>(
      '/knowledge/sources',
    );
    return res.items || [];
  } catch {
    return [];
  }
}

export async function searchKnowledge(query: string): Promise<any[]> {
  const res = await apiGet<{ success: boolean; results: any[] }>(`/knowledge/search?q=${encodeURIComponent(query)}`);
  return res.results || [];
}

export type TitleSearchResult = {
  title: string;
  url: string;
  collection: string;
  library_name: string;
  mime_type: string;
};

export async function searchTitles(
  query: string,
  limit: number = 50,
): Promise<{ results: TitleSearchResult[]; total: number }> {
  const res = await apiGet<{
    success: boolean;
    results: TitleSearchResult[];
    total: number;
  }>(`/knowledge/search/titles?q=${encodeURIComponent(query)}&limit=${limit}`);
  return { results: res.results || [], total: res.total || 0 };
}

/*
 * THERE IS NO FULL-TEXT SEARCH IN THIS BUILD.
 *
 * `searchFulltext()` used to live here, calling GET /knowledge/search/fulltext
 * for an FTS5 pass over article bodies. That route does not exist: this backend
 * serves exactly two knowledge search endpoints, /knowledge/search (semantic)
 * and /knowledge/search/titles. Nor does it ever emit the `fts_ready` flag the
 * UI gated the feature on, so the flag was permanently undefined.
 *
 * The cost of keeping it was not just a wasted 404 per keystroke. The library
 * Search tab rendered "No matches in this library" after the call failed — a
 * confident empty result for a query that had never run. Callers now use
 * searchTitles + searchKnowledge and say plainly, once, that article bodies are
 * not indexed here (see app/dashboard/knowledge/page.tsx).
 *
 * If an FTS shard builder ever ships, add the function back WITH its route.
 */

export async function uploadDocument(file: File, zone: 1 | 2): Promise<{ success: boolean }> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('zone', String(zone));

  // Same-origin through the authenticated proxy: the server attaches the
  // backend API key, so this works from any LAN device (a phone on /send, a
  // laptop browser) and not just the kiosk that had a key in localStorage.
  // No Authorization header — the client holds no credential.
  const res = await fetch('/api/pai/knowledge/upload', {
    method: 'POST',
    body: formData,
  });
  return res.json();
}

export async function deleteLibrary(name: string): Promise<{ success: boolean }> {
  return apiDelete(`/knowledge/library/${encodeURIComponent(name)}`);
}

export async function browseLibrary(
  collection: string,
  page: number = 0,
  limit: number = 50,
  search?: string,
): Promise<{ articles: LibraryArticle[]; total: number; has_more: boolean }> {
  const params = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (search) params.set('q', search);
  const res = await apiGet<{
    success: boolean;
    articles: LibraryArticle[];
    total: number;
    has_more: boolean;
  }>(`/knowledge/library/${encodeURIComponent(collection)}/articles?${params}`);
  return { articles: res.articles || [], total: res.total || 0, has_more: res.has_more || false };
}

/** Same-origin URL for embedding an uploaded document's original file
 *  (PDF viewer). Proxied through the Next server so the browser doesn't need
 *  to send an API key on the <embed>/<iframe> request. */
export function librarySourceEmbedUrl(collection: string): string {
  return `/api/knowledge/source?name=${encodeURIComponent(collection)}`;
}

/** Fetch a user-uploaded document's text — the retained original reconstructed
 *  from the indexed chunks when no viewable original file is available. */
export async function getLibrarySourceText(
  collection: string,
): Promise<{ text: string; note: string } | null> {
  try {
    const res = await apiGet<{ success: boolean; text: string; note: string }>(
      `/knowledge/library/${encodeURIComponent(collection)}/source?format=text`,
    );
    if (!res.success) return null;
    return { text: res.text || '', note: res.note || '' };
  } catch {
    return null;
  }
}

export async function getArticle(
  collection: string,
  url: string,
  opts?: { fresh?: boolean },
): Promise<ArticleContent | null> {
  try {
    const res = await apiGet<{ success: boolean; article: ArticleContent }>(
      `/knowledge/library/${encodeURIComponent(collection)}/article?url=${encodeURIComponent(url)}`,
      // Deep-linked "show me X" opens the same article URL repeatedly; skip the
      // HTTP cache so a re-show never renders a stale copy on iframe remount.
      opts?.fresh ? { cache: "no-store" } : undefined,
    );
    if (!res.article) return null;

    // Rewrite asset URLs to the range-enabled same-origin asset route.
    // The backend emits these already prefixed with the keyed proxy path,
    // "/api/pai/knowledge/library/<col>/asset?path=...". The optional
    // "(?:/api/pai)?" is load-bearing: without it the regex matched only the
    // "/knowledge/library/.../asset" tail and left "/api/pai" in front, so the
    // result was "/api/pai/api/knowledge/asset?..." — a doubly-prefixed 404
    // that made TED videos, posters and subtitles silently fail to load (black
    // player, 0:00). Consuming the prefix yields the correct
    // "/api/knowledge/asset?collection=<col>&path=<path>", which streams with
    // HTTP range support so the <video> plays and seeks.
    if (res.article.html) {
      res.article.html = res.article.html.replace(
        /(?:\/api\/pai)?\/knowledge\/library\/([^/]+)\/asset\?path=([^"'&\s]+)/gi,
        (_match, col, path) =>
          `/api/knowledge/asset?collection=${encodeURIComponent(col)}&path=${encodeURIComponent(decodeURIComponent(path))}`,
      );
    }

    return res.article;
  } catch {
    return null;
  }
}
