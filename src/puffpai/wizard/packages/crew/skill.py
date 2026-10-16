"""Agents: who does what, and how far they may go.

Phase 2 (spec C.8). The card contract: :func:`propose` sizes the crew for this
machine and lists who owns what, :func:`apply` writes the ``crew`` section, and
:func:`verify` observes the live roster rather than re-reading the proposal.

Three things this card deliberately does not do:

* **It never invents the master-agent count.** The count is settled at
  seven, so the card states it, but it counts
  :data:`puffpai.agents.registry.ROSTER` on this machine rather than printing a
  number from memory, and an unreadable roster states nothing at all.
* **It shows no autonomy ceiling**, because this tree has none to show. There
  is no per-agent ceiling anywhere in ``src/puffpai``, and a card inventing one
  would be the exact dishonesty the review screen exists to prevent.
* **It names only agents that exist here**, read from
  :data:`puffpai.agents.registry.ROSTER` -- the definitions in this tree, with
  the display names they already carry (the reviewer's id is ``sentinel`` and
  its name is Drawbridge).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations


def _roster() -> list[dict]:
    """Who this build actually ships, from the agent definitions in this tree.

    Never raises and never guesses: an unreadable roster returns nothing, and
    the card then says the ownership map could not be read rather than printing
    a team from memory.
    """
    try:
        from puffpai.agents import registry
        return [{"id": entry.name,
                 "name": entry.display_name or entry.name.capitalize(),
                 "area": entry.role,
                 "blurb": entry.blurb}
                for entry in (registry.ROSTER or ())]
    except Exception:  # noqa: BLE001 - a missing roster is a gap, not a crash
        return []


def _budget(ctx) -> dict:
    from puffpai import crew_sizing
    census = ctx.shared.get("census") or {}
    return crew_sizing.crew_budget(census)


def _applied_budget(ctx) -> dict:
    """What this skill already wrote, if anything. The propose rule."""
    sizing = ctx.applied("sizing", default=None)
    return dict(sizing) if isinstance(sizing, dict) and sizing else {}


def propose(ctx) -> dict:
    """Say who works inside the bot. Read-only, and nothing is asked.

    The specialists number is not on the card: it has
    no effect on the running bot. ``build_team`` builds the seven masters and
    nothing reads the figure but screens that print it. The sizing is still
    recorded, because the manifest and the build page carry it.
    """
    from puffpai import cli
    from puffpai.wizard import wording

    cli.enter_step("Crew sizing")
    budget = _budget(ctx)
    roster = _roster()
    shared = getattr(ctx, "shared", None) or {}
    name = str(shared.get("bot_name") or cli.DEFAULT_BOT_NAME)
    mode = wording.mode_of(shared)
    if roster:
        line = " ".join(wording.say("team_line", mode, count=len(roster),
                                    name=name).split())
    else:
        line = ("The list of helpers could not be read on this computer, so "
                "nothing is claimed about them.")
    card = {
        "skill": "crew", "section": "crew", "title": "Its helpers",
        "decided": line,
        "status": "ready" if roster else "gap",
        "consequential": False,
        "estimate": {"bytes": 0, "seconds": 0, "basis": "measured"},
        "fields": [{"id": "team", "kind": "info", "label": "Who works inside",
                    "current": line}],
    }
    return {"card": card, "budget": budget, "roster": roster}


def apply(ctx, answers: dict) -> dict:
    """Write ``crew``: the same sizing block Phase 1 wrote, plus the map.

    ``sizing`` is byte-for-byte what ``cli._setup_crew_step`` returned for the
    same census when the owner changes nothing -- that equality is the whole
    point of the conversion, and the test holds it. A lowered figure is
    recorded as the owner's, which is what ``crew_sizing`` already means by a
    basis.
    """
    proposal = ctx.proposal or {}
    budget = dict(proposal.get("budget") or _budget(ctx))
    applied = _applied_budget(ctx)
    if "specialists" not in answers and applied:
        # Re-applying without an answer keeps what the owner settled on; the
        # fresh fit is a recommendation, not a reset.
        budget = applied
    roster = proposal.get("roster")
    if roster is None:
        roster = _roster()

    chosen = answers.get("specialists")
    if chosen is not None and int(chosen) != int(budget.get("max_specialists") or 0):
        budget["max_specialists"] = int(chosen)
        budget["basis"] = "owner-stated"

    ownership = [{"agent": agent["name"], "area": agent["area"]}
                 for agent in roster]
    return {"section": "crew",
            "data": {"sizing": budget, "ownership": ownership},
            "shared": {"crew": budget}}


def verify(ctx) -> dict:
    """Observe the roster this build ships, not the map that was proposed."""
    section = ctx.own()
    recorded = [row.get("agent") for row in section.get("ownership") or ()
                if isinstance(row, dict)]
    live = {agent["name"] for agent in _roster()}

    if not live:
        return {"status": "gap",
                "evidence": "the agent roster could not be read",
                "gap": {"skill": "crew",
                        "what": "This build could not read its own agent "
                                "roster.",
                        "why": "Importing the agent definitions on this "
                               "machine did not work.",
                        "impact": "Nothing can say which agent owns which "
                                  "area, so the crew card is empty.",
                        "fix": {"kind": "command", "id": "setup.rerun",
                                "args": {}}}}

    missing = [name for name in recorded if name not in live]
    if missing:
        return {"status": "gap",
                "evidence": f"{', '.join(missing[:3])} is not on this build's "
                            f"roster",
                "gap": {"skill": "crew",
                        "what": "The crew this build recorded is not the crew "
                                "it ships.",
                        "why": f"{', '.join(missing[:3])} was recorded and is "
                               f"not among the agents on this machine.",
                        "impact": "Work assigned to that agent has nobody to "
                                  "do it.",
                        "fix": {"kind": "command", "id": "setup.rerun",
                                "args": {}}}}
    return {"status": "pass",
            "evidence": "every agent recorded is on this build's roster"}
