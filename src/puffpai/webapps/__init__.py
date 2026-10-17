"""Browser apps found on GitHub, each locked on its own address.

A web app Toolsmith integrates is plain files (HTML, script, styles). It is
never imported or run by Python. It is served by :mod:`puffpai.webapps.host`
at its own never-reused address ``http://<id>.localhost:<port>/``, opens only
inside the owner's dashboard frame or the bot's own hidden browser
(:mod:`puffpai.webapps.runner_host`), and is driven by the bot's AI only
through actions compiled from the app itself (:mod:`puffpai.webapps.compile_form`)
and checked by Drawbridge (:mod:`puffpai.webapps.recipe_check`).

This module holds the few facts every part shares: the id shapes, where
things live, and the optional-import helper for the other parts' modules.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import importlib
import os
import re
import sys
from typing import Any, Optional

#: An integrated app's id: ``<slug of name, <=30>-<8 random base32 lowercase>``.
#: Issued by the caller (``integrate.records.new_id``); this module only checks it.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}$")

#: A throwaway check copy's label: its own address, empty storage, every outside
#: host off. It can never equal an app id: an id ends in ``-`` + 8 characters and
#: a check label has exactly one ``-``, followed by 16.
CHECK_RE = re.compile(r"^chk-[a-z2-7]{16}$")

DEFAULT_PORT = 4271


def valid_app_id(value: Any) -> bool:
    return isinstance(value, str) and bool(ID_RE.fullmatch(value))


def is_link(path: str) -> bool:
    """A symbolic link or a Windows directory junction (never followed).
    Read from the entry itself: ``os.path.isjunction`` is not in Python 3.11."""
    from puffpai.security import owner_folders
    return owner_folders.is_link(path)


def walk(root: str):
    """``os.walk`` that never enters a link or a junction (sorted, stable)."""
    for dirpath, dirnames, files in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not is_link(os.path.join(dirpath, d)))
        yield dirpath, dirnames, sorted(f for f in files if not is_link(os.path.join(dirpath, f)))


def valid_label(value: Any) -> bool:
    """An address label: an app id or a check copy label."""
    return isinstance(value, str) and bool(ID_RE.fullmatch(value) or CHECK_RE.fullmatch(value))


def _optional(modname: str):
    """The module, or None when it is not in this build yet (pinned, I-10)."""
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


# ---------------------------------------------------------------------------
# where things live
# ---------------------------------------------------------------------------

def state_root() -> str:
    """``<state>/webapps``: the mirror, the watch log, per-app records."""
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "webapps")


def app_state_dir(app_id: str) -> str:
    if not valid_app_id(app_id):
        raise ValueError("not an app id")
    return os.path.join(state_root(), app_id)


def check_state_dir(label: str) -> str:
    if not (isinstance(label, str) and CHECK_RE.fullmatch(label)):
        raise ValueError("not a check copy label")
    return os.path.join(state_root(), "_checks", label)


def label_state_dir(label: str) -> str:
    return check_state_dir(label) if CHECK_RE.fullmatch(label or "") else app_state_dir(label)


def apps_state_dir(app_id: str) -> str:
    """``<state>/apps/<id>``: the app manifest and its compiled actions."""
    if not valid_app_id(app_id):
        raise ValueError("not an app id")
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "apps", app_id)


def files_root() -> str:
    """Where downloaded app files live (outside state, keys and vault).

    ``safe_archive.extraction_root()`` when it is in this build;
    otherwise the same rule inline: env ``PUFFPAI_APPS_ROOT``, else
    ``%LOCALAPPDATA%\\PuffPAI\\apps`` (``$XDG_DATA_HOME/puffpai/apps`` elsewhere).
    """
    sa = _optional("puffpai.security.safe_archive")
    if sa is not None and hasattr(sa, "extraction_root"):
        return os.path.abspath(sa.extraction_root())
    env = os.environ.get("PUFFPAI_APPS_ROOT")
    if env:
        return os.path.abspath(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "PuffPAI", "apps")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "puffpai", "apps")


def web_dir(app_id: str) -> str:
    """The app's served files: ``<apps root>/<id>/web``."""
    if not valid_app_id(app_id):
        raise ValueError("not an app id")
    return os.path.join(files_root(), app_id, "web")


# ---------------------------------------------------------------------------
# ports
# ---------------------------------------------------------------------------

def _raw_cfg(cfg: Any = None) -> dict:
    if cfg is None:
        try:
            from puffpai.config import get_config
            cfg = get_config()
        except Exception:  # noqa: BLE001 - defaults are fine
            return {}
    raw = getattr(cfg, "_raw", None)
    return raw if isinstance(raw, dict) else {}


def _port_value(value: Any) -> Optional[int]:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return None
    return port if 1024 <= port <= 65535 else None


def configured_port(cfg: Any = None) -> int:
    """``webapps.port`` (default 4271); env ``PUFFPAI_WEBAPPS_PORT`` wins."""
    env = _port_value(os.environ.get("PUFFPAI_WEBAPPS_PORT"))
    if env:
        return env
    block = _raw_cfg(cfg).get("webapps")
    if isinstance(block, dict):
        port = _port_value(block.get("port"))
        if port:
            return port
    return DEFAULT_PORT


def dashboard_port(cfg: Any = None) -> int:
    env = _port_value(os.environ.get("PUFFPAI_DASHBOARD_PORT"))
    if env:
        return env
    try:
        if cfg is None:
            from puffpai.config import get_config
            cfg = get_config()
        port = _port_value(getattr(getattr(cfg, "dashboard", None), "port", None))
        if port:
            return port
    except Exception:  # noqa: BLE001
        pass
    return 3003


__all__ = ["ID_RE", "CHECK_RE", "valid_app_id", "valid_label", "state_root",
           "app_state_dir", "check_state_dir", "apps_state_dir", "files_root",
           "web_dir", "configured_port", "dashboard_port", "DEFAULT_PORT"]
