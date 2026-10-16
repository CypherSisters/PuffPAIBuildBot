"""PuffPAI message router — keyword-based intent classification.

Maps a free-text user message to a coarse :class:`RouteTarget` and a
human-readable category string. The Concierge uses the category to decide
which team-mate (if any) owns the request and delegates through the bus.

This is a deliberately small, transparent classifier: keyword lists, not a
model. It is easy to audit and cheap to run, and it never reaches the network.
The public build ships the in-scope categories only (house, media, apps,
library, briefing, general security status, knowledge). The network-edge
vocabulary lives on the hardware appliance and is not part of this release.

The vocabulary itself now lives in :mod:`puffpai.rulepacks` (JSON packs under
``config/rulepacks/``) rather than in this file, so it can be audited, edited,
and turned into an accurate phrasebook without touching routing code. The words
did not change in that move. What the packs add is a routing *tier*: a weaker
brain loads MORE deterministic packs, because it cannot be trusted to notice an
action request on its own. The reference brain runs at the ``light`` tier and
routes exactly as it always has.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional

from puffpai import rulepacks


class RouteTarget(Enum):
    """Coarse routing decision for a user message."""

    AGENT = "agent"          # needs a tool-capable team-mate
    LIBRARY = "library"      # direct knowledge-base / collection lookup
    RAG = "rag"              # retrieval + LLM answer (the default)
    HOME = "home"            # house control (subset of AGENT)
    BRIEFING = "briefing"    # daily briefing / catch-up
    APP_LAUNCH = "app_launch"  # open a built-in app


# ── Keyword definitions (public-safe subset) ────────────────────────────────
# Loaded from the rulepacks, not typed here. The module-level names are kept
# because they are the router's long-standing shape and callers read them.

_PACKS: list[dict[str, Any]] = rulepacks.load_packs()


def _kw(surface: str) -> list[str]:
    return rulepacks.keywords_for(surface, _PACKS)


def _pack(surface: str) -> dict[str, Any]:
    for pack in _PACKS:
        if pack.get("surface") == surface:
            return pack
    return {}


_BRIEFING_KEYWORDS = _kw("briefing")
_HOME_KEYWORDS = _kw("house")
_SECURITY_KEYWORDS = _kw("security")
_MEDIA_KEYWORDS = _kw("media")
_APP_KEYWORDS = _kw("apps")
_ACTION_KEYWORDS = _kw("action")
_LIBRARY_KEYWORDS = _kw("library")
_LIBRARY_QUERY_WORDS = list(_pack("library").get("query_words") or [])
_LIBRARY_EXCLUDE_PHRASES = tuple(_pack("library").get("exclude_phrases") or ())

_ALL_AGENT_KEYWORDS = (
    _HOME_KEYWORDS + _SECURITY_KEYWORDS + _MEDIA_KEYWORDS + _APP_KEYWORDS
)


# ── Routing tier ────────────────────────────────────────────────────────────
# Which packs this build loads depends on the brain it is running. Resolved
# lazily and cached: the router is on the hot path and must not pay for a
# config read per message, and it must keep working if config is unreadable.

_active_tier: Optional[str] = None


def active_tier() -> str:
    """The routing tier for the configured brain. Cached; never raises."""
    global _active_tier
    if _active_tier is None:
        try:
            from puffpai.config import get_config
            from puffpai.model_profile import routing_tier
            _active_tier = routing_tier(get_config().ollama.model)
        except Exception:
            _active_tier = rulepacks.DEFAULT_TIER
    return _active_tier


def _reset_tier_cache() -> None:
    """Drop the cached tier so the next call re-resolves it (tests, reconfig)."""
    global _active_tier
    _active_tier = None


def _extra_category_packs() -> list[dict[str, Any]]:
    """Packs above the light tier that this build loads, in consultation order.

    The light tier gets none of these by construction, which is what keeps the
    reference build's classification byte-identical to what it was before the
    vocabulary moved into JSON.
    """
    light = {p.get("surface") for p in rulepacks.assemble("light", _PACKS)}
    return [p for p in rulepacks.assemble(active_tier(), _PACKS)
            if p.get("surface") not in light]


# ── Action-intent safety net ────────────────────────────────────────────────
# Action-shaped phrasing that misses every keyword list still needs a
# tool-capable agent — the plain RAG path has no tools and would hallucinate
# compliance ("Sure, I turned on the lights") instead of acting or declining.

_ACTION_VERBS_RE = re.compile(
    r"\b(?:turn|switch|dim|brighten|set|start|stop|pause|resume|play|skip|"
    r"add|create|make|schedule|remind|cancel|delete|remove|lock|unlock|"
    r"open|close|adjust|lower|raise|activate|deactivate|launch|queue)\b")

_ACTION_OBJECTS_RE = re.compile(
    r"\b(?:light|lights|lamp|fan|thermostat|temperature|heat|ac\b|tv|"
    r"timer|alarm|reminder|task|event|appointment|meeting|list|"
    r"music|song|playlist|movie|device|door|scene|volume|"
    r"grocery|groceries|shopping)\b")

_COMFORT_RE = re.compile(
    r"\btoo (?:dark|bright|hot|cold|warm|loud|quiet)\b"
    r"|\b(?:it'?s|its) (?:freezing|boiling|dark|chilly) in\b"
    r"|\bdon'?t let me forget\b"
    r"|\bwake me\b")


def _looks_like_action_request(lower: str) -> bool:
    """True when the message is a do-something request in indirect words."""
    if _COMFORT_RE.search(lower):
        return True
    return bool(_ACTION_VERBS_RE.search(lower)
                and _ACTION_OBJECTS_RE.search(lower))


# ── Router functions ────────────────────────────────────────────────────────

def route_message(text: str, mode: str = "auto") -> RouteTarget:
    """Determine the best route for a user message.

    Args:
        text: the user's message.
        mode: ``"auto"`` (keyword detection), ``"home"`` (force house), or
            ``"general"`` (force RAG).
    """
    mode = (mode or "auto").strip().lower()
    if mode == "home":
        return RouteTarget.HOME
    if mode == "general":
        return RouteTarget.RAG

    lower = (text or "").lower()

    if any(k in lower for k in _BRIEFING_KEYWORDS):
        return RouteTarget.BRIEFING
    # Agent keywords BEFORE the library check so "movie library",
    # "music library" etc. are not misrouted to the catalog handler.
    if any(k in lower for k in _ALL_AGENT_KEYWORDS):
        return RouteTarget.AGENT
    if any(k in lower for k in _ACTION_KEYWORDS):
        return RouteTarget.AGENT
    if _is_library_query(lower):
        return RouteTarget.LIBRARY
    if _looks_like_action_request(lower):
        return RouteTarget.AGENT
    return RouteTarget.RAG


def get_route_category(text: str) -> str:
    """Return a human-readable category for the Concierge / logging.

    One of: ``briefing``, ``home``, ``security``, ``media``, ``app``,
    ``action``, ``library``, ``rag``.
    """
    lower = (text or "").lower()
    if any(k in lower for k in _BRIEFING_KEYWORDS):
        return "briefing"
    if any(k in lower for k in _HOME_KEYWORDS):
        return "home"
    if any(k in lower for k in _SECURITY_KEYWORDS):
        return "security"
    if any(k in lower for k in _MEDIA_KEYWORDS):
        return "media"
    if any(k in lower for k in _APP_KEYWORDS):
        return "app"
    if any(k in lower for k in _ACTION_KEYWORDS):
        return "action"
    if _is_library_query(lower):
        return "library"
    # Tiers below 'light' load extra deterministic packs so a weaker brain is
    # handed a named surface instead of being left to infer one. The light tier
    # loads none of these, so its answer here is unchanged.
    for pack in _extra_category_packs():
        if rulepacks.matches(pack, lower):
            return str(pack.get("category", "rag"))
    return "rag"


def _is_library_query(lower_text: str) -> bool:
    """True when the message asks about the indexed libraries/collections."""
    # Citation-style questions ("cite your sources", "where did you get that")
    # are about the previous answer, not a catalog dump — keep them on RAG.
    if any(p in lower_text for p in _LIBRARY_EXCLUDE_PHRASES):
        return False
    has_library_word = any(k in lower_text for k in _LIBRARY_KEYWORDS)
    has_query_word = any(k in lower_text for k in _LIBRARY_QUERY_WORDS)
    return has_library_word and has_query_word


# ── Category → build surfaces (self-knowledge gating) ───────────────────────
# Which surfaces of the shipped taxonomy a routed category actually needs. The
# dispatch path consults this BEFORE handing a message to a model, so a request
# for a surface this build never loaded is answered honestly instead of being
# improvised into a fake success ("Sure, I turned on the lights").
#
# Two rules make this readable:
#   * A category is available when ANY of its surfaces is. A build with music
#     but no video still handles "media"; the honest refusal then happens at the
#     specific request, not across the whole family.
#   * A category mapped to () is NEVER gated. Those are the bot talking about
#     what it already is or knows, and gating them would let a trimmed build
#     refuse to describe itself.
#
# Surface ids are validated against build_interview.surface_taxonomy() by the
# test suite, so this map cannot silently rot when the taxonomy moves.
CATEGORY_SURFACES: dict[str, tuple[str, ...]] = {
    # The bot summarising what it already has. Not backed by a surface.
    "briefing": (),
    # Retrieval + answer over what is already indexed. Never gated: refusing to
    # *talk about* a subject because the matching surface is unloaded is the
    # opposite mistake, and a worse one.
    "rag": (),
    # Approvals, consent, "what are you allowed to do". Core behaviour every
    # build has, including a sealed one. Gating it would let a trimmed build
    # refuse the word "yes".
    "security": (),
    # Calculator and file-scratchpad verbs. No loadable surface backs them.
    "action": (),
    # House control. Cameras live here too - they are a house surface, not the
    # security/approvals vocabulary.
    "home": ("house.lights", "house.climate", "house.sensors", "house.cameras"),
    # Playback and the media libraries behind it.
    "media": ("media.video", "media.audio", "media.books"),
    # The built-in apps: timers/alarms, calendar, pantry, fitness, games.
    "app": ("comms.timers", "comms.calendar", "daily.kitchen",
            "daily.fitness", "daily.games"),
    # A catalog question about the offline reference index.
    "library": ("knowledge.library", "knowledge.medical", "knowledge.maps"),
}

# Words that name one surface outright. Used ONLY to narrow a category's set so
# a refusal can name the thing that was actually asked for ("I do not have
# house.cameras") rather than the whole family. This never widens the set: a
# phrase for a surface outside the routed category is ignored, and a message
# that names nothing keeps the full category set.
_SURFACE_PHRASES: dict[str, tuple[str, ...]] = {
    "house.cameras": ("camera", "doorbell", "cctv"),
    "house.lights": ("light", "lighting", "lamp", "brightness", "dim "),
    "house.climate": ("thermostat", "temperature", "heat", "heating", "cooling",
                      "air conditioning"),
    "house.sensors": ("sensor", "motion", "humidity", "leak"),
    "media.video": ("movie", "film", "video", "watch", "tv show"),
    "media.audio": ("music", "song", "album", "playlist", "radio", "artist"),
    "media.books": ("book", "ebook", "audiobook", "reading"),
    "comms.timers": ("timer", "alarm", "countdown", "remind"),
    "comms.calendar": ("calendar", "appointment", "meeting", "event",
                       "schedule"),
    "daily.kitchen": ("pantry", "recipe", "meal", "grocery", "groceries",
                      "shopping list"),
    "daily.fitness": ("workout", "exercise", "fitness", "steps"),
    "daily.games": ("game", "trivia", "quiz"),
    "knowledge.medical": ("medical", "medicine", "symptom", "first aid"),
    "knowledge.maps": ("map", "directions", "atlas"),
}


def surfaces_for_category(category: str, text: str = "") -> tuple[str, ...]:
    """The surfaces a routed ``category`` needs, narrowed by what ``text`` names.

    Returns an empty tuple for the categories that are never surface-gated.
    """
    base = CATEGORY_SURFACES.get((category or "").strip().lower(), ())
    if not base or not text:
        return base
    lower = text.lower()
    named = tuple(surface for surface in base
                  if any(phrase in lower
                         for phrase in _SURFACE_PHRASES.get(surface, ())))
    return named or base


def surfaces_for_message(text: str) -> tuple[str, ...]:
    """The surfaces a message needs, or () when it is not surface-gated."""
    return surfaces_for_category(get_route_category(text), text or "")


def _surface_vocabulary(surface: str) -> tuple[str, ...]:
    """The words that name ONE surface: its phrases, plus its own id tail.

    The tail is there so a surface nobody wrote phrases for can still be asked
    for by name - ``knowledge.library`` is asked about with the word "library".
    Short tails are dropped: a two-letter word is a coincidence, not evidence.
    """
    tail = surface.rsplit(".", 1)[-1].replace("_", " ")
    words = _SURFACE_PHRASES.get(surface, ())
    return words + ((tail,) if len(tail) >= 4 else ())


def _gated_surfaces() -> tuple[str, ...]:
    return tuple(sorted({s for row in CATEGORY_SURFACES.values() for s in row}))


def surfaces_named_in(text: str) -> tuple[str, ...]:
    """The surfaces whose OWN vocabulary this sentence uses.

    Evidence, never a bucket. ``get_route_category`` sorts free text into broad
    families - "summarise my garden so far" lands in ``app`` because of one goal
    word - and a category is not a request: reporting on the first surface of a
    family as though it had been asked for is how a garden bot answered a
    question about its garden with "I do not have comms.timers".
    """
    lower = " " + " ".join((text or "").lower().split()) + " "
    return tuple(surface for surface in _gated_surfaces()
                 if any(_says_word(lower, word)
                        for word in _surface_vocabulary(surface)))


def _says_word(lower: str, word: str) -> bool:
    """True when *word* (or its plural) stands as a whole word in *lower*.

    A piece of a longer word is not evidence: "meteor watching" does not ask
    for a video library, "wheat" is not heating, "delight" is not a lamp.
    """
    word = word.strip()
    if not word:
        return False
    return re.search(r"(?<![a-z])" + re.escape(word) + r"(?:s|es)?(?![a-z])",
                     lower) is not None


def surfaces_asked_for(text: str) -> tuple[str, ...]:
    """The surfaces this message actually asked for, or () - evidence or silence.

    The routed category's surfaces, narrowed to the ones the sentence NAMES. A
    message that names none returns empty, which callers must read as "this is
    not a capability question" rather than as "nothing is available". Absence is
    not a limitation.
    """
    base = surfaces_for_category(get_route_category(text))
    if not base:
        return ()
    named = set(surfaces_named_in(text))
    return tuple(surface for surface in base if surface in named)


__all__ = ["RouteTarget", "route_message", "get_route_category", "active_tier",
           "CATEGORY_SURFACES", "surfaces_for_category",
           "surfaces_for_message", "surfaces_named_in", "surfaces_asked_for"]
