"""Your first apps: the apps BuildBot ships with, off until the owner turns them on.

A shipped app whose manifest says ``"first_app": true`` (AlarmFlow, Calendar,
FreshTable, FitFam and Trivia today) is on disk in every bot, and turned on per
bot. A new bot starts with none on. The owner turns one on from the Apps page
("Your first apps"), or the build plan lists the ones that fit her goal on its
one Yes ("Turn on: FitFam (one of your first apps)") and her Yes turns them on.
Turning one on is local only: nothing is downloaded and nothing reaches outside.

An app that is off is left out of :func:`puffpai.app_registry.load_manifests`
(so it is not in the sidebar, the home tiles, the brain's tools, the chat's
fast paths or the router's words), and its data routes answer "turned off".
Its saved data stays where it is. An app in the owner's own folder
(``<state>/apps``) is never off.

The on-set is one small file, ``<state>/apps_on.json``: ``{"on": [ids]}``.

**Bots made before this change keep what they use.** A bot with no file yet
counts as on each first app it already uses: one with saved data (its app_data
document), one whose page the build already shows (a surface on the build's
trimmed page list), or one she put on her home page. A new bot has none of
these, so it starts with an empty set. Reading never writes; the set is kept in
the file once (:func:`settle`) when the backend starts and before a build
record is written (``build_manifest.write_manifest``), so a new bot's own plan
never reads as "already used".

The five apps are planned as independent marketplace apps; this per-app switch
is the step before that.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

STATE_NAME = "apps_on.json"
#: Said after an app's name wherever the plan lists it.
FIRST_APP_WORDS = "one of your first apps"
#: The plan's line for one app, and the turned-off answer, worded once.
TURN_ON_LINE = "Turn on: {name} (" + FIRST_APP_WORDS + ")"
TURNED_OFF_LINE = "{name} is turned off. Turn it on from the Apps page."
NOT_FIRST = "That is not one of your first apps."

_lock = threading.Lock()


# ── the on-set ───────────────────────────────────────────────────────────────

def _path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), STATE_NAME)


def _read() -> Optional[list[str]]:
    """The saved on-set, or None when this bot has no file yet."""
    path = _path()
    if not os.path.isfile(path):
        return None
    from puffpai import fsutil
    data = fsutil.read_json(path, default={})
    raw = data.get("on") if isinstance(data, dict) else None
    return [str(x) for x in raw if isinstance(x, str)] if isinstance(raw, list) else []


class NotSaved(OSError):
    """The on-set file could not be written, so nothing changed."""


NOT_SAVED = "That could not be saved on this computer, so nothing changed. Try again."


def _write(on: list[str]) -> bool:
    from puffpai import fsutil
    try:
        fsutil.atomic_write_json(_path(), {"on": sorted(set(on))})
        return True
    except OSError as exc:
        logger.warning("first apps: the on-set was not saved (%s)", type(exc).__name__)
        return False


def _build_pages() -> set[str]:
    """The surfaces a TRIMMED build shows. An untrimmed build (no page list)
    names none: showing every page is not the same as using one app."""
    try:
        from puffpai import build_manifest
        record = build_manifest.read_manifest()
    except Exception:  # noqa: BLE001
        return set()
    surfaces = (record or {}).get("surfaces") if isinstance(record, dict) else None
    allow = surfaces.get("allowlist") if isinstance(surfaces, dict) else None
    return {str(s) for s in allow} if isinstance(allow, list) else set()


def _on_home() -> set[str]:
    """Thing ids she put on her home page (``<state>/things/home.json``)."""
    try:
        from puffpai import fsutil
        from puffpai.config import get_state_dir
        data = fsutil.read_json(os.path.join(get_state_dir(), "things", "home.json"),
                                default={})
    except Exception:  # noqa: BLE001
        return set()
    return {str(k) for k, v in data.items() if v is True} if isinstance(data, dict) else set()


def _has_data(app_id: str) -> bool:
    try:
        from puffpai import app_data, app_registry
        path = app_registry.data_path(app_id, app_data.data_dir_for(app_id))
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except Exception:  # noqa: BLE001
        return False


def in_use(manifest: dict[str, Any], pages: Optional[set[str]] = None,
           home: Optional[set[str]] = None) -> bool:
    """Does this bot already use this app: saved data, its page on the build,
    or its tile on her home page?"""
    app_id = str(manifest.get("id") or "")
    if not app_id:
        return False
    if _has_data(app_id):
        return True
    home = _on_home() if home is None else home
    if f"builtin-{app_id}" in home:
        return True
    pages = _build_pages() if pages is None else pages
    return any(str(s) in pages for s in manifest.get("surfaces") or [])


def _migrate(firsts: list[dict[str, Any]]) -> list[str]:
    """The on-set for a bot with no file yet: the first apps it already uses.
    Reading never writes; :func:`settle` and :func:`set_on` keep it."""
    pages, home = _build_pages(), _on_home()
    return [str(m["id"]) for m in firsts if in_use(m, pages, home)]


def _first_manifests() -> list[dict[str, Any]]:
    from puffpai import app_registry
    return [m for m in app_registry.all_manifests() if app_registry.is_first_app(m)]


def on_ids(firsts: Optional[list[dict[str, Any]]] = None) -> set[str]:
    """The first apps this bot has turned on. A bot with no file yet: the
    ones it already uses (:func:`_migrate`), read, not written."""
    with _lock:
        saved = _read()
        if saved is None:
            saved = _migrate(firsts if firsts is not None else _first_manifests())
    return set(saved)


def settle() -> None:
    """Keep this bot's on-set in its file, once: the backend calls this when it
    starts, and ``build_manifest.write_manifest`` before it writes a build, so
    a new bot's own plan is never mistaken for an old bot's use. Never raises."""
    try:
        with _lock:
            if _read() is not None:
                return
            on = _migrate(_first_manifests())
            _write(on)
        if on:
            logger.info("first apps: kept on for this bot: %s", ", ".join(on))
    except Exception as exc:  # noqa: BLE001
        logger.info("first apps: not settled (%s)", type(exc).__name__)


def only_on(manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*manifests* without the first apps this bot has not turned on."""
    from puffpai import app_registry
    firsts = [m for m in manifests if app_registry.is_first_app(m)]
    if not firsts:
        return manifests
    try:
        on = on_ids(firsts)
    except Exception as exc:  # noqa: BLE001 - an unreadable set is an empty one
        logger.warning("first apps: the on-set could not be read (%s)", type(exc).__name__)
        on = set()
    return [m for m in manifests if not app_registry.is_first_app(m) or str(m["id"]) in on]


