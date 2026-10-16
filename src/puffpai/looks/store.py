"""The look this build uses, what was found, and one-step undo.

Files (per build, under the state folder)::

    looks/current.json   {"look": <package> | null, "background": str | null,
                          "chosen_background": str | null, "since": iso}
    looks/history.json   [{"look": <package> | null, "background": ...,
                           "chosen_background": ..., "at": iso}, ...] (last 10)

A look carries its own background (package ``background``); the row's
``background`` is used only with the bot's own look (``look`` null), where
there is no package to carry it. ``chosen_background`` is the background the
owner picked herself (so "Keep it" brings the clouds back). Every
look she then uses wears it, unless it would make that look too hard to read.
Undo brings back all three; going back to the bot's own look forgets it.
    looks/found.json     {"words", "at", "order": [ids], "pool": [<package>]}

A look can be used only if this code can name where it came from: a free look
list shipped with the bot, a look found for the owner's words (the last 12),
or one used before (history). A request that carries colours is never read
for them; only an id is. Every look is validated and checked for readability
again before it is written.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from puffpai.looks import catalog
from puffpai.looks import package as P

HISTORY_MAX = 10
FOUND_POOL_MAX = 12

_LOCK = threading.RLock()

#: Which pages follow a look. Kept honest by the proof screenshots; a page not
#: listed in PAGES_OWN is not promised anything.
PAGES_LINE = ("The main pages change: home, chat, knowledge, apps, settings "
              "and the menu, and the background behind them.")
PAGES_OWN = ("The pictures inside cards, the book covers in the library and "
             "the 3D avatar keep their own colours.")


def _dir() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "looks")


def _path(name: str) -> str:
    return os.path.join(_dir(), name)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(name: str, default: Any) -> Any:
    from puffpai import fsutil
    return fsutil.read_json(_path(name), default)


def _write(name: str, data: Any) -> None:
    from puffpai import fsutil
    os.makedirs(_dir(), exist_ok=True)
    fsutil.atomic_write_json(_path(name), data)


def _safe(pkg: Any) -> Optional[dict]:
    """A stored look, re-validated and re-checked, or None."""
    if not isinstance(pkg, dict):
        return None
    try:
        return P.check(P.validate({k: v for k, v in pkg.items()
                                   if k not in ("checks", "adjusted")}))
    except (P.LookError, AttributeError, TypeError, ValueError):
        return None


# -- found ---------------------------------------------------------------------

def remember_found(words: str, looks: list[dict]) -> None:
    """Keep the last find's order (for numbers) and a pool of found looks.

    A look found for words that name a background ("deep blue with
    clouds") and wearing that background is remembered as such
    (``worded``): her words for that look win over an older choice.
    """
    from puffpai.looks import find as F
    named = F.names_background(words)
    # Backgrounds her words turned down ("deep
    # blue, no clouds") are remembered too, so an older choice of that very
    # background is not put back on these looks.
    down = sorted(F.turned_down_backgrounds(words))
    with _LOCK:
        old = _read("found.json", {})
        old = old if isinstance(old, dict) else {}
        pool = [p for p in old.get("pool") or [] if isinstance(p, dict)]
        new_ids = [p["id"] for p in looks]
        pool = [p for p in pool if p.get("id") not in new_ids] + list(looks)
        pool = pool[-FOUND_POOL_MAX:]
        worded = {k: v for k, v in (old.get("worded") or {}).items()
                  if isinstance(k, str) and v in P.BACKGROUNDS and k not in new_ids}             if isinstance(old.get("worded"), dict) else {}
        if named is not None:
            for p in looks:
                if p.get("background") == named:
                    worded[p["id"]] = named
        keep_ids = {p.get("id") for p in pool}
        worded = {k: v for k, v in worded.items() if k in keep_ids}
        old_down = old.get("declined") if isinstance(old.get("declined"), dict) else {}
        declined = {k: [b for b in v if b in P.BACKGROUNDS] for k, v in old_down.items()
                    if isinstance(k, str) and isinstance(v, list) and k in keep_ids
                    and k not in new_ids}
        if down:
            for p in looks:
                declined[p["id"]] = down
        _write("found.json", {"words": str(words)[:200], "at": _now(),
                              "order": new_ids, "pool": pool, "worded": worded,
                              "declined": declined})


def worded_background(look_id: Any) -> Optional[str]:
    """The background her own words asked for with this found look, or None."""
    data = _read("found.json", {})
    worded = data.get("worded") if isinstance(data, dict) else None
    if not isinstance(worded, dict) or not isinstance(look_id, str):
        return None
    got = worded.get(look_id)
    return got if got in P.BACKGROUNDS else None


def declined_backgrounds(look_id: Any) -> set[str]:
    """The backgrounds her words turned down when this look was found."""
    data = _read("found.json", {})
    got = data.get("declined") if isinstance(data, dict) else None
    if not isinstance(got, dict) or not isinstance(look_id, str):
        return set()
    row = got.get(look_id)
    return {b for b in row if b in P.BACKGROUNDS} if isinstance(row, list) else set()


def last_found() -> list[dict]:
    """The last find's looks, in the order they were shown."""
    data = _read("found.json", {})
    if not isinstance(data, dict):
        return []
    by_id = {p.get("id"): p for p in data.get("pool") or [] if isinstance(p, dict)}
    out = []
    for look_id in data.get("order") or []:
        pkg = _safe(by_id.get(look_id))
        if pkg is not None:
            out.append(pkg)
    return out


def _found_pool() -> list[dict]:
    data = _read("found.json", {})
    return [p for p in (data.get("pool") if isinstance(data, dict) else None) or []
            if isinstance(p, dict)]


# -- current / history ---------------------------------------------------------

def current() -> Optional[dict]:
    data = _read("current.json", {})
    return _safe(data.get("look")) if isinstance(data, dict) else None


def _own_background(row: Any) -> str:
    got = row.get("background") if isinstance(row, dict) else None
    return got if got in P.BACKGROUNDS else "clouds"


def _chosen_of(row: Any) -> Optional[str]:
    got = row.get("chosen_background") if isinstance(row, dict) else None
    return got if got in P.BACKGROUNDS else None


def chosen_background() -> Optional[str]:
    """The background the owner picked herself, or None."""
    return _chosen_of(_read("current.json", {}))


#: Said when a look cannot wear her background (the name goes in lower case).
KEEPS_OWN_LINE = ("That look does not read well with {background}, so it keeps "
                  "its own background.")
#: Said when her words for a look named its background and that replaces an
#: older choice (the name goes in lower case).
WORDED_LINE = ("It keeps the {background} background you asked for, and new "
               "looks will use it too.")


def wearing_chosen(pkg: dict, chosen: Optional[str] = None) -> tuple[dict, str]:
    """*pkg* wearing her chosen background, and the line to say when it cannot.

    ``chosen`` defaults to the stored choice. A look that would be too hard to
    read with it keeps its own background, and the line says so.
    """
    # A look found for words that name its
    # background ("deep blue with clouds") keeps it; her words for this look
    # are newer than her older choice.
    worded = worded_background(pkg.get("id"))
    if worded is not None and pkg.get("background") == worded:
        return pkg, ""
    chosen = chosen if chosen in P.BACKGROUNDS else chosen_background()
    declined = declined_backgrounds(pkg.get("id"))
    if chosen in declined:
        # Her words for this look turned that background down: they are
        # newer than her older choice.
        chosen = None
    if chosen is None or pkg.get("background") == chosen:
        if pkg.get("background") in declined:
            # The look's own background is one she turned down ("no
            # clouds"): it wears the first one she did not.
            for other in ("plain", "soft", "stars", "clouds"):
                if other in declined:
                    continue
                try:
                    return P.with_background(pkg, other), ""
                except P.LookError:
                    continue
        return pkg, ""
    try:
        return P.with_background(pkg, chosen), ""
    except P.LookError:
        label = P.BACKGROUND_LABELS.get(chosen, chosen).lower()
        return pkg, KEEPS_OWN_LINE.format(background=label)


def current_background() -> str:
    """The background in use: the look's own, or the one set on the bot's own look."""
    now = current()
    if now is not None:
        return now["background"]
    return _own_background(_read("current.json", {}))


def history() -> list[Optional[dict]]:
    data = _read("history.json", [])
    if not isinstance(data, list):
        return []
    return [_safe(row.get("look")) if isinstance(row, dict) else None
            for row in data][-HISTORY_MAX:]


def resolve(look_id: Any) -> Optional[dict]:
    """A look this code can vouch for, by id only."""
    if not isinstance(look_id, str) or len(look_id) > 64:
        return None
    for source in ([catalog.get(look_id) or {}], _found_pool(),
                   [(row or {}) for row in history()], [current() or {}]):
        for pkg in source:
            if isinstance(pkg, dict) and pkg.get("id") == look_id:
                safe = _safe(pkg)
                if safe is not None:
                    return safe
    return None


_KEEP = object()


def _set(pkg: Optional[dict], *, push: bool, background: Optional[str] = None,
         chosen: Any = _KEEP) -> None:
    """Write the look in use. *chosen* is her own background choice; left out,
    the stored one is kept."""
    with _LOCK:
        before = _read("current.json", {})
        before = before if isinstance(before, dict) else {}
        if push:
            rows = _read("history.json", [])
            rows = rows if isinstance(rows, list) else []
            rows.append({"look": before.get("look"),
                         "background": before.get("background") if before.get("look") is None
                         and before.get("background") in P.BACKGROUNDS else None,
                         "chosen_background": _chosen_of(before),
                         "at": _now()})
            _write("history.json", rows[-HISTORY_MAX:])
        own = background if pkg is None and background in P.BACKGROUNDS else None
        keep = _chosen_of(before) if chosen is _KEEP else (
            chosen if chosen in P.BACKGROUNDS else None)
        _write("current.json", {"look": pkg, "background": own,
                                "chosen_background": keep, "since": _now()})


def use(look_id: Any) -> dict:
    """Switch the build to a look. ``{"ok", "message", "look"}``."""
    pkg = resolve(look_id)
    if pkg is None:
        return {"ok": False, "changed": False, "look": None,
                "message": "I do not know that look. Find one first, then pick it."}
    # The look wears the background she picked, as the try-on showed.
    # Her words for this very look ("with clouds") are her newest choice.
    worded = worded_background(pkg["id"])
    worded = worded if worded == pkg.get("background") else None
    before = chosen_background()
    pkg, note = wearing_chosen(pkg)
    if worded is not None and before is not None and before != worded:
        label = P.BACKGROUND_LABELS.get(worded, worded).lower()
        note = WORDED_LINE.format(background=label)
    now = current()
    if now is not None and now["id"] == pkg["id"] and now["background"] == pkg["background"]:
        return {"ok": True, "changed": False, "look": pkg,
                "message": f"Your bot already uses the look '{pkg['name']}'."}
    if worded is not None and before is not None:
        _set(pkg, push=True, chosen=worded)
    else:
        _set(pkg, push=True)
    message = f"Your bot now uses the look '{pkg['name']}'."
    return {"ok": True, "changed": True, "look": pkg,
            "message": f"{message} {note}" if note else message}


def use_wearing(look_id: Any, background: Any) -> dict:
    """Use a look in the background it was shown with (the welcome tour).

    One undo step. The background becomes her choice, so later looks keep it
    and "Keep it" never brings the clouds back.
    """
    pkg = resolve(look_id)
    if pkg is None:
        return {"ok": False, "changed": False, "look": None,
                "message": "I do not know that look. Find one first, then pick it."}
    if background not in P.BACKGROUNDS:
        names = ", ".join(P.BACKGROUND_LABELS[b] for b in P.BACKGROUNDS)
        return {"ok": False, "changed": False, "look": current(),
                "message": f"Pick one of these backgrounds: {names}."}
    note = ""
    try:
        pkg = P.with_background(pkg, background)
    except P.LookError:
        note = KEEPS_OWN_LINE.format(
            background=P.BACKGROUND_LABELS.get(background, background).lower())
    _set(pkg, push=True, chosen=pkg["background"])
    message = f"Your bot now uses the look '{pkg['name']}'."
    return {"ok": True, "changed": True, "look": pkg,
            "message": f"{message} {note}" if note else message}


def undo() -> dict:
    with _LOCK:
        rows = _read("history.json", [])
        if not isinstance(rows, list) or not rows:
            return {"ok": False, "changed": False, "look": current(),
                    "message": "There is nothing to go back to."}
        last = rows.pop()
        _write("history.json", rows)
        pkg = _safe(last.get("look")) if isinstance(last, dict) else None
        _set(pkg, push=False, background=_own_background(last),
             chosen=_chosen_of(last))
    if pkg is None:
        return {"ok": True, "changed": True, "look": None,
                "message": "Your bot is back to its own look."}
    return {"ok": True, "changed": True, "look": pkg,
            "message": f"Your bot is back to the look '{pkg['name']}'."}


def set_background(background: Any) -> dict:
    """Change only the background of the look in use (undoable)."""
    if isinstance(background, str):
        background = background.strip().lower().replace(" color", "").replace(" colour", "")
    if background not in P.BACKGROUNDS:
        names = ", ".join(P.BACKGROUND_LABELS[b] for b in P.BACKGROUNDS)
        return {"ok": False, "changed": False, "look": current(),
                "message": f"Pick one of these backgrounds: {names}."}
    label = P.BACKGROUND_LABELS[background]
    if current_background() == background:
        if chosen_background() != background:
            # Already worn, but now it is her choice too: later looks keep it.
            with _LOCK:
                data = _read("current.json", {})
                data = data if isinstance(data, dict) else {}
                data["chosen_background"] = background
                _write("current.json", data)
        return {"ok": True, "changed": False, "look": current(),
                "message": f"The background is already {label.lower()}."}
    now = current()
    if now is None:
        _set(None, push=True, background=background, chosen=background)
        return {"ok": True, "changed": True, "look": None,
                "message": f"The background is now {label.lower()}. You can undo this."}
    try:
        pkg = P.with_background(now, background)
    except P.LookError:
        return {"ok": False, "changed": False, "look": now,
                "message": "That background makes this look too hard to read, "
                           "so I kept the one you have."}
    _set(pkg, push=True, chosen=background)
    return {"ok": True, "changed": True, "look": pkg,
            "message": f"The background is now {label.lower()}. You can undo this."}


def reset() -> dict:
    if current() is None and current_background() == "clouds" \
            and chosen_background() is None:
        return {"ok": True, "changed": False, "look": None,
                "message": "Your bot already uses its own look."}
    _set(None, push=True, chosen=None)
    return {"ok": True, "changed": True, "look": None,
            "message": "Your bot is back to its own look. You can undo this."}


def state() -> dict:
    """Everything a screen needs about the look in use."""
    now = current()
    hist = history()
    background = current_background()
    return {
        "current": P.summary(now) if now else None,
        # What the page wears (null: the bot's own look with its clouds).
        "vars": (P.summary(now)["vars"] if now else P.own_vars(background)),
        "background": background,
        # The background she picked herself (null: none); every look she
        # tries on or keeps wears it.
        "chosen_background": chosen_background(),
        "background_line": P.background_line(background),
        "backgrounds": [{"id": b, "label": P.BACKGROUND_LABELS[b]} for b in P.BACKGROUNDS],
        "can_undo": bool(_read("history.json", [])),
        "history": [{"id": p["id"], "name": p["name"]} if p else
                    {"id": "", "name": "The bot's own look"} for p in reversed(hist)],
        "pages_line": PAGES_LINE,
        "pages_own": PAGES_OWN,
    }


__all__ = ["HISTORY_MAX", "KEEPS_OWN_LINE", "PAGES_LINE", "PAGES_OWN",
           "chosen_background", "current", "current_background", "wearing_chosen",
           "worded_background", "WORDED_LINE",
           "history", "last_found", "remember_found", "reset", "resolve",
           "set_background", "state", "undo", "use", "use_wearing"]
