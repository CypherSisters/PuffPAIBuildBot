"""Project Gutenberg: free public-domain books about a subject.

Two ways to look, the same answer shape:

* **the saved list** (works offline, used while a wizard is drawn): a dated
  snapshot made from Project Gutenberg's own catalog file
  (``pg_catalog.csv``), English books only, with their subjects and shelves.
  Literature (Library of Congress class P: novels, poems, plays) is left out
  of the saved list, because a bot that should know a subject learns it from
  books about it; the live search still finds everything.
* **the live search** through Gutendex (``gutendex.com``), a public search
  over the same catalog, asked only when the owner searches and only for
  books Gutendex marks as not under copyright. When it cannot be reached the
  saved list answers instead, and the result says so.

A book is fetched as Project Gutenberg's own plain-text UTF-8 file from
``www.gutenberg.org`` (the address is built by code from the book's number,
never taken from an answer). Gutenberg's header and licence footer are cut off
before the book is learned, and a short note of where it came from is kept
with the book instead. A file whose header says the book is still under
copyright is refused.

At most :data:`MAX_PROPOSED` books are proposed for one goal.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import threading
import time
import urllib.parse
from typing import Any, Callable, Iterable, Optional

from puffpai.catalogs import goal_words
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

CATALOG_ID = "gutenberg"
ID_PREFIX = "gutenberg"
SEARCH_HOST = "gutendex.com"
DOWNLOAD_HOST = "www.gutenberg.org"
MAX_PROPOSED = 3
LIVE_TERM_LIMIT = 2
CACHE_SECONDS = 600
MAX_SEARCH_BYTES = 2 * 1024 * 1024
#: The public-domain status is the United States' rule; this is the id we
#: record for it (SPDX has no id for "public domain", so a LicenseRef).
LICENSE = "LicenseRef-Public-Domain-US"
LICENSE_URL = "https://www.gutenberg.org/policy/permission.html"
LICENSE_PLAIN = ("public domain in the USA, free to use, even in something you "
                 "sell (outside the USA, check your country's rules)")
SNAPSHOT_NAME = "gutenberg_catalog_en_2026-09-24.json.gz"

_ID_RE = re.compile(r"^gutenberg:([1-9][0-9]{0,6})$")
_START_RE = re.compile(r"^\*{3}\s*START OF (?:THE|THIS) PROJECT GUTENBERG E-?BOOK.*$",
                       re.I | re.M)
_END_RE = re.compile(r"^\*{3}\s*END OF (?:THE|THIS) PROJECT GUTENBERG E-?BOOK.*$",
                     re.I | re.M)
_COPYRIGHTED_RE = re.compile(r"copyrighted project gutenberg", re.I)
_KIDS_RE = re.compile(r"juvenile|children", re.I)

Getter = Callable[..., tuple[Optional[bytes], str]]


class GutenbergError(RuntimeError):
    """Plain words for why a lookup or a book did not work."""


# ---------------------------------------------------------------------------
# the saved list
# ---------------------------------------------------------------------------

def snapshot_path() -> str:
    from puffpai.catalogs import live
    return os.path.join(live.repo_root(), "config", "catalog_snapshots", SNAPSHOT_NAME)


_SNAP_LOCK = threading.Lock()
_SNAP: dict[str, Any] = {}


def load_snapshot(path: Optional[str] = None) -> dict[str, Any]:
    """``{taken_at, books: [{id,title,author,subjects}], index}``; empty if unreadable."""
    target = path or snapshot_path()
    with _SNAP_LOCK:
        if _SNAP.get("path") == target:
            return _SNAP["data"]
    empty = {"taken_at": None, "books": [], "index": {}}
    try:
        with gzip.open(target, "rt", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError, EOFError):
        return empty
    if not isinstance(raw, dict):
        return empty
    subjects = [str(s)[:80] for s in raw.get("subjects") or [] if isinstance(s, str)]
    books = []
    for row in raw.get("books") or []:
        if not (isinstance(row, list) and len(row) == 4 and isinstance(row[0], int)
                and 0 < row[0] < 10_000_000):
            continue
        subs = [subjects[i] for i in row[3] if isinstance(i, int) and 0 <= i < len(subjects)]
        books.append({"id": row[0], "title": str(row[1])[:120],
                      "author": str(row[2])[:60], "subjects": subs})
    index: dict[str, set[int]] = {}
    for pos, book in enumerate(books):
        text = book["title"] + " " + " ".join(book["subjects"])
        for word in set(goal_words.raw_words(text[:2000])):
            index.setdefault(word, set()).add(pos)
    data = {"taken_at": str(raw.get("taken_at") or "") or None, "books": books,
            "index": index}
    with _SNAP_LOCK:
        _SNAP.clear()
        _SNAP.update({"path": target, "data": data})
    return data


def build_snapshot(csv_text: str, taken_at: str) -> dict[str, Any]:
    """The saved list from Gutenberg's ``pg_catalog.csv`` (how the shipped file was made)."""
    import csv
    import io

    subjects: dict[str, int] = {}
    books = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        if row.get("Type") != "Text" or row.get("Language") != "en":
            continue
        locc = [x.strip() for x in str(row.get("LoCC") or "").split(";") if x.strip()]
        if locc and all(x.startswith("P") for x in locc):
            continue
        idx: list[int] = []
        for part in (str(row.get("Subjects") or "") + ";" +
                     str(row.get("Bookshelves") or "")).split(";"):
            name = re.sub(r"^\s*Category:\s*", "", part).strip()[:80]
            if not name:
                continue
            i = subjects.setdefault(name, len(subjects))
            if i not in idx:
                idx.append(i)
        books.append([int(row["Text#"]), " ".join(str(row.get("Title") or "").split())[:100],
                      str(row.get("Authors") or "").split(";")[0].strip()[:40], idx[:12]])
    return {"schema": "gutenberg_catalog/v1", "taken_at": taken_at,
            "source": "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv",
            "note": "English books, literature (LoCC class P) left out.",
            "subjects": list(subjects), "books": books}


