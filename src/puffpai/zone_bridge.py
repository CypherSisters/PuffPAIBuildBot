"""Zone 3, the Bridge - what this bot is allowed to reach, decided on purpose.

Step 6 of the wizard. The Bridge is a **policy** choice: three postures, plainly
described, one of which the owner picks. An owner with no firewall and no
managed network still walks this step - in fact they are the person it exists
for, because the bot's own fence is the only fence they have. The step is never
skipped on the grounds that there is nothing to configure.

Scope, stated so it stays true
------------------------------
This module is policy and nothing else. It does not scan a network, enumerate
hosts, resolve names, or classify devices, and it must never grow the ability
to. The device-classification cascade is withheld from the open base; the
posture step deliberately does not depend on it, because what a bot is *allowed*
to reach is a decision the owner makes, not a discovery the software performs.
The only inputs here are three booleans the owner answered out loud.

Two rules the reconciliation obeys
----------------------------------
* **The stricter of the two choices wins.** ``run_egress`` from Step 0 sets a
  floor; a posture below that floor is raised to it. Nothing in here can lower a
  posture the owner chose.
* **The ordering lives in code.** ``config/bridge_postures.json`` carries the
  wording. The strictness ranking that the math uses is a constant below, so a
  hand-edited config file can change how a posture reads but never how strict it
  is.

What is enforced
------------------------------------------------------------------
The choice is read back at run time by
:func:`puffpai.security.bridge_guard.reach_level`, and three places
act on it. Nothing here scans or resolves anything; the acting is done by the
code that already sits at each exit:

* ``open_yard`` and ``fenced`` (level ``open``): the home network is allowed,
  and BuildBot's own finding and adding tools are on as built, each with a
  one-tap off. The two work the same today; ``open_yard`` loosens nothing.
* ``drawbridge_up`` (level ``switched_on``): the bot reaches nothing on the
  home network or the internet except the services the owner switched on.
  :func:`puffpai.security.outside_switches.is_off` reads a service with no
  switch as OFF instead of "as built";
  :mod:`puffpai.security.lan_posture` keeps the brain and the books on this
  computer and allows a home service only while it is switched on (Home
  Assistant, n8n, the media server); :mod:`puffpai.security.bridge_guard`
  refuses, at the moment a connection is made, anything else leaving this
  computer.
* a build whose ``run_egress`` is ``sealed`` (level ``sealed``): nothing
  leaves this computer at all, switched on or not.

A record that cannot be read, or a Bridge value nobody knows, is read as
``drawbridge_up``: when the choice is unclear, the stricter rule applies.

All strings are ASCII: they reach a Windows console.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

# Strictness ordering. Policy, not copy - deliberately not read from the JSON,
# so editing the shipped file cannot loosen anything.
_STRICTNESS: dict[str, int] = {
    "open_yard": 0,
    "fenced": 1,
    "drawbridge_up": 2,
}

DEFAULT_POSTURE = "fenced"

# Retired postures and what they now mean. "moat" promised to ask before every
# outside visit; nothing ever enforced that, and it cannot work with daily
# updates and live cards that refresh on their own, so it is dropped.
# It folds into "fenced": each outside service asks once, when it is
# added, and can be switched off at any time. A record that still says "moat"
# is read as "fenced", with MOAT_FOLDED_NOTE.
RETIRED_POSTURES: dict[str, str] = {"moat": "fenced"}

MOAT_FOLDED_NOTE = (
    "The stricter asking setting is no longer offered, because asking before "
    "every outside visit was never built. Your bot now uses \"Ask when it "
    "reaches further\": apart from the tools your plan named, each outside "
    "service asks you once before it is switched on, and you can switch any "
    "of them off at any time."
)

# Never handed out as a recommendation. Open Yard is a legitimate choice, but it
# is the one posture that has to be chosen deliberately rather than accepted
# because the wizard suggested it.
_NEVER_RECOMMENDED = frozenset({"open_yard"})

# run_egress (Step 0) sets the floor a posture cannot sit below. An unrecognised
# value falls back to the safe default rather than to the loosest option.
_EGRESS_FLOOR: dict[str, str] = {
    "sealed": "drawbridge_up",
    "island": "fenced",
    "enumerated": "open_yard",
}
_UNKNOWN_EGRESS_FLOOR = DEFAULT_POSTURE

_CHOSEN_BY = frozenset({"owner", "default", "override"})

# Baked-in fallback so the Bridge step still runs if the shipped file is missing
# or unreadable. Same three postures, same ids, shortened wording; the full copy
# lives in config/bridge_postures.json and is what maintainers edit.
_FALLBACK_POSTURES: dict[str, Any] = {
    "schema": "bridge_postures/v1",
    "default": DEFAULT_POSTURE,
    "postures": [
        {
            "id": "open_yard",
            "label": "Keep what you said yes to",
            "strictness": 0,
            "recommendable": False,
            "summary": "Today this works the same as \"Ask when it reaches further\". Nothing extra is switched off, and nothing is loosened either.",
            "allows": {
                "lan_devices": "Things on your home network are allowed.",
                "actions": "The same checks as \"Ask when it reaches further\".",
                "egress": "The outside services you switched on stay on. Each new one still asks you once.",
                "consent": "Asked once for each outside service, when you switch it on."
            },
            "costs": "No extra protection over \"Ask when it reaches further\". Worth choosing on purpose, never by accident.",
            "matches_run_egress": "enumerated"
        },
        {
            "id": "fenced",
            "label": "Ask when it reaches further",
            "strictness": 1,
            "recommendable": True,
            "summary": "Your bot can talk to things in your house. BuildBot's own tools for finding and adding things are on while your bot is made; after that, only the ones your plan named stay on. Every other outside service asks you once before it is switched on, and you can switch any of them off at any time.",
            "allows": {
                "lan_devices": "Things on your home network are allowed.",
                "actions": "The same checks as always: each outside service has its own switch.",
                "egress": "Outside services are listed one by one on \"What leaves your computer\". BuildBot's own tools for finding and adding things are on while your bot is made, and only the ones your plan named stay on after it; everything else starts off. Any of them can be switched off at any time.",
                "consent": "For BuildBot's own tools: your press of Start looking and your yes to the plan. For every other outside service: asked once, when you switch it on."
            },
            "costs": "You will be asked to confirm now and then. That is the trade for a bot that is useful in the house without being loose about the world outside it.",
            "matches_run_egress": "island"
        },
        {
            "id": "drawbridge_up",
            "label": "Only what you switched on",
            "strictness": 2,
            "recommendable": True,
            "summary": "Your bot connects to nothing on your home network or the internet, except the services you switched on yourself. Home Assistant counts only if you switched it on. Looking up a website's address still asks your network; the Bridge does not stop that.",
            "allows": {
                "lan_devices": "Only the home services you switched on, like Home Assistant. The brain and the books must be on this computer.",
                "actions": "Anything that would reach past what you switched on is stopped, and the bot says so.",
                "egress": "Only the outside services you switched on. Even BuildBot's own tools for finding and adding things stay off until you switch them on.",
                "consent": "Asked once for each service, when you switch it on."
            },
            "costs": "Anything that needs a service you have not switched on is off the table, and the bot will tell you which things those are.",
            "matches_run_egress": "sealed"
        }
    ]
}


def _postures_path() -> str:
    """Absolute path of the shipped posture descriptions."""
    from puffpai.config import get_config
    return os.path.join(get_config().REPO_ROOT, "config", "bridge_postures.json")


def load_postures(path: Optional[str] = None) -> dict[str, Any]:
    """Load the posture copy, degrading to the baked-in fallback on any failure.

    A file that does not describe all three postures is treated as unusable: the
    Bridge step offers the full ladder or it offers the trustworthy fallback,
    never a partial menu that quietly hides the strict end of it.
    """
    target = path or _postures_path()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("postures"), list):
            ids = {p.get("id") for p in data["postures"] if isinstance(p, dict)}
            if ids == set(_STRICTNESS):
                return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return _FALLBACK_POSTURES


def posture_menu(postures: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """The three postures, loosest first, each with its code-owned strictness."""
    data = postures or load_postures()
    menu: list[dict[str, Any]] = []
    for entry in data.get("postures") or []:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("id", ""))
        if pid not in _STRICTNESS:
            continue
        item = dict(entry)
        # Strictness is ours, not the file's.
        item["strictness"] = _STRICTNESS[pid]
        item["recommendable"] = pid not in _NEVER_RECOMMENDED
        menu.append(item)
    menu.sort(key=lambda item: item["strictness"])
    return menu


def posture_by_id(posture: str, postures: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    """Return one posture's entry, or None if the id is not one of the three."""
    for entry in posture_menu(postures):
        if entry.get("id") == posture:
            return entry
    return None


