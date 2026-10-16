"""The Internet Archive: old public-domain books scanned by libraries.

Asked only online, when someone searches (there is no saved list):

* **search**: the Archive's own search (``archive.org/advancedsearch.php``),
  texts only, and only books its scanning centres marked "not in copyright"
  that were published long enough ago to be public domain in the USA
  (:func:`public_domain_year`). A licence an uploader typed on their own is
  never trusted: anyone can mark anything "public domain" there. A book is
  listed only when its title or subjects hold the words (evidence or
  nothing), best match first, then the most downloaded.
* **the book**: its plain text (the Archive's own text made from the scan),
  read once from ``archive.org/stream/<id>/<id>_djvu.txt`` into a book folder
  (:mod:`puffpai.knowledge.text_books`). The address is built by code from
  the book's id.

Ids are ``archive:<the Archive's id>``.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import datetime
import html
import json
import re
import urllib.parse
from typing import Any, Callable, Iterable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

CATALOG_ID = "internet_archive"
ID_PREFIX = "archive"
SERVICE = "archive_books"
HOST = "archive.org"
MAX_PROPOSED = 3
SEARCH_ROWS = 40
MAX_SEARCH_BYTES = 2 * 1024 * 1024
LICENSE = "LicenseRef-Public-Domain-US"
LICENSE_URL = "https://archive.org/about/terms.php"
#: US rule: a work published 95 years ago or more is public domain from the
#: 1st of January of the 96th year (2026: published in 1930 or before).
PD_YEARS = 96

_ID_RE = re.compile(r"^archive:([A-Za-z0-9][A-Za-z0-9._-]{0,99})$")
_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.S | re.I)
_KIDS_RE = re.compile(r"juvenile|children", re.I)

Getter = Callable[..., tuple[Optional[bytes], str]]


class ArchiveError(RuntimeError):
    """Plain words for why the Internet Archive could not be read."""


def public_domain_year(today: Optional[datetime.date] = None) -> int:
    """The last publication year that is public domain in the USA today."""
    return (today or datetime.date.today()).year - PD_YEARS


def license_plain(year: Any = None) -> str:
    when = f" (published in {year})" if isinstance(year, int) else ""
    return (f"public domain in the USA{when}, free to use, even in something you "
            f"sell (outside the USA, check your country's rules)")


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


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _filters() -> str:
    return (f"mediatype:texts AND possible-copyright-status:NOT_IN_COPYRIGHT AND "
            f"year:[1500 TO {public_domain_year()}] AND language:(eng OR English)")


def search_url(terms: list[str]) -> str:
    words = " OR ".join(terms)
    query = f"(title:({words}) OR subject:({words})) AND {_filters()}"
    return search_url_for(query)


def search_url_for(query: str) -> str:
    params = [("q", query)] + [("fl[]", f) for f in (
        "identifier", "title", "creator", "year", "subject", "downloads")] + [
        ("sort[]", "downloads desc"), ("rows", str(SEARCH_ROWS)), ("output", "json")]
    return f"https://{HOST}/advancedsearch.php?" + urllib.parse.urlencode(params)


def _first(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return " ".join(str(value or "").split())


def parse_results(body: bytes) -> list[dict[str, Any]]:
    """The search's answer as rows. Untrusted data: shapes checked."""
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise ArchiveError("The Internet Archive's answer could not be read.") from exc
    docs = ((data.get("response") or {}).get("docs") if isinstance(data, dict) else None)
    if not isinstance(docs, list):
        raise ArchiveError("The Internet Archive's search did not answer.")
    out = []
    last = public_domain_year()
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        ident = str(doc.get("identifier") or "")
        if not _ID_RE.match(f"{ID_PREFIX}:{ident}"):
            continue
        try:
            year = int(str(_first(doc.get("year")))[:4])
        except ValueError:
            year = None
        if year is None or year > last:
            continue
        subjects = doc.get("subject")
        subjects = subjects if isinstance(subjects, list) else [subjects] if subjects else []
        downloads = doc.get("downloads")
        out.append({"identifier": ident, "title": _first(doc.get("title"))[:160],
                    "creator": _first(doc.get("creator"))[:60], "year": year,
                    "subjects": [" ".join(str(s).split())[:80] for s in subjects][:8],
                    "downloads": downloads if isinstance(downloads, int) else 0})
    return out


