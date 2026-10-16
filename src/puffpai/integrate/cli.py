"""``puffpai integrations list|remove|undo``.

The terminal is the owner's own device (``consent_tickets.terminal_device_id``):
it may list, remove and undo. Output is plain words; no paths on this
computer are printed.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import argparse
from typing import Any

from puffpai.integrate import records, wording

STATUS_WORDS = {
    "ready": "ready", "held": "waiting for your yes", "waiting_box": "waiting for the locked box",
    "refused": "left out", "failed": "did not work", "building": "being set up",
    "checking": "being checked", "fetching": "downloading", "removed": "removed",
}


def lines_for_list() -> list[str]:
    rows = records.all()
    if not rows:
        return [wording.CLI_NONE]
    out = []
    for rec in rows:
        what = STATUS_WORDS.get(str(rec.get("status")), str(rec.get("status")))
        out.append(f"{rec['id']}  {rec.get('name') or ''}: {what}")
    return out


def cmd(args: Any) -> int:
    from puffpai.integrate import actions
    from puffpai.security import consent_tickets
    action = getattr(args, "integrations_action", "list") or "list"
    if action == "list":
        print("\n".join(lines_for_list()))
        return 0
    if action == "remove":
        ident = str(getattr(args, "id", "") or "")
        if not ident:
            print(wording.CLI_NEED_ID)
            return 2
        got = records.remove(ident, by="terminal")
        print(got.get("line") or "")
        return 0 if got.get("ok") else 1
    if action == "undo":
        got = actions.undo_last(consent_tickets.terminal_device_id())
        print(got.get("line") or "")
        return 0 if got.get("ok") else 1
    print(wording.CLI_NEED_ID)
    return 2


def add_parsers(subparsers: Any) -> argparse.ArgumentParser:
    """Add ``integrations`` to the ``puffpai`` command."""
    p = subparsers.add_parser(
        "integrations",
        help="Apps, data and pictures Toolsmith added: list, remove <id>, undo")
    p.add_argument("integrations_action", nargs="?", default="list",
                   choices=["list", "remove", "undo"])
    p.add_argument("id", nargs="?", default=None,
                   help="What to remove, as `puffpai integrations list` shows it")
    p.set_defaults(func=cmd)
    return p


__all__ = ["add_parsers", "cmd", "lines_for_list"]
