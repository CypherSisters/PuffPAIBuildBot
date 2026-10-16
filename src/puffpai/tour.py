"""The welcome tour: the first time the dashboard opens after a build.

Three short steps, then the home page:

1. "Here is what your <Bot> got": a card per thing (the dashboard reads
   ``GET /things``), each with a "put on my home page" tick.
2. "Let's pick your look": 4 to 6 looks from the shipped look lists, each
   wearing a background, picked with theme words read from the goal
   ("a wedding planner bot" -> elegant, flower, rose, pastel...).
3. "Want more?": the Find more search.

Then, if she wants, a short walk around the pages (:func:`walk`): the home
page, a thing's page, Chat, Find more, Looks, the Vault, Brain and Bridge,
Developer and Settings, one plain step each, pointing at each in the sidebar.
Settings starts the walk again.

The tour shows once per build. The build is known by the time its record was
written (``generated_at``); "Show me around again" in Setup asks for it once
more. Nothing here reaches outside the computer.

Routes (a blueprint behind the same key check as every other route)::

    GET  /tour                  show it?, is there a build?, the bot's name and
                                every sentence
    GET  /tour/looks            the looks that fit the bot's theme
    POST /tour/look {id, background}   keep one (one undo step)
    POST /tour/done             seen for this build
    POST /tour/again            show it again on the next visit

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from flask import Blueprint, jsonify, request

bp = Blueprint("tour", __name__)

MIN_LOOKS = 4
MAX_LOOKS = 6

#: (words in a goal, look words that fit it, two backgrounds that fit it).
#: Look words are the describing words the shipped look lists use.
THEMES: tuple[tuple[frozenset, tuple[str, ...], tuple[str, str]], ...] = (
    (frozenset("wedding weddings bride bridal groom marriage engagement anniversary "
               "romance romantic honeymoon".split()),
     ("elegant", "flower", "rose", "bloom", "pastel", "dreamy", "luxury", "soft"),
     ("petals", "soft")),
    (frozenset("finance finances money invest investing investment investments budget "
               "budgeting stock stocks bank banking tax taxes accounting savings".split()),
     ("clean", "serious", "minimal", "office", "work", "calm"),
     ("plain", "soft")),
    (frozenset("printing printer printers 3d maker makers robot robots electronics "
               "code coding computer computers tech programming".split()),
     ("tech", "futuristic", "bold", "coding", "robot", "neon"),
     ("stars", "plain")),
    (frozenset("space astronomy star stars planet planets rocket rockets telescope".split()),
     ("space", "stars", "galaxy", "cosmic", "night", "astronomy"),
     ("stars", "stars")),
    (frozenset("garden gardening plant plants farm farming nature hiking flowers "
               "vegetables".split()),
     ("garden", "nature", "plants", "earthy", "leaves", "forest"),
     ("clouds", "soft")),
    (frozenset("cooking cook recipe recipes baking bake kitchen food meal meals "
               "coffee dinner".split()),
     ("cozy", "baking", "cafe", "warm", "coffee", "bakery"),
     ("soft", "clouds")),
    (frozenset("kid kids child children family baby babies school homework".split()),
     ("playful", "fun", "pastel", "school", "sweet", "candy"),
     ("confetti", "clouds")),
    (frozenset("party parties birthday birthdays event events celebration holiday "
               "holidays".split()),
     ("party", "fun", "playful", "bold", "candy", "sweet"),
     ("confetti", "soft")),
    (frozenset("music band bands song songs guitar piano".split()),
     ("music", "pop", "neon", "bold", "synthwave"),
     ("stars", "soft")),
    (frozenset("game games gaming gamer trivia arcade".split()),
     ("gaming", "arcade", "retro", "neon", "games"),
     ("stars", "plain")),
    (frozenset("book books reading library history writing study studying".split()),
     ("books", "reading", "library", "vintage", "paper", "writing"),
     ("plain", "soft")),
    (frozenset("ocean beach boat boats sailing fishing travel trip trips vacation".split()),
     ("ocean", "sea", "beach", "summer", "water"),
     ("clouds", "soft")),
    (frozenset("fitness health workout workouts running yoga sleep wellness".split()),
     ("calm", "fresh", "soft", "clean"),
     ("clouds", "soft")),
    (frozenset("pet pets cat cats dog dogs".split()),
     ("cozy", "cat", "soft", "playful", "pastel"),
     ("clouds", "soft")),
)

#: Looks shown when the goal names no theme the lists can match.
FAVOURITES = ("tweakcn:modern-minimal", "tweakcn:pastel-dreams", "tweakcn:cosmic-night",
              "tweakcn:sage-garden", "tweakcn:soft-pop", "tweakcn:ocean-breeze")

#: Look words that want the night sky behind them.
_NIGHT_WORDS = frozenset("night dark space stars starry midnight cosmic galaxy".split())

#: Words that name the bot's role, not its subject ("wedding planner bot").
_ROLE_ENDINGS = ("bot", "helper", "assistant", "app")


# -- theme words ---------------------------------------------------------------

def goal_terms(goal: str) -> list[str]:
    from puffpai.looks import find as F
    return F.terms_of(goal)


def theme_of(goal: str) -> Optional[tuple]:
    """The first theme whose words the goal uses, or None."""
    terms = set(goal_terms(goal))
    raw = set(str(goal or "").lower().replace("-", " ").split())
    for theme in THEMES:
        if theme[0] & (terms | raw):
            return theme
    return None


def theme_words(goal: str) -> list[str]:
    """The goal's own words, then the look words its theme brings."""
    out = list(goal_terms(goal))
    theme = theme_of(goal)
    if theme is not None:
        out += [w for w in theme[1] if w not in out]
    return out


