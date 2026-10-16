"""Model profiles — how much scaffolding a given brain needs.

BuildBot has no native tool-calling. Tool routing is done with keyword
rulepacks (:mod:`puffpai.rulepacks`) plus prompt-shaped structure, and every
one of those layers was originally hand-tuned against a single model. That made
the wizard's brain menu dishonest: it offered six models and only one of them
behaved. This module is the adapter layer that fixes it.

A profile answers four questions about a model, and nothing else:

* **How much scaffolding?** ``routing_tier`` is ``light`` (trust the model to
  find its own way), ``guided`` (one step at a time), or ``rails`` (set
  phrasings only). It is an amount of help, not a quality score.
* **How do we ask for structure?** ``dialect`` is the shape of a structured
  reply the model can actually hold: a fenced block, hard JSON mode, or nothing
  at all.
* **How much can it hold?** ``budgets`` are CEILINGS. A caller may ask for
  less; it can never be handed more, so a small brain is never given an 8k
  context it will quietly truncate.
* **What can it not do?** ``limits_note`` is one plain sentence the bot or the
  wizard can say out loud, so the menu tells the truth up front.

Honesty rule, inherited from the model catalog and non-negotiable: ``basis`` is
``measured:<silicon_id>`` only for a model we have actually benched on that
board. Today that is exactly one pair. Everything else is
``conservative-default`` and says so. Never invent a measured number.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from puffpai import config_health

from puffpai import rulepacks

# Baked-in fallback so the adapter layer still works if the shipped profile
# file is missing or unreadable. Deliberately tiny: the reference brain, and the
# conservative middle that an unknown model gets. The real table lives in
# config/model_profiles.json and is the thing maintainers edit.
_FALLBACK_PROFILES: dict[str, Any] = {
    "schema": "model_profiles/v1",
    "profiles": {
        "qwen3:30b-a3b": {
            "routing_tier": "light",
            "dialect": "json_block",
            "supports": {"planning": True, "json_mode": True, "thinking": True},
            "budgets": {"num_ctx": 8192, "num_predict": 1024, "plan_ctx": 8192},
            "prompt": {"no_think_token": "/no_think", "system_suffix": None},
            "limits_note": (
                "This brain handles multi-step requests and picks its own tools, "
                "so you can ask in your own words."
            ),
            "basis": "measured:jetson_agx_orin_64gb",
        },
        "_default": {
            "routing_tier": "guided",
            "dialect": "json_mode",
            "supports": {"planning": False, "json_mode": True, "thinking": False},
            "budgets": {"num_ctx": 4096, "num_predict": 512, "plan_ctx": 4096},
            "prompt": {
                "no_think_token": None,
                "system_suffix": (
                    "Answer briefly. If you cannot do something, say so plainly "
                    "instead of describing an action you did not take."
                ),
            },
            "limits_note": (
                "We have not tested this brain, so it is set up to be told one "
                "step at a time. Reports welcome if it does better than that."
            ),
            "basis": "conservative-default",
        },
    },
}

# Last-resort profile, used when even '_default' is missing from a hand-edited
# file. Same conservative posture as the shipped default.
_HARD_DEFAULT: dict[str, Any] = dict(_FALLBACK_PROFILES["profiles"]["_default"])


def _profiles_path() -> str:
    """Absolute path of the shipped model profile table."""
    from puffpai.config import get_config
    return os.path.join(get_config().REPO_ROOT, "config", "model_profiles.json")


def validate_profiles(data: Any, model_ids: Optional[list[str]] = None) -> Optional[str]:
    """Structural check of a parsed profile table; None means healthy.

    ``_default`` is the entry every unknown model lands on, so a table without
    it has no floor. The coverage check is the other half: a profile table that
    does not name every model on the ladder silently gives those models the
    conservative default and no one is told why they feel slow.
    """
    if not isinstance(data, dict):
        return "The profile table is not a JSON object."
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        return "The profile table has no profiles."
    if "_default" not in profiles:
        return "The profile table has no '_default' profile."
    for key, entry in profiles.items():
        if not isinstance(entry, dict):
            return f"Profile '{key}' is not a JSON object."
    if model_ids is None:
        from puffpai import model_catalog
        catalog = model_catalog.load_catalog()
        # If the ladder itself is degraded we are comparing against a stub, so
        # coverage would be a cascade, not a finding. The ladder's own record
        # already tells the owner what is wrong.
        if any(
            r["config"] == "model_catalog" for r in config_health.report(degraded_only=True)
        ):
            return None
        model_ids = [
            str(m.get("id")) for m in (catalog.get("models") or [])
            if isinstance(m, dict) and m.get("id")
        ]
    missing = [mid for mid in model_ids if mid not in profiles]
    if missing:
        return (
            f"The profile table does not cover every model on the ladder: "
            f"{', '.join(sorted(missing))}."
        )
    return None


def load_profiles(path: Optional[str] = None) -> dict[str, Any]:
    """Load the profile table, degrading to the baked-in fallback.

    An absent table falls back silently; a present-but-corrupt one records the
    degradation in :mod:`puffpai.config_health` first, because a build running
    every model on the conservative default is a build with a limitation its
    owner should be able to see.
    """
    target = path or _profiles_path()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        config_health.note_load_failure("model_profiles", target, exc, reduced=True)
        return _FALLBACK_PROFILES
    problem = validate_profiles(data)
    if problem is None:
        return data  # type: ignore[no-any-return]
    config_health.record(
        "model_profiles", target, config_health.INVALID, problem, reduced=True,
    )
    return _FALLBACK_PROFILES


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------

def get_profile(
    model_id: str, profiles: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """The profile for a model id: exact match, then family, then the default.

    Family matching exists because people run quantised and re-tagged variants:
    ``qwen2.5:7b-instruct-q4_K_M`` is the same brain as
    ``qwen2.5:7b-instruct`` for routing purposes, and it would be worse than
    useless to drop it to the unknown-model default. The longest matching key
    wins, so a specific profile always beats a shorter family prefix.

    Matching is one-directional on purpose: a longer id may inherit a shorter
    profile, never the reverse. A bare family name like ``qwen3`` must not
    inherit the reference brain's tier by accident. And because a variant is
    not the thing we benched, an inherited profile's ``basis`` is downgraded to
    ``conservative-default`` -- measured stays attached to the exact tag only.

    Never raises. An empty or unrecognised id gets the conservative default.
    """
    table = (profiles or load_profiles()).get("profiles") or {}
    if not isinstance(table, dict):
        return dict(_HARD_DEFAULT)

    wanted = (model_id or "").strip().lower()
    named = {str(k).lower(): v for k, v in table.items() if k != "_default"}

    if wanted in named and isinstance(named[wanted], dict):
        return dict(named[wanted])

    if wanted:
        candidates = [k for k in named if wanted.startswith(k)]
        if candidates:
            best = max(candidates, key=len)
            if isinstance(named[best], dict):
                inherited = dict(named[best])
                inherited["basis"] = "conservative-default"
                inherited["inherited_from"] = best
                return inherited

    fallback = table.get("_default")
    if isinstance(fallback, dict):
        return dict(fallback)
    return dict(_HARD_DEFAULT)


def routing_tier(model_id: str, profiles: Optional[dict[str, Any]] = None) -> str:
    """The routing tier for a model: 'light', 'guided', or 'rails'."""
    tier = str(get_profile(model_id, profiles).get("routing_tier", "")).lower()
    return tier if tier in rulepacks.TIER_RANK else rulepacks.DEFAULT_TIER


def supports(
    model_id: str, feature: str, profiles: Optional[dict[str, Any]] = None
) -> bool:
    """Whether a model supports a capability ('planning', 'json_mode', 'thinking').

    Fail-closed: an unknown feature, or a profile that does not mention it, is
    False. We would rather add scaffolding a strong model did not need than
    hand a weak one a job it cannot do.
    """
    caps = get_profile(model_id, profiles).get("supports")
    if not isinstance(caps, dict):
        return False
    return bool(caps.get(feature, False))


def dialect(model_id: str, profiles: Optional[dict[str, Any]] = None) -> str:
    """The structured-reply dialect this model can hold."""
    value = str(get_profile(model_id, profiles).get("dialect", "")).strip()
    return value or "keyword_only"


def limits_sentence(model_id: str, profiles: Optional[dict[str, Any]] = None) -> str:
    """One plain sentence about what this brain cannot do, for a person to read."""
    note = get_profile(model_id, profiles).get("limits_note")
    if isinstance(note, str) and note.strip():
        return note.strip()
    return str(_HARD_DEFAULT["limits_note"])


def basis(model_id: str, profiles: Optional[dict[str, Any]] = None) -> str:
    """Where this profile's numbers come from: 'measured:<board>' or a default."""
    value = get_profile(model_id, profiles).get("basis")
    return str(value) if value else "conservative-default"


