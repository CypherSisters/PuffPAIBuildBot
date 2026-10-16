"""Wikimedia Commons: free pictures, each with its licence and credit.

One search a build at most 2 (budget pool ``commons``): the phrase's words in
the file namespace, 10 answers, each with a 320-pixel thumbnail address and
its licence. Every thumbnail is fetched (``picture_search``) from Wikimedia's
own picture hosts only (thumb.wikimedia.org, upload.wikimedia.org) and re-drawn on this computer
(:func:`searchers.save_thumbnail`); the dashboard shows only that copy.
A picture with no licence BuildBot can read is dropped. Only files whose type
is a JPEG, PNG, GIF or WebP picture are kept (no SVG).

Commons' robots.txt asks crawlers to keep off its programs' addresses.
BuildBot asks only after the owner presses "Start looking", a few times a
build (the registry row says ``on_request_only``).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import urllib.parse
from typing import Any, Callable, Optional

from puffpai.catalogs import searchers as S

API_HOST = "commons.wikimedia.org"
FILE_HOST = "upload.wikimedia.org"
#: Where Commons serves thumbnails (measured: thumb.wikimedia.org for
#: scaled copies, upload.wikimedia.org for originals). Nothing else is asked.
THUMB_HOSTS = frozenset({"thumb.wikimedia.org", "upload.wikimedia.org"})
SERVICE = "picture_search"
POOL = "commons"
PER_PAGE = 10
MAX_BYTES = 512 * 1024
_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_TAG_RE = re.compile(r"<[^>]{0,500}>")


def _optional(modname: str):
    return S._optional(modname)


def _plain(value: Any, limit: int) -> str:
    text = html_lib.unescape(_TAG_RE.sub(" ", str(value or "")))
    return S.plain_text(text, limit).rstrip(".")


def _meta(info: dict, key: str) -> str:
    rec = (info.get("extmetadata") or {}).get(key)
    return str(rec.get("value") or "") if isinstance(rec, dict) else ""


def _thumb_ok(url: Any) -> bool:
    parts = urllib.parse.urlsplit(str(url or ""))
    return parts.scheme == "https" and parts.hostname in THUMB_HOSTS and not parts.port \
        and not parts.username and len(str(url)) <= 1000


def query_url(row: dict, quoted_words: str) -> str:
    return str(row.get("query_template") or "").format(words=quoted_words, per_page=PER_PAGE,
                                                        min_stars=0, since="")


def found_from_page(page: Any, *, budget, progress=None,
                    fetch_thumb: bool = True) -> Optional[dict[str, Any]]:
    from puffpai.catalogs import trust_floor, where_to_look
    if not isinstance(page, dict):
        return None
    infos = page.get("imageinfo")
    info = infos[0] if isinstance(infos, list) and infos and isinstance(infos[0], dict) else None
    title = str(page.get("title") or "")
    pageid = page.get("pageid")
    if info is None or not title.startswith("File:") or not isinstance(pageid, int):
        return None
    if str(info.get("mime") or "") not in _MIMES:
        return None                       # SVG and the rest: not a plain picture
    licence = _plain(_meta(info, "LicenseShortName"), 60)
    if not licence or where_to_look.license_words(licence) is None:
        return None
    thumb_url = info.get("thumburl")
    if not _thumb_ok(thumb_url):
        return None
    name = _plain(re.sub(r"\.[A-Za-z0-9]{2,5}$", "", title[len("File:"):]), 100)
    credit = _plain(_meta(info, "Artist"), 120) or "Wikimedia Commons"
    thumb_local = None
    if fetch_thumb:
        host = urllib.parse.urlsplit(str(thumb_url)).hostname
        got = S.fetch(str(thumb_url), hosts=[host], max_bytes=S.THUMB_MAX_BYTES,
                      service=SERVICE, pool=POOL + "_thumb", budget=budget, progress=progress,
                      robots=False, cache_key=("commons_thumb", str(thumb_url)))
        thumb_local = S.save_thumbnail(got.get("body")) if got.get("status") == 200 else None
        if thumb_local is None:
            return None                   # no safe copy: never shown from outside
    rec = S.new_found(source="commons", part="pictures", id=f"commons:{pageid}", name=name,
                      friendly=name, what_it_is=f"A picture by {credit}.",
                      url=str(info.get("descriptionurl") or
                              f"https://commons.wikimedia.org/?curid={pageid}")[:300],
                      picture={"license": licence, "credit": credit, "thumb_local": thumb_local,
                               "full_host": FILE_HOST},
                      mode_guess="other", integrable=True)
    rec["trust"] = trust_floor.trust_record(rec)
    return rec


def _mine(job: dict, phrases: list, kind: str) -> list:
    """The phrase *search* sends (the best one for the job's pictures part)."""
    if kind != "pictures":
        return []
    from puffpai.catalogs.searchers import github as G
    return G.phrases_for_kind(job, list(phrases or []), kind)[:1]


def would_send(job: dict, phrases: list, *, kind: str, row: dict) -> bool:
    """Would :func:`search` ask Commons anything for these phrases? Sends nothing."""
    oq = S._optional("puffpai.security.outbound_query")
    if oq is None or not all(oq.is_sendable(p) for p in phrases or []):
        return False
    return bool(_mine(job, phrases, kind))


def search(job: dict, phrases: list, *, kind: str, budget, row: dict,
           progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Registry adapter: pictures for *job*'s picture phrases (at most one call each, 2 a build)."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None or kind != "pictures":
        return []
    for p in phrases or []:
        if not oq.is_sendable(p):
            raise TypeError("Commons search takes only Sendable phrases")
    mine = _mine(job, phrases, kind)
    out: list[dict] = []
    for p in mine:
        if budget.later(POOL) or budget.remaining(POOL) == 0:
            break
        url = query_url(row, oq.url_quote(p))
        got = S.fetch(url, hosts=[API_HOST], max_bytes=MAX_BYTES, service=SERVICE, pool=POOL,
                      budget=budget, progress=progress,
                      robots=row.get("robots") != "on_request_only",
                      accept="application/json", cache_key=("commons_search", url))
        if got.get("status") != 200 or not got.get("body"):
            continue
        try:
            data = json.loads(got["body"].decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        pages = ((data.get("query") or {}).get("pages") or []) if isinstance(data, dict) else []
        pages = sorted((pg for pg in pages if isinstance(pg, dict)),
                       key=lambda pg: pg.get("index") if isinstance(pg.get("index"), int) else 99)
        for pg in pages[:PER_PAGE]:
            rec = found_from_page(pg, budget=budget, progress=progress)
            if rec is not None:
                out.append(rec)
    return out


__all__ = ["found_from_page", "query_url", "search", "would_send"]
