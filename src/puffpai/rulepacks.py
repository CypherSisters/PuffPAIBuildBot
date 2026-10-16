"""Routing rulepacks — the words that steer a message to a surface.

BuildBot's brain has no native tool-calling, so the first layer of tool routing
is a small, transparent keyword classifier (:mod:`puffpai.router`). This module
holds those words as data instead of code, one JSON pack per surface under
``config/rulepacks/``, so three things become possible that were not before:

* **The vocabulary is auditable and editable** without touching the router. A
  pack is a list of phrases, a surface tag, and the tier it starts loading at.
* **Scaffolding scales with the brain.** ``min_tier`` says how much determinism
  a pack is for. Packs marked ``light`` load everywhere; packs marked ``guided``
  or ``rails`` load only for models that need the extra net. A weaker brain gets
  MORE deterministic packs, never fewer, because over-routing is recoverable and
  under-routing is not.
* **The phrasebook cannot drift.** The wordings we tell people to use are
  generated from these same packs (see :func:`puffpai.model_profile.
  generate_phrasebook`), so a documented phrase is by construction a phrase that
  actually routes.

The full vocabulary is also baked in below as ``_FALLBACK_PACKS``. The router is
load-bearing, so it must keep classifying correctly on a build where the config
directory is missing or a pack file is corrupt.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from puffpai import config_health

# Routing tiers, weakest brain last. A pack loads when the active tier is at
# least as scaffolded as the pack's min_tier, so 'light' packs load for
# everyone and 'rails' packs load only on the rails tier.
TIER_RANK: dict[str, int] = {"light": 0, "guided": 1, "rails": 2}

DEFAULT_TIER = "guided"


# Baked-in vocabulary, verbatim from the shipped packs. This is the safety net
# for a build with no readable config/rulepacks directory; the files are the
# thing maintainers edit.
_FALLBACK_PACKS: list[dict[str, Any]] = [
    {
        "schema": "rulepack/v1",
        "surface": "briefing",
        "category": "briefing",
        "route": "briefing",
        "min_tier": "light",
        "order": 10,
        "match": "substring",
        "blurb": "Ask for the day in one go and it gathers the pieces for you.",
        "title": "The daily briefing",
        "examples": [
            "give me my daily briefing",
            "catch me up on what i missed",
            "what did i miss while i was out",
            "read me today's briefing",
            "give me the morning report",
            "start my daily digest"
        ],
        "keywords": [
            "daily update",
            "daily briefing",
            "daily report",
            "morning briefing",
            "morning update",
            "morning report",
            "what did i miss",
            "catch me up",
            "brief me",
            "daily digest",
            "today's briefing",
            "today's update"
        ]
    },
    {
        "schema": "rulepack/v1",
        "surface": "house",
        "category": "home",
        "route": "agent",
        "min_tier": "light",
        "order": 20,
        "match": "substring",
        "blurb": "Lights, switches, fans, thermostat, scenes, and the dashboard widgets.",
        "title": "The house",
        "examples": [
            "turn on the kitchen lights",
            "turn off the fan in the bedroom",
            "set the thermostat to 68",
            "dim the lamp in the living room",
            "activate scene movie night",
            "show me my dashboard",
            "show me the front door camera"
        ],
        "keywords": [
            "home assistant",
            "smart home",
            "turn on",
            "turn off",
            "thermostat",
            "set brightness",
            "dim the",
            "the light",
            "the lights",
            "the switch",
            "the fan",
            "list devices",
            "control the",
            "activate scene",
            "my dashboard",
            "the dashboard",
            "widget",
            "widgets",
            "camera",
            "cameras",
            "doorbell",
            "cctv"
        ]
    },
    {
        "schema": "rulepack/v1",
        "surface": "security",
        "category": "security",
        "route": "agent",
        "min_tier": "light",
        "order": 30,
        "match": "substring",
        "blurb": "Approvals, consent, and what the bot is allowed to do. Say yes or no in plain words.",
        "title": "Permission and approvals",
        "examples": [
            "what is my security status",
            "is there anything that needs approval",
            "who is on my network",
            "show me what has permission to run",
            "go ahead and do it",
            "i do not consent to that"
        ],
        "keywords": [
            "security status",
            "needs approval",
            "approval",
            "who is on my network",
            "devices on my network",
            "approve",
            "consent",
            "permission",
            "authorize",
            "go ahead",
            "allow it",
            "deny it"
        ]
    },
    {
        "schema": "rulepack/v1",
        "surface": "media",
        "category": "media",
        "route": "agent",
        "min_tier": "light",
        "order": 40,
        "match": "substring",
        "blurb": "Movies, music, books, and playback. Naming the kind of thing helps.",
        "title": "Movies, music and books",
        "examples": [
            "what movies do i have",
            "play music in the kitchen",
            "what is currently playing",
            "search my music for miles davis",
            "shuffle the road trip playlist",
            "is jellyfin running"
        ],
        "keywords": [
            "media server",
            "jellyfin",
            "movie",
            "movies",
            "film",
            "films",
            "music library",
            "my music",
            "song",
            "songs",
            "album",
            "albums",
            "artist",
            "audiobook",
            "audiobooks",
            "ebook",
            "ebooks",
            "playlist",
            "playlists",
            "now playing",
            "currently playing",
            "play music",
            "play movie",
            "play video",
            "watch",
            "what movies",
            "what music",
            "what books",
            "my library",
            "media library",
            "search movies",
            "search music",
            "search books"
        ]
    },
    {
        "schema": "rulepack/v1",
        "surface": "apps",
        "category": "app",
        "route": "agent",
        "min_tier": "light",
        "order": 50,
        "match": "substring",
        "blurb": "The built-in apps: timers and alarms, calendar, tasks, pantry and meals, fitness, trivia.",
        "title": "The built-in apps",
        "examples": [
            "set a timer for ten minutes",
            "remind me to call the vet at four",
            "what is on my calendar tomorrow",
            "add milk to the shopping list",
            "what is in the pantry",
            "log today's workout",
            "what are the trivia scores"
        ],
        "keywords": [
            "timer",
            "alarm",
            "countdown",
            "remind me in",
            "remind me to",
            "set a timer",
            "set an alarm",
            "calendar",
            "schedule",
            "event",
            "appointment",
            "meeting",
            "task",
            "to do",
            "todo",
            "pantry",
            "shopping list",
            "grocery",
            "meal plan",
            "workout",
            "exercise",
            "fitness",
            "trivia",
            "quiz"
        ]
    },
    {
        "schema": "rulepack/v1",
        "surface": "action",
        "category": "action",
        "route": "agent",
        "min_tier": "light",
        "order": 60,
        "match": "substring",
        "blurb": "Small jobs done with a tool rather than talked about: arithmetic and reading or writing a file.",
        "title": "Quick jobs",
        "examples": [
            "calculate 17 times 4",
            "compute the total for me",
            "what is the sum of those numbers",
            "multiply 12 by 8",
            "divide 144 by 12",
            "save to my project notes"
        ],
        "keywords": [
            "calculate",
            "compute",
            "multiply",
            "divide",
            "sum of",
            "create file",
            "write file",
            "save to",
            "read file",
            "open file"
        ]
    },
    {
        "schema": "rulepack/v1",
        "surface": "library",
        "category": "library",
        "route": "library",
        "min_tier": "light",
        "order": 70,
        "match": "substring",
        "blurb": "What is in the indexed collections: knowledge packs, wikipedia, and anything you have loaded yourself.",
        "title": "The knowledge library",
        "examples": [
            "what do you have in your library about beekeeping",
            "how many wikipedia articles are indexed",
            "what collections can i search",
            "tell me what is in the knowledge base",
            "list everything in the offline library",
            "do you have a database of medical guides"
        ],
        "keywords": [
            "librar",
            "database",
            "indexed",
            "collection",
            "wikipedia",
            "knowledge base"
        ],
        "query_words": [
            "list",
            "how many",
            "what",
            "which",
            "show",
            "tell",
            "about",
            "describe",
            "do you have",
            "do you know",
            "available",
            "access",
            "can you search"
        ],
        "exclude_phrases": [
            "source",
            "sources",
            "cite",
            "citation",
            "citations",
            "reference",
            "references",
            "where did you",
            "come from",
            "came from"
        ]
    },
    {
        "schema": "rulepack/v1",
        "surface": "action_net",
        "category": "action",
        "route": "agent",
        "min_tier": "guided",
        "order": 80,
        "match": "word",
        "require": "verb_and_object",
        "blurb": "A request tucked inside an ordinary sentence. Name what you want changed and the thing you want it done to.",
        "title": "Asking for something in passing",
        "examples": [
            "lower the volume",
            "raise the volume a bit",
            "unlock the door",
            "adjust the temperature",
            "brighten the lamp",
            "turn the heat down"
        ],
        "_note": "Vocabulary lifted verbatim from the action-intent safety net in router.py (_ACTION_VERBS_RE / _ACTION_OBJECTS_RE). Word-boundary matched, and a message must hit both a verb and an object. Loaded from the guided tier down; the light tier does not need it because the model can be trusted to notice an action request on its own.",
        "verbs": [
            "turn",
            "switch",
            "dim",
            "brighten",
            "set",
            "start",
            "stop",
            "pause",
            "resume",
            "play",
            "skip",
            "add",
            "create",
            "make",
            "schedule",
            "remind",
            "cancel",
            "delete",
            "remove",
            "lock",
            "unlock",
            "open",
            "close",
            "adjust",
            "lower",
            "raise",
            "activate",
            "deactivate",
            "launch",
            "queue"
        ],
        "objects": [
            "light",
            "lights",
            "lamp",
            "fan",
            "thermostat",
            "temperature",
            "heat",
            "ac",
            "tv",
            "timer",
            "alarm",
            "reminder",
            "task",
            "event",
            "appointment",
            "meeting",
            "list",
            "music",
            "song",
            "playlist",
            "movie",
            "device",
            "door",
            "scene",
            "volume",
            "grocery",
            "groceries",
            "shopping"
        ]
    }
]


def _rulepack_dir() -> str:
    """Absolute path of the shipped rulepack directory."""
    from puffpai.config import get_config
    return os.path.join(get_config().REPO_ROOT, "config", "rulepacks")


def _valid(pack: Any) -> bool:
    """True when a loaded object looks like a usable rulepack."""
    if not isinstance(pack, dict) or not pack.get("surface"):
        return False
    return bool(pack.get("keywords")) or bool(pack.get("verbs"))


def validate_pack(pack: Any) -> Optional[str]:
    """Structural check of one parsed rulepack; None means healthy.

    A pack needs a surface to route to, something to match on (``keywords`` or
    ``verbs`` — the shipped ``action_net`` pack matches on verbs), and the
    owner-facing ``title`` and ``examples`` the wizard shows. A pack missing the
    owner-facing half still routes, but the wizard cannot describe it, which is
    a limitation the owner should hear about rather than discover.
    """
    if not isinstance(pack, dict):
        return "The pack is not a JSON object."
    if not pack.get("surface"):
        return "The pack names no surface."
    if not pack.get("keywords") and not pack.get("verbs"):
        return f"Pack '{pack.get('surface')}' has neither keywords nor verbs."
    for field in ("title", "examples"):
        if not pack.get(field):
            return f"Pack '{pack.get('surface')}' has no {field}."
    return None


def _apply_app_manifests(
    packs: list[dict[str, Any]], apps_dir: Optional[str] = None
) -> list[dict[str, Any]]:
    """Replace the apps pack's vocabulary with the installed apps' own words.

    ``config/rulepacks/apps.json`` used to be one static blob shared by every
    app, which meant a build with no Trivia app still routed "start a trivia
    quiz", and a NEW app could contribute no vocabulary at all without editing
    core. Here the pack's *shell* - its surface, route, category, tier and order
    - stays core, and its ``keywords`` and ``examples`` come from the manifests
    in ``apps/``. Install an app and its words arrive; remove the folder and
    they leave.

    Fail-open, deliberately: no readable manifests at all means the shipped
    vocabulary stands. A build whose ``apps/`` directory is missing gets the
    router it has always had, never an empty one. With the five shipped apps
    installed the assembly reproduces ``apps.json`` exactly.

    The pack dict is copied rather than edited, because on the fallback path it
    is a member of the module-level :data:`_FALLBACK_PACKS`.
    """
    from puffpai import app_registry
    manifests = app_registry.load_manifests(apps_dir)
    if not manifests:
        return packs
    vocabulary = app_registry.assemble_vocabulary(manifests)
    if not vocabulary["keywords"]:
        return packs
    out: list[dict[str, Any]] = []
    for pack in packs:
        if pack.get("surface") != app_registry.APPS_SURFACE:
            out.append(pack)
            continue
        merged = dict(pack)
        merged["keywords"] = list(vocabulary["keywords"])
        if vocabulary["examples"]:
            merged["examples"] = list(vocabulary["examples"])
        out.append(merged)
    return out


def load_packs(
    path: Optional[str] = None, apps_dir: Optional[str] = None
) -> list[dict[str, Any]]:
    """Load every rulepack, ordered, degrading to the baked-in set.

    A single unreadable file does not take the router down: the file is skipped
    and the rest load. But skipping one silently is exactly the trap - the
    vocabulary shrinks and nothing says so - so every corrupt or malformed pack
    is recorded in :mod:`puffpai.config_health`. An absent directory is the
    legitimate case (a fresh clone) and falls back to the baked-in vocabulary
    silently; a directory we cannot read for any other reason does not.

    Which packs load is unchanged: a structurally odd pack that the router can
    still use is kept and merely reported.
    """
    target = path or _rulepack_dir()
    packs: list[dict[str, Any]] = []
    try:
        names = sorted(n for n in os.listdir(target) if n.endswith(".json"))
    except OSError as exc:
        # The baked-in set is the full shipped vocabulary, so falling back
        # wholesale does not shrink anything; it is not a "reduced" degradation.
        config_health.note_load_failure("rulepacks", target, exc)
        return sorted(
            _apply_app_manifests(list(_FALLBACK_PACKS), apps_dir), key=_sort_key)
    for name in names:
        full = os.path.join(target, name)
        try:
            with open(full, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            # A file that is listed but will not open or parse is corrupt, never
            # merely absent - the vocabulary shrinks by one pack.
            config_health.record(
                "rulepacks", full, config_health.UNPARSEABLE,
                f"Rulepack '{name}' could not be read: {exc}", reduced=True,
            )
            continue
        problem = validate_pack(data)
        if problem is not None:
            config_health.record(
                "rulepacks", full, config_health.INVALID,
                f"Rulepack '{name}': {problem}", reduced=not _valid(data),
            )
        if _valid(data):
            packs.append(data)
    if not packs:
        if names:
            config_health.record(
                "rulepacks", target, config_health.INVALID,
                "No rulepack in the directory was usable.", reduced=False,
            )
        packs = list(_FALLBACK_PACKS)
    return sorted(_apply_app_manifests(packs, apps_dir), key=_sort_key)


def _sort_key(pack: dict[str, Any]) -> tuple[int, str]:
    """Packs are consulted in declared order; surface breaks ties."""
    try:
        order = int(pack.get("order", 999))
    except (TypeError, ValueError):
        order = 999
    return (order, str(pack.get("surface", "")))


def tier_rank(tier: str) -> int:
    """Rank of a routing tier; an unknown tier is treated as the safe middle."""
    return TIER_RANK.get((tier or "").strip().lower(), TIER_RANK[DEFAULT_TIER])


def assemble(
    tier: str = DEFAULT_TIER, packs: Optional[list[dict[str, Any]]] = None
) -> list[dict[str, Any]]:
    """The rulepacks a given routing tier loads, in consultation order.

    Over-routing is safe and under-routing is not, so an unrecognised
    ``min_tier`` is treated as ``light`` (load it) rather than dropped.
    """
    active = tier_rank(tier)
    out: list[dict[str, Any]] = []
    for pack in (packs if packs is not None else load_packs()):
        needed = TIER_RANK.get(str(pack.get("min_tier", "light")).lower(), 0)
        if active >= needed:
            out.append(pack)
    return out


def keywords_for(
    surface: str, packs: Optional[list[dict[str, Any]]] = None
) -> list[str]:
    """The keyword list of one surface, in file order. Empty if unknown."""
    for pack in (packs if packs is not None else load_packs()):
        if pack.get("surface") == surface:
            return list(pack.get("keywords") or [])
    return []


def _word_re(words: list[str]) -> re.Pattern[str]:
    """Word-boundary alternation over a vocabulary list."""
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b")


def matches(pack: dict[str, Any], lower_text: str) -> bool:
    """True when a lower-cased message hits this pack.

    ``substring`` packs hit on any keyword appearing anywhere (the router's
    long-standing behaviour). ``word`` packs with ``require: verb_and_object``
    need both halves on a word boundary, which is how the action-intent net
    avoids firing on a message that merely contains a verb.
    """
    if pack.get("match") == "word":
        verbs = list(pack.get("verbs") or [])
        objects = list(pack.get("objects") or [])
        if pack.get("require") == "verb_and_object":
            if not verbs or not objects:
                return False
            return bool(_word_re(verbs).search(lower_text)
                        and _word_re(objects).search(lower_text))
        vocabulary = verbs + objects + list(pack.get("keywords") or [])
        return bool(vocabulary) and bool(_word_re(vocabulary).search(lower_text))

    for phrase in (pack.get("keywords") or []):
        if phrase in lower_text:
            return True
    return False


def examples_for(pack: dict[str, Any]) -> list[str]:
    """The published example phrasings for a pack.

    These are what the phrasebook prints, and they are the ONLY thing it
    prints. The matcher tokens are internals: "librar" and "control the" are
    substrings the router happens to look for, not sentences, and publishing
    them taught a reader our implementation instead of our interface.

    Examples are hand-written for a person to read, then held to the machine by
    a test that routes every one of them through the real matcher. An example
    that stops working is a failing test, not a stale doc.
    """
    return [str(e) for e in (pack.get("examples") or [])]


def title_for(pack: dict[str, Any]) -> str:
    """The published heading for a pack; falls back to the surface tag."""
    title = pack.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return str(pack.get("surface", "unknown")).replace("_", " ").title()


__all__ = [
    "TIER_RANK",
    "DEFAULT_TIER",
    "load_packs",
    "assemble",
    "keywords_for",
    "matches",
    "examples_for",
    "title_for",
    "tier_rank",
]