def manifest_of(app_id: str) -> Optional[dict[str, Any]]:
    """A first app's manifest, on or off, or None."""
    return next((m for m in _first_manifests() if str(m.get("id")) == str(app_id)), None)


def is_off(app_id: str) -> bool:
    """True for a first app this bot has not turned on."""
    manifest = manifest_of(app_id)
    return manifest is not None and str(app_id) not in on_ids()


def turned_off_line(app_id: str) -> str:
    manifest = manifest_of(app_id) or {}
    return TURNED_OFF_LINE.format(name=str(manifest.get("name") or app_id))


# ── the Apps page ────────────────────────────────────────────────────────────

def listing() -> list[dict[str, Any]]:
    """``[{id, name, blurb, route, icon, on}]``, in the apps' own order."""
    firsts = _first_manifests()
    on = on_ids(firsts)
    out = []
    for m in firsts:
        raw_ui = m.get("ui")
        ui: dict[str, Any] = raw_ui if isinstance(raw_ui, dict) else {}
        out.append({"id": str(m["id"]), "name": str(m.get("name") or m["id"])[:64],
                    "blurb": str(m.get("blurb") or "")[:200],
                    "route": str(ui.get("route") or ""), "icon": str(ui.get("icon") or "")[:64],
                    "on": str(m["id"]) in on})
    return out


def _show_pages(manifest: dict[str, Any]) -> None:
    """On a trimmed build, the app's pages join the build's page list (the
    same step a built-in app put on the home page takes), so its page shows."""
    surfaces = [str(s) for s in manifest.get("surfaces") or [] if isinstance(s, str)]
    if not surfaces:
        return
    try:
        from puffpai import persona_scaffold
        name = str(manifest.get("name") or manifest.get("id"))
        persona_scaffold._update_build_surfaces(  # noqa: SLF001
            add={s: f"{name}, {FIRST_APP_WORDS}, turned on." for s in surfaces})
    except Exception as exc:  # noqa: BLE001
        logger.info("first apps: page list not changed (%s)", type(exc).__name__)


