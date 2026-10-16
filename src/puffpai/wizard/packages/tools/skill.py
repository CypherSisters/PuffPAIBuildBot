"""Tools: what this bot can reach for, and what it cannot.

**Tools v1**: the
integrations question today's step asks, plus a read-only declared-vs-available
list. No keyed API can be turned on here, because this build has nowhere safe
to put a key; one is recorded as ``declared, no key path yet`` and gapped.

Where the two halves come from:

* **The question.** The same Home Assistant question, with the same gates, the
  same posture check at the point of capture, and the same write through
  ``setup_ops.set_ha_config``. The token is a ``secret_entry``: it has no value
  on the card, the runner logs ``cards.redact_answers``, and the only place it
  goes is the config writer that already owned it.
* **The list.** Tool needs come from the ``sources`` section if another step has put
  them there, and otherwise from the goal -- the catalog ids
  ``build_interview.propose`` already matched, which is how the goal reaches the
  capability catalog today. Each need is then matched against what this build
  actually ships: an app folder on disk, a configured integration, or nothing.

The statuses are the spec's four and nothing else, and every one of them is
decided from the catalog row plus the state of this machine, never from a
package's claim about itself.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Optional

#: The address an owner may type for a hub. Host or host:port, optional scheme
#: and path-less; ``setup_ops.set_ha_config`` does the real parsing.
URL_PATTERN = r"^(?:https?://)?[A-Za-z0-9._-]{1,120}(?::\d{1,5})?/?$"

DEFAULT_HA_URL = "http://homeassistant.local:8123"

#: The four honest statuses (spec C.5). A row is in exactly one of them.
AVAILABLE = "available"
DOOR_SHUT = "needs something you have not switched on"
NOTHING = "nothing here does this"
NO_KEY = "needs a key this build cannot keep safely yet"

#: What an integration row needs before it can work **on this build**, read off
#: the code that implements it rather than off the catalog's prose:
#:
#: * ``home_assistant`` -- a URL and a long-lived token, and this wizard step is
#:   the path that stores them (``setup_ops.set_ha_config``).
#: * ``weather`` -- the dashboard's weather card and chat read the built-in
#:   weather service (Open-Meteo, no key) with the town-level home location
#:   (``puffpai.weather_service``). It needs that one service switched on and
#:   nothing else: NOT Home Assistant (most people
#:   do not have Home Assistant).
#: * ``media`` -- ``media_bridge`` authenticates with
#:   ``cfg.media_server.jellyfin_api_key``, and nothing in this build asks for
#:   one or has anywhere to keep it. That is the keyed case, stated plainly.
INTEGRATION_NEEDS: dict[str, dict[str, str]] = {
    "integration:home_assistant": {
        "door": "the hub's web address and its key",
        "needs": "hub",
        "without": "it will say it cannot reach the house rather than "
                   "pretending it did",
    },
    "integration:weather": {
        "door": "the weather forecast switched on (on the What leaves your "
                "computer page)",
        "needs": "service",
        "service": "weather",
        "without": "the weather card says it is switched off and how to "
                   "switch it on",
    },
    "integration:media": {
        "door": "a key for your media server",
        "needs": "key",
        "without": "it will say it has no media server rather than guessing",
    },
}

MAX_ROWS = 20


# -- the declared-vs-available list -------------------------------------------

def _declared(ctx) -> list[dict]:
    """The tool needs this build declared: from ``sources`` if it has them.

    ``sources`` is the skill that owns the goal-to-needs step (C.4). Until it
    writes one, the needs are the catalog ids the goal already matched, which
    is the same list by a shorter route -- and it is read, never invented.
    """
    try:
        section = ctx.read("sources")
    except Exception:  # noqa: BLE001 - an undeclared read is the runner's call
        section = {}
    needs = section.get("tool_needs")
    if isinstance(needs, list) and needs:
        return [{"id": str(row.get("id") or row), "label": str(
            row.get("label") if isinstance(row, dict) else row)}
            for row in needs[:MAX_ROWS] if row]

    try:
        persona = ctx.read("persona")
    except Exception:  # noqa: BLE001
        persona = {}
    interview = persona.get("interview")
    if not isinstance(interview, dict):
        interview = ctx.shared.get("interview") or {}
    proposal = interview.get("proposal") if isinstance(interview, dict) else None
    flat = (proposal or {}).get("flat") if isinstance(proposal, dict) else None
    out: list[dict] = []
    for entry in flat or ():
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") not in ("integration", "app"):
            continue
        out.append({"id": str(entry.get("id") or ""),
                    "label": str(entry.get("label") or entry.get("id") or "")})
    return out[:MAX_ROWS]


def _installed_apps() -> set[str]:
    try:
        from puffpai import app_registry
        # A first app that is off counts: the plan's Yes turns it on.
        return {str(m.get("id") or "") for m in app_registry.load_manifests(include_off=True)}
    except Exception:  # noqa: BLE001 - no app scan is "nothing here does this"
        return set()


def _row_status(need: dict, *, ha_configured: bool, apps: set[str],
                services_on: Optional[set[str]] = None) -> dict:
    """One row: what it does, its status, its door, and what is said without it."""
    tool_id = need["id"]
    label = need["label"] or tool_id
    if tool_id.startswith("app:"):
        app_id = tool_id.split(":", 1)[1]
        if app_id in apps:
            return {"id": tool_id, "label": label, "status": AVAILABLE,
                    "door": "", "without": ""}
        return {"id": tool_id, "label": label, "status": NOTHING,
                "door": "",
                "without": "it will say this build does not carry that app"}

    spec = INTEGRATION_NEEDS.get(tool_id)
    if spec is None:
        return {"id": tool_id, "label": label, "status": NOTHING, "door": "",
                "without": "nothing in this build does this yet"}
    if spec["needs"] == "key":
        return {"id": tool_id, "label": label, "status": NO_KEY,
                "door": spec["door"], "without": spec["without"]}
    if spec["needs"] == "service":
        # A built-in outside service: on when the owner said yes to it (on the
        # summary or its switch), whatever any hub says.
        if spec.get("service") in (services_on or ()):
            return {"id": tool_id, "label": label, "status": AVAILABLE,
                    "door": spec["door"], "without": ""}
        return {"id": tool_id, "label": label, "status": DOOR_SHUT,
                "door": spec["door"], "without": spec["without"]}
    if ha_configured:
        return {"id": tool_id, "label": label, "status": AVAILABLE,
                "door": spec["door"], "without": ""}
    return {"id": tool_id, "label": label, "status": DOOR_SHUT,
            "door": spec["door"], "without": spec["without"]}


def _services_on(ctx) -> set[str]:
    """Built-in outside services said yes to: on the summary, or already on."""
    out: set[str] = set()
    try:
        from puffpai.wizard import proposal
        out |= set(proposal.approved_refs(
            (getattr(ctx, "shared", None) or {}).get("summary"), "door"))
    except Exception:  # noqa: BLE001 - no summary is "nothing said yes to"
        pass
    try:
        from puffpai.security import egress_doors
        out |= {d.id for d in egress_doors.DOORS
                if egress_doors.egress_allowed(d.id)[0]}
    except Exception:  # noqa: BLE001
        pass
    return out


def _rows(ctx, *, ha_configured: bool) -> list[dict]:
    apps = _installed_apps()
    services = _services_on(ctx)
    return [_row_status(need, ha_configured=ha_configured, apps=apps,
                        services_on=services)
            for need in _declared(ctx) if need["id"]]


def _sentence(text: str) -> str:
    text = str(text or "").strip()
    return (text[:1].upper() + text[1:] + ("" if text.endswith(".") else ".")
            if text else "")


def _fix_for(row: dict) -> dict:
    """The one command that fixes a row: its service's switch, else setup."""
    service = (INTEGRATION_NEEDS.get(row.get("id") or "") or {}).get("service")
    if service:
        return {"kind": "command", "id": "outside.on",
                "args": {"service": service}}
    return {"kind": "command", "id": "setup.rerun", "args": {}}


