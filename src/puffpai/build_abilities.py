"""What this build can really do, read from the running build - one list.

A test asked a bot built for "space ... and works with my
smart telescope" what was in the sky tonight, and was offered "Would you like
to set up the telescope now?". Nothing in this tree talks to a telescope. The
bot's own "what can you do?" answer was wrong the same way, because it read the
build MANIFEST (what the wizard wrote down) and not the build that was running:
the manifest said "weather, web_fetch"; the running config had web_fetch shut.

BuildBot makes ANY bot, so nothing here knows about telescopes, cars or
gardens. This module is the one place that answers "what can this build do?",
from the RUNTIME:

* the outside services that are really open (``egress_doors.door_states()``),
* the apps that are really installed and loaded (``app_registry``, trimmed to
  the build's page list when the wizard trimmed it),
* the offline library that is really indexed (the knowledge pack index),
* generic abilities that are ABSENT unless a provider says otherwise: messages
  on a schedule, smart-home control, live information, web search, reading a
  web page in chat,
* pages on the build's list that nothing backs (a page is a label and an icon;
  it is only an ability when an app or a row stands behind it),
* things the owner's GOAL asks the bot to work with that no row covers. These
  are computed from the goal sentence ("works with my smart telescope" ->
  "Work with your smart telescope: not connected yet"), never from a topic
  table.

Every other reader goes through here: ``self_knowledge`` (the "what can you do"
answer and ``can()``), ``build_context`` (the "things you cannot do" lines the
model is given), ``honesty`` (the correction added when a reply offers
something this build cannot do), the canned workflows answer, and the wizard's
surfaces and handoff cards.

The row shape (shared with the other parts)
-------------------------------------------
Every row is a plain dict::

    {"id": str,                     # stable, e.g. "live_data", "outside:weather"
     "label": str,                  # plain words, a verb phrase an owner reads:
                                    #   "Search the web". Never an internal id.
     "status": "available" | "not_set_up" | "not_in_this_build" | "unknown",
     "how_to_add": str | None}      # a real command or page that works TODAY,
                                    #   or None when there is no way yet

A row may also carry ``"action": {"id": str, "args": {}}``: the way to
add it, as a registered action that :func:`puffpai.plain_words.render_action`
words for the terminal, the browser or chat. ``how_to_add`` is then the
terminal wording, kept for everything that already reads it.

``"unknown"`` is never treated as available. ``"not_set_up"`` means the build
has the code and the owner has not switched it on; ``"not_in_this_build"``
means there is no code for it yet.

Plugging in another part (provider hook)
----------------------------------------
A part that adds an ability (routines, live data, a device adapter) exposes a
function that takes no arguments and returns a list of rows, and registers it::

    from puffpai import build_abilities
    build_abilities.register_provider(my_part.ability_rows)

A provider row whose ``id`` matches a core row REPLACES it: the routines part
returns ``{"id": "scheduled_messages", "status": "available", ...}`` and the
"not in this build" row is gone. New ids are appended. One convenience rule: an
AVAILABLE provider row whose id starts with ``live:`` (one live source) removes
the generic ``live_data`` row, because "no live information at all" would then
be false; the live source row speaks for itself. A provider that raises is
skipped and logged; its rows are simply absent and the core rows (the
conservative ones) stay. A malformed row is dropped; a row with an unknown
status becomes ``"unknown"``.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

AVAILABLE = "available"
NOT_SET_UP = "not_set_up"
NOT_IN_THIS_BUILD = "not_in_this_build"
UNKNOWN = "unknown"
STATUSES = (AVAILABLE, NOT_SET_UP, NOT_IN_THIS_BUILD, UNKNOWN)

#: Statuses that mean "the bot cannot do this right now".
ABSENT = (NOT_SET_UP, NOT_IN_THIS_BUILD, UNKNOWN)

#: Pages (the wizard's page taxonomy) that core code, not an app, stands
#: behind, and the row that says whether it works. Every other page is backed
#: only by an installed app that declares it. Generic abilities, no topics.
PAGE_ABILITIES: dict[str, str] = {
    "knowledge.library": "knowledge_library",
    "daily.weather": "outside:weather",
    "house.lights": "device_control",
    "house.climate": "device_control",
    "house.sensors": "device_control",
    "house.cameras": "device_control",
}

#: What an owner reads for each outside service this build ships. Plain words;
#: the internal ids stay internal. A service another part adds gets a generic
#: label from its host until it names itself.
_DOOR_LABELS: dict[str, str] = {
    "weather": "Get the weather forecast for your town (no sign-up needed)",
    "air_quality": "Get air quality readings from an air-quality website",
    "news_rss": "Show news headlines from a news feed you name",
}

#: The words people use for each GENERIC ability. Used to put the abilities the
#: owner's goal talks about first, and by ``honesty`` to spot an offer of one.
#: Ability vocabulary, not topics: nothing here is about space or cars.
ABILITY_TERMS: dict[str, tuple[str, ...]] = {
    "scheduled_messages": ("daily", "weekly", "every", "remind", "reminder",
                           "reminders", "update", "updates", "schedule",
                           "morning", "alert", "alerts", "notify"),
    "live_data": ("live", "latest", "today", "tonight", "current", "news",
                  "new", "picture", "pictures", "photo", "photos", "show",
                  "shows", "cool"),
    "web_search": ("search", "web", "internet", "online", "google"),
    "web_page": ("website", "websites", "site", "page", "link", "links"),
    # No "smart": a "smart telescope" is the owner's own thing (a goal row),
    # not a home hub's lights.
    "device_control": ("light", "lights", "lamp", "plug", "plugs", "switch",
                       "thermostat", "lock", "locks", "device", "devices",
                       "home"),
    "knowledge_library": ("know", "knows", "learn", "fact", "facts",
                          "everything", "library", "book", "books", "tips",
                          "guide", "guides"),
}

#: Short words that never name a thing.
_STOP = frozenset("""
a an the and or of to in on at for with from by as is are be it its this that
these those my your our their me you i we us them can could will would should
may might do does did so if but not no yes like just also into over under up
out about any all some more most other own same than too very one two things
thing stuff lot lots
""".split())

Row = dict[str, Any]
Provider = Callable[[], Iterable[Row]]

_PROVIDERS: list[Provider] = []


# ---------------------------------------------------------------------------
# the provider hook
# ---------------------------------------------------------------------------

def register_provider(provider: Provider) -> None:
    """Add a part's row provider. Registering the same callable twice is a no-op."""
    if callable(provider) and provider not in _PROVIDERS:
        _PROVIDERS.append(provider)