# ---------------------------------------------------------------------------
# recommendation
# ---------------------------------------------------------------------------

def posture_for_environment(
    has_home_assistant: bool,
    has_external_firewall: bool,
    has_managed_lan: bool,
    postures: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Suggest one posture for what the owner says they have, and show all three.

    The three inputs are answers the owner gave, not anything this module found
    out. The result is a suggestion the wizard displays next to the full menu; it
    is never applied on its own, which is why the returned dict always carries
    the whole ladder and says so in ``note``.

    Open Yard is never the suggestion. Someone who wants it can pick it, but the
    wizard does not talk anybody into the one posture that removes the second
    chance to say no. Never raises.
    """
    home_assistant = bool(has_home_assistant)
    firewall = bool(has_external_firewall)
    managed_lan = bool(has_managed_lan)

    if home_assistant:
        recommended = "fenced"
        reason = (
            "You have Home Assistant, so your bot needs to reach devices in the "
            "house. Asking when it reaches further lets it do that while still "
            "checking anything that goes past what you allowed."
        )
    elif managed_lan or firewall:
        gear = "a managed network" if managed_lan else "a firewall of your own"
        recommended = "fenced"
        reason = (
            f"You already run {gear}, which handles the edge of your network. "
            f"Asking when it reaches further covers the part that gear does not: "
            f"what your bot itself is allowed to do once it is inside."
        )
    else:
        recommended = "fenced"
        reason = (
            "There is no firewall and no managed network in front of this "
            "computer, so your bot's own rules are the only rules. Asking when "
            "it reaches further keeps every outside service off until you say "
            "yes to it, and you can switch any of them off at any time."
        )

    return {
        "recommended": recommended,
        "reason": reason,
        "default": DEFAULT_POSTURE,
        "never_recommended": sorted(_NEVER_RECOMMENDED),
        "environment": {
            "home_assistant": home_assistant,
            "external_firewall": firewall,
            "managed_lan": managed_lan,
        },
        "postures": posture_menu(postures),
        "note": (
            "This is a suggestion, not a setting. The Bridge is not configured "
            "until you pick one of the three yourself."
        ),
    }


# ---------------------------------------------------------------------------
# reconciliation with Step 0's run_egress
# ---------------------------------------------------------------------------

def reconcile_with_egress(posture: str, run_egress: str) -> tuple[str, str]:
    """Reconcile a Bridge posture with the build's ``run_egress``. Stricter wins.

    A sealed build forces Drawbridge Up; an island build will not sit behind Open
    Yard. Returns ``(posture, explanation)`` where the explanation is always a
    plain sentence - describing the override when there was one, and confirming
    the choice when there was not, so the wizard can print it either way.

    Fails closed twice over: an unrecognised posture becomes the default rather
    than the loosest option, and an unrecognised ``run_egress`` applies the
    default's floor rather than no floor at all. Never loosens, ever.
    """
    chosen = str(posture or "").strip().lower()
    egress = str(run_egress or "").strip().lower()

    folded = chosen in RETIRED_POSTURES
    if folded:
        chosen = RETIRED_POSTURES[chosen]
    known_posture = chosen in _STRICTNESS
    if not known_posture:
        chosen = DEFAULT_POSTURE

    floor = _EGRESS_FLOOR.get(egress, _UNKNOWN_EGRESS_FLOOR)
    result = chosen if _STRICTNESS[chosen] >= _STRICTNESS[floor] else floor

    notes: list[str] = []
    if folded:
        notes.append(MOAT_FOLDED_NOTE)
    if not known_posture:
        notes.append(
            f"We did not recognise that asking setting, so we used "
            f"\"{_label(DEFAULT_POSTURE)}\", the safe default. Pick one on "
            f"purpose before you finish."
        )
    if egress not in _EGRESS_FLOOR:
        notes.append(
            "We did not recognise what the bot may reach while it runs, so we "
            "kept the safe default rather than assuming the loose one."
        )

    if result != chosen:
        if egress == "sealed":
            notes.append(
                f"You chose a bot that reaches nothing outside this computer, "
                f"so this is set to \"{_label('drawbridge_up')}\". The stricter "
                f"of your two choices always wins."
            )
        else:
            notes.append(
                f"\"{_label(chosen)}\" would allow more than the rest of your "
                f"answers do, so this is set to \"{_label(result)}\" instead. "
                f"The stricter of your two choices always wins."
            )
    elif known_posture and egress in _EGRESS_FLOOR and not folded:
        notes.append(
            f"\"{_label(result)}\" fits the rest of your answers, so nothing "
            f"was changed."
        )

    return result, " ".join(notes)


def _label(posture: str) -> str:
    """Display label for a posture id, falling back to the id itself."""
    entry = posture_by_id(posture)
    if entry and entry.get("label"):
        return str(entry["label"])
    return posture


# ---------------------------------------------------------------------------
# manifest block
# ---------------------------------------------------------------------------

def bridge_manifest_block(
    posture: str,
    *,
    run_egress: Optional[str] = None,
    environment: Optional[dict[str, Any]] = None,
    chosen_by: str = "owner",
    configured_at: Optional[float] = None,
    postures: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Record the Bridge policy for the manifest.

    When ``run_egress`` is given the posture is reconciled first, so a manifest
    can never record a Bridge looser than the build's egress tier allows - the
    record is the reconciled truth, not what was asked for.

    Carries no key that could be read back as a switch on the contracts (the
    vault, the consent gate, the action gates, zero telemetry). It survives
    :func:`puffpai.build_manifest.sanitize_manifest` unchanged. Never raises.
    """
    requested = str(posture or "").strip().lower()
    if run_egress is not None:
        final, note = reconcile_with_egress(requested, str(run_egress))
    elif requested in RETIRED_POSTURES:
        final, note = RETIRED_POSTURES[requested], MOAT_FOLDED_NOTE
    elif requested in _STRICTNESS:
        final, note = requested, ""
    else:
        final = DEFAULT_POSTURE
        note = (
            f"We did not recognise that Bridge posture, so we recorded "
            f"{_label(DEFAULT_POSTURE)}, the safe default."
        )

    entry = posture_by_id(final, postures) or {}
    env = environment or {}
    source = str(chosen_by).strip().lower()
    if source not in _CHOSEN_BY:
        source = "owner"
    if requested != final:
        source = "override"

    return {
        "posture": final,
        "label": entry.get("label", final),
        "summary": entry.get("summary", ""),
        "allows": dict(entry.get("allows") or {}),
        "costs": entry.get("costs", ""),
        "strictness": _STRICTNESS[final],
        "run_egress": str(run_egress).strip().lower() if run_egress is not None else None,
        "requested_posture": requested or None,
        "reconciled": requested != final,
        "note": note,
        "environment": {
            "home_assistant": bool(env.get("home_assistant")),
            "external_firewall": bool(env.get("external_firewall")),
            "managed_lan": bool(env.get("managed_lan")),
        },
        "chosen_by": source,
        "enforced_by": "Drawbridge",
        "configured_at": configured_at if configured_at is not None else time.time(),
    }


def migrate_record(block: Any) -> dict[str, Any]:
    """An older Bridge record, read as today's. Never raises, never loosens.

    A record that still says ``moat`` becomes ``fenced`` -- the same strictness
    it actually had, since asking before every outside visit was never built
    -- with :data:`MOAT_FOLDED_NOTE` as its note and today's wording. Every
    other record is returned unchanged (as a copy); :func:`current_words`
    is what a page shows.
    """
    out = dict(block) if isinstance(block, dict) else {}
    old = str(out.get("posture") or "").strip().lower()
    if old not in RETIRED_POSTURES:
        return out
    new = RETIRED_POSTURES[old]
    entry = posture_by_id(new) or {}
    out.update({
        "posture": new,
        "label": entry.get("label", new),
        "summary": entry.get("summary", ""),
        "allows": dict(entry.get("allows") or {}),
        "costs": entry.get("costs", ""),
        "strictness": _STRICTNESS[new],
        "note": MOAT_FOLDED_NOTE,
        "migrated_from": old,
    })
    return out


def current_words(block: Any) -> dict[str, Any]:
    """A Bridge record with TODAY's words for its posture. Never raises.

    A record written before the Bridge was enforced carries sentences that
    promised more than the code did ("Nothing leaves this computer, ever" for
    a choice that allows the services you switched on). A page must never
    show those again, so it shows these. An old ``moat`` record is migrated
    first; an unknown posture is returned as it was (a copy).
    """
    out = migrate_record(block)
    pid = str(out.get("posture") or "").strip().lower()
    if pid in _STRICTNESS:
        entry = posture_by_id(pid) or {}
        out.update({
            "posture": pid,
            "label": entry.get("label", pid),
            "summary": entry.get("summary", ""),
            "allows": dict(entry.get("allows") or {}),
            "costs": entry.get("costs", ""),
            "strictness": _STRICTNESS[pid],
        })
    return out


# ---------------------------------------------------------------------------
# display
# ---------------------------------------------------------------------------

def posture_summary_lines(recommendation: dict[str, Any]) -> list[str]:
    """Plain-language lines describing the Bridge menu, for the CLI/API."""
    lines: list[str] = []
    rec = recommendation.get("recommended")
    for entry in recommendation.get("postures") or []:
        mark = " <- suggested" if entry.get("id") == rec else ""
        lines.append(f"  - {entry.get('label')}{mark}: {entry.get('summary')}")
        lines.append(f"      Costs you: {entry.get('costs')}")
    if rec:
        lines.append(f"Suggested: {_label(str(rec))}")
        lines.append(f"  {recommendation.get('reason', '')}")
    if recommendation.get("note"):
        lines.append(f"  {recommendation['note']}")
    return lines


# ---------------------------------------------------------------------------
# enforcement: what the running bot may actually reach
# ---------------------------------------------------------------------------

#: The home network is allowed; BuildBot's own tools are on as built.
LEVEL_OPEN = "open"
#: Drawbridge up: only the services the owner switched on.
LEVEL_SWITCHED_ON = "switched_on"
#: run_egress sealed: nothing leaves this computer, switched on or not.
LEVEL_SEALED = "sealed"

#: A service with no row of its own on "What leaves your computer" that is
#: used only as part of another one. With the Bridge up it follows that
#: row's switch: the locked box's setup and its code parts are downloads of
#: code the owner picked, so they wait for "Downloading an app or code you
#: picked" to be switched on.
SWITCH_FOLLOWS: dict[str, str] = {"box_install": "code_download"}

#: What the Bridge does not hold (security.bridge_guard, "What it does not
#: do"): a name lookup asks the resolver before any connection is judged, and
#: a UDP datagram sent without connecting is not seen. Said wherever a line
#: says what the bot connects to, so no line claims more than is enforced.
NAME_LOOKUP_LINE = ("Looking up a website's address still asks your network; "
                    "the Bridge does not stop that.")

#: One plain sentence per level, for the pages and the terminal. Only what is
#: enforced: connections (security.bridge_guard), not name lookups.
ENFORCED_LINES: dict[str, str] = {
    LEVEL_OPEN: ("Your bot may reach things on your home network. Each outside "
                 "service has its own switch on \"What leaves your computer\"."),
    LEVEL_SWITCHED_ON: ("The Bridge is up: your bot connects to nothing on "
                        "your home network or the internet, except the "
                        "services you switched on. Home Assistant counts only "
                        "if you switched it on. " + NAME_LOOKUP_LINE),
    LEVEL_SEALED: ("Your bot keeps everything on this computer: it connects to "
                   "nothing on your home network or the internet. "
                   + NAME_LOOKUP_LINE),
}

#: What the owner reads when the Bridge stopped something.
REFUSED_LINE = ("The Bridge is up, and this was not switched on, so nothing "
                "was sent.")
SEALED_LINE = ("Your bot is set to keep everything on this computer, so "
               "nothing was sent.")

def _run_egress_of(block: Any) -> str:
    """``run_egress`` from a manifest's ``posture`` block, normalised the way
    :func:`puffpai.build_posture.assess_posture` does: an unknown value is the
    ordinary default (``island``), never a looser one."""
    raw = str((block or {}).get("run_egress") or "").strip().lower()         if isinstance(block, dict) else ""
    return raw if raw in _EGRESS_FLOOR else "island"


def enforced_from_manifest(manifest: Any) -> dict[str, Any]:
    """The Bridge as the running bot enforces it, from a manifest dict.

    Pure: reads nothing, opens nothing. ``None`` (no manifest yet: no wizard
    has run) reads as the default. A manifest that is not a dict, or a Bridge
    value nobody knows, reads as ``drawbridge_up``. The run setting's floor
    then applies: a sealed build is never looser than ``drawbridge_up``, and
    its level is ``sealed``. The cached reading from disk that the running
    bot uses is :func:`puffpai.security.bridge_guard.enforced`.
    """
    posture_block: Any = {}
    if manifest is None:
        chosen, source = DEFAULT_POSTURE, "default"
    elif not isinstance(manifest, dict):
        chosen, source = "drawbridge_up", "unreadable"
    else:
        bridge = manifest.get("bridge")
        posture_block = manifest.get("posture")
        source = "record"
        if bridge is None:
            chosen, source = DEFAULT_POSTURE, "default"
        elif not isinstance(bridge, dict):
            chosen = "drawbridge_up"
        else:
            raw = str(bridge.get("posture") or "").strip().lower()
            if not raw:
                chosen, source = DEFAULT_POSTURE, "default"
            elif raw in RETIRED_POSTURES:
                chosen = RETIRED_POSTURES[raw]
            elif raw in _STRICTNESS:
                chosen = raw
            else:
                chosen = "drawbridge_up"
    run_egress = _run_egress_of(posture_block)
    floor = _EGRESS_FLOOR.get(run_egress, _UNKNOWN_EGRESS_FLOOR)
    final = chosen if _STRICTNESS[chosen] >= _STRICTNESS[floor] else floor
    if run_egress == "sealed":
        level = LEVEL_SEALED
    elif final == "drawbridge_up":
        level = LEVEL_SWITCHED_ON
    else:
        level = LEVEL_OPEN
    return {"posture": final, "chosen": chosen, "run_egress": run_egress,
            "level": level, "source": source, "line": ENFORCED_LINES[level]}


def switch_allows_at(level: str, sid: str, get_switch) -> bool:
    """Does the Bridge at *level* let *sid* be used, given the switches?

    *get_switch* answers True / False / None (never set) for a service id,
    as :func:`puffpai.security.outside_switches.get` does. Level ``open``:
    anything not switched off. ``switched_on``: only an explicit ON (or the
    ON of the row it follows, :data:`SWITCH_FOLLOWS`). ``sealed``: nothing.
    An unknown level is read as ``switched_on``.
    """
    got = get_switch(sid)
    if got is False or level == LEVEL_SEALED:
        return False
    if level == LEVEL_OPEN:
        return True
    if got is True:
        return True
    follow = SWITCH_FOLLOWS.get(str(sid or "").strip().lower())
    return bool(follow) and get_switch(follow) is True


__all__ = [
    "DEFAULT_POSTURE",
    "ENFORCED_LINES",
    "LEVEL_OPEN",
    "LEVEL_SEALED",
    "LEVEL_SWITCHED_ON",
    "MOAT_FOLDED_NOTE",
    "REFUSED_LINE",
    "RETIRED_POSTURES",
    "SEALED_LINE",
    "SWITCH_FOLLOWS",
    "bridge_manifest_block",
    "current_words",
    "enforced_from_manifest",
    "switch_allows_at",
    "load_postures",
    "migrate_record",
    "posture_by_id",
    "posture_for_environment",
    "posture_menu",
    "posture_summary_lines",
    "reconcile_with_egress",
]
