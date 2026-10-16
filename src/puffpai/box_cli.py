"""``puffpai box status|setup|remove|finish``: the locked box from the terminal.

The terminal is the owner at her own computer: a yes typed here is bound to
this terminal's own device id (``consent_tickets.terminal_device_id``), the
same way a paired device's yes is bound to that device.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import argparse
from typing import Any, Optional

from puffpai.box import driver
from puffpai.box import wording as W

ASK_SETUP = ("Set up the locked box for outside code? It downloads about 4 MB, then "
             "about 130 MB of tools. Type Y and press Enter. Anything else keeps things "
             "as they are.")
ASK_REMOVE = ("Remove this BuildBot's locked box? Anything BuildBot built in it goes too. "
              "Type Y and press Enter. Anything else keeps it.")
ASK_ORPHANS = ("Remove the locked boxes left over from BuildBots that were deleted? Type Y "
               "and press Enter. Anything else keeps them.")
KEPT = "Nothing was changed."
REMOVED = "The locked box is removed."
NOT_REMOVED = "The locked box was not removed."


def _say(text: str) -> None:
    print(text, flush=True)


def _yes(args: argparse.Namespace, question: str) -> bool:
    if getattr(args, "yes", False):
        return True
    _say(question)
    try:
        return input().strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _ticket() -> tuple[str, str, str]:
    """(ticket, device, "") or ("", "", plain refusal)."""
    from puffpai.security import consent_tickets as ct
    device = ct.terminal_device_id()
    try:
        ticket = ct.mint("box_setup", ct.target_box(driver.distro_name()),
                         "Set up the locked box for outside code.", device)
    except ct.TicketRefused as exc:
        return "", "", exc.message
    except ValueError:
        return "", "", W.TAKEN.format(distro=driver.distro_name())
    return ticket, device, ""


def cmd_status(args: argparse.Namespace) -> int:
    st = driver.status()
    _say(st.message)
    for line in driver.waiting_lines():
        _say(line)
    return 0 if st.ready else 1


def cmd_setup(args: argparse.Namespace) -> int:
    st = driver.status()
    if st.ready:
        _say(W.SETUP_ALREADY)
        return 0
    if not _yes(args, ASK_SETUP):
        _say(KEPT)
        return 1
    ticket, device, why = _ticket()
    if not ticket:
        _say(why)
        return 2
    st = driver.setup(ticket=ticket, device_id=device, progress=_say)
    if not st.ready:
        _say(W.SETUP_FAILED.format(message=st.message))
        return 1
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    orphans = bool(getattr(args, "orphans", False))
    if not _yes(args, ASK_ORPHANS if orphans else ASK_REMOVE):
        _say(KEPT)
        return 1
    got = driver.remove_box(orphan=orphans)
    if got.get("why") == W.SHARED_STAYS:
        _say(W.SHARED_STAYS)
        return 0
    _say(REMOVED if got.get("removed") else NOT_REMOVED)
    return 0 if got.get("removed") else 1


def cmd_finish(args: argparse.Namespace) -> int:
    if not driver.waiting():
        _say(W.FINISH_NOTHING)
        return 0
    if not _yes(args, ASK_SETUP):
        _say(KEPT)
        return 1
    ticket, device, why = _ticket()
    if not ticket:
        _say(why)
        return 2
    from puffpai.security import consent_tickets as ct
    ok, verdict = ct.redeem(ticket, device, "box_setup", ct.target_box(driver.distro_name()))
    if not ok:
        _say(ct.refusal_words(verdict))
        return 2
    results = driver.finish_waiting(progress=_say)
    for r in results:
        if r.get("line"):
            _say(str(r["line"]))
    return 0 if all(r.get("status") in ("added", "added_off") for r in results) else 1


def add_parsers(subparsers: Any) -> None:
    box = subparsers.add_parser("box", help="The locked box where outside code runs")
    sub = box.add_subparsers(dest="box_command")
    p = sub.add_parser("status", help="Where the locked box stands")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("setup", help="Set up the locked box (it asks first)")
    p.add_argument("--yes", action="store_true", help="Do not ask (for scripts)")
    p.set_defaults(func=cmd_setup)
    p = sub.add_parser("remove", help="Remove this BuildBot's locked box (it asks first)")
    p.add_argument("--yes", action="store_true", help="Do not ask (for scripts)")
    p.add_argument("--orphans", action="store_true",
                   help="Remove boxes left over from BuildBots that were deleted")
    p.set_defaults(func=cmd_remove)
    p = sub.add_parser("finish", help="Set up the box and add what was waiting for it")
    p.add_argument("--yes", action="store_true", help="Do not ask (for scripts)")
    p.set_defaults(func=cmd_finish)
    box.set_defaults(func=cmd_status)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="puffpai")
    add_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["box"] + list(argv or []))
    return args.func(args)


__all__ = ["add_parsers", "cmd_finish", "cmd_remove", "cmd_setup", "cmd_status", "main"]
