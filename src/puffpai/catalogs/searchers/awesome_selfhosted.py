"""The awesome-selfhosted snapshot: programs people run for themselves, with their licences.

Read on this computer only; nothing is sent. Three uses:

* :func:`local` - code finds for a job (words first, then meaning, shipped
  vectors). A listed project on GitHub or Codeberg is read at a pinned commit
  like any other find when the plan looks closer (``where_to_look.details``),
  so a listed static page can be added; the rest are notes.
* :func:`listed` - the trust floor's "listed" path: is this address in the
  list, and under which licences (all of them must be open: they are joined
  with AND, so the strictest one decides). A licence the list calls non-free
  makes the row unusable.
* :func:`topics` - the list's tags as GitHub topic words (one word each), for
  the topic query shape.

The data is awesome-selfhosted-data (CC BY-SA 3.0); the snapshot's header
keeps the attribution and the source commit. awesome-selfhosted lists
programs, not data sets, so it answers code jobs only (measured: none of its
tags name a data set).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import threading
from typing import Any, Optional

from puffpai.catalogs import searchers as S

STEM = "awesome_selfhosted_2026-09-27"
SCHEMA = "awesome_selfhosted_snapshot/v1"
_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"key": None}
_SPDX_ID = re.compile(r"^[A-Za-z0-9.+-]{1,60}$")


def _load() -> dict[str, Any]:
    """The snapshot with its lookup tables (read once per file change). Empty when absent."""
    from puffpai.catalogs import snapshot_build_code as build
    from puffpai.catalogs import where_to_look
    path = where_to_look.snapshot_file(STEM)
    if path is None:
        return {"rows": [], "by_repo": {}, "by_url": {}, "tags": {}}
    key = (path, os.path.getmtime(path))
    with _LOCK:
        if _CACHE["key"] == key:
            return _CACHE["data"]
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, ValueError):
        snap = {}
    if not isinstance(snap, dict) or snap.get("schema") != SCHEMA:
        snap = {"rows": []}
    rows = [r for r in snap.get("rows") or [] if isinstance(r, dict) and r.get("id")]
    by_repo: dict[str, dict] = {}
    by_url: dict[str, dict] = {}
    for r in rows:
        for u in (r.get("source_code_url"), r.get("website_url")):
            k = build.repo_key(u)
            if k:
                by_repo.setdefault(k, r)
            if isinstance(u, str) and u:
                by_url.setdefault(u.lower().rstrip("/"), r)
    data = {"rows": rows, "by_repo": by_repo, "by_url": by_url,
            "tags": dict(snap.get("tag_slugs") or {})}
    with _LOCK:
        _CACHE.update(key=key, data=data)
    return data


def rows_raw() -> list[dict]:
    return list(_load()["rows"])


def license_of(row: dict) -> Optional[str]:
    """The row's licences as one expression (``A AND B``), or None when any is non-free or odd."""
    lic = [str(x) for x in row.get("licenses") or []]
    if not lic or row.get("nonfree") or not all(_SPDX_ID.match(x) for x in lic):
        return None
    return lic[0] if len(lic) == 1 else " AND ".join(lic)


def listed(url: Any) -> Optional[dict[str, Any]]:
    """The list's row for *url* (a project or its website), with ``license`` set; else None."""
    from puffpai.catalogs import snapshot_build_code as build
    data = _load()
    row = None
    key = build.repo_key(url)
    if key:
        row = data["by_repo"].get(key)
    if row is None and isinstance(url, str) and url:
        row = data["by_url"].get(url.lower().rstrip("/"))
    if row is None:
        return None
    return dict(row, license=license_of(row))


def topics() -> set[str]:
    """The list's tags that are one word, as GitHub topic words."""
    return {s for s in _load()["tags"].values() if re.fullmatch(r"[a-z0-9]{3,40}", s or "")}


def _fields(row: dict) -> dict[str, str]:
    return {"name": str(row.get("name") or ""), "tag": " ".join(row.get("tags") or []),
            "text": str(row.get("description") or "")}


def _repo(row: dict) -> Optional[dict[str, Any]]:
    from puffpai.catalogs import snapshot_build_code as build
    key = build.repo_key(row.get("source_code_url"))
    if not key:
        return None
    host, owner, name = key.split("/")
    real = re.match(r"^https://[^/]+/([^/]+)/([^/]+?)(?:\.git)?/?$", str(row["source_code_url"]))
    stars = row.get("stars")
    return {"host": host, "owner": real.group(1) if real else owner,
            "name": real.group(2) if real else name, "default_branch": None,
            "pinned_commit": None, "pinned_at": None,
            "stars": stars if isinstance(stars, int) and stars >= 0 else 0,
            "pushed_at": row.get("updated_at"), "archived": row.get("archived") is True,
            "fork": False, "license_reported": license_of(row), "license_read": None,
            "size_kb": None, "language": None, "topics": [], "latest_release": None,
            "in_awesome_selfhosted": True, "renamed_from": None}


def _found(row: dict, part: str) -> dict[str, Any]:
    from puffpai.catalogs import trust_floor
    rec = S.new_found(source="awesome_selfhosted", part=part, id=str(row["id"]),
                      name=str(row.get("name") or ""), what_it_is=str(row.get("description") or ""),
                      url=str(row.get("source_code_url") or row.get("website_url") or ""),
                      repo=_repo(row), mode_guess="unknown", integrable=False)
    rec["trust"] = trust_floor.trust_record(rec)
    rec["snapshot"] = STEM
    return rec


def local(job: dict, *, kind: str, row: dict, limit: int = 25) -> list[dict]:
    """Code finds for *job*. A data-template job gets none (the list holds programs)."""
    if kind != "code-app":
        return []
    rows = S.local_search(job, kind, limit, key="awesome_selfhosted", loader=rows_raw,
                          fields=_fields, stem=STEM, row_id=lambda r: str(r.get("id")))
    return [_found(r, "code-app") for r in rows]


def rows() -> list[tuple[str, dict]]:
    return [(str(r["id"]), {"name": str(r.get("name") or ""),
                            "description": str(r.get("description") or ""),
                            "tags": [str(t) for t in r.get("tags") or []]})
            for r in rows_raw()]


__all__ = ["STEM", "license_of", "listed", "local", "rows", "rows_raw", "topics"]
