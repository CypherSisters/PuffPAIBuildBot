"""Adding things from chat: the bot's own built-in apps first, then the home page.

Three tools for the chat's tool loop (``puffpai.brain_tools``):

* ``list_builtin_apps(words)``: the apps that came with this bot (Calendar,
  lists and so on, from :mod:`puffpai.app_registry`), searched by name, id,
  short description and their own words. A small fuzzy match, so "calander"
  still finds Calendar. The model offers one of these before it searches
  outside.
* ``put_on_home(thing, on=True)``: puts a built-in app, or something she
  installed, on her home page (or takes it off).
* ``arrange_dashboard(action, thing, ...)``: pins, hides, shows, moves or
  renames a home page tile or a menu item, in the one layout file the page
  reads (``dashboard_widgets.arrange``, ``<state>/dashboard_layout.json``).

Both go through the things list (:mod:`puffpai.things`, the API
``GET /things`` and ``POST /things/<id>/home {on}``), so a built-in app she
puts on her home page shows as a tile in "Your <Bot>'s things" next to her
skills, tools and apps.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional

_WORD_RE = re.compile(r"[a-z0-9]+")
_SKIP = frozenset("""a an the my our your me to on in of for and or with can you
please add put make want would like i some page dashboard home screen view app
apps thing new get have show give need""".split())


# ── the bot's own built-in apps ───────────────────────────────────────────────

def _words(text: Any) -> list[str]:
    return _WORD_RE.findall(str(text or "").lower())


def builtin_apps() -> list[dict[str, Any]]:
    """The apps that came with this bot: ``{id, name, blurb, page, words}``."""
    from puffpai import app_registry
    shipped = app_registry._apps_dir()
    out: list[dict[str, Any]] = []
    for m in app_registry.current_manifests():
        app_id = str(m.get("id") or "")
        if not app_id or m.get("kind") == app_registry.WEB_KIND:
            continue
        if not os.path.isfile(os.path.join(shipped, app_id, app_registry.MANIFEST_NAME)):
            continue  # her own app or one added later, not built in
        ui = m.get("ui") if isinstance(m.get("ui"), dict) else {}
        vocab = m.get("vocabulary") if isinstance(m.get("vocabulary"), dict) else {}
        words = set(_words(app_id.replace("_", " ")) + _words(m.get("name"))
                    + _words(m.get("blurb")))
        for kw in vocab.get("keywords") or []:
            words.update(_words(kw))
        out.append({"id": app_id, "name": str(m.get("name") or app_id),
                    "blurb": str(m.get("blurb") or ""),
                    "page": str(ui.get("route") or f"/dashboard/apps/{app_id}"),
                    "words": sorted(words)})
    return out


def _edits(a: str, b: str) -> int:
    """How many letters to add, drop or change to turn *a* into *b*."""
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
    return row[-1]


def _near(a: str, b: str) -> bool:
    """The same word, or a near spelling: one slip in a short word, two in a
    long one ("calander" is "calendar")."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4 or abs(len(a) - len(b)) > 2:
        return False
    return _edits(a, b) <= (1 if min(len(a), len(b)) < 7 else 2)


def _close(word: str, words: list[str]) -> bool:
    return any(_near(word, w) for w in words)


def search_builtin(words: Any) -> list[dict[str, Any]]:
    """Built-in apps that fit *words*, best first. Typos within reason match."""
    asked = [w for w in _words(words) if w not in _SKIP and len(w) > 1]
    if not asked:
        return []
    scored = []
    for app in builtin_apps():
        name_words = _words(app["name"]) + _words(app["id"].replace("_", " "))
        hits = sum(2 if _close(w, name_words) else 1 if _close(w, app["words"]) else 0
                   for w in asked)
        if hits:
            scored.append((-hits, app["name"].lower(), app))
    return [a for _h, _n, a in sorted(scored, key=lambda t: t[:2])]


