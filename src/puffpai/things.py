"""Your bot's things: one list of everything it has, each with its own page.

Everything the bot got lives in different places: skills in
``<state>/skills``, tool servers in ``mcp_servers.json``, apps and
data in ``<state>/integrations``, books in ``<state>/learning``. This module
reads them all into ONE list, so the home page can show a tile per thing and
every thing can have its own page at ``/dashboard/things/<id>``.

Each thing is ``{id, kind, name, icon, blurb, on_home, page}`` where kind is
``skill``, ``tool_server``, ``app``, ``data`` or ``book``. Ids are made from
where the thing lives (``skill-<folder>``, ``tool-<server>``,
``app-<integration>``, ``data-<integration>``, ``book-<book>``), so they stay
the same across restarts and nothing new has to be stored to find them.

An app BuildBot put together for the bot's jobs (:mod:`puffpai.assemble`)
is ``assembled-<app id>`` (kind ``app``): its page shows the Assembled panel
(each job working or not yet, Start and Stop, Open, Use it here, Put it
together again). It runs on this computer like any other app there, so its
Start goes through the Assemble step's own start.

**One page per job.** Once a bot has its build record
(``<state>/build/build.json``, :mod:`puffpai.build`), a live card, an app or a
data set it got (``feed-*``, ``app-*``, ``data-*``) is an ingredient row on
the page of the job it is used for, not a page of its own:
:func:`redirect_for` sends its old address to that job's page, or to the
"Not used" list when no job uses it, and :func:`ingredient_row` is the row.
A bot with no build record yet keeps the old pages.

A built-in app (Calendar and the like, see :mod:`puffpai.home_things`) joins
the list as ``builtin-<app>`` (kind ``app``) once she puts it on her home page
from chat; its tile opens the app's own page.

The page recipe (Toolsmith's job: title, icon, one plain line, 3 or 4 "Try
it" requests and "what it can do") is made when a thing is installed, and on
the first visit for a thing that was there before. It is made from the words
at once (the SKILL.md description and headings, or the tool list), then the
brain is asked once in the background for friendlier words. Recipes and the
"On my home page" choices are kept in ``<state>/things/`` rather than inside a
skill's own folder, so a skill's files stay exactly what was installed (its
fingerprints in ``.lock.json`` still match, and ``skill_view`` lists only its
own files).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Callable, Optional

from puffpai import fsutil
from puffpai import skills as skills_mod
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

KINDS = ("skill", "tool_server", "app", "data", "book", "feed")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,100}$")
PAGE = "/dashboard/things/{id}"
#: A job's own page, and the "Not used" list for what no job uses.
JOB_PAGE = "/dashboard/jobs/{id}"
NOT_USED_PAGE = "/dashboard#not-used"
#: Thing id prefixes that are ingredients of a job, never a page of their own.
INGREDIENT_PREFIXES = ("feed", "app", "data")
ICONS = {"skill": "\U0001F9E9", "tool_server": "\U0001F6E0️", "app": "\U0001FA9F",
         "data": "\U0001F4CA", "book": "\U0001F4DA",
         "feed": "\U0001F4E1"}
KIND_WORDS = {"skill": "skill", "tool_server": "tool server", "app": "app",
              "data": "data", "book": "book", "feed": "live card"}
LINE_MAX = 160
TRY_MAX = 4
CAN_MAX = 6
#: How much of a SKILL.md or tool list the brain reads to make a recipe.
SOURCE_MAX = 6000
#: Rows a data page shows per list.
TABLE_ROWS = 200
#: Headings in a SKILL.md that say how the file is laid out, not what it does.
_PLUMBING = frozenset({
    "when to use", "architecture", "quick reference", "setup", "set up", "install",
    "installation", "requirements", "usage", "overview", "license", "licence", "notes",
    "references", "files", "core rules", "rules", "troubleshooting", "memory",
    "configuration", "config", "changelog", "about", "how to use", "instructions",
    "prerequisites", "dependencies", "security", "privacy", "contents",
    "table of contents", "related", "see also", "credits", "feedback", "data storage",
    "external endpoints", "trust", "common traps", "traps", "examples", "example"})
_HEADING_RE = re.compile(r"(?m)^#{2,3}\s+(.+?)\s*#*\s*$")
_LOCK = threading.Lock()
_MAKING: set[str] = set()

NO_THING = "There is nothing by that name on your bot."
BOOK_REMOVE = "Books are taken out on the Knowledge page."
FEED_SHIPPED = ("This live card came with your bot, so it stays. "
                "Take it off your home page instead.")
ASSEMBLED_STAYS = ("BuildBot put this app together for your bot's jobs, so it stays. "
                   "Take it off your home page instead, or put it together again.")
#: A "Try it" line that promises a search; a live card that cannot look a
#: subject up never shows one (it could only make an answer up).
_PROMISES_LOOKUP = re.compile(r"(?i)\b(look\w*(\s+\S+){0,4}\s+up|lookup|search\w*|find\w*)\b")


# ── where things are kept ──────────────────────────────────────────────────

def _dir(*parts: str) -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "things", *parts)


def _home_path() -> str:
    return _dir("home.json")


def _page_path(thing_id: str) -> str:
    return _dir("pages", f"{thing_id}.json")


def _screens_path() -> str:
    return _dir("screens.json")


def remember_screen(thing_id: str, url: str) -> None:
    """The local screen (127.0.0.1) a thing's command printed last, so its
    page can offer Open. Only a local address is kept."""
    from puffpai import brain_commands
    if not ID_RE.match(str(thing_id or "")) or brain_commands.local_addresses(url) != [url]:
        return
    with _LOCK:
        data = fsutil.read_json(_screens_path(), default={})
        data = data if isinstance(data, dict) else {}
        data[thing_id] = url
        fsutil.atomic_write_json(_screens_path(), data)


def screen_of(thing_id: str) -> str:
    from puffpai import brain_commands
    data = fsutil.read_json(_screens_path(), default={})
    url = str((data if isinstance(data, dict) else {}).get(thing_id) or "")
    return url if url and brain_commands.local_addresses(url) == [url] else ""


def _home_choices() -> dict[str, bool]:
    data = fsutil.read_json(_home_path(), default={})
    return {str(k): bool(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _one_line(value: Any, limit: int = LINE_MAX) -> str:
    """Plain words on one line: no long dashes, cut at a word."""
    text = " ".join(str(value or "").split())
    text = re.sub(r"\s*[\u2014\u2013]\s*", ", ", text).strip(" ,")
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def _first_sentence(value: Any, limit: int = LINE_MAX) -> str:
    text = _one_line(value, 1000)
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return _one_line(m.group(1) if m else text, limit)


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").lower()).strip("-_")
    return text[:90]


# ── reading what the bot has ───────────────────────────────────────────────

def _emoji_of(meta: dict) -> str:
    """The emoji a skill's frontmatter names (OpenClaw / ClawHub metadata)."""
    found = meta.get("emoji")
    raw = meta.get("metadata")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    if not found and isinstance(raw, dict):
        for inner in raw.values():
            if isinstance(inner, dict) and inner.get("emoji"):
                found = inner["emoji"]
                break
    text = str(found or "").strip()
    if 0 < len(text) <= 8 and not re.search(r"[A-Za-z0-9<>]", text):
        return text
    return ""


def _skill_lock() -> dict:
    try:
        from puffpai.finding import install
        return install._read_lock()  # noqa: SLF001 - the one record of where a skill came from
    except Exception:  # noqa: BLE001
        return {}


