"""Hugging Face datasets: data sets with a licence tag. Look-only.

``https://huggingface.co/api/datasets?search=<one word>&limit=20&full=true``
(at most 2 a build). Kept only when the dataset is public, not gated, not
disabled, and carries exactly one ``license:<id>`` tag BuildBot can read
("any licence tag" is required: no tag, ``other`` or ``unknown`` is dropped).
A non-commercial licence is kept and labelled "personal use".

Toolsmith has no Hugging Face downloader in this build, so every find is
``integrable: false``: shown as something to look at, never as something
BuildBot adds (an honest note, not a promise).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from puffpai import artifact_review
from puffpai.catalogs import searchers as S
from puffpai.catalogs.searchers import github as G

HOST = "huggingface.co"
SERVICE = "code_search"
POOL = "huggingface"
PER_PAGE = 20
MAX_BYTES = 1024 * 1024
_ID_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.-]{0,95})/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})$")
_LICENSE_TAG = re.compile(r"^license:([a-z0-9.+-]{1,60})$")


def _optional(modname: str):
    return S._optional(modname)


def licence_of(tags: Any) -> Optional[str]:
    """The one ``license:`` tag as an id the licence words read ("cc-by-4.0" -> "CC-BY-4.0")."""
    found = [m.group(1) for t in (tags or []) if isinstance(t, str)
             for m in [_LICENSE_TAG.match(t)] if m]
    if len(set(found)) != 1 or found[0] in ("other", "unknown"):
        return None
    lic = found[0].upper()
    if lic.startswith("CC-BY") or lic.startswith("CC0"):
        return lic
    return {"MIT": "MIT", "APACHE-2.0": "Apache-2.0", "BSD-3-CLAUSE": "BSD-3-Clause",
            "BSD-2-CLAUSE": "BSD-2-Clause", "GPL-3.0": "GPL-3.0", "ODC-BY": None,
            "UNLICENSE": "Unlicense"}.get(lic)


def found_from_item(item: Any) -> Optional[dict[str, Any]]:
    from puffpai.catalogs import trust_floor, where_to_look
    if not isinstance(item, dict):
        return None
    m = _ID_RE.match(str(item.get("id") or ""))
    if not m or item.get("private") is not False or item.get("gated") not in (False, None) \
            or item.get("disabled") is True:
        return None
    lic = licence_of(item.get("tags"))
    if lic is None or where_to_look.license_words(lic) is None:
        return None
    card = item.get("cardData") if isinstance(item.get("cardData"), dict) else {}
    description = item.get("description") if isinstance(item.get("description"), str) else \
        (card.get("pretty_name") if isinstance(card.get("pretty_name"), str) else "")
    if artifact_review.reads_like_orders(G._URL_RE.sub(" ", str(description)[:2000])):
        return None
    likes = item.get("likes")
    changed = item.get("lastModified") if isinstance(item.get("lastModified"), str) else ""
    repo = {"host": HOST, "owner": m.group(1), "name": m.group(2), "default_branch": None,
            "pinned_commit": None, "pinned_at": None,
            "stars": likes if isinstance(likes, int) and not isinstance(likes, bool)
            and likes >= 0 else 0,
            "pushed_at": changed[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", changed) else None,
            "archived": False, "fork": False, "license_reported": lic, "license_read": None,
            "size_kb": None, "language": None, "topics": [], "latest_release": None,
            "in_awesome_selfhosted": False, "renamed_from": None}
    rec = S.new_found(source="huggingface", part="data-template",
                      id=f"huggingface:{m.group(1)}/{m.group(2)}", name=m.group(2),
                      friendly=G._friendly(m.group(2)),
                      what_it_is=G._clean_text(description or "A data set.", S.WHAT_IT_IS_MAX),
                      url=f"https://huggingface.co/datasets/{m.group(1)}/{m.group(2)}", repo=repo,
                      mode_guess="data_template", integrable=False)
    rec["trust"] = trust_floor.trust_record(rec)
    return rec


def _query_words(job: dict, phrases: list, kind: str, oq) -> list[list[str]]:
    """The words of each query *search* sends (one query, the best phrase)."""
    if kind != "data-template":
        return []
    out = []
    for p in G.phrases_for_kind(job, list(phrases or []), kind)[:1]:
        words = [w for w in G._core_words(oq.url_quote(p)) if w not in G.DATA_WORDS]
        if words:
            out.append(words)
    return out


def would_send(job: dict, phrases: list, *, kind: str, row: dict) -> bool:
    """Would :func:`search` ask Hugging Face anything for these phrases? Sends nothing."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None or not all(oq.is_sendable(p) for p in phrases or []):
        return False
    return bool(_query_words(job, phrases, kind, oq))


def search(job: dict, phrases: list, *, kind: str, budget, row: dict,
           progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Registry adapter: licensed data sets for *job*'s data phrases (one word a call)."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None or kind != "data-template":
        return []
    for p in phrases or []:
        if not oq.is_sendable(p):
            raise TypeError("Hugging Face search takes only Sendable phrases")
    out: list[dict] = []
    for words in _query_words(job, phrases, kind, oq):
        if budget.later(POOL) or budget.remaining(POOL) == 0:
            continue
        url = str(row.get("query_template") or "").format(words=words[0], per_page=PER_PAGE,
                                                            min_stars=0, since="")
        got = S.fetch(url, hosts=[HOST], max_bytes=MAX_BYTES, service=SERVICE, pool=POOL,
                      budget=budget, progress=progress,
                      robots=row.get("robots") != "on_request_only",
                      accept="application/json", cache_key=("huggingface_search", url))
        if got.get("status") != 200 or not got.get("body"):
            continue
        try:
            data = json.loads(got["body"].decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        for item in (data if isinstance(data, list) else [])[:PER_PAGE]:
            rec = found_from_item(item)
            if rec is not None:
                out.append(rec)
    return out


__all__ = ["found_from_item", "licence_of", "search", "would_send"]