def _gaps(rows: list[dict]) -> list[dict]:
    gaps = []
    for row in rows:
        if row["status"] == AVAILABLE:
            continue
        gaps.append({
            "skill": "tools",
            "what": f"Your bot cannot reach {row['label']}.",
            "why": f"It {row['status']}"
                   + (f": {row['door']}" if row["door"] else "") + ".",
            "impact": _sentence(row["without"] or "that part of the goal has "
                                                  "nothing behind it"),
            "fix": _fix_for(row),
        })
    return gaps[:MAX_ROWS]


# -- the card -----------------------------------------------------------------

def _remote(ctx) -> bool:
    """True when a browser is drawing this card.

    ``non_interactive`` means two different things and they had been conflated:
    ``--yes`` (nobody is there to answer) and an HTTP request (somebody is
    there, in a browser, and answers come back in the next request). The hub
    question is asked in both of the latter's renderers -- the dashboard's own
    onboarding already takes a Home Assistant URL and token on its Smart home
    step (``POST /setup/ha``) -- so refusing to ask it here was not a safety
    property, it was the reason the card never asked and the API's secret
    attempt limiter guarded nothing.
    """
    return bool(getattr(ctx, "remote", False))


def _hub_requested(ctx) -> bool:
    """Did the owner already say yes to a hub -- on this card, or on the
    summary? (the propose rule)"""
    from puffpai.wizard import proposal

    if ctx.applied("hub_requested", default=False):
        return True
    return bool(proposal.approved_refs(
        (getattr(ctx, "shared", None) or {}).get("summary"), "home_assistant"))


