"""Assemble: the one build plan.

Runs after Integrate (everything chosen is on disk by then) and before Prove.
This step calls ``build.pipeline.plan(goal, ctx)``: it works out the jobs,
finds and checks what fits each, and saves one setup list in
``<state>/build/build.json``. Nothing is made here; the owner's one Yes on the
setup list (the dashboard) starts the making.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations


def _given_jobs(shared: dict) -> list[dict]:
    """The jobs she already has: the capabilities the wizard kept (never
    replaced), else the ones she ticked on the console's jobs step."""
    try:
        from puffpai.finding import capabilities
        kept = [{"name": c.get("name"), "does": c.get("does", "")}
                for c in capabilities.load() if isinstance(c, dict) and c.get("name")]
    except Exception:  # noqa: BLE001
        kept = []
    if kept:
        return kept
    try:
        from puffpai.wizard import proposal
        needs = proposal.needs_of(shared)
    except Exception:  # noqa: BLE001 - no jobs step in this build
        needs = None
    jobs = (needs or {}).get("jobs") if isinstance(needs, dict) else None
    return [{"name": j.get("name"), "does": j.get("does", "")}
            for j in jobs or () if isinstance(j, dict) and j.get("ticked") and j.get("name")]


def part_plan(ctx):
    """The one build plan."""
    from puffpai.build import pipeline
    shared = ctx.shared if isinstance(getattr(ctx, "shared", None), dict) else {}
    goal = str((shared.get("interview") or {}).get("goal") or "")
    # The pipeline's own context: its defaults (this build's brain, saved),
    # and the jobs she already has, planned exactly.
    given = _given_jobs(shared)
    record = pipeline.plan(goal, {"jobs": given} if given else {})
    record = record if isinstance(record, dict) else {}
    jobs = [j for j in record.get("jobs") or () if isinstance(j, dict)]
    setup = [r for r in record.get("setup") or () if isinstance(r, dict)]
    note = (f"{len(jobs)} job{'' if len(jobs) == 1 else 's'} planned, "
            f"{len(setup)} thing{'' if len(setup) == 1 else 's'} on the setup list")
    # Her first apps that fit the goal: on the same Yes (puffpai.first_apps).
    from puffpai import first_apps
    turn_on = first_apps.lines(record)
    if turn_on:
        note += "; " + "; ".join(turn_on)
    return {"status": "done", "note": note,
            "shared": {"build_plan": {"jobs": len(jobs), "setup": len(setup)}}}
