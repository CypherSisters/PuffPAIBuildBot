"""Terminal commands: ``puffpai looks ...`` -- how the dashboard looks.

Kept out of ``cli.py`` so that file only gains the lines that add this parser.
The terminal wording is its own; the browser page has its own.

    puffpai looks [show]              the look in use
    puffpai looks find "<words>"      looks for your words, numbered (nothing changes)
    puffpai looks use <n>             use number n from the last find
    puffpai looks background <name>   clouds, stars, plain, soft, petals or confetti
    puffpai looks undo                go back one step
    puffpai looks reset               back to the bot's own look

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import argparse
import sys

RELOAD = "Reload the dashboard page to see it."


def add_parsers(subparsers) -> None:
    sub = subparsers.add_parser(
        "looks", help="How the dashboard looks: show, find <words>, use <n>, "
                      "background <clouds|stars|plain|soft|petals|confetti>, undo, reset")
    sub.add_argument("looks_action", nargs="?", default="show",
                     choices=["show", "find", "use", "background", "undo", "reset"])
    sub.add_argument("words", nargs="*", default=[],
                     help="For 'find': what you would like it to look like. "
                          "For 'use': the number 'find' showed. "
                          "For 'background': clouds, stars, plain, soft, petals or confetti")
    sub.add_argument("--no-brain", action="store_true",
                     help="For 'find': do not ask the bot's brain")
    sub.set_defaults(func=cmd_looks)


def _print_look(n: int | None, row: dict, out) -> None:
    from puffpai.looks import package as P
    pkg = row["look"]
    head = f"{n:>2}  " if n is not None else "    "
    print(f"{head}{pkg['name']}: {P.colours_line(pkg)}", file=out)
    print(f"    {row['why']} {P.background_line(pkg['background'])}", file=out)
    if pkg.get("night") is None:
        print("    It has one set of colours, used day and night.", file=out)
    for note in pkg.get("adjusted") or []:
        print(f"    {note}", file=out)


def _show(out) -> int:
    from puffpai.looks import package as P
    from puffpai.looks import store
    st = store.state()
    now = store.current()
    if now is None:
        print("Your bot uses its own look.", file=out)
    else:
        print(f"Your bot uses the look '{now['name']}': {P.colours_line(now)}.", file=out)
        print(P.from_line(now), file=out)
    print(st["background_line"], file=out)
    print(st["pages_line"], file=out)
    print(st["pages_own"], file=out)
    print('Find another with: puffpai looks find "<your words>"', file=out)
    print("Change the background with: puffpai looks background "
          "<clouds, stars, plain, soft, petals or confetti>", file=out)
    if st["can_undo"]:
        print("Go back one step with: puffpai looks undo", file=out)
    return 0


def _find(words: str, brain: bool, out) -> int:
    from puffpai.looks import find as F
    from puffpai.looks import store
    if not words.strip():
        print('Say what you would like, for example: puffpai looks find "outer space"',
              file=out)
        return 2
    ask = F.default_ask() if brain else None
    result = F.find(words, ask=ask)
    store.remember_found(words, [f["look"] for f in result["looks"]])
    if brain and ask is None:
        print("The bot's brain is not running, so it was not asked.", file=out)
    if not result["looks"]:
        print(result["note"], file=out)
        return 0
    print(f"Looks for '{words.strip()[:60]}'. Nothing changes until you pick one.",
          file=out)
    for n, row in enumerate(result["looks"], 1):
        _print_look(n, row, out)
    if result["note"]:
        print(result["note"], file=out)
    print("Try one with: puffpai looks use <number>      "
          "Go back any time with: puffpai looks undo", file=out)
    return 0


def _use(which: str, out) -> int:
    from puffpai.looks import store
    found = store.last_found()
    if not which.isdigit() or not (1 <= int(which) <= len(found)):
        if not found:
            print('Find some looks first: puffpai looks find "<your words>"', file=out)
        else:
            print(f"Pick a number from 1 to {len(found)}.", file=out)
        return 2
    got = store.use(found[int(which) - 1]["id"])
    print(got["message"], file=out)
    if got.get("changed"):
        print(RELOAD, file=out)
    return 0 if got["ok"] else 1


def cmd_looks(args: argparse.Namespace, out=None) -> int:
    from puffpai.looks import store
    out = out or sys.stdout
    action = args.looks_action
    words = " ".join(args.words or [])
    if action == "show":
        return _show(out)
    if action == "find":
        return _find(words, not getattr(args, "no_brain", False), out)
    if action == "use":
        return _use(words.strip(), out)
    if action == "background":
        got = store.set_background(words)
        print(got["message"], file=out)
        if got.get("changed"):
            print(RELOAD, file=out)
        return 0 if got["ok"] else 2
    got = store.undo() if action == "undo" else store.reset()
    print(got["message"], file=out)
    if got.get("changed"):
        print(RELOAD, file=out)
    return 0 if got["ok"] else 1


__all__ = ["add_parsers", "cmd_looks"]