def _offers_hub(ctx) -> bool:
    """Whether the hub question is asked at all, by today's rules."""
    gates = ctx.shared.get("gates") or {}
    if not gates.get("may_offer_lan_integrations", True):
        return False
    if getattr(ctx.args, "skip_ha", False):
        return False
    if getattr(ctx.args, "ha_url", None) and getattr(ctx.args, "ha_token", None):
        return False
    if ctx.applied("home_assistant_configured", default=False):
        return False
    return (not ctx.non_interactive) or _remote(ctx)


def propose(ctx) -> dict:
    """Ask the integrations question and list declared against available."""
    from puffpai import cli

    cli.enter_step("Integrations")
    sealed = not (ctx.shared.get("gates") or {}).get(
        "may_offer_lan_integrations", True)
    rows = _rows(ctx, ha_configured=False)
    offer = _offers_hub(ctx)
    # Being *asked* whether to connect a hub reaches nothing. What reaches the
    # network while the bot runs is a hub this card has actually been told to
    # connect -- the owner's yes on the toggle, or a URL and a token passed on
    # the command line. A fresh build where nothing was declared and no hub was
    # asked for was flagged "turns on internet access while it runs", which is
    # a warning about a connection nobody made.
    connects = (not sealed) and not getattr(ctx.args, "skip_ha", False) and (
        _hub_requested(ctx)
        or bool(getattr(ctx.args, "ha_url", None)
                and getattr(ctx.args, "ha_token", None)))

    fields: list[dict] = []
    if sealed:
        fields.append({"id": "sealed", "kind": "info", "label": "Home Assistant",
                       "current": "this build reaches nothing, so nothing on "
                                  "your network is offered here"})
    elif offer:
        wants = _hub_requested(ctx)
        fields.append({"id": "connect_hub", "kind": "toggle",
                       "label": "Connect Home Assistant, your smart home "
                                "hub, now?",
                       "current": wants, "proposed": wants,
                       "help": "Say no and run puffpai setup again later; "
                               "nothing is asked for until you say yes."})
        fields.append({"id": "hub_url", "kind": "text",
                       "label": "Home Assistant's web address",
                       "current": str(ctx.applied("hub_url",
                                                  default="") or DEFAULT_HA_URL),
                       "max_length": 200, "pattern": URL_PATTERN})
        # The token is asked for only once the hub has been said yes to. On a
        # console both come in one pass; in a browser the toggle is applied
        # first, so a card that carried a secret field from the start could
        # never be part of "accept the rest" (which refuses any card with one)
        # and the owner could not get past it.
        if wants or not _remote(ctx):
            fields.append({"id": "hub_token", "kind": "secret_entry",
                           "label": "Your Home Assistant key",
                           "max_length": 400,
                           "help": "In Home Assistant open your profile, then "
                                   "Security, and make a long-lived access "
                                   "token. Paste it here. What you type stays "
                                   "hidden and is kept only in this "
                                   "computer's settings."})
    else:
        fields.append({"id": "hub", "kind": "info", "label": "Home Assistant",
                       "current": "not being asked for on this run; re-run "
                                  "`puffpai setup` to add it"})

    for row in rows:
        detail = row["status"]
        if row["door"]:
            detail += f" - needs {row['door']}"
        if row["without"]:
            detail += f" - without it, {row['without']}"
        fields.append({"id": "tool_" + _field_id(row["id"]), "kind": "info",
                       "label": row["label"], "current": detail})
    if not rows:
        fields.append({"id": "tool_none", "kind": "info",
                       "label": "What this build was asked to reach for",
                       "current": "nothing yet: the goal named no tools"})

    missing = [row for row in rows if row["status"] != AVAILABLE]
    card = {
        "skill": "tools", "section": "tools",
        "title": "What it can reach for",
        "decided": _decided(rows),
        "status": "gap" if missing else ("needs_you" if offer else "ready"),
        "consequential": connects,
        "consequential_reasons": ["runtime_egress"] if connects else [],
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": fields,
    }
    return {"card": card, "rows": rows, "offer": offer, "sealed": sealed}


def _field_id(tool_id: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in tool_id.lower())[:30]


