"""The bot's copy of a browser app's stored data (its localStorage).

Why a copy: the browser keeps an app's storage apart per top-level site, so
the same app framed under ``localhost`` and under ``127.0.0.1`` would see two
different data sets (measured). The host inlines this copy into the page
before the app runs and the shim writes every change back with the version
it started from. A write from a stale page gets 409 and the page reloads, so
it never overwrites newer data (the bot's hidden browser and her screens all
see one truth).

Rules (pinned): versioned; 409 on a stale write; the last 10 versions are kept
for undo; at most 5 MB per app; ``watch.jsonl`` at most 1 MB, rotated; cookie
values are never logged (the watch log keeps closed fields only).

While the bot runs one of the app's actions (:func:`begin_bot_run` to
:func:`end_bot_run`), only the bot's own hidden browser may write
(``by="runner"``). A write from the owner's screen gets 423 and the shim sends
it again once the bot is done (then the usual 409 rule applies). Without this,
an edit she made during a bot action was counted as the action's own and
could be put back or undone with it. An undo
(:func:`restore`) is refused while a bot run holds the app.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any

from puffpai import webapps

MAX_BYTES = 5 * 1024 * 1024
KEEP_VERSIONS = 10
WATCH_MAX_BYTES = 1024 * 1024

_LOCK = threading.RLock()
#: label -> monotonic deadline while a bot action holds the app's copy.
_busy: dict[str, float] = {}
#: The longest a bot run may hold an app (every runner call has a shorter
#: timeout, so this is only a floor for a run that never ended).
BOT_RUN_MAX_S = 300.0
_WATCH_FIELDS = ("kind", "host", "directive", "method", "reason")
_FIELD_RE = re.compile(r"^[A-Za-z0-9 ._:/\-]{0,120}$")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _dir(label: str) -> str:
    return webapps.label_state_dir(label)


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _current(label: str) -> dict:
    cur = _read_json(os.path.join(_dir(label), "storage.json"), None)
    if not isinstance(cur, dict) or not isinstance(cur.get("v"), int) \
            or not isinstance(cur.get("data"), dict):
        return {"v": 0, "data": {}}
    return {"v": cur["v"], "data": cur["data"], "at": cur.get("at")}


def valid_data(data: Any) -> bool:
    return isinstance(data, dict) and all(
        isinstance(k, str) and isinstance(v, str) for k, v in data.items())


def data_bytes(data: dict) -> int:
    return len(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def get(label: str) -> dict:
    """``{"v": int, "data": {key: str}}``; version 0 and no data when new."""
    with _LOCK:
        cur = _current(label)
    return {"v": cur["v"], "data": dict(cur["data"])}


def version(label: str) -> int:
    with _LOCK:
        return _current(label)["v"]


def _store(label: str, new_v: int, data: dict) -> None:
    base = _dir(label)
    _write_json(os.path.join(base, "versions", f"{new_v}.json"),
                {"v": new_v, "data": data, "at": _now()})
    _write_json(os.path.join(base, "storage.json"), {"v": new_v, "data": data, "at": _now()})
    # keep the last KEEP_VERSIONS versions (the current one included)
    vdir = os.path.join(base, "versions")
    try:
        names = os.listdir(vdir)
    except OSError:
        return
    for name in names:
        m = re.fullmatch(r"(\d+)(?:\.undo)?\.json", name)
        if m and int(m.group(1)) <= new_v - KEEP_VERSIONS:
            try:
                os.remove(os.path.join(vdir, name))
            except OSError:
                pass


def _is_busy(label: str) -> bool:
    """Caller holds ``_LOCK``."""
    deadline = _busy.get(label)
    if deadline is None:
        return False
    if time.monotonic() > deadline:
        _busy.pop(label, None)
        return False
    return True


def busy(label: str) -> bool:
    """True while a bot action holds this app's copy."""
    with _LOCK:
        return _is_busy(label)


def begin_bot_run(label: str) -> None:
    """From now until :func:`end_bot_run`, only the bot's hidden browser writes."""
    _dir(label)                      # refuses anything that is not a label
    with _LOCK:
        _busy[label] = time.monotonic() + BOT_RUN_MAX_S


def end_bot_run(label: str) -> None:
    with _LOCK:
        _busy.pop(label, None)


def put(label: str, base: Any, data: Any, *, by: str = "page") -> tuple[int, dict]:
    """Write ``data`` if ``base`` is the current version. Returns (status, body).

    ``by`` is ``"runner"`` only for a write the host saw carrying the runner
    secret; every other write is the owner's screen (``"page"``) and waits
    (423) while a bot action holds the app.
    """
    if not valid_data(data):
        return 400, {"error": "data must be strings"}
    if data_bytes(data) > MAX_BYTES:
        return 413, {"error": "too_big"}
    if not isinstance(base, int) or isinstance(base, bool):
        return 400, {"error": "base"}
    with _LOCK:
        cur = _current(label)
        if by != "runner" and _is_busy(label):
            return 423, {"busy": True, "v": cur["v"]}
        if base != cur["v"]:
            return 409, {"v": cur["v"]}
        if data == cur["data"]:
            # an app that saves the same data again on opening (a read, or
            # the owner looking at it) changed nothing: no new version, so
            # "undo" of her last change still works (final fix)
            return 200, {"v": cur["v"]}
        new_v = cur["v"] + 1
        _store(label, new_v, data)
    return 200, {"v": new_v}


