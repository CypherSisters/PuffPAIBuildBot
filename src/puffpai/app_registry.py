"""App manifests — an app is a folder with a declaration, not a core edit.

An app used to be a pattern hand-copied through seven files in the core: the
valid-app list and default data shape in :mod:`puffpai.app_data`, the nav entry,
the surface map, the catalog record, and - the sharpest one - the words the
router knows for it, in one static ``config/rulepacks/apps.json`` shared by every
app. That meant "install an app" was "fork the core", a build with no Trivia app
still had trivia vocabulary, and a NEW app could get no vocabulary at all.

This module is the other end of that. ``apps/<id>/app.json`` declares, in one
place, everything the core needs to know, and installing is rescanning: no
script runs, nothing is imported, nothing is executed.

**A manifest is data and only data.** It is parsed as JSON, validated, and used
as lists and strings. No field is ever imported, resolved to a module path,
``eval``'d, or concatenated into the model's instructions. A third-party app is
untrusted input, and the routing layer being keyword-based rather than
prompt-injected tool definitions is what makes a hostile app's vocabulary
harmless: it can add words to a matcher, and that is all it can do. Do not
"improve" routing by feeding app declarations to the model as text. The one
exception is narrow and does not come from a manifest: for a browser app
Toolsmith added (``kind: "web"``), the action words Toolsmith's
compiler made from the app itself and Drawbridge checked (R1-R12, sealed, see
:mod:`puffpai.webapps.recipes`) may be shown to the model as tool
descriptions. A web manifest itself still carries no words for the router and
no actions.

A declared surface is a claim of membership, not a grant of capability. Naming
``house.cameras`` says which existing surface this app belongs to so the gate can
reason about it; it does not produce camera access. Vault, consent, action gates
and egress remain contracts no manifest field touches.

A malformed manifest is recorded through :mod:`puffpai.config_health` and
skipped, exactly the way a corrupt rulepack is: the other apps keep working and
the owner is told, rather than quietly running a build that is missing something.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Optional

from puffpai import config_health

SCHEMA = "puffpai_app/v1"

#: Pack manifests. A pack is a curated BUNDLE of apps for a topic or persona;
#: it carries no data, no vocabulary and no code of its own, and every app it
#: lists must already be installed.
PACK_SCHEMA = "puffpai_pack/v1"

# The rulepack surface app vocabulary is assembled into. Apps contribute words
# to the router's existing "apps" pack; they do not invent router surfaces.
APPS_SURFACE = "apps"

APPS_DIRNAME = "apps"
MANIFEST_NAME = "app.json"

PACKS_DIRNAME = "packs"
PACK_MANIFEST_NAME = "pack.json"

CONFIG_NAME = "app_manifests"
PACK_CONFIG_NAME = "pack_manifests"


# ---------------------------------------------------------------------------
# bounds
# ---------------------------------------------------------------------------
# Everything an app declares is bounded, because an app is untrusted input and
# the router is a linear scan over every keyword of every pack for every
# message. The numbers below are "comfortably more than any honest app needs,
# far less than enough to do damage":
#
#   * 64 KB per manifest - the five shipped manifests are all under 2 KB, and
#     the largest legitimate field (a default data shape) is bounded separately.
#   * 60 keywords per app - the busiest shipped app (calendar) declares 8. Sixty
#     apps at sixty words each is 3600 substring checks per message, which is
#     still cheap; six thousand words per app would not be.
#   * 2..64 characters per keyword - a one-character keyword matches nearly every
#     message, which is the flooding attack by another route.
#   * 12 examples of 120 characters - examples are published on the phrasebook
#     page, so this is a page-length bound as much as a safety one.
#   * 64 installed apps - past that the nav and the phrasebook stop being
#     readable, and no owner has curated that many.
#   * 32 KB of default data - a default is a starting shape, not a dataset. An
#     app that wants to ship content ships it as knowledge, under that gate.
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTALLED_APPS = 64
MIN_ID_LEN = 2
MAX_ID_LEN = 32
MAX_KEYWORDS = 60
MIN_KEYWORD_LEN = 2
MAX_KEYWORD_LEN = 64
MAX_EXAMPLES = 12
MAX_EXAMPLE_LEN = 120
MAX_ACTIONS = 32
MAX_SURFACES = 8
MAX_LABEL_LEN = 200
MAX_DEFAULT_BYTES = 32 * 1024
MAX_ORDER = 9999

# Privacy block (five fields).
MAX_DOORS = 12
MAX_DOOR_HOSTS = 16
MAX_HOST_LEN = 253          # the DNS name-length ceiling
MAX_PURPOSE_LEN = 140       # an author who cannot say why in one line has not
MAX_DATA_SENT_LEN = 200     # worked out why either

# Packs.
MAX_INSTALLED_PACKS = 32
MAX_PACK_APPS = 64

# Second, tighter bounds for the strings that leave the box. `name` and `blurb`
# are validated at 200 characters on the way in, which is the right ceiling for
# a file on disk; a nav label at 200 characters is a broken sidebar. Bounding
# again on the way out means a manifest edited after install, or one loaded by
# an older validator, still cannot stretch the UI.
MAX_UI_NAME_LEN = 64
MAX_UI_BLURB_LEN = 200
MAX_UI_ICON_LEN = 64

# A route that leaves the box is an href the browser will follow. Validation
# already refuses '..' and anything not absolute; this refuses the rest of what
# an absolute-looking string can still be. '//evil.example' starts with a slash
# and is a protocol-relative URL off this box, and a colon opens the door to a
# scheme. Neither has any business in an in-app route.
_SAFE_ROUTE_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")

# Lowercase, starts with a letter, letters/digits/underscore after. The id
# becomes a path component and a data filename, so it is the single most
# dangerous field in the manifest: no dots, no separators, no case games, no
# leading underscore. It is still passed through a containment check before it
# touches a path - a pattern is a first line of defence, not the only one.
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# The id Toolsmith issues for an app it added from outside
# (``integrate.records.new_id``): ``<slug, <=30>-<8 random base32>``. The same
# rule as :data:`puffpai.webapps.ID_RE` and ``integrate.records.ID_RE``; one
# rule, so a browser app's manifest is never "not usable" here. Also safe as a
# path component: lowercase letters, digits and hyphens, no dot, no leading
# hyphen. A declarative app's id can never have this shape (no hyphen).
ISSUED_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}\Z")

#: A browser app's manifest (written by ``puffpai.webapps.install``) may hold
#: exactly these keys; anything else (words for the router, actions, a data
#: block, a feed, outside services) is refused.
WEB_KIND = "web"
_WEB_KEYS = frozenset({"schema", "id", "name", "blurb", "kind", "web", "outside", "ui"})
_WEB_SOURCE_KEYS = frozenset({"repo", "commit", "license", "sha256"})
MAX_WEB_NAME_LEN = 64
MAX_WEB_OUTSIDE = 50

# An exact hostname: labels of letters/digits/hyphens, no leading or trailing
# hyphen, no scheme, no port, no path, no wildcard.
_HOST_RE = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
                      r"(?:\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$")

#: What this box actually does with ``doors[].hosts[]`` TODAY, in one sentence,
#: carried on every payload that shows a door so no surface can imply more.
#:
#: A manifest's hosts are schema-checked at load - exact hostname, no wildcard,
#: no scheme - and then they stop: nothing registers them with
#: :mod:`puffpai.security.egress_doors`, so the door table this build enforces
#: has never heard of them. Registering manifest doors into that table
#: (namespaced ``app:<id>:<door>``, ``default=False`` forced) is a change to
#: network policy and not this module's to make. Until it lands, the honest
#: line ships and no card may claim otherwise.
HOSTS_ENFORCEMENT = "declared_only"
HOSTS_ENFORCEMENT_NOTICE = (
    "Declared by the author; your box does not yet enforce these hosts."
)

#: What a door's credential may be. ``password`` and ``cookie`` are refused
#: outright: an app that wants the owner's password is asking for the one thing
#: the vault exists to stop it having.
CREDENTIAL_KINDS = ("none", "api_key", "oauth2")
REFUSED_CREDENTIAL_KINDS = ("password", "cookie")

# v1 apps are declarative. These keys are the shapes a code-carrying app would
# take, and they are refused by name so the deferral is enforced rather than
# merely intended: an app that wants to run code is the next tier and needs a
# trust model that does not exist yet.
_FORBIDDEN_KEYS = (
    "handler", "handlers", "module", "modules", "import", "imports",
    "entrypoint", "entry_point", "exec", "eval", "python", "script",
    "scripts", "command", "commands", "hook", "hooks", "plugin", "run",
)


# ---------------------------------------------------------------------------
# requirements
# ---------------------------------------------------------------------------
# `requires` was, for a while, a block nothing read: FreshTable declared
# `network: false` while its page called a model, and Trivia declared nothing
# about the model it cannot work without. A declaration no code consumes is
# decoration, and worse than silence because it reads as a guarantee.
#
# So the box now evaluates it. An unmet requirement NEVER blocks an install -
# an owner may install FreshTable today and add a model tomorrow - it marks the
# app degraded with a reason, and the reason is what a surface says instead of
# failing halfway through an action.

#: requirement key -> the sentence an owner hears, completed as "<App> <this>."
#: Used when the box KNOWS the requirement is unmet.
REQUIREMENT_REASONS = {
    "ollama": "needs a local model, and none is available on this build",
    "home_assistant": "works only with a Home Assistant hub, if you have one, and this build is not connected to one",
    "network": "needs a network connection, and this build has none",
}

#: The same, for when the probe itself could not answer.
#:
#: This module used to say "None means unknown and is NOT unmet", on the theory
#: that a badge which cries wolf is a badge owners learn to ignore. That was
#: wrong in exactly the case the badge exists for: `config.get_llm_backend()`
#: RAISES on a box with no model, the probe caught it, wrote None, and a
#: model-less build marked nothing degraded at all. Silence is not the safe
#: default when the thing you cannot verify is the thing the app needs; saying
#: "I could not check" is honest, and it is what an owner can act on.
REQUIREMENT_UNVERIFIED = {
    "ollama": "needs a local model, and this build could not verify that one is there",
    "home_assistant": "works only with a Home Assistant hub, if you have one, and this build could not check that one is connected",
    "network": "needs a network connection, and this build could not verify it has one",
}


def declared_requirements(manifest: dict[str, Any]) -> list[str]:
    """The requirement keys this manifest says it needs. Unknown keys ignored.

    An app cannot invent a requirement any more than it can invent a surface:
    a key this build has never heard of is not a veto it gets to cast.
    """
    block = manifest.get("requires")
    if not isinstance(block, dict):
        return []
    return [key for key in REQUIREMENT_REASONS if block.get(key) is True]


def unmet_requirements(manifest: dict[str, Any],
                       available: Optional[dict[str, Any]] = None) -> list[str]:
    """Which of an app's declared requirements this box cannot vouch for.

    ``available`` maps a requirement key to True, False, or None. Only True is
    met. **False is unmet and None is ALSO unmet** - the difference between
    them is the sentence the owner reads, not whether they are told. A probe
    that could not answer is a fact about this build, and hiding it is how a
    model-less box came to report five perfectly healthy apps.

    ``available`` being empty or ``None`` is different again: that is "nobody
    probed anything", the shape every caller who does not care passes, and it
    degrades nothing.
    """
    if not available:
        return []
    return [key for key in declared_requirements(manifest)
            if available.get(key) is not True]


def degraded_reason(manifest: dict[str, Any],
                    available: Optional[dict[str, Any]] = None) -> str:
    """One owner-readable sentence naming what this app is missing, or ``""``.

    A requirement the box could not check reads "could not verify" rather than
    "none is available": both are degraded, and an owner deserves to know which
    of the two they are looking at.
    """
    unmet = unmet_requirements(manifest, available)
    if not unmet:
        return ""
    name = str(manifest.get("name") or manifest.get("id") or "This app")
    parts = [REQUIREMENT_REASONS[key] if (available or {}).get(key) is False
             else REQUIREMENT_UNVERIFIED[key]
             for key in unmet]
    if len(parts) == 1:
        return f"{name} {parts[0]}."
    return f"{name} " + ", and ".join([", ".join(parts[:-1]), parts[-1]]) + "."


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def _apps_dir() -> str:
    """Absolute path of the SHIPPED apps directory, inside the checkout."""
    from puffpai.config import get_config
    return os.path.join(get_config().REPO_ROOT, APPS_DIRNAME)


def _packs_dir() -> str:
    """Absolute path of the SHIPPED packs directory, inside the checkout."""
    from puffpai.config import get_config
    return os.path.join(get_config().REPO_ROOT, PACKS_DIRNAME)


def user_apps_dir() -> str:
    """Where an owner's OWN apps live: ``<state dir>/apps``.

    A pip-installed build has no writable checkout - ``apps/`` is inside
    site-packages, and an owner who scaffolds a persona there is editing the
    package. So the scan has a second root under the state directory
    (``PUFFPAI_STATE_DIR``, or the per-user OS state folder), which is outside
    any checkout on every platform and is where ``puffpai persona new`` writes.

    Shipped folders are scanned first, so an owner folder can never shadow a
    shipped app silently: two folders claiming one id is the existing collision,
    reported by name, and neither is installed.
    """
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), APPS_DIRNAME)


def user_packs_dir() -> str:
    """Where an owner's OWN packs live: ``<state dir>/packs``. See above."""
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), PACKS_DIRNAME)


