"""What leaves your computer: one honest list of every outside service.

This is the one owner-facing list. Every row is something this build can
reach outside the computer and home network, in plain words:

    {id, name, for, what_leaves, websites, state, state_line, can_switch,
     why_cannot_switch, turned_on_by, turned_on_at, last_used, today}

``state`` is ``on``, ``off``, ``not added`` or ``not switchable``. ``id`` is
for the program only and is never shown.

Where the rows come from
------------------------
* built-in services (weather, air quality, news headlines, reading a web page,
  town lookup) -- ``security.egress_doors.DOORS``;
* live cards -- one row per installed app with a live-data service
  (``feeds``), id ``card-<app id>``;
* the book library (searches, book lookups, downloads, and the mirror sites a
  book names) -- ``source_fetch``;
* updating the lists of free services -- ``catalogs.live``;
* a sample look at a service before it is added -- ``build_probe_get``;
* the internet check while building -- ``wizard.netcheck``;
* looking for and downloading apps, code and pictures, and saving
  the parts an app loads (``code_search``, ``code_download``,
  ``picture_search``, ``picture_download``, ``app_parts``), on as built with
  one-tap off, like the book library;
* rows other parts of BuildBot register (:func:`register_rows`): one per
  browser app and outside site it uses, one per locked-box project. Their
  state is checked here against the real switch, never taken on trust;
* the reading helper download -- ``knowledge.reading_helper.outside_row()`` when
  that module exists, else an honest placeholder;
* brain downloads, which Ollama makes itself -- a fixed row BuildBot cannot
  switch or see;
* web search -- not added (a later add-on with a free sign-up key).

Switching
---------
OFF is always one call (:func:`switch_off`) and needs nothing else. ON
(:func:`switch_on`) is only ever called by a route or command that has just
redeemed a consent ticket (:mod:`puffpai.security.consent_tickets`) minted
with :func:`proposal`'s sentence. There is no PIN: being
paired is the gate, and the ticket binds the yes to exactly this switch.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

TITLE = "What leaves your computer"

#: Plain words for the built-in services. Keyed by the registry id.
_BUILT_IN: dict[str, dict[str, str]] = {
    "weather": {
        "name": "Weather forecast",
        "for": "The weather card on the home page, and weather questions "
               "in chat.",
        "what_leaves": "Your town's rough location (to about 11 km) and your "
                       "internet address.",
    },
    "air_quality": {
        "name": "Air quality",
        "for": "Air quality and pollen on the weather card.",
        "what_leaves": "Your town's rough location (to about 11 km) and your "
                       "internet address.",
    },
    "news_rss": {
        "name": "News headlines",
        "for": "Headlines from the news sites you name.",
        "what_leaves": "A request for the news page you named, and your "
                       "internet address.",
    },
    "web_fetch": {
        "name": "Reading a web page",
        "for": "Reading one page you name, when you ask for it in a chat.",
        "what_leaves": "The address of that page, and your internet address. "
                       "Each read also has to be asked for in that chat.",
    },
    "github": {
        "name": "GitHub",
        "for": "Your own GitHub account, when you connect it: better searches, your "
               "own projects as finds, and a private project when you say yes to it.",
        "what_leaves": "Your GitHub token (only to api.github.com, never in an "
                       "address), the short search words shown on screen, the names "
                       "of your projects, and your internet address.",
    },
    "toolsmith_cloud": {
        "name": "Toolsmith's cloud model (Anthropic)",
        "for": "Toolsmith installs and starts apps faster with Anthropic's model, "
               "when you choose it in Settings. Every other agent stays on this "
               "computer.",
        "what_leaves": "When Toolsmith finds and installs apps: your goal and your "
                       "bot's jobs, the names and descriptions of the apps it found, "
                       "each app's README, docs and file list, the start of its setup "
                       "files, the commands it runs with their output, and what the "
                       "app's pages show, with keys and personal details it spots "
                       "taken out. A folder of your own it is pointed at is read the "
                       "same way. Your Anthropic key goes only to api.anthropic.com. "
                       "Never your chats or vault.",
    },
    "geocode": {
        "name": "Town lookup",
        "for": "Finding your town once, so updates know your time and weather.",
        "what_leaves": "The town name you typed, and your internet address. "
                       "Only a rough position is kept from the answer.",
    },
}

#: Services whose switch lives in ``outside_switches.json`` and whose missing
#: entry means "as built" (on when used).
_OFF_SWITCHES = ("library", "catalog_refresh", "sample_look", "internet_check",
                 "wikipedia_books", "gutenberg_books", "openstax_books",
                 "wikibooks_books", "archive_books", "code_search", "code_download", "picture_search",
                 "picture_download", "app_parts")

#: Rows for finding apps, code and pictures: (id, name, for, what leaves,
#: on line, websites when the "where to look" list names none). On as built,
#: one tap off; the switch is
#: read by ``source_fetch.fetch_small`` / ``get_archive`` before any socket.
_R3_ROWS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("code_search", "Looking for apps and code",
     "Finding apps and code for the jobs you ticked, when you press Start "
     "looking.",
     "Only the short search words shown on screen, and your internet address.",
     "Switched on. Used only when you press Start looking.",
     "the code sites under Where to look"),
    ("code_download", "Downloading an app or code you picked",
     "Downloading an app or code you ticked, at the version shown on your "
     "plan.",
     "The name of the project and your internet address.",
     "Switched on. Used only for something you ticked.",
     "the site the project is kept on"),
    ("picture_search", "Looking for pictures",
     "Finding free pictures for the jobs you ticked, when you press Start "
     "looking.",
     "Only the short search words shown on screen, and your internet address.",
     "Switched on. Used only when you press Start looking.",
     "the picture sites under Where to look"),
    ("picture_download", "Downloading pictures you picked",
     "Downloading the pictures you ticked.",
     "Which pictures, and your internet address.",
     "Switched on. Used only for pictures you ticked.",
     "the site each picture is kept on"),
    ("app_parts", "Saving the parts an app loads",
     "Saving, once, the parts an app you added loads from the internet, so it "
     "runs without fetching them again.",
     "Which parts, once, and your internet address.",
     "Switched on. Used only while an app is being added.",
     "the sites the app loads its parts from"),
)

#: The skill and tool shelves BuildBot searches when it finds things for her
#: bot: (id, name, for, what leaves, on line, websites). On as built, one
#: tap off, like the rows above; the switch is
#: read by ``source_fetch.fetch_small`` before any connection.
SHELF_ROWS: tuple[tuple[str, str, str, str, str, tuple[str, ...]], ...] = (
    ("shelf_clawhub", "ClawHub skill shelf",
     "Finding skills other people wrote, and downloading one you say yes to.",
     "Only the short search words shown on screen, or the name of the skill "
     "you picked, and your internet address.",
     "Switched on. Used only when you look for things or add a skill.",
     ("clawhub.ai",)),
    ("shelf_skills_sh", "skills.sh skill shelf",
     "Finding skills other people wrote, and downloading one you say yes to "
     "from the project it is kept in.",
     "Only the short search words shown on screen, or the name of the skill "
     "you picked, and your internet address.",
     "Switched on. Used only when you look for things or add a skill.",
     ("skills.sh", "api.github.com", "raw.githubusercontent.com")),
    ("shelf_hermes", "Hermes skill list",
     "Reading the Hermes Agent list of skills once a day, and downloading a "
     "skill from it that you say yes to.",
     "A request for the list itself, or the name of the skill you picked, "
     "and your internet address.",
     "Switched on. Used only when you look for things or add a skill.",
     ("nousresearch.github.io", "api.github.com", "raw.githubusercontent.com")),
    ("shelf_mcp_registry", "MCP Registry tool shelf",
     "Finding tool servers (MCP) other people built for bots like yours.",
     "Only the short search words shown on screen, and your internet address.",
     "Switched on. Used only when you look for things.",
     ("registry.modelcontextprotocol.io",)),
)
_OFF_SWITCHES = _OFF_SWITCHES + tuple(r[0] for r in SHELF_ROWS)

#: Row kinds another part of BuildBot may register, and the id shape each
#: must have (``outside_switches.switch_id_app`` / ``switch_id_box``).
_APP_ROW_RE = re.compile(r"^app_[0-9a-f]{24}$")
_BOX_ROW_RE = re.compile(r"^box_[0-9a-f]{24}$")
#: A tool server's row (puffpai.mcp_client.switch_id): switched both ways here.
_MCP_ROW_RE = re.compile(r"^mcp_[a-z][a-z0-9_]{0,23}$")
MCP_LOG_NAME = "A tool server that was removed"
_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,31}$")
_MAX_DYNAMIC_ROWS = 200
_providers_lock = threading.Lock()
_ROW_PROVIDERS: dict[str, Callable[[], list]] = {}

#: Why an app's or a box project's row is not switched ON from this list.
APP_ON_ELSEWHERE = ("Switch this on from the app's own page, where it shows "
                    "what the app sends.")
BOX_ON_ELSEWHERE = ("Switch this on again where the tool was added, where it "
                    "shows what the tool sends.")
NOT_SWITCHABLE_HERE = "This cannot be switched from this list."
#: Log lines from an app or a locked-box tool whose row is gone.
APP_LOG_NAME = "An app you added"
BOX_LOG_NAME = "A tool in the locked box"

#: The book sites the owner says yes to one book at a time. Each has its own
#: row (and its own log lines, see source_fetch.BOOK_SITE_SERVICES): on as
#: built, used only for a book the owner said yes to, one tap off.
_BOOK_SITES: dict[str, dict[str, str]] = {
    "wikipedia_books": {
        "name": "Wikipedia books",
        "for": "Making a book of Wikipedia articles about your bot's subject, "
               "when you say yes to that book.",
        "what_leaves": "The book's subject words and the names of the "
                       "articles it reads, and your internet address. Nothing "
                       "about you.",
        "on_line": "Switched on. Used only for a Wikipedia book you said yes to.",
    },
    "gutenberg_books": {
        "name": "Project Gutenberg books",
        "for": "Finding free old books on your bot's subject and fetching the "
               "one you say yes to.",
        "what_leaves": "The words you search for, or the number of the book "
                       "you chose, and your internet address. Nothing about "
                       "you.",
        "on_line": "Switched on. Used only when you look for a book or add one.",
    },
    "openstax_books": {
        "name": "OpenStax textbooks",
        "for": "Finding free school textbooks and fetching the one you say "
               "yes to.",
        "what_leaves": "A request for the list of textbooks or for the book "
                       "you chose, and your internet address. Nothing about "
                       "you.",
        "on_line": "Switched on. Used only when you look for a book or add one.",
    },
    "wikibooks_books": {
        "name": "Wikibooks",
        "for": "Finding free how-to books on your bot's subject and fetching the "
               "one you say yes to.",
        "what_leaves": "The words you search for, or the pages of the book you "
                       "chose, and your internet address. Nothing about you.",
        "on_line": "Switched on. Used only when you look for a book or add one.",
    },
    "archive_books": {
        "name": "Internet Archive books",
        "for": "Finding old public-domain books on your bot's subject and "
               "fetching the one you say yes to.",
        "what_leaves": "The words you search for, or the id of the book you "
                       "chose, and your internet address. Nothing about you.",
        "on_line": "Switched on. Used only when you look for a book or add one.",
    },
}

#: The rows that are always listed and can never be switched here.
_FIXED = ("reading_helper", "brain_downloads", "web_search")

_ENV_KILLS = {"sample_look": "PUFFPAI_BUILD_PROBES",
              "internet_check": "PUFFPAI_WIZARD_NET_PROBE"}

_CARD_PREFIX = "card-"
_SID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")


class OutsideError(ValueError):
    """A switch that could not happen. ``message`` is plain words."""

    def __init__(self, message: str, code: str = "cannot"):
        super().__init__(message)
        self.message = message
        self.code = code


# ── small readers ────────────────────────────────────────────────────────────

def _posture() -> str:
    try:
        from puffpai.security import egress_doors
        return str(egress_doors.run_posture().get("run_egress") or "island")
    except Exception:
        return "island"


def _bridge_level() -> str:
    """The Bridge as enforced now (bridge_guard.reach_level). Fails closed."""
    try:
        from puffpai.security import bridge_guard
        return bridge_guard.reach_level()
    except Exception:
        return "switched_on"


#: An "as built" row that is off only because the Bridge is up.
BRIDGE_UP_ROW_LINE = ("Off: the Bridge is up, so this is used only after you "
                      "switch it on.")


def _open_posture() -> str:
    from puffpai.security import egress_doors
    return egress_doors.OPEN_POSTURE


def _local_time(iso: Any) -> str:
    """'9:14 am' in this computer's own time, from a stored UTC stamp."""
    try:
        when = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    here = when.astimezone()
    hour = here.hour % 12 or 12
    return f"{hour}:{here.minute:02d} {'am' if here.hour < 12 else 'pm'}"


