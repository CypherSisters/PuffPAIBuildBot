"""Integration records (``integration/v1``): one per integrated thing.

``<state>/integrations/<id>/record.json``. The id is the box project name,
the web app id and the app's origin label, so it is issued here once and never
again: ``<slug of name, at most 30>-<8 random base32>``,
every id ever issued appended to ``<state>/webapps/issued_ids.txt``, a new
one redrawn on collision.

What this protects against: an id that could climb out of the state folder
(every id is matched against :data:`ID_RE` before it touches a path), an app
origin being reused by a later app (ids never repeat within an install), and a
record growing fields other code would trust (only the pinned fields are
kept). What it does not: another process on this computer with the owner's
rights can still edit the files.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
from typing import Any, Optional

from puffpai import fsutil
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA = "integration/v1"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}\Z")
_B32 = "abcdefghijklmnopqrstuvwxyz234567"

STATUSES = ("fetching", "checking", "held", "building", "ready", "waiting_box",
            "refused", "failed", "removed", "checked")
#: Record modes. A project classified ``other`` keeps ``mode: None`` until it
#: runs on this computer (``integrate.run_here``): then ``own_server`` (its
#: own server), ``cli_tool`` (command line tools) or ``library``.
#: ``checked``: downloaded and checked for a yes card, nothing added yet.
MODES = ("static_web", "web_build", "cli_tool", "data_template", "picture_set",
         "folder_palette", "own_server", "library")

#: The pinned fields of ``integration/v1``, exactly.
FIELDS = ("schema", "id", "name", "purpose", "from", "plan_facts", "mode",
          "status", "drawbridge", "held", "lock", "files_root", "box", "app_id",
          "list_app_id", "book_id", "pictures_dir", "actions", "outside",
          "folder", "placed", "need", "added_at", "by", "undo", "build_failure")

_lock = threading.Lock()


class RecordError(ValueError):
    """A record that does not have the pinned shape."""


def _state_dir() -> str:
    from puffpai.config import get_state_dir
    return get_state_dir()


def root() -> str:
    return os.path.join(_state_dir(), "integrations")


def issued_path() -> str:
    return os.path.join(_state_dir(), "webapps", "issued_ids.txt")


def valid_id(integration_id: Any) -> bool:
    return isinstance(integration_id, str) and bool(ID_RE.fullmatch(integration_id))


def folder(integration_id: str) -> str:
    """``<state>/integrations/<id>``; raises on anything that is not an id."""
    if not valid_id(integration_id):
        raise RecordError("not an integration id")
    return os.path.join(root(), integration_id)


def slug(name: Any) -> str:
    """Lowercase letters, digits and single hyphens, at most 30 characters."""
    text = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    text = text[:30].strip("-")
    return text or "app"


def _issued() -> set[str]:
    try:
        return {line.strip() for line in fsutil.read_text(issued_path()).splitlines()
                if line.strip()}
    except OSError:
        return set()


def new_id(name: str) -> str:
    """A never-reused id for *name*: ``slug(name)[:30]-<8 random base32>``."""
    base = slug(name)
    with _lock:
        seen = _issued()
        if os.path.isdir(root()):
            seen |= {d for d in os.listdir(root()) if valid_id(d)}
        for _ in range(64):
            suffix = "".join(secrets.choice(_B32) for _ in range(8))
            candidate = f"{base}-{suffix}"
            if candidate not in seen and ID_RE.match(candidate):
                fsutil.atomic_append(issued_path(), candidate)
                return candidate
    raise RecordError("could not draw a new id")  # 32**8 values; never in practice


def _check(rec: Any) -> dict[str, Any]:
    if not isinstance(rec, dict):
        raise RecordError("a record is a mapping")
    if rec.get("schema") != SCHEMA:
        raise RecordError("wrong schema")
    if not valid_id(rec.get("id")):
        raise RecordError("not an integration id")
    extra = [k for k in rec if k not in FIELDS]
    if extra:
        raise RecordError(f"fields not in {SCHEMA}: {sorted(extra)}")
    if rec.get("status") not in STATUSES:
        raise RecordError("unknown status")
    mode = rec.get("mode")
    if mode is not None and mode not in MODES:
        raise RecordError("unknown mode")
    return rec


def blank(integration_id: str, *, name: str, purpose: str = "", by: str = "wizard",
          need: Optional[str] = None) -> dict[str, Any]:
    """A new record with every pinned field present."""
    return {
        "schema": SCHEMA, "id": integration_id, "name": str(name)[:80],
        "purpose": str(purpose)[:160],
        "from": {"source": "", "url": "", "commit": "", "archive_sha256": "",
                 "license_reported": "", "license_read": "", "fetched_at": ""},
        "plan_facts": {"license": "", "mode": "", "size_bytes": 0,
                       "runtime_hosts": [], "box": False},
        "mode": None, "status": "fetching",
        "drawbridge": {"verdict": "", "rows_sha": "", "checker_version": "",
                       "checked_at": "", "line": ""},
        "held": {"reasons": [], "ticket_target": ""},
        "lock": {"kind": "none", "path": "", "sha256": ""},
        "files_root": "", "box": {"project": "", "image_mb": 0},
        "app_id": None, "list_app_id": None, "book_id": None, "pictures_dir": None,
        "actions": [], "outside": [],
        "folder": {"granted": False, "mode": "ro", "realpath_sha": None},
        "placed": False, "need": need, "added_at": "", "by": str(by)[:20],
        "undo": {"kind": "integration", "ref": integration_id},
    }


def save(rec: dict) -> None:
    """Write *rec* (``integration/v1``, pinned fields only) in one step."""
    _check(rec)
    path = os.path.join(folder(rec["id"]), "record.json")
    with _lock:
        fsutil.atomic_write_json(path, rec)


def get(integration_id: str) -> dict | None:
    """The record, or None (also for anything that is not an id)."""
    if not valid_id(integration_id):
        return None
    rec = fsutil.read_json(os.path.join(root(), integration_id, "record.json"))
    try:
        return _check(rec) if rec is not None else None
    except RecordError:
        logger.warning("integration record %s has the wrong shape; ignored",
                       integration_id)
        return None


def all() -> list[dict]:  # noqa: A001 - pinned name
    """Every readable record, oldest first."""
    try:
        names = sorted(os.listdir(root()))
    except OSError:
        return []
    out = []
    for name in names:
        if valid_id(name):
            rec = get(name)
            if rec is not None:
                out.append(rec)
    out.sort(key=lambda r: str(r.get("added_at") or ""))
    return out


KEEP_REMOVED_DAYS = 30


def find(ident: str) -> dict | None:
    """The record named by its own id or by the install id of the app it
    drives (the dashboard's app page knows only the app id)."""
    rec = get(ident)
    if rec is not None:
        return rec
    if not isinstance(ident, str) or not ident:
        return None
    return next((r for r in all() if r.get("app_id") == ident
                 or r.get("list_app_id") == ident), None)


def removed_dir() -> str:
    return os.path.join(_state_dir(), "removed")


def _prune_removed(now: float) -> None:
    folder = removed_dir()
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        try:
            if name.endswith(".json") and os.path.isfile(path) and not os.path.islink(path) \
                    and now - os.path.getmtime(path) > KEEP_REMOVED_DAYS * 86400:
                os.remove(path)
        except OSError:
            continue


def _app_ids(rec: dict) -> list[str]:
    """Every app install id this integration ever had (held attempts too)."""
    ids = [rec.get("app_id")] if rec.get("app_id") else []
    stash = fsutil.read_json(os.path.join(folder(rec["id"]), "check.json"), default={})
    for a in (stash or {}).get("app_ids") or [] if isinstance(stash, dict) else []:
        if isinstance(a, str) and a not in ids:
            ids.append(a)
    return [a for a in ids if valid_id(a)]


def remove(integration_id: str, *, by: str) -> dict:
    """Take an integration out, every part of it.

    Closes and uninstalls its app (the app host clears the app's saved data
    in the runner and in her browser), removes its locked-box project, keeps a
    copy of its data for 30 days in ``<state>/removed/``, deletes its folders
    (never following a link out of them), its list app, its passages, its
    pictures shelf (never her own folder), its home tile. Its actions and
    ability rows go with the record. The ledger is kept, and the id is never
    issued again. Returns ``{"ok", "line"}``.
    """
    import json as _json
    import time as _time
    from datetime import date as _date

    from puffpai.integrate import optional, wording
    rec = find(integration_id)
    if rec is None:
        return {"ok": False, "line": wording.UNKNOWN_ITEM}
    safe_archive = optional("puffpai.security.safe_archive")
    rmtree = getattr(safe_archive, "safe_rmtree", None) if safe_archive else None
    if rmtree is None:
        return {"ok": False, "line": wording.NOT_IN_BUILD}
    iid, name = rec["id"], rec.get("name") or rec["id"]
    copy: dict[str, Any] = {"schema": "removed/v1", "id": iid, "name": name, "by": str(by)[:20],
                            "record": public_view(rec), "apps": {}, "lists": None}
    problems: list[str] = []

    # 1. keep a copy of the data first
    mirror = optional("puffpai.webapps.mirror")
    for app_id in _app_ids(rec):
        try:
            if mirror is not None:
                copy["apps"][app_id] = mirror.get(app_id)
        except Exception:  # noqa: BLE001 - nothing saved for it is nothing to keep
            pass
    if rec.get("list_app_id"):
        try:
            from puffpai.app_data import AppData
            copy["lists"] = AppData().get_data(rec["list_app_id"])
        except Exception:  # noqa: BLE001
            pass
    now = _time.time()
    try:
        os.makedirs(removed_dir(), exist_ok=True)
        fsutil.atomic_write(os.path.join(removed_dir(), f"{iid}-{_date.today().isoformat()}.json"),
                            _json.dumps(copy, ensure_ascii=False, default=str))
    except OSError as exc:
        logger.warning("integrate: the 30-day copy could not be written: %s", type(exc).__name__)
        return {"ok": False, "line": wording.REMOVE_FAILED.format(name=name)}
    _prune_removed(now)

    # 2. the app, the box project, the list app, the passages
    install = optional("puffpai.webapps.install")
    for app_id in _app_ids(rec):
        if install is not None and getattr(install, "uninstall", None):
            try:
                install.uninstall(app_id)
            except Exception as exc:  # noqa: BLE001
                problems.append("app")
                logger.warning("integrate: uninstall failed: %s", type(exc).__name__)
    # A project that runs on this computer: stopped, its folder deleted.
    try:
        from puffpai.integrate import run_here
        run_here.remove(iid, by=str(by))
    except Exception as exc:  # noqa: BLE001
        problems.append("host")
        logger.warning("integrate: the app on this computer was not removed: %s",
                       type(exc).__name__)
    driver = optional("puffpai.box.driver")
    if driver is not None and getattr(driver, "remove_waiting", None):
        try:
            driver.remove_waiting(iid)       # removed things never wait for the box
        except Exception:  # noqa: BLE001
            problems.append("waiting")
    box = rec.get("box") or {}
    if box.get("project"):
        fn = getattr(driver, "remove_project", None) if driver else None
        if fn is not None:
            try:
                fn(box["project"])
            except Exception:  # noqa: BLE001
                problems.append("box")
    if rec.get("list_app_id"):
        try:
            from puffpai import persona_scaffold
            persona_scaffold.remove(rec["list_app_id"])
        except Exception:  # noqa: BLE001
            problems.append("list")
    if rec.get("book_id"):
        try:
            from puffpai.integrate import data_import
            data_import.remove_texts(iid)
        except Exception:  # noqa: BLE001
            problems.append("text")

    # 3. folders: only under their own roots, never following a link
    from puffpai.config import get_state_dir
    state = get_state_dir()
    targets = [(folder(iid), root())]
    for app_id in [iid] + _app_ids(rec):
        targets.append((os.path.join(state, "apps", app_id), os.path.join(state, "apps")))
        targets.append((os.path.join(state, "webapps", app_id), os.path.join(state, "webapps")))
    try:
        ext_root = safe_archive.extraction_root()
        targets.append((os.path.join(ext_root, iid), ext_root))
    except Exception:  # noqa: BLE001
        pass
    if rec.get("pictures_dir"):
        try:
            from puffpai.integrate import pictures
            targets.append((pictures.shelf_dir(rec["pictures_dir"]), pictures.pictures_root()))
        except ValueError:
            pass
    for path, allowed in targets:
        if os.path.lexists(path):
            try:
                rmtree(path, allowed_roots=[allowed])
            except Exception as exc:  # noqa: BLE001
                problems.append("files")
                logger.warning("integrate: a folder was not removed: %s", type(exc).__name__)

    # 4. the tile
    try:
        from puffpai import home_placement
        home_placement.unplace("app", iid)
    except Exception:  # noqa: BLE001
        pass
    if problems:
        logger.warning("integrate: removal of %s left parts: %s", iid, sorted(set(problems)))
    return {"ok": True, "line": wording.REMOVED.format(name=name), "left": sorted(set(problems))}


def public_view(rec: dict) -> dict:
    """A record for the dashboard: no paths on this computer."""
    out = {k: v for k, v in rec.items() if k not in ("files_root", "pictures_dir")}
    lock = dict(out.get("lock") or {})
    lock.pop("path", None)
    out["lock"] = lock
    return out


__all__ = ["FIELDS", "ID_RE", "RecordError", "SCHEMA", "STATUSES", "all", "blank",
           "folder", "get", "new_id", "public_view", "remove", "save", "slug",
           "valid_id"]