def _version_data(label: str, v: int) -> Any:
    if v == 0:
        return {}
    rec = _read_json(os.path.join(_dir(label), "versions", f"{v}.json"), None)
    if isinstance(rec, dict) and rec.get("v") == v and valid_data(rec.get("data")):
        return rec["data"]
    return None


def restore(app_id: str, version: int) -> dict:
    """Undo the change that made ``version``: put back what was there before it.

    Refused when a newer write exists (someone, or the app, changed the data
    after that change), when the older copy is no longer kept, or for an id
    that is not an app's.
    """
    if not webapps.valid_app_id(app_id):
        return {"ok": False, "why": "not_an_app"}
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return {"ok": False, "why": "bad_version"}
    with _LOCK:
        if _is_busy(app_id):
            return {"ok": False, "why": "busy"}
        cur = _current(app_id)
        if cur["v"] != version:
            return {"ok": False, "why": "newer_write" if cur["v"] > version else "unknown_version"}
        older = _undo_data(app_id, version)
        if older is None:
            older = _version_data(app_id, version - 1)
        if older is None:
            return {"ok": False, "why": "not_kept"}
        _store(app_id, cur["v"] + 1, older)
    return {"ok": True, "why": ""}


def _undo_data(label: str, v: int) -> Any:
    rec = _read_json(os.path.join(_dir(label), "versions", f"{v}.undo.json"), None)
    if isinstance(rec, dict) and rec.get("v") == v and valid_data(rec.get("data")):
        return rec["data"]
    return None


def mark_undo(app_id: str, version: int, data: Any) -> bool:
    """Remember that undoing ``version`` means going back to ``data``.

    An action can make several writes; its undo puts back the data from
    before the whole action, not just before its last write. Kept and pruned
    with the version it belongs to. False when nothing was remembered.
    """
    if not webapps.valid_app_id(app_id) or not valid_data(data):
        return False
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return False
    with _LOCK:
        if _current(app_id)["v"] != version:
            return False
        _write_json(os.path.join(_dir(app_id), "versions", f"{version}.undo.json"),
                    {"v": version, "data": data, "at": _now()})
    return True


def put_back(app_id: str, to_version: int, *, data: Any = None) -> dict:
    """Make the data what it was at ``to_version`` again, as a new version.

    Used when an action did more than it should: every write since
    ``to_version`` is undone at once (an action can make several). ``data``
    is the copy the caller held in memory since ``to_version`` (actions always
    pass it, so a run that made more than ``KEEP_VERSIONS`` writes is still
    put back); without it the kept version file is used. Nothing is written
    when the data already is that copy. Refused when neither is available.
    The caller must check ``ok``.
    """
    if not webapps.valid_app_id(app_id):
        return {"ok": False, "why": "not_an_app"}
    if not isinstance(to_version, int) or isinstance(to_version, bool) or to_version < 0:
        return {"ok": False, "why": "bad_version"}
    if data is not None and not valid_data(data):
        return {"ok": False, "why": "bad_data"}
    with _LOCK:
        cur = _current(app_id)
        older = dict(data) if data is not None else (
            None if cur["v"] <= to_version else _version_data(app_id, to_version))
        if data is None and cur["v"] <= to_version:
            return {"ok": True, "why": ""}
        if older is None:
            return {"ok": False, "why": "not_kept"}
        if cur["data"] == older:
            return {"ok": True, "why": ""}
        _store(app_id, cur["v"] + 1, older)
    return {"ok": True, "why": ""}


def clear(label: str) -> None:
    """Forget a check copy's data (check copies only)."""
    if not webapps.CHECK_RE.fullmatch(label or ""):
        raise ValueError("only a check copy is cleared this way")
    base = _dir(label)
    with _LOCK:
        for sub in ("versions",):
            d = os.path.join(base, sub)
            if os.path.isdir(d):
                for name in os.listdir(d):
                    try:
                        os.remove(os.path.join(d, name))
                    except OSError:
                        pass
                try:
                    os.rmdir(d)
                except OSError:
                    pass
        for name in ("storage.json", "watch.jsonl", "watch.1.jsonl"):
            try:
                os.remove(os.path.join(base, name))
            except OSError:
                pass
        try:
            os.rmdir(base)
        except OSError:
            pass


def watch(label: str, **fields: Any) -> None:
    """One line in the app's first-run watch. Closed fields, short values."""
    row: dict[str, Any] = {"time": _now()}
    for key in _WATCH_FIELDS:
        value = fields.get(key)
        if value is None:
            continue
        text = str(value)[:120]
        row[key] = text if _FIELD_RE.fullmatch(text) else "?"
    path = os.path.join(_dir(label), "watch.jsonl")
    line = json.dumps(row) + "\n"
    with _LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) + len(line) > WATCH_MAX_BYTES:
                os.replace(path, os.path.join(os.path.dirname(path), "watch.1.jsonl"))
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)


__all__ = ["get", "version", "put", "restore", "put_back", "mark_undo", "clear", "watch", "MAX_BYTES",
           "busy", "begin_bot_run", "end_bot_run", "BOT_RUN_MAX_S",
           "KEEP_VERSIONS", "WATCH_MAX_BYTES"]