def _board_tokens(silicon_id: str, label: str) -> set[str]:
    """The words that have to appear in a census for it to BE this board.

    Derived from the silicon id and its label rather than a second hand-kept
    list, so adding a benched board to config/model_catalog.json does not also
    require editing this module. Pure-size tokens ("64gb") are dropped: a
    32 GB Orin is still an Orin.
    """
    import re
    words = set(re.split(r"[^a-z0-9]+", f"{silicon_id} {label}".lower()))
    return {w for w in words
            if w and not re.fullmatch(r"\d+(gb|mb|tb)?", w)}


def _census_is_this_board(census: dict[str, Any], silicon_id: str,
                          board: dict[str, Any]) -> bool:
    """True only when this machine really is the board a number was measured on."""
    if str(census.get("hardware_class", "")) != str(board.get("hardware_class")):
        return False
    soc = census.get("soc") or {}
    accel = census.get("accelerator") or {}
    cpu = census.get("cpu") or {}
    haystack = " ".join(str(v or "") for v in (
        soc.get("model"), accel.get("name"), accel.get("kind"), cpu.get("model"),
    )).lower()
    tokens = _board_tokens(silicon_id, str(board.get("label") or ""))
    return bool(tokens) and all(t in haystack for t in tokens)


def basis_label(model_id: str, census: Optional[dict[str, Any]] = None,
                profiles: Optional[dict[str, Any]] = None,
                catalog: Optional[dict[str, Any]] = None) -> str:
    """The provenance to PRINT next to a routing tier, for this machine.

    :func:`basis` is the record; this is the sentence. A cold-clone run on a
    Windows laptop with an RTX 2000 Ada once printed
    ``Routing tier: light (measured:jetson_agx_orin_64gb)`` - honest about where
    the number came from, and read by a stranger as "this tool is hard-coded to
    the authors' hardware", which is the opposite of what the census exists to
    say. So the measured label appears only on a machine that is actually that
    board; everywhere else the number is a conservative default and says so.
    The raw ``basis`` still goes into the build manifest unchanged.
    """
    raw = basis(model_id, profiles)
    if not raw.startswith("measured:"):
        return "conservative default"
    silicon_id = raw.split(":", 1)[1].strip()
    if not silicon_id or not isinstance(census, dict):
        return "conservative default"
    if catalog is None:
        from puffpai import model_catalog
        catalog = model_catalog.load_catalog()
    board = (catalog.get("silicon") or {}).get(silicon_id)
    if not isinstance(board, dict):
        return "conservative default"
    if not _census_is_this_board(census, silicon_id, board):
        return "conservative default"
    return f"measured on {board.get('label') or silicon_id}"


