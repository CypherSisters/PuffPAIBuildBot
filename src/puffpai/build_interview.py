"""The build interview — "what are you trying to build today?"

Step 3b of the wizard. The census asks what the machine *is*; this asks what the
owner is *making*. A goal in plain words ("a bot that knows everything about
space", "help me run the greenhouse") is matched against a bundled, offline
catalog of real, first-party capabilities, and the wizard proposes what fits.
The owner picks; a later step installs.

Honesty and safety, both load-bearing:

* **Offline and first-party in v1.** The catalog is bundled
  (``config/capability_catalog.json``); matching does no network lookup, so the
  interview works with the WAN unplugged. Only real packs, apps and integrations
  are offered. Community and store items are not proposed until the untrusted-
  contribution review model exists.
* **The goal is data, never an instruction.** The typed goal is sanitised, used
  only for keyword matching, and stored in the manifest as a record. It is never
  concatenated into an agent prompt *as instructions*: since the build-context
  block exists (:mod:`puffpai.build_context`) the goal does reach the model, but
  only re-sanitised, capped, quoted, and under a standing sentence saying a
  quotation is data whatever it says. A bot that cannot be told what it is for is
  a bot that does not know what it is - that was the trade, and quoting is how it
  is paid for.
* **The goal also trims the build.** Every catalog item declares the *surfaces*
  it needs, and the match proposes a surface allowlist (:func:`derive_surfaces`)
  with a one-line reason each. A space bot does not need the security cameras.
  Surfaces outside the allowlist are not loaded - not hidden, not loaded - so
  the trimming is a smaller attack surface, not a cosmetic filter. The wizard
  proposes; the owner adds and removes; nothing is auto-applied.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from puffpai import config_health

# Must not be lower than the CLI's prompt cap (cli.GOAL_MAX_CHARS). The
# wizard invites a paragraph and tells the owner honestly when it trims
# at 1000; a second, silent cut here would be exactly the defect that
# invitation exists to remove. A test pins the two together.
_MAX_GOAL_LEN = 1000

_FALLBACK_CATALOG: dict[str, Any] = {
    "schema": "capability_catalog/v2",
    "_surface_taxonomy": {
        "house.lights": {"label": "Lights and switches", "sensitive": False},
        "comms.calendar": {"label": "Calendar and events", "sensitive": False},
    },
    "items": [
        {"id": "app:calendar", "kind": "app", "label": "Calendar, events and tasks",
         "domain": "home", "tags": ["calendar", "schedule", "reminders", "home", "family"],
         "surfaces": ["comms.calendar"]},
        {"id": "integration:home_assistant", "kind": "integration",
         "label": "Home Assistant, smart home", "domain": "home",
         "tags": ["smart home", "home automation", "lights", "devices", "home assistant"],
         "surfaces": ["house.lights"]},
    ],
}

# How we speak about each kind, in owner-facing order.
_KIND_ORDER = ("knowledge_pack", "integration", "app")
_KIND_LABEL = {
    "knowledge_pack": "Knowledge packs",
    "integration": "Integrations",
    "app": "Apps",
}


def _catalog_path() -> str:
    from puffpai.config import get_config
    return os.path.join(get_config().REPO_ROOT, "config", "capability_catalog.json")


def validate_catalog(data: Any) -> Optional[str]:
    """Structural check of a parsed capability catalog; None means healthy.

    Parsing is not health. A catalog that parses but has no items, or whose items
    name surfaces outside its own ``_surface_taxonomy``, is corrupt in exactly
    the way that matters: the wizard would offer the owner a build it cannot
    actually assemble.
    """
    if not isinstance(data, dict):
        return "The catalog is not a JSON object."
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return "The catalog has no items."
    taxonomy = data.get("_surface_taxonomy")
    known = set(taxonomy) if isinstance(taxonomy, dict) else set()
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("id"):
            return f"Item {index} has no id."
        surfaces = item.get("surfaces")
        if not isinstance(surfaces, list) or not surfaces:
            return f"Item '{item.get('id')}' names no surfaces."
        unknown = [str(s) for s in surfaces if str(s) not in known]
        if unknown:
            return (
                f"Item '{item.get('id')}' names surfaces outside the taxonomy: "
                f"{', '.join(sorted(unknown))}."
            )
    return None


def load_catalog(path: Optional[str] = None) -> dict[str, Any]:
    """Load the bundled capability catalog, degrading to a tiny fallback.

    An absent catalog is legitimate (a fresh clone) and falls back silently. A
    catalog that is present but unreadable or structurally invalid is a corrupt
    first-party config: the fallback is used so the wizard still finishes, but
    the degradation is recorded in :mod:`puffpai.config_health` and the build
    says so rather than quietly offering two items instead of seventeen.
    """
    target = path or _catalog_path()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        config_health.note_load_failure("capability_catalog", target, exc, reduced=True)
        return _FALLBACK_CATALOG
    problem = validate_catalog(data)
    if problem is None:
        return data  # type: ignore[no-any-return]
    config_health.record(
        "capability_catalog", target, config_health.INVALID, problem, reduced=True,
    )
    return _FALLBACK_CATALOG


def installed_catalog(catalog: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The catalog with its app rows reduced to the apps this build has.

    The catalog file is a tag dictionary, not an inventory. It says what the
    word "pantry" has to do with FreshTable; it does not get to say FreshTable
    is here. That answer lives in ``apps/``, so an owner who removes an app
    stops being offered it - a wizard proposing an app that is not installed is
    the same defect as a sidebar linking to one.

    Only ``kind: "app"`` rows are touched. Knowledge packs and integrations are
    not installed as folders and are left exactly as the file has them.

    An installed app with no catalog row gets one derived from its manifest, so
    a stranger's app is proposable without a core edit. Its tags come from the
    words it already declares to the router; a derived row is thinner than a
    hand-tagged one, and that is the honest outcome rather than a promise the
    manifest never made.

    Never raises: if the app scan fails, the catalog is returned untouched. A
    wizard offering one app too many is a smaller harm than a wizard offering
    none.
    """
    cat = catalog if catalog is not None else load_catalog()
    items = cat.get("items")
    if not isinstance(items, list):
        return cat

    try:
        from puffpai import app_registry
        # Off first apps too: the wizard suggests them, and her Yes turns
        # them on (puffpai.first_apps).
        manifests = app_registry.load_manifests(include_off=True)
    except Exception:  # pragma: no cover - defensive; the scan is quiet already
        return cat

    installed = {str(m.get("id")): m for m in manifests}
    rows_by_id = {
        str(i.get("id")): i for i in items
        if isinstance(i, dict) and str(i.get("kind")) == "app"
    }

    kept: list[dict[str, Any]] = [
        i for i in items
        if not (isinstance(i, dict) and str(i.get("kind")) == "app")
    ]
    for app_id, manifest in installed.items():
        row = rows_by_id.get(f"app:{app_id}")
        kept.append(row if row is not None else _derived_app_row(manifest))

    out = dict(cat)
    out["items"] = kept
    return out