def unregister_provider(provider: Provider) -> None:
    """Remove a provider (tests, or a part switching itself off)."""
    if provider in _PROVIDERS:
        _PROVIDERS.remove(provider)


def providers() -> tuple[Provider, ...]:
    return tuple(_PROVIDERS)


def row(ability_id: str, label: str, status: str,
        how_to_add: Optional[str] = None, *, note: Optional[str] = None,
        action: Optional[dict[str, Any]] = None) -> Row:
    """One row in the shared shape. An unknown status becomes ``"unknown"``.

    ``note`` is the row's state in plain words when the status alone would
    say the wrong thing ("being learned now" is not "not switched on yet").

    ``action`` (``{"id", "args"}``, see :mod:`puffpai.plain_words`) is the way
    to add it, so each place can word it its own way; the row then carries
    ``"action"`` and its ``how_to_add`` is the action's terminal wording. An
    action that does not check out is left off, and ``how_to_add`` stays as
    given.
    """
    out = {"id": str(ability_id), "label": str(label),
           "status": status if status in STATUSES else UNKNOWN,
           "how_to_add": str(how_to_add) if how_to_add else None}
    if action is not None:
        from puffpai import plain_words
        try:
            clean = plain_words.validate_action(action)
        except plain_words.ActionError:
            logger.debug("ability %s: action refused", ability_id)
        else:
            out["action"] = clean
            out["how_to_add"] = plain_words.render_action(clean, "terminal")
    if note:
        out["note"] = str(note)
    return out


def _clean(raw: Any) -> Optional[Row]:
    """A provider row made safe, or None when it cannot be one."""
    if not isinstance(raw, dict):
        return None
    ability_id = str(raw.get("id") or "").strip()
    label = str(raw.get("label") or "").strip()
    if not ability_id or not label:
        return None
    note = raw.get("note")
    act = raw.get("action")
    return row(ability_id, label, str(raw.get("status") or ""),
               raw.get("how_to_add") or None,
               note=note if isinstance(note, str) else None,
               action=act if isinstance(act, dict) else None)