def _entry(row: dict[str, Any]) -> dict[str, Any]:
    return {"title": row["title"], "name": "", "tags": row["subjects"],
            "category": "", "summary": ""}


def search_live(terms: list[str], *, getter: Optional[Getter] = None,
                limit: int = MAX_PROPOSED) -> list[dict[str, Any]]:
    """Books matching the words, best match then most downloaded. Raises ArchiveError."""
    from puffpai.catalogs import knowledge_resolver as kr

    words = [t for t in terms if re.match(r"^[a-z0-9]{2,30}$", str(t))][:4]
    if not words:
        return []
    body, reason = _fetch(getter)(search_url(words), allowed_hosts=_hosts(),
                                  max_bytes=MAX_SEARCH_BYTES, honour_robots=False)
    if body is None:
        raise ArchiveError(f"The Internet Archive did not answer: {reason}")
    scored = []
    for pos, row in enumerate(parse_results(body)):
        score, matched = kr.score_entry(_entry(row), words)
        if score and kr.enough_words(words, matched):
            scored.append((score, row["downloads"], pos, row, matched))
    scored.sort(key=lambda s: (-s[0], -s[1], s[2]))
    seen: set[str] = set()
    out = []
    for score, _d, _p, row, matched in scored:
        key = row["title"].lower()[:60]
        if key in seen:     # the same book scanned by two libraries: once
            continue
        seen.add(key)
        out.append(dict(row, _score=score, _matched=matched))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------

def details_url(ident: str) -> str:
    return f"https://{HOST}/details/{ident}"


def text_url(ident: str) -> str:
    """The Archive's own text of the scan, served by archive.org. Built by code."""
    return f"https://{HOST}/stream/{ident}/{ident}_djvu.txt"


def candidate(row: dict[str, Any], *, match_terms: Iterable[str] = (),
              verified_at: Optional[str] = None) -> dict[str, Any]:
    from puffpai.knowledge import text_books

    ident = str(row["identifier"])
    year = row.get("year") if isinstance(row.get("year"), int) else None
    by = f" by {row['creator']}" if row.get("creator") else ""
    when = f" ({year})" if year else ""
    what = f"A free old book{by}{when}, scanned by a library for the Internet Archive."
    if row.get("subjects"):
        what += " About: " + "; ".join(row["subjects"][:4])
    return {
        "id": f"{ID_PREFIX}:{ident}",
        "kind": "knowledge",
        "tier": "archive",
        "name": str(row.get("title") or ident)[:80],
        "what_it_is": what[:240],
        "source_catalog": "the Internet Archive",
        "url": details_url(ident),
        "host": HOST,
        "sample_endpoint": None,
        "auth": "none",
        "license": LICENSE,
        "commercial_ok": True,
        "personal_use_only": False,
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
        "license_plain": license_plain(year),
        "fetchable": True,
        "why_not": "",
        "audience": "kids" if any(_KIDS_RE.search(s) for s in row.get("subjects") or [])
        else "general",
        "category": "archive",
        "updated": "",
        "sends": [],
        "identifier": ident,
        "year": year,
        "downloads": int(row.get("downloads") or 0),
        "subjects": list(row.get("subjects") or [])[:8],
    }


def find(terms: list[str], *, live: bool = False, getter: Optional[Getter] = None,
         limit: int = MAX_PROPOSED) -> dict[str, Any]:
    """``{candidates, searched, note}``. Never raises. Offline: nothing (no saved list)."""
    if not live:
        return {"candidates": [], "searched": "not searched", "note": ""}
    try:
        rows = search_live(terms, getter=getter, limit=limit)
    except ArchiveError as exc:
        return {"candidates": [], "searched": "not searched", "note": str(exc)}
    today = datetime.date.today().isoformat()
    cands = []
    for row in rows:
        cand = candidate(row, match_terms=row.get("_matched") or [], verified_at=today)
        cand["_score"] = int(row.get("_score") or 0)
        cand["reason"] = "Matched on: " + ", ".join(row.get("_matched") or []) + "."
        cands.append(cand)
    return {"candidates": cands, "searched": "live", "note": ""}


