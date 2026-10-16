"""Wikibooks: free how-to books and textbooks, written together, CC BY-SA 4.0.

Asked only online, when someone searches (there is no saved list):

* **search**: the site's own search (``en.wikibooks.org/w/api.php``,
  ``list=search``) for the words. Its answers are chapters; a chapter's book
  is the part of its title before the first ``/``. A book is listed only
  when its OWN title holds the words (evidence or nothing: "Muggles' Guide
  to Harry Potter" has a chapter about a wedding, it is not a wedding book).
  For the few books kept, one more request reads how many pages each has and
  how big they are, so the size is shown before anyone says yes.
* **the book**: its front page and its sub-pages (at most :data:`MAX_PAGES`),
  each read once as plain text through the site's text API, one request a
  page, into a book folder (:mod:`puffpai.knowledge.text_books`).

Ids are ``wikibooks:<page number of the book's front page>``. Addresses are
built by code from the site's own answers, never taken from anyone else.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Callable, Iterable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

CATALOG_ID = "wikibooks"
ID_PREFIX = "wikibooks"
SERVICE = "wikibooks_books"
HOST = "en.wikibooks.org"
LICENSE = "CC-BY-SA-4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
LICENSE_PLAIN = "CC BY-SA 4.0, free to use, even in something you sell"
MAX_PROPOSED = 3
MAX_PAGES = 300
SEARCH_LIMIT = 50
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_PAGE_BYTES = 2 * 1024 * 1024
#: Plain text kept from a page's wiki text, roughly (measured 0.6 to 0.9).
TEXT_SHARE = 0.8
MAXLAG_SECONDS = 5
#: A book this small is an empty front page, not a book.
MIN_WIKI_BYTES = 3000

_ID_RE = re.compile(r"^wikibooks:([1-9][0-9]{0,9})$")

Getter = Callable[..., tuple[Optional[bytes], str]]


class WikibooksError(RuntimeError):
    """Plain words for why Wikibooks could not be read."""


def _hosts() -> set[str]:
    try:
        from puffpai.security import egress_doors
        door = egress_doors.door(SERVICE)
    except Exception:  # noqa: BLE001 - no door table: nothing is allowed
        return set()
    return {h for h in (door.hosts if door is not None else ()) if h == HOST}


def _fetch(getter: Optional[Getter]) -> Getter:
    if getter is not None:
        return getter
    from puffpai import source_fetch

    def fetch(url: str, **kwargs: Any) -> tuple[Optional[bytes], str]:
        return source_fetch.get_small(url, service=SERVICE, **kwargs)
    return fetch


def api_url(params: dict[str, Any]) -> str:
    query = {"action": "query", "format": "json", "formatversion": "2",
             "maxlag": str(MAXLAG_SECONDS)}
    query.update({k: str(v) for k, v in params.items()})
    return f"https://{HOST}/w/api.php?" + urllib.parse.urlencode(query)


def ask(params: dict[str, Any], *, getter: Optional[Getter] = None,
        max_bytes: int = MAX_JSON_BYTES) -> dict[str, Any]:
    """One API request, parsed. Raises WikibooksError."""
    body, reason = _fetch(getter)(api_url(params), allowed_hosts=_hosts(),
                                  max_bytes=max_bytes, honour_robots=False)
    if body is None:
        raise WikibooksError(f"Wikibooks did not answer: {reason}")
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise WikibooksError("Wikibooks' answer could not be read.") from exc
    if not isinstance(data, dict) or isinstance(data.get("error"), dict):
        raise WikibooksError("Wikibooks said no.")
    return data


def _query(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = (data.get("query") or {}).get(key) if isinstance(data.get("query"), dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


def _book_title(title: Any) -> str:
    text = " ".join(str(title or "").split())
    return text.split("/", 1)[0].strip()[:200]


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _sizes(roots: list[str], getter: Optional[Getter]) -> dict[str, dict[str, Any]]:
    """``{title: {pageid, pages, wiki_bytes}}`` for books that exist."""
    data = ask({"prop": "info", "titles": "|".join(roots)}, getter=getter)
    out: dict[str, dict[str, Any]] = {}
    for page in _query(data, "pages"):
        if page.get("missing") or page.get("ns") != 0 or not isinstance(page.get("pageid"), int):
            continue
        title = str(page.get("title") or "")
        if "/" in title:
            continue
        out[title] = {"pageid": page["pageid"], "pages": 1,
                      "wiki_bytes": int(page.get("length") or 0)}
    for title, row in out.items():
        sub = ask({"generator": "allpages", "gapnamespace": "0",
                   "gapprefix": title + "/", "gaplimit": "max", "prop": "info"},
                  getter=getter)
        pages = _query(sub, "pages")[:MAX_PAGES - 1]
        row["pages"] += len(pages)
        row["wiki_bytes"] += sum(int(p.get("length") or 0) for p in pages)
    return out


def search_live(terms: list[str], *, getter: Optional[Getter] = None,
                limit: int = MAX_PROPOSED) -> list[dict[str, Any]]:
    """Books whose own title holds the words, best first. Raises WikibooksError."""
    from puffpai.catalogs import knowledge_resolver as kr

    words = [t for t in terms if re.match(r"^[a-z0-9]{2,30}$", str(t))][:4]
    if not words:
        return []
    data = ask({"list": "search", "srsearch": " ".join(words), "srnamespace": "0",
                "srlimit": str(SEARCH_LIMIT), "srprop": ""}, getter=getter)
    hits: dict[str, int] = {}
    for row in _query(data, "search"):
        root = _book_title(row.get("title"))
        if root:
            hits[root] = hits.get(root, 0) + 1
    scored = []
    for pos, (root, count) in enumerate(hits.items()):
        score, matched = kr.score_entry({"title": root}, words)
        if score and kr.enough_words(words, matched):
            scored.append((score, count, pos, root, matched))
    scored.sort(key=lambda s: (-s[0], -s[1], s[2]))
    kept = scored[:limit]
    if not kept:
        return []
    sizes = _sizes([s[3] for s in kept], getter)
    out = []
    for score, count, _pos, root, matched in kept:
        if root in sizes and sizes[root]["wiki_bytes"] >= MIN_WIKI_BYTES:
            out.append(dict(sizes[root], title=root, hits=count, _score=score,
                            _matched=matched))
    return out


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------

def page_url(title: str) -> str:
    return f"https://{HOST}/wiki/" + urllib.parse.quote(title.replace(" ", "_"), safe="/:")


def candidate(row: dict[str, Any], *, match_terms: Iterable[str] = (),
              verified_at: Optional[str] = None) -> dict[str, Any]:
    from puffpai.knowledge import text_books

    pages = int(row.get("pages") or 1)
    kept = int(int(row.get("wiki_bytes") or 0) * TEXT_SHARE) or None
    if kept:
        kept = min(kept, text_books.MAX_BOOK_BYTES)
    title = str(row["title"])
    return {
        "id": f"{ID_PREFIX}:{int(row['pageid'])}",
        "kind": "knowledge",
        "tier": "wikibooks",
        "name": title[:80],
        "what_it_is": (f"A free how-to book from Wikibooks, written together by "
                       f"many people, {pages} page{'s' if pages != 1 else ''}.")[:240],
        "source_catalog": "Wikibooks",
        "url": page_url(title),
        "host": HOST,
        "sample_endpoint": None,
        "auth": "none",
        "license": LICENSE,
        "commercial_ok": True,
        "personal_use_only": False,
        "size_bytes": kept,
        "size_ceiling": text_books.MAX_BOOK_BYTES,
        "pages": pages,
        "local_or_cloud": "local",
        "needs": [],
        "match_terms": [str(t) for t in match_terms],
        "verified_at": verified_at,
        "book": "",
        "flavour": "",
        "meta4_url": "",
        "license_url": LICENSE_URL,
        "license_plain": LICENSE_PLAIN,
        "fetchable": True,
        "why_not": "",
        "audience": "general",
        "category": "wikibooks",
        "updated": "",
        "sends": [],
        "title": title,
        "pageid": int(row["pageid"]),
    }


def find(terms: list[str], *, live: bool = False, getter: Optional[Getter] = None,
         limit: int = MAX_PROPOSED) -> dict[str, Any]:
    """``{candidates, searched, note}``. Never raises. Offline: nothing (no saved list)."""
    if not live:
        return {"candidates": [], "searched": "not searched", "note": ""}
    try:
        rows = search_live(terms, getter=getter, limit=limit)
    except WikibooksError as exc:
        return {"candidates": [], "searched": "not searched", "note": str(exc)}
    today = _today()
    cands = []
    for row in rows:
        cand = candidate(row, match_terms=row.get("_matched") or [], verified_at=today)
        cand["_score"] = int(row.get("_score") or 0)
        cand["reason"] = "Matched on: " + ", ".join(row.get("_matched") or []) + "."
        cands.append(cand)
    return {"candidates": cands, "searched": "live", "note": ""}


def split_id(ident: Any) -> Optional[int]:
    match = _ID_RE.match(str(ident or "").strip())
    return int(match.group(1)) if match else None


def candidate_by_id(ident: str, *, live: bool = False,
                    getter: Optional[Getter] = None) -> Optional[dict[str, Any]]:
    """A book by its id, asked of Wikibooks (online only)."""
    num = split_id(ident)
    if num is None or not live:
        return None
    try:
        data = ask({"prop": "info", "pageids": str(num)}, getter=getter)
        pages = [p for p in _query(data, "pages") if p.get("pageid") == num
                 and p.get("ns") == 0 and "/" not in str(p.get("title") or "")]
        if not pages:
            return None
        title = str(pages[0]["title"])
        sizes = _sizes([title], getter)
    except WikibooksError:
        return None
    if title not in sizes:
        return None
    return candidate(dict(sizes[title], title=title), verified_at=_today())


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# the book itself
# ---------------------------------------------------------------------------

def book_meta(cand: dict[str, Any]) -> dict[str, Any]:
    title = str(cand.get("title") or cand.get("name"))
    return {
        "id": cand["id"], "kind": "wikibooks", "catalog": "Wikibooks",
        "title": f"Wikibooks: {title}"[:120],
        "language": "en", "license": LICENSE, "license_url": LICENSE_URL,
        "license_plain": LICENSE_PLAIN,
        "credit": (f"{title}, from Wikibooks ({page_url(title)}), by its many "
                   f"writers, under CC BY-SA 4.0. Each page names its address."),
        "commercial_use": True, "personal_use_only": False,
        "source_url": page_url(title),
        "note": "The book's front page and its sub-pages, read as plain text.",
    }


def titles(cand: dict[str, Any], *, getter: Optional[Getter] = None) -> list[str]:
    """The book's front page, then its sub-pages, at most :data:`MAX_PAGES`."""
    title = str(cand.get("title") or cand.get("name"))
    data = ask({"list": "allpages", "apnamespace": "0", "apprefix": title + "/",
                "aplimit": "max"}, getter=getter)
    subs = [str(p.get("title")) for p in _query(data, "allpages") if p.get("title")]
    return [title] + subs[:MAX_PAGES - 1]