def _entry(book: dict[str, Any]) -> dict[str, Any]:
    return {"title": book["title"], "name": "", "tags": book["subjects"],
            "category": "", "summary": ""}


def _enough(terms: list[str], matched: list[str]) -> bool:
    """A book must match two of the goal's words when the goal has two or more.

    The library is 60,000 books; one shared word ("civic" in "Social Civics")
    is not evidence that a book is about a 2014 Honda Civic.
    """
    need = 1 if len(terms) < 2 else 2
    hit = {t for t in terms for w in matched if goal_words.term_hits_word(t, w)}
    return len(hit) >= need


def search_snapshot(terms: list[str], *, limit: int = MAX_PROPOSED,
                    snapshot: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """Books on the saved list whose title or subjects match, best first."""
    from puffpai.catalogs import knowledge_resolver

    snap = snapshot if snapshot is not None else load_snapshot()
    index = snap.get("index") or {}
    books = snap.get("books") or []
    pool: set[int] = set()
    for term in terms:
        for word, where in index.items():
            if goal_words.term_hits_word(term, word):
                pool |= where
    scored = []
    for pos in pool:
        book = books[pos]
        score, matched = knowledge_resolver.score_entry(_entry(book), terms)
        if score and _enough(terms, matched):
            scored.append((score, pos, book, matched))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [dict(b, _score=s, _matched=m) for s, _p, b, m in scored[:limit]]


# ---------------------------------------------------------------------------
# the live search (Gutendex)
# ---------------------------------------------------------------------------

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _hosts(which: str) -> set[str]:
    """``{which}`` when the named outside service lists it; nothing otherwise."""
    try:
        from puffpai.security import egress_doors
        door = egress_doors.door("gutenberg_books")
    except Exception:  # noqa: BLE001 - no door table: nothing is allowed
        return set()
    return {which} if door is not None and which in door.hosts else set()

def _logged_fetch(source_fetch: Any) -> Getter:
    """source_fetch.get_small, logged and switched under this site's own row
    on "What leaves your computer" (gutenberg_books)."""
    def fetch(url: str, **kwargs: Any) -> tuple[Optional[bytes], str]:
        return source_fetch.get_small(url, service="gutenberg_books", **kwargs)
    return fetch



def search_url(term: str) -> str:
    return (f"https://{SEARCH_HOST}/books/?" +
            urllib.parse.urlencode({"topic": term, "languages": "en",
                                    "copyright": "false"}))


def parse_results(body: bytes) -> list[dict[str, Any]]:
    """Gutendex's answer, as saved-list rows. Untrusted data: shapes checked."""
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise GutenbergError("Gutendex's answer could not be read.") from exc
    out = []
    for row in (data.get("results") if isinstance(data, dict) else None) or []:
        if not isinstance(row, dict) or row.get("copyright") is not False:
            continue
        num = row.get("id")
        if not isinstance(num, int) or not 0 < num < 10_000_000:
            continue
        if "en" not in (row.get("languages") or []):
            continue
        authors = [a.get("name") for a in row.get("authors") or [] if isinstance(a, dict)]
        subs = [str(s)[:80] for s in (row.get("subjects") or []) + (row.get("bookshelves") or [])
                if isinstance(s, str)]
        subs = [re.sub(r"^Category:\s*", "", s) for s in subs]
        out.append({"id": num, "title": " ".join(str(row.get("title") or "").split())[:120],
                    "author": str(authors[0] if authors else "")[:60],
                    "subjects": subs[:12], "copyright": False})
    return out


def search_live(terms: list[str], *, getter: Optional[Getter] = None,
                limit: int = MAX_PROPOSED) -> list[dict[str, Any]]:
    """Ask Gutendex (at most :data:`LIVE_TERM_LIMIT` requests). Raises GutenbergError."""
    from puffpai import source_fetch
    from puffpai.catalogs import knowledge_resolver

    fetch = getter or _logged_fetch(source_fetch)
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for term in terms[:LIVE_TERM_LIMIT]:
        kept = _CACHE.get(term) if getter is None else None
        if kept and time.time() - kept[0] < CACHE_SECONDS:
            found = kept[1]
        else:
            # A person's search, asked through the site's public API. Its
            # robots.txt asks crawlers to stay out of /books/; this is not a crawl.
            body, reason = fetch(search_url(term), allowed_hosts=_hosts(SEARCH_HOST),
                                 max_bytes=MAX_SEARCH_BYTES, honour_robots=False)
            if body is None:
                raise GutenbergError(f"Gutendex did not answer: {reason}")
            found = parse_results(body)
            if getter is None:
                _CACHE[term] = (time.time(), found)
        for row in found:
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)
    scored = []
    for pos, row in enumerate(rows):
        score, matched = knowledge_resolver.score_entry(_entry(row), terms)
        if score and _enough(terms, matched):
            scored.append((score, pos, row, matched))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [dict(r, _score=s, _matched=m) for s, _p, r, m in scored[:limit]]


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------

