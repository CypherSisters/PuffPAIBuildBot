"""What this build cannot do, and why - answered from the build itself.

What the build CAN do (open outside services, installed apps, indexed
libraries, and the abilities it lacks) comes from
:mod:`puffpai.build_abilities`, which reads the running build. The choices the
owner made at setup (pages left out, the internet shut) come from the build
manifest. The wizard already decided those limitations. A build that
runs a small brain cannot plan; a sealed build cannot reach the network; a
space bot never loaded the camera surface; a vault that is sealed needs a PIN.
All of it is recorded in ``config/build_manifest.json`` by the time setup
finishes. Nothing here detects anything new. It reads the record and turns it
into plain words.

Why this exists at all: a bot that cannot do something has three ways to fail,
and two of them are terrible. It can raise an opaque error, it can quietly
pretend it complied ("Sure, I turned on the lights"), or it can say what it is
and what it is not. The third is the only acceptable one, and hallucinated
compliance is the worst thing a local assistant can do to the person trusting
it. The smaller the brain, the more often it happens - which means the builds
that need this most are exactly the ones running on a stranger's old laptop.

The rule that makes it work: **ask before dispatch, not after failure.** A
request for a surface this build never loaded is answered here, honestly, and
never reaches a model that would improvise compliance.

Voice: a limitation is a fact plus a door, never an apology. Say what the build
is, not what it failed to be, and where a limit can be lifted, say how.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
from typing import Any, Optional

# Why a capability is unavailable. Ordered most-specific first: when several
# apply, the most actionable explanation is the one worth saying out loud.
CAUSE_NOT_CONFIGURED = "not_configured"
CAUSE_SURFACE_NOT_LOADED = "surface_not_loaded"
CAUSE_EGRESS_SEALED = "egress_sealed"
CAUSE_EGRESS_LAN_ONLY = "egress_lan_only"
CAUSE_NO_PLANNING = "no_planning"
CAUSE_VAULT_SEALED = "vault_sealed"
CAUSE_NO_CREW = "no_crew"
CAUSE_CONFIG_DEGRADED = "config_degraded"
CAUSE_NOT_BUILT = "not_built"
CAUSE_UNKNOWN = "unknown"

# Surfaces whose whole point is reaching past this box. Asking for one of these
# on a sealed build is a posture answer, not a surface answer.
_WAN_SURFACES = frozenset({
    "knowledge.web", "media.streaming", "comms.mail",
})


def _manifest(manifest: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """The committed manifest, or an empty dict. Never raises."""
    if manifest is not None:
        return manifest
    try:
        from puffpai import build_manifest
        return build_manifest.read_manifest() or {}
    except Exception:
        return {}


def _trimming_is_active(surfaces: dict[str, Any]) -> bool:
    """True when this build actually has a surface allowlist.

    A freshly assembled manifest carries an empty allowlist because the build
    interview has not run yet. That is "not trimmed", not "nothing allowed" -
    reading it the other way would make a stock build claim it cannot do
    anything, which is a lie in the opposite direction.
    """
    if surfaces.get("derived_from_goal") or surfaces.get("owner_edited"):
        return True
    return bool(surfaces.get("allowlist"))


def _model_id(man: dict[str, Any]) -> Optional[str]:
    brain = man.get("brain") or {}
    chosen = brain.get("model")
    if chosen:
        return str(chosen)
    try:
        from puffpai.config import get_config
        return str(get_config().ollama.model)
    except Exception:
        return None


def can(capability: str,
        manifest: Optional[dict[str, Any]] = None) -> tuple[bool, str]:
    """Can this build do ``capability``, and if not, why - in plain words.

    ``capability`` is a page id from the taxonomy (``house.cameras``), or one
    of the behaviour names ``plan``, ``reach_wan``, ``reach_lan``,
    ``read_vault``.

    Returns ``(True, "")`` when available, else ``(False, reason)`` where the
    reason names the cause AND the way out of it. Never raises.

    **Never yes by default.** A build with no setup record, or one whose record
    could not be read, gets ``(False, <we cannot tell>)``: an honest unknown,
    not a claim. A page on the list is only a yes when something stands behind
    it (:func:`puffpai.build_abilities.page_row`): otherwise a space bot with
    a telescope page and nothing behind it says it "carries" it.
    """
    ok, reason, _cause = _assess(capability, manifest)
    return (ok, reason)


def unknown(capability: str,
            manifest: Optional[dict[str, Any]] = None) -> bool:
    """True when this build cannot tell whether it can do ``capability``.

    That is: no setup record, an unreadable one, a check that failed, or an
    ability whose state nothing can see. A caller that must not refuse on a
    guess reads this; a caller that must not CLAIM on a guess reads
    :func:`can`, which says no.
    """
    _ok, _reason, cause = _assess(capability, manifest)
    return cause in (CAUSE_NOT_CONFIGURED, CAUSE_UNKNOWN)


#: The causes the dispatch gate answers for, before any model is asked. Only
#: decisions the owner made at setup (a page left out, the internet shut): an
#: ability that is merely missing is the model's to explain, with the "things
#: you cannot do" lines in front of it, because the router's word match cannot
#: tell "tell me about this album" from "play this album".
_REFUSING_CAUSES = (CAUSE_SURFACE_NOT_LOADED, CAUSE_EGRESS_SEALED,
                    CAUSE_EGRESS_LAN_ONLY)


def refusal(capability: str,
            manifest: Optional[dict[str, Any]] = None) -> Optional[str]:
    """The sentence the dispatch gate says instead of asking the model, or None.

    None whenever the build CAN do it, cannot tell, or merely lacks the ability
    (see :data:`_REFUSING_CAUSES`). Never raises.
    """
    _ok, reason, cause = _assess(capability, manifest)
    return reason if cause in _REFUSING_CAUSES else None


def _assess(capability: str,
            manifest: Optional[dict[str, Any]]) -> tuple[bool, str, str]:
    """``(ok, reason, cause)``; cause is "" when ok. Never raises."""
    try:
        man = _manifest(manifest)
        if not man:
            return (False, why(CAUSE_NOT_CONFIGURED), CAUSE_NOT_CONFIGURED)
        return _can(capability, man)
    except Exception:
        return (False, why(CAUSE_UNKNOWN), CAUSE_UNKNOWN)


def _no(cause: str, **detail: Any) -> tuple[bool, str, str]:
    return (False, why(cause, **detail), cause)


_YES = (True, "", "")


def _can(capability: str, man: dict[str, Any]) -> tuple[bool, str, str]:
    cap = (capability or "").strip().lower()
    posture = man.get("posture") or {}
    egress = str(posture.get("run_egress") or "").lower()
    surfaces = man.get("surfaces") or {}
    allowlist = set(surfaces.get("allowlist") or [])

    # --- behaviour questions -------------------------------------------
    if cap == "plan":
        model = _model_id(man)
        if model:
            from puffpai.model_profile import supports
            if not supports(model, "planning"):
                return _no(CAUSE_NO_PLANNING, model=model)
        return _YES

    if cap == "reach_wan":
        if egress == "sealed":
            return _no(CAUSE_EGRESS_SEALED)
        if egress == "island":
            return _no(CAUSE_EGRESS_LAN_ONLY)
        return _YES

    if cap == "reach_lan":
        if egress == "sealed":
            return _no(CAUSE_EGRESS_SEALED)
        return _YES

    if cap == "read_vault":
        return _no(CAUSE_VAULT_SEALED)

    # --- page questions -------------------------------------------------
    if cap in _WAN_SURFACES and egress in ("sealed", "island"):
        return _no(CAUSE_EGRESS_SEALED if egress == "sealed"
                   else CAUSE_EGRESS_LAN_ONLY)

    if _trimming_is_active(surfaces) and cap and cap not in allowlist:
        if _is_known_surface(cap):
            return _no(CAUSE_SURFACE_NOT_LOADED, surface=cap,
                       goal=_build_goal(man))

    # A page on the list is not the ability behind it: only an app or a
    # working row makes it real.
    from puffpai import build_abilities as ba
    backing = ba.page_row(cap)
    if backing is None or backing.get("status") == ba.AVAILABLE:
        return _YES
    if backing.get("status") == ba.UNKNOWN:
        return _no(CAUSE_UNKNOWN, ability=backing)
    return _no(CAUSE_NOT_BUILT, ability=backing)


def _build_goal(man: dict[str, Any]) -> Optional[str]:
    """The owner's stated goal, if this build recorded one.

    Checked in order of specificity. The manifest does not always carry it -
    the surfaces block records the derivation, not the sentence - so callers
    must cope with ``None`` and say the generic thing instead of inventing a
    goal the owner never typed.
    """
    surfaces = man.get("surfaces") or {}
    goal = surfaces.get("goal")
    if goal:
        return str(goal)
    name = (man.get("build") or {}).get("name")
    return str(name) if name else None


def _surface_words(surface: Any) -> str:
    """A page id in the owner's words: ``house.cameras`` -> "security cameras".

    The ids are internal. What an owner reads is the taxonomy's own label.
    """
    sid = str(surface or "").strip()
    if not sid:
        return "that"
    try:
        from puffpai.build_interview import surface_taxonomy
        label = str((surface_taxonomy() or {}).get(sid, {}).get("label") or "")
    except Exception:
        label = ""
    return label.lower() if label else "that"


def _is_known_surface(surface: str) -> bool:
    """True when the id is a real surface in the shipped taxonomy."""
    try:
        from puffpai.build_interview import surface_taxonomy
        tax = surface_taxonomy() or {}
        return surface in tax
    except Exception:
        return False


def why(cause: str, **detail: Any) -> str:
    """The plain-words explanation for a cause. Fact plus a door, no apology."""
    surface = _surface_words(detail.get("surface"))
    if cause == CAUSE_SURFACE_NOT_LOADED:
        goal = detail.get("goal")
        because = (f" This build was set up for {goal}, so it was left "
                   f"out.") if goal else " It was left out of this build."
        return (f"This build does not include {surface}.{because} "
                f"You can add it by running setup again.")
    if cause == CAUSE_EGRESS_SEALED:
        return ("I cannot reach anything outside this computer. That was the "
                "choice at setup, and it is doing its job. Run puffpai setup "
                "again to let me use particular outside services.")
    if cause == CAUSE_EGRESS_LAN_ONLY:
        return ("I can reach things on your own home network but not the "
                "internet. Run puffpai setup again to let me use particular "
                "outside services.")
    if cause == CAUSE_NO_PLANNING:
        return ("I do one step at a time on this machine and I do not chain "
                "them together. Ask me for one thing and I will do it well. "
                "A bigger brain than this hardware fits would chain them.")
    if cause == CAUSE_VAULT_SEALED:
        return ("That is in your vault, so I need your PIN. Even I have to "
                "ask.")
    if cause == CAUSE_NO_CREW:
        return ("This machine runs the core team only, with no room for extra "
                "specialists. That is a size limit, not a setting.")
    if cause == CAUSE_CONFIG_DEGRADED:
        return ("Part of my setup could not be read, so I am working from a "
                "smaller built-in list and I am missing options I should have.")
    if cause == CAUSE_NOT_BUILT:
        ability = detail.get("ability") or {}
        from puffpai import build_abilities
        label = build_abilities.lower_first(
            str(ability.get("label") or "do that"))
        how = ability.get("how_to_add")
        tail = (f" To add it: {str(how).rstrip('.')}." if how else
                " It is not connected yet; it can be added later.")
        return f"I can't {label} in this build yet.{tail}"
    if cause == CAUSE_UNKNOWN:
        ability = detail.get("ability")
        if ability:
            from puffpai import build_abilities
            label = build_abilities.lower_first(str(ability.get("label") or ""))
            return (f"I can't tell right now whether I can {label}, so I "
                    f"won't say I can.")
        return ("I can't tell whether this build can do that, because part of "
                "my own setup record could not be read. Running puffpai setup "
                "again will fix the record.")
    if cause == CAUSE_NOT_CONFIGURED:
        return ("I have not been set up yet. Run setup and I will know what I "
                "am.")
    return "That is not something this build does."


#: A request to PLAY something, at the start of the message: "play my records",
#: "can you put on some jazz", "let's listen to my albums". Ability words
#: only (the verbs of playback), never a topic. "what should I play at a
#: party?" starts with a question word and is left to the model.
_PLAY_ASK = re.compile(
    r"(?ix)^\s*(?:(?:hey|ok|okay|please)[\s,]+)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+|let'?s\s+"
    r"|i\s+(?:want|would\s+like|'d\s+like|wanna)\s+(?:you\s+)?to\s+)?"
    r"(?P<verb>play|put\s+on|stream|start\s+playing|queue\s+up|listen\s+to"
    r"|watch)\b")

#: The pages that play things, by verb. Taxonomy pages, not topics.
_PLAY_PAGES = {"watch": ("media.video",)}
_PLAY_DEFAULT_PAGES = ("media.audio", "media.video")


def play_refusal(message: Any,
                 manifest: Optional[dict[str, Any]] = None) -> Optional[str]:
    """The honest answer to "play my records" when this build cannot play.

    Answered from the build record like "show me my telescope", never by the
    model: asked to play, a 7B model makes small talk about turntables or
    repeats its last answer. None when the message is not a play request, or
    when this build can play (a page an app backs, or a media server that is
    switched on). Never raises.
    """
    try:
        match = _PLAY_ASK.match(str(message or ""))
        if match is None:
            return None
        try:
            from puffpai.config import get_config
            if bool(get_config().media_server.enabled):
                return None  # the media fast path answers it
        except Exception:
            pass
        verb = " ".join(match.group("verb").lower().split())
        reasons = []
        for page in _PLAY_PAGES.get(verb, _PLAY_DEFAULT_PAGES):
            ok, reason = can(page, manifest)
            if ok:
                return None
            reasons.append(reason)
        return reasons[0] if reasons else None
    except Exception:
        return None


def explain_refusal(intent: str,
                    manifest: Optional[dict[str, Any]] = None) -> str:
    """What to say instead of pretending. Use this in place of a fake success.

    ``intent`` is a surface id or behaviour name. Always returns something
    sayable, even when the build turns out to be capable after all - in that
    case the caller had a different problem and should say so plainly.
    """
    ok, reason = can(intent, manifest)
    if not ok:
        return reason
    return ("I should be able to do that, and it did not work. That is a "
            "fault on my side, not something you did wrong.")


def limits_summary(manifest: Optional[dict[str, Any]] = None,
                   rows: Optional[list[dict[str, Any]]] = None, *,
                   for_chat: bool = False) -> list[str]:
    """The honest self-description: what this build is and is not.

    Returned as ASCII lines so the CLI, the first-run walkthrough, and the
    dashboard can all print it. Capability and limitation are stated together,
    never separately - a list of only limits reads as an apology and a list of
    only features reads as a brochure.

    What it can and cannot do comes from :mod:`puffpai.build_abilities`, which
    reads the RUNNING build (open outside services, installed apps, indexed
    libraries), not what the wizard wrote down, so it never claims web_fetch
    while web_fetch is shut.
    Plain words throughout: no door, tier or page ids.
    """
    man = _manifest(manifest)
    if not man:
        return [why(CAUSE_NOT_CONFIGURED)]

    from puffpai import build_abilities as ba
    if rows is None:
        rows = ba.abilities(manifest=man)
    if for_chat:
        return _chat_summary(man, rows)

    lines: list[str] = []
    model = _model_id(man)
    if model:
        lines.append(f"I run on this computer, using the {model} language "
                     f"model. I answer from what it learned before it was "
                     f"built, so I can be out of date.")
        try:
            from puffpai.model_profile import limits_sentence
            sentence = limits_sentence(model)
            if sentence:
                lines.append(sentence)
        except Exception:
            pass

    can_do = ba.available(rows)
    if can_do:
        lines.append("Things I can do now:")
        lines.extend(f"- {_line(ba.lower_first(r['label']))}" for r in can_do)

    lines.append(_reach_line(man, rows))

    cannot = ba.goal_order(ba.absent(rows), ba.goal_of(man))
    if cannot:
        lines.append("Things I can't do yet:")
        lines.extend(f"- {ba.absent_line(r, with_how=not for_chat)}"
                     for r in cannot)
        if for_chat and any(r.get("how_to_add") for r in cannot):
            # Commands stay out of a chat answer; the way to add is one ask away.
            lines.append("Ask me \"how do I add more to you?\" and I will tell "
                         "you how to switch any of these on.")

    try:
        from puffpai import config_health
        if config_health.is_degraded():
            lines.append(why(CAUSE_CONFIG_DEGRADED))
    except Exception:
        pass

    return [ln for ln in lines if ln]


#: The chat answer to "what can you do?" is read by whoever built the bot,
#: often a child: a handful of lines about THEIR goal, not the whole record
#: (not 11 can-do lines, 17 can't-do lines, a model id and a host name). The
#: full record stays on the terminal and the first-run page.
CHAT_CAN_LINES = 5
CHAT_CANNOT_LINES = 3

#: A host name in a row label ("use the outside website api.x.org").
_HOST_IN_LABEL = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)+\.[a-z]{2,}\b", re.I)


def _line(text: str) -> str:
    return text.rstrip(" .") + "."


def _chat_summary(man: dict[str, Any], rows: list[dict[str, Any]]) -> list[str]:
    """"What can you do?" for chat: short, about the goal, and consistent.

    At most :data:`CHAT_CAN_LINES` things it can do (the goal's first) and
    :data:`CHAT_CANNOT_LINES` things it can't that the goal asks about. A
    can't line that says the same thing as a can line is left out (it used to
    list a daily update under both). No model id, no host names, no commands.
    """
    from puffpai import build_abilities as ba
    goal = ba.goal_of(man) or str((man.get("surfaces") or {}).get("goal") or "")
    goal_words = {ba._stem(w) for w in ba.words(goal) if w not in ba._STOP}  # noqa: SLF001

    def shown(r: dict[str, Any]) -> bool:
        return not _HOST_IN_LABEL.search(str(r.get("label") or ""))

    lines = ["I run on this computer, and I learned most of what I know before "
             "I was built, so I can be out of date."]
    can_do = [r for r in ba.goal_order(ba.available(rows), goal) if shown(r)]
    if can_do:
        lines.append("Things I can do now:")
        lines.extend(f"- {_line(ba.lower_first(r['label']))}"
                     for r in can_do[:CHAT_CAN_LINES])
        if len(can_do) > CHAT_CAN_LINES:
            lines.append(f"- and {len(can_do) - CHAT_CAN_LINES} more small "
                         f"things.")

    can_terms = [ba.terms(r) for r in can_do]

    def about_goal(r: dict[str, Any]) -> bool:
        # The goal's own things, the pages the goal chose, something already
        # on its way (a book being learned), or a row sharing the goal's words.
        # With no goal on record, every row may speak (the order still holds).
        rid = str(r.get("id") or "")
        return (not goal_words or rid.startswith(("goal:", "page:"))
                or bool(r.get("note")) or bool(ba.terms(r) & goal_words))

    def said_already(r: dict[str, Any]) -> bool:
        mine = ba.terms(r)
        return any(len(mine & other) >= 2 for other in can_terms)

    cannot = [r for r in ba.goal_order(ba.absent(rows), goal)
              if about_goal(r) and shown(r) and not said_already(r)]
    # The goal's own things and the pages it chose lead: they are what this
    # owner asked for by name.
    cannot.sort(key=lambda r: 0 if str(r.get("id") or "").startswith("goal:")
                else 1 if str(r.get("id") or "").startswith("page:") else 2)
    if cannot:
        lines.append("Things I can't do yet:")
        lines.extend(f"- {ba.absent_line(r, with_how=False)}"
                     for r in cannot[:CHAT_CANNOT_LINES])
        lines.append("Ask me \"how do I add more to you?\" and I will tell "
                     "you how to add more.")
    lines.append(_chat_reach_line(man))
    return [ln for ln in lines if ln]


def _chat_reach_line(man: dict[str, Any]) -> str:
    posture = str((man.get("posture") or {}).get("run_egress") or "").lower()
    if posture == "sealed":
        return "Nothing leaves your computer without your consent."
    if posture == "island":
        return "I can reach your home network, but not the internet."
    return ("I only use the outside websites you said yes to. The What leaves "
            "your computer page lists every request I make.")


#: What leaves this computer for each outside service, in plain words.
_OUTSIDE_WORDS = {
    "outside:weather": "a weather website (it is sent your rough location)",
    "outside:air_quality": ("an air-quality website (it is sent your rough "
                            "location)"),
    "outside:news_rss": ("the news feeds you name (they learn this "
                         "computer's internet address)"),
}


def _reach_line(man: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """One sentence about what may leave this computer."""
    posture = str((man.get("posture") or {}).get("run_egress") or "").lower()
    if posture == "sealed":
        return "Nothing leaves your computer without your consent."
    if posture == "island":
        return "I can reach your home network, but not the internet."
    outside = [r for r in rows if str(r.get("id") or "").startswith("outside:")]
    if any(r.get("status") == "unknown" for r in outside):
        return "I can't tell right now which outside websites I may use."
    opened = [_OUTSIDE_WORDS.get(r["id"], _row_words(r)) for r in outside
              if r.get("status") == "available"]
    # Live cards the owner allowed reach their own website too.
    opened += [_card_words(r) for r in rows
               if str(r.get("id") or "").startswith("live:")
               and r.get("status") == "available"]
    if not opened:
        return "I don't use any outside websites right now."
    return ("Outside websites I may use: " + "; ".join(opened)
            + ". Everything else stays shut.")


def _card_words(r: dict[str, Any]) -> str:
    """"Show live iNaturalist on a card" -> "the website behind your
    iNaturalist card"."""
    import re
    label = str(r.get("label") or "")
    found = re.match(r"^Show live (.+?)(?: from .+)? on a card$", label)
    name = found.group(1) if found else label
    return f"the website behind your {name} card"