def _derived_app_row(manifest: dict[str, Any]) -> dict[str, Any]:
    """A catalog row for an installed app the catalog file has never heard of.

    App-supplied strings land in a label and a tag list - matched and rendered,
    never concatenated into the model's instructions. Tags are lowercased and
    stopwords dropped, because a tag no goal can ever match is dead vocabulary
    that only makes the scan longer.
    """
    app_id = str(manifest.get("id"))
    vocabulary = manifest.get("vocabulary")
    words = (vocabulary.get("keywords") or []) if isinstance(vocabulary, dict) else []
    tags: list[str] = []
    for raw in [app_id] + [str(w) for w in words]:
        tag = str(raw).strip().lower()
        if not tag or tag in tags:
            continue
        if all(word in _STOPWORDS for word in tag.split()):
            continue
        tags.append(tag)
    name = str(manifest.get("name") or app_id)
    blurb = str(manifest.get("blurb") or "").rstrip(".")
    return {
        "id": f"app:{app_id}",
        "kind": "app",
        "label": f"{name}, {blurb}" if blurb else name,
        "domain": "app",
        "tags": tags,
        "surfaces": [str(s) for s in (manifest.get("surfaces") or [])],
    }


def _sanitize_goal(goal: str) -> str:
    """Trim, cap length, and strip control characters. The goal is only ever data."""
    cleaned = "".join(ch for ch in (goal or "") if ch.isprintable())
    return cleaned.strip()[:_MAX_GOAL_LEN]


