"""The pointer an installer leaves behind, so a bare `puffpai` finds its install.

The problem this exists for. ``install.ps1 -StateDir .\\state`` mints the key and
writes the launchers for a state dir beside the checkout, and then the documented
next step is ``puffpai setup`` - a bare command, in a shell that has none of that
environment. Without it, setup resolved the DEFAULT per-user state dir
(``%LOCALAPPDATA%\\PuffPAI``), wrote the config, the vault and the manifest there,
and ``run-backend.ps1`` then served the ``.\\state`` the owner asked for, which
had no setup in it. Two installs, one command apart, and nothing said so.

So the installers drop ``.puffpai-install.json`` at the checkout root::

    {"env_file": "<absolute path to buildbot.env>"}

and :func:`apply` loads that env file at the top of ``puffpai``'s entry point,
before anything resolves a config or a state dir.

Three rules keep it honest:

* **Explicit environment always wins.** A variable already set in the
  environment - by a launcher, by a test harness, by a person typing it - is
  never overwritten. The pointer is a default for a shell that was told nothing.
* **Only ``PUFFPAI_*`` names are read.** The env file lives next to a secret and
  is written by us, but a file is still a file: it does not get to set ``PATH``.
* **A missing pointer is silent; a broken one says so once.** No pointer is the
  normal case for a manual install. A pointer that names a file that is not
  there is a real fault and gets exactly one line, because an install that
  quietly ignores its own record is how this bug happened in the first place.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Optional

#: The file an installer writes at the checkout root. Gitignored: it holds one
#: machine's absolute paths and belongs to that machine only.
POINTER_NAME = ".puffpai-install.json"

#: Names :func:`apply` is allowed to set from an env file.
_NAME_RE = re.compile(r"^PUFFPAI_[A-Z0-9_]+$")

#: Said once per process, so a broken pointer does not narrate every command.
_warned = False

#: The env file this process actually loaded, for ``puffpai where`` to report.
_loaded: Optional[str] = None


def _warn(message: str) -> None:
    global _warned
    if _warned:
        return
    _warned = True
    print(message, file=sys.stderr)


def candidate_roots() -> list[str]:
    """The directories a pointer could sit in, best anchor first.

    Two anchors, because both are true of an install this project makes: the
    package is installed editable from ``<checkout>/src``, so ``puffpai.__file__``
    is inside the checkout; and the venv is ``<checkout>/venv``, so the running
    interpreter's prefix is one level down from it. A site-packages install
    matches neither, finds no pointer, and carries on - which is correct.
    """
    out: list[str] = []

    def _add(path: str) -> None:
        if not path:
            return
        full = os.path.abspath(path)
        if full not in out:
            out.append(full)

    try:
        from puffpai import __file__ as pkg_file
        # <checkout>/src/puffpai/__init__.py -> <checkout>
        _add(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(pkg_file)))))
    except Exception:  # pragma: no cover - import of ourselves cannot fail
        pass
    # <checkout>/venv -> <checkout>
    _add(os.path.dirname(os.path.abspath(sys.prefix)))
    return out


def find_pointer() -> Optional[str]:
    """Path of this install's pointer file, or None when there is not one."""
    for root in candidate_roots():
        path = os.path.join(root, POINTER_NAME)
        if os.path.isfile(path):
            return path
    return None


def read_pointer(path: str) -> Optional[str]:
    """The env file a pointer names, or None with one warning line.

    "Corrupt" covers every way this file can fail to mean anything: unreadable,
    not JSON, no ``env_file``, or an ``env_file`` that is not there.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        _warn(f"Ignoring {path}: it is not readable as JSON ({exc}). "
              f"Re-run the installer to rewrite it.")
        return None
    env_file = (data.get("env_file") or "").strip() if isinstance(data, dict) else ""
    if not env_file:
        _warn(f"Ignoring {path}: it names no env_file. "
              f"Re-run the installer to rewrite it.")
        return None
    env_file = os.path.abspath(env_file)
    if not os.path.isfile(env_file):
        _warn(f"Ignoring {path}: the env file it names ({env_file}) is not "
              f"there. Re-run the installer to rewrite it.")
        return None
    return env_file


def env_file() -> Optional[str]:
    """The env file this install's pointer names, or None."""
    pointer = find_pointer()
    return read_pointer(pointer) if pointer else None


def parse_env_file(path: str) -> dict[str, str]:
    """The ``PUFFPAI_*`` settings in a ``KEY=value`` file. Never raises."""
    out: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        _warn(f"Ignoring {path}: it could not be read ({exc}).")
        return out
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        name, _, value = text.partition("=")
        name = name.strip()
        if _NAME_RE.match(name):
            out[name] = value.strip()
    return out


def apply() -> Optional[str]:
    """Load this install's env file into the environment. Prints nothing.

    Returns the env file that was used, or None when there was no usable
    pointer. Idempotent, and never overwrites a variable that is already set.
    """
    global _loaded
    path = env_file()
    if not path:
        return None
    for name, value in parse_env_file(path).items():
        if (os.environ.get(name) or "").strip():
            continue  # explicit environment wins, always
        os.environ[name] = value
    _loaded = path
    return path


def loaded() -> Optional[str]:
    """The env file :func:`apply` loaded in this process, or None."""
    return _loaded


def write_pointer(root: str, env_path: str) -> str:
    """Write the pointer for a checkout. Used by the installers' Python side."""
    path = os.path.join(os.path.abspath(root), POINTER_NAME)
    body = {"env_file": os.path.abspath(env_path)}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(body, handle, indent=2)
        handle.write("\n")
    return path


__all__ = ["POINTER_NAME", "candidate_roots", "find_pointer", "read_pointer",
           "env_file", "parse_env_file", "apply", "loaded", "write_pointer"]
