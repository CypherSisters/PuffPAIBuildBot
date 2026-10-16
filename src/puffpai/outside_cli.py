"""Terminal commands: ``puffpai outside ...`` -- what leaves your computer.

Kept out of ``cli.py`` so that file only gains one line that adds this parser.
Every line printed here is the terminal's own wording; the browser page has
its own.

    puffpai outside [list]         every outside service, numbered
    puffpai outside off <n|name>   switch one off (nothing else asked)
    puffpai outside on <n|name>    shows what would leave, asks, switches on
    puffpai outside log [--hours]  every request BuildBot made (last 24 hours)
    puffpai outside ok             hide the "switch list was damaged" note
                                   (switches nothing on)

Switching ON in the terminal uses the same consent ticket as the browser,
bound to this terminal process: the sentence printed is the sentence agreed
to, and the ticket is spent once.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import argparse
import sys


def add_parsers(subparsers) -> None:
    sub = subparsers.add_parser(
        "outside", help="What leaves your computer: list, on <n>, off <n>, log")
    sub.add_argument("outside_action", nargs="?", default="list",
                     choices=["list", "on", "off", "log", "ok"])
    sub.add_argument("which", nargs="?", default=None,
                     help="The number or name `puffpai outside` shows")
    sub.add_argument("--hours", type=float, default=24.0,
                     help="For 'log': how many hours back (at most 720)")
    sub.add_argument("-y", "--yes", action="store_true",
                     help="For 'on': say yes without being asked (the "
                          "sentence is still printed)")
    sub.set_defaults(func=cmd_outside)


def _print_list() -> int:
    from puffpai import outside_services
    page = outside_services.page("terminal")
    print(page["title"])
    if page.get("notice"):
        print()
        print(page["notice"])
    print(page["line"])
    print()
    for item in page["services"]:
        print(f"  {item['number']:>2}. {item['name']} - {item['state_line']}")
        if item["for"]:
            print(f"      For: {item['for']}")
        print(f"      What leaves your computer: {item['what_leaves']}")
        if item["websites"]:
            print(f"      Goes to: {', '.join(item['websites'])}")
        if item["last_used"]:
            today = item["today"]
            print(f"      Last used {item['last_used']} ({today['sent']} sent, "
                  f"{today['stopped']} stopped in the last day)")
        if item["how_to_switch"]:
            print(f"      To switch it {item['switch_to']}: {item['how_to_switch']}")
        elif item["why_cannot_switch"]:
            print(f"      {item['why_cannot_switch']}")
    print()
    print(page["not_seen_line"])
    for line in page["not_seen"]:
        print(f"  - {line}")
    print(page["home_network_line"])
    print("To see every request from the last day: puffpai outside log")
    return 0


def _print_log(hours: float) -> int:
    from puffpai import outside_services
    hours = max(1.0, min(float(hours or 24.0), 720.0))
    rows = outside_services.log_rows(hours)
    span = "day" if hours == 24 else f"{hours:g} hours"
    if not rows:
        print(f"BuildBot made no requests outside this computer in the last {span}.")
        return 0
    print(f"Every request BuildBot made in the last {span}, newest first:")
    for row in rows:
        print(f"  {row['line']}")
    print("Kept for 30 days on this computer. What you searched for or typed "
          "is never kept.")
    return 0


def _pick(which):
    from puffpai import outside_services
    listing = outside_services.page("terminal")["services"]
    item = outside_services.find(which, listing=listing)
    if item is None:
        print("There is no outside service by that name or number. "
              "`puffpai outside` lists them.")
    return item


def _switch_off(which) -> int:
    from puffpai import outside_services
    item = _pick(which)
    if item is None:
        return 1
    try:
        after = outside_services.switch_off(item["id"], by="owner")
    except outside_services.OutsideError as exc:
        print(exc.message)
        return 1
    print(f"{after['name']}: {after['state_line']}")
    return 0


def _switch_on(which, *, yes: bool) -> int:
    from puffpai import cli, outside_services
    from puffpai.security import consent_tickets
    item = _pick(which)
    if item is None:
        return 1
    try:
        sentence = outside_services.proposal(item["id"])
    except outside_services.OutsideError as exc:
        print(exc.message)
        return 1
    device = consent_tickets.terminal_device_id()
    ticket = consent_tickets.mint("outside_on", item["id"], sentence, device)
    print(sentence)
    if not yes:
        interactive = sys.stdin is not None and sys.stdin.isatty()
        if not interactive:
            print("Nothing was switched on: this needs a yes typed here, or "
                  "--yes.")
            return 1
        if not cli._ask_yes_no("Switch it on?", default=False):  # noqa: SLF001
            print("Nothing was switched on.")
            return 0
    ok, verdict = consent_tickets.redeem(ticket, device, "outside_on", item["id"])
    if not ok:
        print(consent_tickets.refusal_words(verdict))
        return 1
    try:
        after = outside_services.switch_on(item["id"], by="owner")
    except outside_services.OutsideError as exc:
        print(exc.message)
        return 1
    print(f"{after['name']}: {after['state_line']}")
    return 0


def cmd_outside(args: argparse.Namespace) -> int:
    action = getattr(args, "outside_action", None) or "list"
    which = getattr(args, "which", None)
    if action == "list":
        return _print_list()
    if action == "log":
        return _print_log(getattr(args, "hours", 24.0))
    if action == "ok":
        from puffpai import outside_services
        try:
            done = outside_services.acknowledge_notice()
        except outside_services.OutsideError as exc:
            print(exc.message)
            return 1
        print("The note is hidden. Every outside service stays as it is now."
              if done else "There is no note to hide.")
        return 0
    if not which:
        print(f"Say which one: puffpai outside {action} <number>. "
              f"`puffpai outside` lists them.")
        return 1
    if action == "off":
        return _switch_off(which)
    return _switch_on(which, yes=bool(getattr(args, "yes", False)))


__all__ = ["add_parsers", "cmd_outside"]
