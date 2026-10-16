"""Prove: real questions asked of the built bot.

The static half runs here, inside the wizard: no server, no model,
no network, and no output of its own. It reads what this build just wrote and
checks the classes of failure an owner has hit: a reserved persona
id, a persona nothing could show, packs chosen and never downloaded, and a
closing screen printing commands that did not exist on that machine.

It prints nothing. Phase 1 froze the wizard's output, and the one place Prove
speaks is the closing screen, which Handoff owns: the record travels in
``ctx.shared`` and Handoff renders it between two markers. A passing build gets
one line -- run ``puffpai prove`` once it is started, which is the half of the
proof this half cannot do.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations


def part_check(ctx):
    """Run the static checks, record them, and stay quiet."""
    from puffpai import prove

    record = prove.static_prove()
    # The same record the home page reads (GET /build/prove): the closing
    # screen cannot say "5 passed" while the home page says "Not proven yet".
    try:
        prove.write_record(record)
    except OSError:
        pass
    note = f"static checks: {record['verdict']}"
    return {
        "section": "prove",
        "data": record,
        "gaps": record.get("gaps") or [],
        "shared": {"prove_record": record},
        "note": note,
    }
