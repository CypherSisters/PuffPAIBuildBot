"""Reader for the Kiwix library: offline Wikipedia, Q&A sites and more.

The Kiwix library publishes its whole shelf as an OPDS (Atom) feed at
``opds.library.kiwix.org/catalog/v2/entries``. Every book in it carries a name
(``vikidia_en_all``), a flavour (``nopic``, ``maxi``), a size in bytes and a
link to a ``.meta4`` file. The ``.meta4`` file names the dated file, its exact
size, its SHA-256 and the mirrors that serve it. That is everything this build
needs to download a book safely: which host to start at, which mirrors a
redirect may go to, and the digest the bytes must match when they land.

Rules this module keeps:

* **What the library sends is data.** Feed and ``.meta4`` bodies are size
  capped, refused if they declare a DOCTYPE or an ENTITY (so no entity
  expansion and no external fetches while parsing), and parsed with
  ``defusedxml`` when it is installed. A title or summary is shown to the owner
  as text and never placed in a prompt as instruction.
* **The feed carries no license.** Licenses come from the dated rule table in
  ``config/catalog_index.json``, keyed on the book's category or author. A book
  that matches no rule is shown with "license not known" and is never
  downloadable here (evidence or silence).
* **Nothing here downloads a book.** :mod:`puffpai.source_fetch` moves the
  bytes. This module only turns catalog answers into candidates and download
  plans.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import copy
import re
import threading
import time
import urllib.parse
from collections.abc import Mapping
from typing import Any, Callable, Iterable, Optional

SEARCH_URL = "https://opds.library.kiwix.org/catalog/v2/entries"
SEARCH_HOST = "opds.library.kiwix.org"
#: Where the ``.meta4`` files live and where a download starts.
DOWNLOAD_HOST = "lb.download.kiwix.org"
DEFAULT_LANG = "eng"
CATALOG_ID = "kiwix"
SOURCE_CATALOG = "Kiwix library"

MAX_FEED_BYTES = 4 * 1024 * 1024
#: A .meta4 lists one hash per 4 MB piece, about 15 bytes per MB of book:
#: 1.8 MB for the 127 GB Wikipedia. 4 MB covers books up to about 280 GB.
MAX_META4_BYTES = 4 * 1024 * 1024
MAX_COUNT = 100
MAX_SUMMARY_CHARS = 200
MAX_TITLE_CHARS = 80

_ATOM = "{http://www.w3.org/2005/Atom}"
_META4 = "{urn:ietf:params:xml:ns:metalink}"

#: A book name as the library spells it: letters, digits, dot, dash, underscore.
BOOK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,150}$")
FLAVOUR_RE = re.compile(r"^[A-Za-z0-9_-]{0,24}$")
#: The only shape a downloaded file may be named.
ZIM_FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.zim$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LANG_RE = re.compile(r"^[a-z]{3}(,[a-z]{3})*$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]")

ID_PREFIX = "kiwix:"

#: What a saved download-description path may look like: zim/<folder>/<file>.zim.meta4
_META4_PATH_RE = re.compile(r"^zim/[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*\.zim\.meta4$")


class CatalogError(Exception):
    """The library could not be read, or its answer was not usable. Plain words."""


class LiveLookupOff(CatalogError):
    """Live lookups of the library are switched off in the catalog index."""


# ---------------------------------------------------------------------------
# untrusted XML
# ---------------------------------------------------------------------------

def safe_parse(data: bytes, *, max_bytes: int) -> Any:
    """Parse untrusted XML, or raise :class:`CatalogError`.

    A DOCTYPE or ENTITY declaration is refused outright: OPDS and metalink
    documents never need one, and refusing them removes entity expansion and
    external entities from the table whatever parser is underneath.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise CatalogError("The library's answer was not a document.")
    if len(data) > max_bytes:
        raise CatalogError("The library's answer was too large to read safely.")
    head = bytes(data).lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise CatalogError("The library's answer declared entities, so it was not read.")
    try:
        from defusedxml import ElementTree as parser  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - depends on the machine
        import xml.etree.ElementTree as parser  # noqa: N813
    try:
        return parser.fromstring(bytes(data))
    except Exception as exc:  # noqa: BLE001 - any parse failure is "unreadable"
        raise CatalogError(f"The library's answer could not be read ({type(exc).__name__}).")


