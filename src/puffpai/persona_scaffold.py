"""Scaffold a persona — a blank app and its pack, written from the owner's words.

The build interview used to end one of two ways: we ship something for your goal,
or you are on your own with a JSON schema. For "a garden and greenhouse assistant
for a small family farm" the honest answer is the second one, and "here is the
manifest reference" is not an answer a person can use at the end of a wizard.

So this module writes the file for them. Given an id, a display name, a goal in
plain words and the lists they want kept, it writes:

* ``apps/<id>/app.json`` - one declarative app: vocabulary derived from the list
  names and the meaningful words of the goal, a ``data.default`` holding those
  lists, and the generic app page as its route, and
* ``packs/<id>/pack.json`` - a one-app pack, so the persona is a thing with a
  name rather than a loose folder.

Both land in the OWNER's directories (:func:`app_registry.user_apps_dir`), not in
the checkout: a pip-installed build must be extendable without editing the
package. Nothing is executed and no core file is edited - the manifest goes
through the real :func:`app_registry.validate_manifest` before it is written, and
installing is still rescanning.

**The goal stays data.** It is sanitised to printable ASCII, capped, and used for
a blurb the dashboard renders as text, keyword tokens for the router's substring
matcher, and - since :mod:`puffpai.build_context` exists - one quoted line of the
system prompt, so the bot can say what it is for. It is never concatenated into
the model's *instructions*, exactly as in :mod:`puffpai.build_interview`.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from puffpai import app_registry

#: The namespace a persona's OWN surface lives in. A scaffolded app used to join
#: ``daily.notes`` - true about its shape, useless as a gate: whether the owner's
#: own bot appeared in the nav then depended on an unrelated surface being on the
#: allowlist, and when it was not, the bot the build exists for was invisible.
#: So a persona carries its own surface, ``persona.<id>``, and scaffolding one
#: ADDS that surface to the build's allowlist. The persona then passes the same
#: strict gate as every other page instead of being excepted from it.
SURFACE_PREFIX = "persona."

#: The surface family, for callers describing the offer before an id exists (the
#: wizard's scaffold step). A real persona's surface is :func:`persona_surface`.
SURFACE = f"{SURFACE_PREFIX}<id>"


def persona_surface(app_id: Any) -> str:
    """The surface id a persona of this id carries: ``persona.<id>``."""
    return f"{SURFACE_PREFIX}{app_id}"


def _known_surfaces(*app_ids: str) -> set[str]:
    """The taxonomy, plus the persona surfaces of the ids being written.

    :func:`app_registry.validate_manifest` refuses a surface the build does not
    know, and the build learns a persona surface by finding the persona on disk
    (see :func:`surface_taxonomy_entries`) - which a persona being written for
    the first time is not yet. Naming it here is not a loosening: it is the same
    id this call is about to create.
    """
    from puffpai import build_interview

    known = set(build_interview.surface_taxonomy())
    known.update(persona_surface(app_id) for app_id in app_ids)
    return known


def scaffolded_ids() -> list[str]:
    """The ids of the apps the OWNER added, read off the folder names.

    A directory listing, not a registry scan: this is called from
    :func:`puffpai.build_interview.surface_taxonomy`, which the scan itself
    calls, so anything heavier would be a loop.
    """
    root = app_registry.user_apps_dir()
    out: list[str] = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        if not app_registry.valid_id(name):
            continue
        if os.path.isfile(os.path.join(root, name, app_registry.MANIFEST_NAME)):
            out.append(name)
    return out


def surface_taxonomy_entries() -> dict[str, dict[str, Any]]:
    """The taxonomy entries for the personas on this build.

    The shipped taxonomy is a closed list on purpose - an app may not invent a
    capability. A persona the owner made is not a capability we ship: it is a
    page this build really has, so the surface that gates it is real too, and it
    exists exactly as long as the persona's folder does.
    """
    return {persona_surface(app_id): {
        "label": _owner_app_label(app_id),
        "sensitive": False,
    } for app_id in scaffolded_ids()}


def _owner_app_label(app_id: str) -> str:
    """The owner's app in words: its own name, and whether it is a live card.

    A live card BuildBot made (it has a ``feed`` block) is not a persona, and
    its folder name is an id nobody should have to read. Never raises.
    """
    try:
        path = os.path.join(app_registry.user_apps_dir(), app_id,
                            app_registry.MANIFEST_NAME)
        with open(path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        manifest = {}
    name = str((manifest or {}).get("name") or app_id).strip()[:60] or app_id
    if isinstance(manifest, dict) and isinstance(manifest.get("feed"), dict):
        return f"{name} (a live card)"
    return f"{name}, a persona you made"


#: Where the generic app page lives. An app with no page of its own is rendered
#: from its store by ``dashboard/app/dashboard/apps/[id]/page.tsx``.
ROUTE_PREFIX = "/dashboard/apps/"

#: Scaffolded apps sort after the shipped five, whose orders are 10..60.
ORDER = 600

MAX_LISTS = 8
MAX_LIST_KEY_LEN = 32
#: How long a display name may be. Shorter than the registry's ceiling on
#: purpose: this one is typed into a form and shown on a tile, and a name that
#: does not fit either is a name nobody chose.
MAX_NAME_LEN = 40
#: Characters that may not appear in a list NAME. ``list_key`` would strip them
#: anyway, which is the problem: an owner who typed ``../secrets`` would get a
#: list quietly called ``secrets`` instead of being told no.
_PATH_CHARS = ("/", "\\", "..", ":", "\0")
MAX_GOAL_CHARS = 1000
MAX_BLURB_CHARS = 180
#: Goal words that become router keywords. Bounded well under the registry's 60:
#: a keyword list padded out with every noun in a paragraph is a matcher that
#: answers for everything.
MAX_GOAL_KEYWORDS = 8

_WORD_RE = re.compile(r"[a-z0-9]+")

#: Ids a persona may not take, because something else already answers to them.
#: Each one is a dashboard route, a folder in the state dir, or a word this
#: product uses for a thing that is not an app - ``apps`` is all three, and a
#: persona that took it landed in ``state/apps/apps`` behind the
#: ``/dashboard/apps`` page. A collision an owner cannot see is a collision they
#: cannot fix, so these are refused where the id is suggested AND where it is
#: typed.
RESERVED_IDS = frozenset({
    "agents", "api", "app", "apps", "avatar", "chat", "config", "dashboard",
    "device", "knowledge", "new", "pack", "packs", "persona", "personas",
    "puffpai", "settings", "setup", "skills", "state", "talk", "vault",
})

#: What to call an id when nothing in the name or the goal makes one.
FALLBACK_ID = "mybot"


def is_reserved_id(app_id: Any) -> bool:
    """True when ``app_id`` is a word this build already uses for something."""
    return isinstance(app_id, str) and app_id.strip().lower() in RESERVED_IDS


class ScaffoldError(ValueError):
    """The persona could not be written, with the reason in plain words."""


def _ascii_plain(text: Any, limit: int) -> str:
    """Printable ASCII, whitespace collapsed, trimmed to ``limit``."""
    raw = text if isinstance(text, str) else str(text or "")
    kept = [ch for ch in raw if ch.isprintable() and ord(ch) < 128]
    return " ".join("".join(kept).split())[:limit].strip()


def list_key(name: str) -> str:
    """A list name as a data key: lowercase words joined by underscores."""
    words = _WORD_RE.findall((name or "").lower())
    return "_".join(words)[:MAX_LIST_KEY_LEN].strip("_")


def list_label(key: str) -> str:
    """A data key as the owner's words again: ``harvest_log`` -> harvest log."""
    return str(key).replace("_", " ").strip()


