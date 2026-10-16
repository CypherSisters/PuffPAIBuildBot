"""Openverse: openly licensed pictures from many collections.

One search a build at most 2 (budget pool ``openverse``; Openverse's own pace
is 20 a minute and 200 a day keyless): the phrase's words, only licences that
allow use and change (``license_type=commercial,modification``) and never
mature pictures (``mature=false``, and any row saying ``mature: true`` is
dropped too). Each thumbnail comes from api.openverse.org only
(``picture_search``) and is re-drawn on this computer
(:func:`searchers.save_thumbnail`).

Openverse's robots.txt asks crawlers to keep off ``/v1/images/``. BuildBot
asks only after the owner presses "Start looking", a few times a build (the
registry row says ``on_request_only``).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Callable, Optional

from puffpai.catalogs import searchers as S

HOST = "api.openverse.org"
SERVICE = "picture_search"
POOL = "openverse"
PER_PAGE = 10
MAX_BYTES = 512 * 1024
_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CODE_RE = re.compile(r"^(?:by|by-sa|by-nd|by-nc|by-nc-sa|by-nc-nd|cc0|pdm)$")
_VERSION_RE = re.compile(r"^\d\.\d$")


def _optional(modname: str):
    return S._optional(modname)


def licence_name(code: Any, version: Any) -> Optional[str]:
    """Openverse's licence code as a name the licence words know ("CC BY-SA 2.0")."""
    code = str(code or "").lower()
    if not _CODE_RE.match(code):
        return None
    if code == "pdm":
        return "Public domain"
    if code == "cc0":
        return "CC0 1.0"
    version = str(version or "")
    return f"CC {code.upper()}" + (f" {version}" if _VERSION_RE.match(version) else "")


def thumb_url(image_id: str) -> str:
    if not _ID_RE.match(image_id or ""):
        raise ValueError("not an Openverse picture id")
    return f"https://{HOST}/v1/images/{image_id}/thumb/"


def found_from_result(item: Any, *, budget, progress=None,
                      fetch_thumb: bool = True) -> Optional[dict[str, Any]]:
    from puffpai.catalogs import trust_floor, where_to_look
    if not isinstance(item, dict) or item.get("mature") is not False:
        return None                       # only pictures that say they are not mature
    image_id = str(item.get("id") or "")
    if not _ID_RE.match(image_id):
        return None
    licence = licence_name(item.get("license"), item.get("license_version"))
    if licence is None or where_to_look.license_words(licence) is None:
        return None
    name = S.plain_text(item.get("title"), 100).rstrip(".") or "A picture"
    credit = S.plain_text(item.get("creator"), 120).rstrip(".") or \
        S.plain_text(item.get("provider"), 60).rstrip(".") or "Openverse"
    thumb_local = None
    if fetch_thumb:
        url = thumb_url(image_id)
        got = S.fetch(url, hosts=[HOST], max_bytes=S.THUMB_MAX_BYTES, service=SERVICE,
                      pool=POOL + "_thumb", budget=budget, progress=progress, robots=False,
                      cache_key=("openverse_thumb", image_id))
        thumb_local = S.save_thumbnail(got.get("body")) if got.get("status") == 200 else None
        if thumb_local is None:
            return None
    landing = str(item.get("foreign_landing_url") or "")
    parts = urllib.parse.urlsplit(landing)
    page = landing if parts.scheme == "https" and parts.hostname and len(landing) <= 300 \
        else f"https://openverse.org/image/{image_id}"
    rec = S.new_found(source="openverse", part="pictures", id=f"openverse:{image_id}", name=name,
                      friendly=name, what_it_is=f"A picture by {credit}.", url=page,
                      picture={"license": licence, "credit": credit, "thumb_local": thumb_local,
                               "full_host": urllib.parse.urlsplit(str(item.get("url") or "")).hostname
                               or None},
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
    """Would :func:`search` ask Openverse anything for these phrases? Sends nothing."""
    oq = S._optional("puffpai.security.outbound_query")
    if oq is None or not all(oq.is_sendable(p) for p in phrases or []):
        return False
    return bool(_mine(job, phrases, kind))


def search(job: dict, phrases: list, *, kind: str, budget, row: dict,
           progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Registry adapter: pictures for *job*'s picture phrases."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None or kind != "pictures":
        return []
    for p in phrases or []:
        if not oq.is_sendable(p):
            raise TypeError("Openverse search takes only Sendable phrases")
    out: list[dict] = []
    for p in _mine(job, phrases, kind):
        if budget.later(POOL) or budget.remaining(POOL) == 0:
            break
        url = str(row.get("query_template") or "").format(words=oq.url_quote(p),
                                                            per_page=PER_PAGE, min_stars=0,
                                                            since="")
        got = S.fetch(url, hosts=[HOST], max_bytes=MAX_BYTES, service=SERVICE, pool=POOL,
                      budget=budget, progress=progress,
                      robots=row.get("robots") != "on_request_only",
                      accept="application/json", cache_key=("openverse_search", url))
        if got.get("status") != 200 or not got.get("body"):
            continue
        try:
            data = json.loads(got["body"].decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        for item in ((data.get("results") or []) if isinstance(data, dict) else [])[:PER_PAGE]:
            rec = found_from_result(item, budget=budget, progress=progress)
            if rec is not None:
                out.append(rec)
    return out


__all__ = ["found_from_result", "licence_name", "search", "thumb_url"]