def _local_day(iso: Any) -> str:
    try:
        when = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    here = when.astimezone()
    today = datetime.now().astimezone().date()
    if here.date() == today:
        return "today"
    return here.strftime("%d %B").lstrip("0")


def _who(by: Any) -> str:
    return {"owner": "you", "wizard": "when your bot was made",
            "installer": "when BuildBot was installed",
            "migration": "when BuildBot was updated"}.get(str(by or ""), "")


def _card_apps() -> list[tuple[dict, dict]]:
    try:
        from puffpai import feeds
        return list(feeds.feed_apps())
    except Exception as exc:
        logger.debug("live cards unavailable for the outside list: %s", exc)
        return []


def _catalog_hosts() -> tuple[list[str], list[str]]:
    """(library hosts, list-refresh hosts) from config/catalog_index.json."""
    library: list[str] = []
    lists: list[str] = []
    try:
        import json
        import os

        from puffpai.config import get_config
        path = os.path.join(get_config().REPO_ROOT, "config", "catalog_index.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for row in data.get("catalogs") or []:
            if not isinstance(row, dict):
                continue
            hosts = [h for h in (row.get("search_hosts") or [])
                     + (row.get("download_hosts") or []) if isinstance(h, str)]
            target = lists if row.get("kind") in ("live_data", "integration") \
                else library
            for host in hosts:
                if host not in target:
                    target.append(host)
    except Exception as exc:
        logger.debug("catalog hosts unavailable: %s", exc)
    return library, lists


# ── rows ─────────────────────────────────────────────────────────────────────

def turned_on_line(by: Any, at: Any = "") -> str:
    """"Switched on by you, today." as one whole sentence, or ''.

    *by* is :func:`_who`'s words ("you", "when your bot was made", ...). The
    page does not write "Switched on by " + by, which would make "Switched on
    by built in." and "Switched on by when your bot was made." A service that
    is on only because BuildBot comes that way has no "by": its state line says
    so.
    """
    who = str(by or "").strip()
    if not who:
        return ""
    when = str(at or "").strip()
    head = "Switched on by you" if who == "you" else f"Switched on {who}"
    return f"{head}, {when}." if when else f"{head}."


#: The start of a row's on-line when it is on only because BuildBot comes
#: that way (nobody switched it on or off).
AS_BUILT_ON = "On as BuildBot comes."


def _as_built_line(on_line: str) -> str:
    rest = on_line[len("Switched on."):].strip()         if on_line.startswith("Switched on.") else ""
    return f"{AS_BUILT_ON} {rest}".strip()


def _row(sid: str, name: str, purpose: str, what_leaves: str, websites: list[str],
         state: str, state_line: str, *, can_switch: bool,
         why_cannot_switch: str = "", turned_on_by: str = "",
         turned_on_at: str = "", kind: str = "") -> dict[str, Any]:
    return {
        "id": sid, "name": name, "for": purpose, "what_leaves": what_leaves,
        "websites": list(websites), "state": state, "state_line": state_line,
        "can_switch": bool(can_switch),
        "switch_to": ("off" if state == "on" else "on") if can_switch else None,
        "why_cannot_switch": why_cannot_switch,
        "turned_on_by": turned_on_by, "turned_on_at": turned_on_at,
        "turned_on_line": (turned_on_line(turned_on_by, turned_on_at)
                           if state == "on" else ""),
        "kind": kind, "last_used": "", "today": {"sent": 0, "stopped": 0},
    }


def _door_row(d: Any) -> dict[str, Any]:
    import os

    from puffpai.security import egress_doors, outside_switches
    # A service another module appends to the registry gets its own words
    # (purpose, data_sent) until it is given plain ones here.
    text = _BUILT_IN.get(d.id) or {
        "name": d.id.replace("_", " ").capitalize(),
        "for": str(d.purpose or ""), "what_leaves": str(d.data_sent or "")}
    websites = list(d.hosts) if not d.dynamic_hosts \
        else ["the address you name"]
    allowed, _why = egress_doors.egress_allowed(d.id)
    decided, _source = egress_doors._switch(d)  # noqa: SLF001 - same family
    posture = _posture()
    env_set = str(os.environ.get(d.env_var) or "").strip() != ""
    stored = outside_switches.entry(d.id) or {}
    by, at = "", ""
    if decided is True:
        if stored.get("on") is True:
            by, at = _who(stored.get("by")), _local_day(stored.get("at"))
        else:
            by = "when your bot was made"
    if allowed:
        state, line = "on", "Switched on."
    elif stored.get("reset") and decided is False:
        state, line = "off", RESET_ROW_LINE
    elif decided is True and posture != _open_posture():
        state = "off"
        line = ("You said yes, but your bot is set to stay off the internet, "
                "so this is off.")
    else:
        state, line = "off", "Switched off."
    can, why = True, ""
    if env_set:
        can, why = False, ("Whoever runs this computer set this in its "
                           "settings file, so it cannot be changed here.")
    elif posture == "sealed":
        can, why = False, ("Your bot is set to keep everything on this "
                           "computer, so this cannot be switched on.")
        state, line = "off", "Off: your bot keeps everything on this computer."
    return _row(d.id, text["name"], text["for"], text["what_leaves"], websites,
                state, line, can_switch=can, why_cannot_switch=why,
                turned_on_by=by if state == "on" else "",
                turned_on_at=at if state == "on" else "", kind="built_in")


def _card_rows() -> list[dict[str, Any]]:
    from puffpai import feeds
    from puffpai.security import egress_doors
    out: list[dict[str, Any]] = []
    posture = _posture()
    for manifest, feed in _card_apps():
        app_id = str(manifest.get("id") or "")
        if not app_id:
            continue
        try:
            state = feeds.service_state(app_id)
        except Exception as exc:
            logger.debug("card %s state unavailable: %s", app_id, exc)
            continue
        name = str(manifest.get("name") or app_id)[:64]
        service = str(state.get("service") or "")
        label = name if service.lower() in ("", name.lower()) \
            else f"{name} ({service})"
        # Its full site; its picture site is reached for pictures only
        # (egress_doors picture_hosts), and said so, not listed as a site.
        data_host = str((feed.get("service") or {}).get("host") or "")
        websites = [h for h in state.get("hosts") or [] if isinstance(h, str)
                    and (h == data_host or not data_host)]
        pictures = [h for h in state.get("hosts") or [] if isinstance(h, str)
                    and data_host and h != data_host]
        on = state.get("state") == "open"
        can, why = True, ""
        if state.get("state") == "switched_off":
            can, why = False, ("Whoever runs this computer switched live cards "
                               "off in its settings, so this cannot be changed "
                               "here.")
        elif posture == "sealed":
            can, why = False, ("Your bot is set to keep everything on this "
                               "computer, so this cannot be switched on.")
        if on:
            line = "Switched on."
        elif state.get("state") == "bot_offline":
            line = ("You said yes, but your bot is set to stay off the "
                    "internet, so this is off.")
        else:
            line = "Switched off."
        by = at = ""
        if on:
            try:
                entry = egress_doors._read_app_consent().get(  # noqa: SLF001
                    egress_doors.app_door(app_id).config_flag) or {}
            except Exception:
                entry = {}
            by, at = "you", _local_day(entry.get("at"))
        out.append(_row(_CARD_PREFIX + app_id, label,
                        f"The live card \"{name}\"."
                        + (f" Pictures only from {', '.join(pictures)}." if pictures else ""),
                        str(state.get("what_leaves") or feeds.what_leaves(feed)),
                        websites, "on" if on else "off", line, can_switch=can,
                        why_cannot_switch=why, turned_on_by=by,
                        turned_on_at=at, kind="card"))
    return out


def _off_switch_row(sid: str, name: str, purpose: str, what_leaves: str,
                    websites: list[str], on_line: str) -> dict[str, Any]:
    import os

    from puffpai.security import outside_switches
    stored = outside_switches.entry(sid) or {}
    env = _ENV_KILLS.get(sid)
    env_off = bool(env) and str(os.environ.get(env) or "").strip().lower() in (
        "off", "0", "no", "false")
    if stored.get("broken"):
        return _row(sid, name, purpose, what_leaves, websites, "off",
                    "Off: the switch file on this computer could not be read.",
                    can_switch=True, kind="as_built")
    if stored.get("reset") and not env_off:
        return _row(sid, name, purpose, what_leaves, websites, "off",
                    RESET_ROW_LINE, can_switch=True, kind="as_built")
    if env_off:
        return _row(sid, name, purpose, what_leaves, websites, "off",
                    "Switched off.", can_switch=False,
                    why_cannot_switch=("Whoever runs this computer set this in "
                                       "its settings, so it cannot be changed "
                                       "here."), kind="as_built")
    if stored.get("on") is False:
        return _row(sid, name, purpose, what_leaves, websites, "off",
                    "Switched off.", can_switch=True, kind="as_built")
    level = _bridge_level()
    if level == "sealed":
        return _row(sid, name, purpose, what_leaves, websites, "off",
                    "Off: your bot keeps everything on this computer.",
                    can_switch=False,
                    why_cannot_switch=("Your bot is set to keep everything on "
                                       "this computer, so this cannot be "
                                       "switched on."), kind="as_built")
    if level == "switched_on" and stored.get("on") is not True:
        return _row(sid, name, purpose, what_leaves, websites, "off",
                    BRIDGE_UP_ROW_LINE, can_switch=True, kind="as_built")
    if stored.get("on") is True:
        by, at = _who(stored.get("by")), _local_day(stored.get("at"))
        line = on_line
    else:
        # Nobody switched it on: it is on because BuildBot comes that way.
        by, at, line = "", "", _as_built_line(on_line)
    return _row(sid, name, purpose, what_leaves, websites, "on", line,
                can_switch=True, turned_on_by=by, turned_on_at=at,
                kind="as_built")


def _reading_helper_row() -> dict[str, Any]:
    """The reading helper's row when it exists, else an honest placeholder."""
    try:
        from puffpai.knowledge import reading_helper  # type: ignore[attr-defined]
        row = reading_helper.outside_row()
        if isinstance(row, dict) and row.get("name") and row.get("what_leaves"):
            state = str(row.get("state") or "not switchable")
            can = bool(row.get("can_switch")) and state in ("on", "off")
            clean = _row("reading_helper", str(row["name"])[:80],
                         str(row.get("for") or "")[:200],
                         str(row["what_leaves"])[:300],
                         [str(h) for h in (row.get("websites") or [])
                          if isinstance(h, str)][:4],
                         state, str(row.get("state_line") or "")[:200],
                         can_switch=can,
                         why_cannot_switch=str(row.get("why_cannot_switch") or "")[:200],
                         turned_on_by=str(row.get("turned_on_by") or "")[:60],
                         turned_on_at=str(row.get("turned_on_at") or "")[:40],
                         kind="reading_helper")
            clean["switch_via"] = row.get("switch_via") or ""
            return clean
    except Exception as exc:  # module absent or row broken: placeholder
        logger.debug("reading helper row unavailable: %s", exc)
    return _row(
        "reading_helper", "Reading helper download",
        "Helps your bot find passages in books by meaning.",
        "If the helper is missing on this computer, the book reader downloads "
        "it once (about 80 MB). Nothing about you is sent.",
        ["chroma-onnx-models.s3.amazonaws.com"], "not switchable",
        "BuildBot cannot see or switch this download yet.", can_switch=False,
        why_cannot_switch="The book reader makes this download itself.",
        kind="fixed")


def _r3_hosts() -> dict[str, list[str]]:
    """Finding service id -> the hosts the "where to look" list names for it."""
    out: dict[str, list[str]] = {}
    try:
        import json
        import os

        from puffpai.config import get_config
        path = os.path.join(get_config().REPO_ROOT, "config", "where_to_look.json")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        for item in data.get("rows") or []:
            if not isinstance(item, dict) or item.get("enabled") is False:
                continue
            sid = item.get("service")
            if sid not in {r[0] for r in _R3_ROWS}:
                continue
            for host in item.get("hosts") or []:
                if isinstance(host, str) and host not in out.setdefault(sid, []):
                    out[sid].append(host)
    except Exception as exc:
        logger.debug("where-to-look hosts unavailable: %s", exc)
    return out


# ── rows other parts of BuildBot register ────────────────────────────────────

def register_rows(name: str, fn: Callable[[], list]) -> None:
    """Add *fn*'s rows to the list. *fn()* returns rows in :func:`rows` shape.

    Registering the same *name* again replaces it. A row is kept only when
    its kind is ``app_web`` or ``box`` and its id has that kind's shape
    (``app_<24 hex>`` / ``box_<24 hex>``), its words are text, and its id is
    not taken; any other row is left out, since its state could not be
    checked. Its state is not taken on trust: an ``app_web`` row is on only
    when ``egress_doors.app_web_hosts`` says one of its sites is on, and a
    ``box`` row is off once its switch (``outside_switches``, id
    ``switch_id_box``) is off. Switching OFF from this list works for an
    ``app_web`` row, and for a ``box`` row whose provider says it can switch
    (``can_switch``: it promises to read that switch before any request).
    Nothing is switched ON from here; that happens where it was added.
    """
    if not isinstance(name, str) or not _PROVIDER_RE.match(name):
        raise ValueError("not a usable name for a list of rows")
    if not callable(fn):
        raise ValueError("rows must come from a function")
    with _providers_lock:
        _ROW_PROVIDERS[name] = fn


def unregister_rows(name: str) -> bool:
    """Stop listing *name*'s rows. True when it was registered."""
    with _providers_lock:
        return _ROW_PROVIDERS.pop(str(name or ""), None) is not None


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _web_row_target(row_id: str) -> Optional[tuple[str, list[str]]]:
    """``(app id, its sites under this row)`` for an ``app_<hash>`` row id:
    the row id is ``switch_id_app(app id, site or the name it is grouped
    under)``. None when no browser app here has such a site."""
    from puffpai.security import egress_doors, outside_switches
    for app_id in egress_doors.web_app_ids():
        d = egress_doors.web_app_door(app_id)
        if d is None:
            continue
        for host in d.hosts:
            labels = host.split(".")
            for i in range(len(labels) - 1):
                key = ".".join(labels[i:])
                if outside_switches.switch_id_app(app_id, key) == row_id:
                    covered = [h for h in d.hosts
                               if h == key or h.endswith("." + key)]
                    return app_id, covered
    return None


def _web_row_for_line(service: str, host: str,
                      by_id: dict[str, dict[str, Any]]) -> Optional[str]:
    """The app row a browser app's log line (service ``app:<id>:web``)
    belongs to, or None."""
    from puffpai.security import egress_doors, outside_switches
    prefix, suffix = egress_doors.APP_DOOR_PREFIX, egress_doors.WEB_DOOR_SUFFIX
    if not (service.startswith(prefix) and service.endswith(suffix)) or not host:
        return None
    app_id = service[len(prefix):-len(suffix)]
    labels = host.split(".")
    for i in range(len(labels) - 1):
        sid = outside_switches.switch_id_app(app_id, ".".join(labels[i:]))
        if sid in by_id:
            return sid
    return None


def _dynamic_row(raw: Any) -> Optional[dict[str, Any]]:
    """One registered row, checked and put in the list's shape, or None."""
    from puffpai.security import egress_doors, outside_switches
    if not isinstance(raw, dict):
        return None
    sid = raw.get("id")
    name = _text(raw.get("name"), 120)
    if not isinstance(sid, str) or not _SID_RE.match(sid) or not name:
        return None
    kind = raw.get("kind")
    if not ((kind == "app_web" and _APP_ROW_RE.match(sid))
            or (kind == "box" and _BOX_ROW_RE.match(sid))
            or (kind == "mcp" and _MCP_ROW_RE.match(sid))):
        # Its state could not be checked against a real switch, and it could
        # not be switched off from the list: showing it would show the owner
        # a state nobody checked. Left out.
        logger.debug("outside list: a registered row was left out: its id "
                     "does not fit its kind")
        return None
    websites = [_text(w, 120) for w in (raw.get("websites") or [])
                if isinstance(w, str) and w.strip()][:8] \
        if isinstance(raw.get("websites"), list) else []
    said = raw.get("state")
    state = said if said in ("on", "off", "not switchable", "not added") \
        else "off"
    line = _text(raw.get("state_line"), 200)
    can, why = False, _text(raw.get("why_cannot_switch"), 200)
    if kind == "app_web":
        target = _web_row_target(sid)
        on = False
        if target is not None:
            app_id, covered = target
            sites = egress_doors.app_web_hosts(app_id)
            on = any(sites.get(h) is True for h in covered)
        state = "on" if on else "off"
        line = "Switched on." if on else "Switched off."
        can, why = on, ("" if on else APP_ON_ELSEWHERE)
    elif kind == "mcp":
        # A tool server she added: its own switch, read here, never trusted
        # from the provider. Off and on both work from this list.
        on = not outside_switches.is_off(sid)
        state = "on" if on else "off"
        line = "Switched on." if on else "Switched off."
        can, why = True, ""
        stored = outside_switches.entry(sid) or {}
        raw = {**raw, "turned_on_by": _who(stored.get("by")),
               "turned_on_at": _local_day(stored.get("at"))}
    else:  # a box project's row
        stored = outside_switches.entry(sid) or {}
        promised = raw.get("can_switch") is True
        if stored.get("on") is False or stored.get("broken") or stored.get("reset"):
            state, line = "off", "Switched off."
            can, why = False, BOX_ON_ELSEWHERE
        elif state == "on":
            line = line or "Switched on."
            can, why = promised, ("" if promised else (why or NOT_SWITCHABLE_HERE))
        else:
            can, why = False, why or BOX_ON_ELSEWHERE
    item = _row(sid, name, _text(raw.get("for"), 300),
                _text(raw.get("what_leaves"), 300), websites, state,
                line or ("Switched on." if state == "on" else "Switched off."),
                can_switch=can, why_cannot_switch="" if can else why,
                turned_on_by=_text(raw.get("turned_on_by"), 60) if state == "on" else "",
                turned_on_at=_text(raw.get("turned_on_at"), 40) if state == "on" else "",
                kind=kind)
    return item


def _mcp_rows() -> list:
    """One row per tool server she added (puffpai.mcp_client)."""
    from puffpai import mcp_client
    return mcp_client.outside_rows()


def _dynamic_rows(taken: set[str]) -> list[dict[str, Any]]:
    """Every registered provider's rows, checked. Never raises."""
    with _providers_lock:
        providers = [("mcp", _mcp_rows)] + list(_ROW_PROVIDERS.items())
    out: list[dict[str, Any]] = []
    for name, fn in providers:
        try:
            got = fn()
        except Exception as exc:  # noqa: BLE001 - one list never breaks the page
            logger.warning("outside list: rows from %s failed: %s", name,
                           type(exc).__name__)
            continue
        if not isinstance(got, list):
            continue
        for raw in got:
            if len(out) >= _MAX_DYNAMIC_ROWS:
                return out
            try:
                item = _dynamic_row(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("outside list: a row from %s failed: %s", name,
                               type(exc).__name__)
                continue
            if item is None or item["id"] in taken:
                continue
            taken.add(item["id"])
            out.append(item)
    return out


def rows() -> list[dict[str, Any]]:
    """Every outside service this build has, in a stable order. Never raises."""
    from puffpai.security import egress_doors
    out: list[dict[str, Any]] = []
    book_hosts: dict[str, list[str]] = {}
    for d in egress_doors.DOORS:
        if d.id in _BOOK_SITES:
            book_hosts[d.id] = list(d.hosts)
            continue
        if d.id == egress_doors.RESULT_PICTURES:
            # Listed, but no switch of its own: each data source's row says
            # "and the picture sites its results point to", and its switch
            # covers it (egress_doors.result_picture_get).
            out.append(_row(
                d.id, "Pictures a data source's results point to",
                "Pictures in the apps your jobs made, from the addresses each data "
                "source's own answer names.",
                "One picture request per picture; that site learns your internet address. "
                "Nothing you typed, no cookie, no key.",
                ["the picture sites each data source's results point to"],
                "off", "Comes with each data source's own switch, and is off when that "
                "one is off.", can_switch=False,
                why_cannot_switch="It is switched with each data source.", kind="as_part"))
            continue
        try:
            out.append(_door_row(d))
        except Exception as exc:
            logger.warning("outside list: %s row failed: %s", d.id, exc)
    try:
        out.extend(_card_rows())
    except Exception as exc:
        logger.warning("outside list: live card rows failed: %s", exc)
    try:
        # The build's other data sources: one switch per host (build.sources).
        from puffpai.build import sources
        have = {r["id"] for r in out}
        out.extend(r for r in sources.outside_rows(_row, _posture(), _open_posture())
                   if r["id"] not in have)
    except Exception as exc:  # noqa: BLE001 - one list never breaks the page
        logger.warning("outside list: data source rows failed: %s", type(exc).__name__)
    library_hosts, list_hosts = _catalog_hosts()
    taken = {h for hosts in book_hosts.values() for h in hosts}
    out.append(_off_switch_row(
        "library", "Book library",
        "Searching for books and downloading the ones you say yes to.",
        "The words you search for, or the name of the book you chose, and "
        "your internet address.",
        [h for h in library_hosts if h not in taken]
        + ["the mirror sites a book names"],
        "Switched on. Used only when you search for a book or add one."))
    for sid, text in _BOOK_SITES.items():
        if sid in book_hosts:  # a site with no registry row is never used
            out.append(_off_switch_row(sid, text["name"], text["for"],
                                       text["what_leaves"], book_hosts[sid],
                                       text["on_line"]))
    out.append(_off_switch_row(
        "catalog_refresh", "Updating the lists of free services",
        "Keeping the lists of free live-info websites and connections current.",
        "A request for the list itself, and your internet address. Nothing "
        "about you or your bot.",
        list_hosts + ["each service on the checked list, when that list is "
                      "checked"],
        "Switched on. Used only when a list is updated."))
    out.append(_off_switch_row(
        "sample_look", "Looking at a service before adding it",
        "One look at a service's sample reply, so you can see what a live card "
        "would show before you say yes.",
        "A request for the service's own sample address, and your internet "
        "address. Nothing about you.",
        ["the service being looked at"],
        "Switched on. Used only while making or adding to your bot."))
    out.append(_off_switch_row(
        "internet_check", "Internet check while making your bot",
        "Seeing whether this computer can reach the internet.",
        "One connection that sends nothing, and your internet address.",
        ["registry.ollama.ai"],
        "Switched on. Used only while making your bot."))
    r3_hosts = _r3_hosts()
    for sid, name, purpose, leaves, on_line, fallback in _R3_ROWS:
        out.append(_off_switch_row(sid, name, purpose, leaves,
                                   r3_hosts.get(sid) or [fallback], on_line))
    for sid, name, purpose, leaves, on_line, sites in SHELF_ROWS:
        out.append(_off_switch_row(sid, name, purpose, leaves, list(sites), on_line))
    taken = {r["id"] for r in out} | set(_FIXED) | {"reading_helper"}
    out.extend(_dynamic_rows(taken))
    helper = _reading_helper_row()
    door_row = next((r for r in out if r["id"] == helper["id"]), None)
    if door_row is None:
        out.append(helper)
    elif helper["kind"] == "reading_helper":
        # The reading helper made it a real switch AND gave it words: keep the
        # switch (the registry row) and use its words.
        for key in ("name", "for", "what_leaves"):
            if helper.get(key):
                door_row[key] = helper[key]
    fixed = []
    fixed.append(_row(
        "brain_downloads", "Brain downloads (Ollama)",
        "Downloading the AI model your bot thinks with.",
        "Ollama downloads models itself, from registry.ollama.ai and its "
        "download sites. BuildBot only asks Ollama to start a download.",
        ["registry.ollama.ai"], "not switchable",
        "BuildBot cannot see or switch Ollama's own downloads.",
        can_switch=False,
        why_cannot_switch="Ollama is a separate program that makes these "
                          "downloads itself.", kind="fixed"))
    fixed.append(_row(
        "web_search", "Web search",
        "Searching the whole web.",
        "Nothing: web search is not added.", [], "not added",
        "Not added. It can be added later with a free sign-up key.",
        can_switch=False, why_cannot_switch="It is not added yet.",
        kind="fixed"))
    # A module that adds one of these as a real service replaces the
    # fixed row rather than listing it twice.
    present = {r["id"] for r in out}
    out.extend(r for r in fixed if r["id"] not in present)
    _add_usage(out)
    return out


def _add_usage(out: list[dict[str, Any]]) -> None:
    """``last_used`` and today's counts from the outside-traffic log."""
    try:
        from puffpai.security import egress_ledger
        lines = egress_ledger.read(24.0, limit=egress_ledger.MAX_READ)
    except Exception:
        return
    by_id = {r["id"]: r for r in out}
    for rec in lines:  # newest first
        row = by_id.get(rec["service"])
        if row is None:
            try:
                found = _web_row_for_line(rec["service"], rec["host"], by_id)
            except Exception:  # noqa: BLE001 - counts are best effort
                found = None
            row = by_id.get(found) if found else None
        if row is None:
            continue
        if not row["last_used"]:
            row["last_used"] = _local_time(rec["t"])
        key = "sent" if rec["outcome"] == "done" else "stopped"
        row["today"][key] += 1


def row(sid: str) -> Optional[dict[str, Any]]:
    """One row by id, or None."""
    key = str(sid or "").strip().lower()
    for item in rows():
        if item["id"] == key:
            return item
    return None


def find(term: Any, *, listing: Optional[list[dict[str, Any]]] = None
         ) -> Optional[dict[str, Any]]:
    """A row by its number in the list (1-based) or by (part of) its name."""
    items = listing if listing is not None else rows()
    text = str(term or "").strip().lower()
    if not text:
        return None
    if text.isdigit():
        index = int(text) - 1
        return items[index] if 0 <= index < len(items) else None
    for item in items:
        if item["id"] == text or item["name"].lower() == text:
            return item
    hits = [i for i in items if text in i["name"].lower()]
    return hits[0] if len(hits) == 1 else None


# ── the sentence, and the switches ───────────────────────────────────────────

def _also_opened(target: str) -> list[str]:
    """Services the owner said yes to that are off only because the bot is
    set to stay off the internet -- widening that setting turns them on too."""
    names: list[str] = []
    for item in rows():
        if item["id"] != target and item["state"] == "off" and \
                item["state_line"].startswith("You said yes"):
            names.append(item["name"])
    return names


def proposal(sid: str) -> str:
    """The server's own sentence for switching *sid* on. Raises OutsideError."""
    item = row(sid)
    if item is None:
        raise OutsideError("There is no outside service by that name.", "unknown")
    if not item["can_switch"]:
        raise OutsideError(item["why_cannot_switch"] or
                           "That cannot be switched here.", "cannot")
    if item["state"] == "on":
        raise OutsideError(f"{item['name']} is already switched on.", "already")
    sites = ", ".join(item["websites"]) or "an outside website"
    text = (f"Switch on {item['name']}. What leaves your computer: "
            f"{item['what_leaves']} It goes to {sites}.")
    if item["kind"] in ("built_in", "card") and _posture() != _open_posture():
        if not _has_posture_record():
            raise OutsideError("This bot has not been set up yet, so nothing "
                               "can be switched on.", "not_set_up")
        also = _also_opened(item["id"])
        text += (" Your bot is set to stay off the internet, so this also "
                 "lets it use the outside services you said yes to")
        text += (f": {', '.join(also)}." if also else ".")
        text += " Every other one stays off."
    text += " You can switch it off again at any time."
    return text


def _has_posture_record() -> bool:
    try:
        from puffpai import build_manifest
        manifest = build_manifest.read_manifest() or {}
    except Exception:
        return False
    return isinstance(manifest.get("posture"), dict) and bool(manifest["posture"])


def _widen_run_setting() -> bool:
    """Island -> 'only the services you said yes to'. Never touches sealed."""
    from puffpai import build_manifest, build_posture
    from puffpai.security import egress_doors
    posture = _posture()
    if posture == _open_posture():
        return False
    if posture == "sealed":
        raise OutsideError("Your bot is set to keep everything on this "
                           "computer, so this cannot be switched on.", "cannot")
    manifest = build_manifest.read_manifest() or {}
    block = manifest.get("posture") if isinstance(manifest.get("posture"),
                                                  dict) else {}
    if not block:
        raise OutsideError("This bot has not been set up yet, so nothing can "
                           "be switched on.", "not_set_up")
    manifest["posture"] = build_posture.posture_manifest_block(
        build_posture.assess_posture(block.get("build_network"),
                                     egress_doors.OPEN_POSTURE,
                                     block.get("enabled_switches") or []))
    build_manifest.write_manifest(manifest)
    egress_doors.reset_cache()
    return True


def _set(switches: Any, sid: str, on: bool, by: str) -> None:
    try:
        switches.set_switch(sid, on, by=by)
    except switches.SwitchesUnreadable as exc:
        raise OutsideError(exc.message, "cannot") from exc


def switch_on(sid: str, *, by: str = "owner") -> dict[str, Any]:
    """Switch *sid* on. ONLY call after a consent ticket for it was redeemed."""
    from puffpai.security import egress_doors, outside_switches
    item = row(sid)
    if item is None:
        raise OutsideError("There is no outside service by that name.", "unknown")
    if not item["can_switch"]:
        raise OutsideError(item["why_cannot_switch"] or
                           "That cannot be switched here.", "cannot")
    if item["kind"] == "built_in":
        # Refuse BEFORE anything is written: a yes that cannot take effect
        # (no run setting yet, or one that keeps everything here) is not kept.
        if _posture() != _open_posture():
            if _posture() == "sealed":
                raise OutsideError("Your bot is set to keep everything on this "
                                   "computer, so this cannot be switched on.", "cannot")
            if not _has_posture_record():
                raise OutsideError("This bot has not been set up yet, so nothing "
                                   "can be switched on.", "not_set_up")
        _set(outside_switches, item["id"], True, by)
        _widen_run_setting()
    elif item["kind"] == "card":
        from puffpai import feeds
        app_id = item["id"][len(_CARD_PREFIX):]
        feeds.sync_doors()
        egress_doors.set_app_door_consent(app_id, True)
        _widen_run_setting()
    elif item["kind"] in ("as_built", "mcp"):
        _set(outside_switches, item["id"], True, by)
    else:
        # An app's site or a locked-box project: switched on where it was
        # added (with its own owner-only yes), never from this list.
        raise OutsideError(item["why_cannot_switch"] or NOT_SWITCHABLE_HERE,
                           "cannot")
    egress_doors.reset_cache()
    _record_consent(item["id"], True, by)
    if item["kind"] == "card":
        # Its first fetch starts now, in the background, so the tile shows
        # data in seconds instead of waiting for the next scheduled look.
        from puffpai import feeds
        feeds.start_first_fetch(item["id"][len(_CARD_PREFIX):])
    return row(sid) or item


def record_yes(sid: str, *, by: str = "owner") -> dict[str, Any]:
    """Before the bot is set up (no run setting recorded yet): keep her yes
    to a built-in service, changing no run setting. ONLY call after a consent
    ticket for it was redeemed. The run setting, when the wizard records it,
    still decides whether it may open."""
    from puffpai.security import egress_doors, outside_switches
    item = row(sid)
    if item is None:
        raise OutsideError("There is no outside service by that name.", "unknown")
    if item["kind"] != "built_in" or not item["can_switch"] or _has_posture_record():
        raise OutsideError(item.get("why_cannot_switch") or NOT_SWITCHABLE_HERE, "cannot")
    _set(outside_switches, item["id"], True, by)
    egress_doors.reset_cache()
    _record_consent(item["id"], True, by)
    return row(sid) or item


def switch_off(sid: str, *, by: str = "owner") -> dict[str, Any]:
    """Switch *sid* off. One call, nothing else needed."""
    from puffpai.security import egress_doors, outside_switches
    item = row(sid)
    if item is None:
        raise OutsideError("There is no outside service by that name.", "unknown")
    if item["kind"] in ("built_in", "as_built", "mcp"):
        _set(outside_switches, item["id"], False, by)
    elif item["kind"] == "app_web":
        _app_web_off(item["id"], by)
    elif item["kind"] == "box" and item["can_switch"]:
        _set(outside_switches, item["id"], False, by)
    elif item["kind"] == "card":
        from puffpai import feeds
        feeds.sync_doors()
        egress_doors.set_app_door_consent(item["id"][len(_CARD_PREFIX):], False)
    else:
        raise OutsideError(item["why_cannot_switch"] or
                           "That cannot be switched here.", "cannot")
    egress_doors.reset_cache()
    _record_consent(item["id"], False, by)
    return row(sid) or item


def _app_web_off(sid: str, by: str) -> None:
    """Switch off every site of an app under the row *sid*. No ticket needed."""
    from puffpai.security import egress_doors
    target = _web_row_target(sid)
    if target is None:
        return  # no such app site here: nothing can go there already
    app_id, covered = target
    if egress_doors.web_app_door(app_id) is None:
        return
    # Names only the sites to switch off: nothing read here is written back.
    done = egress_doors.switch_off_web_hosts(app_id, covered, by=by)
    if not done.get("ok"):
        raise OutsideError(done.get("message") or
                           "That could not be switched off.", "cannot")


def _record_consent(sid: str, on: bool, by: str) -> None:
    try:
        from puffpai.security.consent_gate import get_ledger
        ledger = get_ledger()
        (ledger.approve if on else ledger.deny)(f"outside:{sid}", by=by)
    except Exception as exc:  # the switch is the control; this is the record
        logger.debug("consent record for %s skipped: %s", sid, exc)


# ── the log, in plain words ──────────────────────────────────────────────────

_STOPPED = {
    "switched_off": "stopped: switched off",
    "bot_offline": "stopped: your bot is set to stay off the internet",
    "turned_off_here": "stopped: switched off in this computer's settings",
    "inside_address": "stopped: it pointed inside your home network",
    "network_filter": "stopped: blocked by your network's filter",
    "peer_moved": ("stopped: the connection went somewhere other than the "
                   "website's address"),
    "bridge_up": "stopped: the Bridge is up, and this was not switched on",
    "bridge_proxy": ("stopped: the Bridge is up, and this was going through a "
                     "proxy on this computer, which could pass it on"),
    "not_encrypted": "stopped: not a private connection",
    "not_on_list": "stopped: not a website this service may use",
    "redirected_away": "stopped: the site sent it somewhere else",
    "too_many_redirects": "stopped: the site sent it somewhere else",
    "sign_in_refused": "stopped: not allowed to carry a sign-in",
    "cannot_look_up": "stopped: the website's name could not be found",
    "not_home_network": ("stopped: it was meant for your home network but "
                         "was going to the internet"),
    "through_proxy": ("stopped: it was meant for your home network but was "
                      "going through a proxy, which could pass it on to the "
                      "internet"),
    "too_big": "stopped: it was bigger than allowed",
    "sha_mismatch": ("stopped: the download was not the exact version you "
                     "picked"),
    "host": "stopped: not a website this service may use",
    "redirects": "stopped: the site sent it somewhere else too many times",
    "header_refused": "stopped: it tried to send something extra",
    "key_host": "stopped: your key may not be sent to that website",
    "key_unavailable": "stopped: your key could not be used",
}


#: Log lines from parts of BuildBot that have no row of their own on the
#: list (they cannot be switched here), named plainly instead of "An outside
#: service".
_LOG_ONLY_NAMES: dict[str, str] = {
    "app_runner": "The hidden browser Toolsmith uses to try apps",
    "the_bridge": "Something that was not switched on",
    "box_install": "Setting up the locked box",
    "box_traffic": BOX_LOG_NAME,
}

#: A refusal that means something more exact for one service.
_SERVICE_STOPPED: dict[tuple[str, str], str] = {
    ("app_runner", "not_allowed"): (
        "stopped: the browser's own background call (like updates or search), "
        "which is never let out"),
}


#: The hidden browser's own background calls, stopped as always: shown as
#: one line on the outside log (five such lines read as a worry though
#: every one was stopped).
_QUIET_STOP = ("app_runner", "refused", "not_allowed")
QUIET_STOP_WHAT = "tried {n} background calls of its own (like updates or search)"
QUIET_STOP_WHAT_ONE = "tried 1 background call of its own (like updates or search)"
QUIET_STOP_RESULT = "all stopped, as always: none of them reached the internet"


def _size(num: Any) -> str:
    if not isinstance(num, int):
        return "size not known"
    if num < 1024:
        return "less than 1 KB" if num else "nothing came back"
    if num < 1024 * 1024:
        return f"{round(num / 1024)} KB"
    return f"{num / (1024 * 1024):.1f} MB"


def log_rows(hours: float = 24.0, *, limit: int = 500,
             service: str = "") -> list[dict[str, Any]]:
    """The outside-traffic log in plain words, newest first."""
    from puffpai.security import egress_ledger
    items = rows()
    names = {r["id"]: r["name"] for r in items}
    by_id = {r["id"]: r for r in items}
    out: list[dict[str, Any]] = []
    quiet: dict[str, Any] = {"row": None, "n": 0, "hosts": set()}
    for rec in egress_ledger.read(hours, limit=limit):
        if service and rec["service"] != service:
            continue
        app_row = None
        if rec["service"] not in names:
            try:
                app_row = _web_row_for_line(rec["service"], rec["host"], by_id)
            except Exception:  # noqa: BLE001
                app_row = None
        name = names.get(rec["service"]) or names.get(app_row or "") or (
            APP_LOG_NAME if rec["service"] == "app_web" or (
                rec["service"].startswith("app:") and rec["service"].endswith(":web"))
            else BOX_LOG_NAME if _BOX_ROW_RE.match(rec["service"]) else None) or (
            "A live card that was removed" if rec["service"].startswith(
                _CARD_PREFIX)
            else MCP_LOG_NAME if _MCP_ROW_RE.match(rec["service"])
            else "A home network service" if rec["service"] == "home_network"
            else _LOG_ONLY_NAMES.get(rec["service"]) or "An outside service")
        what = egress_ledger.WHAT.get(rec["what"], "made a request")
        if rec["outcome"] == "done":
            if rec["status"] in ("4xx", "5xx"):
                result = "the website answered with a problem"
            else:
                result = _size(rec["bytes_in"])
        elif rec["outcome"] == "refused":
            result = (_SERVICE_STOPPED.get((rec["service"], rec["code"]))
                      or _STOPPED.get(rec["code"], "stopped: not allowed"))
        else:
            result = "did not answer"
        time_text = _local_time(rec["t"])
        if (rec["service"], rec["outcome"], rec["code"]) == _QUIET_STOP:
            # The hidden browser's own background calls (updates, search):
            # always stopped. One line for all of them, at the newest one's
            # place, so a list of stopped calls does not read as a worry.
            quiet["hosts"].add(rec["host"])
            quiet["n"] += 1
            if quiet["row"] is None:
                quiet["row"] = {"time": time_text, "service": name, "website": "",
                                "what": what, "result": "", "outcome": rec["outcome"],
                                "line": ""}
                out.append(quiet["row"])
            continue
        out.append({
            "time": time_text, "service": name, "website": rec["host"],
            "what": what, "result": result, "outcome": rec["outcome"],
            "line": f"{time_text} - {name} - {what} - {result}",
        })
    row = quiet["row"]
    if row is not None:
        n, sites = quiet["n"], len(quiet["hosts"])
        row["what"] = (QUIET_STOP_WHAT_ONE if n == 1 else QUIET_STOP_WHAT.format(n=n))
        row["result"] = QUIET_STOP_RESULT
        row["website"] = (next(iter(quiet["hosts"])) if sites == 1
                          else f"{sites} websites")
        row["line"] = f"{row['time']} - {row['service']} - {row['what']} - {row['result']}"
    return out


# ── the whole page ───────────────────────────────────────────────────────────

def exceptions(items: Optional[list[dict[str, Any]]] = None) -> list[str]:
    """What this page cannot see, named plainly."""
    out = ["Ollama's own brain downloads: Ollama makes them itself."]
    helper = next((r for r in (items if items is not None else rows())
                   if r["id"] == "reading_helper"), None)
    if helper is None or helper.get("kind") == "fixed":
        out.append("The reading helper download, if the book reader has to "
                   "fetch it.")
    out.append("Installing BuildBot itself (its Python and web page parts).")
    return out


#: A row that is off because a damaged switch list was kept aside.
RESET_ROW_LINE = "Off: switched off to be safe when the switch list was damaged."


def notice(surface: str = "browser") -> tuple[str, bool]:
    """``(words, can_ok)``: what to tell the owner about the switch list.

    ``can_ok`` is True when the owner may hide the note. Hiding it switches
    nothing on.
    """
    from puffpai.security import outside_switches
    try:
        found = outside_switches.reset_notice()
    except Exception:  # noqa: BLE001 - a note must never break the page
        found = None
    if not found:
        return "", False
    if found["kind"] == "unreadable":
        return ("The list of your outside switches on this computer cannot be "
                "read, so every outside service is off, and nothing can be "
                "switched until it can be read again."), False
    if found["seen"]:
        return "", False
    kept = (f" The damaged list was kept as {found['kept_as']}."
            if found["kept_as"] else "")
    words = ("The list of your outside switches on this computer was damaged, "
             "so every outside service was switched off to be safe. Nothing "
             "goes out until you switch it on again here." + kept)
    if surface == "terminal":
        words += " To hide this note: puffpai outside ok"
    return words, True


def acknowledge_notice() -> bool:
    """The owner saw the note. Switches nothing on."""
    from puffpai.security import outside_switches
    try:
        return outside_switches.acknowledge_reset()
    except outside_switches.SwitchesUnreadable as exc:
        raise OutsideError(exc.message, "cannot") from exc


def page(surface: str = "browser") -> dict[str, Any]:
    """Everything the page or the terminal shows. Each gets its own wording."""
    items = rows()
    terminal = surface == "terminal"
    for index, item in enumerate(items, 1):
        item["number"] = index
        if not item["can_switch"]:
            item["how_to_switch"] = ""
        elif terminal:
            item["how_to_switch"] = (f"puffpai outside {item['switch_to']} "
                                     f"{index}")
        else:
            item["how_to_switch"] = ("Switch off" if item["switch_to"] == "off"
                                     else "Switch on")
    on = sum(1 for i in items if i["state"] == "on")
    note, can_ok = notice(surface)
    return {
        "title": TITLE,
        "notice": note,
        "notice_can_ok": can_ok,
        "line": (f"This is every request BuildBot makes to the internet. "
                 f"{on} outside service{'s' if on != 1 else ''} "
                 f"{'are' if on != 1 else 'is'} switched on."),
        "not_seen_line": "BuildBot cannot see or stop these:",
        "not_seen": exceptions(items),
        "home_network_line": ("Things on your home network, like Home "
                              "Assistant, are not listed here: that traffic "
                              "stays in your home."),
        "log_line": ("The log below keeps 30 days on this computer. It never "
                     "keeps what you searched for or typed."),
        "services": items,
    }


__all__ = ["APP_LOG_NAME", "APP_ON_ELSEWHERE", "BOX_LOG_NAME", "BOX_ON_ELSEWHERE",
           "NOT_SWITCHABLE_HERE", "OutsideError", "RESET_ROW_LINE", "TITLE",
           "acknowledge_notice", "exceptions", "find", "log_rows", "notice",
           "page", "proposal", "register_rows", "row", "rows", "switch_off",
           "switch_on", "unregister_rows"]