def _reject_path_chars(raw: Any, what: str) -> None:
    """Refuse anything that looks like it is trying to be a path."""
    text = raw if isinstance(raw, str) else ""
    for bad in _PATH_CHARS:
        if bad in text:
            raise ScaffoldError(
                f"{what} cannot contain '{bad}'. These become folder and key "
                f"names, so they are letters, digits and spaces only.")


def parse_lists(raw: Any) -> list[str]:
    """``"plantings, harvest log"`` -> ``["plantings", "harvest_log"]``.

    Empty means one list named after nothing in particular is NOT invented: an
    app with no lists is a valid app, and guessing a list name the owner never
    said is the same class of lie as guessing a capability.
    """
    if raw is None:
        return []
    parts = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
    keys: list[str] = []
    for part in parts:
        raw_name = str(part).strip()
        if not raw_name:
            continue
        _reject_path_chars(raw_name, f"A list name ({raw_name!r})")
        if len(raw_name) > MAX_LIST_KEY_LEN:
            raise ScaffoldError(
                f"The list name {raw_name!r} is {len(raw_name)} characters; "
                f"{MAX_LIST_KEY_LEN} is the most one list name carries.")
        key = list_key(raw_name)
        if not key:
            raise ScaffoldError(
                f"{raw_name!r} has no letters or digits in it, so there is no "
                f"list name to make out of it.")
        if key not in keys:
            keys.append(key)
    if len(keys) > MAX_LISTS:
        raise ScaffoldError(
            f"That is {len(keys)} lists; {MAX_LISTS} is as many as one "
            f"scaffolded app carries. Name the ones you will actually keep.")
    return keys


def goal_keywords(goal: str) -> list[str]:
    """The meaningful words of a goal, as router keywords.

    Stopword-filtered by the same list the build interview matches with, so
    "a bot that helps with stuff" contributes nothing rather than teaching the
    router to answer for the word "help".
    """
    from puffpai import build_interview
    from puffpai.catalogs import goal_words

    words = build_interview._content_tokens(_ascii_plain(goal, MAX_GOAL_CHARS))
    # Only words goal_words.search_terms keeps, so nothing personal and no
    # news or filler word becomes a router keyword (else "WeddingBot" answers
    # to "sister" and "able").
    keep: set[str] = set()
    for t in goal_words.search_terms(str(goal or ""), limit=24):
        keep |= {t, goal_words.singular(t)}
    ordered = [w for w in _WORD_RE.findall(_ascii_plain(goal, MAX_GOAL_CHARS).lower())
               if w in words and (w in keep or goal_words.singular(w) in keep)]
    out: list[str] = []
    for word in ordered:
        if word not in out and len(word) >= app_registry.MIN_KEYWORD_LEN:
            out.append(word)
    return out[:MAX_GOAL_KEYWORDS]


#: Goal words that are never the topic: feelings, wants, filler and the
#: words for a bot itself. "I love dinosaurs" is about dinosaurs, not love.
NOT_TOPIC_WORDS = frozenset({
    "love", "like", "want", "wants", "need", "needs", "works", "work", "helper",
    "helps", "buddy", "friend", "smart", "keep", "keeps", "running", "stuff",
    "cool", "thing", "things", "every", "everything", "knows", "know", "tips",
    "bot", "bots", "assistant", "about", "shows", "show", "help", "make",
})


def topic_word(goal: str) -> str:
    """The goal's topic in one word: "I love dinosaurs" -> "dinosaur".

    The first word of the bot's one topic phrase
    (:func:`puffpai.bot_identity.topic_phrase`, which also names the daily
    update and the greeting), letters only, lower case, singular: "Help me
    bake bread" -> "bread", never "bake". When the goal has no topic phrase,
    the first meaningful goal word (:data:`NOT_TOPIC_WORDS` skipped). ""
    when nothing fits.
    """
    try:
        from puffpai import bot_identity
        phrase = bot_identity.topic_phrase(str(goal or ""), form="subject")
    except Exception:  # noqa: BLE001 - the plain word list below still works
        phrase = ""
    words = [w.lower() for w in phrase.split() if w.isalpha() and len(w) >= 3]
    for word in words[:1] or [w for w in goal_keywords(str(goal or ""))
                              if w.isalpha() and len(w) >= 3
                              and w not in NOT_TOPIC_WORDS][:1]:
        if word.endswith("s") and not word.endswith("ss") and len(word) > 4:
            word = word[:-1]
        return word
    return ""


def suggest_id(name: str, goal: Any = "") -> str:
    """An id for a persona, from the name it was given and then from its goal.

    "Garden Bot" -> ``garden_bot``; a bot called "Infinity" that is for space
    -> ``infinity``. The NAME comes first because it is the word the owner will
    recognise on a folder and in a URL. When the name makes no id - it has no
    letters, it starts with a digit, or it slugifies to something this build
    already uses (:data:`RESERVED_IDS`) - the first usable noun of the goal is
    offered instead, so the space bot gets ``space`` rather than a word nobody
    typed.

    Returns "" when neither yields anything; the caller asks for an id, or
    falls back to :data:`FALLBACK_ID`, rather than inventing a word.
    """
    candidates = [list_key(name)]
    candidates.extend(goal_keywords(_ascii_plain(goal, MAX_GOAL_CHARS)))
    for key in candidates:
        if key and app_registry.valid_id(key) and not is_reserved_id(key):
            return key
    return ""


def validate_request(app_id: Any, name: Any = "", goal: Any = "",
                     lists: Any = None) -> dict[str, Any]:
    """The one validator both wizards and the API use. Raises on refusal.

    Returns ``{"id", "name", "goal", "lists"}``, cleaned. It is called by
    :func:`scaffold` itself rather than sitting in front of it, because a rule
    the CLI does not enforce is a rule that does not exist: the API and
    ``puffpai persona new`` refuse exactly the same requests.
    """
    if not isinstance(app_id, str) or not app_id:
        raise ScaffoldError(
            "A persona needs an id - lowercase letters, digits and "
            "underscores, starting with a letter.")
    _reject_path_chars(app_id, "An id")
    if not app_registry.valid_id(app_id):
        raise ScaffoldError(
            f"'{app_id}' cannot be an app id. An id is "
            f"{app_registry.MIN_ID_LEN} to {app_registry.MAX_ID_LEN} "
            f"characters, lowercase, starting with a letter, and only letters, "
            f"digits and underscores after that.")
    if is_reserved_id(app_id):
        raise ScaffoldError(
            f"'{app_id}' is a word this build already uses for one of its own "
            f"pages and folders, so a persona cannot be called that. Pick "
            f"another id - '{app_id}bot' is free.")

    raw_name = name if isinstance(name, str) else ""
    _reject_path_chars(raw_name, "A name")
    if len(raw_name.strip()) > MAX_NAME_LEN:
        raise ScaffoldError(
            f"That name is {len(raw_name.strip())} characters; {MAX_NAME_LEN} "
            f"is as long as a name that has to fit on a tile can be.")

    return {
        "id": app_id,
        "name": _ascii_plain(raw_name, MAX_NAME_LEN) or app_id,
        "goal": _ascii_plain(goal, MAX_GOAL_CHARS),
        "lists": parse_lists(lists),
    }