def _decided(rows: list[dict]) -> str:
    if not rows:
        return "Nothing was declared, so nothing is claimed."
    have = sum(1 for row in rows if row["status"] == AVAILABLE)
    return f"{have} of {len(rows)} declared tools are available on this build."


# -- apply --------------------------------------------------------------------

def apply(ctx, answers: dict) -> dict:
    """Write ``tools``. The token goes to the config writer and nowhere else.

    Every branch today's step had is here, in the same order and with the same
    outcome: a sealed build is not offered the hub, ``--skip-ha`` skips, a
    command-line pair is posture-checked and **refuses the run** if the posture
    forbids it, a non-interactive run without one skips, and a typed pair that
    the posture forbids is dropped without the token ever being written.
    """
    from puffpai import cli, setup_ops

    proposal = ctx.proposal or {}
    sealed = proposal.get("sealed")
    if sealed is None:
        sealed = not (ctx.shared.get("gates") or {}).get(
            "may_offer_lan_integrations", True)
    posture = ctx.shared.get("posture") or {}

    ha_url = ha_token = ""
    note = ""
    gap = None
    if sealed:
        note = "sealed build: no integrations offered"
    elif getattr(ctx.args, "skip_ha", False):
        note = "Home Assistant skipped"
    elif getattr(ctx.args, "ha_url", None) and getattr(ctx.args, "ha_token", None):
        violation = cli._posture_violation("home_assistant", ctx.args.ha_url,
                                           posture)
        if violation is not None:
            # A credential was supplied for an address this build may not
            # reach. Today's step stops the run rather than quietly dropping
            # it, and a card does not get to soften that.
            raise cli._refuse_for_posture(violation)
        ha_url, ha_token = ctx.args.ha_url, ctx.args.ha_token
    elif ctx.non_interactive and not _remote(ctx):
        note = "Home Assistant skipped (non-interactive, no --ha-url)"
    elif answers.get("connect_hub"):
        typed_url = answers.get("hub_url") or DEFAULT_HA_URL
        violation = cli._posture_violation("home_assistant", typed_url, posture)
        if violation is not None:
            gap = {"skill": "tools",
                   "what": "The hub address you gave was not written down.",
                   "why": getattr(violation, "user_message", str(violation)),
                   "impact": "Home Assistant is skipped; no token was asked "
                             "for or written.",
                   "fix": {"kind": "command", "id": "setup.rerun", "args": {}}}
        else:
            ha_token = answers.get("hub_token") or ""
            if ha_token:
                ha_url = typed_url
            else:
                note = "incomplete, so Home Assistant is skipped for now"

    ha_path = ""
    if ha_url and ha_token:
        ha_path = setup_ops.set_ha_config(ha_url, ha_token, enabled=True)
    del ha_token  # never past this line, and never into a return value

    rows = _rows(ctx, ha_configured=bool(ha_path))
    # What was asked for, never what was typed: the toggle and the address are
    # decisions, the token is a credential and it is already gone by here.
    requested = bool(answers.get("connect_hub")) if "connect_hub" in answers \
        else _hub_requested(ctx)
    typed_hub_url = str(answers.get("hub_url") or "") if requested else ""
    result = {
        "section": "tools",
        "data": {"home_assistant_configured": bool(ha_path),
                 "hub_requested": requested and not ha_path,
                 "hub_url": typed_hub_url or str(
                     ctx.applied("hub_url", default="") or ""),
                 "declared_vs_available": rows,
                 "available": [row["id"] for row in rows
                               if row["status"] == AVAILABLE]},
        "shared": {"ha_path": ha_path or ""},
        "gaps": _gaps(rows),
        "note": note,
    }
    if gap is not None:
        result["gap"] = gap
    return result


def verify(ctx) -> dict:
    """Observe the config on disk, not the row that was proposed."""
    from puffpai import setup_ops

    section = ctx.own()
    claimed = bool(section.get("home_assistant_configured"))
    if not claimed:
        return {"status": "pass",
                "evidence": "no hub was configured, and none is recorded"}

    try:
        configured = bool(setup_ops.ha_configured())
    except Exception:  # noqa: BLE001 - unreachable is a gap, never a failure
        configured = False
    if configured:
        return {"status": "pass",
                "evidence": "the hub address this build wrote is on disk"}
    return {"status": "gap",
            "evidence": "nothing on disk holds the hub address that was "
                        "recorded",
            "gap": {"skill": "tools",
                    "what": "The hub this build recorded is not configured on "
                            "this machine.",
                    "why": "The integrations file does not hold the address "
                           "the build says it wrote.",
                    "impact": "Your bot will say it cannot reach the house.",
                    "fix": {"kind": "command", "id": "setup.rerun",
                            "args": {}}}}