# Function words and filler. None of these may ever score: a goal that says
# "a bot that knows everything and helps with stuff" has told us nothing, and a
# wizard that answers it with the entire catalog is lying about understanding.
#
# The last two lines are the household filler that caused the defect this list
# was widened for: "a garden and greenhouse assistant for a small family farm"
# was answered with "Calendar", because the word FAMILY happened to be a tag on
# the calendar app. Nobody asking for a greenhouse bot has asked for a calendar.
# Words like family, kids, small and everyday describe who a build is for, never
# what it does, so they are filler here and the tags that used to carry them
# were removed from the catalog in the same change.
# Filtering happens on the goal side, so a catalog tag from this list is dead
# vocabulary - test_catalog_tags_are_not_stopwords keeps that from creeping in.
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those there here
is are was were be been being am it its they them their we our us you your
yours i me my mine he she his her him do does did doing done can could would
should will shall may might must of to in on at for with from by about into
over under out up down off as so such not no nor only just very really quite
lot lots more most much many all any some every each other another same
want wants wanted need needs needed like likes liked help helps helped helping
know knows knew knowing understand understands learn learns tell tells show shows
give gives get gets got make makes made use uses used using run runs running
keep keeps keeping build builds building set sets setting have has had having
everything anything something nothing thing things stuff bit
bot bots ai assistant agent robot computer machine box
please thanks good great best better new nice cool
myself someone anyone everyone people
what when where which who whom how why
also too still even ever never always
family families household households kid kids child children wife husband
small little big large simple basic everyday personal private own kind sort
""".split())

# The minimum score an item needs before the wizard will put it on the menu.
#
# Chosen from measured behaviour, not roundness. Across ten real goals (space,
# astronomy, smart home, fitness, medical, homestead, media, and three vague or
# nonsense ones) every genuinely relevant item scored 2 or more and every one of
# those scores included at least one exact catalog-tag hit; every noise item
# scored exactly 1, always from label-word overlap alone with no tag hit. The
# separation is clean, so the floor is "one real tag match" rather than a
# tunable number. :func:`_match` enforces that structurally as well, by
# requiring tag-kind evidence - the floor is the consequence, not the rule.
_SCORE_FLOOR = 2

_WEIGHTS = {"tag_phrase": 3, "tag": 2, "label": 1}

#: Which wizard step offers to scaffold a persona for a goal nothing matches.
#: Named here so the sentence below and the CLI step that honours it cannot
#: drift apart; ``cli._setup_interview_step`` is that step.
SCAFFOLD_STEP = "step 6 of 14"

#: What the owner is told when the catalog has nothing for their goal.
#:
#: The old sentence said "no strong matches ... add any pack by hand", which
#: read as "we might have something, look harder". A goal we ship nothing for is
#: a fact, and the useful next move is not a shopping list: it is a blank
#: persona with the owner's own words in it. Absence is not limitation.
NO_MATCH_MESSAGE = (
    "Nothing we ship matches that yet. You can still build it: we will "
    f"scaffold a blank persona for it ({SCAFFOLD_STEP}) - an app of your own "
    "with your words, your lists and its own page - and you can add packs, "
    "apps and integrations by hand at any time."
)


#: The shipped apps that already keep LISTS THE OWNER WRITES INTO. A goal that
#: matched one of these has somewhere to put things, so the scaffold offer
#: defaults to NO - not because the offer is wrong, but because saying yes would
#: give the owner a second shopping list next to FreshTable's.
#:
#: Alarmflow and trivia are deliberately NOT here. A timer is not a list and a
#: quiz is not a list, and treating them as one is how the garden goal - which
#: matches alarmflow, because "remind me when to water" is a reminder - lost its
#: default all over again by a different route.
HOUSEHOLD_APP_IDS = frozenset({
    "app:calendar", "app:freshtable", "app:fitfam",
})

#: The question the offer asks, in both wizards, worded once.
SCAFFOLD_QUESTION = "Want a place to keep lists for this?"

#: How many list names the offer suggests.
MAX_SUGGESTED_LISTS = 3


#: Where a goal ASKS for a list: "a planting list", "a list of my seeds",
#: "a log of oil changes", "keep track of my harvests". The word caught is the
#: one tied to the list, never any other word of the sentence.
_LIST_ASKS = (
    re.compile(r"\b([a-z0-9]+)\s+(?:list|lists|log|logs|tracker|inventory)\b"),
    re.compile(r"\b(?:list|lists|log|logs|inventory|track)\s+of\s+"
               r"(?:my\s+|our\s+|the\s+|all\s+|every\s+)?([a-z0-9]+)"),
    re.compile(r"\bkeep\s+track\s+of\s+(?:my\s+|our\s+|the\s+)?([a-z0-9]+)"),
)


def suggested_lists(goal: str) -> list[str]:
    """List names to suggest for a goal: only lists the owner asked for.

    A goal that never asks for a list suggests none. Splitting "a bot for my
    2014 Honda Civic" into lists called 2014, honda and civic made the bot's
    own subject words into list names, and ordinary questions about the car
    were then answered "Which list - honda, civic or 2014?". So only the word tied to a list is taken ("a planting list",
    "a log of oil changes" -> oil, "keep track of my harvests").

    Strictly their words, in the order they typed them, filtered to things that
    can legally be a list name. Nothing is pluralised and nothing is invented.
    """
    from puffpai import persona_scaffold

    clean = _sanitize_goal(goal or "")
    content = _content_tokens(clean)
    lower = clean.lower()
    asked: list[tuple[int, str]] = []
    for pattern in _LIST_ASKS:
        for match in pattern.finditer(lower):
            asked.append((match.start(1), match.group(1)))
    out: list[str] = []
    for _at, word in sorted(asked):
        if word not in content or word in out:
            continue
        if len(word) < 4 or len(word) > persona_scaffold.MAX_LIST_KEY_LEN:
            continue
        out.append(word)
        if len(out) >= MAX_SUGGESTED_LISTS:
            break
    return out


def scaffold_offer(proposal: dict[str, Any]) -> dict[str, Any]:
    """Whether to offer a place to keep lists, and what to default to.

    **Always offered.** The old rule offered the scaffold only when the catalog
    matched NOTHING, and that is exactly the defect: "a garden and greenhouse
    assistant" hits Wikipedia's tags, so it matched something, so the owner with
    the most obvious need for a plantings list was the one person never asked.
    Matching a knowledge pack is not somewhere to write a tomato down.

    **Defaults to yes.** The place is no longer only somewhere to keep lists:
    it is what puts the bot in the menu, on the home page and in search. A goal
    that brushes a household app ("current space events" matches the calendar)
    used to flip the default to no, and an owner who pressed Enter ended up
    with a bot they could not find. What changes with the match is the REASON
    shown, never the default. Never raises.
    """
    matched = [str(e.get("id")) for e in (proposal or {}).get("flat") or []]
    household = sorted(i for i in matched if i in HOUSEHOLD_APP_IDS)
    goal = str((proposal or {}).get("goal") or "")

    if household:
        reason = (
            "Your goal already matched " + ", ".join(
                i.split(":", 1)[-1] for i in household)
            + ", which keeps lists of its own. This place is still worth "
              "having: it is what puts your bot in the menu and on the home "
              "page. Say no if you only want what already matched.")
    elif matched:
        reason = ("What matched your goal is knowledge and integrations, not "
                  "somewhere to write things down. This is that place.")
    else:
        reason = ("Nothing we ship matches your goal, so this is the whole of "
                  "what your bot will keep for you.")

    return {
        "offered": True,
        "default_yes": True,
        "question": SCAFFOLD_QUESTION,
        "reason": reason,
        "step": SCAFFOLD_STEP,
        "suggested_lists": suggested_lists(goal),
        "matched_household": household,
    }


def _tokens(text: str) -> set[str]:
    """Lowercase alphanumeric word tokens."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _content_tokens(text: str) -> set[str]:
    """Goal tokens that carry meaning: no stopwords, nothing under three characters."""
    return {w for w in _tokens(text) if w not in _STOPWORDS and len(w) >= 3}