def split_id(ident: Any) -> Optional[str]:
    match = _ID_RE.match(str(ident or "").strip())
    return match.group(1) if match else None


def candidate_by_id(ident: str, *, live: bool = False,
                    getter: Optional[Getter] = None) -> Optional[dict[str, Any]]:
    """A book by its id, asked again with the same public-domain rule (online only)."""
    key = split_id(ident)
    if key is None or not live:
        return None
    url = search_url_for(f'identifier:"{key}" AND {_filters()}')
    body, _reason = _fetch(getter)(url, allowed_hosts=_hosts(),
                                   max_bytes=MAX_SEARCH_BYTES, honour_robots=False)
    if body is None:
        return None
    try:
        rows = [r for r in parse_results(body) if r["identifier"] == key]
    except ArchiveError:
        return None
    return candidate(rows[0], verified_at=datetime.date.today().isoformat()) if rows else None


# ---------------------------------------------------------------------------
# the book itself
# ---------------------------------------------------------------------------

def stream_text(body: bytes) -> str:
    """The text inside the stream page's ``<pre>``, or ""."""
    found = _PRE_RE.search(body.decode("utf-8", errors="replace"))
    return html.unescape(found.group(1)).strip() if found else ""


def book_meta(cand: dict[str, Any]) -> dict[str, Any]:
    ident = str(cand.get("identifier") or split_id(cand["id"]))
    year = cand.get("year")
    return {
        "id": cand["id"], "kind": "archive", "catalog": "the Internet Archive",
        "title": str(cand.get("name") or ident),
        "language": "en", "license": LICENSE, "license_url": LICENSE_URL,
        "license_plain": license_plain(year if isinstance(year, int) else None),
        "credit": (f"From the Internet Archive ({details_url(ident)}): a library's "
                   f"scan of a book published"
                   f"{f' in {year}' if isinstance(year, int) else ' long ago'}, public "
                   f"domain in the USA. The text was made from the scan, so it has "
                   f"some misread words."),
        "commercial_use": True, "personal_use_only": False,
        "source_url": details_url(ident),
        "note": "The Internet Archive's own text of the scan.",
    }


def pages(cand: dict[str, Any], *, getter: Optional[Getter] = None) -> list[dict[str, Any]]:
    from puffpai.knowledge import text_books

    ident = str(cand.get("identifier") or split_id(cand["id"]) or "")
    if not _ID_RE.match(f"{ID_PREFIX}:{ident}"):
        raise ArchiveError("That is not a book BuildBot found on the Internet Archive.")
    body, reason = _fetch(getter)(text_url(ident), allowed_hosts=_hosts(),
                                  max_bytes=2 * text_books.MAX_BOOK_BYTES,
                                  honour_robots=True)
    if body is None:
        raise ArchiveError(f"The Internet Archive did not send the book: {reason}")
    text = stream_text(body)
    if not text:
        raise ArchiveError("The Internet Archive has no text for that book.")
    title = str(cand.get("name") or ident)
    parts = _parts(text)
    return [{"title": title if len(parts) == 1 else f"{title} (part {n})",
             "url": details_url(ident), "revision": "", "text": part}
            for n, part in enumerate(parts, 1)]


#: A long book is kept as parts no bigger than this (a book page holds 2 MB).
PART_CHARS = 1_000_000


def _parts(text: str) -> list[str]:
    out = []
    while len(text) > PART_CHARS:
        cut = text.rfind("\n", 0, PART_CHARS)
        cut = cut if cut > PART_CHARS // 2 else PART_CHARS
        out.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return out + ([text] if text else [])


__all__ = ["CATALOG_ID", "ID_PREFIX", "SERVICE", "HOST", "LICENSE", "ArchiveError",
           "public_domain_year", "license_plain", "search_url", "parse_results",
           "search_live", "candidate", "find", "split_id", "candidate_by_id",
           "stream_text", "text_url", "book_meta", "pages"]