# ---------------------------------------------------------------------------
# budgets
# ---------------------------------------------------------------------------

_BUDGET_FLOOR: dict[str, int] = {"num_ctx": 512, "num_predict": 32, "plan_ctx": 512}


def _clamp(name: str, ceiling: Any, requested: Optional[int]) -> int:
    """The smaller of what the caller asked for and what the profile allows."""
    try:
        cap = int(ceiling)
    except (TypeError, ValueError):
        cap = int(_HARD_DEFAULT["budgets"][name])
    cap = max(cap, _BUDGET_FLOOR[name])
    if requested is None:
        return cap
    try:
        want = int(requested)
    except (TypeError, ValueError):
        return cap
    if want <= 0:
        return cap
    return min(want, cap)


def resolve_budgets(
    model_id: str,
    *,
    requested_ctx: Optional[int] = None,
    requested_predict: Optional[int] = None,
    requested_plan_ctx: Optional[int] = None,
    profiles: Optional[dict[str, Any]] = None,
) -> dict[str, int]:
    """Settle the context and output budgets for one call to a model.

    The profile is a CEILING, never a target. A caller asking for less keeps
    the smaller number; a caller asking for more is clamped down. There is no
    path through this function that raises a budget above the profile, which is
    the whole point: the 8k context tuned for the reference brain must not be
    handed to a 1B model that will truncate the prompt without saying so.
    """
    budgets = get_profile(model_id, profiles).get("budgets")
    if not isinstance(budgets, dict):
        budgets = dict(_HARD_DEFAULT["budgets"])
    return {
        "num_ctx": _clamp("num_ctx", budgets.get("num_ctx"), requested_ctx),
        "num_predict": _clamp(
            "num_predict", budgets.get("num_predict"), requested_predict),
        "plan_ctx": _clamp(
            "plan_ctx",
            budgets.get("plan_ctx", budgets.get("num_ctx")),
            requested_plan_ctx),
    }