def _without_role(phrase: str) -> str:
    words = str(phrase or "").split()
    while words and words[-1].lower() in _ROLE_ENDINGS:
        words.pop()
    return " ".join(words)[:60]


def kind_of(goal: str) -> str:
    """What the bot is about, in the owner's words: "wedding planner"."""
    from puffpai import bot_identity
    try:
        phrase = bot_identity.topic_phrase(goal, form="subject")
    except Exception:  # noqa: BLE001 - a missing phrase just drops the line
        phrase = ""
    return _without_role(phrase)


def looks_line(kind: str, themed: bool) -> str:
    """The bot, picking its own look."""
    if kind and themed:
        return f"I'm your {kind} bot, so I picked looks that fit my theme."
    if kind:
        return f"I'm your {kind} bot. Here are some looks to start with."
    return "Here are some looks to start with."


#: The Find more box's example when the goal names no job and there is no kind.
FIND_EXAMPLE = "For example: track something, plan something, learn something"
#: Words in front of a job that are not the job: "help me", "can you", "please".
_LEAD_IN = frozenset({"please", "help", "helps", "me", "us", "can", "could", "would",
                      "will", "you", "i", "we", "want", "need", "to", "also", "and",
                      "then", "just", "it", "should"})
#: A clause naming the bot itself is the bot, not a job of it ("Build me a ... helper").
_BOT_WORDS = frozenset({"bot", "bots", "robot", "helper", "assistant", "buddy",
                        "companion", "app"})
FIND_EXAMPLE_WORDS = 6


def find_example(goal: str, kind: str = "") -> str:
    """The Find more box's example, from this bot: the first job in the goal
    ("Find models to print"), else its kind, else a neutral line."""
    from puffpai import bot_identity
    for clause in bot_identity._clauses(goal):  # noqa: SLF001 - the one goal reader
        words = list(clause)
        while words and words[0].lower() in _LEAD_IN:
            words.pop(0)
        if len(words) < 2 or not bot_identity.is_doing_word(words[0]):
            continue
        if {w.lower() for w in words} & _BOT_WORDS:
            continue
        lows = [w.lower() for w in words]
        if "and" in lows[1:]:
            words = words[:lows.index("and", 1)]
        words = words[:FIND_EXAMPLE_WORDS]
        if len(words) >= 2:
            return "For example: " + " ".join([words[0].lower(), *words[1:]])
    if kind:
        return f"For example: help with {kind}"
    return FIND_EXAMPLE