def page(title: str, *, getter: Optional[Getter] = None) -> Optional[dict[str, Any]]:
    data = ask({"prop": "extracts|revisions|info", "explaintext": "1", "rvprop": "ids",
                "titles": title}, getter=getter, max_bytes=MAX_PAGE_BYTES)
    for row in _query(data, "pages"):
        text = str(row.get("extract") or "")
        if row.get("missing") or not text.strip():
            return None
        revs = row.get("revisions") or [{}]
        return {"title": str(row.get("title") or title), "url": page_url(str(row.get("title") or title)),
                "revision": str((revs[0] or {}).get("revid") or row.get("lastrevid") or ""),
                "text": text}
    return None


def pages(cand: dict[str, Any], *, getter: Optional[Getter] = None,
          cancel: Any = None,
          on_progress: Optional[Callable[[dict[str, Any]], None]] = None
          ) -> Iterable[dict[str, Any]]:
    """Yield the book's pages as text, one polite request each."""
    from puffpai.knowledge import text_books

    names = titles(cand, getter=getter)
    total = 0
    for n, name in enumerate(names, 1):
        if cancel is not None and cancel.is_set():
            return
        try:
            got = page(name, getter=getter)
        except WikibooksError as exc:
            logger.info("page skipped: %s", exc)
            got = None
        if got:
            total += len(got["text"].encode("utf-8"))
            yield got
        if on_progress is not None:
            try:
                on_progress({"done": n, "total": len(names), "downloaded": total})
            except Exception:  # noqa: BLE001
                pass
        if total >= text_books.MAX_BOOK_BYTES:
            return


__all__ = ["CATALOG_ID", "ID_PREFIX", "SERVICE", "HOST", "LICENSE", "WikibooksError",
           "api_url", "ask", "search_live", "candidate", "find", "split_id",
           "candidate_by_id", "book_meta", "titles", "page", "pages"]