def _row_words(r: dict[str, Any]) -> str:
    from puffpai import build_abilities
    return build_abilities.lower_first(str(r.get("label") or ""))


#: "How do I add more to you?" in the shapes people ask it.
_HOW_TO_ADD = re.compile(
    r"(?ix)\b(?:"
    r"how\s+(?:do|can|could|would|should)\s+(?:i|we)\s+"
    r"(?:add|give|teach|put|load|get|install|upload)\b[^?]*\b(?:you|your|bot)\b"
    r"|how\s+(?:do|can)\s+you\s+learn"
    r"|teach\s+you\s+(?:more|new|about)"
    r"|(?:add|give)\s+(?:you\s+)?more\s+\w+(?:\s+\w+)?\s+to\s+you"
    r")")


def how_to_add_answer(user_message: Any,
                      rows: Optional[list[dict[str, Any]]] = None) -> Optional[str]:
    """The real ways to add to this build, or None when that was not asked.

    Asked "how do I add more space stuff to you?", a model may say "tell me
    about specific space topics", but talking to it adds nothing. The ways that
    work are commands; this says them.
    """
    if not _HOW_TO_ADD.search(str(user_message or "")):
        return None
    from puffpai import build_abilities as ba
    if rows is None:
        rows = ba.abilities()
    lines = ["Talking to me does not add anything I keep. These are the ways "
             "to add to me that work today:"]
    library = ba.get("knowledge_library", rows)
    if library and library.get("how_to_add"):
        lines.append("- More to look up (an offline library): "
                     + str(library["how_to_add"]).rstrip(".") + ".")
    for r in ba.absent(rows):
        if r.get("id") == "knowledge_library" or not r.get("how_to_add"):
            continue
        lines.append(f"- To {ba.lower_first(r['label'])}: "
                     + str(r["how_to_add"]).rstrip(".") + ".")
    missing = [ba.lower_first(r["label"]) for r in ba.absent(rows)
               if not r.get("how_to_add")
               and r.get("status") == ba.NOT_IN_THIS_BUILD]
    if missing:
        lines.append("Not possible in this build yet, whatever you add: "
                     + "; ".join(missing) + ".")
    return "\n".join(lines)


