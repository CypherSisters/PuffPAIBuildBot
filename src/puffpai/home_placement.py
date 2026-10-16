"""What the owner put on their home page, in order.

Something added that is outside of traditional knowledge can go on the
dashboard right there. An addition that has something to show (a live card, an update, a look) can be
put on the home page when it is added: the Add page's yes step offers "Put it
on my home page" (ticked for live cards), and a chat request that names the
home page ("add the ISS tracker to my dashboard") puts it there.

One small file in the state folder, ``home_placement.json``, written whole
(:func:`puffpai.fsutil.atomic_write_json`)::

    {"schema": "home_placement/v1",
     "placed": [{"kind": "card", "ref": "<app id>", "label": "...", "at": "..."}]}

``kind`` is what the thing is (``card``, ``update``, ``look``, or a kind a
later part adds); ``ref`` is its own id. Order is the order things were put
there. The home page reads :func:`front_rows`, which keeps only what still
exists; how a row is drawn is the home page's business.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA = "home_placement/v1"
FILE = "home_placement.json"
#: The most things the home page holds; putting one more is refused plainly.
MAX_PLACED = 24

#: An addition's ``undo`` kind -> what it is on the home page. A book is not
#: here: it has nothing to show on a page (its answers come in chat).
PLACE_AS = {"card": "card", "routine": "update", "look": "look", "integration": "app"}

_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")

_lock = threading.Lock()


def _path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), FILE)


def _valid(row: Any) -> Optional[dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    kind, ref = str(row.get("kind") or ""), str(row.get("ref") or "")
    if not _KIND_RE.match(kind) or not _REF_RE.match(ref):
        return None
    label = " ".join(str(row.get("label") or "").split())[:80]
    return {"kind": kind, "ref": ref, "label": label, "at": str(row.get("at") or "")}


def placed() -> list[dict[str, Any]]:
    """Everything on the home page, in the order it was put there."""
    from puffpai import fsutil
    data = fsutil.read_json(_path(), default=None)
    rows = data.get("placed") if isinstance(data, dict) else None
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows or []:
        clean = _valid(row)
        if clean and (clean["kind"], clean["ref"]) not in seen:
            seen.add((clean["kind"], clean["ref"]))
            out.append(clean)
    return out


def _save(rows: list[dict[str, Any]]) -> None:
    from puffpai import fsutil
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fsutil.atomic_write_json(path, {"schema": SCHEMA, "placed": rows})


def is_placed(kind: str, ref: str) -> bool:
    return any(r["kind"] == kind and r["ref"] == ref for r in placed())


def place(kind: str, ref: str, label: str = "") -> bool:
    """Put one thing on the home page (last). True when it is there now."""
    row = _valid({"kind": kind, "ref": ref, "label": label,
                  "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if row is None:
        return False
    with _lock:
        rows = placed()
        if any(r["kind"] == row["kind"] and r["ref"] == row["ref"] for r in rows):
            return True
        if len(rows) >= MAX_PLACED:
            return False
        rows.append(row)
        _save(rows)
    return True


def unplace(kind: str, ref: str) -> bool:
    """Take one thing off the home page. True when it was there."""
    with _lock:
        rows = placed()
        kept = [r for r in rows if not (r["kind"] == kind and r["ref"] == ref)]
        if len(kept) == len(rows):
            return False
        _save(kept)
    return True


def for_undo(undo: Any) -> Optional[tuple[str, str]]:
    """``(kind, ref)`` on the home page for an addition's undo record, or None
    when it has nothing to show there (a book)."""
    if not isinstance(undo, dict):
        return None
    kind = PLACE_AS.get(str(undo.get("kind") or ""))
    ref = str(undo.get("ref") or "")
    if kind is None or not _REF_RE.match(ref):
        return None
    return kind, ref


def front_rows(card_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The home page's ``placed`` list: what is still there, in order.

    *card_rows* are the home page's own card rows (``front_page.cards``); a
    placed card that is gone (taken out) is left out, and so is an update or
    a look that is no longer in use. Never raises.
    """
    try:
        rows = placed()
    except Exception:  # noqa: BLE001 - the home page never breaks on this
        logger.warning("home placement could not be read", exc_info=True)
        return []
    cards = {str(c.get("app_id")): c for c in card_rows or []}
    out: list[dict[str, Any]] = []
    for row in rows:
        kind, ref = row["kind"], row["ref"]
        if kind == "card":
            card = cards.get(ref)
            if card is None:
                continue
            out.append({"kind": "card", "id": ref, "name": card.get("name") or row["label"],
                        "status": card.get("status"), "line": card.get("line"),
                        "credit": card.get("credit") or "",
                        "href": card.get("href") or f"/dashboard/apps/{ref}"})
        elif kind == "update":
            if not _routine_exists(ref):
                continue
            out.append({"kind": "update", "id": ref, "name": row["label"] or "Your update",
                        "href": ""})
        elif kind == "look":
            if not _look_in_use(ref):
                continue
            out.append({"kind": "look", "id": ref, "name": row["label"] or "Your look",
                        "href": "/dashboard/looks"})
        elif kind == "app":
            rec = _integration_ready(ref)
            if rec is None:
                continue
            target = rec.get("app_id") or rec.get("list_app_id") or ""
            out.append({"kind": "app", "id": ref, "name": rec.get("name") or row["label"],
                        "href": f"/dashboard/apps/{target}" if target else ""})
        else:
            out.append({"kind": kind, "id": ref, "name": row["label"], "href": ""})
    return out


def off_home(card_rows: list[dict[str, Any]]) -> list[str]:
    """App ids of live cards added from the Add page that are NOT on the home
    page (the owner unticked "Put it on my home page", or took it off). The
    home grid leaves these out; they stay on their own page under Apps. A
    card that came with the build is never here. Never raises.
    """
    try:
        from puffpai import add_more
        added = add_more.added()
        on = {r["ref"] for r in placed() if r["kind"] == "card"}
    except Exception:  # noqa: BLE001 - the home page never breaks on this
        logger.warning("home placement: added list could not be read", exc_info=True)
        return []
    have = {str(c.get("app_id")) for c in card_rows or []}
    out: list[str] = []
    for row in added:
        where = for_undo(row.get("undo"))
        if where and where[0] == "card" and where[1] in have                 and where[1] not in on and where[1] not in out:
            out.append(where[1])
    return out


def _routine_exists(ref: str) -> bool:
    try:
        from puffpai import routines
        return routines.get_routine(ref) is not None
    except Exception:  # noqa: BLE001
        return False


def _integration_ready(ref: str) -> Optional[dict[str, Any]]:
    """The integration record *ref* when it is ready (Toolsmith's app, list,
    pictures or mood board), else None."""
    try:
        from puffpai.integrate import records
        rec = records.get(ref)
    except Exception:  # noqa: BLE001
        return None
    return rec if rec is not None and rec.get("status") == "ready" else None


def _look_in_use(ref: str) -> bool:
    try:
        from puffpai.looks import store
        return str((store.current() or {}).get("id") or "") == ref
    except Exception:  # noqa: BLE001
        return False


__all__ = ["FILE", "MAX_PLACED", "PLACE_AS", "SCHEMA", "for_undo", "front_rows",
           "is_placed", "off_home", "place", "placed", "unplace"]
