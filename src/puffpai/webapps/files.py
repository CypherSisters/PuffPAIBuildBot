"""A folder the owner gives a browser app, and serving it to the bot's hidden browser only.

An app may be given a folder she picks (her pictures,
say), never the vault, the vault inbox, keys, pairing secrets or another
app's data. The rules for WHICH folder may be given are the security part's, in one
place (:func:`puffpai.security.owner_folders.check`); this module keeps the
grant and serves it.

* **Giving** (:func:`give`): the path must pass ``owner_folders.check`` for
  the mode; the owner's yes is a device-bound, single-use ``folder_give``
  ticket for exactly this app, this real path and this mode; the path is
  checked again after the yes and must still be the same real folder. The
  grant is kept in ``<state>/webapps/<id>/folder.json``. Taking it back
  (:func:`take_back`) needs nothing.
* **Serving** (``/__puffpai/files/<path>``, :func:`handle`): the host lets
  only a request carrying the runner's per-boot secret reach here (never the
  owner's frame, never the app's own script on her screen). On EVERY request
  the grant is checked again with ``owner_folders.check``; every step of the
  path is refused when it is a link or junction (a folder that contains a
  junction to the vault is not followed into it), when its name looks like a
  key (``*key*``, ``*secret*``, ``*token*``, ``.env``, ``*.pem`` and a few
  more), or when it starts with a dot; the final real path must still be
  inside the granted folder. Read-only, whatever the mode. A folder answers
  with a list of its names; a file with its bytes (at most 25 MB).

When ``owner_folders`` is not in this build, no folder can be given
("not in this build yet") and nothing is served.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import re
import stat
import time
from typing import Any, Optional

from puffpai import webapps
from puffpai.webapps import wording

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_LIST = 500
ACTION = "folder_give"
#: Names never shown or served, whatever folder they are in.
_KEYISH = re.compile(r"key|secret|token|password|passwd|credential|^\.env|\.pem$|\.pfx$|\.p12$"
                     r"|\.kdbx$|^id_(rsa|ed25519|ecdsa|dsa)", re.I)
_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
          ".webp": "image/webp", ".avif": "image/avif", ".heic": "image/heic", ".bmp": "image/bmp",
          ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
          ".csv": "text/csv; charset=utf-8", ".json": "application/json", ".pdf": "application/pdf",
          ".mp3": "audio/mpeg", ".wav": "audio/wav", ".mp4": "video/mp4", ".webm": "video/webm"}


def _folders():
    of = webapps._optional("puffpai.security.owner_folders")
    return of if of is not None and callable(getattr(of, "check", None)) else None


def keyish(name: str) -> bool:
    return bool(_KEYISH.search(name or "")) or (name or "").startswith(".")


def _grant_path(app_id: str) -> str:
    return os.path.join(webapps.app_state_dir(app_id), "folder.json")


def _read_grant(app_id: str) -> Optional[dict]:
    try:
        with open(_grant_path(app_id), encoding="utf-8") as fh:
            g = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(g, dict) or g.get("mode") not in ("ro", "rw") or not isinstance(g.get("path"), str):
        return None
    return g


def granted(app_id: str) -> Optional[dict]:
    """The app's folder grant, checked again now; None when there is none or it no longer passes."""
    if not webapps.valid_app_id(app_id):
        return None
    of = _folders()
    g = _read_grant(app_id)
    if of is None or g is None:
        return None
    real, _why = of.check(g["path"], g["mode"])
    if not real or os.path.normcase(os.path.realpath(real)) != os.path.normcase(g["path"]):
        return None
    return {"path": g["path"], "mode": g["mode"]}


def granted_name(app_id: str) -> Optional[str]:
    """The given folder's own name, for the owner's words (never the full path)."""
    g = granted(app_id)
    if g is None:
        return None
    return os.path.basename(g["path"].rstrip("\\/")) or g["path"]


def _app_name(app_id: str) -> str:
    from puffpai.webapps import host
    return str((host.read_record(app_id) or {}).get("name") or app_id)


def question_lines(app_id: str, real: str, mode: str) -> list[str]:
    folder = os.path.basename(real.rstrip("\\/")) or real
    key = wording.FOLDER_QUESTION_RW if mode == "rw" else wording.FOLDER_QUESTION
    return [key.format(app=_app_name(app_id), folder=folder), wording.FOLDER_NEVER_VAULT]


