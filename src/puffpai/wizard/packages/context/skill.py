"""Context: what this build told the model about itself.

**Context v1**: deterministic assembly and a read-only card. No owner decision,
no model-written prompt text, and no free prose from another skill.

The mechanism is :data:`ALLOWLIST`: a typed list
of *scalar* manifest fields, each with a length cap and a character filter.
Nothing reaches this card except those fields. Not a source's description, not
a proposal's reasoning, not a blurb -- a source label is allowed as a **label**,
capped at 60 characters with every control character dropped, which is why a
label reading "ignore previous instructions\\n\\x1b[31m" arrives as one short
line of visible text and reaches nowhere else.

What the card shows is what the model is actually told, not a re-description of
it: :func:`puffpai.build_context.system_prompt` assembles the prompt today, and
the card is built from the same inputs plus a digest of the assembled text.
``build_context`` already gives the goal this exact treatment
(``as_data``/``_plain``) and interpolates nothing from another wizard skill, so
v1 needed no change there; the allowlist below is the rule any future field has
to pass before it is interpolated.

Long text is not stored. ``apply`` writes the fields that were used and a
digest of the assembled prompt -- the prompt itself lives where it is
assembled, and a second copy in the run record is a second thing to keep true.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib

#: One allowlisted field: where it comes from, what a person is shown, the type
#: it must be, and the cap it is held to. ``kind`` is one of:
#:
#: * ``text`` -- printable text, control characters dropped, capped;
#: * ``enum`` -- one of a fixed set, or dropped entirely;
#: * ``labels`` -- a list of short labels, each capped, the list capped;
#: * ``count`` -- a whole number, shown as digits.
ALLOWLIST: tuple[dict, ...] = (
    {"id": "bot_name", "label": "The name it answers to",
     "kind": "text", "cap": 40},
    {"id": "goal", "label": "What the owner said it is for",
     "kind": "text", "cap": 300},
    {"id": "persona_id", "label": "Its own page", "kind": "text", "cap": 40},
    {"id": "personality", "label": "How it is asked to sound", "kind": "enum",
     "cap": 40},
    {"id": "posture", "label": "What it may reach", "kind": "enum", "cap": 40},
    {"id": "source_labels", "label": "What it learns from", "kind": "labels",
     "cap": 60, "items": 20},
    {"id": "tool_ids", "label": "What it can reach for", "kind": "labels",
     "cap": 60, "items": 20},
    {"id": "crew_size", "label": "Extra helpers it has room for",
     "kind": "count", "cap": 3},
)

#: What a person reads for each enum value. The value itself stays the value
#: the runtime reads; it is never what is shown.
ENUM_WORDS: dict[str, dict[str, str]] = {
    "posture": {
        "sealed": "nothing at all, not even your home network",
        "island": ("your home network, and on the internet only BuildBot's "
                   "own tools for finding things"),
        "enumerated": "only the outside services on the plan",
    },
}

#: Field ids the card carries that are not interpolated content: they are
#: measurements of the assembled prompt, not anything a skill wrote.
DERIVED_FIELDS: tuple[str, ...] = ("assembled_digest", "assembled_chars")

#: The enums. A value outside its set is dropped, not shown as itself, because
#: an enum that can hold anything is a text field nobody capped.
#: Both sets are the ones this tree actually has:
#: ``build_context.PERSONALITY_STYLES`` and ``build_posture._RUN_EGRESS``.
ENUMS: dict[str, tuple[str, ...]] = {
    "personality": ("helpful", "concise", "creative", "technical"),
    "posture": ("sealed", "island", "enumerated"),
}


# -- the allowlist ------------------------------------------------------------

def _printable(raw, cap: int) -> str:
    """Capped, control-stripped, whitespace-collapsed. The character filter."""
    text = raw if isinstance(raw, str) else ""
    clean = "".join(ch for ch in text
                    if ch == " " or (ch.isprintable() and ch != "\x7f"))
    return " ".join(clean.split())[:cap]


def _labels(raw, cap: int, items: int) -> list[str]:
    out: list[str] = []
    for item in raw if isinstance(raw, (list, tuple)) else ():
        if isinstance(item, dict):
            item = item.get("label") or item.get("id")
        text = _printable(item, cap)
        if text:
            out.append(text)
        if len(out) >= items:
            break
    return out


def _value(spec: dict, raw):
    """One allowlisted value, typed. ``None`` means it does not go on the card."""
    kind = spec["kind"]
    if kind == "text":
        return _printable(raw, spec["cap"]) or None
    if kind == "enum":
        text = _printable(raw, spec["cap"]).lower()
        allowed = ENUMS.get(spec["id"], ())
        return text if text in allowed else None
    if kind == "labels":
        found = _labels(raw, spec["cap"], spec.get("items", 12))
        return found or None
    if kind == "count":
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        return str(int(raw))[:spec["cap"]]
    return None


def _raw_inputs(ctx) -> dict:
    """The scalar values the allowlist may draw on, read from the sections."""

    def section(name: str) -> dict:
        try:
            return ctx.read(name)
        except Exception:  # noqa: BLE001 - an undeclared read is the runner's call
            return {}

    persona = section("persona")
    interview = persona.get("interview")
    if not isinstance(interview, dict):
        interview = ctx.shared.get("interview") or {}
    persona_block = persona.get("persona")
    if not isinstance(persona_block, dict):
        persona_block = {}
    knowledge = section("knowledge")
    tools = section("tools")
    crew = section("crew")
    posture = (section("posture").get("posture") or {})

    return {
        "bot_name": persona.get("bot_name") or persona_block.get("name"),
        "goal": interview.get("goal") or persona_block.get("goal"),
        # ``app`` is what a scaffolded persona block carries (``id`` only ever
        # appeared in hand-written records), so reading ``id`` alone left this
        # field off the card on every real build.
        "persona_id": persona_block.get("app") or persona_block.get("id"),
        "personality": persona_block.get("personality"),
        "posture": posture.get("run_egress"),
        "source_labels": _source_labels(knowledge, section("sources")),
        "tool_ids": tools.get("available"),
        "crew_size": (crew.get("sizing") or {}).get("max_specialists"),
    }


def _source_labels(knowledge: dict, sources: dict) -> list:
    for block in (sources.get("selected"), knowledge.get("selected"),
                  (knowledge.get("block") or {}).get("selected")
                  if isinstance(knowledge.get("block"), dict) else None):
        if isinstance(block, list) and block:
            return block
    return []


def fields_from(ctx) -> tuple[list[dict], dict]:
    """The allowlisted card fields, and the values behind them."""
    raw = _raw_inputs(ctx)
    fields: list[dict] = []
    used: dict = {}
    for spec in ALLOWLIST:
        value = _value(spec, raw.get(spec["id"]))
        if value is None:
            continue
        used[spec["id"]] = value
        shown = ", ".join(value) if isinstance(value, list) else value
        shown = ENUM_WORDS.get(spec["id"], {}).get(shown, shown)
        fields.append({"id": spec["id"], "kind": "info",
                       "label": spec["label"], "current": shown})
    return fields, used


# -- the prompt this build actually assembles ---------------------------------

def assembled(used: dict) -> str:
    """What ``build_context`` produces for these inputs. Never raises."""
    try:
        from puffpai import build_context
        return build_context.system_prompt(
            bot_name=used.get("bot_name") or "",
            goal=used.get("goal") or "",
            apps=build_context.collect_apps(),
            personality=used.get("personality")
            or build_context.DEFAULT_PERSONALITY)
    except Exception:  # noqa: BLE001 - an unassemblable prompt is a gap
        return ""


def digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def propose(ctx) -> dict:
    """Assemble deterministically and show it. Read-only, nothing asked."""
    fields, used = fields_from(ctx)
    text = assembled(used)
    fields.append({"id": "assembled_digest", "kind": "info",
                   "label": "A short code that changes when what it was "
                            "told changes",
                   "current": digest(text)})
    fields.append({"id": "assembled_chars", "kind": "info",
                   "label": "How long that is", "current": f"{len(text)} characters"})

    card = {
        "skill": "context", "section": "context",
        "title": "What it was told about itself",
        "decided": _decided(used),
        "status": "ready" if used.get("goal") else "gap",
        "consequential": False,
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": fields,
        "gaps": [] if used.get("goal") else [{
            "skill": "context",
            "what": "Your bot was told nothing about what it is for.",
            "why": "No goal was recorded, so the prompt says so rather than "
                   "inventing a purpose.",
            "impact": "It will answer as a general assistant, not as yours.",
            "fix": {"kind": "command", "id": "setup.rerun", "args": {}}}],
    }
    return {"card": card, "used": used, "text": text}


def _decided(used: dict) -> str:
    name = used.get("bot_name") or "This bot"
    goal = used.get("goal")
    if not goal:
        return f"{name} was told its name and nothing about its purpose."
    return f"{name} was told: {goal}"[:200]


def apply(ctx, answers: dict) -> dict:
    """Write ``context``: which fields were used, and a digest of the result.

    Read-only card, so ``answers`` is always empty; the argument exists because
    the lifecycle has one shape.
    """
    proposal = ctx.proposal or {}
    used = proposal.get("used")
    if used is None:
        _, used = fields_from(ctx)
    text = proposal.get("text")
    if text is None:
        text = assembled(used)
    return {"section": "context",
            "data": {"fields_used": sorted(used),
                     "values": used,
                     "prompt_digest": digest(text),
                     "prompt_chars": len(text)},
            "note": "assembled from the typed allowlist"}


def verify(ctx) -> dict:
    """Observe what the live build tells the model, not what was proposed.

    ``build_context.collect_goal`` reads the manifest on disk (and the
    scaffolded app if there is no manifest). If the card said the bot is for
    one thing and the disk says another, the card was a description of nothing.
    """
    from puffpai import build_context

    section = ctx.own()
    values = section.get("values") or {}
    shown_goal = values.get("goal") or ""
    shown_name = values.get("bot_name") or ""

    live_goal = ""
    try:
        live_goal = build_context.collect_goal()
    except Exception:  # noqa: BLE001
        live_goal = ""
    live_name = _live_name()

    goal_ok = _printable(live_goal, 300) == shown_goal
    name_ok = (not shown_name) or _printable(live_name, 40) == shown_name
    if goal_ok and name_ok:
        return {"status": "pass",
                "evidence": "the goal and name on disk are the ones the card "
                            "showed"}
    if shown_goal and not live_goal:
        # Nothing on disk carries the goal at all: the manifest has no persona
        # block and no place was scaffolded. ``puffpai setup`` on its own is
        # the wrong line to print here -- a second non-interactive run would
        # record exactly the same nothing -- so the fix names the flag that
        # makes the place.
        return {"status": "gap",
                "evidence": "the goal the card showed is on nothing this bot "
                            "reads",
                "gap": {"skill": "context",
                        "what": "What your bot was told is not written down "
                                "anywhere it reads.",
                        "why": "The build recorded no persona block and no "
                               "place of its own, so the goal reaches no file.",
                        "impact": "Your bot will answer as a general "
                                  "assistant, not as yours.",
                        "fix": {"kind": "command", "id": "setup.scaffold",
                                "args": {}}}}
    return {"status": "gap",
            "evidence": f"the build records {live_name or 'no name'} / "
                        f"{live_goal or 'no goal'}",
            "gap": {"skill": "context",
                    "what": "What your bot was told is not what this build "
                            "recorded.",
                    "why": "The goal or the name on disk is not the one the "
                           "card showed.",
                    "impact": "Your bot will introduce itself as something "
                              "other than what you set up.",
                    "fix": {"kind": "command", "id": "setup.rerun",
                            "args": {}}}}


def _live_name() -> str:
    try:
        from puffpai import build_manifest
        manifest = build_manifest.read_manifest() or {}
        build = manifest.get("build")
        if isinstance(build, dict):
            return str(build.get("name") or "")
    except Exception:  # noqa: BLE001
        pass
    return ""
