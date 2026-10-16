"""The first-run walkthrough - the bot makes itself the owner's.

Setup builds the machine. It does not make it *theirs*. Naming it, giving it a
face, arranging the dashboard, choosing what it should be good at, deciding who
is on the crew - that is where a build stops being a box that boots and starts
being someone's bot. Today all of that lives on dashboard pages a stranger has
to find alone, which is why a person can finish the wizard and reasonably
conclude they were handed a terminal chatbot.

So the bot leads this, not the wizard. As soon as it exists it says what it is
and asks to be made theirs.

**The opening is not copy.** It is :func:`puffpai.self_knowledge.limits_summary`
verbatim - the honest account of what THIS build can and cannot do, assembled
from its own manifest. A bot whose first words are its own limits, and whose
second words are a request to be personalised, is the whole product thesis on
one screen. Nothing here writes a greeting over the top of it.

Three rules shape the rest:

* **Never re-ask what setup already settled.** Every step reads the real state
  first. The build interview already asked what the bot should be good at and
  crew sizing already picked specialists; those steps are shown as done, with
  the answer, not asked again.
* **Every step is skippable and skipping never dead-ends.** A skipped step
  leaves a working bot with a shipped default, and says which default it took.
* **One prompt, and "not now" is respected.** The walkthrough nags exactly once
  and then stops until the owner comes back. :func:`dismiss` is permanent until
  :func:`restart`.

Answers go where that kind of answer already lives - the config identity block
for the name and the face, the manifest for build identity, the existing
dashboard pages for the look, the apps, and the crew. This module is a thread
through what exists, not a second copy of it.

**The name is untrusted text.** It is rendered in the dashboard and it is
substituted into the persona the model is given, so a name carrying a newline
and a role marker would be a prompt-injection vector with the owner's own hand
on it. :func:`sanitize_name` reduces a name to letters, digits, spaces and four
punctuation marks, caps it short, and refuses what is left of nothing. Same
doctrine as the build goal in :mod:`puffpai.build_interview`: it is stored as
data, matched as data, never read as an instruction.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import time
import unicodedata
from typing import Any, Optional

SCHEMA = "first_run/v1"

# Step ids. Ordered: identity first (it is the one everything else renders),
# then appearance, then what the bot is for, then who else is in the building.
STEP_NAME = "name"
STEP_FACE = "face"
STEP_LOOK = "look"
STEP_APPS = "apps"
STEP_CREW = "crew"

STEP_ORDER = (STEP_NAME, STEP_FACE, STEP_LOOK, STEP_APPS, STEP_CREW)

# Status vocabulary. "settled" is distinct from "done" on purpose: the owner did
# not answer it here, setup did, and saying so is what stops the walkthrough
# feeling like it forgot the conversation they just had.
STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_SETTLED = "settled"

# A name is a label, not a sentence. Long enough for "The Observatory Bot",
# short enough that nothing narrative fits.
MAX_NAME_LEN = 40

# Punctuation a real name uses. Everything else - newlines, colons, braces,
# backticks, angle brackets, zero-width and bidi marks - is dropped, because the
# only reason to put those in a bot's name is to break out of the slot it is
# rendered into.
_NAME_PUNCTUATION = frozenset(" -_.'")

# Names the product ships with. Finding one of these on disk means nobody has
# chosen yet, so the name step is still worth asking.
_SHIPPED_NAMES = frozenset({"puffpai", "buildbot", ""})

# The faces the dashboard can actually draw (dicebear styles, mirrored from the
# profile page's picker). An unknown style is refused rather than stored, so the
# avatar never falls back to a broken render.
FACES: tuple[dict[str, str], ...] = (
    {"id": "notionists", "label": "Notionists", "note": "Line-drawn, plain."},
    {"id": "bottts", "label": "Bottts", "note": "Obviously a robot."},
    {"id": "pixel-art", "label": "Pixel Art", "note": "Small and blocky."},
    {"id": "adventurer", "label": "Adventurer", "note": "A face, not a device."},
)

_FACE_IDS = frozenset(f["id"] for f in FACES)
DEFAULT_FACE = "notionists"


# ---------------------------------------------------------------------------
# the steps
# ---------------------------------------------------------------------------
#
# ``kind`` is the difference between a question this module answers and a
# question a page already answers better:
#
#   "answer" - the walkthrough takes a value and writes it somewhere.
#   "visit"  - the walkthrough sends the owner to the page that already does
#              this well and records that they went. Rebuilding those UIs here
#              would fork them, and a forked settings page is worse than a link.

_STEPS: tuple[dict[str, Any], ...] = (
    {
        "id": STEP_NAME,
        "kind": "answer",
        "title": "A name",
        "line": ("I do not have a name yet. Pick one you will actually say out "
                 "loud - it goes on the dashboard and it is what I answer to."),
        "wants": "A name, up to 40 characters.",
        "stores": "config identity.name, and the build manifest's build name.",
        "default": "I stay as I shipped, and you can name me any time.",
        "page": "/dashboard/profile",
    },
    {
        "id": STEP_FACE,
        "kind": "answer",
        "title": "A face",
        "line": ("Pick what I look like. It is drawn on this box from your "
                 "name - nothing is fetched and nothing is uploaded."),
        "wants": "One of the four avatar styles.",
        "stores": "config identity.avatar_style and identity.avatar_seed.",
        "default": "I use the plain line-drawn face.",
        "page": "/dashboard/avatar",
    },
    {
        "id": STEP_LOOK,
        "kind": "visit",
        "title": "A look",
        "line": ("The dashboard is yours to arrange. Move the tiles, drop the "
                 "ones you do not want, set light or dark. I keep what you "
                 "leave."),
        "wants": "A look around the dashboard.",
        "stores": "The tile registry on this box; the theme in your browser.",
        "default": "You get the shipped layout, and it is rearrangeable later.",
        "page": "/dashboard",
    },
    {
        "id": STEP_APPS,
        "kind": "visit",
        "title": "What I should be good at",
        "line": ("These are the surfaces this build carries. Open the ones you "
                 "will use so they have something in them - an empty calendar "
                 "is not much of a calendar."),
        "wants": "A look at the apps this build loaded.",
        "stores": "The manifest's surface allowlist; each app keeps its own data.",
        "default": "Everything this build loaded stays loaded, unused.",
        "page": "/dashboard/apps",
    },
    {
        "id": STEP_CREW,
        "kind": "visit",
        "title": "Who is on the crew",
        "line": ("The master team runs whatever this machine fits. You can see "
                 "who is on it and call them what you like."),
        "wants": "A look at the roster and its display names.",
        "stores": "The agent roster on this box.",
        "default": "The crew keeps its shipped names.",
        "page": "/dashboard/agents",
    },
)

_STEPS_BY_ID: dict[str, dict[str, Any]] = {s["id"]: s for s in _STEPS}


def steps() -> list[dict[str, Any]]:
    """The ordered step definitions, as copies. Never raises."""
    return [dict(s) for s in _STEPS]


def step_ids() -> tuple[str, ...]:
    """The step ids, in order."""
    return STEP_ORDER


# ---------------------------------------------------------------------------
# the name - untrusted text, all the way down
# ---------------------------------------------------------------------------

def sanitize_name(raw: Any) -> str:
    """Reduce an owner-typed name to something safe to render and to store.

    Kept: letters and digits in any script, spaces, and ``-_.'``. Dropped:
    every control character, every Unicode format character (zero-width joiners,
    bidi overrides, the invisible things that make one string look like
    another), and every punctuation mark that reads as structure - newlines,
    colons, braces, brackets, angle brackets, backticks, pipes.

    That list is not aesthetic. ``AI_NAME`` is substituted into the persona text
    the model is handed, so a name containing a newline and a role marker would
    be an injected instruction wearing the owner's own choice as a disguise.
    A name that cannot contain a line break cannot open a new turn.

    Whitespace collapses, the result is trimmed and capped at
    :data:`MAX_NAME_LEN`, and an empty result comes back empty for the caller to
    refuse. Never raises.
    """
    try:
        text = raw if isinstance(raw, str) else str(raw or "")
    except Exception:
        return ""

    kept: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        # Cc/Cf/Cs/Co/Cn: control, format, surrogate, private-use, unassigned.
        # None of them belong in a name and all of them are useful to an
        # attacker, so the whole C block goes.
        if category.startswith("C"):
            continue
        if ch.isalnum() or ch in _NAME_PUNCTUATION:
            kept.append(ch)
        elif ch.isspace():
            kept.append(" ")

    collapsed = " ".join("".join(kept).split())
    return collapsed[:MAX_NAME_LEN].strip()


def name_is_acceptable(raw: Any) -> tuple[bool, str]:
    """Whether a typed name survives sanitizing, and what to say if it does not.

    A fact plus a door: what is missing and what to type instead.
    """
    clean = sanitize_name(raw)
    if not clean:
        return (False, "A name needs at least one letter or number. "
                       "Letters, digits, spaces and - _ . ' are what I keep.")
    return (True, "")


# ---------------------------------------------------------------------------
# state on disk
# ---------------------------------------------------------------------------

def state_path() -> str:
    """Absolute path of the walkthrough's record (honours PUFFPAI_DATA_DIR).

    Sits beside ``setup_state.json`` for the same reason that one does: a
    scratch install tracks its own progress and never reads another install's.
    """
    from puffpai.config import get_config
    return os.path.join(get_config().DATA_DIR, "data", "first_run.json")


def _empty_state() -> dict[str, Any]:
    return {"schema": SCHEMA, "runs": 0, "steps": {},
            "completed_at": None, "dismissed_at": None}


def read_state(path: Optional[str] = None) -> dict[str, Any]:
    """Load the walkthrough record, or an empty one. Never raises."""
    target = path or state_path()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    state = _empty_state()
    state.update({k: v for k, v in data.items() if k in state})
    if not isinstance(state.get("steps"), dict):
        state["steps"] = {}
    return state


def _write_state(state: dict[str, Any], path: Optional[str] = None) -> str:
    target = path or state_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")
    return target


def _record(step_id: str, status: str, *, note: str = "",
            path: Optional[str] = None) -> dict[str, Any]:
    state = read_state(path)
    entry: dict[str, Any] = {"status": status, "at": time.time()}
    if note:
        entry["note"] = note
    state["steps"][step_id] = entry
    if state.get("runs", 0) < 1:
        state["runs"] = 1
    _write_state(state, path)
    return state


# ---------------------------------------------------------------------------
# reading what already exists
# ---------------------------------------------------------------------------

def _raw_config() -> dict[str, Any]:
    """The on-disk main config as written, not as defaulted. Never raises."""
    from puffpai.config import get_config
    target = os.path.join(get_config().CONFIG_DIR, "puffpai_config.json")
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _identity_block() -> dict[str, Any]:
    block = _raw_config().get("identity")
    return block if isinstance(block, dict) else {}


def _manifest() -> dict[str, Any]:
    try:
        from puffpai import build_manifest
        return build_manifest.read_manifest() or {}
    except Exception:
        return {}


def current_name() -> str:
    """The name this build answers to right now, sanitized on the way out.

    The manifest is the build's record of identity and wins; the config block is
    what the running app reads. Sanitizing on read as well as on write means a
    hand-edited config cannot smuggle a name past us either.
    """
    man = _manifest()
    recorded = ((man.get("build") or {}).get("name") or "")
    if str(recorded).strip():
        return sanitize_name(recorded)
    return sanitize_name(_identity_block().get("name") or "")


def _detect(step_id: str) -> tuple[bool, str]:
    """Has setup already settled this step, and what did it settle it to?

    Returns ``(settled, plain-words answer)``. Only steps setup genuinely
    decides are ever reported settled - the look, for one, is nothing setup has
    an opinion about, so it is asked once rather than assumed.
    """
    if step_id == STEP_NAME:
        name = current_name()
        if name and name.strip().lower() not in _SHIPPED_NAMES:
            return (True, f"You called me {name}.")
        return (False, "")

    if step_id == STEP_FACE:
        style = str(_identity_block().get("avatar_style") or "").strip()
        if style and style in _FACE_IDS:
            label = next((f["label"] for f in FACES if f["id"] == style), style)
            return (True, f"Your face is set to {label}.")
        return (False, "")

    if step_id == STEP_APPS:
        surfaces = _manifest().get("surfaces") or {}
        allow = [str(s) for s in (surfaces.get("allowlist") or [])]
        if allow and (surfaces.get("derived_from_goal") or surfaces.get("owner_edited")):
            return (True, "Setup already decided this: I carry "
                          + ", ".join(sorted(allow)) + ".")
        return (False, "")

    if step_id == STEP_CREW:
        crew = _manifest().get("crew") or {}
        picked = [str(c) for c in (crew.get("selected_specialists") or [])]
        if picked:
            return (True, "Setup already picked the crew: "
                          + ", ".join(picked) + ".")
        return (False, "")

    # The look. Setup never asks about it, so there is nothing to read back.
    return (False, "")


# ---------------------------------------------------------------------------
# the opening
# ---------------------------------------------------------------------------

def opening(manifest: Optional[dict[str, Any]] = None) -> list[str]:
    """What the bot says first: its own limits, in its own words.

    This is :func:`puffpai.self_knowledge.limits_summary` and nothing else. No
    greeting is written over it, because the honest account of what this build
    can and cannot do IS the introduction, and anything friendlier in front of
    it would be the first thing the bot ever said that was not true.
    """
    from puffpai import self_knowledge
    return list(self_knowledge.limits_summary(manifest))


def opening_ask() -> str:
    """The one line that turns the opening into a request. Not a greeting."""
    return ("That is what I am. What I am not yet is yours. Five things and "
            "you can skip any of them: a name, a face, a look, what I should "
            "be good at, and who is on the crew.")


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------

def _status_for(step_id: str, state: dict[str, Any]) -> tuple[str, str]:
    """The live status of one step, and the plain-words answer behind it."""
    recorded = state.get("steps", {}).get(step_id)
    settled, detail = _detect(step_id)
    if isinstance(recorded, dict):
        status = str(recorded.get("status") or "")
        if status == STATUS_DONE:
            return (STATUS_DONE, detail or str(recorded.get("note") or ""))
        if status == STATUS_SKIPPED:
            # A skip is not a dead end. If setup turns out to have settled it
            # anyway, say so rather than leaving a hole in the record.
            return (STATUS_SKIPPED, detail)
    if settled:
        return (STATUS_SETTLED, detail)
    return (STATUS_PENDING, "")


def progress(state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Where the owner is, in numbers a caller can render.

    ``resolved`` counts everything that will not be asked again - answered,
    already settled by setup, or deliberately skipped - because that is what
    "am I finished" means to the person doing it.
    """
    st = state if state is not None else read_state()
    done = skipped = settled = 0
    current: Optional[str] = None
    for step_id in STEP_ORDER:
        status, _ = _status_for(step_id, st)
        if status == STATUS_DONE:
            done += 1
        elif status == STATUS_SETTLED:
            settled += 1
        elif status == STATUS_SKIPPED:
            skipped += 1
        elif current is None:
            current = step_id
    resolved = done + settled + skipped
    return {
        "total": len(STEP_ORDER),
        "done": done,
        "settled": settled,
        "skipped": skipped,
        "resolved": resolved,
        "remaining": len(STEP_ORDER) - resolved,
        "current": current,
        "finished": current is None,
        "complete": bool(st.get("completed_at")),
        "dismissed": bool(st.get("dismissed_at")),
        "runs": int(st.get("runs") or 0),
    }