def _shelf(kind: str, ref: str) -> dict:
    """What the shelf called a thing she added from Find more: ``{name, what}``
    from its install entry (and the find it came from), or {}."""
    try:
        from puffpai.finding import find, install
        rows = install.entries()
    except Exception:  # noqa: BLE001
        return {}
    key = {"skill": "folder", "mcp": "server", "app": "integration"}[kind]
    for e in reversed(rows):
        how = e.get("undo") or {}
        if how.get("kind") == kind and str(how.get(key) or "") == ref:
            what = e.get("what") or (find.recall(e.get("id")) or {}).get("what") or ""
            return {"name": str(e.get("name") or ""), "what": str(what)}
    return {}


def skill_name(rec: dict) -> str:
    """A skill's real name: the shelf's name for it, else its SKILL.md name,
    never just its folder ("Wedding.skill", not "wedding-skill")."""
    return (_one_line(_shelf("skill", rec["folder"]).get("name"), 80)
            or _one_line(rec.get("name"), 80) or rec["folder"])


def _skills() -> list[dict]:
    lock = _skill_lock()
    out = []
    for rec in skills_mod.installed():
        folder = rec["folder"]
        tid = f"skill-{folder.lower()}"
        if not ID_RE.match(tid):
            continue
        meta = rec.get("frontmatter") or {}
        out.append({"id": tid, "kind": "skill", "name": skill_name(rec),
                    "icon": _emoji_of(meta) or ICONS["skill"],
                    "blurb": _first_sentence(rec.get("description")),
                    "from": str((lock.get(folder) or {}).get("url") or ""),
                    "ref": folder})
    return out


def _tool_servers() -> list[dict]:
    try:
        from puffpai import mcp_client
        rows = mcp_client.servers()
    except Exception:  # noqa: BLE001 - a broken list shows no tool servers
        return []
    out = []
    for srv in rows:
        tid = f"tool-{srv['name']}"
        if not ID_RE.match(tid):
            continue
        tools = mcp_client.model_tools(mcp_client.list_tools(srv["name"]))
        # What the tool list says the server is for; one tool's own text is
        # not (else a page reads "Create a new anonymous project").
        blurb = _first_sentence(_shelf("mcp", srv["name"]).get("what")) or \
            f"{len(tools)} tools your bot can use."
        out.append({"id": tid, "kind": "tool_server", "name": srv["label"],
                    "icon": ICONS["tool_server"], "blurb": blurb,
                    "from": str(srv.get("source") or ""), "ref": srv["name"]})
    return out


def _integrations() -> list[dict]:
    try:
        from puffpai.integrate import records
        rows = records.all()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for rec in rows:
        if rec.get("status") != "ready":
            continue
        if rec.get("app_id") and rec.get("mode") in ("static_web", "web_build"):
            kind = "app"
        elif rec.get("mode") in HOST_MODES:
            kind = "app"          # runs on this computer (integrate.run_here)
        elif rec.get("list_app_id") and rec.get("mode") == "data_template":
            kind = "data"
        else:
            continue
        tid = f"{kind}-{rec['id']}"
        out.append({"id": tid, "kind": kind, "name": _one_line(rec.get("name"), 80) or rec["id"],
                    "icon": ICONS[kind], "blurb": _first_sentence(rec.get("purpose")),
                    "from": str((rec.get("from") or {}).get("url") or ""), "ref": rec["id"]})
    return out


def _books() -> list[dict]:
    try:
        from puffpai.knowledge import progress
        rows = progress.read_all()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for rec in rows:
        bid = str(rec.get("id") or "")
        tid = f"book-{_slug(bid)}"
        if not bid or not ID_RE.match(tid):
            continue
        name = _one_line(rec.get("name") or bid, 80)
        out.append({"id": tid, "kind": "book", "name": name, "icon": ICONS["book"],
                    "blurb": "A book your bot reads to answer questions.",
                    "from": str(rec.get("source") or rec.get("url") or ""), "ref": bid})
    return out


def _feeds() -> list[dict]:
    """Live cards (apps with a feed): what they found lately, and a lookup."""
    try:
        from puffpai import feeds
        rows = feeds.feed_apps()
    except Exception:  # noqa: BLE001 - a broken list shows no live cards
        return []
    out = []
    for manifest, feed in rows:
        app_id = str(manifest.get("id") or "")
        tid = f"feed-{_slug(app_id)}"
        if not app_id or not ID_RE.match(tid):
            continue
        name = _one_line(manifest.get("name") or feed["service"]["name"], 80) or app_id
        out.append({"id": tid, "kind": "feed", "name": name, "icon": ICONS["feed"],
                    "blurb": _first_sentence(manifest.get("blurb"))
                    or f"Live data from {name}.",
                    "from": f"https://{feed['service']['host']}/", "ref": app_id})
    return out


def builtin_id(app_id: Any) -> str:
    return f"builtin-{_slug(app_id)}"


def _builtins(chosen_only: bool = True) -> list[dict]:
    """Built-in apps as things. *chosen_only*: just the ones she put on (or
    took off) her home page, so the list does not fill up with every app."""
    try:
        from puffpai import home_things
        apps = home_things.builtin_apps()
    except Exception:  # noqa: BLE001
        return []
    home = _home_choices() if chosen_only else {}
    out = []
    for app in apps:
        tid = builtin_id(app["id"])
        if not ID_RE.match(tid) or (chosen_only and tid not in home):
            continue
        out.append({"id": tid, "kind": "app", "name": _one_line(app["name"], 80) or app["id"],
                    "icon": ICONS["app"], "blurb": _first_sentence(app.get("blurb")),
                    "from": "", "ref": app["id"], "open": app["page"], "builtin": True})
    return out


def assembled_id(app_id: Any) -> str:
    return f"assembled-{_slug(app_id)}"


def _assembled() -> list[dict]:
    """Apps BuildBot put together (one per spec under ``<state>/assemble``)."""
    try:
        from puffpai.assemble import api as assemble_api
        from puffpai.assemble.server import host
        specs = assemble_api.all_specs()
    except Exception:  # noqa: BLE001 - no Assemble step, no such things
        return []
    out = []
    for spec in specs:
        app_id = str(spec.get("id") or "")
        tid = assembled_id(app_id)
        if not app_id or not ID_RE.match(tid):
            continue
        words = spec.get("cap_words") or {}
        jobs = [str(words.get(c) or "").split(": ", 1)[0] for c in spec.get("caps") or []]
        jobs = [j for j in jobs if j]
        try:
            ref = host.host_id(app_id)
        except Exception:  # noqa: BLE001
            continue
        out.append({"id": tid, "kind": "app", "name": _one_line(spec.get("title"), 80) or app_id,
                    "icon": ICONS["app"],
                    "blurb": _one_line("BuildBot put this together for: " + "; ".join(jobs) + "."
                                       if jobs else "An app BuildBot put together."),
                    "from": "", "ref": ref, "assembled": app_id})
    return out


def _everything() -> list[dict]:
    return [*_skills(), *_tool_servers(), *_integrations(), *_books(), *_feeds(),
            *_builtins(), *_assembled()]


# ── ingredients of a job (the one build record) ────────────────────────────

def _build_record() -> Optional[dict]:
    """The bot's build record, or None when it has none yet (an older bot)."""
    from puffpai.config import get_state_dir
    data = fsutil.read_json(os.path.join(get_state_dir(), "build", "build.json"),
                            default=None)
    return data if isinstance(data, dict) else None