def preview(app_id: Any, name: Any = "", goal: Any = "",
            lists: Any = None) -> dict[str, Any]:
    """What :func:`scaffold` WOULD write, without writing anything.

    The dashboard's "Build a persona" page shows the vocabulary the bot will
    understand before the owner commits to it. That preview is computed here,
    from the same functions the write uses, so the page cannot show one set of
    words and install another - the whole reason it is not derived in the
    browser.

    ``taken`` is True when something with this id is already installed. It is a
    fact about the build, not a refusal: the page greys out Create and says so,
    and :func:`scaffold` is still the thing that refuses.
    """
    request = validate_request(app_id, name, goal, lists)
    manifest = build_app_manifest(request["id"], request["name"],
                                  request["goal"], request["lists"])
    problem = app_registry.validate_manifest(manifest,
                                             _known_surfaces(request["id"]))
    if problem is not None:
        raise ScaffoldError(f"The app this would write is not valid: {problem}")

    return {
        "id": request["id"],
        "name": manifest["name"],
        "goal": request["goal"],
        "blurb": manifest["blurb"],
        "lists": request["lists"],
        "list_labels": [list_label(key) for key in request["lists"]],
        "keywords": list(manifest["vocabulary"]["keywords"]),
        "examples": list(manifest["vocabulary"]["examples"]),
        "says": [str(a["says"]) for a in manifest.get("actions") or []],
        "route": manifest["ui"]["route"],
        "surfaces": list(manifest["surfaces"]),
        "app_path": os.path.join(app_registry.user_apps_dir(), request["id"],
                                 app_registry.MANIFEST_NAME),
        "pack_path": os.path.join(app_registry.user_packs_dir(), request["id"],
                                  app_registry.PACK_MANIFEST_NAME),
        "taken": request["id"] in app_registry.installed_ids(
            app_registry.load_manifests(include_off=True)),
    }


def build_app_manifest(app_id: str, name: str, goal: str = "",
                       lists: Optional[list[str]] = None) -> dict[str, Any]:
    """The ``app.json`` for a scaffolded persona. Pure; writes nothing."""
    keys = list(lists or [])
    display = _ascii_plain(name or app_id, app_registry.MAX_UI_NAME_LEN) or app_id
    clean_goal = _ascii_plain(goal, MAX_GOAL_CHARS)

    keywords: list[str] = []
    for candidate in ([display.lower()]
                      + [list_label(k) for k in keys]
                      + goal_keywords(clean_goal)):
        word = _ascii_plain(candidate, app_registry.MAX_KEYWORD_LEN).lower()
        if (word and word not in keywords
                and len(word) >= app_registry.MIN_KEYWORD_LEN):
            keywords.append(word)
    keywords = keywords[:app_registry.MAX_KEYWORDS]

    examples: list[str] = []
    for key in keys[:3]:
        label = list_label(key)
        examples.append(_ascii_plain(f"what is on the {label} list",
                                     app_registry.MAX_EXAMPLE_LEN).lower())
        examples.append(_ascii_plain(f"add something to the {label} list",
                                     app_registry.MAX_EXAMPLE_LEN).lower())
    examples = examples[:app_registry.MAX_EXAMPLES]

    actions: list[dict[str, Any]] = []
    if keys:
        label = list_label(keys[0])
        actions = [
            {"id": "add_item", "says": f"add something to the {label} list"},
            {"id": "show_list", "says": f"what is on the {label} list"},
            {"id": "remove_item",
             "says": f"remove something from the {label} list"},
        ]

    # The owner's whole goal sentence ("My brother just got engaged ...")
    # must never be the bot's tagline on the home page or the Apps page: the
    # tagline is the bot's plain subject. The goal itself is kept in the app's own record ("goal"),
    # read back by goal_of and never copied to a page.
    blurb = tagline(clean_goal, display)

    manifest: dict[str, Any] = {
        "schema": app_registry.SCHEMA,
        "id": app_id,
        "name": display,
        "blurb": _ascii_plain(blurb, MAX_BLURB_CHARS),
        "goal": clean_goal,
        "version": "1.0.0",
        "author": "You",
        "license": "Apache-2.0",
        "order": ORDER,
        "surfaces": [persona_surface(app_id)],
        "data": {"default": {key: [] for key in keys}},
        "ui": {"route": f"{ROUTE_PREFIX}{app_id}", "icon": "mdiNotebookOutline"},
        "vocabulary": {"keywords": keywords, "examples": examples},
        "doors": [{
            "hosts": [],
            "purpose": "None. A scaffolded persona keeps its lists on this box.",
            "data_sent_plain": "nothing",
        }],
        "credential": {"kind": "none"},
        "requires": {"home_assistant": False, "network": False, "ollama": False},
    }
    if actions:
        manifest["actions"] = actions
    return manifest


def build_pack_manifest(app_id: str, name: str) -> dict[str, Any]:
    """The one-app ``pack.json`` that gives the persona a name."""
    display = _ascii_plain(name or app_id, app_registry.MAX_UI_NAME_LEN) or app_id
    return {
        "schema": app_registry.PACK_SCHEMA,
        "id": app_id,
        "display_name": _ascii_plain(f"{display} pack",
                                     app_registry.MAX_LABEL_LEN),
        "blurb": _ascii_plain(f"The app {display} was scaffolded with.",
                              app_registry.MAX_LABEL_LEN),
        "version": "1.0.0",
        "author": "You",
        "license": "Apache-2.0",
        "posture_min": "offline",
        "apps": [{"id": app_id, "version": "1.0.0"}],
    }