# ---------------------------------------------------------------------------
# words
# ---------------------------------------------------------------------------

def words(text: Any) -> list[str]:
    """Lower-case words of ``text``, in order."""
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _stem(word: str) -> str:
    """A crude singular, so "lights" meets "light". Good enough for matching."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def terms(r: Row) -> set[str]:
    """The words that name a row's ability, stemmed.

    A generic core ability uses :data:`ABILITY_TERMS`; every other row (a page,
    a goal thing, a provider's row) uses the content words of its own label.
    That is what keeps the matching generic: a new row brings its own words.
    """
    rid = str(r.get("id") or "")
    if rid in ABILITY_TERMS:
        return {_stem(w) for w in ABILITY_TERMS[rid]}
    return label_terms(r)


def label_terms(r: Row) -> set[str]:
    """The content words of a row's own label, stemmed, minus its first verb."""
    label = re.sub(r"\([^)]*\)", " ", str(r.get("label") or ""))
    out = {_stem(w) for w in words(label) if w not in _STOP and len(w) > 2}
    # The verb that starts every label ("Work with", "Use", "Get") names no
    # thing; leave it out so it cannot match on its own.
    first = words(label)[:1]
    if first:
        out.discard(_stem(first[0]))
    return out


# ---------------------------------------------------------------------------
# reading the runtime
# ---------------------------------------------------------------------------

def _manifest() -> dict[str, Any]:
    try:
        from puffpai import build_manifest
        return build_manifest.read_manifest() or {}
    except Exception:
        return {}


def _config() -> Any:
    try:
        from puffpai.config import get_config
        return get_config()
    except Exception:
        return None


def goal_of(man: Optional[dict[str, Any]] = None) -> str:
    """The owner's goal sentence for this build, or "".

    A record passed in speaks for itself; with none, the build on disk is read
    (its record, then the app the owner made).
    """
    given = man is not None
    man = _manifest() if man is None else man
    persona = man.get("persona") if isinstance(man, dict) else None
    if isinstance(persona, dict) and isinstance(persona.get("goal"), str):
        return persona["goal"].strip()
    if given:
        return ""
    try:
        from puffpai import build_context
        return build_context.collect_goal()
    except Exception:
        return ""


def _door_rows() -> list[Row]:
    """One row per outside service the runtime knows, open or shut."""
    try:
        from puffpai.security import egress_doors
        states = egress_doors.door_states()
        switches = {d.id: d.posture_switch for d in egress_doors.DOORS}
    except Exception:
        logger.debug("door states unreadable", exc_info=True)
        return [row("outside:" + d, label, UNKNOWN)
                for d, label in _DOOR_LABELS.items()]

    out: list[Row] = []
    for door in states.get("doors") or []:
        door_id = str(door.get("id") or "")
        if not door_id or door_id == "web_fetch":
            # web_fetch has its own row (web_page): an open door is not an
            # ability when chat has no way to use it.
            continue
        label = _DOOR_LABELS.get(door_id)
        if not label:
            hosts = [h for h in (door.get("hosts") or []) if "(" not in str(h)]
            label = (f"Use the outside website {hosts[0]}" if hosts
                     else "Use an outside website")
        if door.get("open") is True:
            if door_id in _NEEDS_TOWN and not _town_set():
                # Switched on, but there is no town to ask about yet.
                out.append(row("outside:" + door_id, label, NOT_SET_UP,
                               note="switched on, and it needs your home "
                                    "town first",
                               action=_town_action()))
                continue
            out.append(row("outside:" + door_id, label, AVAILABLE))
            continue
        out.append(row("outside:" + door_id, label, NOT_SET_UP,
                       action=_switch_on_action(door_id, switches)))
    return out


#: Built-in services that ask about the owner's town (its rough position).
_NEEDS_TOWN = frozenset({"weather", "air_quality"})


def _town_set() -> bool:
    try:
        from puffpai import home_location
        return home_location.is_set()
    except Exception:
        return False


def _town_action() -> dict[str, Any]:
    try:
        from puffpai import weather_service
        card = weather_service.has_card()
    except Exception:
        card = False
    return {"id": "home.set_card" if card else "home.set", "args": {}}


def _switch_on_action(door_id: str, switches: dict[str, Any]
                      ) -> Optional[dict[str, Any]]:
    """Where this service is switched on: its own switch on the What leaves
    your computer page, named as that page names it. A service with no page
    name keeps the older "say yes in setup" way when setup has a switch."""
    try:
        from puffpai import outside_services
        name = str((outside_services._BUILT_IN.get(door_id)  # noqa: SLF001
                    or {}).get("name") or "")
    except Exception:
        name = ""
    if name:
        return {"id": "outside.on", "args": {"name": name}}
    return {"id": "setup.outside", "args": {}} if switches.get(door_id) else None


LIBRARY_LABEL = "Look things up in an offline library on this computer"


def library_row(records: list[dict[str, Any]], how: Any) -> Row:
    """The offline library row from the pack index's records.

    ``how`` is the way to add books: an action (``{"id", "args"}``, see
    :mod:`puffpai.plain_words`) or, for older callers, a terminal line.

    Learned books make it available. A book still being learned says so
    rather than "none added yet" (otherwise the same answer says no library
    was added while two books are being learned).
    """
    from puffpai.knowledge.auto_index import STATUS_COMPLETE, STATUS_INDEXING
    act = how if isinstance(how, dict) else None
    how = None if act is not None else how
    done = [r for r in records if r.get("status") == STATUS_COMPLETE
            and int(r.get("chunks") or 0) > 0]
    started = [r for r in records if r.get("status") == STATUS_INDEXING]
    learning = [r for r in started if _still_learning(r)]
    stopped = [r for r in started if not _still_learning(r)]
    if done:
        count = "1 library" if len(done) == 1 else f"{len(done)} libraries"
        extra = (f", {len(learning)} more being learned now" if learning else "")
        return row("knowledge_library", f"{LIBRARY_LABEL} ({count} added{extra})",
                   AVAILABLE, how, action=act)
    if learning:
        many = "1 book is" if len(learning) == 1 else f"{len(learning)} books are"
        return row("knowledge_library", LIBRARY_LABEL, NOT_SET_UP, how,
                   note=f"{many} being learned now, and it works as soon as "
                        f"that finishes", action=act)
    if stopped:
        many = "a book" if len(stopped) == 1 else f"{len(stopped)} books"
        return row("knowledge_library", LIBRARY_LABEL, NOT_SET_UP, how,
                   note=f"{many} started being learned but did not finish",
                   action=act)
    return row("knowledge_library", LIBRARY_LABEL + " (none added yet)",
               NOT_SET_UP, how, action=act)


#: A book whose learning has said nothing for this long is not being learned:
#: the process that was doing it stopped (progress is written every 250
#: articles, which takes seconds, not minutes).
LEARNING_QUIET_SECONDS = 30 * 60


def _still_learning(record: dict[str, Any]) -> bool:
    from datetime import datetime, timezone
    stamp = record.get("progress_at") or record.get("started_at")
    try:
        when = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - when).total_seconds()
    return age < LEARNING_QUIET_SECONDS


