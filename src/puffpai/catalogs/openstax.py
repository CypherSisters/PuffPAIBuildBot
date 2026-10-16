"""OpenStax: free, peer-reviewed textbooks, matched to a goal.

The list of books comes from OpenStax's own content API
(``openstax.org/apps/cms/api/v2/pages/?type=books.Book``), which states each
book's license by name (``license_name`` + ``license_version``). A dated copy
of that list ships with BuildBot, so a wizard can offer a textbook with no
network; the live list is asked only when the owner searches, and then at
most once a day: the list OpenStax sent (about 370 KB) is kept, dated, in the
state folder (:data:`LIVE_LIST_FILE`) and used for a day's searches. Without
this, the whole list is downloaded again on every search.

Licenses, mapped to SPDX ids by code:

* Creative Commons Attribution -> ``CC-BY-<version>``: free to use, even in
  something you sell.
* Creative Commons Attribution-NonCommercial-ShareAlike ->
  ``CC-BY-NC-SA-<version>``: offered, labelled **free for personal use only**,
  fetched by the owner's own computer, and never included when a build is
  shared or sold. Never ticked by default.
* no license stated -> not offered for download.

A textbook is read from its public web pages (``openstax.org/books/<book>/
pages/...``), whose list OpenStax publishes for exactly this in its sitemap.
One polite request a second; at most :data:`MAX_PAGES` pages and
:data:`text_books.MAX_BOOK_BYTES` of text. The PDF editions are about 160 MB,
far over the size limit, so they are never fetched.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import urllib.parse
from html.parser import HTMLParser
from typing import Any, Callable, Iterable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

CATALOG_ID = "openstax"
ID_PREFIX = "openstax"
HOST = "openstax.org"
LIST_URL = (f"https://{HOST}/apps/cms/api/v2/pages/?" + urllib.parse.urlencode({
    "type": "books.Book", "limit": "250",
    "fields": "title,license_name,license_version,license_url,book_state,"
              "book_subjects,book_categories,description"}))
SNAPSHOT_NAME = "openstax_books_2026-09-24.json"
MAX_PAGES = 600
MAX_LIST_BYTES = 4 * 1024 * 1024
MAX_SITEMAP_BYTES = 2 * 1024 * 1024
MAX_PAGE_BYTES = 3 * 1024 * 1024
CACHE_SECONDS = 600
#: The live book list is asked for at most once in this long.
LIVE_LIST_SECONDS = 24 * 3600
LIVE_LIST_FILE = "openstax_books_live.json"
#: Measured on three textbook pages (1 KB to 39 KB of text from
#: web pages of 330 KB to 440 KB): a rough middle. Sizes shown say "about".
TEXT_PER_PAGE = 20 * 1024
HTML_PER_PAGE = 340 * 1024
#: Book states that are real books a person can still read online.
STATES = ("live", "retired")
PERSONAL_USE = "free for personal use only"
#: A title or subject match (2 or more); a blurb word alone (1) is not enough.
MIN_SCORE = 2

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$")
_ID_RE = re.compile(r"^openstax:([a-z0-9][a-z0-9-]{1,80})$")
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_TAG_RE = re.compile(r"<[^>]+>")

Getter = Callable[..., tuple[Optional[bytes], str]]


class OpenStaxError(RuntimeError):
    """Plain words for why OpenStax could not be read."""


def _hosts() -> set[str]:
    """The host of the named outside service; nothing when it is not listed."""
    try:
        from puffpai.security import egress_doors
        door = egress_doors.door("openstax_books")
    except Exception:  # noqa: BLE001 - no door table: nothing is allowed
        return set()
    return {h for h in (door.hosts if door is not None else ()) if h == HOST}

def _logged_fetch(source_fetch: Any) -> Getter:
    """source_fetch.get_small, logged and switched under this site's own row
    on "What leaves your computer" (openstax_books)."""
    def fetch(url: str, **kwargs: Any) -> tuple[Optional[bytes], str]:
        return source_fetch.get_small(url, service="openstax_books", **kwargs)
    return fetch



def _get(url: str, max_bytes: int, getter: Optional[Getter], robots: bool = True) -> bytes:
    from puffpai import source_fetch
    fetch = getter or _logged_fetch(source_fetch)
    body, reason = fetch(url, allowed_hosts=_hosts(), max_bytes=max_bytes,
                         honour_robots=robots)
    if body is None:
        raise OpenStaxError(f"OpenStax did not answer: {reason}")
    return body


# ---------------------------------------------------------------------------
# licenses
# ---------------------------------------------------------------------------

_LICENSE_NAMES = {
    "creative commons attribution license": "CC-BY",
    "creative commons attribution-sharealike license": "CC-BY-SA",
    "creative commons attribution-noncommercial-sharealike license": "CC-BY-NC-SA",
    "creative commons attribution-noncommercial license": "CC-BY-NC",
}


def spdx(license_name: Any, version: Any = None, url: Any = None) -> str:
    """SPDX id for OpenStax's license name, or ``""`` when it cannot be told."""
    base = _LICENSE_NAMES.get(" ".join(str(license_name or "").lower().split()))
    if not base:
        return ""
    ver = str(version or "").strip()
    if not re.match(r"^\d\.\d$", ver):
        found = re.search(r"/(\d\.\d)/?$", str(url or ""))
        ver = found.group(1) if found else ""
    return f"{base}-{ver}" if ver else ""