def _refs_of(thing_id: str, thing: Optional[dict]) -> set[str]:
    """Every way the build record may name this thing: its id, its own
    reference, ``<kind>:<reference>``, and a web app's own app id."""
    tail = thing_id.split("-", 1)[1] if "-" in thing_id else thing_id
    refs = {thing_id, tail}
    if thing:
        ref = str(thing.get("ref") or "")
        refs |= {ref, f"{thing['kind']}:{ref}", f"{thing_id.split('-', 1)[0]}:{ref}"}
        if thing_id.startswith("app-"):
            try:
                from puffpai.integrate import records
                app_id = str((records.get(ref) or {}).get("app_id") or "")
            except Exception:  # noqa: BLE001
                app_id = ""
            if app_id:
                refs |= {app_id, f"app:{app_id}", f"webapp:{app_id}"}
        if thing_id.startswith(("app-", "data-")):
            # The find it was installed from (the build record names a part
            # by its find id, ``github:owner/repo``).
            try:
                from puffpai.finding import install
                refs |= {str(e.get("id") or "") for e in install.entries()
                         if (e.get("undo") or {}).get("integration") == ref}
            except Exception:  # noqa: BLE001
                pass
    return {r for r in refs if r}


def _ingredient_ref(entry: Any) -> str:
    if isinstance(entry, dict):
        entry = entry.get("ref") or entry.get("id")
    return str(entry or "")


def owning_job(thing_id: Any, record: Optional[dict] = None, *,
               thing: Optional[dict] = None) -> Optional[dict]:
    """The job in the build record whose ingredients name this thing, or None."""
    tid = str(thing_id or "")
    record = _build_record() if record is None else record
    if not record or not ID_RE.match(tid):
        return None
    refs = _refs_of(tid, thing if thing is not None else get(tid))
    parts = record.get("parts") if isinstance(record.get("parts"), dict) else {}
    host = _feed_host(tid)
    for job in record.get("jobs") or ():
        if not isinstance(job, dict) or not job.get("id"):
            continue
        for entry in job.get("ingredients") or ():
            ref = _ingredient_ref(entry)
            if ref in refs or ref.rsplit(":", 1)[-1] in refs:
                return job
            # The part's own forms: the live card her Yes made for a website
            # ("seed:nasa_epic" -> nasa_epic_earth_photos), its app id, or
            # the one site the live card reads.
            part = parts.get(ref) if isinstance(parts.get(ref), dict) else {}
            if part.get("unusable"):
                continue
            own = {str(part.get(k) or "") for k in ("live_card", "app_id")} - {""}
            if own & refs or (host and host in [str(h).lower() for h in part.get("hosts") or []]):
                return job
    return None