def _knowledge_row() -> Row:
    """The offline library: indexed packs, from the pack index itself."""
    how = {"id": "knowledge.folder", "args": {}}
    try:
        from puffpai.knowledge.auto_index import PackIndex
        return library_row(PackIndex().records(), how)
    except Exception:
        logger.debug("pack index unreadable", exc_info=True)
        return row("knowledge_library", LIBRARY_LABEL, UNKNOWN, action=how)


def _trimmed(man: dict[str, Any]) -> bool:
    surfaces = man.get("surfaces") or {}
    if surfaces.get("derived_from_goal") or surfaces.get("owner_edited"):
        return True
    return bool(surfaces.get("allowlist"))


def page_list(man: Optional[dict[str, Any]] = None) -> Optional[list[str]]:
    """The page list the wizard trimmed this build to, or None if untrimmed."""
    man = _manifest() if man is None else man
    if not isinstance(man, dict) or not _trimmed(man):
        return None
    return [str(s) for s in ((man.get("surfaces") or {}).get("allowlist") or [])]


def _manifests() -> list[dict[str, Any]]:
    try:
        from puffpai import app_registry
        return [a for a in app_registry.current_manifests()
                if isinstance(a, dict)]
    except Exception:
        logger.debug("app scan failed", exc_info=True)
        return []