def commercial_ok(license_id: str) -> Optional[bool]:
    if not license_id:
        return None
    return "-NC" not in license_id


def plain_license(license_id: str) -> str:
    if not license_id:
        return "license not stated, so it cannot be downloaded here"
    if commercial_ok(license_id):
        return f"{license_id}, free to use, even in something you sell"
    return f"{license_id}, {PERSONAL_USE}"


# ---------------------------------------------------------------------------
# the book list
# ---------------------------------------------------------------------------

def _plain(text: Any, limit: int) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", str(text or ""))).split())[:limit]


def parse_list(body: bytes) -> list[dict[str, Any]]:
    """OpenStax's book list as saved-list rows. Untrusted data: shapes checked."""
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise OpenStaxError("OpenStax's book list could not be read.") from exc
    out = []
    for item in (data.get("items") if isinstance(data, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        meta = item.get("meta") or {}
        slug = str(meta.get("slug") or "")
        if not _SLUG_RE.match(slug) or str(meta.get("locale") or "en") != "en":
            continue
        state = str(item.get("book_state") or "")
        if state not in STATES:
            continue
        subjects = [str(s.get("subject_name")) for s in item.get("book_subjects") or []
                    if isinstance(s, dict) and s.get("subject_name")]
        subjects += [str(s.get("subject_category")) for s in item.get("book_categories") or []
                     if isinstance(s, dict) and s.get("subject_category")]
        out.append({
            "slug": slug,
            "title": _plain(item.get("title"), 100),
            "state": state,
            "license": spdx(item.get("license_name"), item.get("license_version"),
                            item.get("license_url")),
            "license_url": str(item.get("license_url") or "")[:200]
            if str(item.get("license_url") or "").startswith("https://creativecommons.org/")
            else "",
            "subjects": list(dict.fromkeys(subjects))[:6],
            "summary": _plain(item.get("description"), 300),
        })
    return out


def sitemap_url(slug: str) -> str:
    return f"https://{HOST}/rex/sitemaps/{slug}.xml"


def page_urls(body: bytes, slug: str) -> list[str]:
    """The book's own page addresses from its sitemap. Anything else is dropped."""
    prefix = f"https://{HOST}/books/{slug}/pages/"
    out = []
    for loc in _LOC_RE.findall(body.decode("utf-8", errors="replace")):
        url = html.unescape(loc)
        rest = url[len(prefix):] if url.startswith(prefix) else ""
        if rest and re.match(r"^[a-z0-9][a-z0-9-]{0,160}$", rest) and url not in out:
            out.append(url)
        if len(out) >= MAX_PAGES:
            break
    return out


def build_snapshot(*, getter: Optional[Getter] = None, taken_at: str) -> dict[str, Any]:
    """The saved list (how the shipped file was made): books + page counts."""
    books = parse_list(_get(LIST_URL, MAX_LIST_BYTES, getter))
    for book in books:
        try:
            book["pages"] = len(page_urls(_get(sitemap_url(book["slug"]),
                                               MAX_SITEMAP_BYTES, getter), book["slug"]))
        except OpenStaxError:
            book["pages"] = None
    return {"schema": "openstax_books/v1", "taken_at": taken_at, "source": LIST_URL,
            "books": books}


def snapshot_path() -> str:
    from puffpai.catalogs import live
    return os.path.join(live.repo_root(), "config", "catalog_snapshots", SNAPSHOT_NAME)


def load_snapshot(path: Optional[str] = None) -> dict[str, Any]:
    try:
        with open(path or snapshot_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"taken_at": None, "books": []}
    books = []
    for row in (data.get("books") if isinstance(data, dict) else None) or []:
        if isinstance(row, dict) and _SLUG_RE.match(str(row.get("slug") or "")):
            books.append(row)
    return {"taken_at": data.get("taken_at"), "books": books}


_LIVE: dict[str, Any] = {}
_LIVE_LOCK = threading.Lock()


def _live_list_path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "catalog_cache", LIVE_LIST_FILE)


def _read_live_list() -> Optional[dict[str, Any]]:
    """The kept live list, if it is younger than a day and readable."""
    from puffpai import fsutil
    data = fsutil.read_json(_live_list_path(), default=None)
    if not isinstance(data, dict):
        return None
    at = data.get("fetched_at")
    books = [b for b in data.get("books") or []
             if isinstance(b, dict) and _SLUG_RE.match(str(b.get("slug") or ""))]
    if not isinstance(at, (int, float)) or not books:
        return None
    if not 0 <= time.time() - at < LIVE_LIST_SECONDS:
        return None
    return {"books": books, "at": float(at), "date": str(data.get("date") or "")}


def _keep_live_list(books: list[dict[str, Any]], at: float) -> None:
    from puffpai import fsutil
    path = _live_list_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fsutil.atomic_write_json(path, {"schema": "openstax_live_list/v1",
                                        "fetched_at": at, "date": _today(),
                                        "books": books})
    except OSError as exc:  # the search still has its answer
        logger.info("openstax: the book list was not kept: %s", exc)


def list_live(getter: Optional[Getter] = None) -> list[dict[str, Any]]:
    """OpenStax's own book list: asked at most once a day (kept on disk)."""
    with _LIVE_LOCK:
        kept = _LIVE.get("books")
        if getter is None and kept and time.time() - _LIVE["at"] < LIVE_LIST_SECONDS:
            return kept
        if getter is None:
            saved = _read_live_list()
            if saved is not None:
                _LIVE.update(saved)
                return saved["books"]
    books = parse_list(_get(LIST_URL, MAX_LIST_BYTES, getter))
    if getter is None:
        now = time.time()
        _keep_live_list(books, now)
        with _LIVE_LOCK:
            _LIVE.update({"books": books, "at": now, "date": _today()})
    return books


def live_list_date() -> Optional[str]:
    """The day the live list in use was fetched, if one is in use."""
    with _LIVE_LOCK:
        return _LIVE.get("date") or None


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------

def _entry(book: dict[str, Any]) -> dict[str, Any]:
    return {"title": book.get("title") or "", "name": book.get("slug", "").replace("-", " "),
            "tags": book.get("subjects") or [], "category": "",
            "summary": book.get("summary") or ""}


def estimate(pages: Any) -> tuple[Optional[int], Optional[int]]:
    """(text kept, traffic) in bytes for a page count, or (None, None)."""
    if not isinstance(pages, int) or pages <= 0:
        return None, None
    return pages * TEXT_PER_PAGE, pages * HTML_PER_PAGE


def candidate(book: dict[str, Any], *, match_terms: Iterable[str] = (),
              verified_at: Optional[str] = None) -> dict[str, Any]:
    from puffpai.knowledge import text_books

    license_id = str(book.get("license") or "")
    ok = commercial_ok(license_id)
    kept, traffic = estimate(book.get("pages"))
    older = " An older edition, still free to read." if book.get("state") == "retired" else ""
    what = (f"A free OpenStax textbook{older} " + str(book.get("summary") or "")).strip()
    why_not = ""
    if not license_id:
        why_not = "OpenStax does not state its license, so it cannot be downloaded here."
    elif kept is None:
        why_not = ("OpenStax does not list this book's pages any more, so it "
                   "cannot be read here.")
    name = str(book.get("title") or book["slug"])
    if ok is False:
        name = f"{name} ({PERSONAL_USE})"
    return {
        "id": f"{ID_PREFIX}:{book['slug']}",
        "kind": "knowledge",
        "tier": "openstax",
        "name": name[:80],
        "what_it_is": what[:240],
        "source_catalog": "OpenStax",
        "url": f"https://{HOST}/details/books/{book['slug']}",
        "host": HOST,
        "sample_endpoint": None,
        "auth": "none",
        "license": license_id or "unknown",
        "commercial_ok": ok,
        "personal_use_only": ok is False,
        "size_bytes": kept,
        "traffic_bytes": traffic,
        "size_ceiling": text_books.MAX_BOOK_BYTES,
        "pages": book.get("pages") if isinstance(book.get("pages"), int) else None,
        "local_or_cloud": "local",
        "needs": [],
        "match_terms": [str(t) for t in match_terms],
        "verified_at": verified_at,
        "book": "",
        "flavour": "",
        "meta4_url": "",
        "license_url": str(book.get("license_url") or ""),
        "license_plain": plain_license(license_id),
        "fetchable": not why_not,
        "why_not": why_not,
        "audience": "general",
        "category": "openstax",
        "updated": "",
        "sends": [],
        "slug": book["slug"],
    }


def find(terms: list[str], *, live: bool = False, getter: Optional[Getter] = None,
         limit: int = 3) -> dict[str, Any]:
    """``{candidates, searched, note, snapshot_date}``. Never raises."""
    from puffpai.catalogs import knowledge_resolver

    snap = load_snapshot()
    books = list(snap.get("books") or [])
    searched, note, when = "snapshot", "", snap.get("taken_at")
    if live:
        try:
            fresh = list_live(getter)
            pages = {b["slug"]: b.get("pages") for b in books}
            for book in fresh:
                book["pages"] = pages.get(book["slug"])
            books, searched = fresh, "live"
            when = (live_list_date() if getter is None else None) or _today()
        except OpenStaxError as exc:
            note = f"OpenStax could not be reached ({exc}), so the saved list was used."
    scored = []
    for pos, book in enumerate(books):
        score, matched = knowledge_resolver.score_entry(_entry(book), terms)
        # a word only in a textbook's blurb ("competitive markets") is not
        # enough; its title or subject has to say it
        if score >= MIN_SCORE:
            scored.append((score, pos, book, matched))
    scored.sort(key=lambda s: (-s[0], s[1]))
    cands = []
    for score, _pos, book, matched in scored[:limit]:
        cand = candidate(book, match_terms=matched, verified_at=when)
        cand["_score"] = score
        cand["reason"] = "Matched on: " + ", ".join(matched) + "."
        cands.append(cand)
    return {"candidates": cands, "searched": searched, "note": note,
            "snapshot_date": snap.get("taken_at")}


def candidate_by_id(ident: str, *, live: bool = False,
                    getter: Optional[Getter] = None) -> Optional[dict[str, Any]]:
    match = _ID_RE.match(str(ident or "").strip())
    if not match:
        return None
    slug = match.group(1)
    snap = load_snapshot()
    for book in snap.get("books") or []:
        if book.get("slug") == slug:
            return candidate(book, verified_at=snap.get("taken_at"))
    if live:
        try:
            for book in list_live(getter):
                if book["slug"] == slug:
                    return candidate(book, verified_at=_today())
        except OpenStaxError:
            return None
    return None


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# reading the pages
# ---------------------------------------------------------------------------

_BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "figcaption", "tr",
          "dt", "dd", "caption", "blockquote", "section", "div"}