def give(app_id: str, path: str, mode: str, *, device: str, ticket: Optional[str] = None) -> dict:
    """Give ``app_id`` a folder. Without ``ticket``: the question and a ticket to
    agree with; with it: the grant (after checking the path again)."""
    of = _folders()
    if of is None:
        return {"ok": False, "refused": "not_in_this_build", "message": wording.FOLDER_NOT_IN_BUILD}
    if not webapps.valid_app_id(app_id):
        return {"ok": False, "refused": "unknown_app", "message": wording.NOT_FOUND}
    if mode not in ("ro", "rw"):
        return {"ok": False, "refused": "bad_mode", "message": wording.REFUSED}
    real, why = of.check(path, mode)
    if not real:
        return {"ok": False, "refused": "folder", "message": str(why or wording.REFUSED)}
    real = os.path.realpath(real)
    from puffpai.security import consent_tickets
    try:
        target = consent_tickets.target_folder(app_id, real, mode)
    except ValueError:
        return {"ok": False, "refused": "folder", "message": wording.REFUSED}
    lines = question_lines(app_id, real, mode)
    if not ticket:
        try:
            secret = consent_tickets.mint(ACTION, target, " ".join(lines), device)
        except consent_tickets.TicketRefused as exc:
            return {"ok": False, "refused": exc.code, "message": exc.message}
        return {"ok": False, "needs_yes": True, "ticket": secret, "words": lines,
                "buttons": [wording.GIVE_FOLDER, wording.NO]}
    ok, verdict = consent_tickets.redeem(ticket, device, ACTION, target)
    if not ok:
        return {"ok": False, "refused": verdict, "message": consent_tickets.refusal_words(verdict)}
    again, _why = of.check(path, mode)
    if not again or os.path.normcase(os.path.realpath(again)) != os.path.normcase(real):
        return {"ok": False, "refused": "folder_moved", "message": wording.REFUSED}
    rec = {"path": real, "mode": mode, "by": "device" if device.startswith("device:") else "terminal",
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    p = _grant_path(app_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)
    os.replace(tmp, p)
    return {"ok": True, "line": wording.FOLDER_GIVEN.format(app=_app_name(app_id),
                                                           folder=os.path.basename(real) or real)}


def take_back(app_id: str) -> dict:
    if not webapps.valid_app_id(app_id):
        return {"ok": False, "refused": "unknown_app", "message": wording.NOT_FOUND}
    try:
        os.remove(_grant_path(app_id))
    except FileNotFoundError:
        pass
    return {"ok": True, "line": wording.FOLDER_TAKEN_BACK.format(app=_app_name(app_id))}


def _is_link(of: Any, path: str) -> bool:
    fn = getattr(of, "is_link", None)
    return bool(fn(path)) if callable(fn) else webapps.is_link(path)


def resolve(app_id: str, raw_path: str) -> tuple[Optional[str], str]:
    """(real path, "file"|"folder") inside the app's granted folder, or (None, why)."""
    of = _folders()
    g = granted(app_id)
    if of is None or g is None:
        return None, "no_folder"
    from puffpai.webapps import host
    parts = host.safe_rel_path(raw_path or "/")
    if parts is None:
        return None, "bad_path"
    root = g["path"]
    cur = root
    for part in parts:
        if keyish(part):
            return None, "hidden"
        cur = os.path.join(cur, part)
        if _is_link(of, cur):
            return None, "link"            # never followed, not even inside the folder
        try:
            st = os.lstat(cur)
        except OSError:
            return None, "missing"
        if stat.S_ISLNK(st.st_mode):
            return None, "link"
    real = os.path.realpath(cur)
    r = os.path.normcase(os.path.realpath(root))
    if not (os.path.normcase(real) == r or os.path.normcase(real).startswith(r.rstrip("\\/") + os.sep)):
        return None, "outside"
    if os.path.isdir(real):
        return real, "folder"
    if os.path.isfile(real):
        return real, "file"
    return None, "missing"


def listing(folder: str) -> dict:
    of = _folders()
    names_f, names_d = [], []
    try:
        with os.scandir(folder) as it:
            entries = sorted(it, key=lambda e: e.name.lower())
    except OSError:
        entries = []
    for e in entries:
        if len(names_f) + len(names_d) >= MAX_LIST:
            break
        if keyish(e.name) or _is_link(of, e.path):
            continue
        try:
            if e.is_dir(follow_symlinks=False):
                names_d.append(e.name)
            elif e.is_file(follow_symlinks=False):
                names_f.append({"name": e.name, "bytes": e.stat(follow_symlinks=False).st_size})
        except OSError:
            continue
    return {"folders": names_d, "files": names_f}


def handle(handler: Any, label: str) -> None:
    """``GET /__puffpai/files/<path>`` (the host has checked the runner secret)."""
    if not webapps.valid_app_id(label):
        return handler._json(404, {"error": "not_found"})
    raw = (handler.path or "").split("?", 1)[0].split("#", 1)[0]
    rel = raw[len("/__puffpai/files"):] or "/"
    real, kind = resolve(label, rel)
    if real is None:
        return handler._json(404, {"error": kind})
    if kind == "folder":
        return handler._json(200, listing(real))
    try:
        size = os.path.getsize(real)
        if size > MAX_FILE_BYTES:
            return handler._json(413, {"error": "too_big"})
        with open(real, "rb") as fh:
            data = fh.read(MAX_FILE_BYTES + 1)
    except OSError:
        return handler._json(404, {"error": "missing"})
    ctype = _TYPES.get(os.path.splitext(real)[1].lower(), "application/octet-stream")
    return handler._send(200, data, ctype, {"Cache-Control": "no-store"})


__all__ = ["give", "take_back", "granted", "granted_name", "resolve", "listing", "handle",
           "keyish", "ACTION"]
