"""PyPI: one exact package name at a time (no search: PyPI's search page asks for a browser check).

``https://pypi.org/pypi/<name>/json`` for a phrase that is exactly one word
and a valid package name (a README that says ``pip install <name>`` is the
usual source of such a word). At most 3 a build. The licence comes from the
package's own record (``license_expression``, else a single ``License ::``
classifier). Whether it has a command is not in PyPI's record, so a PyPI
find is shown as a library unless a later read proves a command: never
added on a guess.

PyPI's robots.txt asks crawlers to keep off ``/pypi/*/json``. BuildBot asks
only after the owner presses "Start looking" (the registry row says
``on_request_only``).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from puffpai import artifact_review
from puffpai.catalogs import searchers as S
from puffpai.catalogs.searchers import github as G

HOST = "pypi.org"
SERVICE = "code_search"
POOL = "pypi"
MAX_BYTES = 1024 * 1024
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,98}[a-z0-9])?$")
_CLASSIFIER_SPDX = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: BSD License": "BSD",
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)": "GPL-3.0",
    "License :: OSI Approved :: GNU General Public License v2 (GPLv2)": "GPL-2.0",
    "License :: OSI Approved :: GNU Affero General Public License v3": "AGPL-3.0",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL-3.0",
    "License :: CC0 1.0 Universal (CC0 1.0) Public Domain Dedication": "CC0-1.0",
    "License :: OSI Approved :: The Unlicense (Unlicense)": "Unlicense",
}


def _optional(modname: str):
    return S._optional(modname)


def licence_of(info: dict) -> Optional[str]:
    expr = info.get("license_expression")
    if isinstance(expr, str) and 0 < len(expr) <= 120:
        return expr
    known = [_CLASSIFIER_SPDX[c] for c in info.get("classifiers") or []
             if isinstance(c, str) and c in _CLASSIFIER_SPDX]
    return known[0] if len(set(known)) == 1 else None


def found_from_json(data: Any, name: str, *, part: str) -> Optional[dict[str, Any]]:
    info = data.get("info") if isinstance(data, dict) and isinstance(data.get("info"), dict) else None
    if info is None or str(info.get("name") or "").lower().replace("_", "-") != name.replace("_", "-"):
        return None
    summary = info.get("summary") if isinstance(info.get("summary"), str) else ""
    if artifact_review.reads_like_orders(G._URL_RE.sub(" ", summary[:2000])):
        return None
    version = info.get("version") if isinstance(info.get("version"), str) \
        and re.fullmatch(r"[0-9A-Za-z.+!-]{1,60}", info["version"]) else None
    urls = info.get("project_urls") if isinstance(info.get("project_urls"), dict) else {}
    link = None
    for u in list(urls.values())[:20]:
        m = re.match(r"^https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9_.-]{1,100}?)(?:\.git)?/?$",
                     str(u or ""))
        if m and G.repo_ok(m.group(1), m.group(2)):
            link = (m.group(1), m.group(2))
            break
    repo = {"host": "github.com" if link else "pypi.org", "owner": link[0] if link else None,
            "name": link[1] if link else name, "default_branch": None, "pinned_commit": None,
            "pinned_at": None, "stars": 0, "pushed_at": None, "archived": False, "fork": False,
            "license_reported": licence_of(info), "license_read": None, "size_kb": None,
            "language": "Python", "topics": [], "latest_release": version,
            "in_awesome_selfhosted": False, "renamed_from": None}
    return S.new_found(source="pypi", part=part, id=f"pypi:{name}", name=name,
                       friendly=G._friendly(name), what_it_is=G._clean_text(summary, S.WHAT_IT_IS_MAX),
                       url=f"https://pypi.org/project/{name}/", repo=repo,
                       package={"registry": "pypi", "name": name, "version": version,
                                "downloads_monthly": None, "bin": [], "repo_name_matches": None})


def _lookup(name: str, *, part: str, budget, row: dict, progress) -> Optional[dict[str, Any]]:
    url = str(row.get("query_template") or "").format(words=name, per_page=1, min_stars=0,
                                                        since="")
    got = S.fetch(url, hosts=[HOST], max_bytes=MAX_BYTES, service=SERVICE, pool=POOL,
                  budget=budget, progress=progress,
                  robots=row.get("robots") != "on_request_only",
                  accept="application/json", cache_key=("pypi_json", name))
    if got.get("status") != 200 or not got.get("body"):
        return None
    try:
        data = json.loads(got["body"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return found_from_json(data, name, part=part)


def _names(job: dict, phrases: list, kind: str, oq) -> list[str]:
    """The exact package names *lookup* asks for: a one-word phrase that is a valid name."""
    if kind != "cli":
        return []
    out = []
    for p in G.phrases_for_kind(job, list(phrases or []), kind):
        quoted = oq.url_quote(p)
        if "+" in quoted or not NAME_RE.match(quoted):
            continue                          # exact names only, never a sentence
        out.append(quoted)
    return out


def would_send(job: dict, phrases: list, *, kind: str, row: dict) -> bool:
    """Would :func:`lookup` ask PyPI anything for these phrases? Sends nothing."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None or not all(oq.is_sendable(p) for p in phrases or []):
        return False
    return bool(_names(job, phrases, kind, oq))


def lookup(job: dict, phrases: list, *, kind: str, budget, row: dict,
           progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Registry adapter: exact names only (a one-word phrase that is a valid package name)."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None or kind != "cli":
        return []
    for p in phrases or []:
        if not oq.is_sendable(p):
            raise TypeError("PyPI lookup takes only Sendable phrases")
    out = []
    for quoted in _names(job, phrases, kind, oq):
        if budget.later(POOL) or budget.remaining(POOL) == 0:
            break
        rec = _lookup(quoted, part=kind, budget=budget, row=row, progress=progress)
        if rec is not None:
            out.append(rec)
    return out


def details(found: dict[str, Any], *, budget, row: Optional[dict] = None,
            progress: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    """Re-read the package's own record (its facts never come from the find)."""
    pkg = found.get("package") if isinstance(found.get("package"), dict) else {}
    name = pkg.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name) or found.get("id") != f"pypi:{name}":
        return found
    from puffpai.catalogs import where_to_look
    search_row = where_to_look.row_by_id("pypi") or {}
    fresh = _lookup(name, part=str(found.get("part") or "cli"), budget=budget, row=search_row,
                    progress=progress)
    if fresh is None:
        found["package"] = dict(pkg, downloads_monthly=None, bin=[], repo_name_matches=None)
        return found
    fresh["meaning"] = found.get("meaning")
    return fresh


__all__ = ["details", "found_from_json", "licence_of", "lookup", "would_send"]