def apps_dirs() -> list[str]:
    """Every directory the app scan reads, shipped first."""
    return [_apps_dir(), user_apps_dir()]


def packs_dirs() -> list[str]:
    """Every directory the pack scan reads, shipped first."""
    return [_packs_dir(), user_packs_dir()]


def _within(root: str, candidate: str) -> bool:
    """True when ``candidate`` resolves inside ``root``.

    Same containment test as :func:`puffpai.source_fetch._within`; symlinks and
    ``..`` are resolved away before the comparison so neither can escape.
    """
    root_real = os.path.realpath(root)
    cand_real = os.path.realpath(candidate)
    return cand_real == root_real or cand_real.startswith(root_real + os.sep)


def resolve_within(root: str, name: str) -> str:
    """Join ``name`` onto ``root`` and refuse if the result escapes.

    Raises ``ValueError`` rather than returning a path the caller might use
    anyway. Every place an app id becomes a path goes through here.
    """
    candidate = os.path.join(root, name)
    if not _within(root, candidate):
        raise ValueError(f"'{name}' resolves outside {root}.")
    return candidate


def app_dir(app_id: str, apps_dir: Optional[str] = None) -> str:
    """The folder an app lives in. Raises ``ValueError`` on an unsafe id."""
    if not valid_id(app_id):
        raise ValueError(f"'{app_id}' is not a valid app id.")
    return resolve_within(apps_dir or _apps_dir(), app_id)