def should_prompt(state: Optional[dict[str, Any]] = None) -> bool:
    """Whether to show the one amber prompt.

    Once, and only while there is something left to ask. Marking complete stops
    it. "Not now" stops it. Both are respected until :func:`restart`.
    """
    st = state if state is not None else read_state()
    if st.get("completed_at") or st.get("dismissed_at"):
        return False
    return progress(st)["remaining"] > 0


def prompt(state: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """The single amber prompt, or None. One at a time, never a queue."""
    st = state if state is not None else read_state()
    if not should_prompt(st):
        return None
    prog = progress(st)
    remaining = prog["remaining"]
    thing = "thing" if remaining == 1 else "things"
    return {
        "tone": "amber",
        "line": (f"I am running. There are {remaining} {thing} left that would "
                 f"make me yours rather than just installed."),
        "action": "Pick up where we left off",
        "dismiss": "Not now",
        "step": prog["current"],
    }


# ---------------------------------------------------------------------------
# the whole thing, for a caller to render
# ---------------------------------------------------------------------------

def walkthrough(manifest: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Everything a caller needs to draw the walkthrough at this moment."""
    state = read_state()
    out_steps: list[dict[str, Any]] = []
    for step_id in STEP_ORDER:
        step = dict(_STEPS_BY_ID[step_id])
        status, detail = _status_for(step_id, state)
        step["status"] = status
        step["answer"] = detail
        if step_id == STEP_FACE:
            step["choices"] = [dict(f) for f in FACES]
        out_steps.append(step)
    return {
        "schema": SCHEMA,
        "opening": opening(manifest),
        "opening_ask": opening_ask(),
        "name": current_name(),
        "steps": out_steps,
        "progress": progress(state),
        "prompt": prompt(state),
    }


# ---------------------------------------------------------------------------
# answering
# ---------------------------------------------------------------------------

def answer(step_id: str, value: Any = None) -> dict[str, Any]:
    """Record an answer for one step and persist it where it belongs.

    Returns ``{"ok": bool, "message": str, "step": id, ...}``. Never raises on a
    bad step id or a bad value - it says what it wanted instead.
    """
    step = _STEPS_BY_ID.get(str(step_id or ""))
    if step is None:
        return {"ok": False, "step": step_id,
                "message": "That is not one of the five. "
                           + ", ".join(STEP_ORDER) + "."}

    if step["id"] == STEP_NAME:
        return _answer_name(value)
    if step["id"] == STEP_FACE:
        return _answer_face(value)
    return _answer_visit(step)


def _answer_name(value: Any) -> dict[str, Any]:
    ok, reason = name_is_acceptable(value)
    if not ok:
        return {"ok": False, "step": STEP_NAME, "message": reason}
    clean = sanitize_name(value)

    from puffpai import setup_ops
    # The config identity block is what the running app reads for AI_NAME, and
    # write_main_config is the existing writer for it - honours PUFFPAI_DATA_DIR
    # and reloads the singleton, so the name is live without a restart.
    setup_ops.write_main_config({"identity": {"name": clean}})
    recorded = _record_name_in_manifest(clean)

    _record(STEP_NAME, STATUS_DONE, note=clean)
    return {"ok": True, "step": STEP_NAME, "name": clean,
            "manifest_updated": recorded,
            "message": f"I am {clean}."}


def _record_name_in_manifest(clean: str) -> bool:
    """Put the name in the manifest's identity slot, through its own writer.

    The manifest is where a build records what it is, so a named build should
    say so there too. This goes through :func:`build_manifest.write_manifest`,
    which runs the contract guard on the way to disk - the name is sanitized
    twice over and can still never turn a contract off. No manifest, no write:
    an unconfigured build has nothing to amend.
    """
    try:
        from puffpai import build_manifest
        man = build_manifest.read_manifest()
        if not man:
            return False
        build = man.get("build")
        if not isinstance(build, dict):
            build = {}
        build["name"] = clean
        man["build"] = build
        build_manifest.write_manifest(man)
        return True
    except Exception:
        return False


def _answer_face(value: Any) -> dict[str, Any]:
    style = str(value or "").strip().lower()
    if style not in _FACE_IDS:
        return {"ok": False, "step": STEP_FACE,
                "message": "I can draw four faces: "
                           + ", ".join(f["id"] for f in FACES) + "."}
    from puffpai import setup_ops
    from puffpai.config import get_config
    # The face is drawn from the seed, so an unnamed build seeds from whatever
    # it is currently called rather than from the style id - a face generated
    # from the word "bottts" belongs to nobody.
    seed = current_name() or get_config().identity.name
    setup_ops.write_main_config(
        {"identity": {"avatar_style": style, "avatar_seed": seed}})
    _record(STEP_FACE, STATUS_DONE, note=style)
    label = next((f["label"] for f in FACES if f["id"] == style), style)
    return {"ok": True, "step": STEP_FACE, "face": style,
            "message": f"Drawn as {label}, from this box."}


def _answer_visit(step: dict[str, Any]) -> dict[str, Any]:
    """Mark a page-owned step seen. The page did the persisting, not us."""
    _record(step["id"], STATUS_DONE)
    return {"ok": True, "step": step["id"], "page": step["page"],
            "message": f"{step['title']} - noted."}


def skip(step_id: str) -> dict[str, Any]:
    """Skip one step. Says which shipped default it is leaving in place.

    Skipping never dead-ends: the default is a working answer, the step stays
    re-answerable, and nothing downstream waits on it.
    """
    step = _STEPS_BY_ID.get(str(step_id or ""))
    if step is None:
        return {"ok": False, "step": step_id,
                "message": "That is not one of the five. "
                           + ", ".join(STEP_ORDER) + "."}
    _record(step["id"], STATUS_SKIPPED)
    return {"ok": True, "step": step["id"], "skipped": True,
            "message": step["default"]}


def complete() -> dict[str, Any]:
    """Mark the walkthrough finished. The prompt stops."""
    state = read_state()
    state["completed_at"] = time.time()
    state["dismissed_at"] = None
    if state.get("runs", 0) < 1:
        state["runs"] = 1
    _write_state(state)
    return {"ok": True, "complete": True,
            "message": "Done. Everything here can be changed again later."}


def dismiss() -> dict[str, Any]:
    """"Not now" - and it is respected. The prompt stops until a restart."""
    state = read_state()
    state["dismissed_at"] = time.time()
    _write_state(state)
    return {"ok": True, "dismissed": True,
            "message": "Not now. I will not bring it up again - it is on the "
                       "dashboard when you want it."}


def restart() -> dict[str, Any]:
    """Run the whole thing again. Answers on disk stay; the record resets.

    A re-run does not undo anything. The name, the face, the tiles and the crew
    stay exactly as they are; what resets is the walkthrough's own bookkeeping,
    so each step is re-offered showing the current answer rather than a blank.
    """
    state = read_state()
    runs = int(state.get("runs") or 0)
    fresh = _empty_state()
    fresh["runs"] = runs + 1
    _write_state(fresh)
    return {"ok": True, "restarted": True, "runs": fresh["runs"],
            "message": "Starting over. Nothing was undone - each step shows "
                       "what it is set to now."}


__all__ = [
    "SCHEMA",
    "STEP_ORDER", "STEP_NAME", "STEP_FACE", "STEP_LOOK", "STEP_APPS", "STEP_CREW",
    "STATUS_PENDING", "STATUS_DONE", "STATUS_SKIPPED", "STATUS_SETTLED",
    "MAX_NAME_LEN", "FACES", "DEFAULT_FACE",
    "steps", "step_ids", "sanitize_name", "name_is_acceptable",
    "state_path", "read_state", "current_name",
    "opening", "opening_ask", "progress", "should_prompt", "prompt",
    "walkthrough", "answer", "skip", "complete", "dismiss", "restart",
]
