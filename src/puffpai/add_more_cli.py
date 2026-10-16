"""``puffpai add``: add more to the bot from the terminal (:mod:`puffpai.add_more`).

::

    puffpai add "dinosaur fossils"            find, then ask which to add
    puffpai add "dinosaur fossils" --pick 1,3 add those without asking
    puffpai add "dinosaur fossils" --online   also ask the book libraries online
    puffpai add --list                        what was added since the build
    puffpai add --take-out 2                  take one out again
    puffpai add --notes | --forget-notes      words nothing was found for

The terminal on this computer says yes by itself: whoever holds it already
owns the bot's files. The same loop, words and checks as the chat and the
dashboard's Add page.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import sys
from typing import Any, Optional


def add_parsers(subparsers) -> None:
    sub = subparsers.add_parser(
        "add", help="Add more to your bot: books, live cards, a daily update")
    sub.add_argument("words", nargs="?", default=None,
                     help="What you want your bot to know or show")
    sub.add_argument("--pick", default=None,
                     help="Which lines to add without being asked: 1,3 or all")
    sub.add_argument("--online", action="store_true",
                     help="Also ask the book libraries online (the words it "
                          "sends are printed first)")
    sub.add_argument("--switch-on", dest="switch_on", action="store_true",
                     help="If a new live card's website is switched off, "
                          "switch it on without asking")
    sub.add_argument("--list", dest="list_added", action="store_true",
                     help="What was added since the build")
    sub.add_argument("--take-out", dest="take_out", default=None,
                     help="Take one addition out again, by its number")
    sub.add_argument("--notes", action="store_true",
                     help="The words nothing was found for")
    sub.add_argument("--forget-notes", dest="forget_notes", action="store_true",
                     help="Forget those words")
    sub.set_defaults(func=cmd_add)


def _say(line: Any = "") -> None:
    text = str(line)
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(enc, "replace").decode(enc, "replace"))


def _ask(prompt: str) -> Optional[str]:
    from puffpai import cli
    return cli._read_line(prompt)  # noqa: SLF001


def _yes(prompt: str) -> bool:
    from puffpai import cli
    return cli._ask_yes_no(prompt, default=False)  # noqa: SLF001


def _numbers(text: str, count: int) -> Optional[list[int]]:
    """"1,3", "1 3", "all" -> numbers; "" -> []; anything else -> None."""
    text = str(text or "").strip().lower()
    if not text:
        return []
    if text in ("all", "a", "everything"):
        return list(range(1, count + 1))
    out: list[int] = []
    for part in text.replace(",", " ").split():
        if not part.isdigit() or not 1 <= int(part) <= count:
            return None
        out.append(int(part))
    return sorted(dict.fromkeys(out))


def _print_session(session: dict) -> None:
    from puffpai import add_more
    for line in add_more.render_text(session, "terminal").splitlines():
        _say(line)


def cmd_add(args) -> int:
    from puffpai import add_more
    from puffpai.security import consent_tickets

    if getattr(args, "list_added", False):
        for line in add_more.added_lines("terminal"):
            _say(line)
        return 0
    if getattr(args, "take_out", None):
        try:
            _say(add_more.take_out(args.take_out, by="terminal"))
        except add_more.AddError as exc:
            _say(exc.message)
            return 1
        return 0
    if getattr(args, "notes", False):
        rows = add_more.wishes()
        if not rows:
            _say("No notes: everything you asked for was found, or nothing was "
                 "asked yet.")
        for row in rows:
            _say(f"{str(row['at'])[:10]}: {', '.join(row['terms'])}")
        return 0
    if getattr(args, "forget_notes", False):
        _say(f"Forgot {add_more.forget_wishes()} notes.")
        return 0
    words = getattr(args, "words", None)
    if not words or not str(words).strip():
        _say('Tell me what to add, for example: puffpai add "bird songs"')
        return 1

    device = consent_tickets.terminal_device_id()
    session = add_more.find(words, via="terminal", client=device,
                            online=bool(getattr(args, "online", False)))
    _say(add_more.title())
    _print_session(session)
    # Questions are asked only when there is someone to answer them: a read
    # that finds nothing (piped input, or Windows' empty NUL device, which
    # claims to be a keyboard) is a no, never a yes.
    if session.get("online_possible") and args.pick is None:
        _say(add_more.online_line(session, "terminal"))
        if _yes("Look online too?"):
            session = add_more.find(words, via="terminal", client=device,
                                    online=True)
            _print_session(session)
    rows = add_more.actionable(session)
    if not rows:
        return 0
    if args.pick is not None:
        picked = _numbers(args.pick, len(rows))
        if picked is None:
            _say(f"--pick takes numbers from 1 to {len(rows)}, or all.")
            return 1
    else:
        answer = _ask(add_more.choose_line("terminal") + " ")
        if answer is None:
            _say("Nothing was added: choose with --pick 1,3 or --pick all.")
            return 1
        picked = _numbers(answer, len(rows))
        if picked is None:
            _say("Nothing was added: that was not a list of numbers.")
            return 1
    if not picked:
        _say("Nothing was added.")
        return 0
    ids = add_more.ids_for_numbers(session, picked)
    try:
        results = add_more.approve(session["id"], ids, client=device,
                                   device=device, surface="terminal",
                                   by="terminal")
    except add_more.AddError as exc:
        _say(exc.message)
        return 1
    failed = 0
    for result in results:
        _say(f"{result['n']}. {result['line']}")
        for line in result.get("progress") or []:
            _say(f"   {line}")
        for row in result.get("checks") or []:
            if row.get("result") != "pass" and row.get("why"):
                _say(f"   Check: {row['why']}")
        if result["status"] in ("held", "refused"):
            failed += 1
        offer = result.get("switch_on")
        if offer:
            try:
                said = add_more.switch_on_sentence(offer["service"])
            except add_more.AddError as exc:
                _say(f"   {exc.message}")
                continue
            _say(f"   {said}")
            if getattr(args, "switch_on", False) or _yes("Switch it on?"):
                try:
                    _say(f"   {add_more.switch_on(offer['service'], ticket=None, device=device, surface='terminal')}")
                except add_more.AddError as exc:
                    _say(f"   {exc.message}")
            else:
                _say("   It stays switched off. To switch it on later, run "
                     "puffpai outside and then puffpai outside on <its number>")
    _say("See everything you added with: puffpai add --list")
    return 1 if failed and failed == len(results) else 0


__all__ = ["add_parsers", "cmd_add"]
