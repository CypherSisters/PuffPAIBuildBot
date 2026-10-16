"""Crew sizing — how many specialists this machine can carry.

Step 3a of the wizard. The master team ships with every build (a contract, not a
choice). On top of it, a persona adds domain specialists; this module says,
plainly, how many this hardware can carry well, and that number becomes the
budget the build interview (:mod:`puffpai.build_interview`) spends.

Honesty rules, same as the brain menu:

* The one class we have actually run the master team on live is the reference
  Orin (``workstation``). Its budget is grounded; every other class is a
  deliberately conservative default marked as such, with reports welcome.
* The specialists share the resident brain rather than each loading their own
  model (the engine runs one tag in both slots), so this is a headroom-and-
  concurrency budget, not a RAM-per-agent multiplication. When RAM is unknown we
  offer the core only until the wizard asks.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from typing import Any, Optional

# Conservative specialist budgets on top of the always-present master team.
# 'workstation' is grounded in the reference Orin running the master team live;
# the rest are careful defaults, not measured limits (basis is recorded below so
# the wizard never presents a guess as a measurement).
_SPECIALIST_BUDGET: dict[str, int] = {
    "workstation": 5,
    "high": 4,
    "mid": 2,
    "entry": 1,
    "minimal": 0,
    "unknown": 0,
}

_GROUNDED_CLASSES = frozenset({"workstation"})


def _master_core_count() -> Optional[int]:
    """The number of master agents this build ships, read from the roster.

    Best effort: reads the live registry so the number tracks the code (six
    today; if a seventh lands, this updates itself). None if the roster cannot
    be read, in which case callers speak of "the master team" without a count.
    """
    try:
        from puffpai.agents import registry
        roster = getattr(registry, "ROSTER", None)
        if roster:
            return len(roster)
    except Exception:
        pass
    return None


def crew_budget(census: dict[str, Any]) -> dict[str, Any]:
    """Return the specialist budget for a machine, plus an honest note.

    Never raises. ``max_specialists`` is what the build interview may spend on
    top of the master core; ``basis`` is 'reference' only for the class we have
    run live, else 'conservative-default'.
    """
    hardware_class = str(census.get("hardware_class", "unknown"))
    max_specialists = _SPECIALIST_BUDGET.get(hardware_class, 0)
    grounded = hardware_class in _GROUNDED_CLASSES
    master_core = _master_core_count()

    if hardware_class == "unknown":
        note = (
            "We do not know this machine's memory yet, so we are offering the "
            "master team only. Tell the wizard how much RAM it has and we will "
            "size the crew."
        )
    elif max_specialists == 0:
        note = (
            "This machine can carry the master team that ships with every build. "
            "We would not add specialists on top at this size; a bigger box gives "
            "the crew room to grow."
        )
    else:
        plural = "specialist" if max_specialists == 1 else "specialists"
        basis = (
            "grounded in a live run of the full agent team on measured test hardware"
            if grounded
            else "a conservative starting budget, not a measured limit, and reports are welcome"
        )
        note = (
            f"On a machine this size, the crew can take up to {max_specialists} "
            f"{plural} on top of the master team. That number is {basis}."
        )

    return {
        "hardware_class": hardware_class,
        "master_core": master_core,
        "max_specialists": max_specialists,
        "basis": "reference" if grounded else "conservative-default",
        "note": note,
    }


def crew_summary_lines(budget: dict[str, Any]) -> list[str]:
    """Plain-language lines describing the crew budget, for the CLI/API."""
    lines: list[str] = []
    core = budget.get("master_core")
    if core:
        lines.append(f"Master team: {core} agents ship with every build.")
    else:
        lines.append("Master team: ships with every build.")
    lines.append(f"Room for specialists: up to {budget.get('max_specialists', 0)} more.")
    if budget.get("note"):
        lines.append(f"  {budget['note']}")
    return lines


__all__ = [
    "crew_budget",
    "crew_summary_lines",
]