def data_path(app_id: str, data_dir: str) -> str:
    """Where an app's owner data file lives: ``<data_dir>/<id>.json``.

    ``data_dir`` is passed in rather than looked up so :mod:`puffpai.app_data`
    keeps owning the location of the owner's files - the paths existing owners
    already have must not move.
    """
    if not valid_id(app_id):
        raise ValueError(f"'{app_id}' is not a valid app id.")
    if is_issued_id(app_id):
        # A browser app Toolsmith added keeps its data in the bot's copy of its
        # own storage (puffpai.webapps.mirror): no second store for it here.
        raise ValueError(f"'{app_id}' is a browser app; it keeps its own data.")
    return resolve_within(data_dir, f"{app_id}.json")


def is_issued_id(app_id: Any) -> bool:
    """True for an id Toolsmith issued for an app it added (``<slug>-<8>``)."""
    return isinstance(app_id, str) and bool(ISSUED_ID_RE.match(app_id))


def valid_id(app_id: Any) -> bool:
    """True when an app id is safe to use as a path component.

    Either a declarative app's id (lowercase, starts with a letter, letters,
    digits and underscores, 2 to 32 characters) or an id Toolsmith issued
    (:data:`ISSUED_ID_RE`).
    """
    if not isinstance(app_id, str):
        return False
    if is_issued_id(app_id):
        return True
    if not (MIN_ID_LEN <= len(app_id) <= MAX_ID_LEN):
        return False
    return bool(_ID_RE.match(app_id))


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _ascii_text(value: Any, limit: int) -> bool:
    """A printable, bounded, ASCII string. Console-reachable strings only."""
    if not isinstance(value, str) or not value.strip():
        return False
    if len(value) > limit:
        return False
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return all(ch.isprintable() for ch in value)


