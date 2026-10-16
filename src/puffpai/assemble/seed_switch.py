"""Switch on a website an assembled app needs that is not a live card yet.

A part from BuildBot's own list of free websites (``config/catalog_seeds``)
has no Bridge switch until it is a live card. Its "not switched on yet" row
carries the fix ``{"kind": "switch_on", "service": "seed-<row id>"}``; the
card says what it adds and where it reaches, and her one tap on it
(:func:`apply`, from :mod:`puffpai.fix_api`, a paired device only) does what
the Add page does for a website: one sample look, Drawbridge's check, the
live card installed with its switch on (her tap is that yes), and then the
capabilities that need it are put together and proven again.

The one tap covers every switch that path needs. Making a live card takes one
look at the website first, under the "Looking at a service before adding it"
switch; when that is off, the card names it too, and her tap switches it on
through the same consent ticket and audit line as any switch card. A switch
that is held where a card cannot reach (by whoever runs this computer) is
said plainly, and nothing is changed.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

PREFIX = "seed-"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,58}$")


def _row(seed_id: Any) -> Optional[dict]:
    sid = str(seed_id or "")
    if not _ID_RE.match(sid):
        return None
    try:
        from puffpai.catalogs import api_seeds
        return next((r for r in api_seeds.load_rows()
                     if r.get("id") == sid and api_seeds.row_problem(r) is None), None)
    except Exception:  # noqa: BLE001 - no list, no card
        return None


def _name(row: dict) -> str:
    return " ".join(re.sub(r"[^\w .,&()'+/:!-]", " ", str(row.get("name") or "")).split())[:60] \
        or "this website"


#: The switches making a live card needs besides the website's own (the outside
#: list's row ids), with how the card names them.
NEEDED = (("sample_look", "looking at services before adding them"),)


def needed_switches() -> list[dict]:
    """``[{"id", "words", "row"}]``: the switches still off that one tap must
    also switch on."""
    out = []
    try:
        from puffpai import outside_services
        for sid, words in NEEDED:
            row = outside_services.row(sid)
            if row and row.get("state") != "on":
                out.append({"id": sid, "words": words, "row": row})
    except Exception:  # noqa: BLE001 - unreadable: the tap finds out and says so
        pass
    return out


def card(seed_id: Any) -> Optional[dict]:
    """The switch_on card for a website on BuildBot's list, or None. It names
    every switch the one tap turns on."""
    row = _row(seed_id)
    if row is None:
        return None
    name = _name(row)
    also = needed_switches()
    label = f"Switch on {name}" + "".join(f" and {n['words']}" for n in also) + \
        ("?" if also else " now?")
    detail = (f"Adds {name} as a live card. Your bot then reaches "
              f"{str(row['host']).lower()} for it, and only there.")
    if also:
        detail += (" It also switches on " + ", ".join(
            f"\"{n['row'].get('name') or n['id']}\"" for n in also) +
            ", so BuildBot can take one look at it first.")
    return {"kind": "switch_on", "service": PREFIX + row["id"], "name": name,
            "label": label, "detail": detail}


def _held_line(sw: dict, name: str) -> str:
    return (f"{sw['row'].get('name') or sw['id']} is held by whoever runs this computer, "
            f"so a card cannot switch it on. Nothing was added for {name}.")


def _caps_using(seed_id: str) -> list[str]:
    from puffpai.assemble import plan
    return [str(r.get("cap")) for r in plan.load_plan()["capabilities"]
            if any(p.get("service") == seed_id or p.get("seed") == seed_id
                   for p in r.get("parts") or [])]


def apply(seed_id: Any, device: str, *, reassemble: bool = True,
          hosts: Optional[Any] = None, result_pictures: bool = False) -> tuple[dict, int]:
    """Her tap: make the live card, switch it on, put its apps together again
    (*reassemble* False: the build's queue does that instead).

    *hosts* (the build's one Yes): the sites her list showed for it. When the
    card made from the sample would reach any other site (a picture host),
    nothing is installed or switched on: the answer says which sites
    (``needs_hosts``), so they can be put on her list first."""
    from puffpai import add_more, drawbridge, feed_generator
    from puffpai.catalogs import api_seeds
    if not device:
        return {"ok": False, "message": "This has to be done from a browser that is "
                                        "paired with your bot."}, 403
    row = _row(seed_id)
    if row is None:
        return {"ok": False, "message": "There is no outside service by that name."}, 404
    name = _name(row)
    from puffpai import fix_api
    for sw in needed_switches():
        if not sw["row"].get("can_switch"):
            return {"ok": False, "message": _held_line(sw, name)}, 409
        got, status = fix_api._switch_on(sw["id"], device)  # noqa: SLF001 - its ticket + audit
        if not got.get("ok"):
            return {"ok": False, "message": f"{got.get('message') or 'It was not switched on.'}"
                                            f" Nothing was added for {name}."}, status
        fix_api._audit("chat_fix_switch_on", "on", sw["id"])  # noqa: SLF001
    try:
        draft = feed_generator.draft_feed_app(api_seeds.to_candidate(row),
                                              build_online=add_more.online_allowed())
    except feed_generator.DraftError as exc:
        return {"ok": False, "message": f"{name}: {exc} Nothing was added."}, 409
    if hosts is not None:
        from puffpai import feeds
        allowed = {str(h).lower() for h in hosts if isinstance(h, str)}
        feed = (draft.get("manifest") or {}).get("feed") or {}
        if result_pictures:
            # Her row said "<source> and the picture sites its results point
            # to": the card's picture host came from the sample answer of the
            # listed site itself (feed_generator.card_options), and the card
            # fetches pictures only (feeds.IMAGE_TYPES, size-capped).
            pic = str((feed.get("service") or {}).get("image_host") or "").lower()
            if pic:
                allowed.add(pic)
        reach = [str(h).lower() for h in feeds.hosts_of(feed)]
        extra = [h for h in dict.fromkeys(reach) if h not in allowed]
        if extra:
            feed_generator.discard_draft(draft["id"])
            return {"ok": False, "needs_hosts": extra,
                    "message": f"{name} also needs {', '.join(extra)}, which was not on "
                               f"your list. Nothing was added."}, 409
    verdict = drawbridge.check_addition(draft, by="assemble:switch_on")
    if verdict.get("verdict") == drawbridge.BLOCK:
        feed_generator.discard_draft(draft["id"])
        return {"ok": False, "message": str(verdict.get("line") or "Drawbridge stopped it.")}, 409
    try:
        feed_generator.install_draft(draft["id"], allow=True)
    except feed_generator.DraftError as exc:
        feed_generator.discard_draft(draft["id"])
        return {"ok": False, "message": f"{name}: {exc}"}, 409
    if not reassemble:
        # The build's one Yes (build.yes): its own queue makes the jobs next.
        return {"ok": True, "line": f"{name} is switched on.", "again": False,
                "app_id": draft["id"]}, 200
    caps = _caps_using(row["id"])
    try:
        from puffpai.assemble import api
        for cap in caps:     # each one now, or queued right after the run that is going
            api.start_background(cap=cap, pending="switched on, " + api.PENDING)
    except Exception as exc:  # noqa: BLE001 - switched on; putting together is retried by hand
        logger.warning("assemble: not put together again (%s)", type(exc).__name__)
    return {"ok": True, "line": f"{name} is switched on. Putting it together again.",
            "again": False, "app_id": draft["id"]}, 200


__all__ = ["NEEDED", "PREFIX", "apply", "card", "needed_switches"]