def _app_rows(man: dict[str, Any], apps: list[dict[str, Any]]) -> list[Row]:
    """Installed apps whose pages this build actually loads."""
    loaded = page_list(man)
    loaded_set = set(loaded) if loaded is not None else None
    out: list[Row] = []
    for app in apps:
        app_id = str(app.get("id") or "")
        if not app_id:
            continue
        surfaces = set(str(s) for s in (app.get("surfaces") or []))
        if loaded_set is not None and surfaces and not (surfaces & loaded_set):
            continue
        name = str(app.get("name") or app_id)
        blurb = str(app.get("blurb") or "").strip().rstrip(".")
        from puffpai import persona_scaffold
        if persona_scaffold.goal_of(app):
            # A scaffold's blurb is the goal sentence, kept there as a record;
            # read back as an ability it was "scaffolded from your goal: ...".
            default = (app.get("data") or {}).get("default")
            keeps = isinstance(default, dict) and any(
                isinstance(v, list) for v in default.values())
            blurb = ("its own page and the lists it keeps for you" if keeps
                     else "its own page")
        label = f"Use the {name} app" + (f": {blurb[0].lower()}{blurb[1:]}"
                                         if blurb else "")
        out.append(row("app:" + app_id, label, AVAILABLE))
    return out


def _page_label(sid: str) -> str:
    try:
        from puffpai.build_interview import surface_taxonomy
        label = str((surface_taxonomy() or {}).get(sid, {}).get("label") or "")
    except Exception:
        label = ""
    return label


def _page_rows(man: dict[str, Any], apps: list[dict[str, Any]]) -> list[Row]:
    """Pages on the build's list that nothing stands behind.

    A page backed by core code is covered by that code's own row; a page an
    installed app declares is covered by the app row. What is left is a label
    and an icon, and it is said out loud rather than shown as if it worked.
    Pages outside the shipped taxonomy (a persona's own page) are the persona's
    and are left alone.
    """
    listed = page_list(man)
    if not listed:
        return []
    declared = {str(s) for a in apps for s in (a.get("surfaces") or [])}
    out: list[Row] = []
    for sid in listed:
        if sid in PAGE_ABILITIES or sid in declared:
            continue
        label = _page_label(sid)
        if not label:
            continue
        out.append(row("page:" + sid, f"Work with {label.lower()}",
                       NOT_IN_THIS_BUILD))
    return out


def _scheduled_row(cfg: Any) -> Row:
    label = ("Send you messages on a schedule without being asked "
             "(like daily updates or reminders that ring)")
    try:
        engine_on = bool(cfg.n8n.enabled) if cfg is not None else False
    except Exception:
        engine_on = False
    # An outside workflow server may deliver something on a schedule; this
    # build cannot see inside it, so it cannot say yes.
    return row("scheduled_messages", label,
               UNKNOWN if engine_on else NOT_IN_THIS_BUILD)


def _ha_mcp_connected() -> bool:
    """True when Home Assistant is on the tool server list and switched on."""
    try:
        from puffpai import mcp_client
        rec = mcp_client.server(mcp_client.HOME_ASSISTANT)
        return bool(rec and rec.get("switched_on") and rec.get("tool_count"))
    except Exception:
        return False


def _device_row(cfg: Any) -> Row:
    # Only home gadgets need a hub; the label says so, so nothing else reads
    # as needing Home Assistant.
    label = ("Control lights, plugs and other home gadgets (only with a "
             "Home Assistant hub, if you have one)")
    try:
        ha = cfg.ha if cfg is not None else None
        configured = bool(ha and ha.enabled and ha.token)
        if not configured and ha and ha.enabled:
            from puffpai.security import keys
            configured = keys.has_house_key("home_assistant")
    except Exception:
        configured = False
    if _ha_mcp_connected():
        # Home Assistant's own MCP Server is on the tool list: its tools
        # (GetLiveContext, HassTurnOn ...) are in the chat's tool loop.
        return row("device_control", label, AVAILABLE)
    if configured:
        # Configured is not the same as working; nothing here probes it.
        return row("device_control", label, UNKNOWN)
    return row("device_control", label, NOT_SET_UP,
               action={"id": "setup.smart_home", "args": {}})


# ---------------------------------------------------------------------------
# things the goal asks for that nothing covers
# ---------------------------------------------------------------------------