def book_id(num: int) -> str:
    return f"{ID_PREFIX}:{int(num)}"


def split_id(ident: str) -> Optional[int]:
    match = _ID_RE.match(str(ident or "").strip())
    return int(match.group(1)) if match else None


def text_url(num: int) -> str:
    """Gutenberg's own plain-text UTF-8 file for a book number. Built by code."""
    return f"https://{DOWNLOAD_HOST}/cache/epub/{int(num)}/pg{int(num)}.txt"


def candidate(row: dict[str, Any], *, match_terms: Iterable[str] = (),
              verified_at: Optional[str] = None) -> dict[str, Any]:
    from puffpai.knowledge import text_books

    num = int(row["id"])
    author = str(row.get("author") or "")
    subjects = [s for s in row.get("subjects") or []][:4]
    by = f" by {author}" if author else ""
    what = f"A free old book{by} from Project Gutenberg."
    if subjects:
        what += " About: " + "; ".join(subjects)
    kids = any(_KIDS_RE.search(s) for s in row.get("subjects") or [])
    return {
        "id": book_id(num),
        "kind": "knowledge",
        "tier": "gutenberg",
        "name": str(row.get("title") or f"Project Gutenberg book {num}")[:80],
        "what_it_is": what[:240],
        "source_catalog": "Project Gutenberg",
        "url": f"https://{DOWNLOAD_HOST}/ebooks/{num}",
        "host": DOWNLOAD_HOST,
        "sample_endpoint": None,
        "auth": "none",
        "license": LICENSE,
        "commercial_ok": True,
        "size_bytes": None,
        "size_ceiling": text_books.MAX_BOOK_BYTES,
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
        "audience": "kids" if kids else "general",
        "category": "gutenberg",
        "updated": "",
        "sends": [],
        "number": num,
        # What the library files the book under: the offer checks it
        # (goal_words.match_strength), never shown as it is.
        "subjects": [str(s)[:80] for s in row.get("subjects") or []][:12],
    }


def find(terms: list[str], *, live: bool = False, getter: Optional[Getter] = None,
         limit: int = MAX_PROPOSED) -> dict[str, Any]:
    """``{candidates, searched, note}``. Never raises.

    The catalog saved on this computer is the knowledge path: it is
    used whenever it is there, even when *live* is allowed, because the live
    Gutendex search failed often. Gutendex is asked only when there is no
    saved list at all.
    """
    snap_date = None
    note = ""
    rows: list[dict[str, Any]] = []
    searched = "snapshot"
    snap = load_snapshot()
    if live and not snap.get("books"):
        try:
            rows = search_live(terms, getter=getter, limit=limit)
            searched = "live"
        except GutenbergError as exc:
            note = f"Project Gutenberg's search could not be reached ({exc}), so " \
                   f"the saved list was used."
    if searched == "snapshot":
        snap_date = snap.get("taken_at")
        rows = search_snapshot(terms, snapshot=snap, limit=limit)
    today = _today() if searched == "live" else snap_date
    cands = []
    for row in rows:
        cand = candidate(row, match_terms=row.get("_matched") or [], verified_at=today)
        cand["_score"] = int(row.get("_score") or 0)
        cand["reason"] = "Matched on: " + ", ".join(row.get("_matched") or []) + "."
        cands.append(cand)
    return {"candidates": cands, "searched": searched, "note": note,
            "snapshot_date": snap_date}


