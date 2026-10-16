"""What the plan says leaves the computer, and the build keeping to it.

The rule: the plan names
every outside service that will be on for this bot, with what was already
sent while looking; anything the plan does not name is off once the bot is
made. The plan, "What leaves your computer", the sidebar count and the home
page then give the same list, because all of them read
:func:`puffpai.outside_services.rows` and this module makes that list match
the plan.

Two calls, fed the same things, so they cannot disagree:

* :func:`stay_on` -- the list, worked out from the plan's ticked lines, the
  internet choice, what the looking step really sent (the outside-traffic
  log, from the moment she pressed Start looking) and what she switched on
  herself. The plan footer (:func:`footer`) is drawn from it.
* :func:`settle` -- run once at the end of the build: every service that is
  on and is not on the list is switched off (``by="wizard"``).

Nothing here ever switches a service ON. A service the Bridge keeps off is
left off and is not named, so a raised Bridge is never loosened by a plan.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: BuildBot's own looking tools. When one of them sent requests during this
#: run's look (she pressed Start looking, which named the sites), it stays on
#: after the build, named on the plan.
LOOK_KEPT = ("code_search", "picture_search")

#: The shelves "Finding things for your bot" searches. When she went through
#: that step they were switched on for her bot (``finding.find.switch_on_shelves``),
#: and they stay on, named on the plan, so chat can find things too.
FIND_KEPT = ("shelf_clawhub", "shelf_skills_sh", "shelf_hermes", "shelf_mcp_registry",
             "code_search")

#: Services used while the looking ran, said on the plan as already sent.
LOOK_SAID = ("code_search", "picture_search", "library", "wikipedia_books",
             "gutenberg_books", "openstax_books", "wikibooks_books",
             "archive_books", "sample_look",
             "catalog_refresh")

#: Plan line kind -> BuildBot's own tools it uses, which then stay on so
#: more of the same can be added later.
KIND_SERVICES: dict[str, tuple[str, ...]] = {
    "code_project": ("code_download", "app_parts"),
    "data_template": ("code_download",),
    "picture_set": ("picture_download",),
    "box_setup": ("code_download",),
    "cli_tool": ("code_download",),
}

#: Book lines: the book site whose row lists the line's host, else the
#: book library.
BOOK_KINDS = ("knowledge.add", "source_fetch")
BOOK_SERVICES = ("wikipedia_books", "gutenberg_books", "openstax_books",
                 "wikibooks_books", "archive_books", "library")
LIVE_KINDS = ("live_feed", "live_source")
CARD_PLACEHOLDER = "card-line:"

REACH_OFFLINE = "offline"

#: Plain names for the sites the looking step asks.
SITE_NAMES: tuple[tuple[str, str], ...] = (
    ("github.com", "GitHub"), ("githubusercontent.com", "GitHub"),
    ("npmjs.org", "npm"), ("npmjs.com", "npm"),
    ("huggingface.co", "Hugging Face"), ("codeberg.org", "Codeberg"),
    ("wikimedia.org", "Wikimedia Commons"), ("openverse.org", "Openverse"),
    ("wikipedia.org", "Wikipedia"), ("gutenberg.org", "Project Gutenberg"),
    ("openstax.org", "OpenStax"), ("kiwix.org", "Kiwix"),
    ("archive.org", "Internet Archive"),
)

_WORDS = {
    "head": {"standard": "What leaves your computer: only what you say yes to.",
             "simple": "Only what you say yes to leaves your computer."},
    "already": {"standard": "Already sent while looking, only the words shown "
                            "before you pressed Start looking: {list}.",
                "simple": "Already sent while looking (only the words you "
                          "saw): {list}."},
    "during": {"standard": "When you press Looks good: requests to {hosts}.",
               "simple": "When you press Looks good:\nYour computer will talk "
                         "to: {hosts}."},
    "looks": {"standard": "When you press Looks good, the plan looks for each job on "
                          "{list}; they stay on so your bot can find more later.",
              "simple": "When you press Looks good, it looks for each job on {list}, "
                        "and they stay on so your bot can find more later."},
    "kept": {"standard": "After {name} is made, these stay on: {list}. Every "
                         "other outside service is off until you switch it on.",
             "simple": "After {name} is made, these stay on: {list}.\nEverything "
                       "else is off until you switch it on."},
    "none_kept": {"standard": "After {name} is made, every outside service is "
                              "off until you switch it on.",
                  "simple": "After {name} is made, everything outside is off "
                            "until you switch it on."},
}


def _say(key: str, mode: str, **values: Any) -> str:
    words = _WORDS[key]
    return words.get(mode if mode in words else "standard", words["standard"]
                     ).format(**values)


def site_name(host: Any) -> str:
    """"GitHub" for api.github.com; any other address as it is."""
    text = str(host or "").strip().lower()
    for suffix, name in SITE_NAMES:
        if text == suffix or text.endswith("." + suffix):
            return name
    return str(host or "")


def _names(hosts: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(site_name(h) for h in hosts if str(h or "").strip()))


def _rows() -> dict[str, dict]:
    try:
        from puffpai import outside_services
        return {r["id"]: r for r in outside_services.rows()}
    except Exception as exc:  # noqa: BLE001 - no list: nothing named, all off
        logger.warning("plan outside list: the outside list failed: %s",
                       type(exc).__name__)
        return {}


def look_hours(shared: Any, *, now: Optional[float] = None) -> Optional[float]:
    """How far back the looking step's requests go, or None when she did not
    let it look online (``needs_look`` is "online" only after Start looking
    with the internet on). ``needs_look_at`` is when she pressed it; an older
    run without it reads the last day."""
    if not isinstance(shared, dict) or shared.get("needs_look") != "online":
        return None
    at = shared.get("needs_look_at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return 24.0
    now = time.time() if now is None else now
    return max(0.0, min((now - float(at)) / 3600.0 + 1.0 / 60.0, 30 * 24.0))


def already_sent(shared: Any, rows: Optional[dict] = None) -> list[dict]:
    """``[{id, name, sent, sites}]``: what the looking step really sent, from
    the outside-traffic log. Counts sent requests only (stopped ones went
    nowhere)."""
    hours = look_hours(shared)
    if hours is None:
        return []
    try:
        from puffpai.security import egress_ledger
        lines = egress_ledger.read(hours, limit=egress_ledger.MAX_READ)
    except Exception as exc:  # noqa: BLE001
        logger.debug("plan outside list: log unreadable: %s", exc)
        return []
    rows = rows if rows is not None else _rows()
    counts: dict[str, int] = {}
    sites: dict[str, list[str]] = {}
    for rec in lines:
        sid = rec.get("service")
        if sid not in LOOK_SAID or rec.get("outcome") != "done":
            continue
        counts[sid] = counts.get(sid, 0) + 1
        host = str(rec.get("host") or "")
        if host and host not in sites.setdefault(sid, []):
            sites[sid].append(host)
    out = []
    for sid in LOOK_SAID:
        if counts.get(sid):
            name = str((rows.get(sid) or {}).get("name") or sid)
            out.append({"id": sid, "name": name, "sent": counts[sid],
                        "sites": _names(sorted(sites.get(sid) or []))})
    return out


def _entry(row: dict, why: str, *, item_id: str = "") -> dict:
    websites = [w for w in row.get("websites") or [] if isinstance(w, str)]
    out = {"id": str(row["id"]), "name": str(row.get("name") or row["id"]),
           "websites": _names(websites), "why": why}
    if item_id:
        out["item_id"] = item_id
    return out


def _book_service(host: str, rows: dict) -> str:
    for sid in BOOK_SERVICES:
        if host and host in ((rows.get(sid) or {}).get("websites") or []):
            return sid
    return "library"


def _sealed() -> bool:
    try:
        from puffpai.security import bridge_guard
        return bridge_guard.is_sealed()
    except Exception:  # noqa: BLE001 - not known: treat as sealed (nothing named)
        return True


def _switched_on(sid: str) -> bool:
    """True when *sid* was switched on (not only on as BuildBot comes)."""
    try:
        from puffpai.security import outside_switches
        return outside_switches.get(sid) is True
    except Exception:  # noqa: BLE001 - not known: not kept
        return False


def build_finds() -> bool:
    """Does this build's plan search the shelves itself (the one build
    pipeline, after Looks good)? Then they are named on the plan and
    switched on when she presses Looks good (``wizard.service.approve``)."""
    try:
        import importlib.util
        return importlib.util.find_spec("puffpai.build.pipeline") is not None
    except Exception:  # noqa: BLE001
        return False


def finding_sites(rows: dict, reach: Any = None) -> list[str]:
    """The shelf and code sites the build plan's search asks for each job, by
    name: the ones that are on now and that she did not switch off. None for
    a bot that stays offline or keeps everything on this computer."""
    if reach == REACH_OFFLINE or _sealed() or not build_finds():
        return []
    return list(dict.fromkeys(_names(w for sid in FIND_KEPT
                                     for w in ((rows.get(sid) or {}).get("websites") or [])
                                     if (rows.get(sid) or {}).get("state") == "on")))


def stay_on(shared: Any, items: Iterable[dict], reach: Any,
            *, rows: Optional[dict] = None) -> list[dict]:
    """Every outside service that is on for this bot once it is made.

    ``[{id, name, websites, why}]`` in a stable order. *items* are the plan's
    ticked lines (``apply``, ``outside_host``, ``label``, ``id``); *reach* is
    the internet choice. A live card is not made yet, so its id is
    ``card-line:<plan line id>`` until :func:`settle` maps it to the card.
    Never raises: on any doubt a service is left off the list (and so off).
    """
    rows = rows if rows is not None else _rows()
    items = list(items or ())
    offline = reach == REACH_OFFLINE
    sealed = _sealed()
    out: list[dict] = []
    seen: set[str] = set()

    def add(entry: dict) -> None:
        if entry["id"] not in seen:
            seen.add(entry["id"])
            out.append(entry)

    def on_now(sid: str) -> Optional[dict]:
        row = rows.get(sid)
        return row if row is not None and row.get("state") == "on" else None

    # 1. What she switched on herself stays on (a stay-offline choice still
    #    keeps the bot's own services off: the run setting wins there).
    for row in rows.values():
        if row.get("state") != "on":
            continue
        kind = row.get("kind")
        if kind in ("app_web", "box"):
            add(_entry(row, "you switched it on"))
        elif kind == "mcp":
            add(_entry(row, "you said yes to this tool"))
        elif kind in ("built_in", "card") and not offline \
                and row.get("turned_on_by") == "you":
            add(_entry(row, "you switched it on"))
        elif kind == "as_built" and row.get("turned_on_by") == "you":
            add(_entry(row, "you switched it on"))
    if offline or sealed:
        return out
    # 2. The shelves the build plan searches for the bot's jobs (switched on
    #    when she presses Looks good, named here first), and BuildBot's
    #    looking tools that really looked for this bot.
    found_step = False
    for sid in FIND_KEPT:
        row = on_now(sid)
        if row is not None and _switched_on(sid):
            add(_entry(row, "Finding things for your bot uses it"))
            found_step = True
    # The book libraries stay on whenever the plan used them: that step
    # searched them, or a ticked line is a book. Find more and chat search
    # them all (otherwise they read "off" after the build, and Find more
    # offers only Wikipedia).
    if found_step or any(
            isinstance(i, dict) and i.get("on") is not False
            and str((i.get("apply") or {}).get("kind") or "") in BOOK_KINDS
            for i in items or ()):
        for sid in BOOK_SERVICES:
            row = on_now(sid)
            if row is not None:
                add(_entry(row, "Find more knowledge uses it"))
    sent = {row["id"] for row in already_sent(shared, rows)}
    for sid in LOOK_KEPT:
        row = on_now(sid)
        if row is not None and sid in sent:
            add(_entry(row, "it looked for your jobs"))
    # 3. What the ticked lines use.
    for item in items or ():
        if not isinstance(item, dict) or item.get("on") is False:
            continue
        apply = item.get("apply") if isinstance(item.get("apply"), dict) else {}
        kind = str(apply.get("kind") or "")
        ref = str(apply.get("ref") or "")
        if kind == "door":
            row = rows.get(ref)
            if row is not None and (row.get("state") == "on"
                                    or row.get("can_switch")):
                add(_entry(row, "you ticked it on the plan"))
        elif kind in LIVE_KINDS:
            item_id = str(item.get("id") or ref)
            host = str(item.get("outside_host") or "")
            add({"id": CARD_PLACEHOLDER + item_id,
                 "name": str(item.get("label") or "A live card")[:80],
                 "websites": _names([host]), "why": "you ticked it on the plan",
                 "item_id": item_id})
        elif kind in KIND_SERVICES:
            for sid in KIND_SERVICES[kind]:
                row = on_now(sid)
                if row is not None:
                    add(_entry(row, "a line you ticked uses it"))
        elif kind in BOOK_KINDS:
            row = on_now(_book_service(str(item.get("outside_host") or ""),
                                       rows))
            if row is not None:
                add(_entry(row, "a book you ticked uses it"))
    return out


def _list_text(entries: list[dict]) -> str:
    parts = []
    for e in entries:
        sites = ", ".join(e.get("websites") or [])
        parts.append(f"{e['name']} ({sites})" if sites else e["name"])
    return "; ".join(parts)


def footer(*, shared: Any, items: Iterable[dict], reach: Any, name: str,
           mode: str, hosts_text: str = "") -> dict:
    """The plan's "What leaves your computer" words and the list behind them.

    ``{"line", "stay_on", "already", "count"}``. *hosts_text* is the plan's
    own list of places its ticked lines download from (said as "When you
    press Looks good")."""
    rows = _rows()
    kept = stay_on(shared, items, reach, rows=rows)
    sent = already_sent(shared, rows)
    parts = [_say("head", mode)]
    if sent:
        said = "; ".join(
            f"{s['name']}, {s['sent']} sent"
            + (f" to {', '.join(s['sites'])}" if s["sites"] else "")
            for s in sent)
        parts.append(_say("already", mode, list=said))
    # The plan looks for each job on these when she presses Looks good; they
    # stay on so the bot can find more later (as the old find step did).
    looks = finding_sites(rows, reach)
    if hosts_text:
        parts.append(_say("during", mode, hosts=hosts_text))
    if looks:
        parts.append(_say("looks", mode, list=", ".join(looks)))
    parts.append(_say("kept", mode, name=name, list=_list_text(kept)) if kept
                 else _say("none_kept", mode, name=name))
    sep = "\n" if mode == "simple" else " "
    return {"line": sep.join(parts), "stay_on": kept, "already": sent,
            "count": len(kept)}


def _expected_ids(kept: list[dict], record: Any) -> set[str]:
    """The list's ids, a live card's plan line mapped to the card it made."""
    made: dict[str, str] = {}
    for row in ((record or {}).get("items") or []) if isinstance(record, dict) else []:
        if isinstance(row, dict) and row.get("ok") and row.get("app_id"):
            made[str(row.get("id") or "")] = str(row["app_id"])
    out: set[str] = set()
    for e in kept:
        if e["id"].startswith(CARD_PLACEHOLDER):
            app_id = made.get(e.get("item_id") or "")
            if app_id:
                out.add("card-" + app_id)
        else:
            out.add(e["id"])
    return out


def settle(shared: Any, items: Iterable[dict], reach: Any, record: Any = None,
           *, switch_off: Optional[Callable[[str], Any]] = None) -> dict:
    """Switch off every outside service that is on and is not on the plan's
    list. Run once, after the build carried out the plan.

    Returns ``{"kept", "switched_off", "could_not", "named_but_off"}``, each
    a list of service ids.
    Never switches anything on. Never raises.
    """
    try:
        from puffpai import outside_services
        rows = {r["id"]: r for r in outside_services.rows()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("plan outside list: could not read the outside list "
                       "after the build: %s", type(exc).__name__)
        return {"kept": [], "switched_off": [], "could_not": ["(the list)"],
                "named_but_off": []}
    kept = stay_on(shared, items, reach, rows=rows)
    allowed = _expected_ids(kept, record)
    off = switch_off or (lambda sid: outside_services.switch_off(sid, by="wizard"))
    done: list[str] = []
    failed: list[str] = []
    for sid, row in rows.items():
        if row.get("state") != "on" or sid in allowed:
            continue
        try:
            off(sid)
            done.append(sid)
        except Exception as exc:  # noqa: BLE001 - one failure never stops the rest
            logger.warning("plan outside list: a service the plan did not name "
                           "could not be switched off: %s", type(exc).__name__)
            failed.append(sid)
    # A service the plan named that is not on now (the build could not add
    # its line, or a setting on this computer keeps it off): kept in the
    # build record, so a plan that said more than happened is seen.
    try:
        now_on = {r["id"] for r in outside_services.rows() if r["state"] == "on"}
    except Exception:  # noqa: BLE001
        now_on = set()
    return {"kept": sorted(allowed), "switched_off": done, "could_not": failed,
            "named_but_off": sorted(allowed - now_on)}


__all__ = ["BOOK_KINDS", "CARD_PLACEHOLDER", "FIND_KEPT", "KIND_SERVICES", "LIVE_KINDS",
           "LOOK_KEPT", "LOOK_SAID", "already_sent", "footer", "look_hours",
           "settle", "site_name", "stay_on"]
