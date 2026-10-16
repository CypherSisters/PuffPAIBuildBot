"""Terminal commands: ``puffpai home ...`` and ``puffpai routines ...``.

Kept out of ``cli.py`` so that file only gains one line that adds these
parsers. Every line printed here is plain words for the owner.

    puffpai home                      where home is, and what is stored
    puffpai home set "Dayton, Ohio"   look the town up (asks first) and save it
    puffpai home set --lat 39.8 --lon -84.2 [--label ..] [--timezone ..]
    puffpai home clear
    puffpai routines [list]           what runs on its own
    puffpai routines on <id>          switch one on (daily_update: say yes to it)
    puffpai routines off <id>         switch one off
    puffpai routines add reminder --at 21:00 --say "Water the plants"
    puffpai routines add watch --feed <id> --above 30   (no --above/--below: tell me
                                      when that list gets something new)
    puffpai routines speak on|off     say updates out loud

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import argparse
import sys


def add_parsers(subparsers) -> None:
    sub = subparsers.add_parser(
        "home", help="Tell the bot your home town (rough position only)")
    sub.add_argument("home_action", nargs="?", choices=["show", "set", "clear"],
                     default="show")
    sub.add_argument("town", nargs="?", default=None,
                     help='Town name, like "Dayton, Ohio"')
    sub.add_argument("--lat", type=float, default=None, help="Latitude, typed by hand")
    sub.add_argument("--lon", type=float, default=None, help="Longitude, typed by hand")
    sub.add_argument("--label", default="", help="A name for the place you typed")
    sub.add_argument("--timezone", default="", help="Time zone, like America/New_York")
    sub.add_argument("-y", "--yes", action="store_true",
                     help="Say yes to the town lookup without being asked")
    sub.set_defaults(func=cmd_home)

    sub = subparsers.add_parser(
        "routines", help="Things the bot does on its own: list, on, off, add, speak")
    sub.add_argument("routines_action", nargs="?",
                     choices=["list", "on", "off", "add", "remove", "speak"],
                     default="list")
    sub.add_argument("target", nargs="?", default=None,
                     help="Routine name (on/off/remove), kind (add), or on/off (speak)")
    sub.add_argument("--at", default=None, help="Time of day, like 08:00")
    sub.add_argument("--say", default=None, help="What a reminder should say")
    sub.add_argument("--once", default=None,
                     help="Date and time for a one-time reminder, like 2026-09-24T09:00")
    sub.add_argument("--feed", default=None, help="Which live info card to watch")
    sub.add_argument("--above", type=float, default=None)
    sub.add_argument("--below", type=float, default=None)
    sub.add_argument("--every", type=int, default=None, help="Minutes between looks")
    sub.add_argument("--name", default=None, help="A short name for the new routine")
    sub.set_defaults(func=cmd_routines)


def _ask(question: str) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        return False
    try:
        return input(question + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _print_home() -> None:
    from puffpai import home_location
    home = home_location.get_home()
    if home is None:
        print("Your bot doesn't know your home town yet.")
        print('Set it with: puffpai home set "Your Town"')
        return
    tz = home["timezone"] or "this computer's clock"
    print(f"Home: {home['label']}")
    print(f"Time zone: {tz}")
    print(home_location.WHAT_IS_STORED)


def cmd_home(args: argparse.Namespace) -> int:
    from puffpai import home_location
    action = args.home_action
    if action == "show":
        _print_home()
        return 0
    if action == "clear":
        home_location.clear_home()
        print("Done. Your bot no longer knows your home town.")
        return 0
    try:
        if args.lat is not None or args.lon is not None:
            home_location.set_home_coords(args.lat, args.lon, label=args.label,
                                          timezone_name=args.timezone)
            print("Saved. Nothing was sent off this computer.")
            _print_home()
            return 0
        if not args.town:
            print('Please type a town, like: puffpai home set "Dayton, Ohio"')
            return 2
        allowed, why = home_location.geocode_allowed()
        if not allowed:
            print(why)
            from puffpai.security import egress_doors
            if egress_doors.run_posture().get("run_egress") != egress_doors.OPEN_POSTURE:
                print("You can type the position instead: "
                      "puffpai home set --lat 39.8 --lon -84.2 --timezone America/New_York")
                return 1
            print("To find it, I would send only the town name you typed to "
                  "geocoding-api.open-meteo.com, once, and only if you say "
                  "yes.")
            if not (args.yes or _ask("Is that okay?")):
                print("Okay, I didn't look it up. You can type the position instead: "
                      "puffpai home set --lat 39.8 --lon -84.2")
                return 1
            if not home_location.allow_geocode(by="owner"):
                print("I couldn't switch the town lookup on.")
                return 1
        home, others = home_location.set_home_town(args.town)
    except home_location.HomeError as exc:
        print(str(exc))
        return 1
    print("Saved.")
    _print_home()
    if others:
        print("Not the right place? I also found: " + "; ".join(others))
        print('Add the state or country, like: puffpai home set "Springfield, Missouri"')
    return 0


def _list() -> None:
    from puffpai import offers, routines
    rows = routines.list_routines()
    if not rows:
        print("Nothing runs on its own yet.")
    for row in rows:
        print("  " + routines.describe(row, with_name=True))
    offer = offers.current_offer()
    if offer:
        print()
        print(offer["question"] + " " + offer["detail"])
        print("Say yes with: puffpai routines on daily_update")
    s = routines.settings()
    print()
    print("Saying updates out loud: " + ("on" if s["speak_aloud"] else "off")
          + (" (puffpai routines speak on)" if not s["speak_aloud"] else ""))


def cmd_routines(args: argparse.Namespace) -> int:
    from puffpai import announcer, offers, routines
    action, target = args.routines_action, args.target
    if action == "list":
        _list()
        return 0
    if action == "speak":
        if target not in ("on", "off"):
            print("Say: puffpai routines speak on   (or off)")
            return 2
        routines.set_speak_aloud(target == "on")
        if target == "on":
            box, why = announcer.box_voice_status()
            print("Okay, updates will be said out loud."
                  + ("" if box else " " + why))
        else:
            print("Okay, updates will not be said out loud.")
        return 0
    if action in ("on", "off", "remove") and not target:
        print(f"Which routine? See the names with: puffpai routines list")
        return 2
    if action == "on":
        if routines.get_routine(target) is None and target == offers.ROUTINE_ID:
            result = offers.accept(offers.OFFER_ID, by="owner", at=args.at)
            print(result["message"])
            return 0 if result["ok"] else 1
        row = routines.set_enabled(target, True, by="owner")
        if row is None:
            print("There is no routine with that name. See: puffpai routines list")
            return 1
        print(f"Okay, \"{row['label']}\" is on: {routines.spoken_when(row)}.")
        return 0
    if action == "off":
        row = routines.set_enabled(target, False, by="owner")
        if row is None:
            print("There is no routine with that name. See: puffpai routines list")
            return 1
        print(f"Okay, \"{row['label']}\" is off.")
        return 0
    if action == "remove":
        ok = routines.remove_routine(target, by="owner")
        print("Removed." if ok else "There is no routine with that name.")
        return 0 if ok else 1
    # add
    kinds = [k for k in routines.kinds() if k != "daily_update"]
    if target not in kinds:
        print("You can add: " + ", ".join(kinds) + ". For example:")
        print('  puffpai routines add reminder --at 21:00 --say "Water the plants"')
        return 2
    params = {"message": args.say, "feed_id": args.feed,
              "above": args.above, "below": args.below}
    try:
        row = routines.add_routine(target, routine_id=args.name or None,
                                   daily_at=args.at, once_at=args.once,
                                   every_minutes=args.every,
                                   params={k: v for k, v in params.items()
                                           if v is not None}, by="owner")
    except routines.RoutineError as exc:
        print(str(exc))
        return 1
    print(f"Added \"{row['label']}\" ({row['id']}): {routines.spoken_when(row)}.")
    return 0


__all__ = ["add_parsers", "cmd_home", "cmd_routines"]