def find_builtin(ref: Any) -> Optional[dict[str, Any]]:
    """One built-in app by its id or name (a near spelling counts), or None."""
    want = " ".join(_words(ref))
    if not want:
        return None
    apps = builtin_apps()
    for app in apps:
        if want in (app["id"].replace("_", " "), " ".join(_words(app["name"]))):
            return app
    return next((a for a in apps if _near(want, " ".join(_words(a["name"])))
                 or _near(want, a["id"])), None)


# ── the home page ─────────────────────────────────────────────────────────────

def list_things() -> list[dict[str, Any]]:
    """Every thing, as ``GET /things`` gives it."""
    from puffpai import things
    return things.listing()


def _thing_id(ref: str) -> str:
    """The things-list id for *ref*: a thing's id, its name, or the find
    install entry that put it here. "" when there is no such thing."""
    from puffpai import things
    if things.get(ref) is not None:
        return ref
    want = ref.lower()
    for row in things.listing():
        if row["name"].lower() == want:
            return row["id"]
    try:
        from puffpai.finding import install
        entries = install.entries()
    except Exception:  # noqa: BLE001
        return ""
    for e in reversed(entries):
        if want in {str(e.get(k) or "").lower() for k in ("id", "entry", "name")}:
            return things.id_for_install(e.get("undo"))
    return ""


def set_home(thing_id: str, on: bool = True) -> Optional[dict[str, Any]]:
    """``POST /things/<id>/home {on}``: the thing row after the change, or None
    when there is no such thing."""
    from puffpai import things
    tid = _thing_id(str(thing_id or ""))
    if not tid or not things.set_home(tid, on).get("ok"):
        return None
    return next((r for r in things.listing() if r["id"] == tid), None)


def _enable_builtin(app: dict[str, Any]) -> None:
    """Switch a built-in app on for this build: its surfaces join the build's
    page list (the same step a persona she makes takes), so its page shows in
    the dashboard. A build that shows every page is left as it is."""
    from puffpai import app_registry, persona_scaffold
    manifest = next((m for m in app_registry.current_manifests()
                     if m.get("id") == app["id"]), {})
    surfaces = [str(s) for s in manifest.get("surfaces") or [] if isinstance(s, str)]
    if surfaces:
        reason = f"{app['name']}, a built-in app you asked for in chat."
        persona_scaffold._update_build_surfaces(add={s: reason for s in surfaces})


def put_on_home(ref: Any, on: bool = True) -> dict[str, Any]:
    """Put a built-in app or an installed thing on the home page (or take it
    off). ``{ok, line, name, page}``."""
    ref = " ".join(str(ref or "").split())[:128]
    app = find_builtin(ref)
    if app is not None:
        from puffpai import things
        if on:
            _enable_builtin(app)
        things.set_home(things.builtin_id(app["id"]), on)
        return {"ok": True, "name": app["name"], "page": app["page"],
                "line": (f"{app['name']} is on your home page now. Tap its tile to open it."
                         if on else f"{app['name']} is off your home page. It is still in "
                                    "the menu.")}
    row = set_home(ref, on) if ref else None
    if row is None:
        return {"ok": False, "name": ref, "page": "",
                "line": f"I could not find \"{ref}\" among the apps and things on this bot."}
    name = str(row.get("name") or ref)
    page = str(row.get("page") or f"/dashboard/things/{row.get('id') or ref}")
    return {"ok": True, "name": name, "page": page,
            "line": (f"{name} is on your home page now. Tap its tile to open it." if on
                     else f"{name} is off your home page. Say so and I will put it back.")}


# ── the tools ─────────────────────────────────────────────────────────────────

LIST_DESCRIPTION = (
    "Search the apps that came built into this bot (like Calendar or lists) by a few "
    "plain words. Use it first whenever the owner asks to add something, before "
    "find_things. Spelling mistakes are fine.")