def _text(value: Any, cap: int) -> str:
    return " ".join(_CONTROL_RE.sub(" ", str(value or "")).split())[:cap]


def _int(value: Any) -> Optional[int]:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _meta4_url_ok(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return (parts.scheme == "https" and (parts.hostname or "").lower() == DOWNLOAD_HOST
            and parts.port is None and not parts.username and not parts.query
            and parts.path.endswith(".zim.meta4"))


# ---------------------------------------------------------------------------
# the feed
# ---------------------------------------------------------------------------

def parse_feed(data: bytes) -> list[dict[str, Any]]:
    """Book records from one OPDS page. Malformed entries are dropped one by one."""
    root = safe_parse(data, max_bytes=MAX_FEED_BYTES)
    out: list[dict[str, Any]] = []
    for node in root.findall(_ATOM + "entry"):
        entry = _entry(node)
        if entry is not None:
            out.append(entry)
    return out


def _entry(node: Any) -> Optional[dict[str, Any]]:
    def text(tag: str, cap: int = 200) -> str:
        return _text(node.findtext(_ATOM + tag), cap)

    name = text("name", 160)
    if not BOOK_NAME_RE.match(name):
        return None
    flavour = text("flavour", 30)
    if not FLAVOUR_RE.match(flavour):
        flavour = ""
    meta4_url, size = "", None
    for link in node.findall(_ATOM + "link"):
        rel = str(link.get("rel") or "")
        if "acquisition" in rel and _meta4_url_ok(str(link.get("href") or "")):
            meta4_url = str(link.get("href"))
            size = _int(link.get("length"))
            break
    author = node.find(_ATOM + "author")
    publisher = node.find(_ATOM + "publisher")
    tags = [t for t in text("tags", 400).split(";") if t and not t.startswith("_")]
    language = text("language", 40).lower()
    return {
        "name": name,
        "flavour": flavour,
        "title": text("title", MAX_TITLE_CHARS),
        "summary": text("summary", MAX_SUMMARY_CHARS),
        "language": language if _LANG_RE.match(language) else "",
        "category": text("category", 40),
        "tags": tags[:12],
        "article_count": _int(node.findtext(_ATOM + "articleCount")),
        "size_bytes": size,
        "meta4_url": meta4_url,
        "updated": text("updated", 30),
        "author": _text(author.findtext(_ATOM + "name") if author is not None else "", 60),
        "publisher": _text(publisher.findtext(_ATOM + "name") if publisher is not None else "", 60),
    }


# ---------------------------------------------------------------------------
# the .meta4 file
# ---------------------------------------------------------------------------

def parse_meta4(data: bytes) -> dict[str, Any]:
    """``{filename, size, sha256, urls}`` from a metalink, or raise.

    ``urls`` are the mirrors in the library's own priority order, https only.
    A metalink with no SHA-256 is refused: the digest is what makes following a
    mirror safe, so a download without one is not offered.
    """
    root = safe_parse(data, max_bytes=MAX_META4_BYTES)
    node = root.find(_META4 + "file")
    if node is None:
        raise CatalogError("The download description names no file.")
    filename = _text(node.get("name"), 220)
    if not ZIM_FILENAME_RE.match(filename):
        raise CatalogError("The download description names a file this build will not write.")
    size = _int(node.findtext(_META4 + "size"))
    if not size:
        raise CatalogError("The download description gives no size.")
    sha256 = ""
    for hash_node in node.findall(_META4 + "hash"):
        if str(hash_node.get("type") or "").lower() == "sha-256":
            sha256 = _text(hash_node.text, 70).lower()
    if not _SHA256_RE.match(sha256):
        raise CatalogError("The library published no SHA-256 for this file, so it is not downloaded.")
    ranked: list[tuple[int, str]] = []
    for position, url_node in enumerate(node.findall(_META4 + "url")):
        url = _text(url_node.text, 400)
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            continue
        if parts.scheme != "https" or not parts.hostname or parts.username \
                or parts.port not in (None, 443):
            continue
        if not parts.path.endswith("/" + filename):
            continue
        priority = _int(url_node.get("priority"))
        ranked.append((priority if priority is not None else 1000 + position, url))
    ranked.sort(key=lambda pair: pair[0])
    return {"filename": filename, "size": size, "sha256": sha256,
            "urls": [url for _p, url in ranked][:8]}


def primary_download_url(meta4_url: str) -> str:
    """The library's own address for the file: the ``.meta4`` address minus ``.meta4``."""
    return meta4_url[: -len(".meta4")] if meta4_url.endswith(".zim.meta4") else ""


def mirror_hosts(plan: dict[str, Any]) -> list[str]:
    hosts: list[str] = []
    for url in plan.get("urls") or []:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if host and host not in hosts:
            hosts.append(host)
    return hosts


# ---------------------------------------------------------------------------
# ids, licenses, audience
# ---------------------------------------------------------------------------

def book_id(name: str, flavour: str = "") -> str:
    """``kiwix:<name>`` or ``kiwix:<name>:<flavour>``. Stable across dated reissues."""
    return ID_PREFIX + name + (f":{flavour}" if flavour else "")


def split_book_id(ident: Any) -> Optional[tuple[str, str]]:
    """``(name, flavour)`` for a well-formed book id, else ``None``."""
    text = str(ident or "").strip()
    if not text.startswith(ID_PREFIX):
        return None
    parts = text[len(ID_PREFIX):].split(":")
    if len(parts) > 2:
        return None
    name = parts[0]
    flavour = parts[1] if len(parts) == 2 else ""
    if not BOOK_NAME_RE.match(name) or not FLAVOUR_RE.match(flavour) \
            or (len(parts) == 2 and not flavour):
        return None
    return name, flavour


def license_for(entry: dict[str, Any], rules: Iterable[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The license rule that covers this book, or ``None`` (not known)."""
    category = str(entry.get("category") or "").lower()
    author = str(entry.get("author") or "").lower()
    for rule in rules:
        rule_category = str(rule.get("category") or "").lower()
        authors = [str(a).lower() for a in (rule.get("authors") or [])]
        if rule_category and rule_category == category:
            return dict(rule)
        if not rule_category and author and author in authors:
            return dict(rule)
    return None


def audience_of(entry: dict[str, Any], audience: Optional[Mapping[str, Any]] = None,
                kids_categories: Iterable[str] = ()) -> str:
    """``kids`` / ``general`` / ``technical``. Used for ORDER only, never to hide.

    The rules are data (the ``audience`` block of the Kiwix row in
    ``config/catalog_index.json``): library categories, words in a book's
    name, title or summary, and book-name prefixes. With no rules every book
    is ``general``.
    """
    rules = audience if isinstance(audience, Mapping) else {}

    def _words(key: str) -> set[str]:
        values = rules.get(key) or []
        if not isinstance(values, (list, tuple)):
            return set()
        return {str(v).lower() for v in values if str(v).strip()}

    category = str(entry.get("category") or "").lower()
    name = str(entry.get("name") or "").lower()
    text = " ".join(str(entry.get(k) or "") for k in ("name", "title", "summary")).lower()
    kids = _words("kids_categories") | {str(c).lower() for c in kids_categories}
    if category in kids or any(w in text for w in _words("kids_words")):
        return "kids"
    if category in _words("technical_categories")             or any(name.startswith(p) for p in _words("technical_name_prefixes")):
        return "technical"
    return "general"


def commercial_words(commercial_ok: Optional[bool]) -> str:
    if commercial_ok is True:
        return "free to use, even in something you sell"
    if commercial_ok is False:
        return "personal use only"
    return "check the terms before using it in anything you sell"


def plain_license(license_id: str, commercial_ok: Optional[bool]) -> str:
    if not license_id:
        return "license not known, so it cannot be downloaded here"
    return f"{license_id}, {commercial_words(commercial_ok)}"


def to_candidate(entry: dict[str, Any], rules: Iterable[dict[str, Any]], *,
                 match_terms: Iterable[str] = (), verified_at: Optional[str] = None,
                 kids_categories: Iterable[str] = (),
                 audience: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """One book as the shared Candidate shape, plus the private fields fetch needs.

    Shared fields keep their exact names. Extra fields (``fetchable``,
    ``why_not``, ``audience``, ``book``, ``flavour``, ``meta4_url``,
    ``license_url``) are this catalog's and other catalogs may ignore them.
    """
    rule = license_for(entry, rules)
    name, flavour = str(entry["name"]), str(entry.get("flavour") or "")
    title = str(entry.get("title") or name)
    flavour_word = {"nopic": "text only", "mini": "short version",
                    "maxi": "with pictures"}.get(flavour, "")
    label = title + (f" ({flavour_word})" if flavour_word else "")
    summary = str(entry.get("summary") or "")
    articles = entry.get("article_count")
    what = (summary or title).rstrip()
    if what and what[-1] not in ".!?":
        what += "."
    if isinstance(articles, int) and articles > 0:
        what = f"{what} About {articles:,} pages, read offline."
    license_id = str((rule or {}).get("license") or "")
    commercial = (rule or {}).get("commercial_use") if rule else None
    fetchable = bool(rule) and bool(entry.get("meta4_url")) and bool(entry.get("size_bytes"))
    why_not = ""
    if not rule:
        why_not = "Its license is not known, so it cannot be downloaded here."
    elif not entry.get("meta4_url") or not entry.get("size_bytes"):
        why_not = "The library does not say where to download it or how big it is."
    return {
        "id": book_id(name, flavour),
        "kind": "knowledge",
        "name": label[:MAX_TITLE_CHARS],
        "what_it_is": _text(what, 240),
        "source_catalog": SOURCE_CATALOG,
        "url": "https://library.kiwix.org/",
        "host": DOWNLOAD_HOST,
        "sample_endpoint": None,
        "auth": "none",
        "license": license_id or "unknown",
        "commercial_ok": commercial if isinstance(commercial, bool) else None,
        "size_bytes": entry.get("size_bytes"),
        "local_or_cloud": "local",
        "needs": [],
        "match_terms": [str(t) for t in match_terms],
        "verified_at": verified_at,
        # fields of this catalog only
        "book": name,
        "flavour": flavour,
        "meta4_url": str(entry.get("meta4_url") or ""),
        "license_url": str((rule or {}).get("license_url") or ""),
        "license_plain": plain_license(license_id, commercial if rule else None),
        "fetchable": fetchable,
        "why_not": why_not,
        "audience": audience_of(entry, audience, kids_categories),
        "category": str(entry.get("category") or ""),
        "updated": str(entry.get("updated") or ""),
    }


# ---------------------------------------------------------------------------
# the dated snapshot - what an offline build searches
# ---------------------------------------------------------------------------

def default_snapshot_path() -> Optional[str]:
    from puffpai import catalogs
    from puffpai.config import get_config

    row = catalogs.catalog_row(CATALOG_ID) or {}
    rel = str(row.get("snapshot") or "")
    if not rel:
        return None
    return os.path.join(get_config().REPO_ROOT, *rel.split("/"))


def load_snapshot(path: Optional[str] = None) -> dict[str, Any]:
    """The shipped snapshot, or an empty one (never raises).

    Entries are re-validated on load exactly as live ones are: a snapshot file
    is data too, and a hand-edited row with a foreign download address simply
    does not survive.
    """
    target = path or default_snapshot_path()
    empty = {"taken_at": None, "lang": DEFAULT_LANG, "entries": []}
    if not target:
        return empty
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return empty
    entries = []
    for raw in data["entries"]:
        entry = _snapshot_entry(raw)
        if entry is not None:
            entries.append(entry)
    return {"taken_at": str(data.get("taken_at") or "") or None,
            "lang": str(data.get("lang") or DEFAULT_LANG), "entries": entries}


def _snapshot_entry(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "")
    flavour = str(raw.get("flavour") or "")
    if not BOOK_NAME_RE.match(name) or not FLAVOUR_RE.match(flavour):
        return None
    meta4_path = str(raw.get("meta4_path") or "")
    if not _META4_PATH_RE.match(meta4_path) or ".." in meta4_path:
        meta4_path = ""
    meta4_url = f"https://{DOWNLOAD_HOST}/{meta4_path}" if meta4_path else ""
    if meta4_url and not _meta4_url_ok(meta4_url):
        meta4_url = ""
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    return {
        "name": name, "flavour": flavour,
        "title": _text(raw.get("title"), MAX_TITLE_CHARS),
        "summary": _text(raw.get("summary"), MAX_SUMMARY_CHARS),
        "language": _text(raw.get("language"), 40).lower(),
        "category": _text(raw.get("category"), 40),
        "tags": [_text(t, 40) for t in tags][:12],
        "article_count": _int(raw.get("article_count")),
        "size_bytes": _int(raw.get("size_bytes")),
        "meta4_url": meta4_url,
        "updated": _text(raw.get("updated"), 30),
        "author": _text(raw.get("author"), 60),
        "publisher": _text(raw.get("publisher"), 60),
    }


def snapshot_row(entry: dict[str, Any]) -> dict[str, Any]:
    """One entry in the compact form the snapshot file stores."""
    meta4 = str(entry.get("meta4_url") or "")
    prefix = f"https://{DOWNLOAD_HOST}/"
    return {
        "name": entry["name"], "flavour": entry.get("flavour") or "",
        "title": entry.get("title") or "", "summary": entry.get("summary") or "",
        "language": entry.get("language") or "", "category": entry.get("category") or "",
        "tags": list(entry.get("tags") or []),
        "article_count": entry.get("article_count"),
        "size_bytes": entry.get("size_bytes"),
        "meta4_path": meta4[len(prefix):] if meta4.startswith(prefix) else "",
        "updated": entry.get("updated") or "", "author": entry.get("author") or "",
    }


# ---------------------------------------------------------------------------
# live lookups - one request per owner action
# ---------------------------------------------------------------------------

Getter = Callable[..., tuple[Optional[bytes], str]]


def _get(url: str, *, hosts: set[str], max_bytes: int, honour_robots: bool,
         getter: Optional[Getter] = None) -> bytes:
    from puffpai import source_fetch

    fetch = getter or source_fetch.get_small
    body, reason = fetch(url, allowed_hosts=hosts, max_bytes=max_bytes,
                         honour_robots=honour_robots)
    if body is None:
        raise CatalogError(reason or "The library did not answer.")
    return body


def search_url(*, q: str = "", name: str = "", lang: str = DEFAULT_LANG,
               tag: str = "", maxsize: Optional[int] = None,
               count: int = 40, start: int = 0) -> str:
    """The one search address this module builds. Parameters are encoded, never pasted."""
    params: list[tuple[str, str]] = []
    if q:
        params.append(("q", _text(q, 80)))
    if name:
        if not BOOK_NAME_RE.match(name):
            raise CatalogError("That is not a book name the library uses.")
        params.append(("name", name))
    if lang:
        if not _LANG_RE.match(lang):
            raise CatalogError("Languages are three-letter codes such as eng.")
        params.append(("lang", lang))
    if tag:
        params.append(("tag", _text(tag, 40)))
    if maxsize:
        params.append(("maxsize", str(int(maxsize))))
    params.append(("count", str(max(1, min(int(count), MAX_COUNT)))))
    if start:
        params.append(("start", str(max(0, int(start)))))
    return SEARCH_URL + "?" + urllib.parse.urlencode(params)


#: Upper bounds on what the catalog index may ask for.
MAX_CACHE_SECONDS = 3600
MAX_CACHE_ENTRIES = 64

_cache_lock = threading.Lock()
_search_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def live_lookup_rule(row: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """The ``live_lookup`` rule of the Kiwix row. Fails closed.

    The listing host publishes ``Disallow: /`` for crawlers. BuildBot asks it
    only on a person's behalf (a typed search, or finding the newest issue of
    one book after a yes). Whether it may do even that is a data switch in
    ``config/catalog_index.json``: anything but ``"allowed": true`` means no
    live lookups at all, and the saved list is used instead.
    """
    if row is None:
        try:
            from puffpai import catalogs
            row = catalogs.catalog_row(CATALOG_ID) or {}
        except Exception:  # noqa: BLE001 - no index means no live lookups
            row = {}
    rule = row.get("live_lookup") if isinstance(row, Mapping) else None
    if not isinstance(rule, Mapping) or rule.get("allowed") is not True:
        return {"allowed": False, "cache_seconds": 0}
    try:
        cache = int(rule.get("cache_seconds") or 0)
    except (TypeError, ValueError):
        cache = 0
    return {"allowed": True, "cache_seconds": max(0, min(cache, MAX_CACHE_SECONDS))}


def _require_live(rule: Mapping[str, Any]) -> None:
    if not rule.get("allowed"):
        raise LiveLookupOff("Asking the Kiwix library online is switched off.")


def _owner_switched_library_off() -> bool:
    """True when the owner switched the book library off ("What leaves your
    computer"). Read from disk each time, so a switch made in another process
    (the terminal) counts at once. Fails shut: an unreadable switch is off."""
    try:
        from puffpai import source_fetch
        from puffpai.security import outside_switches
        return outside_switches.is_off(source_fetch.LIBRARY_SERVICE)
    except Exception:  # noqa: BLE001 - fail shut
        return True


def clear_search_cache() -> None:
    with _cache_lock:
        _search_cache.clear()


def search_live(q: str, *, lang: str = DEFAULT_LANG, tag: str = "",
                maxsize: Optional[int] = None, count: int = 40,
                getter: Optional[Getter] = None,
                clock: Callable[[], float] = time.monotonic) -> list[dict[str, Any]]:
    """One live search of the library. Raises :class:`CatalogError` in plain words.

    Real network answers (no ``getter``) are kept for ``cache_seconds`` so the
    same words typed again do not ask the library again.
    """
    rule = live_lookup_rule()
    _require_live(rule)
    url = search_url(q=q, lang=lang, tag=tag, maxsize=maxsize, count=count)
    ttl = rule["cache_seconds"] if getter is None else 0
    if ttl and _owner_switched_library_off():
        # The owner's OFF wins over a remembered answer: go to the fetch,
        # which refuses (nothing is sent) and logs the refusal.
        ttl = 0
    if ttl:
        with _cache_lock:
            hit = _search_cache.get(url)
            if hit is not None and clock() - hit[0] < ttl:
                return copy.deepcopy(hit[1])
    entries = parse_feed(_get(url, hosts={SEARCH_HOST}, max_bytes=MAX_FEED_BYTES,
                              honour_robots=False, getter=getter))
    if ttl:
        with _cache_lock:
            if len(_search_cache) >= MAX_CACHE_ENTRIES:
                oldest = min(_search_cache, key=lambda k: _search_cache[k][0])
                _search_cache.pop(oldest, None)
            _search_cache[url] = (clock(), copy.deepcopy(entries))
    return entries


def newest(entries: Iterable[dict[str, Any]], name: str, flavour: str) -> Optional[dict[str, Any]]:
    matching = [e for e in entries if e.get("name") == name
                and str(e.get("flavour") or "") == flavour and e.get("meta4_url")]
    if not matching:
        return None
    return max(matching, key=lambda e: str(e.get("updated") or ""))


def resolve_book(name: str, flavour: str = "", *, lang: str = "",
                 getter: Optional[Getter] = None) -> dict[str, Any]:
    """The newest listing of one book, looked up live by name."""
    _require_live(live_lookup_rule())
    body = _get(search_url(name=name, lang=lang, count=20), hosts={SEARCH_HOST},
                max_bytes=MAX_FEED_BYTES, honour_robots=False, getter=getter)
    found = newest(parse_feed(body), name, flavour)
    if found is None:
        raise CatalogError(f"The Kiwix library no longer lists {book_id(name, flavour)}.")
    return found


def fetch_meta4(meta4_url: str, *, getter: Optional[Getter] = None) -> dict[str, Any]:
    if not _meta4_url_ok(meta4_url):
        raise CatalogError("That download description is not on the Kiwix download host.")
    body = _get(meta4_url, hosts={DOWNLOAD_HOST}, max_bytes=MAX_META4_BYTES,
                honour_robots=True, getter=getter)
    return parse_meta4(body)


def download_plan(name: str, flavour: str = "", *, getter: Optional[Getter] = None,
                  fallback_meta4_url: str = "") -> dict[str, Any]:
    """Everything a download needs, from the library itself.

    ``{filename, size, sha256, primary_url, urls, allowed_hosts, meta4_url,
    updated}``. The live listing is asked first so a reissued book is found
    under its new dated name; if the listing cannot be read, the snapshot's
    ``.meta4`` address is tried instead (it may be out of date, and then the
    download host says so).
    """
    updated = ""
    try:
        listing = resolve_book(name, flavour, getter=getter)
        meta4_url = str(listing["meta4_url"])
        updated = str(listing.get("updated") or "")
    except CatalogError:
        saved = newest(load_snapshot().get("entries") or [], name, flavour)
        meta4_url = fallback_meta4_url or str((saved or {}).get("meta4_url") or "")
        updated = "" if fallback_meta4_url else str((saved or {}).get("updated") or "")
        if not meta4_url:
            raise
    plan = fetch_meta4(meta4_url, getter=getter)
    if not plan["filename"].startswith(name):
        raise CatalogError("The download description names a different book.")
    primary = primary_download_url(meta4_url)
    if not primary.endswith("/" + plan["filename"]):
        raise CatalogError("The download description does not match its own address.")
    hosts = [DOWNLOAD_HOST] + [h for h in mirror_hosts(plan) if h != DOWNLOAD_HOST]
    return {**plan, "primary_url": primary, "allowed_hosts": hosts,
            "meta4_url": meta4_url, "updated": updated}


__all__ = [
    "SEARCH_URL", "SEARCH_HOST", "DOWNLOAD_HOST", "DEFAULT_LANG", "CATALOG_ID",
    "SOURCE_CATALOG", "ID_PREFIX", "CatalogError", "LiveLookupOff",
    "live_lookup_rule", "clear_search_cache", "safe_parse", "parse_feed",
    "parse_meta4", "primary_download_url", "mirror_hosts", "book_id",
    "split_book_id", "license_for", "audience_of", "plain_license",
    "commercial_words", "to_candidate", "default_snapshot_path",
    "load_snapshot", "snapshot_row", "search_url", "search_live", "newest",
    "resolve_book", "fetch_meta4", "download_plan",
]