def _feed_host(thing_id: str) -> str:
    """The one site a live card (``feed-<app id>``) reads, or ""."""
    if not thing_id.startswith("feed-"):
        return ""
    try:
        from puffpai.config import get_state_dir
        m = fsutil.read_json(os.path.join(get_state_dir(), "apps", thing_id[5:], "app.json"),
                             default=None)
        return str(((m or {}).get("feed") or {}).get("service", {}).get("host") or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def redirect_for(thing_id: Any, *, thing: Optional[dict] = None,
                 record: Optional[dict] = None) -> Optional[str]:
    """Where an old live card, app or data page now lives: its job's page, or
    the "Not used" list when no job uses it. None for any other thing, for a
    bot with no build record yet, and for a thing that is not there."""
    tid = str(thing_id or "")
    if not ID_RE.match(tid) or tid.split("-", 1)[0] not in INGREDIENT_PREFIXES:
        return None
    record = _build_record() if record is None else record
    if record is None:
        return None
    thing = thing if thing is not None else get(tid)
    job = owning_job(tid, record, thing=thing)
    if job is not None:
        return JOB_PAGE.format(id=job["id"])
    return NOT_USED_PAGE if thing is not None else None


def ingredient_row(thing_id: Any) -> Optional[dict]:
    """A thing as one ingredient row on a job's page: name, kind in plain
    words, where it came from, and the job it is used for ("" when none)."""
    thing = get(thing_id)
    if thing is None:
        return None
    job = owning_job(thing["id"], thing=thing)
    return {"id": thing["id"], "name": thing["name"], "kind": thing["kind"],
            "kind_words": KIND_WORDS[thing["kind"]], "icon": thing["icon"],
            "blurb": thing["blurb"], "from": thing["from"],
            "job": str(job["id"]) if job else ""}


def _page_of(thing: dict, record: Optional[dict]) -> str:
    return (thing.get("open") or redirect_for(thing["id"], thing=thing, record=record)
            or PAGE.format(id=thing["id"]))


def _with_recipe_words(thing: dict) -> dict:
    """The tile's icon and line from its page recipe, once there is one."""
    rec = _read_recipe(thing["id"])
    if rec:
        thing = {**thing, "icon": rec.get("icon") or thing["icon"],
                 "blurb": rec.get("line") or thing["blurb"]}
    return thing


def listing() -> list[dict]:
    """``GET /things``: every thing, in the pinned shape."""
    home = _home_choices()
    record = _build_record()
    out = []
    for t in _everything():
        t = _with_recipe_words(t)
        out.append({"id": t["id"], "kind": t["kind"], "name": t["name"], "icon": t["icon"],
                    "blurb": t["blurb"], "on_home": home.get(t["id"], True),
                    "page": _page_of(t, record)})
    return out


def get(thing_id: Any) -> Optional[dict]:
    """One thing with its private reference (folder, server name, record id)."""
    tid = str(thing_id or "")
    if not ID_RE.match(tid):
        return None
    kind = tid.split("-", 1)[0]
    source = {"skill": _skills, "tool": _tool_servers, "app": _integrations,
              "data": _integrations, "book": _books, "feed": _feeds,
              "builtin": lambda: _builtins(chosen_only=False),
              "assembled": _assembled}.get(kind)
    if source is None:
        return None
    return next((t for t in source() if t["id"] == tid), None)


def set_home(thing_id: Any, on: bool) -> dict:
    thing = get(thing_id)
    if thing is None:
        return {"ok": False, "message": NO_THING}
    with _LOCK:
        home = _home_choices()
        home[thing["id"]] = bool(on)
        fsutil.atomic_write_json(_home_path(), home)
    where = "on" if on else "off"
    return {"ok": True, "on_home": bool(on),
            "message": f"{thing['name']} is {where} your home page."}


def _forget(thing_id: str) -> None:
    with _LOCK:
        home = _home_choices()
        if home.pop(thing_id, None) is not None:
            fsutil.atomic_write_json(_home_path(), home)
        try:
            os.remove(_page_path(thing_id))
        except OSError:
            pass


# ── removing, through the existing undo ────────────────────────────────────

def _install_entry(thing: dict) -> Optional[str]:
    """The find-install entry that put this thing here, if any."""
    from puffpai.finding import install
    want = {"skill": ("skill", "folder"), "tool_server": ("mcp", "server"),
            "app": ("app", "integration"), "data": ("app", "integration")}.get(thing["kind"])
    if want is None:
        return None
    for e in reversed(install.entries()):
        how = e.get("undo") or {}
        if how.get("kind") == want[0] and str(how.get(want[1]) or "") == thing["ref"]:
            return str(e.get("entry") or "")
    return None


def remove(thing_id: Any) -> dict:
    """``DELETE /things/<id>``: through the find-install undo when it came
    that way, else the thing's own remove. Books stay on the Knowledge page."""
    thing = get(thing_id)
    if thing is None:
        return {"ok": False, "message": NO_THING, "code": "unknown"}
    kind, ref = thing["kind"], thing["ref"]
    if kind == "book":
        return {"ok": False, "message": BOOK_REMOVE, "code": "book"}
    if kind == "feed":
        return _remove_feed(thing)
    if thing.get("assembled"):
        return {"ok": False, "message": ASSEMBLED_STAYS, "code": "assembled"}
    if thing.get("builtin"):
        # A built-in app stays built in; it only leaves the home page.
        _forget(thing["id"])
        return {"ok": True, "message": f"{thing['name']} is off your home page. "
                                       "It is still in the menu."}
    from puffpai.finding import install
    entry = _install_entry(thing)
    if entry:
        out = install.undo(entry)
        ok, line = bool(out.get("ok")), str(out.get("line") or "")
    elif kind == "skill":
        ok = install.remove_skill(ref)
        line = f"{thing['name']} is taken out." if ok else "It could not be taken out."
    elif kind == "tool_server":
        from puffpai import mcp_client
        out = mcp_client.remove_server(ref, by="owner")
        ok, line = bool(out.get("ok")), str(out.get("message") or "")
    else:
        from puffpai.integrate import records
        out = records.remove(ref, by="owner")
        ok, line = bool(out.get("ok")), str(out.get("line") or "")
    if ok:
        _forget(thing["id"])
    return {"ok": ok, "message": line, **({} if ok else {"code": "failed"})}


def _remove_feed(thing: dict) -> dict:
    """Take out a live card the wizard or the Add page put in the owner's own
    apps folder: its folder goes, and the rescan drops its outside service."""
    import shutil

    from puffpai import app_registry
    try:
        folder = app_registry.app_dir(thing["ref"], app_registry.user_apps_dir())
    except ValueError:
        return {"ok": False, "message": NO_THING, "code": "unknown"}
    if not os.path.isfile(os.path.join(folder, app_registry.MANIFEST_NAME)):
        return {"ok": False, "message": FEED_SHIPPED, "code": "book"}
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        logger.warning("things: live card not taken out (%s)", type(exc).__name__)
        return {"ok": False, "message": "It could not be taken out.", "code": "failed"}
    app_registry.rescan()
    _forget(thing["id"])
    return {"ok": True, "message": f"{thing['name']} is taken out."}


def _feed_looks_up(thing: dict) -> bool:
    """Whether a live card can really look a subject up on its service."""
    try:
        from puffpai import feeds
        _manifest, feed = feeds._find(thing["ref"])  # noqa: SLF001
        return bool(feeds.lookup_template(feed))
    except Exception:  # noqa: BLE001
        return False


def lookup(thing_id: Any, topic: Any) -> dict:
    """``POST /things/<id>/lookup``: the live card page's own lookup box.
    Her subject goes through the privacy screen and out through the card's
    own outside service (``feeds.lookup``), only when the card can search."""
    thing = get(thing_id)
    if thing is None or thing["kind"] != "feed":
        return {"ok": False, "message": NO_THING, "code": "unknown"}
    words = " ".join(str(topic or "").split())[:80]
    if not words:
        return {"ok": False, "message": "Type what to look up."}
    if not _feed_looks_up(thing):
        return {"ok": False, "message": f"{thing['name']} cannot look a subject up. "
                                        "It only shows its newest items.", "code": "book"}
    from puffpai import feeds
    got = feeds.lookup(thing["ref"], words, said=words)
    if not got.get("ok"):
        return {"ok": False, "message": str(got.get("error") or "That did not work."),
                "how_to_fix": got.get("how_to_fix") or "", "code": "failed"}
    return {"ok": True, "topic": got.get("topic") or words, "items": got.get("items") or [],
            "credit": got.get("credit") or "", "message": got.get("note") or ""}


# ── the page recipe (Toolsmith) ────────────────────────────────────────────

def _read_recipe(thing_id: str) -> Optional[dict]:
    data = fsutil.read_json(_page_path(thing_id), default=None)
    return data if isinstance(data, dict) and data.get("title") else None


def _write_recipe(thing_id: str, recipe: dict) -> None:
    fsutil.atomic_write_json(_page_path(thing_id), recipe)


def _skill_text(folder: str) -> str:
    try:
        with open(os.path.join(skills_mod.skills_dir(), folder, skills_mod.SKILL_FILE),
                  encoding="utf-8", errors="replace") as fh:
            return fh.read(256 * 1024)
    except OSError:
        return ""


def headings(text: str) -> list[str]:
    """What a SKILL.md says it can do: its ## and ### headings, without the
    ones about how the file is laid out."""
    out: list[str] = []
    for raw in _HEADING_RE.findall(text or ""):
        h = _one_line(re.sub(r"[`*_]", "", raw), 60)
        h = re.sub(r"^\d+[.)]\s*", "", h)
        if not h or h.lower().strip(":") in _PLUMBING or h in out:
            continue
        if not re.search(r"[A-Za-z]", h):
            continue
        out.append(h)
    return out


def _tool_words(name: str) -> str:
    return " ".join(re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name)).replace("_", " ")
                    .replace("-", " ").split()).lower()


def _lower_words(text: str) -> str:
    """"Budget Control" -> "budget control"; "RSVP" stays."""
    return " ".join(w.lower() if w[:1].isupper() and w[1:].islower() else w
                    for w in text.split())


def source_text(thing: dict) -> str:
    """What the recipe is made from: the SKILL.md, or the tool list."""
    if thing["kind"] == "skill":
        return _skill_text(thing["ref"])
    if thing["kind"] == "tool_server":
        from puffpai import mcp_client
        about = _shelf("mcp", thing["ref"]).get("what")
        return "\n".join([*([f"What it is: {_one_line(about, 400)}"] if about else []),
                          *(f"- {t['name']}: {_one_line(t.get('description'), 400)}"
                            for t in mcp_client.list_tools(thing["ref"]))])
    return thing.get("blurb") or ""


def fallback_recipe(thing: dict) -> dict:
    """A recipe from the words alone (no brain): the description and the
    headings of a SKILL.md, or the tool list of a tool server."""
    name, kind = thing["name"], thing["kind"]
    line = thing.get("blurb") or ""
    tries: list[str] = []
    can: list[str] = []
    if kind == "skill":
        meta, body = skills_mod.parse_frontmatter(_skill_text(thing["ref"]))
        line = _first_sentence(meta.get("description")) or line
        can = headings(body)[:CAN_MAX]
        # Short headings read as jobs ("Budget Control"); long ones are rules.
        jobs = [h for h in can if len(h.split()) <= 4][:TRY_MAX - 1]
        tries = [f"Help me with {_lower_words(h)}" for h in jobs]
        tries.append(f"What can {name} help me with?")
        if len(tries) < 2:
            tries.append(f"Help me get started with {name}")
    elif kind == "tool_server":
        from puffpai import mcp_client
        tools = mcp_client.model_tools(mcp_client.list_tools(thing["ref"]))
        can = [_first_sentence(t.get("description")) or _tool_words(t["name"])
               for t in tools][:CAN_MAX]
        tries = [f"Use {name} to {_tool_words(t['name'])}" for t in tools[:TRY_MAX]]
        line = line or f"{len(tools)} tools your bot can use."
    elif kind == "data":
        tries = [f"What is in {name}?", f"Show me the first few rows of {name}"]
    elif kind == "book":
        tries = [f"What does {name} cover?"]
    elif kind == "feed":
        tries = [f"What is new on {name}?"]
        if _feed_looks_up(thing):
            tries.append(f"Look something up on {name} for me")
    elif thing.get("assembled"):
        spec = _assembled_spec(thing)
        words = spec.get("cap_words") or {}
        can = [str(words.get(c) or "").split(": ", 1)[0] for c in spec.get("caps") or []]
        tries = [str(c.get("ask") or "") for c in spec.get("checks") or []]
    else:
        line = line or f"Open {name} on its page."
    return {"title": name, "icon": thing.get("icon") or ICONS.get(kind, ""),
            "line": _one_line(line) or f"Your bot's {KIND_WORDS[kind]}.",
            "try": [_one_line(t, 120) for t in tries if t][:TRY_MAX],
            "can": [c for c in can if c][:CAN_MAX], "made_by": "words",
            "made_at": int(time.time())}