def _tag_hit(tag: str, goal_tokens: set[str]) -> bool:
    """Does a single-word tag appear in the goal, allowing a simple plural?

    "telescopes" matches the tag ``telescope`` and "workouts" matches
    ``workout``. Deterministic and offline - a one-character equivalence, not a
    stemmer - so this still works with the WAN unplugged and cannot surprise us.
    """
    return (
        tag in goal_tokens
        or (tag + "s") in goal_tokens
        or (tag.endswith("s") and tag[:-1] in goal_tokens)
    )


def _match(
    item: dict[str, Any],
    goal_text: str,
    goal_tokens: set[str],
) -> tuple[int, list[dict[str, Any]]]:
    """Score a catalog item against a goal, and say which terms did the scoring.

    Returns ``(score, evidence)``. Every evidence entry names the specific term
    that matched, so a proposal can always justify itself in the owner's words -
    and an item that cannot cite a term cannot be proposed at all. That is what
    makes an unjustifiable proposal structurally impossible rather than merely
    unlikely: :func:`propose` refuses anything without tag-kind evidence, so
    there is no code path that writes a reason it cannot support.

    Every cited term comes from the *catalog* (a tag, or a word in our own
    label), never from the owner's typing, so the goal-as-data rule holds even
    though the reasons are owner-facing.

    A multi-word tag ("smart home", "first aid") scores when the whole phrase is
    present. Single-word tags score on token match. Label and domain words are a
    weak reinforcing signal only: on their own they can never reach the floor,
    which is precisely the incidental-word noise that used to leak through.
    """
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()

    for raw in item.get("tags", []):
        tag = str(raw).lower()
        if " " in tag:
            if tag in goal_text:
                evidence.append({"term": tag, "kind": "tag_phrase", "weight": _WEIGHTS["tag_phrase"]})
                seen.add(tag)
        elif _tag_hit(tag, goal_tokens):
            evidence.append({"term": tag, "kind": "tag", "weight": _WEIGHTS["tag"]})
            seen.add(tag)

    label_tokens = _content_tokens(f"{item.get('label', '')} {item.get('domain', '')}")
    for word in sorted(goal_tokens & label_tokens):
        if word not in seen:
            evidence.append({"term": word, "kind": "label", "weight": _WEIGHTS["label"]})

    if not any(e["kind"] in ("tag", "tag_phrase") for e in evidence):
        # Label overlap alone is not evidence of anything. Score it zero rather
        # than let it accumulate into a proposal nobody can explain.
        return 0, []

    evidence.sort(key=lambda e: (-int(e["weight"]), str(e["term"])))
    return sum(int(e["weight"]) for e in evidence), evidence