# ---------------------------------------------------------------------------
# phrasebook
# ---------------------------------------------------------------------------

_TIER_INTRO: dict[str, str] = {
    "light": (
        "Ask in your own words. This brain works out what you meant, so the "
        "phrasings below are a starting point rather than a script."
    ),
    "guided": (
        "Ask for one thing at a time. This brain is reliable when the request "
        "is a single step, and the phrasings below are the ones it never misses."
    ),
    "rails": (
        "Use these phrasings. This brain does not guess at what you meant, so "
        "the wordings below are the ones that work, and near misses will not."
    ),
}


# The limits line is framed for the tier it describes. On the light tier the
# honest sentence is about what you CAN expect, and labelling it a limitation
# would read as false modesty on a public page.
_LIMITS_LABEL: dict[str, str] = {
    "light": "What to expect from this brain",
    "guided": "What this brain cannot do",
    "rails": "What this brain cannot do",
}


def generate_phrasebook(
    model_id: str,
    enabled_surfaces: Optional[list[str]] = None,
    packs: Optional[list[dict[str, Any]]] = None,
    profiles: Optional[dict[str, Any]] = None,
) -> str:
    """A Markdown phrasebook for one brain, generated from the live rulepacks.

    Generated, not written: every phrase listed is a phrase the router actually
    matches, because both come from the same packs. That is the only way a
    phrasebook stays true after someone edits the vocabulary.

    Args:
        model_id: the Ollama tag the build is running.
        enabled_surfaces: restrict to these surfaces (e.g. a build with no
            media server). None means every surface this tier loads.
    """
    tier = routing_tier(model_id, profiles)
    loaded = rulepacks.assemble(tier, packs)
    if enabled_surfaces is not None:
        wanted = {str(s) for s in enabled_surfaces}
        loaded = [p for p in loaded if p.get("surface") in wanted]

    lines: list[str] = [
        f"# Phrasebook for {model_id}",
        "",
        f"Routing tier: **{tier}**. {_TIER_INTRO.get(tier, _TIER_INTRO['guided'])}",
        "",
        f"{_LIMITS_LABEL.get(tier, _LIMITS_LABEL['guided'])}: "
        f"{limits_sentence(model_id, profiles)}",
        "",
    ]

    if not loaded:
        lines.append("No surfaces are enabled on this build yet.")
        return "\n".join(lines) + "\n"

    for pack in loaded:
        lines.append(f"## {rulepacks.title_for(pack)}")
        blurb = pack.get("blurb")
        if isinstance(blurb, str) and blurb.strip():
            lines.append("")
            lines.append(blurb.strip())

        examples = rulepacks.examples_for(pack)
        if examples:
            lines.append("")
            lines.append("These always work:")
            lines.append("")
            for phrase in examples:
                lines.append(f"- \"{phrase}\"")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "Every phrasing above is checked against the real router on each "
        "build, so this page cannot promise something the bot will not do. "
        "The wordings live in `config/rulepacks/`; add your own and they show "
        "up here."
    )

    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "load_profiles",
    "get_profile",
    "routing_tier",
    "supports",
    "dialect",
    "limits_sentence",
    "basis",
    "basis_label",
    "resolve_budgets",
    "generate_phrasebook",
]