RECIPE_RULES = (
    "You make a small page for one thing a person's home AI bot has (a skill, a tool "
    "server, an app, some data or a book). Read what it is, then answer with JSON only: "
    '{"icon": "one emoji that fits", "line": "one plain, friendly sentence saying what '
    'it does for the owner", "try": ["3 or 4 short requests the owner could tap to try '
    'it, written the way the owner would say them, that can be answered in words '
    'right away (advice, a plan, a list, a draft), without running programs or saving '
    'files"], "can": ["up to 5 short lines of what it can do"]}. Use plain everyday '
    "words. Never use long dashes. Never use the word door.")
RECIPE_FORMAT = {"type": "object", "required": ["icon", "line", "try", "can"], "properties": {
    "icon": {"type": "string"}, "line": {"type": "string"},
    "try": {"type": "array", "items": {"type": "string"}},
    "can": {"type": "array", "items": {"type": "string"}}}}


def _backend():
    """The local brain, asked once per recipe. Tests replace this."""
    from puffpai.config import get_config
    from puffpai.inference.ollama_backend import OllamaBackend
    cfg = get_config()
    return OllamaBackend(url=cfg.ollama.url, model=cfg.ollama.model, num_ctx=8192,
                         num_predict=900, timeout=240, think=False)


def _ok_words(text: str) -> bool:
    return bool(text) and not re.search(r"(?i)\bdoors?\b", text)


def brain_recipe(thing: dict, backend: Any = None) -> Optional[dict]:
    """Ask the brain once for the page's words. None when it gave nothing usable."""
    base = fallback_recipe(thing)
    brain = backend if backend is not None else _backend()
    searches = thing["kind"] != "feed" or _feed_looks_up(thing)
    ask = (f"Name: {thing['name']}\nKind: {KIND_WORDS[thing['kind']]}\n"
           + ("" if searches else "It only shows its newest items. It cannot search "
                                  "or look anything up.\n")
           + "\n<source>\n" + source_text(thing)[:SOURCE_MAX] + "\n</source>")
    try:
        answer = brain.chat([{"role": "system", "content": RECIPE_RULES},
                             {"role": "user", "content": ask}],
                            temperature=0.3, max_tokens=900, format=RECIPE_FORMAT)
    except Exception as exc:  # noqa: BLE001 - the words recipe stays
        logger.info("things: the brain gave no recipe (%s)", type(exc).__name__)
        return None
    text = answer if isinstance(answer, str) else str((answer or {}).get("content") or "")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None
    line = _one_line(data.get("line"))
    tries = [_one_line(t, 120) for t in data.get("try") or [] if isinstance(t, str)]
    tries = [t for t in tries if _ok_words(t)
             and (searches or not _PROMISES_LOOKUP.search(t))][:TRY_MAX]
    can = [_one_line(c, 100) for c in data.get("can") or [] if isinstance(c, str)]
    can = [c for c in can if _ok_words(c)][:CAN_MAX - 1]
    if not _ok_words(line) or len(tries) < 2:
        return None
    icon = str(data.get("icon") or "").strip()
    if not (0 < len(icon) <= 8) or re.search(r"[A-Za-z0-9<>]", icon):
        icon = base["icon"]
    return {**base, "icon": icon, "line": line, "try": tries,
            "can": can or base["can"], "made_by": "brain", "made_at": int(time.time())}


def brain_on() -> bool:
    flag = str(os.environ.get("PUFFPAI_THINGS_BRAIN", "on")).strip().lower()
    return flag not in ("0", "off", "false", "no")


def _improve(thing: dict, backend: Any = None) -> None:
    try:
        made = brain_recipe(thing, backend)
        if made is not None and get(thing["id"]) is not None:
            _write_recipe(thing["id"], made)
    except Exception as exc:  # noqa: BLE001
        logger.info("things: recipe not improved (%s)", type(exc).__name__)
    finally:
        with _LOCK:
            _MAKING.discard(thing["id"])


#: Starts the brain's pass. Tests swap it to run at once.
def _start(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="things-recipe", daemon=True).start()


def make_recipe(thing_id: Any) -> Optional[dict]:
    """Make (or keep) the page recipe for one thing: the words recipe now,
    then the brain's words in the background (once). Returns the recipe."""
    thing = get(thing_id)
    if thing is None:
        return None
    rec = _read_recipe(thing["id"])
    if rec is None:
        rec = fallback_recipe(thing)
        _write_recipe(thing["id"], rec)
    if rec.get("made_by") != "brain" and brain_on() and not thing.get("assembled"):
        with _LOCK:
            start = thing["id"] not in _MAKING
            _MAKING.add(thing["id"])
        if start:
            _start(lambda: _improve(thing))
    return _read_recipe(thing["id"]) or rec


def improving(thing_id: str) -> bool:
    with _LOCK:
        return thing_id in _MAKING


# ── after an install ───────────────────────────────────────────────────────

def id_for_install(undo: Any) -> str:
    """The thing id a find-install made (its ``undo``), or ""."""
    how = undo if isinstance(undo, dict) else {}
    if how.get("kind") == "skill" and how.get("folder"):
        return f"skill-{str(how['folder']).lower()}"
    if how.get("kind") == "mcp" and how.get("server"):
        return f"tool-{how['server']}"
    if how.get("kind") == "book" and how.get("book"):
        return f"book-{_slug(how['book'])}"
    if how.get("kind") == "app" and how.get("integration"):
        for t in _integrations():
            if t["ref"] == how["integration"]:
                return t["id"]
    return ""


def after_install(undo: Any) -> str:
    """Put a newly installed thing on the home page and make its page.
    Returns its page address, or "" when it is not a thing with a page."""
    tid = id_for_install(undo)
    if not tid or get(tid) is None:
        return ""
    set_home(tid, True)
    moved = redirect_for(tid)
    if moved:
        return moved        # an ingredient of a job: no page of its own
    make_recipe(tid)
    from puffpai import agent_activity
    agent_activity.record("toolsmith", f"Made the page for {get(tid)['name']}")
    return PAGE.format(id=tid)


# ── what a page shows ──────────────────────────────────────────────────────

def _tables(ref: str) -> list[dict]:
    from puffpai.app_data import AppData
    from puffpai.integrate import records
    rec = records.get(ref) or {}
    try:
        doc = AppData().get_data(rec.get("list_app_id") or "") or {}
    except Exception:  # noqa: BLE001
        return []
    out = []
    for key, rows in (doc.items() if isinstance(doc, dict) else []):
        if key == "credit" or not isinstance(rows, list):
            continue
        cols: list[str] = []
        for row in rows[:TABLE_ROWS]:
            for c in (row.keys() if isinstance(row, dict) else []):
                if c not in cols and not str(c).startswith("_"):
                    cols.append(str(c))
        out.append({"name": str(key).replace("_", " "), "columns": cols[:12],
                    "rows": [{c: _one_line(r.get(c), 200) for c in cols[:12]}
                             for r in rows[:TABLE_ROWS] if isinstance(r, dict)],
                    "count": len(rows)})
    return out