#: The ways a goal says "the bot should work WITH a thing". Verbs only: what
#: follows them is the owner's own words, whatever the topic.
_WITH_THING = re.compile(
    r"(?ix)\b(?:"
    r"(?:works?|working|talks?|talking|connects?|connecting|hooks?\s+up|"
    r"links?|linking|pairs?|pairing|syncs?|syncing|plugs?\s+into)"
    r"\s+(?:with|to|into|up\s+to)"
    r"|controls?|controlling|operates?|operating|drives?|runs?\s+my|"
    r"reads?\s+(?:from\s+)?my|plays?\s+(?:on|through)"
    r")\s+(?:(?:my|our|the|a|an|your|his|her|their)\s+)?"
    r"(?P<thing>[a-z0-9][a-z0-9'\- ]{1,60})")

#: Where a thing's name stops.
_THING_END = re.compile(
    r"(?i)\s+(?:and|or|so|to|that|which|who|when|while|for|in|on|at|with|but|"
    r"from|by|if|because|then)\b|[,.;:!?()]")


#: A second thing on the same verb: "control my switch AND MY record player".
#: Only with a my/the/a in front, so "and shows me cool stuff" is not a thing.
_AND_THING = re.compile(
    r"(?i)\s*(?:,\s*)?(?:and|or|,)\s+(?:my|our|the|a|an|your|his|her|their)"
    r"\s+(?P<thing>[a-z0-9][a-z0-9'\- ]{1,60})")


def goal_things(goal: Any) -> list[str]:
    """The things the goal says the bot should work with, in its own words.

    "works with my smart telescope" -> ["smart telescope"]. At most four words
    each, stop words trimmed off the ends. Pure; no topic list anywhere.
    """
    found: list[str] = []
    text = str(goal or "")
    pos = 0
    while True:
        match = _WITH_THING.search(text, pos)
        if match is None:
            break
        start, raw = match.start("thing"), match.group("thing")
        while True:
            thing = _THING_END.split(raw, maxsplit=1)[0]
            # Resume right after the thing, so "controls my lights and plays
            # through my record player" finds both.
            pos = start + max(len(thing), 1)
            name = _thing_name(thing)
            if name and name not in found:
                found.append(name)
            more = _AND_THING.match(text, pos)
            if more is None:
                break
            start, raw = more.start("thing"), more.group("thing")
    return found


def _thing_name(thing: str) -> str:
    """A thing's words, stop words off both ends, at most four words."""
    parts = [w for w in thing.lower().split() if w]
    while parts and parts[-1] in _STOP:
        parts.pop()
    while parts and parts[0] in _STOP:
        parts.pop(0)
    parts = parts[:4]
    if not parts or all(p in _STOP for p in parts):
        return ""
    return " ".join(parts)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


def goal_rows(goal: Any, rows: list[Row]) -> list[Row]:
    """A "not connected yet" row for each goal thing no row already covers.

    Covered means the thing's last word (its head noun) is one of an existing
    row's words: "control my lights" is the smart-home row's business, open or
    shut, and gets no second row.
    """
    covered: set[str] = set()
    for r in rows:
        rid = str(r.get("id") or "")
        # A page row is itself only a label, so it covers nothing.
        if rid.startswith("page:"):
            continue
        if rid.startswith("app:"):
            # An app covers what it is called, not what its blurb says: the
            # bot's own app carries the goal sentence as its blurb, and "works
            # with my smart telescope" in a description drives no telescope.
            covered |= label_terms({"label": str(r.get("label") or "")
                                    .split(":", 1)[0]})
            continue
        covered |= label_terms(r)
    out: list[Row] = []
    for thing in goal_things(goal):
        head = _stem(thing.split()[-1])
        if head in covered:
            continue
        covered.add(head)
        out.append(row("goal:" + _slug(thing), f"Work with your {thing}",
                       NOT_IN_THIS_BUILD))
    return out


def core_rows(manifest: Optional[dict[str, Any]] = None) -> list[Row]:
    """This build's own rows, before any part's provider is applied.

    ``manifest`` is the build record to read the page list from; ``None``
    reads the one on disk.
    """
    man = _manifest() if manifest is None else manifest
    cfg = _config()
    apps = _manifests()
    rows: list[Row] = []
    rows.extend(_app_rows(man, apps))
    rows.append(_knowledge_row())
    rows.extend(_door_rows())
    rows.append(_device_row(cfg))
    rows.append(_scheduled_row(cfg))
    rows.append(row("live_data",
                    "Look up live information on the internet, like today's "
                    "news or new pictures", NOT_IN_THIS_BUILD))
    rows.append(row("web_search", "Search the web", NOT_IN_THIS_BUILD))
    rows.append(row("web_page", "Open or read a web page for you in chat",
                    NOT_IN_THIS_BUILD))
    rows.extend(_page_rows(man, apps))
    return rows