def workflows_answer(rich: bool = False) -> str:
    """What runs on its own on this build - read from config, never a brochure.

    The canned answer this replaces listed eight scheduled workflows (daily
    briefing, bill reminders, vault backup...) on a build that has none.
    """
    try:
        from puffpai.config import get_config
        n8n = get_config().n8n
        engine_on = bool(n8n.enabled)
        engine_url = str(n8n.url or "")
    except Exception:
        return ("I can't tell right now whether anything is set up to run on "
                "its own: my settings could not be read.")
    # Messages on a schedule, from whatever part of the build provides them
    # (build_abilities' provider hook); absent unless something says so.
    try:
        from puffpai import build_abilities as ba
        scheduled = ba.get("scheduled_messages")
    except Exception:
        scheduled = None
    own = ""
    if scheduled and scheduled.get("status") == "available":
        own = (f"This build can {ba.lower_first(scheduled['label'])}. ")
    if not engine_on:
        if own:
            return own + "Nothing else here runs on its own."
        return ("You don't have any workflows or automations on this build. "
                "Nothing here runs on its own on a schedule: no daily "
                "briefing, no reminders that ring, no automatic backups. I only "
                "do something when you ask me.")
    where = f" at {engine_url}" if engine_url else ""
    text = (own + "A workflow server (n8n) is connected to this build" + where
            + ". I can't see which workflows are switched on inside it, so "
            "check n8n itself for the list.")
    return text


