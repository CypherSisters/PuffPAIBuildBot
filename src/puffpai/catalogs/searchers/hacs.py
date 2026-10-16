"""The HACS snapshot: Home Assistant community add-ons. Look-only.

Read on this computer only; nothing is sent. Offered only when Home Assistant
is set up (``where_to_look.find_local`` checks). Every row is
``integrable: false`` and never pre-ticked: it is something to look at in
Home Assistant, not something BuildBot adds.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import threading
from typing import Any

from puffpai.catalogs import searchers as S

STEM = "hacs_2026-09-27"
SCHEMA = "hacs_snapshot/v1"
_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"key": None, "rows": []}
_FULL = re.compile(r"^([A-Za-z0-9-]{1,39})/([A-Za-z0-9_.-]{1,100})$")
_WHAT = {"integration": "A Home Assistant add-on (a connection)",
         "plugin": "A Home Assistant dashboard card", "theme": "A Home Assistant theme"}


def rows_raw() -> list[dict]:
    from puffpai.catalogs import where_to_look
    path = where_to_look.snapshot_file(STEM)
    if path is None:
        return []
    key = (path, os.path.getmtime(path))
    with _LOCK:
        if _CACHE["key"] == key:
            return list(_CACHE["rows"])
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, ValueError):
        snap = {}
    rows = [r for r in (snap.get("rows") or []) if isinstance(r, dict) and r.get("id")
            and _FULL.match(str(r.get("full_name") or ""))] \
        if isinstance(snap, dict) and snap.get("schema") == SCHEMA else []
    with _LOCK:
        _CACHE.update(key=key, rows=rows)
    return list(rows)


def _fields(row: dict) -> dict[str, str]:
    return {"name": str(row.get("name") or ""), "tag": " ".join(row.get("topics") or []),
            "text": str(row.get("description") or "")}


def _what(row: dict) -> str:
    base = _WHAT.get(str(row.get("category")), "A Home Assistant add-on")
    desc = str(row.get("description") or "").strip()
    return f"{base}: {desc}" if desc else base + "."


def _found(row: dict) -> dict[str, Any]:
    owner, name = _FULL.match(str(row["full_name"])).groups()
    stars = row.get("stars")
    repo = {"host": "github.com", "owner": owner, "name": name, "default_branch": None,
            "pinned_commit": None, "pinned_at": None,
            "stars": stars if isinstance(stars, int) and stars >= 0 else 0,
            "pushed_at": row.get("updated_at"), "archived": False, "fork": False,
            "license_reported": None, "license_read": None, "size_kb": None, "language": None,
            "topics": [str(t) for t in row.get("topics") or []][:20], "latest_release": None,
            "in_awesome_selfhosted": False, "renamed_from": None}
    rec = S.new_found(source="hacs", part="code-app", id=str(row["id"]),
                      name=str(row.get("name") or name), what_it_is=_what(row),
                      url=f"https://github.com/{owner}/{name}", repo=repo, mode_guess="other",
                      integrable=False,
                      trust={"floor_ok": False, "path": "", "pretick_ok": False,
                             "reasons": ["look_only"]})
    rec["snapshot"] = STEM
    return rec


def local(job: dict, *, kind: str, row: dict, limit: int = 25) -> list[dict]:
    if kind != "code-app":
        return []
    rows = S.local_search(job, kind, limit, key="hacs", loader=rows_raw, fields=_fields,
                          stem=STEM, row_id=lambda r: str(r.get("id")))
    return [_found(r) for r in rows]


def rows() -> list[tuple[str, dict]]:
    return [(str(r["id"]), {"name": str(r.get("name") or ""), "description": _what(r),
                            "tags": [str(t) for t in r.get("topics") or []]})
            for r in rows_raw()]


__all__ = ["STEM", "local", "rows", "rows_raw"]