def page(thing_id: Any) -> Optional[dict]:
    """``GET /things/<id>``: the thing, its recipe (made now if it has none)
    and what its kind of page needs. A live card, app or data set that is an
    ingredient of a job answers ``{"id", "redirect"}``: its job's page."""
    moved = redirect_for(thing_id)
    if moved:
        return {"id": str(thing_id), "redirect": moved}
    thing = get(thing_id)
    if thing is None:
        return None
    recipe = make_recipe(thing["id"]) or fallback_recipe(thing)
    # Its real name, also on a recipe saved before the name was known.
    recipe = {**recipe, "title": thing["name"]}
    if thing["kind"] == "feed" and not _feed_looks_up(thing):
        # A page made before this card's search was known: no dead-end tries.
        kept = [t for t in recipe.get("try") or [] if not _PROMISES_LOOKUP.search(str(t))]
        recipe["try"] = kept or [f"What is new on {thing['name']}?"]
    out = {"id": thing["id"], "kind": thing["kind"], "kind_words": KIND_WORDS[thing["kind"]],
           "name": thing["name"], "icon": recipe.get("icon") or thing["icon"],
           "from": thing["from"], "on_home": _home_choices().get(thing["id"], True),
           "recipe": recipe, "improving": improving(thing["id"])}
    screen = screen_of(thing["id"])
    if screen:
        out["screen"] = screen
    try:
        from puffpai.finding import capabilities
        covers = capabilities.covers(thing["id"])
    except Exception:  # noqa: BLE001 - a page never fails on this
        covers = []
    if covers:
        out["covers"] = covers
    if thing["kind"] == "skill":
        try:
            from puffpai import routines
            daily = routines.results_for_skill(thing["ref"])
        except Exception:  # noqa: BLE001
            daily = []
        if daily:
            out["daily"] = daily
        needs = skill_needs(thing["ref"])
        if needs:
            out["needs"] = needs
    if thing["kind"] == "tool_server":
        from puffpai import mcp_client
        out["tools"] = [{"name": t["name"], "title": _tool_words(t["name"]).capitalize(),
                         "description": re.sub(r"\s*[\u2014\u2013]\s*", ", ",
                                               str(t.get("description") or "")),
                         "input_schema": t.get("input_schema") or {"type": "object"},
                         "has_screen": bool(mcp_client.ui_resource_uri(t.get("meta")))}
                        for t in mcp_client.list_tools(thing["ref"])]
    elif thing.get("builtin"):
        out["open"] = thing["open"]
    elif thing.get("assembled"):
        out["assembled"] = thing["assembled"]
        out["host"] = host_view(thing["ref"])
        try:    # keeps the dashboard's port current for its "Use it here" frame
            from puffpai.assemble.server import gateway
            gateway.ensure_running()
        except Exception:  # noqa: BLE001 - the page still shows
            pass
    elif thing["kind"] == "app":
        from puffpai.integrate import records
        rec = records.get(thing["ref"]) or {}
        app_id = rec.get("app_id") or ""
        out["open"] = f"/dashboard/apps/{app_id}" if app_id else ""
        if rec.get("mode") in HOST_MODES:
            out["host"] = host_view(thing["ref"])
            needs = host_needs(thing["ref"])
            if needs:
                out["needs"] = needs
    elif thing["kind"] == "data":
        out["tables"] = _tables(thing["ref"])
    elif thing["kind"] == "book":
        out["open"] = "/dashboard/knowledge"
    elif thing["kind"] == "feed":
        out.update(_feed_page(thing))
    return out


# ── apps that run on this computer (integrate.run_here) ────────────────────

#: Integration modes that run on this computer, as their authors built them.
HOST_MODES = ("own_server", "cli_tool", "library")


def host_view(integration_id: str) -> dict:
    """What the page shows for an app on this computer: its state, where it
    opens (127.0.0.1 only), whether it starts with the bot, its commands and
    what it asks for. Never a key, never its environment."""
    from puffpai.integrate import run_here
    try:
        now = run_here.status(integration_id)
        plan = (run_here.read_state(integration_id).get("plan") or {})
    except Exception:  # noqa: BLE001 - not set up here (any more)
        return {"state": "missing", "url": "", "can_run": False}
    return {"state": now["state"], "url": now.get("url") or "", "port": now.get("port"),
            "can_run": bool(now.get("can_run")), "wanted_on": bool(now.get("wanted_on")),
            "how_words": now.get("how_words") or "", "why": now.get("why") or "",
            "commands": run_here.shown_lines(plan, integration_id) if plan else [],
            "tools": sorted(run_here.cli_commands(integration_id)),
            "keys": list(plan.get("keys") or []),
            "note": "It runs on your computer with your permissions."}


def _held_missing(integration_id: str) -> list[str]:
    """The programs a held app (not set up yet) waits for, from its yes card."""
    try:
        from puffpai.integrate import apply
        card = apply._load_stash(integration_id).get("host_card") or {}  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return []
    return [str(p) for p in card.get("missing") or []] if isinstance(card, dict) else []


def host_needs(integration_id: str) -> dict:
    """The "What it needs" panel for an app that runs on this computer, the
    same shape as a skill's (:func:`skill_needs`): the programs it is missing
    with "Install what's missing" (``run_here.install_missing``, one yes,
    ``puffpai.deps``), and the keys its yes card named, each with a "Get a
    key" link from its README, kept for this app only. {} when it needs
    nothing. Names only: no value ever leaves the vault for a page."""
    from puffpai.integrate import run_here
    try:
        plan = run_here.read_state(integration_id).get("plan") or {}
        rows = run_here.key_rows(integration_id) if plan else []
    except Exception:  # noqa: BLE001 - not set up here
        plan, rows = {}, []
    missing = run_here.missing_programs(plan) if plan else _held_missing(integration_id)
    if not rows and not missing:
        return {}
    line, install = "", {}
    if missing:
        hook = run_here.install_missing({"integration": integration_id}, missing)
        card = hook.get("card") or {}
        line = str(hook.get("line") or "")
        install = {"plan": str(card.get("plan") or ""), "steps": list(card.get("steps") or []),
                   "manual": list(card.get("manual") or [])}
    if not line:
        unset = [r["name"] for r in rows if not r["set"]]
        line = (f"It asks for {', '.join(unset)}. Keep it here and only this app gets it, "
                f"when it starts." if unset else "It has what it needs.")
    return {"line": line,
            "programs_missing": [run_here.PROGRAM_WORDS.get(p, p) for p in missing],
            "keys": rows, "install": install}


def _assembled_spec(thing: dict) -> dict:
    try:
        from puffpai.assemble import api as assemble_api
        return assemble_api.load_spec(thing.get("assembled")) or {}
    except Exception:  # noqa: BLE001
        return {}


