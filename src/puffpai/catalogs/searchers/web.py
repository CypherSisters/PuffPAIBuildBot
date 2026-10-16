"""Web search with the owner's own key: Brave first, Marginalia the backup.

Only with her own key. Both registry rows are ``auth: "key"`` and off by
default; with no working key this module sends nothing, and
``where_to_look.route`` leaves the row out, so the "Start looking" intro
never names a web search that will not happen.

* The key is used only through ``security.keys.keyed_get`` (the bot holds a
  handle, never the value); that call checks the outside-services switch,
  binds the key to the provider's own host and logs the request.
* The words are the ``Sendable`` phrase only, put in the address with
  ``outbound_query.url_quote``; nothing from the goal or the job text is sent.
* Results are data, read by ``web_search``'s own reader: no markup, no
  control characters, a snippet that reads like an order dropped, links
  shown and never fetched. Nothing is cached: Brave's terms do not allow
  storing its results.
* At most :data:`search_budget.CAPS_WIZARD` ``["web"]`` searches a build.

A result is something to look at, never a part to add: ``integrable`` is
false and it is never pre-ticked.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Optional

from puffpai.catalogs import searchers as S

POOL = "web"
PER_PAGE = 5
#: Registry row id -> how ``web_search`` reads that provider's answer.
_PARSERS = {"brave": "_parse_brave", "marginalia": "_parse_marginalia"}
_LOOK_ONLY = {"floor_ok": False, "path": "", "pretick_ok": False, "reasons": ["look_only"]}


def _optional(modname: str):
    return S._optional(modname)


def key_ready(key_id: Any) -> bool:
    """True only when the owner's own key for *key_id* is in the vault and works."""
    keys = _optional("puffpai.security.keys")
    if keys is None or not isinstance(key_id, str) or not key_id:
        return False
    try:
        return (keys.key_status(key_id) or {}).get("state") == "works"
    except Exception:  # noqa: BLE001 - a key that cannot be checked is not used
        return False


def query_url(row: dict, quoted_words: str) -> str:
    return str(row.get("query_template") or "").format(words=quoted_words, per_page=PER_PAGE,
                                                        min_stars=0, since="")


def _found(result: dict, row_id: str) -> dict[str, Any]:
    url = str(result.get("url") or "")
    digest = hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()[:20]
    rec = S.new_found(source="web", part="knowledge", id=f"web:{row_id}:{digest}",
                      name=str(result.get("title") or ""),
                      what_it_is=str(result.get("snippet") or ""), url=url,
                      mode_guess="other", integrable=False, trust=dict(_LOOK_ONLY, reasons=[
                          "look_only"]))
    rec["site"] = S.plain_text(result.get("site"), 80).rstrip(".")
    return rec


def _search(row_id: str, job: dict, phrases: list, *, kind: str, budget, row: dict,
            progress: Optional[Callable[[str], None]]) -> list[dict]:
    oq = _optional("puffpai.security.outbound_query")
    if oq is None or kind != "web-search":
        return []
    for p in phrases or []:
        if not oq.is_sendable(p):
            raise TypeError("web search takes only Sendable phrases")
    key_id = row.get("key_id")
    if not key_ready(key_id):
        return []                          # her own key only: no key, nothing sent
    keys = _optional("puffpai.security.keys")
    ws = _optional("puffpai.web_search")
    parse = getattr(ws, _PARSERS[row_id], None) if ws is not None else None
    if keys is None or not callable(parse):
        return []
    from puffpai import USER_AGENT
    out: list[dict] = []
    for p in list(phrases or [])[:1]:
        if budget.later(POOL) or not budget.take(POOL, progress=progress):
            break
        url = query_url(row, oq.url_quote(p))
        try:
            reply = keys.keyed_get(keys.handle(key_id), url=url, caller="web_search",
                                   headers={"Accept": "application/json",
                                            "User-Agent": USER_AGENT})
        except keys.KeyUnavailable:
            break                          # paused, locked or switched off: nothing more
        if reply.status == 429:
            budget.mark_later(POOL, progress)
            break
        if reply.status != 200:
            continue
        try:
            data = reply.json()
        except ValueError:
            continue
        for result in parse(data)[:PER_PAGE]:
            if isinstance(result, dict) and result.get("url"):
                out.append(_found(result, row_id))
    return out


def brave(job: dict, phrases: list, *, kind: str, budget, row: dict,
          progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Registry adapter for the ``brave`` row (her own Brave key only)."""
    return _search("brave", job, phrases, kind=kind, budget=budget, row=row, progress=progress)


def marginalia(job: dict, phrases: list, *, kind: str, budget, row: dict,
               progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Registry adapter for the ``marginalia`` row (her own Marginalia key only)."""
    return _search("marginalia", job, phrases, kind=kind, budget=budget, row=row,
                   progress=progress)


__all__ = ["brave", "key_ready", "marginalia", "query_url"]
