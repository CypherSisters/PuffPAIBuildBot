"""Which build this is, as a short stable name. Used to keep secrets per build.

WHY THIS EXISTS
---------------
Every build keeps its private files in its own state folder
(:func:`puffpai.config.get_state_dir`). Two secrets did not: the app API key's
password-store entry had one machine-wide name (``puffpai`` /
``PUFFPAI_API_KEY``), and the dashboard's pairing file lived in the code
copy's ``dashboard/data`` folder. So every build run from one code copy, or
started without the installer's key, shared one key and one pairing code, and
a device paired to one bot was paired to all of them.

The build's identity is its state folder. :func:`build_id` is a short hash of
that folder's CANONICAL path, worked out the same way on every run:

    sha256(normcase(canonical_state_dir())).hexdigest()[:16]

where :func:`canonical_state_dir` is the absolute path with links, junctions
and short (8.3) names resolved (``os.path.realpath``) and the Windows
long-path prefix removed, so one folder spelt several ways is still one build.

The id is computed HERE ONLY. :func:`record` writes it, with the canonical
folder, to ``<state>/build_id.json``; the dashboard
(``app/_lib/server/pairing.ts`` and ``scripts/show-pairing-code.js``) reads
that file and never computes an id of its own. It used to: Node's lower-casing
and Python's disagree on some folder names (``İstanbul``, ``ΣΑΣ``), and
long-path prefixes, short names and junctions gave one folder several ids.
A dashboard that finds no such file, or one written for
another folder, does not guess: it refuses the one decision the id is needed
for (see ``pairing.ts``) and says in plain words to start the bot once.

The hash is not a secret. It only names things: a password-store entry and
the owner of a code copy's old pairing file.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Optional

#: The file in the state folder that tells the dashboard which build it is.
ID_FILE = "build_id.json"

_LABEL_RE = re.compile(r"[^A-Za-z0-9_-]+")


def state_dir(state: Optional[str] = None) -> str:
    """The build's state folder, absolute. *state* overrides for tests."""
    if state:
        return os.path.abspath(state)
    from puffpai.config import get_state_dir
    return os.path.abspath(get_state_dir())


_LONG_UNC = "\\\\?\\UNC\\"   # the long-path prefix of a network path
_LONG = "\\\\?\\"            # the long-path prefix of a drive path


def _strip_long_prefix(path: str) -> str:
    if path.startswith(_LONG_UNC):
        return "\\\\" + path[len(_LONG_UNC):]
    if path.startswith(_LONG):
        return path[len(_LONG):]
    return path


def canonical_state_dir(state: Optional[str] = None) -> str:
    """The state folder's one true spelling: absolute, links and junctions
    and short (8.3) names resolved, no long-path prefix."""
    path = _strip_long_prefix(state_dir(state))
    try:
        path = os.path.realpath(path)
    except (OSError, ValueError):
        path = os.path.abspath(path)
    return _strip_long_prefix(path)


def _norm(path: str) -> str:
    return os.path.normcase(canonical_state_dir(path))


def build_id(state: Optional[str] = None) -> str:
    """16 hex characters naming this build. Stable for one state folder."""
    return hashlib.sha256(_norm(state_dir(state)).encode("utf-8")).hexdigest()[:16]


def record(state: Optional[str] = None) -> Optional[str]:
    """Write ``<state>/build_id.json`` for the dashboard. Returns the id.

    ``{"version": 1, "build": <id>, "state_dir": <canonical folder>}``,
    written atomically, and only when it is missing or says something else
    (a state folder copied from another build carries the other build's
    file; it is corrected here). Never raises: None when it could not be
    written (the dashboard then refuses what it needs the id for).
    """
    try:
        home = state_dir(state)
        if not os.path.isdir(home):
            # This may be the first thing to make the state folder (the
            # wizard). It holds secrets: owner-only, as get_state_dir makes it.
            os.makedirs(home, mode=0o700, exist_ok=True)
            try:
                os.chmod(home, 0o700)
            except OSError:  # pragma: no cover - Windows ignores the mode
                pass
        me = build_id(state)
        folder = canonical_state_dir(state)
        target = os.path.join(home, ID_FILE)
        wanted = {"version": 1, "build": me, "state_dir": folder}
        try:
            with open(target, "r", encoding="utf-8") as fh:
                if json.load(fh) == wanted:
                    return me
        except (OSError, ValueError):
            pass
        from puffpai import fsutil
        fsutil.atomic_write(target, json.dumps(wanted, indent=2, ensure_ascii=False))
        return me
    except Exception:  # never let naming the build stop it
        return None


def _label_of(folder: str) -> str:
    base = os.path.basename(folder.rstrip("\\/")) or "build"
    return (_LABEL_RE.sub("-", base).strip("-") or "build")[:24]


def build_label(state: Optional[str] = None) -> str:
    """The state folder's own name, made safe for an entry name.

    Shown next to the hash so an owner looking at the password store can tell
    which bot an entry belongs to. Taken from the CANONICAL folder
    (:func:`canonical_state_dir`), like the id: it used to be the name the
    folder was reached by, so one build started through a junction, a link
    or its short (8.3) name got a different password-store entry name, found
    no key under it and made a new one.
    """
    return _label_of(canonical_state_dir(state))


def old_labels(state: Optional[str] = None) -> list[str]:
    """Labels this build's entry may have been given before the label was
    canonical: the name the folder is reached by now, and (on Windows) its
    short 8.3 name. Never the canonical label itself. Not secrets."""
    labels = [_label_of(_strip_long_prefix(state_dir(state)))]
    short = _short_name(canonical_state_dir(state))
    if short:
        labels.append(_label_of(short))
    now = build_label(state)
    seen: list[str] = []
    for label in labels:
        if label != now and label not in seen:
            seen.append(label)
    return seen


def _short_name(path: str) -> str:
    """The Windows short (8.3) spelling of *path*, or '' when there is none."""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        get = ctypes.windll.kernel32.GetShortPathNameW  # type: ignore[attr-defined]
        get.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get.restype = wintypes.DWORD
        size = get(path, None, 0)
        if not size:
            return ""
        buf = ctypes.create_unicode_buffer(size)
        if not get(path, buf, size):
            return ""
        return buf.value
    except Exception:  # pragma: no cover - no short names on this volume
        return ""


def default_state_dir() -> str:
    """The per-user state folder a build gets when nothing names another."""
    from puffpai.config import _user_state_root
    return os.path.abspath(_user_state_root())


def is_default_build(state: Optional[str] = None) -> bool:
    """True when this build lives in the per-user default state folder.

    That is the one build that could have been using the old machine-wide
    password-store entry as ITS key: a build with its own state folder was
    either started by the installer (which gives it a key of its own) or is
    one of several builds that were wrongly sharing that entry.

    If the default folder cannot be worked out, the answer is no: fail
    closed, this build does not read the shared entry.
    """
    try:
        default = default_state_dir()
    except Exception:
        return False
    return _norm(state_dir(state)) == _norm(default)


__all__ = ["ID_FILE", "build_id", "build_label", "canonical_state_dir",
           "default_state_dir", "is_default_build", "old_labels", "record",
           "state_dir"]