# -- looks ---------------------------------------------------------------------

def _is_night(pkg: dict) -> bool:
    return bool(set(pkg.get("words") or []) & _NIGHT_WORDS)


def _fits(pkg: dict, theme: Optional[tuple]) -> bool:
    """A night look fits only a theme with stars in it (or no theme): no
    Cosmic Night for a wedding."""
    return not _is_night(pkg) or theme is None or "stars" in theme[2]


def _background_for(pkg: dict, theme: Optional[tuple], i: int) -> str:
    """The theme's own backgrounds (petals for a wedding), the night sky for
    a night look."""
    if _is_night(pkg):
        return "stars"
    pair = theme[2] if theme is not None else ("clouds", "soft")
    return pair[i % 2]


def _dressed(pkg: dict, background: str) -> dict:
    """*pkg* wearing *background*, or its own when that is too hard to read."""
    from puffpai.looks import package as P
    try:
        return P.with_background(pkg, background)
    except P.LookError:
        return pkg


def tour_looks(goal: str) -> dict:
    """``{"looks": [rows], "themed": bool, "words": [...]}``. 4 to 6 looks."""
    from puffpai.looks import catalog
    from puffpai.looks import find as F
    from puffpai.looks import package as P

    theme = theme_of(goal)
    words = theme_words(goal)
    # The theme's look words first: a long goal's own words would otherwise
    # fill the search and leave one themed look.
    searches = ([" ".join(theme[1])] if theme is not None else []) + \
        ([" ".join(words)] if words else [])
    picked: list[dict] = []
    seen: set[str] = set()
    for search in searches:
        for match in F.word_matches(search, limit=MAX_LOOKS):
            pkg = match["look"]
            if pkg["id"] not in seen and _fits(pkg, theme):
                picked.append(pkg)
                seen.add(pkg["id"])
    themed = bool(picked)
    for look_id in FAVOURITES:
        if len(picked) >= MIN_LOOKS:
            break
        pkg = catalog.get(look_id)
        if pkg and look_id not in seen and _fits(pkg, theme):
            picked.append(pkg)
            seen.add(look_id)
    rows = []
    for i, pkg in enumerate(picked[:MAX_LOOKS]):
        worn = _dressed(pkg, _background_for(pkg, theme, i))
        row = P.summary(worn)
        row["background_label"] = P.BACKGROUND_LABELS.get(worn["background"], "")
        rows.append(row)
    return {"looks": rows, "themed": themed, "words": words}


# -- tour state ----------------------------------------------------------------

def _path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "tour.json")


def _read() -> dict:
    from puffpai import fsutil
    data = fsutil.read_json(_path(), {})
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    from puffpai import fsutil
    os.makedirs(os.path.dirname(_path()), exist_ok=True)
    fsutil.atomic_write_json(_path(), data)


def build_marker() -> str:
    """When this build's record was written, or "" when there is no build."""
    from puffpai import build_manifest
    man = build_manifest.read_manifest() or {}
    at = man.get("generated_at")
    return str(at) if man and at is not None else ""


def should_show() -> bool:
    marker = build_marker()
    if not marker:
        return False
    data = _read()
    return data.get("again") is True or data.get("done_for") != marker


def mark_done() -> None:
    _write({"done_for": build_marker(), "again": False,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})


def show_again() -> None:
    data = _read()
    data["again"] = True
    _write(data)


# -- the walk around the pages ---------------------------------------------------