def _assembled_host_action(thing: dict, action: str) -> dict:
    """Start through the Assemble step's own start (its checks, its yes);
    stop through the same host as any app on this computer."""
    from puffpai.integrate import run_here
    if action not in ("start", "stop", "restart"):
        return {"ok": False, "message": "Say start, stop or restart.", "code": "bad"}
    ok, line = True, ""
    if action in ("stop", "restart"):
        try:
            got = run_here.stop(thing["ref"], by="owner")
            ok, line = bool(got.get("ok")), str(got.get("line") or "")
        except run_here.HostError as exc:
            ok, line = False, exc.line
    if ok and action in ("start", "restart"):
        from puffpai.assemble import api as assemble_api
        got = assemble_api.start_assembled(thing["assembled"])
        ok = bool(got.get("ok"))
        line = "It is running." if ok else (str(got.get("reason") or "") or "It did not start.")
    return {"ok": ok, "message": line or ("It is stopped." if ok else "That did not work."),
            "code": "" if ok else "failed", "host": host_view(thing["ref"])}


def _host_app(thing: Optional[dict]) -> Optional[str]:
    """The integration id when *thing* is an app that runs on this computer."""
    if thing is None or thing.get("kind") != "app" or thing.get("builtin"):
        return None
    from puffpai.integrate import records
    rec = records.get(thing["ref"]) or {}
    return thing["ref"] if rec.get("mode") in HOST_MODES else None


def host_action(thing_id: Any, action: Any) -> dict:
    """``POST /things/<id>/host``: start, stop or restart an app that runs on
    this computer. Only for such an app; nothing else is ever started."""
    thing = get(thing_id)
    if thing is None or thing["kind"] != "app":
        return {"ok": False, "message": NO_THING, "code": "unknown"}
    if thing.get("assembled"):
        return _assembled_host_action(thing, str(action or ""))
    from puffpai.integrate import records, run_here
    rec = records.get(thing["ref"]) or {}
    if rec.get("mode") not in HOST_MODES:
        return {"ok": False, "message": NO_THING, "code": "unknown"}
    fn = {"start": run_here.start, "stop": run_here.stop,
          "restart": run_here.restart}.get(str(action or ""))
    if fn is None:
        return {"ok": False, "message": "Say start, stop or restart.", "code": "bad"}
    try:
        got = fn(thing["ref"], by="owner", **({} if action == "stop" else {"wait": 20.0}))
    except run_here.HostError as exc:
        return {"ok": False, "message": exc.line, "code": "failed"}
    return {"ok": bool(got.get("ok")), "message": str(got.get("line") or ""),
            "code": "" if got.get("ok") else "failed", "host": host_view(thing["ref"])}


def host_logs(thing_id: Any, which: Any = "run") -> dict:
    """``GET /things/<id>/host/logs``: its last log lines, secrets hidden."""
    thing = get(thing_id)
    if thing is None or thing["kind"] != "app":
        return {"ok": False, "message": NO_THING, "code": "unknown"}
    from puffpai.integrate import records, run_here
    if not thing.get("assembled") and \
            (records.get(thing["ref"]) or {}).get("mode") not in HOST_MODES:
        return {"ok": False, "message": NO_THING, "code": "unknown"}
    which = "install" if which == "install" else "run"
    return {"ok": True, "which": which, "text": run_here.logs(thing["ref"], which)}


# ── what a skill needs (programs and keys) ─────────────────────────────────

KEY_STORED = "Your key {name} is in your vault. Only {skill} gets it, one command at a time."
KEY_REMOVED = "Your key {name} is out of your vault."


def skill_needs(folder: str) -> dict:
    """The skill page's "needs" panel, or {} when it needs nothing:
    ``{"line", "programs_missing", "keys": [{"name", "set", "prompt", "help",
    "get_url", "from"}], "install": {"plan", "steps", "manual"}}``.

    Keys are the ones it declares and the ones its text names (``from`` says
    which); ``install`` is "Install what's missing": the exact steps one yes
    runs (``puffpai.deps``). Names only: no value ever leaves the vault for
    a page."""
    from puffpai import deps
    try:
        req = skills_mod.requirements_of(folder)
        needs = deps.read_skill(folder)
    except Exception:  # noqa: BLE001
        return {}
    if not any(needs[k] for k in ("programs", "any_programs", "packages", "keys", "skills",
                                  "manual")):
        return {}
    try:
        from puffpai.security import keys
        kept = set(keys.skill_keys_set(folder))
    except Exception:  # noqa: BLE001
        kept = set()
    is_set = {k["name"]: k["name"] in kept for k in needs["keys"]}
    missing = skills_mod.missing_programs(req)
    rows = [{"name": k["name"], "set": is_set[k["name"]], "prompt": k.get("prompt", ""),
             "help": k.get("help", ""), "get_url": k.get("get_url", ""), "from": k["from"]}
            for k in needs["keys"]]
    plan = deps.make_plan(folder, thing=f"skill-{folder.lower()}", needs=needs)
    line = skills_mod.needs_line({**req, "env": [k["name"] for k in needs["keys"]]},
                                 missing, is_set)
    if plan["steps"] and not line:
        line = deps.plan_line(plan)
    shown = deps.public_plan(plan)
    return {"line": line or "It has what it needs.", "programs_missing": missing, "keys": rows,
            "install": {"plan": shown["plan"], "steps": shown["steps"],
                        "manual": shown["manual"]}}


def install_missing(plan_id: Any, *, said_yes: bool, device: str = "") -> dict:
    """``POST /things/install-missing``: her one yes to a plan she was shown
    (on a thing page or a chat card). Runs exactly its steps, each checked by
    Drawbridge first. Answers with the lines, and the page's new "needs"."""
    from puffpai import deps
    plan = deps.get_plan(plan_id)
    if plan is None:
        return {"ok": False, "code": "failed",
                "message": "That list is out of date. Open the page again for the new one."}
    out = deps.run_plan(plan_id, said_yes=said_yes, device=device)
    reply: dict[str, Any] = {"ok": bool(out.get("ok")), "lines": out.get("lines") or [],
                             "message": " ".join(out.get("lines") or [])[-600:]}
    if not out.get("ok"):
        reply["code"] = "failed"
    if plan.get("skill"):
        reply["needs"] = skill_needs(plan["skill"])
    elif plan.get("thing"):
        iid = _host_app(get(plan["thing"]))
        if iid:
            reply["needs"] = host_needs(iid)
            reply["host"] = host_view(iid)
    return reply


def put_key(thing_id: Any, name: Any, value: Any) -> dict:
    """``POST /things/<id>/key``: "Put the key in my vault". Only a key the
    skill names (in its settings or its text, ``puffpai.deps.key_names``),
    only while the vault is open. The value is never sent back, logged or
    shown."""
    thing = get(thing_id)
    iid = _host_app(thing)
    if iid:
        return _put_app_key(thing, iid, name, value)
    if thing is None or thing["kind"] != "skill":
        return {"ok": False, "message": NO_THING, "code": "unknown"}
    from puffpai import deps
    from puffpai.security import keys
    declared = deps.key_names(thing["ref"])
    try:
        keys.put_skill_key(thing["ref"], str(name or ""), value, declared=declared, by="owner")
    except keys.KeyRefused as exc:
        return {"ok": False, "message": str(exc), "code": getattr(exc, "code", "") or "refused"}
    return {"ok": True, "message": KEY_STORED.format(name=name, skill=thing["name"]),
            "needs": skill_needs(thing["ref"])}


APP_KEY_STORED = ("Your key {name} is in your vault. Only {app} gets it, when it starts. "
                  "Restart it to use the key now.")