def _write_json(path: str, payload: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _trimming_is_active(surfaces: dict[str, Any]) -> bool:
    """True when this build actually has an allowlist. Same rule as
    :func:`puffpai.self_knowledge._trimming_is_active`, which owns it: an empty
    allowlist on a build that never ran the interview means NOT trimmed."""
    if surfaces.get("derived_from_goal") or surfaces.get("owner_edited"):
        return True
    return bool(surfaces.get("allowlist"))


def _update_build_surfaces(add: Optional[dict[str, str]] = None,
                           drop: Optional[list[str]] = None) -> str:
    """Put persona surfaces on the build's allowlist, or take them off.

    Returns the manifest path when it was rewritten, "" otherwise. Never raises:
    a persona is still installed and still on its page when the build manifest
    is missing or unwritable, and failing the scaffold over it would cost the
    owner the thing they asked for to fix the thing they did not.

    **An UNTRIMMED build is left alone.** Its empty allowlist is what makes the
    dashboard show everything (``_trimming_is_active``); writing one surface into
    it would flip the build to trimmed and hide every other page - the persona
    would be the only thing left. There is nothing to add on such a build anyway:
    nothing is gated out.
    """
    from puffpai import build_manifest

    try:
        record = build_manifest.read_manifest()
        if not isinstance(record, dict):
            return ""
        raw = record.get("surfaces")
        surfaces: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
        if not _trimming_is_active(surfaces):
            return ""
        allowlist = [str(s) for s in (surfaces.get("allowlist") or [])]
        raw_reasons = surfaces.get("reasons")
        reasons = ({str(k): str(v) for k, v in raw_reasons.items()}
                   if isinstance(raw_reasons, dict) else {})

        changed = False
        for surface in drop or []:
            if surface in allowlist:
                allowlist = [s for s in allowlist if s != surface]
                reasons.pop(surface, None)
                changed = True
        for surface, reason in (add or {}).items():
            if surface not in allowlist:
                allowlist.append(surface)
                changed = True
            if reasons.get(surface) != reason:
                reasons[surface] = reason
                changed = True
        if not changed:
            return ""

        surfaces["allowlist"] = sorted(set(allowlist))
        surfaces["reasons"] = {k: v for k, v in reasons.items()
                               if k in surfaces["allowlist"]}
        surfaces["owner_edited"] = True
        record["surfaces"] = surfaces
        return build_manifest.write_manifest(record)
    except Exception:  # pragma: no cover - an unreadable or unwritable manifest
        return ""


def _surface_reason(app_id: str, name: str = "") -> str:
    """The line the manifest records next to a persona's surface."""
    display = _ascii_plain(name, MAX_NAME_LEN) or app_id
    return (f"{display}, the persona you made on this build. Its surface is on "
            f"the list because it is; delete the persona and it comes off.")


def scaffold(app_id: str, *, name: str = "", goal: str = "",
             lists: Any = None, force: bool = False) -> dict[str, Any]:
    """Write a persona's app and pack, validate them, and rescan.

    Returns a record of what was made. Raises :class:`ScaffoldError` with a
    sentence the owner can act on when nothing was written - a refused id, a
    folder that is already there, or a manifest the real validator rejects.
    Validation happens BEFORE the write, so a rejected persona leaves no
    half-installed folder behind.
    """
    request = validate_request(app_id, name, goal, lists)
    app_id = request["id"]
    name = request["name"]
    goal = request["goal"]
    keys = request["lists"]
    manifest = build_app_manifest(app_id, name, goal, keys)
    pack = build_pack_manifest(app_id, name)

    problem = app_registry.validate_manifest(manifest, _known_surfaces(app_id))
    if problem is not None:
        raise ScaffoldError(f"The app this would write is not valid: {problem}")

    apps_root = app_registry.user_apps_dir()
    packs_root = app_registry.user_packs_dir()
    app_folder = app_registry.resolve_within(apps_root, app_id)
    pack_folder = app_registry.resolve_within(packs_root, app_id)
    app_path = os.path.join(app_folder, app_registry.MANIFEST_NAME)
    pack_path = os.path.join(pack_folder, app_registry.PACK_MANIFEST_NAME)

    existing = [p for p in (app_path, pack_path) if os.path.exists(p)]
    if existing and not force:
        raise ScaffoldError(
            f"'{app_id}' already exists here: {', '.join(existing)}. Pick "
            f"another id, or edit that file - nothing of yours is overwritten.")

    for other in app_registry.load_manifests(include_off=True):
        if str(other.get("id")) == app_id:
            raise ScaffoldError(
                f"An app called '{app_id}' is already installed on this build. "
                f"Two apps may not claim one id, so pick another name.")

    _write_json(app_path, manifest)
    _write_json(pack_path, pack)

    # The persona's surface joins the allowlist, so the nav, the home card and
    # the search all gate it the way they gate every other page - no exception,
    # no entry appended behind the gates' back.
    build_path = _update_build_surfaces(
        add={persona_surface(app_id): _surface_reason(app_id, name)})

    manifests = app_registry.rescan()
    installed = app_registry.installed_ids(manifests)
    packs = [p["id"] for p in app_registry.load_packs(manifests=manifests)]

    return {
        "id": app_id,
        "name": manifest["name"],
        "goal": _ascii_plain(goal, MAX_GOAL_CHARS),
        "app_path": app_path,
        "pack_path": pack_path,
        "lists": keys,
        "keywords": list(manifest["vocabulary"]["keywords"]),
        "route": manifest["ui"]["route"],
        "surfaces": list(manifest["surfaces"]),
        "build_manifest": build_path,
        "installed": app_id in installed,
        "pack_installed": app_id in packs,
    }


def _read_manifest_file(app_id: str) -> tuple[str, dict[str, Any]]:
    """One owner persona's ``app.json``, with the path it came from."""
    folder = app_registry.resolve_within(app_registry.user_apps_dir(), app_id)
    path = os.path.join(folder, app_registry.MANIFEST_NAME)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScaffoldError(
            f"'{app_id}' has a manifest that cannot be read ({exc}), so nothing "
            f"was changed. The file is {path}.") from exc
    if not isinstance(manifest, dict):
        raise ScaffoldError(
            f"'{app_id}' has a manifest that is not a JSON object, so nothing "
            f"was changed. The file is {path}.")
    return path, manifest


def _resolve_list_name(raw: Any, existing: list[str]) -> str:
    """One list the owner named, matched against the keys that exist.

    Accepts either the key (``harvest_log``) or the label they read on screen
    (``harvest log``), because those are the same list and refusing one of them
    is a puzzle, not a safeguard.
    """
    key = list_key(_ascii_plain(raw, MAX_LIST_KEY_LEN))
    if key in existing:
        return key
    raise ScaffoldError(
        f"There is no list called {str(raw)!r} on this persona. It keeps: "
        + (", ".join(list_label(k) for k in existing) or "no lists yet")
        + ". Nothing was changed.")


def edit(app_id: str, *, name: Any = None, add_lists: Any = None,
         rename_lists: Any = None) -> dict[str, Any]:
    """Change an owner persona's name and lists, and migrate its store.

    A scaffolded persona used to be write-once: the owner picked "plantings" at
    the wizard, and adding "seed orders" three weeks later meant hand-editing
    JSON and then guessing whether the store would follow. This is that edit.

    What it does, in one transaction as far as the owner is concerned:

    * ``name`` - the display name in ``app.json`` (and the pack's, so the two do
      not drift), with the vocabulary regenerated so the bot answers to the new
      name;
    * ``add_lists`` - new keys in ``data.default``, and new empty lists in the
      live store;
    * ``rename_lists`` - ``{old: new}``; the key changes in ``data.default`` AND
      in the store, and the renamed list keeps every item it had.

    **Nothing is ever dropped, and there is deliberately no ``remove_lists``.**
    Removing a list means deleting rows the owner wrote down, and a PATCH that
    can silently destroy data is the wrong shape for it: an owner who means it
    goes through the delete flow (``DELETE /persona/<id>`` /
    :func:`remove`), which says what will be lost and requires the typed
    confirmation. Renaming covers the case that is actually common - "I called it
    the wrong thing" - without costing anybody an entry.

    The manifest is validated by the real :func:`app_registry.validate_manifest`
    BEFORE it is written, and the store is migrated only after the manifest
    write succeeded, so a refused edit leaves both exactly as they were.
    """
    from puffpai import app_data as app_data_mod

    if not is_user_persona(app_id):
        raise ScaffoldError(
            f"'{app_id}' is not a persona you made, so this will not edit it. "
            f"Only apps under {app_registry.user_apps_dir()} can be changed "
            f"this way; a shipped app is changed by changing the build.")

    path, manifest = _read_manifest_file(app_id)
    old_keys = _lists_in_order(manifest)

    display = str(manifest.get("name") or app_id)
    if name is not None:
        _reject_path_chars(name if isinstance(name, str) else "", "A name")
        cleaned = _ascii_plain(name, MAX_NAME_LEN)
        if not cleaned:
            raise ScaffoldError(
                "That name has no letters or digits in it, so there is no name "
                "to give this persona. Nothing was changed.")
        display = cleaned

    renames: dict[str, str] = {}
    if rename_lists:
        if not isinstance(rename_lists, dict):
            raise ScaffoldError(
                "rename_lists is a mapping of old name to new name, for example "
                "{\"plantings\": \"seedlings\"}. Nothing was changed.")
        for raw_old, raw_new in rename_lists.items():
            old = _resolve_list_name(raw_old, old_keys)
            new = parse_lists([raw_new])
            if len(new) != 1:
                raise ScaffoldError(
                    f"{str(raw_new)!r} is not one list name, so "
                    f"{list_label(old)} was not renamed. Nothing was changed.")
            renames[old] = new[0]

    added = [k for k in parse_lists(add_lists) if k]

    # The new shape, in the owner's own order: existing keys (renamed in place),
    # then the additions. Order is what the app page and the "Try:" line read.
    new_keys: list[str] = []
    for key in old_keys:
        candidate = renames.get(key, key)
        if candidate not in new_keys:
            new_keys.append(candidate)
    for key in added:
        if key not in new_keys:
            new_keys.append(key)
    if len(new_keys) > MAX_LISTS:
        raise ScaffoldError(
            f"That would be {len(new_keys)} lists; {MAX_LISTS} is as many as "
            f"one persona carries. Nothing was changed.")

    rebuilt = build_app_manifest(app_id, display, goal_of(manifest), new_keys)

    # Start from what is on disk so a hand edit survives, and change only the
    # four things this endpoint owns.
    updated = dict(manifest)
    updated["name"] = rebuilt["name"]
    updated["vocabulary"] = _merged_vocabulary(manifest, rebuilt)
    raw_data = manifest.get("data")
    old_block: dict[str, Any] = dict(raw_data) if isinstance(raw_data, dict) else {}
    raw_default = old_block.get("default")
    old_default: dict[str, Any] = (dict(raw_default)
                                   if isinstance(raw_default, dict) else {})
    # A non-list entry in ``data.default`` is some other shape the owner declared
    # (a dict of settings, say). It is not a list and is not this function's
    # business, so it is carried across untouched rather than dropped.
    default: dict[str, Any] = {str(k): v for k, v in old_default.items()
                               if not isinstance(v, list)}
    for key in new_keys:
        default[key] = []
    updated["data"] = {**old_block, "default": default}
    if rebuilt.get("actions"):
        updated["actions"] = rebuilt["actions"]

    problem = app_registry.validate_manifest(updated, _known_surfaces(app_id))
    if problem is not None:
        raise ScaffoldError(
            f"That edit would make the app invalid: {problem}. Nothing was "
            f"changed.")

    _write_json(path, updated)

    pack_path = os.path.join(
        app_registry.resolve_within(app_registry.user_packs_dir(), app_id),
        app_registry.PACK_MANIFEST_NAME)
    if name is not None and os.path.isfile(pack_path):
        try:
            with open(pack_path, "r", encoding="utf-8") as fh:
                pack = json.load(fh)
            if isinstance(pack, dict):
                fresh = build_pack_manifest(app_id, display)
                pack["display_name"] = fresh["display_name"]
                _write_json(pack_path, pack)
        except (OSError, json.JSONDecodeError):
            # The app is the thing the bot reads. A pack label that could not be
            # rewritten is reported in the result, not raised over a good edit.
            pack_path = ""

    manifests = app_registry.rescan()
    store = _migrate_store(app_data_mod, app_id, renames, new_keys)

    return {
        "id": app_id,
        "name": updated["name"],
        "lists": new_keys,
        "list_labels": [list_label(k) for k in new_keys],
        "added": added,
        "renamed": renames,
        "keywords": list(updated["vocabulary"]["keywords"]),
        "app_path": path,
        "pack_path": pack_path,
        "store": store,
        "installed": app_id in app_registry.installed_ids(manifests),
    }


def _lists_in_order(manifest: dict[str, Any]) -> list[str]:
    """This manifest's declared list keys, in the order it declares them."""
    block = manifest.get("data")
    default = block.get("default") if isinstance(block, dict) else None
    if not isinstance(default, dict):
        return []
    return [str(k) for k, v in default.items() if isinstance(v, list)]


_GOAL_BLURB_PREFIX = "Scaffolded from your goal: "


def tagline(goal: str, name: str = "") -> str:
    """The line shown under a scaffolded bot's name: its plain subject ("A
    wedding planner bot."), never her goal sentence, which may tell her
    family's news. With no subject, a plain line
    that says nothing about her."""
    from puffpai.catalogs import personal_words
    try:
        subject = personal_words.subject_of(str(goal or ""))
    except Exception:  # noqa: BLE001 - the plain line below still works
        subject = ""
    subject = _ascii_plain(subject, 60).strip()
    if subject:
        article = "An" if subject[:1].lower() in "aeiou" else "A"
        return f"{article} {subject} bot."
    label = _ascii_plain(name, app_registry.MAX_UI_NAME_LEN).strip()
    return f"{label}, made from your goal." if label else "Made from your goal."


def goal_of(manifest: dict[str, Any]) -> str:
    """The goal recorded in a scaffolded app's record, or "".

    :func:`build_app_manifest` keeps it in the app's own ``goal`` field, so
    this reads it back rather than asking the owner to retype their goal on
    every edit. An older app keeps it in its blurb
    ("Scaffolded from your goal: ..."), which is still read. A blurb the
    owner rewrote by hand simply yields no goal, which costs the vocabulary a
    few keywords and loses nothing.
    """
    goal = manifest.get("goal")
    if isinstance(goal, str) and goal.strip():
        return goal.strip()
    blurb = str(manifest.get("blurb") or "")
    if blurb.startswith(_GOAL_BLURB_PREFIX):
        return blurb[len(_GOAL_BLURB_PREFIX):].strip()
    return ""


def _merged_vocabulary(old: dict[str, Any],
                       rebuilt: dict[str, Any]) -> dict[str, Any]:
    """The regenerated vocabulary, keeping any word the old one already had.

    Regeneration on its own would silently narrow what the bot answers to: the
    goal survives only as a 180-character blurb, so a long goal's keywords cannot
    all be re-derived. Union, then cap, so an edit can only ever widen.
    """
    raw_fresh = rebuilt.get("vocabulary")
    fresh: dict[str, Any] = dict(raw_fresh) if isinstance(raw_fresh, dict) else {}
    raw_previous = old.get("vocabulary")
    previous: dict[str, Any] = (dict(raw_previous)
                                if isinstance(raw_previous, dict) else {})
    keywords = [str(w) for w in (fresh.get("keywords") or [])]
    for word in (previous.get("keywords") or []):
        text = _ascii_plain(word, app_registry.MAX_KEYWORD_LEN).lower()
        if text and text not in keywords:
            keywords.append(text)
    examples = [str(e) for e in (fresh.get("examples") or [])]
    return {"keywords": keywords[:app_registry.MAX_KEYWORDS],
            "examples": examples[:app_registry.MAX_EXAMPLES]}


def _migrate_store(app_data_mod: Any, app_id: str, renames: dict[str, str],
                   new_keys: list[str]) -> dict[str, Any]:
    """Move the live document onto the new shape without dropping a row.

    A renamed list keeps its items under the new key. A new list arrives empty.
    A key the manifest no longer declares is LEFT WHERE IT IS: this function's
    contract is that nothing is lost, and a stale key sitting in the document
    costs nothing while deleting it would cost the owner their entries.
    """
    out: dict[str, Any] = {"migrated": False, "renamed": {}, "added": [],
                           "kept": []}
    try:
        data = app_data_mod.AppData()
        document = data.get_data(app_id)
        if not isinstance(document, dict):
            return out
        for old, new in renames.items():
            if old in document and old != new:
                document[new] = document.pop(old)
                out["renamed"][old] = new
        for key in new_keys:
            if key not in document:
                document[key] = []
                out["added"].append(key)
        out["kept"] = [k for k in new_keys
                       if isinstance(document.get(k), list) and document[k]]
        out["migrated"] = bool(data.set_data(app_id, document))
    except Exception:  # pragma: no cover - a broken store is reported, not raised
        return out
    return out


def _rename_store(app_data_mod: Any, new_id: str) -> str:
    """Move one persona's document onto its new id. Never raises.

    Returns the file it now lives in, or "" when there was nothing to move. The
    old path is resolved by the CALLER before the folders move, because
    :func:`puffpai.app_data.data_dir_for` decides where a document lives by
    asking whether the app is an owner's - which is a question about a folder
    that is about to be somewhere else.
    """
    try:
        new_path = app_registry.data_path(new_id,
                                          app_data_mod.data_dir_for(new_id))
    except (ValueError, OSError):  # pragma: no cover - valid_id already passed
        return ""
    return new_path


def rename_id(app_id: str, new_id: str) -> dict[str, Any]:
    """Give a persona a different id, and move everything that names the old one.

    The migration for a persona that was made with an id it should never have
    been allowed - ``apps``, which is also the folder its own app sits in and
    the dashboard page that lists them. There is no way to fix that by hand
    without knowing all six places the id is written, so this is the one place:
    ``<state>/apps/<id>`` and ``<state>/packs/<id>``, the ``id`` in both
    manifests, the app's route, the pack's app entry, the build manifest's
    ``persona.app`` and ``persona.pack``, and the store document with every row
    the owner has written.

    Reserved and already-taken targets are refused with the same sentences
    :func:`scaffold` uses, and nothing moves until every check has passed.

    **Run it with the backend stopped.** It takes none of the per-file locks the
    running API writes stores under, so a rename underneath a live server could
    be undone by the next write.
    """
    from puffpai import app_data as app_data_mod
    from puffpai import build_manifest

    if not is_user_persona(app_id):
        raise ScaffoldError(
            f"'{app_id}' is not a persona you made, so this will not rename it. "
            f"Only apps under {app_registry.user_apps_dir()} can be changed "
            f"this way; a shipped app is changed by changing the build.")

    new_id = validate_request(new_id)["id"]
    if new_id == app_id:
        raise ScaffoldError(
            f"'{app_id}' is already its id, so there is nothing to rename.")

    apps_root = app_registry.user_apps_dir()
    packs_root = app_registry.user_packs_dir()
    old_app = app_registry.resolve_within(apps_root, app_id)
    old_pack = app_registry.resolve_within(packs_root, app_id)
    new_app = app_registry.resolve_within(apps_root, new_id)
    new_pack = app_registry.resolve_within(packs_root, new_id)

    taken = [p for p in (new_app, new_pack) if os.path.exists(p)]
    if taken:
        raise ScaffoldError(
            f"'{new_id}' already exists here: {', '.join(taken)}. Pick another "
            f"id - nothing of yours is overwritten, so nothing was changed.")
    for other in app_registry.load_manifests(include_off=True):
        if str(other.get("id")) == new_id:
            raise ScaffoldError(
                f"An app called '{new_id}' is already installed on this build. "
                f"Two apps may not claim one id, so nothing was changed.")

    path, manifest = _read_manifest_file(app_id)
    old_surface = persona_surface(app_id)
    updated = dict(manifest)
    updated["id"] = new_id
    raw_ui = manifest.get("ui")
    ui: dict[str, Any] = dict(raw_ui) if isinstance(raw_ui, dict) else {}
    if str(ui.get("route") or "") == f"{ROUTE_PREFIX}{app_id}" or not ui.get("route"):
        ui["route"] = f"{ROUTE_PREFIX}{new_id}"
    updated["ui"] = ui
    # The surface is named after the id, so it moves with it. A surface the
    # owner added by hand is left alone: the id it names is not this one.
    updated["surfaces"] = [persona_surface(new_id) if str(s) == old_surface
                           else str(s)
                           for s in (manifest.get("surfaces") or [])]

    problem = app_registry.validate_manifest(updated,
                                             _known_surfaces(app_id, new_id))
    if problem is not None:
        raise ScaffoldError(
            f"That rename would make the app invalid: {problem}. Nothing was "
            f"changed.")

    # Resolved while the old id is still an owner persona; see _rename_store.
    try:
        old_store = app_registry.data_path(app_id,
                                           app_data_mod.data_dir_for(app_id))
    except (ValueError, OSError):  # pragma: no cover - valid_id already passed
        old_store = ""

    os.rename(old_app, new_app)
    if os.path.isdir(old_pack):
        os.rename(old_pack, new_pack)
    _write_json(os.path.join(new_app, app_registry.MANIFEST_NAME), updated)

    pack_path = os.path.join(new_pack, app_registry.PACK_MANIFEST_NAME)
    if os.path.isfile(pack_path):
        try:
            with open(pack_path, "r", encoding="utf-8") as fh:
                pack = json.load(fh)
            if isinstance(pack, dict):
                pack["id"] = new_id
                for entry in pack.get("apps") or []:
                    if isinstance(entry, dict) and str(entry.get("id")) == app_id:
                        entry["id"] = new_id
                _write_json(pack_path, pack)
        except (OSError, json.JSONDecodeError):
            # The app is the thing the registry installs. A pack that could not
            # be rewritten is reported in the result, not raised over a rename
            # whose folders have already moved.
            pack_path = ""

    store_moved = ""
    new_store = _rename_store(app_data_mod, new_id)
    if old_store and new_store and os.path.isfile(old_store):
        if not os.path.exists(new_store):
            os.makedirs(os.path.dirname(new_store), exist_ok=True)
            os.replace(old_store, new_store)
            store_moved = new_store

    build_path = ""
    try:
        record = build_manifest.read_manifest() or {}
        persona = record.get("persona")
        if isinstance(persona, dict) and (persona.get("app") == app_id
                                          or persona.get("pack") == app_id):
            if persona.get("app") == app_id:
                persona["app"] = new_id
            if persona.get("pack") == app_id:
                persona["pack"] = new_id
            for key in ("app_manifest", "pack_manifest"):
                if isinstance(persona.get(key), str):
                    persona[key] = persona[key].replace(
                        os.sep + app_id + os.sep, os.sep + new_id + os.sep)
            build_path = build_manifest.write_manifest(record)
    except Exception:  # pragma: no cover - a manifest that cannot be read
        build_path = ""

    # The allowlist names the surface, and the surface is named after the id.
    # Swapped, not added: leaving the old one on would keep gating open a page
    # that no longer exists.
    swapped = _update_build_surfaces(
        add={persona_surface(new_id): _surface_reason(
            new_id, str(updated.get("name") or new_id))},
        drop=[old_surface])
    build_path = build_path or swapped

    manifests = app_registry.rescan()
    return {
        "id": new_id,
        "old_id": app_id,
        "app_path": os.path.join(new_app, app_registry.MANIFEST_NAME),
        "pack_path": pack_path,
        "route": updated["ui"]["route"],
        "store_moved": store_moved,
        "build_manifest": build_path,
        "installed": new_id in app_registry.installed_ids(manifests),
    }


def rename_summary_lines(result: dict[str, Any]) -> list[str]:
    """What the rename moved, in plain sentences, for the CLI to print."""
    lines = [f"Renamed {result['old_id']} to {result['id']}.",
             f"  app:   {result['app_path']}",
             f"  pack:  {result['pack_path'] or 'NOT rewritten - check it by hand'}",
             f"  page:  {result['route']}"]
    if result.get("store_moved"):
        lines.append(f"  store: moved to {result['store_moved']}; nothing was "
                     f"dropped")
    else:
        lines.append("  store: nothing to move - this persona had no saved "
                     "lists yet")
    if result.get("build_manifest"):
        lines.append(f"  build manifest updated: {result['build_manifest']}")
    if result.get("installed"):
        lines.append("  Installed: the box rescanned and it is there now.")
    else:
        lines.append("  NOT installed: the rescan did not find it. Check the "
                     "two files above.")
    return lines


def edit_summary_lines(result: dict[str, Any]) -> list[str]:
    """What the edit changed, in plain sentences, for the CLI to print."""
    lines = [f"Edited {result['name']} ({result['id']})."]
    for old, new in (result.get("renamed") or {}).items():
        lines.append(f"  renamed: {list_label(old)} -> {list_label(new)}")
    if result.get("added"):
        lines.append("  added:   " + ", ".join(list_label(k)
                                               for k in result["added"]))
    lines.append("  lists:   " + (", ".join(result.get("list_labels") or [])
                                  or "none"))
    store = result.get("store") or {}
    if store.get("migrated"):
        kept = store.get("kept") or []
        lines.append("  store:   migrated; nothing was dropped"
                     + (f" ({len(kept)} list(s) still hold items)" if kept else ""))
    else:
        lines.append("  store:   NOT migrated - the document could not be "
                     "written. The manifest changed; check the app page.")
    lines.append("  words it answers to: " + ", ".join(result["keywords"]))
    if result.get("lists"):
        lines.append(f"  Try: \"{try_phrase(result['lists'][0])}\".")
    return lines


def repair_surfaces() -> dict[str, Any]:
    """Give every persona made before this change its own surface. Idempotent.

    A persona scaffolded by an earlier build declares ``daily.notes`` and is on
    no allowlist of its own, so on a trimmed build whose owner never loaded
    notes the one page that is theirs is gated out. This puts both halves right:
    the app manifest declares ``persona.<id>``, and the allowlist carries it.

    It runs on the LIST paths (:func:`personas`, :func:`owner_apps`) rather than
    living in a migration note somebody has to find and type, because the
    owner's real build is already in this state and a migration nobody runs
    fixes nobody's build. It is safe to run on every read: it writes only when a
    persona is actually missing its surface, it only ever ADDS - no surface, no
    list and no store is removed - and it leaves an untrimmed build untouched
    (see :func:`_update_build_surfaces`).

    Returns what it changed. Never raises: a read of the persona list must not
    fail because a file could not be rewritten.
    """
    out: dict[str, Any] = {"apps": [], "build_manifest": ""}
    add: dict[str, str] = {}
    for app_id in scaffolded_ids():
        surface = persona_surface(app_id)
        try:
            path, manifest = _read_manifest_file(app_id)
        except ScaffoldError:
            continue
        declared = [str(s) for s in (manifest.get("surfaces") or [])]
        updated = dict(manifest)
        missing_surface = surface not in declared
        if missing_surface:
            updated["surfaces"] = [surface]
        # An older app shows her goal sentence as its tagline ("Scaffolded
        # from your goal: ..."). The
        # goal moves to the app's own record and the tagline becomes the
        # bot's plain subject. Only these two fields change.
        legacy = str(manifest.get("blurb") or "")
        if legacy.startswith(_GOAL_BLURB_PREFIX) and not manifest.get("goal"):
            old_goal = legacy[len(_GOAL_BLURB_PREFIX):].strip()
            updated["goal"] = old_goal
            updated["blurb"] = _ascii_plain(
                tagline(old_goal, str(manifest.get("name") or "")), MAX_BLURB_CHARS)
        if updated != manifest:
            if app_registry.validate_manifest(
                    updated, _known_surfaces(app_id)) is None:
                try:
                    _write_json(path, updated)
                except OSError:  # pragma: no cover - a read-only install
                    continue
                out["apps"].append(app_id)
                # Only a persona that was MISSING its surface joins the
                # allowlist. A persona that already declares one and is not on
                # the list is not broken - it is an owner who took it off, and
                # putting it back on every read would make that impossible.
                if missing_surface:
                    add[surface] = _surface_reason(
                        app_id, str(manifest.get("name") or ""))

    if add:
        out["build_manifest"] = _update_build_surfaces(add=add)
    if out["apps"]:
        app_registry.rescan()
    return out


def _repair_quietly() -> None:
    """:func:`repair_surfaces` on a read path, where nothing may raise."""
    try:
        repair_surfaces()
    except Exception:  # pragma: no cover - a repair is never worth an error
        pass


def personas() -> list[dict[str, Any]]:
    """The personas scaffolded on this build, read back off disk.

    Read from the owner's own packs directory, because that is what "a persona I
    made" means; the shipped packs are the build, not the owner's work.
    """
    _repair_quietly()
    root = app_registry.user_packs_dir()
    installed = {str(m.get("id")): m for m in app_registry.current_manifests()}
    out: list[dict[str, Any]] = []
    for record in app_registry.load_packs(root, manifests=list(installed.values())):
        app_ids = [str(entry["id"]) for entry in record.get("apps") or []]
        out.append({
            "id": record["id"],
            "display_name": record["display_name"],
            "blurb": record["blurb"],
            "apps": app_ids,
            "missing_apps": [a for a in app_ids if a not in installed],
        })
    return out


#: The example item a "Try:" line uses. One word, obviously a placeholder, and
#: the same word the CLI's closing line and the docs use.
TRY_ITEM = "tomatoes"


def try_phrase(key: str) -> str:
    """The one sentence that proves a scaffolded app works: an add, spelled out."""
    return f"add {TRY_ITEM} to the {list_label(key)} list"


def manifest_lists(manifest: dict[str, Any]) -> list[str]:
    """The list keys one manifest declares. Same reading as the chat fastpath."""
    from puffpai import app_fastpath

    return app_fastpath._lists_of(manifest)


def owner_apps() -> list[dict[str, Any]]:
    """The apps the OWNER added, with the words that work on them.

    This is what the dashboard home and the generic app page need and cannot
    get from ``/build/self``: which lists an app keeps, and therefore what to
    suggest saying. The lists are the owner's own keys out of their own
    manifest - nothing about the router's vocabulary is exposed here.
    """
    _repair_quietly()
    own = build_persona_id()
    out: list[dict[str, Any]] = []
    for manifest in app_registry.current_manifests():
        app_id = str(manifest.get("id") or "")
        if not is_user_persona(app_id):
            continue
        keys = manifest_lists(manifest)
        ui = manifest.get("ui") if isinstance(manifest.get("ui"), dict) else {}
        out.append({
            "id": app_id,
            "name": str(manifest.get("name") or app_id),
            "blurb": str(manifest.get("blurb") or ""),
            # What the bot is for, as the pages show it: its plain subject,
            # never her goal sentence. The
            # sentence stays in the app's own record for the bot itself.
            "goal": tagline(goal_of(manifest), str(manifest.get("name") or ""))
            if goal_of(manifest) else "",
            "route": str((ui or {}).get("route") or f"{ROUTE_PREFIX}{app_id}"),
            "lists": keys,
            "list_labels": [list_label(key) for key in keys],
            "try": try_phrase(keys[0]) if keys else "",
            # A persona with nothing in it: no list to keep and no saved row.
            # The Apps page and the home launcher leave such a tile out; the
            # bot's own page and rail entry stay.
            "empty": _persona_is_empty(app_id, manifest, keys),
            # The bot's own page (the place the build scaffolded for itself),
            # not an app she added: no "Remove this app" on it.
            "own": bool(own) and app_id == own,
        })
    return out


def build_persona_id() -> str:
    """The id of the place this build scaffolded for the bot itself, or ""."""
    try:
        from puffpai import build_manifest, prove
        manifest = build_manifest.read_manifest() or {}
        block = manifest.get("persona") if isinstance(manifest, dict) else None
        return prove.persona_id_of(block if isinstance(block, dict) else {})
    except Exception:  # noqa: BLE001 - unknown means "not the bot's own"
        return ""


def _persona_is_empty(app_id: str, manifest: dict[str, Any],
                      keys: list[str]) -> bool:
    """True only for a scaffolded persona that declares no list and holds no
    row. Any other app, an unreadable store, or a list declared (even an empty
    one the owner is about to fill) is not empty: hiding is the rare case."""
    surfaces = manifest.get("surfaces") or []
    if persona_surface(app_id) not in surfaces or keys:
        return False
    try:
        from puffpai import app_data as app_data_mod
        document = app_data_mod.AppData().get_data(app_id)
    except Exception:  # noqa: BLE001 - unreadable means "show it"
        return False
    if document is None:
        return True
    if not isinstance(document, dict):
        return False
    return not any(isinstance(v, list) and v for v in document.values())


def is_user_persona(app_id: Any) -> bool:
    """True when ``app_id`` is an app the OWNER added, not one we shipped.

    Read off disk rather than off the manifest, because a manifest does not
    record which root it came from and the answer decides whether something is
    allowed to be deleted.
    """
    if not app_registry.valid_id(app_id):
        return False
    try:
        folder = app_registry.resolve_within(app_registry.user_apps_dir(), app_id)
        shipped = app_registry.app_dir(app_id)
    except (ValueError, OSError):
        return False
    if os.path.isdir(shipped):
        # Two folders claiming one id. The registry already refuses to install
        # either; deleting one of them here would be picking a winner by
        # deletion, which is not a decision this function gets to make.
        return False
    return os.path.isfile(os.path.join(folder, app_registry.MANIFEST_NAME))


def remove(app_id: str) -> dict[str, Any]:
    """Delete a persona the owner made: its app, its pack and its store.

    Refuses anything that is not in :func:`app_registry.user_apps_dir` - a
    shipped app is part of the build and is removed by changing the build, not
    by a DELETE from a browser. The caller is responsible for the typed
    confirmation; this function is responsible for never touching a file it was
    not asked to.

    The store path is resolved BEFORE the manifest is unlinked, because
    :func:`puffpai.app_data.data_dir_for` decides where a document lives by
    asking whether the app is an owner's - and a moment later it will not be.
    """
    import shutil

    from puffpai import app_data as app_data_mod

    if not is_user_persona(app_id):
        raise ScaffoldError(
            f"'{app_id}' is not a persona you made, so this will not delete it. "
            f"Only apps under {app_registry.user_apps_dir()} can be removed "
            f"this way; a shipped app is removed by changing the build.")

    app_folder = app_registry.resolve_within(app_registry.user_apps_dir(), app_id)
    pack_folder = app_registry.resolve_within(app_registry.user_packs_dir(), app_id)
    try:
        store = app_registry.data_path(app_id, app_data_mod.data_dir_for(app_id))
    except ValueError:  # pragma: no cover - valid_id already passed
        store = ""

    removed_paths: list[str] = []
    for folder in (app_folder, pack_folder):
        if os.path.isdir(folder):
            shutil.rmtree(folder)
            removed_paths.append(folder)
    store_removed = False
    if store and os.path.isfile(store):
        os.remove(store)
        store_removed = True
        removed_paths.append(store)

    # A surface whose page has been deleted is not a surface. It comes off the
    # allowlist with the persona, so the build keeps saying only true things
    # about what it loads.
    build_path = _update_build_surfaces(drop=[persona_surface(app_id)])

    manifests = app_registry.rescan()
    installed = app_registry.installed_ids(manifests)
    return {
        "id": app_id,
        "removed_paths": removed_paths,
        "store_removed": store_removed,
        "build_manifest": build_path,
        "installed": app_id in installed,
        "apps": installed,
    }


def scaffold_summary_lines(result: dict[str, Any]) -> list[str]:
    """What was made, in plain sentences, for the CLI to print."""
    lines = [
        f"Made {result['name']} ({result['id']}).",
        f"  app:   {result['app_path']}",
        f"  pack:  {result['pack_path']}",
    ]
    if result.get("lists"):
        lines.append("  lists: " + ", ".join(list_label(k)
                                             for k in result["lists"]))
    else:
        lines.append("  lists: none yet - add them to data.default when you "
                     "know what you want kept.")
    lines.append("  words it answers to: " + ", ".join(result["keywords"]))
    lines.append(f"  page:  {result['route']}")
    lines.append(f"  surface it joins: {', '.join(result['surfaces'])}")
    if result.get("installed"):
        lines.append("  Installed: the box rescanned and it is there now.")
    else:
        lines.append("  NOT installed: the rescan did not find it. Check the "
                     "two files above.")
    if result.get("lists"):
        first = list_label(result["lists"][0])
        # A Try line the owner can paste. "add something to the plantings list"
        # is not a sentence anybody says, and the fastpath answers it with
        # "What should I add?" - so the very first thing the wizard suggested
        # was the one thing that did not work.
        lines.append(f"  Try: \"{try_phrase(result['lists'][0])}\", then "
                     f"\"what is on the {first} list\".")
    return lines


__all__ = [
    "FALLBACK_ID",
    "MAX_NAME_LEN",
    "ORDER",
    "RESERVED_IDS",
    "ROUTE_PREFIX",
    "SURFACE",
    "SURFACE_PREFIX",
    "ScaffoldError",
    "build_app_manifest",
    "build_pack_manifest",
    "edit",
    "edit_summary_lines",
    "goal_keywords",
    "goal_of",
    "is_reserved_id",
    "is_user_persona",
    "list_key",
    "list_label",
    "manifest_lists",
    "owner_apps",
    "parse_lists",
    "persona_surface",
    "personas",
    "preview",
    "remove",
    "repair_surfaces",
    "scaffolded_ids",
    "surface_taxonomy_entries",
    "rename_id",
    "rename_summary_lines",
    "scaffold",
    "TRY_ITEM",
    "scaffold_summary_lines",
    "suggest_id",
    "try_phrase",
    "validate_request",
]