def validate_manifest(
    data: Any, known_surfaces: Optional[set[str]] = None
) -> Optional[str]:
    """Structural check of one parsed manifest; ``None`` means healthy.

    ``known_surfaces`` is the live surface taxonomy. An app declares membership
    of a surface this build already knows how to gate; it cannot invent one.
    Passing ``None`` reads the taxonomy from the catalog.
    """
    if not isinstance(data, dict):
        return "The manifest is not a JSON object."

    for key in _FORBIDDEN_KEYS:
        if key in data:
            return (
                f"The manifest declares '{key}'. Apps in this version are "
                f"declarative only, so no field may name code to run."
            )

    if str(data.get("schema") or "") != SCHEMA:
        return (
            f"The manifest says schema '{data.get('schema')}'; this build "
            f"reads '{SCHEMA}'."
        )

    if data.get("kind") == WEB_KIND or is_issued_id(data.get("id")):
        return _validate_web_manifest(data)

    app_id = data.get("id")
    if not valid_id(app_id):
        return (
            f"App id {app_id!r} is not usable. An id is {MIN_ID_LEN} to "
            f"{MAX_ID_LEN} characters, lowercase, starting with a letter and "
            f"using only letters, digits and underscores."
        )

    if not _ascii_text(data.get("name"), MAX_LABEL_LEN):
        return f"App '{app_id}' has no usable display name."

    for field in ("blurb", "version", "author", "license"):
        if field in data and not _ascii_text(data.get(field), MAX_LABEL_LEN):
            return f"App '{app_id}' has an unusable '{field}'."

    if "order" in data:
        order = data.get("order")
        if not isinstance(order, int) or isinstance(order, bool):
            return f"App '{app_id}' has a non-numeric order."
        if not (0 <= order <= MAX_ORDER):
            return f"App '{app_id}' has an order outside 0..{MAX_ORDER}."

    surfaces = data.get("surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        return f"App '{app_id}' names no surfaces."
    if len(surfaces) > MAX_SURFACES:
        return (
            f"App '{app_id}' names {len(surfaces)} surfaces; "
            f"{MAX_SURFACES} is the most an app may claim."
        )
    if known_surfaces is None:
        from puffpai import build_interview
        known_surfaces = set(build_interview.surface_taxonomy())
    for surface in surfaces:
        if not isinstance(surface, str) or surface not in known_surfaces:
            return (
                f"App '{app_id}' declares surface {surface!r}, which this "
                f"build does not have. An app joins an existing surface; it "
                f"cannot invent one."
            )

    problem = _validate_vocabulary(app_id, data.get("vocabulary"))
    if problem is not None:
        return problem

    problem = _validate_actions(app_id, data.get("actions"))
    if problem is not None:
        return problem

    problem = _validate_data(app_id, data.get("data"))
    if problem is not None:
        return problem

    problem = _validate_privacy(app_id, data)
    if problem is not None:
        return problem

    if "feed" in data:
        # A live-data block: one named outside service, GET only, no code.
        # Validated in full by puffpai.feeds, which refuses unknown fields.
        from puffpai import feeds
        problem = feeds.validate_feed(app_id, data.get("feed"))
        if problem is not None:
            return problem

    return _validate_ui(app_id, data.get("ui"))


def _validate_vocabulary(app_id: Any, vocabulary: Any) -> Optional[str]:
    if vocabulary is None:
        return None
    if not isinstance(vocabulary, dict):
        return f"App '{app_id}' has a vocabulary that is not an object."

    keywords = vocabulary.get("keywords", [])
    if not isinstance(keywords, list):
        return f"App '{app_id}' has a keyword list that is not a list."
    if len(keywords) > MAX_KEYWORDS:
        return (
            f"App '{app_id}' declares {len(keywords)} keywords; "
            f"{MAX_KEYWORDS} is the most one app may add to the router."
        )
    for word in keywords:
        if not _ascii_text(word, MAX_KEYWORD_LEN):
            return f"App '{app_id}' has an unusable keyword: {word!r}."
        if len(word) < MIN_KEYWORD_LEN:
            return (
                f"App '{app_id}' has keyword {word!r}, shorter than "
                f"{MIN_KEYWORD_LEN} characters; it would match nearly "
                f"every message."
            )
        if word != word.lower():
            return f"App '{app_id}' has a keyword that is not lowercase: {word!r}."

    examples = vocabulary.get("examples", [])
    if not isinstance(examples, list):
        return f"App '{app_id}' has an example list that is not a list."
    if len(examples) > MAX_EXAMPLES:
        return (
            f"App '{app_id}' publishes {len(examples)} examples; "
            f"{MAX_EXAMPLES} is the most the phrasebook shows for one app."
        )
    for phrase in examples:
        if not _ascii_text(phrase, MAX_EXAMPLE_LEN):
            return f"App '{app_id}' has an unusable example: {phrase!r}."
        if phrase != phrase.lower():
            return f"App '{app_id}' has an example that is not lowercase: {phrase!r}."
    return None


def _validate_actions(app_id: Any, actions: Any) -> Optional[str]:
    if actions is None:
        return None
    if not isinstance(actions, list):
        return f"App '{app_id}' has an action list that is not a list."
    if len(actions) > MAX_ACTIONS:
        return (
            f"App '{app_id}' declares {len(actions)} actions; "
            f"{MAX_ACTIONS} is the most one app may declare."
        )
    seen: set[str] = set()
    for action in actions:
        if not isinstance(action, dict):
            return f"App '{app_id}' has an action that is not an object."
        for key in _FORBIDDEN_KEYS:
            if key in action:
                return (
                    f"App '{app_id}' has an action declaring '{key}'. An "
                    f"action is a phrase, never a thing to run."
                )
        action_id = action.get("id")
        if not valid_id(action_id):
            return f"App '{app_id}' has an action with an unusable id: {action_id!r}."
        if action_id in seen:
            return f"App '{app_id}' declares action '{action_id}' twice."
        seen.add(str(action_id))
        if not _ascii_text(action.get("says"), MAX_EXAMPLE_LEN):
            return f"App '{app_id}' action '{action_id}' has no usable phrasing."
    return None


def _validate_data(app_id: Any, block: Any) -> Optional[str]:
    if block is None:
        return None
    if not isinstance(block, dict):
        return f"App '{app_id}' has a data block that is not an object."
    default = block.get("default")
    if default is None:
        return None
    if not isinstance(default, dict):
        return f"App '{app_id}' has a default data shape that is not an object."
    try:
        size = len(json.dumps(default).encode("utf-8"))
    except (TypeError, ValueError):
        return f"App '{app_id}' has a default data shape that is not plain JSON."
    if size > MAX_DEFAULT_BYTES:
        return (
            f"App '{app_id}' ships {size} bytes of default data; "
            f"{MAX_DEFAULT_BYTES} is the ceiling. A default is a starting "
            f"shape, not a dataset."
        )
    return None


def _validate_privacy(app_id: Any, data: Any) -> Optional[str]:
    """The five required privacy fields, and nothing else.

    ``doors[].hosts[]``, ``doors[].purpose``, ``doors[].data_sent_plain``,
    ``credential.kind``, ``children.may_be_used_by_minor``. The full 34-field
    contract is deliberately not required here: a ceremony in front
    of an unenforced runtime taxes the honest author and costs a liar ninety
    seconds. ``hosts[]`` is the ONE field this box can independently check, so
    it is the one held to an exact-hostname rule.

    Absent blocks are legitimate on an app that reaches nowhere and holds no
    credential - the five shipped apps are all of that shape - but a block that
    IS present must be complete. Half a declaration is worse than none, because
    the card would render as though the author had answered.
    """
    doors = data.get("doors")
    if doors is not None:
        if not isinstance(doors, list):
            return f"App '{app_id}' has a doors block that is not a list."
        if len(doors) > MAX_DOORS:
            return (
                f"App '{app_id}' declares {len(doors)} doors; {MAX_DOORS} is "
                f"the most one app may name."
            )
        for door in doors:
            if not isinstance(door, dict):
                return f"App '{app_id}' has a door that is not an object."
            for key in _FORBIDDEN_KEYS:
                if key in door:
                    return (
                        f"App '{app_id}' has a door declaring '{key}'. A door "
                        f"is a destination, never a thing to run."
                    )
            hosts = door.get("hosts")
            if not isinstance(hosts, list):
                return (
                    f"App '{app_id}' has a door with no hosts list. Every "
                    f"door names its hosts, even if the list is empty."
                )
            if len(hosts) > MAX_DOOR_HOSTS:
                return (
                    f"App '{app_id}' has a door naming {len(hosts)} hosts; "
                    f"{MAX_DOOR_HOSTS} is the ceiling."
                )
            for host in hosts:
                if not _ascii_text(host, MAX_HOST_LEN):
                    return f"App '{app_id}' has an unusable door host: {host!r}."
                if "*" in host:
                    return (
                        f"App '{app_id}' declares wildcard host {host!r}. A "
                        f"door names exact hostnames; a wildcard cannot be "
                        f"checked and would fail later as a mystery."
                    )
                if not _HOST_RE.match(host):
                    return (
                        f"App '{app_id}' declares host {host!r}, which is not "
                        f"a hostname. Name the host only - no scheme, no port, "
                        f"no path."
                    )
            if not _ascii_text(door.get("purpose"), MAX_PURPOSE_LEN):
                return (
                    f"App '{app_id}' has a door with no usable purpose. One "
                    f"line, at most {MAX_PURPOSE_LEN} characters, saying why."
                )
            if not _ascii_text(door.get("data_sent_plain"), MAX_DATA_SENT_LEN):
                return (
                    f"App '{app_id}' has a door that does not say, in the "
                    f"owner's words, what leaves the box."
                )

    credential = data.get("credential")
    if credential is not None:
        if not isinstance(credential, dict):
            return f"App '{app_id}' has a credential block that is not an object."
        kind = credential.get("kind")
        if kind in REFUSED_CREDENTIAL_KINDS:
            return (
                f"App '{app_id}' asks for a '{kind}'. An app never receives "
                f"the owner's password or session cookie."
            )
        if kind not in CREDENTIAL_KINDS:
            return (
                f"App '{app_id}' declares credential kind {kind!r}; this "
                f"build reads {', '.join(CREDENTIAL_KINDS)}."
            )
        # A manifest is a declaration, never a secret store. This refuses the
        # shape of a leaked credential outright rather than trusting the author.
        for key in ("value", "secret", "token", "key", "password"):
            if key in credential:
                return (
                    f"App '{app_id}' puts a '{key}' in its credential block. "
                    f"A manifest declares WHICH secret is needed, never a "
                    f"secret's value."
                )

    children = data.get("children")
    if children is not None:
        if not isinstance(children, dict):
            return f"App '{app_id}' has a children block that is not an object."
        flag = children.get("may_be_used_by_minor")
        if not isinstance(flag, bool):
            return (
                f"App '{app_id}' does not answer 'may_be_used_by_minor' with "
                f"true or false. A parent looks for this field first."
            )
    return None


def _display_text(value: Any, limit: int) -> bool:
    """Printable text for the dashboard (any language), bounded, one line.

    A browser app's name comes from its own title, cleaned by Toolsmith
    (``webapps.install.friendly_name``); it is rendered as plain text only.
    Control, format (for example a right-to-left override), separator and line
    characters other than a plain space are refused.
    """
    import unicodedata
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        return False
    return all(ch == " " or unicodedata.category(ch)[0] not in ("C", "Z") for ch in value)


def _validate_web_manifest(data: dict) -> Optional[str]:
    """A browser app's manifest: data only, and only the fields it may have.

    It declares no surfaces, no words for the router, no actions and no data
    block: what the bot can do in the app are Toolsmith-compiled,
    Drawbridge-checked actions kept apart from the manifest, and its data is
    the bot's copy of the app's own storage.
    """
    app_id = data.get("id")
    if data.get("kind") != WEB_KIND or not is_issued_id(app_id):
        return (
            f"App id {app_id!r} is not usable. A browser app's id is the one "
            f"Toolsmith issued, and its manifest says kind 'web'."
        )
    extra = sorted(str(k) for k in data if k not in _WEB_KEYS)
    if extra:
        return f"Browser app '{app_id}' declares fields it may not have: {', '.join(extra[:5])}."
    if not _display_text(data.get("name"), MAX_WEB_NAME_LEN):
        return f"Browser app '{app_id}' has no usable display name."
    if "blurb" in data and not _display_text(data.get("blurb"), MAX_UI_BLURB_LEN):
        return f"Browser app '{app_id}' has an unusable blurb."
    web = data.get("web")
    if not isinstance(web, dict) or set(web) - {"source", "files_root", "entry", "storage"}:
        return f"Browser app '{app_id}' has an unusable web block."
    if web.get("entry") != "index.html" or web.get("storage") != "mirror":
        return f"Browser app '{app_id}' has an unusable web block."
    if not isinstance(web.get("files_root"), str) or len(web["files_root"]) > 1024:
        return f"Browser app '{app_id}' has an unusable web block."
    source = web.get("source", {})
    if not isinstance(source, dict) or set(source) - _WEB_SOURCE_KEYS or not all(
            isinstance(v, str) and len(v) <= 300 for v in source.values()):
        return f"Browser app '{app_id}' has an unusable source."
    outside = data.get("outside", [])
    if not isinstance(outside, list) or len(outside) > MAX_WEB_OUTSIDE:
        return f"Browser app '{app_id}' has an unusable outside list."
    for row in outside:
        if not isinstance(row, dict) or set(row) - {"host", "for", "learned"}:
            return f"Browser app '{app_id}' has an unusable outside list."
        host = row.get("host")
        if not isinstance(host, str) or len(host) > MAX_HOST_LEN or not _HOST_RE.match(host):
            return f"Browser app '{app_id}' names an unusable outside host."
        if not _display_text(row.get("for"), 80) or row.get("learned") != "scan":
            return f"Browser app '{app_id}' has an unusable outside list."
    ui = data.get("ui")
    if not isinstance(ui, dict) or set(ui) != {"route"} \
            or ui.get("route") != f"/dashboard/apps/{app_id}":
        return f"Browser app '{app_id}' must open at /dashboard/apps/{app_id} and nowhere else."
    return None


def _validate_ui(app_id: Any, ui: Any) -> Optional[str]:
    if ui is None:
        return None
    if not isinstance(ui, dict):
        return f"App '{app_id}' has a ui block that is not an object."
    route = ui.get("route")
    if route is not None:
        if not _ascii_text(route, MAX_LABEL_LEN):
            return f"App '{app_id}' has an unusable ui route."
        if not route.startswith("/") or ".." in route:
            return (
                f"App '{app_id}' has ui route {route!r}; a route is an "
                f"absolute in-app path and may not walk upwards."
            )
    for field in ("icon", "widget"):
        if field in ui and not _ascii_text(ui.get(field), MAX_LABEL_LEN):
            return f"App '{app_id}' has an unusable ui {field}."
    return None


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def _sort_key(manifest: dict[str, Any]) -> tuple[int, str]:
    """Apps are consulted in declared order; id breaks ties.

    Order is what makes the assembled vocabulary reproducible: the shipped five
    carry explicit orders so their words land in the router in the same
    sequence they had when they were one static file.
    """
    order = manifest.get("order", 500)
    if not isinstance(order, int) or isinstance(order, bool):
        order = 500
    return (order, str(manifest.get("id", "")))


def load_manifests(path: Optional[str] = None) -> list[dict[str, Any]]:
    """Every valid installed manifest, in consultation order.

    Installing is rescanning: this function IS the install mechanism. One bad
    manifest does not take the others down - it is recorded in
    :mod:`puffpai.config_health` and skipped - and an absent ``apps/``
    directory is legitimate (a build that carries no apps) and stays quiet.

    Two manifests claiming one id is a collision, and both are dropped with an
    error naming both folders. Keeping the first would make "whichever folder
    sorts earlier" the winner, which is precisely how a hostile app would
    shadow a shipped one; keeping the last is the same bug facing the other
    way. The owner gets told which two folders are fighting and removes one.

    With no ``path`` this scans BOTH roots - the shipped ``apps/`` and the
    owner's :func:`user_apps_dir` - so a pip-installed build can be extended
    without editing the package. A named ``path`` scans exactly that folder and
    nothing else, which is what every caller that passes one means.
    """
    from puffpai import build_interview
    known_surfaces = set(build_interview.surface_taxonomy())

    by_id: dict[str, dict[str, Any]] = {}
    folder_of: dict[str, str] = {}
    collided: set[str] = set()

    for target in ([path] if path else apps_dirs()):
        _scan_apps_dir(target, known_surfaces, by_id, folder_of, collided,
                       quiet_missing=(path is None and target != _apps_dir()))

    manifests = sorted(by_id.values(), key=_sort_key)
    if len(manifests) > MAX_INSTALLED_APPS:
        config_health.record(
            CONFIG_NAME, path or _apps_dir(), config_health.INVALID,
            f"{len(manifests)} apps are installed; this build carries the "
            f"first {MAX_INSTALLED_APPS}. Remove some and rescan.",
            reduced=False,
        )
        manifests = manifests[:MAX_INSTALLED_APPS]
    return manifests


def _scan_apps_dir(
    target: str,
    known_surfaces: set[str],
    by_id: dict[str, dict[str, Any]],
    folder_of: dict[str, str],
    collided: set[str],
    *,
    quiet_missing: bool = False,
) -> None:
    """Read one apps root into ``by_id`` / ``folder_of``. Never raises.

    ``quiet_missing`` is for the owner's own folder: a build where nobody has
    scaffolded a persona yet has no such directory, and that is not a fault
    worth reporting.
    """
    try:
        names = sorted(os.listdir(target))
    except OSError as exc:
        if not (quiet_missing and isinstance(exc, FileNotFoundError)):
            config_health.note_load_failure(CONFIG_NAME, target, exc)
        return

    for name in names:
        if name.startswith(".") or name.startswith("_"):
            continue
        folder = os.path.join(target, name)
        if not os.path.isdir(folder):
            continue
        manifest_file = os.path.join(folder, MANIFEST_NAME)
        if not os.path.isfile(manifest_file):
            config_health.record(
                CONFIG_NAME, folder, config_health.INVALID,
                f"Folder '{name}' is in the apps directory but has no "
                f"{MANIFEST_NAME}, so nothing there is installed.",
                reduced=False,
            )
            continue
        try:
            size = os.path.getsize(manifest_file)
        except OSError as exc:
            config_health.note_load_failure(CONFIG_NAME, manifest_file, exc, reduced=False)
            continue
        if size > MAX_MANIFEST_BYTES:
            config_health.record(
                CONFIG_NAME, manifest_file, config_health.INVALID,
                f"App manifest '{name}' is {size} bytes; the ceiling is "
                f"{MAX_MANIFEST_BYTES}. It was not installed.",
                reduced=False,
            )
            continue
        try:
            with open(manifest_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            config_health.record(
                CONFIG_NAME, manifest_file, config_health.UNPARSEABLE,
                f"App manifest '{name}' could not be read: {exc}", reduced=False,
            )
            continue

        # Every skip above and below is reduced=False on purpose: one bad
        # manifest costs one app, not the whole registry. The other apps
        # loaded, and claiming a build-wide fallback sends the owner
        # hunting a fault that is not there.
        problem = validate_manifest(data, known_surfaces)
        if problem is not None:
            config_health.record(
                CONFIG_NAME, manifest_file, config_health.INVALID,
                f"App manifest '{name}' was skipped: {problem}", reduced=False,
            )
            continue

        app_id = str(data["id"])
        # The pattern already passed; this is the containment check that has to
        # hold even if the pattern is ever loosened.
        try:
            app_dir(app_id, target)
        except ValueError as exc:
            config_health.record(
                CONFIG_NAME, manifest_file, config_health.INVALID,
                f"App manifest '{name}' was skipped: {exc}", reduced=False,
            )
            continue

        if app_id in by_id or app_id in collided:
            first = folder_of.get(app_id, "an earlier folder")
            config_health.record(
                CONFIG_NAME, target, config_health.INVALID,
                f"Two apps claim the id '{app_id}': the folders '{first}' and "
                f"'{os.path.join(target, name)}'. Neither is installed. Rename "
                f"or remove one and rescan.",
                reduced=False,
            )
            by_id.pop(app_id, None)
            collided.add(app_id)
            continue

        by_id[app_id] = data
        folder_of[app_id] = os.path.join(target, name)


def installed_ids(manifests: Optional[list[dict[str, Any]]] = None) -> list[str]:
    """The ids of the installed apps, in consultation order."""
    return [str(m["id"]) for m in (manifests if manifests is not None
                                   else load_manifests())]


def default_data(manifest: dict[str, Any]) -> dict[str, Any]:
    """A fresh copy of one app's declared starting shape.

    A copy every time: the manifest is loaded once and the caller mutates what
    it gets back, so handing out the manifest's own dict would let one app's
    first write become every later owner's "default".
    """
    block = manifest.get("data")
    default = block.get("default") if isinstance(block, dict) else None
    if not isinstance(default, dict):
        return {}
    return json.loads(json.dumps(default))


# ---------------------------------------------------------------------------
# router vocabulary
# ---------------------------------------------------------------------------

def assemble_vocabulary(
    manifests: Optional[list[dict[str, Any]]] = None,
) -> dict[str, list[str]]:
    """The words and published phrasings of the installed apps, concatenated.

    This is the owner's requirement 5 made mechanical: the router's app vocabulary
    is the apps the build actually has. Remove an app's folder and its words
    leave with it; drop a stranger's folder in and its words arrive, without a
    line of core changing.

    Order follows :func:`_sort_key`, and duplicates collapse onto their first
    appearance so two apps both claiming "reminder" cost one scan, not two, and
    the first app declared keeps its position.
    """
    keywords: list[str] = []
    examples: list[str] = []
    for manifest in (manifests if manifests is not None else load_manifests()):
        vocabulary = manifest.get("vocabulary")
        if not isinstance(vocabulary, dict):
            continue
        for word in (vocabulary.get("keywords") or []):
            if isinstance(word, str) and word not in keywords:
                keywords.append(word)
        for phrase in (vocabulary.get("examples") or []):
            if isinstance(phrase, str) and phrase not in examples:
                examples.append(phrase)
    return {"keywords": keywords, "examples": examples}


# ---------------------------------------------------------------------------
# what the dashboard is told
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# packs
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cached_manifests: Optional[list[dict[str, Any]]] = None


def current_manifests() -> list[dict[str, Any]]:
    """The installed manifests, scanning once and reusing until :func:`rescan`."""
    global _cached_manifests
    with _cache_lock:
        if _cached_manifests is None:
            _cached_manifests = load_manifests()
        return list(_cached_manifests)


def rescan() -> list[dict[str, Any]]:
    """Re-read ``apps/`` from disk. This IS the install/uninstall mechanism.

    Dropping the cache and rebuilding it is the whole of "install": no script
    runs, nothing is imported. An app whose folder is gone is gone from the
    answer on the next call, which is what makes removing a folder an uninstall.
    """
    global _cached_manifests
    with _cache_lock:
        _cached_manifests = None
    manifests = current_manifests()
    # A rescan IS install and uninstall, so it is also when an app's outside
    # service (its feed host) is registered or dropped. Every one starts shut.
    try:
        from puffpai import feeds
        feeds.sync_doors(manifests)
    except Exception as exc:  # pragma: no cover - never blocks a rescan
        config_health.record(CONFIG_NAME, "feeds", config_health.INVALID,
                             f"Live-data services could not be registered: "
                             f"{exc}", reduced=False)
    return manifests


def validate_pack(data: Any, installed: Optional[set] = None) -> Optional[str]:
    """Structural check of one parsed pack manifest; ``None`` means healthy.

    A pack is a LIST, not a container: it names apps that must already be
    installed, and it carries no data, no vocabulary, no ui and no code of its
    own. ``installed`` is the set of installed app ids; a pack naming an app
    this build does not have is refused with the missing ids named, because a
    pack that silently drops half its apps is a persona that quietly is not the
    persona the owner chose.
    """
    if not isinstance(data, dict):
        return "The pack manifest is not a JSON object."

    for key in _FORBIDDEN_KEYS:
        if key in data:
            return (
                f"The pack declares '{key}'. A pack is a list of apps, so no "
                f"field may name code to run."
            )

    if str(data.get("schema") or "") != PACK_SCHEMA:
        return (
            f"The pack says schema '{data.get('schema')}'; this build reads "
            f"'{PACK_SCHEMA}'."
        )

    pack_id = data.get("id")
    if not valid_id(pack_id):
        return f"Pack id {pack_id!r} is not usable."

    if not _ascii_text(data.get("display_name"), MAX_LABEL_LEN):
        return f"Pack '{pack_id}' has no usable display name."

    for field in ("blurb", "version", "author", "license", "posture_min"):
        if field in data and not _ascii_text(data.get(field), MAX_LABEL_LEN):
            return f"Pack '{pack_id}' has an unusable '{field}'."

    apps = data.get("apps")
    if not isinstance(apps, list) or not apps:
        return f"Pack '{pack_id}' lists no apps."
    if len(apps) > MAX_PACK_APPS:
        return (
            f"Pack '{pack_id}' lists {len(apps)} apps; {MAX_PACK_APPS} is the "
            f"most one pack may carry."
        )
    seen: set = set()
    missing: list = []
    for entry in apps:
        if not isinstance(entry, dict):
            return f"Pack '{pack_id}' has an app entry that is not an object."
        entry_id = entry.get("id")
        if not valid_id(entry_id):
            return f"Pack '{pack_id}' lists an unusable app id: {entry_id!r}."
        if entry_id in seen:
            return f"Pack '{pack_id}' lists app '{entry_id}' twice."
        seen.add(str(entry_id))
        version = entry.get("version")
        if version is not None and not _ascii_text(version, MAX_LABEL_LEN):
            return f"Pack '{pack_id}' has an unusable version for '{entry_id}'."
        if installed is not None and entry_id not in installed:
            missing.append(str(entry_id))
    if missing:
        return (
            f"Pack '{pack_id}' lists {', '.join(sorted(missing))}, which "
            f"{'is' if len(missing) == 1 else 'are'} not installed on this "
            f"build. Install the missing app"
            f"{'' if len(missing) == 1 else 's'} and rescan."
        )
    return None


def load_packs(path: Optional[str] = None,
               manifests: Optional[list[dict[str, Any]]] = None,
               available: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """Every valid installed pack, sorted by id, with its apps resolved.

    Packs load AFTER apps because a pack is validated against what is actually
    installed. Each returned pack carries a ``privacy`` summary DERIVED from its
    apps' own declarations - never written by the pack author, who is not
    necessarily the author of the apps and must not get to speak for them.

    Faults go through :mod:`puffpai.config_health`, the same place a bad app
    manifest lands, and every one is ``reduced=False``: one broken pack costs
    one pack, not the build.
    """
    app_manifests = manifests if manifests is not None else load_manifests()
    by_app_id = {str(m["id"]): m for m in app_manifests if valid_id(m.get("id"))}

    def note(where: str, detail: str) -> None:
        config_health.record(PACK_CONFIG_NAME, where, config_health.INVALID,
                             detail, reduced=False)

    if path is None:
        # Both roots: the shipped packs and the owner's own (see
        # :func:`user_packs_dir`). One id per build still, across both.
        merged: dict[str, dict[str, Any]] = {}
        for root in packs_dirs():
            for record in load_packs(root, manifests=app_manifests,
                                     available=available):
                pack_id = str(record["id"])
                if pack_id in merged:
                    note(root,
                         f"Two packs claim the id '{pack_id}', in different "
                         f"folders. Neither is installed. Rename or remove one "
                         f"and rescan.")
                    merged.pop(pack_id, None)
                    continue
                merged[pack_id] = record
        return [merged[key] for key in sorted(merged)][:MAX_INSTALLED_PACKS]

    target = path
    try:
        names = sorted(os.listdir(target))
    except OSError as exc:
        # A build carrying no packs is legitimate and stays quiet.
        if not isinstance(exc, FileNotFoundError):
            config_health.note_load_failure(PACK_CONFIG_NAME, target, exc,
                                            reduced=False)
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in names:
        if name.startswith(".") or name.startswith("_"):
            continue
        folder = os.path.join(target, name)
        if not os.path.isdir(folder):
            continue
        manifest_file = os.path.join(folder, PACK_MANIFEST_NAME)
        if not os.path.isfile(manifest_file):
            note(folder,
                 f"Folder '{name}' is in the packs directory but has no "
                 f"{PACK_MANIFEST_NAME}, so nothing there is installed.")
            continue
        try:
            if os.path.getsize(manifest_file) > MAX_MANIFEST_BYTES:
                note(manifest_file,
                     f"Pack manifest '{name}' is over the "
                     f"{MAX_MANIFEST_BYTES}-byte ceiling; it was not installed.")
                continue
            with open(manifest_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            note(manifest_file,
                 f"Pack manifest '{name}' could not be read: {exc}")
            continue

        problem = validate_pack(data, set(by_app_id))
        if problem is not None:
            note(manifest_file, f"Pack manifest '{name}' was skipped: {problem}")
            continue

        pack_id = str(data["id"])
        if pack_id in seen:
            note(target,
                 f"Two packs claim the id '{pack_id}'. Neither is installed. "
                 f"Rename or remove one and rescan.")
            out = [p for p in out if p["id"] != pack_id]
            continue
        seen.add(pack_id)
        out.append(pack_record(data, by_app_id, available))

        if len(out) > MAX_INSTALLED_PACKS:
            note(target,
                 f"More than {MAX_INSTALLED_PACKS} packs are installed; the "
                 f"rest were not loaded.")
            out = out[:MAX_INSTALLED_PACKS]
            break
    return out


def pack_privacy_summary(app_ids: list, by_app_id: dict) -> dict:
    """The pack's privacy line, aggregated from the apps it lists.

    Derived, never declared: the pack author does not get to summarise somebody
    else's app. ``hosts`` is the union of every door's hosts, ``credentials``
    the set of credential kinds other than "none", and
    ``child_safe_declared`` is true only when EVERY listed app says so - one app
    that will not answer for a minor makes the pack unsuitable for one.

    "Declared" is load-bearing in that name. Every field here is an app author's
    own claim; the box has verified none of it, and a surface that renders this
    as "child safe" is lying on our behalf.
    """
    hosts: list = []
    credentials: list = []
    child_flags: list = []
    for app_id in app_ids:
        manifest = by_app_id.get(app_id)
        if not isinstance(manifest, dict):
            continue
        for door in (manifest.get("doors") or []):
            if not isinstance(door, dict):
                continue
            for host in (door.get("hosts") or []):
                if isinstance(host, str) and host not in hosts:
                    hosts.append(host)
        credential = manifest.get("credential")
        if isinstance(credential, dict):
            kind = credential.get("kind")
            if isinstance(kind, str) and kind != "none" and kind not in credentials:
                credentials.append(kind)
        children = manifest.get("children")
        if isinstance(children, dict):
            child_flags.append(bool(children.get("may_be_used_by_minor")))
    return {
        "hosts": sorted(hosts),
        "hosts_enforcement": HOSTS_ENFORCEMENT,
        "hosts_notice": HOSTS_ENFORCEMENT_NOTICE,
        "credentials": sorted(credentials),
        # DECLARED, never "safe". This is an AND-fold over five boxes the app
        # authors ticked themselves; nothing here has been verified by anything.
        # The name is the honesty: a parent reading "child_safe" would hear a
        # judgement the box has not made.
        "child_safe_declared": bool(child_flags) and all(child_flags),
    }


def pack_record(data: dict, by_app_id: dict,
                available: Optional[dict] = None) -> dict:
    """One validated pack as the API hands it out. Fields copied by name."""
    app_ids = [str(e.get("id")) for e in (data.get("apps") or [])
               if isinstance(e, dict) and valid_id(e.get("id"))]
    return {
        "id": str(data["id"]),
        "display_name": str(data.get("display_name") or data["id"])[:MAX_UI_NAME_LEN],
        "blurb": str(data.get("blurb") or "")[:MAX_UI_BLURB_LEN],
        "version": str(data.get("version") or "")[:MAX_UI_NAME_LEN],
        "posture_min": str(data.get("posture_min") or "")[:MAX_UI_NAME_LEN],
        "apps": [
            {"id": str(e.get("id")),
             "version": str(e.get("version") or "")[:MAX_UI_NAME_LEN]}
            for e in (data.get("apps") or [])
            if isinstance(e, dict) and valid_id(e.get("id"))
        ],
        "privacy": pack_privacy_summary(app_ids, by_app_id),
        # A pack is only as installed as its least-ready app.
        "degraded_apps": [
            {"id": app_id,
             "reason": degraded_reason(by_app_id[app_id], available)}
            for app_id in app_ids
            if app_id in by_app_id
            and degraded_reason(by_app_id[app_id], available)
        ],
    }


# ---------------------------------------------------------------------------
# router vocabulary
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# dashboard records
# ---------------------------------------------------------------------------

def _safe_ui_route(route: Any) -> str:
    """An app's route, or ``""`` when it is not something a browser may follow.

    An app with no usable route still exists - it keeps its words, its data and
    its catalog record. It just gets no nav entry, because there is nowhere
    honest to send the owner.
    """
    if not isinstance(route, str) or not route.startswith("/"):
        return ""
    if route.startswith("//") or ".." in route:
        return ""
    if not _SAFE_ROUTE_RE.match(route) or len(route) > MAX_LABEL_LEN:
        return ""
    return route


def ui_records(
    manifests: Optional[list[dict[str, Any]]] = None,
    available: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """The installed apps as the dashboard needs them, field by field.

    The nav, the apps index and the surface map all used to carry their own
    hand-typed copy of the app list, which is why removing an app left three
    places still insisting it was there. This is the one answer they all read.

    Every field is copied out by name and bounded here, the way ``/build/self``
    copies the manifest. A manifest is never echoed wholesale: an app author is
    not us, and a field this build has no opinion about does not get to reach a
    browser just because it was in the file. ``name`` and ``blurb`` are the
    app author's words and are rendered as text - escaped by React like any
    other string, never markup, never a prompt. ``icon`` is a NAME, not an SVG
    path; the dashboard looks it up in its own icon set and falls back when it
    does not recognise it, so the worst a hostile icon field can do is be
    ignored.
    """
    out: list[dict[str, Any]] = []
    for manifest in (manifests if manifests is not None else load_manifests()):
        app_id = manifest.get("id")
        if not valid_id(app_id):
            continue
        raw_ui = manifest.get("ui")
        ui: dict[str, Any] = raw_ui if isinstance(raw_ui, dict) else {}
        surfaces = [
            str(s) for s in (manifest.get("surfaces") or [])
            if isinstance(s, str)
        ][:MAX_SURFACES]
        order = manifest.get("order", 500)
        if not isinstance(order, int) or isinstance(order, bool):
            order = 500
        icon = ui.get("icon")
        reason = degraded_reason(manifest, available)
        out.append({
            "id": str(app_id),
            # An app this build cannot currently run is INSTALLED and degraded,
            # never hidden: hiding it would make "I installed FreshTable and it
            # vanished" the owner's experience of a missing model.
            "degraded": bool(reason),
            "degraded_reason": reason,
            "requires": declared_requirements(manifest),
            "hosts_enforcement": HOSTS_ENFORCEMENT,
            "hosts_notice": HOSTS_ENFORCEMENT_NOTICE,
            "name": str(manifest.get("name") or app_id)[:MAX_UI_NAME_LEN],
            "blurb": str(manifest.get("blurb") or "")[:MAX_UI_BLURB_LEN],
            "route": _safe_ui_route(ui.get("route")),
            "icon": (str(icon)[:MAX_UI_ICON_LEN] if isinstance(icon, str) else ""),
            "surfaces": surfaces,
            "order": max(0, min(int(order), MAX_ORDER)),
            "feed": _feed_summary(manifest),
        })
    return out


def _feed_summary(manifest: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The app's live-data card, described in bounded fields, or None."""
    if "feed" not in manifest:
        return None
    try:
        from puffpai import feeds
        return feeds.ui_summary(manifest)
    except Exception:  # pragma: no cover - a record never fails on this
        return None


__all__ = [
    "SCHEMA",
    "PACK_SCHEMA",
    "APPS_SURFACE",
    "APPS_DIRNAME",
    "MANIFEST_NAME",
    "MAX_MANIFEST_BYTES",
    "MAX_INSTALLED_APPS",
    "MAX_KEYWORDS",
    "MAX_EXAMPLES",
    "MAX_ACTIONS",
    "CREDENTIAL_KINDS",
    "REFUSED_CREDENTIAL_KINDS",
    "HOSTS_ENFORCEMENT",
    "HOSTS_ENFORCEMENT_NOTICE",
    "REQUIREMENT_REASONS",
    "REQUIREMENT_UNVERIFIED",
    "app_dir",
    "assemble_vocabulary",
    "current_manifests",
    "declared_requirements",
    "degraded_reason",
    "unmet_requirements",
    "load_packs",
    "pack_privacy_summary",
    "pack_record",
    "rescan",
    "validate_pack",
    "data_path",
    "default_data",
    "installed_ids",
    "load_manifests",
    "resolve_within",
    "ui_records",
    "valid_id",
    "is_issued_id",
    "ISSUED_ID_RE",
    "WEB_KIND",
    "validate_manifest",
]