LIST_SCHEMA = {
    "type": "object",
    "properties": {"words": {"type": "string",
                             "description": "A few words about what she wants, e.g. "
                                            "'calendar'"}},
    "required": ["words"],
}
HOME_DESCRIPTION = (
    "Put a built-in app (its id or name, e.g. 'calendar') or something installed (its "
    "id) on the owner's home page, or take it off with on=false. Use it after she says "
    "yes to a built-in app, and after something she picked is installed. Then tell her "
    "it is on her home page (never a page address).")
HOME_SCHEMA = {
    "type": "object",
    "properties": {"thing": {"type": "string",
                             "description": "A built-in app's id or name, or an installed "
                                            "thing's id."},
                   "on": {"type": "boolean",
                          "description": "True to put it on the home page, false to take "
                                         "it off."}},
    "required": ["thing"],
}


ARRANGE_DESCRIPTION = (
    "Change how the owner's home page or menu looks: pin, unpin, hide, show, move or "
    "rename a tile or a menu item, by the name she sees. Use it when she asks, e.g. "
    "'put the world news map first' (move, position first), 'hide cameras' (hide), "
    "'call Chat Talk' (rename). Then say what changed in one short line.")
ARRANGE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string",
                   "enum": ["pin", "unpin", "hide", "show", "move", "rename"]},
        "thing": {"type": "string",
                  "description": "The tile or menu item, by the name she sees."},
        "place": {"type": "string", "enum": ["home", "sidebar"],
                  "description": "home for a home page tile, sidebar for a menu item. "
                                 "Leave it out when unsure."},
        "position": {"type": "string",
                     "description": "For move: first, last, or a number from 1."},
        "name": {"type": "string",
                 "description": "For rename: the new name (empty gives the own name "
                                "back)."},
    },
    "required": ["action", "thing"],
}


def tool_arrange_dashboard(args: dict, _ctx: Any = None) -> dict:
    from puffpai import dashboard_widgets
    args = args or {}
    out = dashboard_widgets.arrange(
        str(args.get("action") or ""), " ".join(str(args.get("thing") or "").split())[:128],
        place=str(args.get("place") or ""), position=args.get("position"),
        name=str(args.get("name") or ""), by="brain")
    return {"ok": out["ok"], "said": out["line"]}


def tool_list_builtin_apps(args: dict, _ctx: Any = None) -> str:
    from puffpai import add_more
    found = search_builtin((args or {}).get("words"))
    bot = add_more.bot_name()
    if not found:
        return (f"{bot} has no built-in app for that. Use find_things to look for "
                f"something other people built.")
    lines = [f"{bot} already has these built in (offer them first, then look outside "
             f"with find_things if she wants more):"]
    for app in found[:5]:
        lines.append(f"[{app['id']}] {app['name']}: {app['blurb']}")
    return "\n".join(lines)


def tool_put_on_home(args: dict, _ctx: Any = None) -> dict:
    args = args or {}
    on = args.get("on")
    out = put_on_home(args.get("thing"), on is not False)
    return {"ok": out["ok"], "said": out["line"]}


def register_all() -> None:
    """Register the tools (registering again replaces them)."""
    from puffpai import brain_tools
    brain_tools.register("list_builtin_apps", LIST_DESCRIPTION, LIST_SCHEMA,
                         tool_list_builtin_apps, progress="Looking at the built-in apps ...")
    brain_tools.register("put_on_home", HOME_DESCRIPTION, HOME_SCHEMA, tool_put_on_home,
                         progress="Putting it on your home page ...")
    brain_tools.register("arrange_dashboard", ARRANGE_DESCRIPTION, ARRANGE_SCHEMA,
                         tool_arrange_dashboard, progress="Arranging your home page ...")


__all__ = ["builtin_apps", "find_builtin", "list_things", "put_on_home",
           "register_all", "search_builtin", "set_home", "tool_arrange_dashboard",
           "tool_list_builtin_apps", "tool_put_on_home"]