def candidate_by_id(ident: str, *, live: bool = False,
                    getter: Optional[Getter] = None) -> Optional[dict[str, Any]]:
    """A book by its id, from the saved list (or Gutendex when allowed)."""
    num = split_id(ident)
    if num is None:
        return None
    snap = load_snapshot()
    for book in snap.get("books") or []:
        if book["id"] == num:
            return candidate(book, verified_at=snap.get("taken_at"))
    if not live:
        return None
    from puffpai import source_fetch
    fetch = getter or _logged_fetch(source_fetch)
    url = f"https://{SEARCH_HOST}/books/?" + urllib.parse.urlencode({"ids": str(num)})
    body, _reason = fetch(url, allowed_hosts=_hosts(SEARCH_HOST),
                          max_bytes=MAX_SEARCH_BYTES, honour_robots=False)
    if body is None:
        return None
    try:
        rows = [r for r in parse_results(body) if r["id"] == num]
    except GutenbergError:
        return None
    return candidate(rows[0], verified_at=_today()) if rows else None


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# the book itself
# ---------------------------------------------------------------------------

def strip_boilerplate(text: str) -> str:
    """The book between Gutenberg's START and END lines. Raises if it is not there
    or if the header says the book is still under copyright."""
    start = _START_RE.search(text)
    if not start:
        raise GutenbergError("Could not find where the book starts in Gutenberg's "
                             "file, so it was not saved.")
    header = text[:start.start()]
    if _COPYRIGHTED_RE.search(header):
        raise GutenbergError("That book is still under copyright, so BuildBot does "
                             "not fetch it.")
    end = _END_RE.search(text, start.end())
    body = text[start.end():end.start() if end else len(text)]
    return body.strip()


def fetch_book(num: int, *, getter: Optional[Getter] = None,
               max_bytes: Optional[int] = None) -> str:
    """The book's text, Gutenberg's header and footer removed."""
    from puffpai import source_fetch
    from puffpai.knowledge import text_books

    fetch = getter or _logged_fetch(source_fetch)
    limit = max_bytes if max_bytes is not None else text_books.MAX_BOOK_BYTES
    body, reason = fetch(text_url(num), allowed_hosts=_hosts(DOWNLOAD_HOST),
                         max_bytes=limit, honour_robots=True)
    if body is None:
        raise GutenbergError(f"Project Gutenberg did not send the book: {reason}")
    return strip_boilerplate(body.decode("utf-8-sig", errors="replace"))


def book_meta(cand: dict[str, Any]) -> dict[str, Any]:
    num = int(cand.get("number") or split_id(cand["id"]) or 0)
    return {
        "id": cand["id"], "kind": "gutenberg", "catalog": "Project Gutenberg",
        "title": str(cand.get("name") or f"Project Gutenberg book {num}"),
        "language": "en", "license": LICENSE, "license_url": LICENSE_URL,
        "license_plain": LICENSE_PLAIN,
        "credit": (f"From Project Gutenberg, ebook #{num} "
                   f"(https://{DOWNLOAD_HOST}/ebooks/{num}). Gutenberg's header and "
                   f"licence text were removed; the book itself is public domain "
                   f"in the USA."),
        "commercial_use": True, "personal_use_only": False,
        "source_url": f"https://{DOWNLOAD_HOST}/ebooks/{num}",
        "note": "Project Gutenberg's own plain-text UTF-8 file.",
    }


def pages(cand: dict[str, Any], *, getter: Optional[Getter] = None) -> list[dict[str, Any]]:
    num = int(cand.get("number") or split_id(cand["id"]) or 0)
    text = fetch_book(num, getter=getter)
    return [{"title": str(cand.get("name") or f"Book {num}"),
             "url": f"https://{DOWNLOAD_HOST}/ebooks/{num}", "revision": "",
             "text": text}]


__all__ = ["CATALOG_ID", "MAX_PROPOSED", "LICENSE", "GutenbergError", "load_snapshot",
           "build_snapshot", "search_snapshot", "search_live", "parse_results",
           "book_id", "split_id", "text_url", "candidate", "find", "candidate_by_id",
           "strip_boilerplate", "fetch_book", "book_meta", "pages"]
