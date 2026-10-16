"""Pictures, sound and video a thing made, for its page's gallery and player.

A skill's commands run in its own folder (:mod:`puffpai.brain_commands`), and
what they make (a drawing, a recording, a short video) is moved to the bot's
work folder, ``<state>/workspace/made/<skill>``. Its page lists the media
files there, and (from before that move) those in the skill's folder that
were NOT part of the install (the lock file pins every installed file), and
plays them.

Read-only and confined: a file is served only when its name is on that list,
its real path is inside the thing's folder (no ``..``, no links out, no
absolute names) and its kind is a picture, sound or video this module knows.
SVG is never served (it can carry script).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
from typing import Any, Optional

#: Extension -> (kind, mimetype). Raster pictures, common sound and video.
TYPES = {
    ".png": ("image", "image/png"), ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"), ".gif": ("image", "image/gif"),
    ".webp": ("image", "image/webp"),
    ".mp3": ("audio", "audio/mpeg"), ".wav": ("audio", "audio/wav"),
    ".ogg": ("audio", "audio/ogg"), ".m4a": ("audio", "audio/mp4"),
    ".mp4": ("video", "video/mp4"), ".webm": ("video", "video/webm"),
}
MAX_FILES = 40
MAX_DEPTH = 4
#: Bigger than this is not served (a page player, not a file share).
MAX_BYTES = 200 * 1024 * 1024


def folder_of(thing: Optional[dict]) -> Optional[str]:
    """The folder a thing works in, or None (only skills have one today)."""
    if not thing or thing.get("kind") != "skill":
        return None
    from puffpai import skills
    root = os.path.realpath(skills.skills_dir())
    folder = os.path.realpath(os.path.join(root, str(thing.get("ref") or "")))
    if not folder.startswith(root + os.sep) or not os.path.isdir(folder):
        return None
    return folder


def made_folder_of(thing: Optional[dict]) -> Optional[str]:
    """``<state>/workspace/made/<skill>`` for a skill, when it exists."""
    if folder_of(thing) is None:
        return None
    from puffpai import brain_commands
    folder = os.path.realpath(brain_commands.made_dir(str(thing.get("ref") or "")))
    return folder if os.path.isdir(folder) else None


def _roots(thing: Optional[dict]) -> list[tuple[str, bool]]:
    """``[(folder, skip installed files)]``: the made folder first."""
    out = []
    made = made_folder_of(thing)
    if made:
        out.append((made, False))
    folder = folder_of(thing)
    if folder:
        out.append((folder, True))
    return out


def _installed_files(thing: dict) -> set[str]:
    try:
        from puffpai.finding import install
        lock = install._read_lock()  # noqa: SLF001 - the one record of installed files
    except Exception:  # noqa: BLE001
        return set()
    files = (lock.get(str(thing.get("ref") or "")) or {}).get("files") or {}
    return {str(rel).replace("\\", "/").lower() for rel in files}


def _inside(path: str, folder: str) -> bool:
    real = os.path.realpath(path)
    return real.startswith(folder + os.sep)


def listing(thing: Optional[dict]) -> list[dict[str, Any]]:
    """``[{name, kind, size, modified}]``, newest first: media files in the
    thing's folder that it made (not installed with it)."""
    roots = _roots(thing)
    if not roots:
        return []
    installed = _installed_files(thing)
    found: list[dict[str, Any]] = []
    for folder, skip_installed in roots:
        _walk(folder, installed if skip_installed else set(), found)
    seen: set = set()
    found = [r for r in found if not (r["name"] in seen or seen.add(r["name"]))]
    found.sort(key=lambda row: row["modified"], reverse=True)
    return found[:MAX_FILES]


def _walk(folder: str, installed: set, found: list) -> None:
    """Add *folder*'s media files (not in *installed*) to *found*."""
    for here, dirs, files in os.walk(folder):
        depth = os.path.relpath(here, folder).count(os.sep)
        dirs[:] = [d for d in dirs if not d.startswith(".") and depth < MAX_DEPTH
                   and not os.path.islink(os.path.join(here, d))]
        for name in files:
            kind = TYPES.get(os.path.splitext(name)[1].lower())
            path = os.path.join(here, name)
            if kind is None or name.startswith(".") or os.path.islink(path) \
                    or not _inside(path, folder):
                continue
            rel = os.path.relpath(path, folder).replace(os.sep, "/")
            if rel.lower() in installed:
                continue
            try:
                stat = os.stat(path)
            except OSError:
                continue
            if stat.st_size > MAX_BYTES:
                continue
            found.append({"name": rel, "kind": kind[0], "size": stat.st_size,
                          "modified": int(stat.st_mtime)})


def file_for(thing: Optional[dict], name: Any) -> Optional[tuple[str, str]]:
    """``(full path, mimetype)`` for one listed file, or None."""
    rel = str(name or "")
    if not rel or "\x00" in rel or "\\" in rel or rel.startswith("/") or ":" in rel \
            or any(part in ("", ".", "..") for part in rel.split("/")):
        return None
    if rel not in {row["name"] for row in listing(thing)}:
        return None
    for folder, _skip in _roots(thing):
        path = os.path.join(folder, *rel.split("/"))
        if os.path.islink(path) or not _inside(path, folder) or not os.path.isfile(path):
            continue
        kind = TYPES.get(os.path.splitext(path)[1].lower())
        return (os.path.realpath(path), kind[1]) if kind else None
    return None


__all__ = ["MAX_FILES", "TYPES", "file_for", "folder_of", "listing", "made_folder_of"]