def propose(
    goal: str,
    crew_budget: Optional[dict[str, Any]] = None,
    catalog: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Propose matching capabilities for a goal, grouped by kind.

    Never raises. An empty or no-match goal returns a friendly message and the
    full first-party menu is still reachable by the owner browsing; this step
    only *suggests*.
    """
    # An explicitly supplied catalog is the caller's and is used as given. The
    # bundled one goes through installed_catalog, so the wizard proposes the
    # apps this build actually carries rather than the five that were shipped.
    cat = catalog or installed_catalog()
    items: list[dict[str, Any]] = list(cat.get("items") or [])
    clean_goal = _sanitize_goal(goal)
    goal_text = clean_goal.lower()
    goal_tokens = _content_tokens(clean_goal)

    scored: list[tuple[int, dict[str, Any], list[dict[str, Any]]]] = []
    if goal_tokens:
        for item in items:
            s, evidence = _match(item, goal_text, goal_tokens)
            if s >= _SCORE_FLOOR and evidence:
                scored.append((s, item, evidence))
    scored.sort(key=lambda triple: (-triple[0], str(triple[1].get("id"))))

    matched: dict[str, list[dict[str, Any]]] = {kind: [] for kind in _KIND_ORDER}
    flat: list[dict[str, Any]] = []
    for s, item, evidence in scored:
        entry = {
            "id": item.get("id"),
            "label": item.get("label"),
            "kind": item.get("kind"),
            "domain": item.get("domain"),
            "note": item.get("note"),
            "surfaces": [str(x) for x in (item.get("surfaces") or [])],
            "score": s,
            "evidence": evidence,
            "matched_terms": [str(e["term"]) for e in evidence],
        }
        flat.append(entry)
        matched.setdefault(str(item.get("kind")), []).append(entry)

    result: dict[str, Any] = {
        "goal": clean_goal,
        "matched": matched,
        "flat": flat,
        "crew_budget": crew_budget,
        "message": None,
        "note": (
            "A build is shaped by the knowledge packs, apps, and integrations you "
            "add on top of the master team. Pick what fits; the wizard installs it "
            "in a later step."
        ),
    }
    if not clean_goal:
        result["message"] = "Tell the wizard what you want the bot to be good at, and it will suggest a starting set."
    elif not goal_tokens:
        # The goal was all filler. The honest answer is a better question, not
        # the whole catalog offered at a score of one.
        result["message"] = (
            "That could mean almost anything, so the wizard is not going to guess. "
            "Name something concrete: a subject it should know, a room or device it "
            "should reach, or a chore it should take off your hands."
        )
    elif not flat:
        result["message"] = NO_MATCH_MESSAGE
    # The offer is unconditional now - see :func:`scaffold_offer` for why a
    # keyword hit used to hide it. The flag stays on the proposal because both
    # wizards read it from here.
    result["scaffold_offer"] = True
    return result


def proposal_summary_lines(proposal: dict[str, Any]) -> list[str]:
    """Plain-language lines describing a proposal, for the CLI/API."""
    lines: list[str] = []
    if proposal.get("message"):
        lines.append(str(proposal["message"]))

    for kind in _KIND_ORDER:
        group = proposal.get("matched", {}).get(kind) or []
        if not group:
            continue
        lines.append(f"{_KIND_LABEL[kind]}:")
        for entry in group:
            note = f" - {entry['note']}" if entry.get("note") else ""
            lines.append(f"  - {entry.get('label')}{note}")

    budget = proposal.get("crew_budget") or {}
    if budget.get("max_specialists"):
        lines.append(
            f"Your hardware has room for up to {budget['max_specialists']} "
            f"specialists on top of the master team."
        )
    return lines


# ---------------------------------------------------------------------------
# surface trimming - the goal derives an allowlist
# ---------------------------------------------------------------------------

_FALLBACK_SURFACE_LABEL = {
    "house.lights": "Lights and switches",
    "house.climate": "Thermostat and climate",
    "house.sensors": "Household sensors",
    "house.cameras": "Security cameras",
    "media.video": "Video library and playback",
    "media.audio": "Music and radio",
    "media.books": "Book and reading library",
    "knowledge.library": "Offline reference library",
    "knowledge.medical": "Medical reference",
    "knowledge.maps": "Maps and places",
    "instruments.telescope": "Telescope and sky feeds",
    "comms.calendar": "Calendar and events",
    "comms.timers": "Timers and alarms",
    "daily.kitchen": "Pantry and meals",
    "daily.fitness": "Fitness tracking",
    "daily.games": "Games and trivia",
    "daily.notes": "Notes and lists",
    "daily.weather": "Weather and forecast",
}

_SENSITIVE_BY_DEFAULT = frozenset({"house.cameras", "knowledge.medical", "daily.fitness"})

_SURFACES_NOTE = (
    "Surfaces outside this list are not loaded at all - not hidden, not loaded. "
    "This is a proposal: add or remove anything before you accept it."
)


def surface_taxonomy(catalog: Optional[dict[str, Any]] = None) -> dict[str, dict[str, Any]]:
    """The closed set of surfaces a build knows how to load, with labels.

    Read from the catalog's ``_surface_taxonomy`` block so the taxonomy and the
    items that reference it stay in one file. Degrades to a baked-in copy, the
    same way :func:`load_catalog` does, so this still works on a broken install.
    """
    cat = catalog or load_catalog()
    raw = cat.get("_surface_taxonomy")
    out: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict) and raw:
        for key, value in raw.items():
            entry = value if isinstance(value, dict) else {}
            sid = str(key)
            out[sid] = {
                "label": str(entry.get("label") or _FALLBACK_SURFACE_LABEL.get(sid, sid)),
                "sensitive": bool(entry.get("sensitive", sid in _SENSITIVE_BY_DEFAULT)),
            }
        out.update(_persona_surfaces())
        return out
    for sid, label in _FALLBACK_SURFACE_LABEL.items():
        out[sid] = {"label": label, "sensitive": sid in _SENSITIVE_BY_DEFAULT}
    out.update(_persona_surfaces())
    return out


def _persona_surfaces() -> dict[str, dict[str, Any]]:
    """One ``persona.<id>`` surface per persona the owner made. Never raises.

    The catalog's list is closed because an APP may not invent a capability: a
    manifest that names ``house.cameras`` is claiming membership of something
    this build knows how to load, and a stranger's JSON does not get to add to
    that. A persona the owner scaffolded is the other case - it is a page this
    build really has, made on this box by the person who owns it - so its
    surface is real, and it exists exactly as long as its folder does. Nothing
    here can be forged by a manifest: the ids come from the OWNER's app
    directory, not from any file's ``surfaces`` block.
    """
    from puffpai import persona_scaffold

    try:
        return persona_scaffold.surface_taxonomy_entries()
    except Exception:  # pragma: no cover - the taxonomy is never worth an error
        return {}


def derive_surfaces(
    goal_match: dict[str, Any],
    catalog: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Derive the proposed surface allowlist from a goal match.

    Takes the result of :func:`propose` and returns the surfaces those matched
    items need, each with the one line saying why it is on the list ("your
    telescope feed, because you asked for a space bot"). A surface is claimed by
    the highest-scoring item that needs it, so the reason names the thing the
    owner actually recognised.

    The goal is carried through as data - re-sanitised here, used only as a label
    in a reason line, never concatenated into a prompt. Nothing outside the
    taxonomy can enter the allowlist, so an edited catalog cannot invent a
    surface the loader has never heard of. Never raises; a no-match goal yields
    an empty allowlist and a message, not an error.
    """
    cat = catalog or installed_catalog()
    taxonomy = surface_taxonomy(cat)
    by_id = {str(item.get("id")): item for item in (cat.get("items") or [])}

    clean_goal = _sanitize_goal(str((goal_match or {}).get("goal") or ""))
    entries = list((goal_match or {}).get("flat") or [])

    detail: list[dict[str, Any]] = []
    seen: set[str] = set()
    unknown: list[str] = []

    for entry in entries:
        terms = [str(t).strip() for t in (entry.get("matched_terms") or []) if str(t).strip()]
        if not terms:
            # No citable term, no proposal. This is the structural guarantee: a
            # reason that cannot cite evidence is unwriteable, not merely
            # discouraged, so a surface can never arrive with a made-up excuse.
            continue

        surfaces = entry.get("surfaces")
        if not surfaces:
            surfaces = by_id.get(str(entry.get("id")), {}).get("surfaces") or []
        label = str(entry.get("label") or entry.get("id") or "that item")
        term = terms[0]
        for raw in surfaces:
            sid = str(raw)
            if sid in seen:
                continue
            if sid not in taxonomy:
                if sid not in unknown:
                    unknown.append(sid)
                continue
            seen.add(sid)
            surface_label = str(taxonomy[sid]["label"])
            detail.append({
                "surface": sid,
                "label": surface_label,
                "sensitive": bool(taxonomy[sid]["sensitive"]),
                "from": entry.get("id"),
                "matched_terms": terms,
                "reason": (
                    f"{surface_label}, because you asked about '{term}' "
                    f"and that comes with {label}."
                ),
            })

    detail.sort(key=lambda d: str(d["surface"]))
    allowlist = [str(d["surface"]) for d in detail]

    result: dict[str, Any] = {
        "goal": clean_goal,
        "surfaces": allowlist,
        "detail": detail,
        "reasons": {str(d["surface"]): str(d["reason"]) for d in detail},
        "sensitive": [str(d["surface"]) for d in detail if d["sensitive"]],
        "available": sorted(taxonomy.keys()),
        "note": _SURFACES_NOTE,
        "message": None,
    }
    if unknown:
        result["message"] = (
            "Some catalog items name surfaces this build does not know how to "
            "load; those were left out."
        )
    elif not allowlist:
        result["message"] = (
            "Nothing matched yet, so no surfaces are proposed. Add the ones you "
            "want by hand - a build that loads almost nothing is a valid build."
        )
    return result


def apply_surface_choices(
    derivation: dict[str, Any],
    add: Optional[list[str]] = None,
    remove: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Apply the owner's additions and removals to a proposed allowlist.

    The wizard proposes; this is where the owner's answer lands. Additions must
    be surfaces the taxonomy knows about; anything else is dropped with a note
    rather than silently accepted. Removals always win over additions.
    """
    proposed = list(derivation.get("surfaces") or [])
    available = set(str(s) for s in (derivation.get("available") or []))
    reasons = dict(derivation.get("reasons") or {})
    detail = {str(d.get("surface")): d for d in (derivation.get("detail") or [])}

    chosen: list[str] = [str(s) for s in proposed]
    notes: list[str] = []

    for raw in add or []:
        sid = str(raw).strip()
        if sid not in available:
            notes.append(f"Ignoring '{sid}': not a surface this build can load.")
            continue
        if sid not in chosen:
            chosen.append(sid)
            reasons[sid] = f"{sid}, because you added it by hand."

    removed = {str(r).strip() for r in (remove or [])}
    for sid in sorted(removed & set(chosen)):
        notes.append(f"Removed '{sid}': it will not be loaded.")
        reasons.pop(sid, None)
    chosen = sorted(sid for sid in chosen if sid not in removed)

    return {
        "goal": derivation.get("goal", ""),
        "surfaces": chosen,
        "detail": [detail[sid] for sid in chosen if sid in detail],
        "reasons": {sid: str(reasons[sid]) for sid in chosen if sid in reasons},
        "sensitive": [sid for sid in chosen if detail.get(sid, {}).get("sensitive")],
        "available": sorted(available),
        "note": _SURFACES_NOTE,
        "notes": notes,
        "owner_edited": bool(add or remove),
    }


def surfaces_manifest_block(derivation: dict[str, Any]) -> dict[str, Any]:
    """The allowlist, shaped for the manifest's ``surfaces`` block.

    Records the allowlist, why each surface is on it, and whether the owner
    edited the proposal - so a build can say out loud what it is for and what it
    deliberately does not carry.
    """
    surfaces = [str(s) for s in (derivation.get("surfaces") or [])]
    reasons = {
        str(k): str(v) for k, v in (derivation.get("reasons") or {}).items()
        if str(k) in surfaces
    }
    return {
        "allowlist": surfaces,
        "reasons": reasons,
        "sensitive": [str(s) for s in (derivation.get("sensitive") or []) if str(s) in surfaces],
        "derived_from_goal": bool(derivation.get("goal")),
        "owner_edited": bool(derivation.get("owner_edited")),
        "note": _SURFACES_NOTE,
    }


def surfaces_summary_lines(derivation: dict[str, Any]) -> list[str]:
    """Plain-language lines describing the proposed allowlist, for the CLI/API."""
    lines: list[str] = []
    if derivation.get("message"):
        lines.append(str(derivation["message"]))
    surfaces = derivation.get("surfaces") or []
    if surfaces:
        lines.append("Your bot will load these surfaces:")
        reasons = derivation.get("reasons") or {}
        for sid in surfaces:
            lines.append(f"  - {reasons.get(sid, sid)}")
        sensitive = derivation.get("sensitive") or []
        if sensitive:
            lines.append("Worth a second look: " + ", ".join(str(s) for s in sensitive))
    lines.append(str(derivation.get("note") or _SURFACES_NOTE))
    for note in derivation.get("notes") or []:
        lines.append(str(note))
    return lines


__all__ = [
    "installed_catalog",
    "load_catalog",
    "propose",
    "proposal_summary_lines",
    "surface_taxonomy",
    "derive_surfaces",
    "apply_surface_choices",
    "surfaces_manifest_block",
    "surfaces_summary_lines",
    "scaffold_offer",
    "suggested_lists",
    "HOUSEHOLD_APP_IDS",
    "SCAFFOLD_QUESTION",
]
