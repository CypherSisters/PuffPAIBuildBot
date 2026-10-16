"""The free look lists this bot ships with, read from disk. No network.

Files are ``config/catalog_snapshots/looks_<list>_<date>.json`` (shipped) or
the same name under the state folder (a newer copy an owner saved). The
newest file per list wins. Every look in a file is validated and checked
again on load: a file is never trusted just because it is on disk.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Optional

from puffpai.logging_config import get_logger
from puffpai.looks import package as P

logger = get_logger(__name__)

SNAPSHOT_SCHEMA = "looks_snapshot/v1"
_NAME_RE = re.compile(r"^looks_(?P<list>[a-z]{2,20})_(?P<date>\d{4}-\d{2}-\d{2})\.json$")
MAX_FILE_BYTES = 2 * 1024 * 1024

_LOCK = threading.Lock()
_CACHE: dict[str, object] = {"key": None, "looks": [], "lists": {}}


def _dirs() -> list[str]:
    from puffpai.catalogs import live
    out = [live.shipped_snapshot_dir()]
    try:
        out.append(live.state_snapshot_dir())
    except Exception:  # noqa: BLE001 - no state folder yet
        pass
    return out


def snapshot_files() -> dict[str, str]:
    """{list id: newest file path}."""
    best: dict[str, tuple[str, str]] = {}
    for folder in _dirs():
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            m = _NAME_RE.match(name)
            if not m:
                continue
            lst, date = m.group("list"), m.group("date")
            if lst not in best or date > best[lst][0]:
                best[lst] = (date, os.path.join(folder, name))
    return {lst: path for lst, (_, path) in sorted(best.items())}


def _load_file(path: str) -> tuple[list[dict], dict]:
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return [], {}
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.warning("looks: could not read %s: %s", os.path.basename(path), exc)
        return [], {}
    if not isinstance(data, dict) or data.get("schema") != SNAPSHOT_SCHEMA:
        return [], {}
    looks: list[dict] = []
    for raw in data.get("looks") or []:
        try:
            pkg = P.check(P.validate({k: v for k, v in raw.items()
                                      if k not in ("checks", "adjusted")}))
        except (P.LookError, AttributeError, TypeError, ValueError):
            continue
        if pkg["from"].get("list") != data.get("list"):
            continue
        looks.append(pkg)
    info = {"list": data.get("list"), "license": data.get("license"),
            "taken_at": data.get("taken_at"), "count": len(looks)}
    return looks, info


def candidates() -> list[dict]:
    """Every look on the shipped (or newer saved) lists, checked."""
    files = snapshot_files()
    key = tuple((p, os.path.getmtime(p)) for p in files.values() if os.path.exists(p))
    with _LOCK:
        if _CACHE["key"] == key:
            return [dict(x) for x in _CACHE["looks"]]  # type: ignore[union-attr]
        looks: list[dict] = []
        lists: dict[str, dict] = {}
        for lst, path in files.items():
            got, info = _load_file(path)
            looks.extend(got)
            if info:
                lists[lst] = info
        _CACHE.update(key=key, looks=looks, lists=lists)
        return [dict(x) for x in looks]


def lists() -> dict[str, dict]:
    candidates()
    with _LOCK:
        return dict(_CACHE["lists"])  # type: ignore[arg-type]


def get(look_id: str) -> Optional[dict]:
    for pkg in candidates():
        if pkg["id"] == look_id:
            return pkg
    return None


def clear_cache() -> None:
    with _LOCK:
        _CACHE.update(key=None, looks=[], lists={})


__all__ = ["SNAPSHOT_SCHEMA", "candidates", "clear_cache", "get", "lists",
           "snapshot_files"]