_SKIP = {"script", "style", "noscript", "svg", "math", "button", "nav"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}


class _PageText(HTMLParser):
    """The text inside the page's ``data-type="page"`` element. Nothing runs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.skip = 0
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "title":
            self._in_title = True
        if self.depth:
            if tag in _VOID:
                return
            self.depth += 1
            if tag in _SKIP:
                self.skip += 1
            if tag in _BLOCK:
                self.parts.append("\n")
        elif tag == "div" and ("data-type", "page") in attrs:
            self.depth = 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if not self.depth or tag in _VOID:
            return
        if tag in _SKIP and self.skip:
            self.skip -= 1
        self.depth -= 1
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.depth:
            self.title += data
        if self.depth and not self.skip:
            self.parts.append(data)


def page_text(body: bytes) -> tuple[str, str]:
    """(title, text) of one textbook page."""
    parser = _PageText()
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
        parser.close()
    except Exception:  # noqa: BLE001 - a broken page is an empty page
        return "", ""
    text = "".join(parser.parts)
    text = "\n".join(" ".join(line.split()) for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    title = " ".join(parser.title.split()).split(" | ")[0][:200]
    return title, text


def book_meta(cand: dict[str, Any]) -> dict[str, Any]:
    ok = cand.get("commercial_ok")
    title = str(cand.get("name") or cand["slug"])
    return {
        "id": cand["id"], "kind": "openstax", "catalog": "OpenStax",
        "title": f"OpenStax: {title}",
        "language": "en", "license": cand.get("license"),
        "license_url": cand.get("license_url"),
        "license_plain": cand.get("license_plain"),
        "credit": (f"{title}, by OpenStax (Rice University), "
                   f"https://{HOST}/details/books/{cand['slug']}, "
                   f"license {cand.get('license')}."),
        "commercial_use": ok is True,
        "personal_use_only": ok is False,
        "source_url": f"https://{HOST}/details/books/{cand['slug']}",
        "note": "Read from the book's own web pages.",
    }


def pages(cand: dict[str, Any], *, getter: Optional[Getter] = None,
          cancel: Optional[threading.Event] = None,
          on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
          max_bytes: Optional[int] = None) -> Iterable[dict[str, Any]]:
    """Yield the textbook's pages as text, one polite request each."""
    from puffpai.knowledge import text_books

    if not cand.get("fetchable"):
        raise OpenStaxError(str(cand.get("why_not") or "That book cannot be fetched."))
    limit = max_bytes if max_bytes is not None else text_books.MAX_BOOK_BYTES
    slug = str(cand["slug"])
    urls = page_urls(_get(sitemap_url(slug), MAX_SITEMAP_BYTES, getter), slug)
    if not urls:
        raise OpenStaxError("OpenStax lists no pages for that book.")
    total = 0
    for n, url in enumerate(urls, 1):
        if cancel is not None and cancel.is_set():
            return
        try:
            title, text = page_text(_get(url, MAX_PAGE_BYTES, getter))
        except OpenStaxError as exc:
            logger.info("page skipped: %s", exc)
            title, text = "", ""
        if text:
            total += len(text.encode("utf-8"))
            yield {"title": title or url.rsplit("/", 1)[-1], "url": url,
                   "revision": "", "text": text}
        if on_progress is not None:
            try:
                on_progress({"done": n, "total": len(urls), "downloaded": total})
            except Exception:  # noqa: BLE001
                pass
        if total >= limit:
            return


__all__ = ["CATALOG_ID", "HOST", "PERSONAL_USE", "OpenStaxError", "spdx",
           "commercial_ok", "plain_license", "parse_list", "page_urls",
           "build_snapshot", "load_snapshot", "list_live", "candidate", "find",
           "candidate_by_id", "page_text", "book_meta", "pages", "estimate"]