def _put_app_key(thing: dict, iid: str, name: Any, value: Any) -> dict:
    """"Put the key in my vault" for an app that runs on this computer: only
    a name its yes card listed (``run_here`` plan ``keys``)."""
    from puffpai.integrate import run_here
    from puffpai.security import keys
    try:
        plan = run_here.read_state(iid).get("plan") or {}
    except Exception:  # noqa: BLE001
        plan = {}
    # Only names its yes card listed; a Docker project gets none of her keys.
    declared = [] if plan.get("how") == "compose" else list(plan.get("keys") or [])
    try:
        keys.put_app_key(iid, str(name or ""), value, declared=declared, by="owner")
    except keys.KeyRefused as exc:
        return {"ok": False, "message": str(exc), "code": getattr(exc, "code", "") or "refused"}
    return {"ok": True, "message": APP_KEY_STORED.format(name=name, app=thing["name"]),
            "needs": host_needs(iid)}


def remove_key(thing_id: Any, name: Any) -> dict:
    """``DELETE /things/<id>/key``: take one of its keys out of the vault."""
    thing = get(thing_id)
    iid = _host_app(thing)
    if iid:
        from puffpai.security import keys
        if not keys.remove_app_key(iid, str(name or ""), by="owner"):
            return {"ok": False, "message": "That key is not in your vault.", "code": "unknown"}
        return {"ok": True, "message": KEY_REMOVED.format(name=name), "needs": host_needs(iid)}
    if thing is None or thing["kind"] != "skill":
        return {"ok": False, "message": NO_THING, "code": "unknown"}
    from puffpai.security import keys
    if not keys.remove_skill_key(thing["ref"], str(name or ""), by="owner"):
        return {"ok": False, "message": "That key is not in your vault.", "code": "unknown"}
    return {"ok": True, "message": KEY_REMOVED.format(name=name),
            "needs": skill_needs(thing["ref"])}


def _feed_page(thing: dict) -> dict:
    """A live card's page: its newest items (with links), whether its outside
    service is on, and whether chat can look a subject up on it."""
    from puffpai import feeds
    try:
        state = feeds.card_state(thing["ref"])
        _manifest, feed = feeds._find(thing["ref"])  # noqa: SLF001
        looks_up = bool(feeds.lookup_template(feed))
    except Exception:  # noqa: BLE001
        return {"open": f"/dashboard/apps/{thing['ref']}"}
    card = state.get("card") or {}
    items = [{k: i[k] for k in ("title", "detail", "link") if i.get(k)}
             for i in (card.get("items") or [])[:TABLE_ROWS]
             if isinstance(i, dict)]
    return {"open": f"/dashboard/apps/{thing['ref']}",
            "feed": {"status": state.get("status"), "message": state.get("message") or "",
                     "fetched_at": state.get("fetched_at"), "items": items,
                     "credit": state.get("credit") or "",
                     "allowed": bool(state.get("allowed")),
                     "service_message": state.get("service_message") or "",
                     "how_to_fix": state.get("how_to_fix") or "",
                     "looks_up": looks_up,
                     "tool": feeds.lookup_tool_name(thing["ref"]) if looks_up else ""}}


def run_tool(thing_id: Any, tool: Any, arguments: Any, *, from_app: bool = False) -> dict:
    """The tool server page's Run button: one tool, her values.

    ``{"ok", "result"}``, plus, when the tool has an app screen (MCP Apps),
    ``"ui": {"uri", "html"}`` and ``"app_result"`` (the answer as the screen
    receives it). *from_app*: the page's screen asked for this call and she
    said yes on the page; only a tool visible to apps, and no new screen."""
    thing = get(thing_id)
    if thing is None or thing["kind"] != "tool_server":
        return {"ok": False, "message": NO_THING, "code": "unknown"}
    from puffpai import mcp_client
    names = {t["name"] for t in mcp_client.list_tools(thing["ref"])}
    if str(tool or "") not in names:
        return {"ok": False, "message": f"{thing['name']} has no tool called that.",
                "code": "unknown"}
    args = {str(k): v for k, v in (arguments or {}).items()
            if v not in (None, "")} if isinstance(arguments, dict) else {}
    got = mcp_client.call_tool_with_ui(thing["ref"], str(tool), args, from_app=from_app)
    if got.get("refused"):
        return {"ok": False, "message": got["text"], "code": "failed"}
    out: dict[str, Any] = {"ok": True, "result": got["text"]}
    if got.get("result") is not None:
        out["app_result"] = got["result"]
    if got.get("ui"):
        out["ui"] = {"uri": got["ui"]["uri"], "html": got["ui"]["html"]}
    return out


SKILL_NOTE = ('[IMPORTANT: The owner has invoked the "{name}" skill from its page, '
              "indicating they want you to follow its instructions. The full skill "
              "content is loaded below. Answer the owner's request with it. If it names "
              "another of its files, read it with skill_view(\"{folder}\", path). To do "
              "one of the skill's jobs (look something up, add, change or remove), call "
              "run_command with skill \"{folder}\" and the command: that shows the owner a "
              "Yes or No card on this page, and it runs after their yes. Never write a "
              "command out in your answer and never ask in words whether to run it; call "
              "run_command. When the owner says yes to a command from earlier on this page, "
              "call run_command with it. Keep the answer short and practical.]\n\n{text}")
TOOL_NOTE = ("The owner is on the page of the tool server {name}. Use its tools (their "
             "names start with {prefix}) to answer, and say what they found.")
OTHER_NOTE = "The owner is asking about {name}, one of the things this bot has ({kind})."
FEED_NOTE = ("The owner is on the page of the live card {name}. Its newest items: {items}. "
             "{lookup}Give the links you have.")


def ask_note(thing: dict) -> str:
    """The system note that makes a "Try it" or "Ask" use this thing.

    For a skill it is Hermes's skill invocation message (``agent/skill_commands.py``,
    ``build_skill_invocation_message``, MIT, see THIRD_PARTY_NOTICES.md): the whole
    SKILL.md is loaded, so the answer really comes from the skill."""
    if thing["kind"] == "skill":
        _meta, body = skills_mod.parse_frontmatter(_skill_text(thing["ref"]))
        text = body.strip()
        if len(text) > SOURCE_MAX:
            text = text[:SOURCE_MAX].rsplit("\n", 1)[0] + "\n[cut here: read the rest with skill_view]"
        return SKILL_NOTE.format(name=thing["name"], folder=thing["ref"], text=text)
    if thing["kind"] == "tool_server":
        from puffpai import mcp_client
        return TOOL_NOTE.format(name=thing["name"],
                                prefix=mcp_client.tool_name(thing["ref"], "x")[:-1])
    if thing["kind"] == "feed":
        bits = _feed_page(thing).get("feed") or {}
        rows = "; ".join(
            _one_line(" ".join(x for x in (i.get("title"), f"({i['detail']})" if i.get("detail")
                                         else "", i.get("link")) if x), 240)
            for i in (bits.get("items") or [])[:10]) or "none yet"
        lookup = (f"To look a subject up on it, call {bits['tool']} with the subject. "
                  if bits.get("tool") else "")
        return FEED_NOTE.format(name=thing["name"], items=rows, lookup=lookup)
    return OTHER_NOTE.format(name=thing["name"], kind=KIND_WORDS[thing["kind"]])


__all__ = ["ICONS", "ID_RE", "INGREDIENT_PREFIXES", "JOB_PAGE", "KINDS", "NOT_USED_PAGE",
           "PAGE", "after_install", "ingredient_row", "owning_job", "redirect_for", "ask_note", "brain_recipe",
           "builtin_id", "host_needs", "skill_name",
           "fallback_recipe", "get", "headings", "id_for_install", "listing", "make_recipe",
           "install_missing", "lookup", "page", "put_key", "remove", "remove_key", "run_tool",
           "set_home", "skill_needs"]
