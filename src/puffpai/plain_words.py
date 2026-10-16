"""Plain words: the ONE list of words an owner never reads, and actions.

The wizard must not be hard to read or full of unusual lingo. Everything an owner or a builder reads --
terminal lines, browser text, chat sentences, and the commands shown to them
(a command is text a person reads too) -- is checked against this module.
Every test that checks owner text imports it; there is no second list.

:func:`check` returns every violation in a text. It catches:

* the machinery's words: door, posture, tier, surface, manifest, zone (but
  not "time zone"), egress, sealed, island, enumerated, moat, open_yard,
  fenced, drawbridge_up, routing, sourcing, catalog, zim (so ".zim" too),
  shard, scaffold, fastpath, index/indexing (but not "UV index"), endpoint,
  json (but not a ".json" file name), and "https"/"port 443" said as words;
* an internal dotted id ("comms.calendar", "instruments.telescope"); a host
  name (registry.ollama.ai) and a file name (install.cmd) are not ids;
* a bracketed id ("[secure:posture]").

Actions
-------
An ability row may carry ``"action": {"id": str, "args": {}}`` alongside its
``how_to_add``. :func:`render_action` turns it into the words for one place:

* ``"terminal"`` -- the command to type (this is also the row's
  ``how_to_add``, kept for everything that already reads it);
* ``"browser"`` -- which page and which button, or, when the dashboard has no
  button for it yet, that plainly, with the terminal way;
* ``"chat"`` -- one sentence the bot can say, with no command in it.

Like the wizard's fix commands, an action is never a string: the id must be
registered here and every argument must match its declared shape, so a row
cannot put an arbitrary command in front of an owner.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# the one banned list
# ---------------------------------------------------------------------------

#: Base words, each also caught in its usual forms (see _FORMS).
BANNED_WORDS: tuple[str, ...] = (
    "door", "posture", "tier", "surface", "manifest", "zone", "egress",
    "sealed", "island", "enumerated", "moat", "open_yard", "fenced",
    "drawbridge_up", "routing", "sourcing", "catalog", "catalogue", "zim",
    "shard", "scaffold", "fastpath", "index", "endpoint", "json",
)

#: The forms each word is caught in (plurals and the -ed/-ing forms).
_FORMS: dict[str, str] = {
    "door": "doors?",
    "posture": "postures?",
    "tier": "tiers?",
    "surface": "surfaces?",
    "manifest": "manifests?",
    "zone": "zones?",
    "catalog": "catalogs?",
    "catalogue": "catalogues?",
    "shard": "shards?",
    "scaffold": "scaffold(?:s|ed|ing)?",
    "fastpath": "fastpaths?",
    "index": "index(?:es|ed|ing)?|indices",
    "endpoint": "endpoints?",
}

#: Everyday phrases that contain a banned word and are fine.
ALLOWED_PHRASES: tuple[str, ...] = (
    "time zone", "time zones", "timezone", "uv index", "kp index", "k-index",
    "heat index", "air quality index",
)

#: Phrases banned as they stand.
BANNED_PHRASES: tuple[str, ...] = ("port 443",)

_WORD_RES = [(word, re.compile(rf"(?<![A-Za-z_]){_FORMS.get(word, re.escape(word))}"
                               rf"(?![A-Za-z_])", re.IGNORECASE))
             for word in BANNED_WORDS]
#: "json" as a file extension (.json) is a file name, not jargon.
_JSON_FILE = re.compile(r"\.json\b", re.IGNORECASE)
#: "https" said as a word ("uses https"); an address (https://...) is not.
_HTTP_WORD = re.compile(r"(?<![A-Za-z_])https?(?![A-Za-z_:])", re.IGNORECASE)
_ALLOWED_RE = re.compile("|".join(re.escape(p) for p in ALLOWED_PHRASES),
                         re.IGNORECASE)
#: "[secure:posture]", "[kiwix_space_stackexchange]": an id in brackets. It
#: has a separator in it; "[options]" in a usage line is an ordinary word.
_BRACKETED_ID = re.compile(r"\[[a-z0-9]+[_:.][a-z0-9_:.-]*\]")
#: "comms.calendar", "instruments.telescope": an id with a dot in it.
_DOTTED = re.compile(r"(?<![\w./-])([a-z_]+)\.([a-z_]+)(?![\w./-])")
#: The last part of a host name. Any two letters is a country's ending.
_TLDS = frozenset({"com", "org", "net", "ai", "io", "gov", "edu", "local",
                   "dev", "info", "app", "mil", "int", "space", "online",
                   "site", "tech", "store", "cloud", "blog", "news", "live",
                   "world", "wiki", "page", "art", "games", "club", "xyz",
                   "top", "one", "earth", "radio", "museum", "science",
                   "codes", "digital", "network", "systems", "tools",
                   "garden", "music", "photo", "photos", "pics", "social"})
#: A file an owner is told to open or run is named as it is on disk.
_FILE_EXTS = frozenset({"cmd", "sh", "ps1", "env", "md", "txt", "json"})


def _blank_allowed(line: str) -> str:
    """The line with allowed phrases and file names blanked out."""
    line = _ALLOWED_RE.sub(lambda m: " " * len(m.group(0)), line)
    return _JSON_FILE.sub(lambda m: " " * len(m.group(0)), line)


def words_in(text: Any, *, skip: Iterable[str] = ()) -> list[str]:
    """The banned words (by base word) in *text*. Words only, no id rules.

    For scanning a source file, where dotted names are code; *skip* names
    base words that are ordinary code there (``index`` as a loop variable).
    """
    skipped = set(skip)
    found: list[str] = []
    for line in str(text or "").splitlines():
        line = _blank_allowed(line)
        for word, pattern in _WORD_RES:
            if word in skipped:
                continue
            if pattern.search(line) and word not in found:
                found.append(word)
        low = line.lower()
        for phrase in BANNED_PHRASES:
            if phrase in low and phrase not in found:
                found.append(phrase)
        if _HTTP_WORD.search(line) and "https" not in found:
            found.append("https")
    return found


def check(text: Any, *, also: Iterable[str] = ()) -> list[str]:
    """Every violation in *text*, as ``"<what>: <the line it was on>"``.

    *also* adds a caller's own extra phrases (matched case-insensitively
    anywhere): a test that must also keep out an internal id of its own area
    passes it here instead of keeping a second list. Commands are checked
    like any other text.
    """
    extra = [a.lower() for a in also if a]
    found: list[str] = []
    for raw in str(text or "").splitlines():
        line = _blank_allowed(raw)
        shown = raw.strip()
        for word, pattern in _WORD_RES:
            if pattern.search(line):
                found.append(f"{word}: {shown}")
        low = line.lower()
        for phrase in BANNED_PHRASES:
            if phrase in low:
                found.append(f"{phrase}: {shown}")
        if _HTTP_WORD.search(line):
            found.append(f"https as a word: {shown}")
        if _BRACKETED_ID.search(raw):
            found.append(f"bracketed id: {shown}")
        for match in _DOTTED.finditer(raw):
            last = match.group(2)
            if last not in _TLDS | _FILE_EXTS and len(last) != 2:
                found.append(f"dotted id {match.group(0)}: {shown}")
        raw_low = raw.lower()
        for phrase in extra:
            if phrase in raw_low:
                found.append(f"{phrase}: {shown}")
    return found


def is_plain(text: Any, *, also: Iterable[str] = ()) -> bool:
    return not check(text, also=also)


# ---------------------------------------------------------------------------
# actions: one ability, the right words for each place
# ---------------------------------------------------------------------------

SURFACES = ("terminal", "browser", "chat")

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
#: A name shown inside a sentence: printable words, no quotes, brackets or
#: backticks, never words that read like an order.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,.&()'+/:-]{0,79}$")
_NUMBER_RE = re.compile(r"^-?\d{1,12}(?:\.\d{1,6})?(?:e[-+]?\d{1,3})?$")
_ORDERISH = re.compile(r"ignore (?:all |the |any )?(?:previous|prior|above)|"
                       r"system prompt|you are now|https?://|www\.", re.I)


class ActionError(ValueError):
    """An action this build will not put in front of an owner, and why."""


def _terminal_only(command: str) -> dict[str, str]:
    return {"terminal": command,
            "browser": f"There is no button for this yet. In a terminal, "
                       f"type: {command}",
            # Never "from the terminal" in chat: the
            # chat offers a card for what it can fix, and says so plainly here.
            "chat": "There is no button for that in the chat yet."}


def _knowledge_folder(a: dict[str, str]) -> dict[str, str]:
    return {"terminal": "Put the library files in a folder, then run: "
                        "puffpai knowledge sync --folder <that folder>",
            "browser": "On the Knowledge page, use Find more knowledge to add "
                       "a book.",
            "chat": "Open the Knowledge page and use Find more knowledge to "
                    "add a book."}


def _knowledge_search(a: dict[str, str]) -> dict[str, str]:
    return {"terminal": "puffpai sources search <words>",
            "browser": "On the Knowledge page, use Find more knowledge.",
            "chat": "Open the Knowledge page and use Find more knowledge."}


def _knowledge_sync(a: dict[str, str]) -> dict[str, str]:
    return {"terminal": "puffpai knowledge sync",
            "browser": "Nothing to press: your bot finishes learning it the "
                       "next time it starts.",
            "chat": "I finish learning it the next time I start."}


def _setup_outside(a: dict[str, str]) -> dict[str, str]:
    return {"terminal": "Run puffpai setup again and say yes when it asks "
                        "about this outside website",
            "browser": "Open Setup from the menu and say yes when it asks "
                       "about this outside website.",
            "chat": "Run the setup again and say yes when it asks about this "
                    "outside website."}


def _setup_smart_home(a: dict[str, str]) -> dict[str, str]:
    # Only home gadgets need a hub, and most people have none: said as an
    # "if", never as something the bot needs.
    return {"terminal": "If you have Home Assistant (a smart home hub), run "
                        "puffpai setup again and connect it when it asks",
            "browser": "If you have Home Assistant (a smart home hub), open "
                       "Setup from the menu and connect it when it asks.",
            "chat": "If you have Home Assistant (a smart home hub), run the "
                    "setup again and connect it when it asks."}


def _outside_on(a: dict[str, str]) -> dict[str, str]:
    # One built-in outside service, switched on where every switch lives.
    return {"terminal": f'puffpai outside on "{a["name"]}"',
            "browser": f"On the What leaves your computer page, switch on "
                       f"{a['name']}.",
            "chat": f"You can switch on {a['name']} on the What leaves your "
                    f"computer page."}


def _apps_install(a: dict[str, str]) -> dict[str, str]:
    return {"terminal": f"puffpai apps install {a['app']}",
            "browser": f"On the Apps page, press \"Yes, add it\" on {a['name']}.",
            "chat": f"{a['name']} is waiting for your yes on the Apps page."}


def _apps_allow(a: dict[str, str]) -> dict[str, str]:
    return {"terminal": f"puffpai apps allow {a['app']}",
            "browser": f"On the Apps page, press \"Yes, let it use "
                       f"{a['service']}\" on {a['name']}.",
            "chat": f"Say yes to {a['service']} for {a['name']} on the Apps "
                    f"page."}


def _services_on(a: dict[str, str]) -> dict[str, str]:
    out = _terminal_only(f"puffpai services on {a['app']}")
    out["chat"] = (f"This bot is set to stay off the internet, so {a['name']} "
                   f"cannot fetch anything. You can switch it on on the What "
                   f"leaves your computer page.")
    return out


def _routines_daily(a: dict[str, str]) -> dict[str, str]:
    return _terminal_only("puffpai routines on daily_update")


def _routines_reminder(a: dict[str, str]) -> dict[str, str]:
    return _terminal_only('puffpai routines add reminder --at 21:00 '
                          '--say "Water the plants"')


def _routines_watch(a: dict[str, str]) -> dict[str, str]:
    command = f"puffpai routines add watch --feed {a['feed']}"
    if a.get("above"):
        command += f" --above {a['above']}"
    return _terminal_only(command)


def _routines_speak(a: dict[str, str]) -> dict[str, str]:
    return {"terminal": "puffpai routines speak on",
            "browser": "On the Dashboard, switch on \"Read updates out loud\" "
                       "in the \"From your bot\" card.",
            "chat": "On the Dashboard, switch on \"Read updates out loud\" in "
                    "the \"From your bot\" card."}


def _home_set(a: dict[str, str]) -> dict[str, str]:
    out = _terminal_only('puffpai home set "Your Town"')
    # The chat shows a card to type the town on (puffpai.fix_api).
    out["chat"] = "You can type your town on the card here in the chat."
    return out


def _home_set_card(a: dict[str, str]) -> dict[str, str]:
    # A build whose home page carries the weather card asks for the town there.
    return {"terminal": 'puffpai home set "Your Town"',
            "browser": "Type your town on the weather card on the home page.",
            "chat": "Type your town on the weather card on the home page."}


def _search_on(a: dict[str, str]) -> dict[str, str]:
    # The keyless web search (Wikipedia) is one outside service.
    return {"terminal": 'puffpai outside on "Web search"',
            "browser": "On the What leaves your computer page, switch on Web "
                       "search.",
            "chat": "Switch on Web search on the What leaves your computer "
                    "page."}


def _keys_add(a: dict[str, str]) -> dict[str, str]:
    # A key is pasted on the Sign-up keys page or in the terminal,
    # never in chat.
    return {"terminal": f"puffpai keys add {a['key']}",
            "browser": f"On the Sign-up keys page, follow the steps for "
                       f"{a['name']}.",
            "chat": f"Add your {a['name']} key on the Sign-up keys page. Never "
                    f"paste a key here in the chat."}


#: id -> {"args": {name: type}, "optional": {name: type}, "render": fn}.
ACTIONS: dict[str, dict[str, Any]] = {
    "knowledge.folder": {"args": {}, "render": _knowledge_folder},
    "knowledge.search": {"args": {}, "render": _knowledge_search},
    "knowledge.sync": {"args": {}, "render": _knowledge_sync},
    "setup.outside": {"args": {}, "render": _setup_outside},
    "setup.smart_home": {"args": {}, "render": _setup_smart_home},
    "outside.on": {"args": {"name": "name"}, "render": _outside_on},
    "apps.install": {"args": {"app": "token", "name": "name"},
                     "render": _apps_install},
    "apps.allow": {"args": {"app": "token", "name": "name", "service": "name"},
                   "render": _apps_allow},
    "services.on": {"args": {"app": "token", "name": "name"},
                    "render": _services_on},
    "routines.daily_update": {"args": {}, "render": _routines_daily},
    "routines.reminder": {"args": {}, "render": _routines_reminder},
    "routines.watch": {"args": {"feed": "token"},
                       "optional": {"above": "number"},
                       "render": _routines_watch},
    "routines.speak": {"args": {}, "render": _routines_speak},
    "home.set": {"args": {}, "render": _home_set},
    "home.set_card": {"args": {}, "render": _home_set_card},
    "search.on": {"args": {}, "render": _search_on},
    "keys.add": {"args": {"key": "token", "name": "name"}, "render": _keys_add},
}


def _arg_ok(kind: str, value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if kind == "token":
        return bool(_TOKEN_RE.match(value))
    if kind == "number":
        return bool(_NUMBER_RE.match(value))
    if kind == "name":
        return bool(_NAME_RE.match(value)) and not _ORDERISH.search(value)
    return False


def action(action_id: str, **args: Any) -> dict[str, Any]:
    """Build a checked action: ``{"id", "args"}``. Raises :class:`ActionError`."""
    return validate_action({"id": action_id,
                            "args": {k: str(v) for k, v in args.items()
                                     if v is not None}})


def validate_action(act: Any) -> dict[str, Any]:
    """The action made canonical, or :class:`ActionError`."""
    if not isinstance(act, dict):
        raise ActionError("An action is an object, never a line of text.")
    action_id = act.get("id")
    spec = ACTIONS.get(action_id) if isinstance(action_id, str) else None
    if spec is None:
        raise ActionError(f"{action_id!r} is not an action this build has.")
    args = act.get("args") or {}
    if not isinstance(args, dict):
        raise ActionError(f"The arguments for {action_id} are not an object.")
    declared = dict(spec["args"])
    optional = dict(spec.get("optional") or {})
    unknown = set(args) - set(declared) - set(optional)
    if unknown:
        raise ActionError(f"{action_id} does not take {sorted(unknown)}.")
    clean: dict[str, str] = {}
    for name, kind in declared.items():
        if not _arg_ok(kind, args.get(name)):
            raise ActionError(f"{action_id} needs {name} as a plain {kind}.")
        clean[name] = args[name]
    for name, kind in optional.items():
        if name in args:
            if not _arg_ok(kind, args[name]):
                raise ActionError(f"{action_id} needs {name} as a plain {kind}.")
            clean[name] = args[name]
    return {"id": action_id, "args": clean}


def render_action(act: Any, surface: str) -> str:
    """The words for *act* on one surface: ``terminal``, ``browser`` or ``chat``."""
    if surface not in SURFACES:
        raise ActionError(f"{surface!r} is not a place an owner reads.")
    clean = validate_action(act)
    return ACTIONS[clean["id"]]["render"](clean["args"])[surface]


def renderings(act: Any) -> dict[str, str]:
    """All three renderings of *act*."""
    clean = validate_action(act)
    return dict(ACTIONS[clean["id"]]["render"](clean["args"]))


def with_action(row: dict[str, Any], act: Any) -> dict[str, Any]:
    """*row* with ``action`` set and ``how_to_add`` as its terminal rendering.

    ``act`` of ``None`` clears both. An action that does not validate is
    dropped (and ``how_to_add`` with it) rather than shown half-checked.
    """
    out = dict(row)
    if act is None:
        out["action"] = None
        out["how_to_add"] = None
        return out
    try:
        clean = validate_action(act)
    except ActionError:
        out["action"] = None
        out["how_to_add"] = None
        return out
    out["action"] = clean
    out["how_to_add"] = render_action(clean, "terminal")
    return out


__all__ = ["ACTIONS", "ALLOWED_PHRASES", "ActionError", "BANNED_PHRASES",
           "BANNED_WORDS", "SURFACES", "action", "check", "is_plain",
           "render_action", "renderings", "validate_action", "with_action",
           "words_in"]