#: What each step points at: ``rail`` is the sidebar item with the step's
#: page, ``private`` the YOUR PRIVATE STUFF card under it, ``page`` the page
#: itself (no sidebar item of its own), ``spot`` one control on the page, the
#: element marked ``data-walk="<spot>"`` (the dashboard falls back to the
#: sidebar item, then to no ring, while it is not there).
WALK_TARGETS = ("rail", "private", "page", "spot")

#: The controls a ``spot`` step may point at, each a ``data-walk`` mark on a
#: real control in the dashboard.
WALK_SPOTS = ("add-search", "developer-add-own", "vault-unlock", "knowledge-upload",
              "knowledge-link", "knowledge-books", "settings-toolsmith")


def walk(name: str, thing: str = "", thing_page: str = "") -> list[dict]:
    """The page-by-page walk after the welcome tour (and from Settings):
    one short step per part of the dashboard, said for this bot (*name*,
    and its first *thing* when it has one), showing WHERE to go for the key
    jobs: adding tools and apps, adding its own programs, unlocking the
    vault, and giving it documents and books to learn from. ``href`` is the
    page the step opens ("" stays where she is); a ``spot`` step also names
    the control it rings."""
    like = f", like {thing}" if thing else ""
    steps = [
        ("home", "/dashboard", "rail", "", "Your home page",
         f"Each tile is one thing {name} can do. Tap a tile to open it."),
        ("thing", thing_page or "/dashboard", "page", "", "A thing's page",
         f"Every thing has its own page{like}. Try it runs it once, Ask is for "
         "questions about it, On my home page shows or hides its tile, and Remove "
         "takes it out."),
        ("chat", "/dashboard/chat", "rail", "", "Chat",
         f"Type to {name} here. When {name} wants to run a command on this computer, "
         "it shows it to you and waits for your yes."),
        ("find", "/dashboard/add", "spot", "add-search", "Add tools and apps",
         f"Find more is where {name} gets new tools and apps. Say what you want, or "
         "paste a GitHub link. When one fits a job, pick Run it first or Add to the "
         "list. Nothing is added until you say yes."),
        ("own", "/dashboard/developer", "spot", "developer-add-own", "Your own tools",
         "Under Add your own, connect what is already on this computer, like Word or "
         f"Excel, your own programs and your code folders, so {name} can use them."),
        ("parts", "", "private", "", "Vault, Brain and Bridge",
         f"{name} has three parts. The Vault keeps your private things locked with "
         "your PIN. The Brain is the model that thinks, on this computer. The Bridge "
         "is what may go out to the internet, and it starts closed. Nothing leaves "
         "your computer without your consent."),
        ("vault", "/dashboard/vault", "spot", "vault-unlock", "Unlock your vault",
         "Type your PIN and press Unlock vault. It holds your keys and private "
         f"details, and {name} moves them there by itself when it finds them in your "
         f"files. It locks itself after a while, so unlock it here when {name} asks."),
        ("brain", "/dashboard/knowledge", "spot", "knowledge-upload",
         f"{name}'s offline brain",
         f"Under Your files, press Add files or Link a folder. {name} reads them and "
         "keeps learning. Added files go in your setup folder, linked ones stay put, "
         "all on this computer. Keys and private details go to your vault."),
        ("expert", "/dashboard/knowledge", "spot", "knowledge-books", "Make it an expert",
         f"Give {name} books and info about what it does. Under Find more knowledge, "
         f"say what it should know about and press Look. {name} downloads free books "
         "to learn from, once you say yes."),
        ("looks", "/dashboard/looks", "rail", "", "Looks",
         "Change the colors and the background in your own words. You can always "
         "go back."),
        ("settings", "/dashboard/settings", "spot", "settings-toolsmith", "Settings",
         "Your devices and other choices. Toolsmith is the one helper that may use an "
         "online model, only if you choose it: pick Anthropic and set its key here. "
         "Show me around is here too, to see this walk again."),
    ]
    out = []
    for i, h, t, spot, ti, li in steps:
        step = {"id": i, "href": h, "target": t, "title": ti, "line": li}
        if spot:
            step["spot"] = spot
        out.append(step)
    return out