def _refresh() -> None:
    """Everything that reads the installed apps sees the change now."""
    from puffpai import app_registry
    app_registry.rescan()
    try:
        from puffpai import router
        router.reload_app_words()
    except Exception as exc:  # noqa: BLE001
        logger.info("first apps: router words not reloaded (%s)", type(exc).__name__)


def set_on(app_ids: list[str], on: bool = True) -> list[str]:
    """Turn first apps on (or off). Returns the ids that changed. Raises
    KeyError for an id that is not a first app, and :class:`NotSaved` when the
    file could not be written (nothing is changed either way)."""
    by_id = {str(m["id"]): m for m in _first_manifests()}
    wanted = [str(i) for i in app_ids]
    unknown = [i for i in wanted if i not in by_id]
    if unknown:
        raise KeyError(unknown[0])
    with _lock:
        saved = _read()
        current = set(saved if saved is not None else _migrate(list(by_id.values())))
        changed = [i for i in wanted if (i in current) != on]
        if changed:
            current = current | set(changed) if on else current - set(changed)
            if not _write(sorted(current)):
                raise NotSaved(NOT_SAVED)
    if changed:
        if on:
            for i in changed:
                _show_pages(by_id[i])
        _refresh()
    return changed


# ── the build plan ───────────────────────────────────────────────────────────

def suggest(goal: str) -> list[dict[str, Any]]:
    """The first apps that are off and fit *goal*, by the build interview's
    own matching (catalog tags; no model, no network). ``[{id, name, line}]``."""
    from puffpai import build_interview
    firsts = {str(m["id"]): m for m in _first_manifests()}
    if not firsts or not str(goal or "").strip():
        return []
    on = on_ids(list(firsts.values()))
    try:
        proposal = build_interview.propose(goal)
    except Exception:  # noqa: BLE001 - no suggestion is a fine answer
        return []
    out: list[dict[str, Any]] = []
    for entry in (proposal.get("matched") or {}).get("app") or []:
        app_id = str(entry.get("id") or "").split(":", 1)[-1]
        if app_id in firsts and app_id not in on and app_id not in {o["id"] for o in out}:
            name = str(firsts[app_id].get("name") or app_id)
            out.append({"id": app_id, "name": name, "line": TURN_ON_LINE.format(name=name)})
    return out


def plan(build: dict, goal: str) -> None:
    """Put the first apps that fit the goal on the plan's one Yes
    (``build["first_apps"]``). None fit: nothing is listed."""
    try:
        build["first_apps"] = suggest(goal)
    except Exception as exc:  # noqa: BLE001 - a plan never fails over this
        logger.info("first apps: no suggestion (%s)", type(exc).__name__)
        build["first_apps"] = []


def listed(build: dict) -> list[dict[str, Any]]:
    return [i for i in build.get("first_apps") or []
            if isinstance(i, dict) and isinstance(i.get("id"), str)]


def lines(build: dict) -> list[str]:
    """The plan's lines for the first apps it turns on, as shown."""
    return [str(i.get("line") or TURN_ON_LINE.format(name=i.get("name") or i["id"]))
            for i in listed(build)]


def public(build: dict) -> list[dict[str, Any]]:
    return [{"id": i["id"], "name": str(i.get("name") or i["id"]),
             "line": str(i.get("line") or "")} for i in listed(build)]


def turn_on_listed(build: dict) -> list[str]:
    """After her Yes: turn on the first apps the plan listed. Local only."""
    ids = [i["id"] for i in listed(build)]
    if not ids:
        return []
    try:
        return set_on([i for i in ids if manifest_of(i) is not None], True)
    except Exception as exc:  # noqa: BLE001 - the Yes stands; the page can do it
        logger.warning("first apps: not turned on after the Yes (%s)", type(exc).__name__)
        return []


__all__ = ["FIRST_APP_WORDS", "STATE_NAME", "TURNED_OFF_LINE", "TURN_ON_LINE", "in_use",
           "is_off", "listed", "lines", "listing", "manifest_of", "on_ids", "only_on", "plan",
           "public", "set_on", "settle", "suggest", "turn_on_listed", "turned_off_line"]