# ---------------------------------------------------------------------------
# the one answer
# ---------------------------------------------------------------------------

def abilities(goal: Optional[str] = None,
              manifest: Optional[dict[str, Any]] = None) -> list[Row]:
    """Every row: core rows, each provider's rows over them, then the goal's.

    ``goal`` defaults to the build's own goal and ``manifest`` to the build
    record on disk; a caller describing a particular record passes it. Never
    raises.
    """
    try:
        rows = core_rows(manifest)
    except Exception:
        logger.warning("build abilities could not be read", exc_info=True)
        rows = []
    by_id: dict[str, int] = {r["id"]: i for i, r in enumerate(rows)}
    for provider in list(_PROVIDERS):
        try:
            provided = list(provider() or [])
        except Exception:
            logger.warning("ability provider %r failed; its rows are left out",
                           provider, exc_info=True)
            continue
        for raw in provided:
            clean = _clean(raw)
            if clean is None:
                continue
            if clean["id"] in by_id:
                rows[by_id[clean["id"]]] = clean
            else:
                by_id[clean["id"]] = len(rows)
                rows.append(clean)
    if any(r["id"].startswith("live:") and r["status"] == AVAILABLE
           for r in rows):
        rows = [r for r in rows if r["id"] != "live_data"]
    # A live card is one thing: its row stands for the app it is and the
    # outside website it reads, which would otherwise be listed twice more.
    cards = {r["id"][len("live:"):] for r in rows if r["id"].startswith("live:")}
    if cards:
        rows = [r for r in rows if not (
            (r["id"].startswith("app:") and r["id"][4:] in cards)
            or (r["id"].startswith("outside:app:")
                and r["id"][len("outside:app:"):].split(":")[0] in cards))]
    try:
        text = goal_of(manifest) if goal is None else goal
        extra = goal_rows(text, rows)
        # A page row about the same thing as a goal row is the same gap said
        # twice; the goal row uses the owner's own words, so it stays.
        heads = {_stem(r["label"].split()[-1]) for r in extra}
        rows = [r for r in rows
                if not (r["id"].startswith("page:") and terms(r) & heads)]
        rows = extra + rows
    except Exception:
        logger.debug("goal rows skipped", exc_info=True)
    return rows


def asked_goal_thing(message: Any, rows: Optional[list[Row]] = None
                     ) -> Optional[Row]:
    """The goal thing a "my <thing>" question is about, when it is not connected.

    "show me my telescope" on a build whose goal said "works with my smart
    telescope" and has nothing that talks to one: that row. The model must not
    answer it, because a library passage about somebody else's telescope reads
    like a fact about the owner's. None when the thing works or was not asked.
    """
    said = [_stem(w) for w in words(message)]
    if not said:
        return None
    for r in (rows if rows is not None else abilities()):
        if not str(r.get("id") or "").startswith("goal:"):
            continue
        if r.get("status") == AVAILABLE:
            continue
        label_words = words(r.get("label"))
        if not label_words:
            continue
        head = _stem(label_words[-1])
        for i, w in enumerate(said):
            if w != head:
                continue
            # "my telescope", "my smart telescope", "our old smart telescope"
            if any(p in ("my", "our") for p in said[max(0, i - 3):i]):
                return r
    return None


def get(ability_id: str, rows: Optional[list[Row]] = None) -> Optional[Row]:
    for r in (rows if rows is not None else abilities()):
        if r.get("id") == ability_id:
            return r
    return None


def is_available(ability_id: str, rows: Optional[list[Row]] = None) -> bool:
    """True only for a row whose status is ``available``. Missing is False."""
    found = get(ability_id, rows)
    return bool(found and found.get("status") == AVAILABLE)


def available(rows: Optional[list[Row]] = None) -> list[Row]:
    return [r for r in (rows if rows is not None else abilities())
            if r.get("status") == AVAILABLE]