def capabilities(manifest: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Machine-readable view: what is loaded, what is not, and why not."""
    man = _manifest(manifest)
    surfaces = man.get("surfaces") or {}
    try:
        from puffpai.build_interview import surface_taxonomy
        tax = list((surface_taxonomy() or {}).keys())
    except Exception:
        tax = []

    available: list[str] = []
    unavailable: dict[str, str] = {}
    for surface in tax:
        ok, reason = can(surface, man)
        if ok:
            available.append(surface)
        else:
            unavailable[surface] = reason

    return {
        "configured": bool(man),
        "trimmed": _trimming_is_active(surfaces),
        "available": sorted(available),
        "unavailable": unavailable,
        "can_plan": can("plan", man)[0],
        "can_reach_wan": can("reach_wan", man)[0],
        "summary": limits_summary(man),
    }


__all__ = [
    "can", "unknown", "refusal", "why", "explain_refusal", "limits_summary",
    "capabilities", "how_to_add_answer", "workflows_answer",
    "CAUSE_NOT_BUILT", "CAUSE_UNKNOWN",
    "CAUSE_NOT_CONFIGURED", "CAUSE_SURFACE_NOT_LOADED", "CAUSE_EGRESS_SEALED",
    "CAUSE_EGRESS_LAN_ONLY", "CAUSE_NO_PLANNING", "CAUSE_VAULT_SEALED",
    "CAUSE_NO_CREW", "CAUSE_CONFIG_DEGRADED",
]