def words(name: str, goal: str, persona: Optional[dict] = None, *,
          thing: str = "", thing_page: str = "") -> dict:
    """Every sentence the tour shows, said by the bot itself: its name, kind
    and intro come from its personality (:mod:`puffpai.persona`) when it has
    one."""
    p = persona if isinstance(persona, dict) else {}
    name = str(p.get("name") or name)
    kind = _without_role(str(p.get("kind") or "")) or kind_of(goal)
    return {
        "bot_name": name,
        "kind": kind,
        "intro": str(p.get("intro") or ""),
        "things_title": "Here is what I just learned",
        "things_line": "Tick what you want on your home page. You can change it later.",
        "things_none": ("I have not learned anything new yet. We can find skills, tools "
                        "and apps in the last step."),
        "looks_title": "Let's pick my look.",
        "looks_line": looks_line(kind, theme_of(f"{goal} {kind}") is not None),
        "more_title": "Want me to learn more? I can search GitHub, the skill shelves "
                      "and the tool lists.",
        "more_line": "Tell me what you want me to do, and I will look.",
        "find_example": find_example(goal, kind),
        "done_title": "All set!",
        "done_line": "Your home page has my things. Nothing leaves your computer "
                     "without your consent.",
        "walk_offer": "Want a short walk around the pages? It shows what each one is for.",
        "walk": walk(name, thing, thing_page),
    }


# -- routes --------------------------------------------------------------------

def _name_and_goal() -> tuple[str, str]:
    from puffpai import bot_identity
    return bot_identity.bot_name(), bot_identity.bot_goal()


def _persona() -> Optional[dict]:
    """The bot's saved personality, or None (never made here)."""
    from puffpai import persona
    return persona.stored()


def _first_thing() -> tuple[str, str]:
    """The name and page of the bot's first thing on its home page, or ("", "")."""
    try:
        from puffpai import things
        rows = things.listing()
    except Exception:  # noqa: BLE001 - the walk just says it without an example
        return "", ""
    for row in rows:
        page = str(row.get("page") or "")
        if row.get("on_home") and row.get("name") and page.startswith("/dashboard/"):
            return str(row["name"])[:60], page
    return "", ""


@bp.route("/tour", methods=["GET"])
def tour_state():
    name, goal = _name_and_goal()
    data = _read()
    thing, page = _first_thing()
    return jsonify({"show": should_show(), "built": bool(build_marker()),
                    "again": data.get("again") is True,
                    **words(name, goal, _persona(), thing=thing, thing_page=page)})


@bp.route("/tour/looks", methods=["GET"])
def tour_looks_route():
    _, goal = _name_and_goal()
    got = tour_looks(goal)
    return jsonify({"ok": True, "looks": got["looks"], "themed": got["themed"]})


@bp.route("/tour/look", methods=["POST"])
def tour_keep_look():
    from puffpai.looks import store
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    got = store.use_wearing(body.get("id"), body.get("background"))
    out = {"ok": got["ok"], "changed": got["changed"], "message": got["message"]}
    out.update(store.state())
    return jsonify(out), (200 if got["ok"] else 400)


@bp.route("/tour/done", methods=["POST"])
def tour_done():
    mark_done()
    return jsonify({"ok": True, "show": False})


@bp.route("/tour/again", methods=["POST"])
def tour_again():
    show_again()
    return jsonify({"ok": True, "show": should_show()})


__all__ = ["WALK_SPOTS", "WALK_TARGETS", "bp", "kind_of", "mark_done", "should_show", "show_again",
           "theme_of", "theme_words", "tour_looks", "walk", "words"]