def absent(rows: Optional[list[Row]] = None) -> list[Row]:
    """Rows the bot cannot count on right now (unknown included)."""
    return [r for r in (rows if rows is not None else abilities())
            if r.get("status") in ABSENT]


def page_row(page: str, rows: Optional[list[Row]] = None,
             apps: Optional[list[dict[str, Any]]] = None) -> Optional[Row]:
    """The row that says whether a page works, or None for a page with no rule.

    An installed app that declares the page backs it (an app row, available).
    A page core code serves answers with that code's row. A taxonomy page with
    neither is a label only: ``not_in_this_build``. A page outside the taxonomy
    (a persona's own page) returns None: not this module's business.
    """
    sid = str(page or "").strip()
    if not sid:
        return None
    apps = _manifests() if apps is None else apps
    for app in apps:
        if sid in {str(s) for s in (app.get("surfaces") or [])}:
            return row("app:" + str(app.get("id") or ""),
                       f"Use the {app.get('name') or app.get('id')} app",
                       AVAILABLE)
    if sid in PAGE_ABILITIES:
        rid = PAGE_ABILITIES[sid]
        return get(rid, rows) or row(rid, _page_label(sid) or rid, UNKNOWN)
    label = _page_label(sid)
    if not label:
        return None
    return row("page:" + sid, f"Work with {label.lower()}", NOT_IN_THIS_BUILD)


def lower_first(text: str) -> str:
    """"Connect to a website" -> "connect to a website"."""
    text = str(text or "")
    return text[:1].lower() + text[1:] if text else text


def status_words(r: Row) -> str:
    """The row's state in plain words, without the label."""
    status = r.get("status")
    if r.get("note") and status != AVAILABLE:
        return str(r["note"])
    if status == UNKNOWN:
        return "I can't tell whether this works right now"
    if status == NOT_SET_UP:
        return "not switched on yet"
    if status == AVAILABLE:
        return "works now"
    if not r.get("how_to_add"):
        return "not connected yet, it can be added later"
    return "not in this build yet"


def absent_line(r: Row, *, with_how: bool = True) -> str:
    """One plain line about something the bot cannot do, with the way to add it.

    ``with_how=False`` leaves the command out (chat: a person asking "what can
    you do?" is not handed shell commands).
    """
    text = f"{lower_first(r.get('label') or '')}: {status_words(r)}"
    how = r.get("how_to_add") if with_how else None
    if how:
        text += f". To add it: {str(how).rstrip('.')}"
    return text.rstrip(".") + "."


#: When the goal says nothing about them, the order absent abilities are named
#: in: the ones a model is most tempted to fake first (it will happily invent
#: today's news, a schedule, a web search), single outside websites last.
_FAKE_FIRST = ("live_data", "scheduled_messages", "web_search",
               "knowledge_library", "device_control", "web_page")


def _base_rank(r: Row) -> int:
    rid = str(r.get("id") or "")
    if rid in _FAKE_FIRST:
        return _FAKE_FIRST.index(rid)
    if rid.startswith("outside:"):
        return len(_FAKE_FIRST) + 1
    return len(_FAKE_FIRST)


def goal_order(rows: list[Row], goal: str = "") -> list[Row]:
    """Rows the owner's goal talks about first, then the usual order.

    Goal rows (the owner's own things) always lead; after them a row ranks by
    how many of the goal's words are among its own words, then by
    :data:`_FAKE_FIRST`, then by where it was.
    """
    goal_words = {_stem(w) for w in words(goal) if w not in _STOP}

    def score(r: Row) -> int:
        if str(r.get("id") or "").startswith("goal:"):
            return 1000
        return len(terms(r) & goal_words)

    ranked = sorted(enumerate(rows),
                    key=lambda pair: (-score(pair[1]), _base_rank(pair[1]),
                                      pair[0]))
    return [r for _i, r in ranked]


__all__ = [
    "ABILITY_TERMS", "ABSENT", "AVAILABLE", "NOT_IN_THIS_BUILD", "NOT_SET_UP",
    "PAGE_ABILITIES", "STATUSES", "UNKNOWN",
    "abilities", "absent", "absent_line", "asked_goal_thing", "available",
    "core_rows", "get",
    "goal_of", "goal_order", "goal_rows", "goal_things", "is_available",
    "label_terms",
    "lower_first", "page_list", "page_row", "providers", "register_provider",
    "row", "status_words", "terms", "unregister_provider", "words",
]
