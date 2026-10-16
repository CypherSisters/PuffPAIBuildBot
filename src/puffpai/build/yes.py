"""Yes: ONE consent record for the whole setup list and the making.

Her tap goes the way every yes here goes (:mod:`puffpai.assemble.consent`
does the same per app): the server's own sentence, a consent ticket minted
and spent at once for this paired device and exactly this list (``add_on``,
``build:<sha16>``; both written to the audit trail by
:mod:`puffpai.security.consent_tickets`), one audit line, then
``yes: {"given", "at", "sha", "by"}`` in build.json.

The yes covers exactly what she was shown. When the list changes (Find more
adds parts, a new job), :func:`covers` is False for the new list and those
jobs wait for a yes of their own; jobs already built keep going.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

from puffpai.build import schema as S
from puffpai.build.setup import SEED_PREFIX
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

ACTION = "add_on"
BUTTON = "Yes, build all of this"
WRITES = "BuildBot installs what is listed, writes small connecting code and tests it on this computer."


class YesError(Exception):
    def __init__(self, line: str, status: int = 400):
        super().__init__(line)
        self.line = line
        self.status = status


def _open_jobs(build: dict) -> list[dict]:
    return [j for j in build.get("jobs") or [] if j.get("route") != S.D]


#: What of a setup row her yes is for: what it is AND everything it reaches
#: or runs, so a row whose host, service, address or command changes is a new
#: list (``done`` and the installed folder change after the yes; not in it).
ROW_FINGERPRINT = ("id", "kind", "label", "host", "hosts", "service", "mcp_url", "command",
                   "app_id", "name", "step", "make", "ref")


def _row_print(r: dict) -> list:
    out = [r.get(k) if not isinstance(r.get(k), list) else sorted(str(x) for x in r[k])
           for k in ROW_FINGERPRINT]
    if r.get("make") == "approve":
        # The exact commands Toolsmith may run beyond an app's folder, and
        # which of them run with her admin yes: a change is a new list.
        out += [sorted(str(x) for x in r.get("commands") or []),
                sorted(str(x) for x in r.get("admin") or [])]
    if r.get("make") == "make_code":
        # The code projects Toolsmith may make an app from: a change is a new list.
        out += [sorted(str(x) for x in r.get("refs") or [])]
    return out


def sha(build: dict) -> str:
    """What the yes is for: each job's route and parts, and every setup row
    with every site and program it names."""
    body = {"jobs": [[j["id"], j.get("route"), (j.get("pick") or {}).get("ref"),
                      list(j.get("ingredients") or [])] for j in _open_jobs(build)],
            # "<job> also needs <host>" rows come after a yes and have a yes
            # of their own (their tap); they pause only their own jobs.
            "setup": sorted((_row_print(r) for r in build.get("setup") or []
                             if not r.get("also")),
                            key=lambda x: json.dumps(x, sort_keys=True, default=str))}
    if build.get("first_apps"):
        # Her first apps the list turns on (puffpai.first_apps).
        body["first_apps"] = sorted(str(i.get("id")) for i in build["first_apps"]
                                    if isinstance(i, dict))
    if build.get("knowledge"):
        # The knowledge lines she ticked (build.knowledge): a change is a new list.
        body["knowledge"] = sorted(str(i.get("id")) for i in build["knowledge"]
                                   if isinstance(i, dict) and i.get("ticked"))
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()


def target(digest: str) -> str:
    return f"build:{digest[:16]}"


def _books(build: dict) -> list[str]:
    """The knowledge lines her Yes adds (ticked, not here yet), as shown."""
    return [f"{i.get('title')} ({i.get('site')}, {i.get('size_words')})"
            for i in build.get("knowledge") or [] if isinstance(i, dict)
            and i.get("ticked") and i.get("status") != "added"]


def _first_apps(build: dict) -> list[str]:
    """The first apps her Yes turns on, as shown ("Turn on: FitFam (...)")."""
    from puffpai import first_apps
    return first_apps.lines(build)


SENTENCE_MAX = 600


def sentence(build: dict) -> str:
    """The consent ticket's words. The books it adds come first and are never
    cut; the generic text after them is what gives way."""
    parts = build.get("parts") or {}
    jobs = "; ".join(f"{j['name']}: {S.how_words(j, parts)}"
                     + (f" ({parts.get(j['pick']['ref'], {}).get('name')})"
                        if parts.get((j.get("pick") or {}).get("ref"))
                        and (j.get("pick") or {}).get("class") not in S.ANSWER_CLASSES else "")
                     for j in _open_jobs(build)) or "none"
    rows = "; ".join([r["label"] for r in build.get("setup") or []]
                     + _first_apps(build)) or "nothing"
    books = "; ".join(_books(build))
    head = f"Build all of this? Add to the knowledge base: {books}." if books         else "Build all of this?"
    rest = f" Jobs: {jobs}. Setup: {rows}. {WRITES}"
    return head + rest[:max(0, SENTENCE_MAX - len(head))]


def covers(build: dict) -> bool:
    """True only when her recorded yes is for exactly this list."""
    y = build.get("yes") or {}
    return bool(y.get("given")) and y.get("sha") == sha(build)


def reset(build: dict) -> None:
    """The list changed: the old yes is kept in history, a new one is needed."""
    y = build.get("yes") or {}
    if y.get("given"):
        build.setdefault("yes_history", []).append(dict(y))
    build["yes"] = {"given": False, "at": None, "sha": sha(build), "by": ""}


def _audit(result: str, details: str) -> None:
    try:
        from puffpai.security.audit_log import AuditLog
        AuditLog().log(action="build_yes", user="owner", result=result, details=details)
    except Exception as exc:  # noqa: BLE001 - the ticket's own lines are kept
        logger.debug("build yes audit line skipped: %s", exc)


def give(device_id: str, *, seen_sha: str = "") -> dict:
    """Her one yes from a paired device. Returns the new build. Raises
    :class:`YesError`. *seen_sha*: the list she was shown; when given and the
    list changed since, nothing is recorded."""
    from puffpai.security import consent_tickets
    if not consent_tickets.usable_device(device_id):
        raise YesError("Open this from a browser that is paired with your bot.", 403)
    build = S.load_build()
    if not _open_jobs(build) and not _books(build) and not _first_apps(build):
        raise YesError("There is nothing to build yet.", 404)
    digest = sha(build)
    if seen_sha and seen_sha != digest:
        raise YesError("The list changed since you saw it. Look again.", 409)
    if covers(build):
        return build
    tgt = target(digest)
    try:
        ticket = consent_tickets.mint(ACTION, tgt, sentence(build), device_id)
    except consent_tickets.TicketRefused as exc:
        raise YesError(exc.message, 429 if exc.code == "throttled" else 403) from None
    ok, verdict = consent_tickets.redeem(ticket, device_id, ACTION, tgt)
    if not ok:
        raise YesError(consent_tickets.refusal_words(verdict), 403)

    def record(b: dict) -> None:
        if sha(b) != digest:
            raise YesError("The list changed since you saw it. Look again.", 409)
        old = b.get("yes") or {}
        if old.get("given"):
            b.setdefault("yes_history", []).append(dict(old))
        # The paired device her yes came from: a site the list showed for an
        # app that is added only after the yes is switched on for it then,
        # with a ticket for this same device (switch_app_sites).
        b["yes"] = {"given": True, "at": S.now_iso(), "sha": digest, "by": "owner",
                    "device": device_id, "sites": []}
    build = S.update_build(record)
    _audit("yes", tgt)
    try:
        # With the vault open now, Toolsmith's key is made ready for the
        # session (cloud Toolsmith on only), so a later idle lock never pauses
        # the build (security/toolsmith_cloud.py, the session key lease).
        from puffpai.security import toolsmith_cloud
        toolsmith_cloud.take_for_yes()
    except Exception:  # noqa: BLE001 - a run takes it later if it can
        pass
    return build


# ── the outside sites her one yes switches on ──────────────────────────────
#
# Each switch and address row SHOWN on the list is covered by her one yes,
# and nothing else: every site still goes the way it goes everywhere else
# (the Bridge page, fix cards): the server's own sentence, a consent ticket
# for this device and exactly that site, minted and spent at once (both
# written to the audit trail), then the switch. What happened to each is kept
# in the yes record (``yes.sites``); a site that could not be switched on
# keeps its row open with the reason.

#: ``yes.sites[].state``.
SITE_ON, SITE_WHEN_BUILT, SITE_NOT_ON = "on", "when_built", "not_on"


def _listed_rows(build: dict) -> list[dict]:
    open_ids = {j["id"] for j in _open_jobs(build)}
    return [r for r in build.get("setup") or []
            if r.get("kind") in ("switch", "address") and set(r.get("job_ids") or []) & open_ids]


def row_hosts(row: dict) -> list[str]:
    """The sites one row names (``hosts``, else its one ``host``)."""
    got = [str(h).lower() for h in row.get("hosts") or [] if isinstance(h, str) and h]
    return got or ([str(row["host"]).lower()] if row.get("host") else [])


def listed_hosts(build: dict, row: dict, *, with_also: bool = False) -> set[str]:
    """Every site the list shows, and she said yes to, for the part *row* is
    for: its rows' sites, and an "also needs" row's sites once she switched
    that row on (*with_also*: the tap on that row, its own yes). A switch
    may reach these, and nothing else."""
    ref = str(row.get("ref") or "")
    out = set(row_hosts(row))
    if ref:
        for r in _listed_rows(build):
            if str(r.get("ref") or "") != ref:
                continue
            if r.get("also"):
                if r.get("done") or with_also:
                    out.update(row_hosts(r))
            elif r.get("service") == row.get("service"):
                out.update(row_hosts(r))
    return out


def _site(row: dict, state: str, reason: str = "", **extra: object) -> dict:
    return {"row": str(row.get("id") or ""), "kind": str(row.get("kind") or ""),
            "ref": str(row.get("ref") or ""), "host": str(row.get("host") or ""),
            "service": str(row.get("service") or ""), "app_id": str(row.get("app_id") or ""),
            "state": state, "reason": str(reason or "")[:200], "at": S.now_iso(), **extra}


def _host_on(app_id: str, host: str, device: str) -> dict:
    """One site of an app's place on the Bridge switched on: the server's
    sentence, an owner ``app_host_on`` ticket for this device and this site,
    spent by ``egress_doors.set_app_web_hosts`` (one site per call; what is
    on stays on)."""
    from puffpai.security import consent_tickets, egress_doors
    try:
        if egress_doors.app_web_hosts(app_id).get(host):
            return {"ok": True, "line": f"{host} is allowed."}
        target = consent_tickets.target_app_host(app_id, host)
        ticket = consent_tickets.mint("app_host_on", target,
                                      f"Let {app_id} reach {host} while it runs.", device)
        on = [h for h, v in egress_doors.app_web_hosts(app_id).items() if v]
        got = egress_doors.set_app_web_hosts(app_id, on + [host], by=device, ticket=ticket)
    except (ValueError, consent_tickets.TicketRefused) as exc:
        return {"ok": False, "message": str(getattr(exc, "message", "") or exc)}
    if not got.get("ok"):
        return {"ok": False, "message": str(got.get("message") or "It was not allowed.")}
    return {"ok": True, "line": f"{host} is allowed."}


def allow_app_hosts(app_id: str, hosts: list[str], device: str, *,
                    was_on: Optional[list[str]] = None) -> dict[str, dict]:
    """Switch *hosts* on for a browser app, the way the Bridge page does.

    The Bridge keys her yeses by an app's WHOLE site set, so giving the app a
    bigger set turns every site it had off. So: the set is given once, with
    every site needed, and then each site is switched on in turn with its own
    ticket: the new ones, and the ones that were on before (*was_on*, read
    here when not given), so a site she had on stays on. ``{host: {"ok",
    "line"|"message"}}``."""
    from puffpai.security import egress_doors
    now = egress_doors.app_web_hosts(app_id)
    have = list(now)
    before = [h for h in (was_on if was_on is not None else [h for h, v in now.items() if v])
              if isinstance(h, str)]
    want = list(dict.fromkeys(have + [h for h in hosts if isinstance(h, str)]))
    out: dict[str, dict] = {}
    if len(want) > egress_doors.MAX_APP_DOOR_HOSTS:
        fit = want[:egress_doors.MAX_APP_DOOR_HOSTS]
        for h in want[egress_doors.MAX_APP_DOOR_HOSTS:]:
            out[h] = {"ok": False, "message": f"An app may reach at most "
                                              f"{egress_doors.MAX_APP_DOOR_HOSTS} outside "
                                              f"sites, so {h} cannot be added."}
        want = fit
    if want != have:
        try:
            egress_doors.register_web_app_door(app_id, want)
        except ValueError as exc:
            return {h: {"ok": False, "message": str(exc)} for h in hosts}
        try:
            from puffpai.build import make
            make.keep_sites(app_id, want)
        except Exception:  # noqa: BLE001 - kept for the next start only
            pass
    for h in dict.fromkeys([h for h in before if h in want] + [h for h in hosts if h in want]):
        out[h] = _host_on(app_id, h, device)
    return out


def allow_app_host(app_id: str, host: str, device: str) -> dict:
    """One outside site on for a browser app (:func:`allow_app_hosts`)."""
    return allow_app_hosts(app_id, [host], device).get(
        host, {"ok": False, "message": "It was not allowed."})


def _seed_on(row: dict, device: str, listed: set[str],
             hosts: Optional[set[str]] = None) -> dict:
    """A website from BuildBot's own list: added as a live card with its
    switch on (``assemble.seed_switch``, as its fix card does). Its
    ``add_on`` ticket names exactly this website. The one look it takes
    first needs a switch too; that one must be on the list as well. The card
    made from that look may reach ONLY the sites the list shows for it
    (*hosts*); one that needs another site (a picture host) is not added,
    and the site comes back as ``needs`` for a new row."""
    from puffpai import fix_api
    from puffpai.assemble import seed_switch
    from puffpai.security import consent_tickets
    seed = str(row.get("service") or "").split(":", 1)[-1]
    card = seed_switch.card(seed)
    if card is None:
        return _site(row, SITE_NOT_ON, "it is not on BuildBot's own list of websites")
    missing = [n for n in seed_switch.needed_switches() if n["id"] not in listed]
    if missing:
        return _site(row, SITE_NOT_ON, f"it also needs \"{missing[0]['words']}\", "
                                       f"which was not on your list")
    sid = card["service"]
    try:
        ticket = consent_tickets.mint(ACTION, sid, card["detail"], device)
    except consent_tickets.TicketRefused as exc:
        return _site(row, SITE_NOT_ON, exc.message)
    ok, verdict = consent_tickets.redeem(ticket, device, ACTION, sid)
    if not ok:
        return _site(row, SITE_NOT_ON, consent_tickets.refusal_words(verdict))
    allowed = sorted(hosts if hosts is not None else set(row_hosts(row)))
    got, _status = seed_switch.apply(seed, device, reassemble=False, hosts=allowed,
                                     result_pictures=True)
    if not got.get("ok"):
        needs = [str(h) for h in got.get("needs_hosts") or [] if isinstance(h, str)]
        return _site(row, SITE_NOT_ON, str(got.get("message") or "it was not switched on"),
                     needs=needs)
    fix_api._audit("chat_fix_switch_on", "on", sid)  # noqa: SLF001 - the same line as its card
    if got.get("app_id") and row.get("ref"):
        _note_live_card(str(row["ref"]), str(got["app_id"]))
    return _site(row, SITE_ON, target=sid)


def _note_live_card(ref: str, app_id: str) -> None:
    """The live card made for a website is an ingredient of its job only:
    kept on its part, so the pages place it in the job (no page, no tile, no
    sidebar entry of its own; its old address opens the job's page)."""
    def change(b: dict) -> None:
        part = (b.get("parts") or {}).get(ref)
        if isinstance(part, dict):
            part["live_card"] = app_id
    try:
        S.update_build(change)
    except Exception as exc:  # noqa: BLE001 - switched on; placing it is cosmetic
        logger.info("build yes: live card not placed (%s)", type(exc).__name__)


def _bridge_sites(service: str) -> Optional[list[str]]:
    """The sites a Bridge switch for a live card or data source opens (its
    row's websites), or None for a switch that is not one site set (a
    built-in switch such as the one look before adding a website)."""
    try:
        from puffpai import outside_services
        got = outside_services.row(service)
    except Exception:  # noqa: BLE001 - unreadable: treated as unknown sites
        return []
    if not got or got.get("kind") != "card":
        return None if got else []
    return [str(h).lower() for h in got.get("websites") or []]


def _add_needs(b: dict, needs: list) -> list[str]:
    """Inside an update of *b*: each ``(site record, host)`` a part turned
    out to need goes on its source's ONE "also needs" row, and only the jobs
    that use that part wait (needs_setup, with the plain reason). Returns
    the ids of the rows added or grown."""
    from puffpai.build import setup as setup_mod
    added: list[str] = []
    for s, host in needs:
        got = setup_mod.also_needs_row(b, str(s.get("ref") or ""), host,
                                       str(s.get("service") or ""))
        if got is not None and got["id"] not in added:
            added.append(got["id"])
        if str(s.get("service") or "").startswith(SEED_PREFIX):
            setup_mod.note_media_hosts(str(s.get("ref") or ""), [host])
    for j in b.get("jobs") or []:
        also = setup_mod.pending_also(j, b.get("setup") or [])
        if also and j.get("status") not in (S.WORKING, S.BUILDING):
            j["status"] = S.NEEDS_SETUP
            j["reason"] = setup_mod.also_reason(also)
    return added


def _note_needs(site: dict) -> None:
    """A single tap (not her one Yes) found a site the part also needs:
    recorded the same way. Never raises."""
    needs = [(site, h) for h in site.get("needs") or [] if isinstance(h, str)]
    if not needs:
        return
    try:
        S.update_build(lambda b: _add_needs(b, needs))
    except Exception as exc:  # noqa: BLE001 - the refusal itself stands
        logger.info("build yes: also-needs row not kept (%s)", type(exc).__name__)


def _also_on(row: dict, device: str, listed: set[str], build: dict) -> dict:
    """Her tap on an "also needs" row (its own ticket, minted by the switch
    below): the part's own switch, now with the sites that row names too."""
    main = next((r for r in build.get("setup") or [] if not r.get("also")
                 and r.get("kind") == "switch" and r.get("ref") == row.get("ref")
                 and r.get("service") == row.get("service")), None)
    if main is None:
        return _site(row, SITE_NOT_ON, "the part that needs it is no longer on the list")
    got = _switch_one(main, device, listed, build, with_also=True, note=False)
    if got["state"] != SITE_ON:
        return _site(row, got["state"], got.get("reason") or "", needs=got.get("needs") or [])

    def mark(b: dict) -> None:
        for r in b.get("setup") or []:
            if r.get("id") == main["id"]:
                r["done"] = True
    S.update_build(mark)
    return _site(row, SITE_ON, target=str(got.get("target") or ""))


def _switch_one(row: dict, device: str, listed: set[str],
                build: Optional[dict] = None, *, with_also: bool = False,
                note: bool = True) -> dict:
    """One switch or address row on, under her yes: never wider than the
    sites the list shows for it (:func:`listed_hosts`). A site it turns out
    to need goes on an "also needs" row (*note* False: the caller does it)."""
    got = _switch_one_now(row, device, listed, build, with_also=with_also)
    if note and got.get("state") == SITE_NOT_ON and got.get("needs"):
        _note_needs(got)
    return got


def _switch_one_now(row: dict, device: str, listed: set[str],
                    build: Optional[dict] = None, *, with_also: bool = False) -> dict:
    from puffpai import fix_api
    build = build if build is not None else S.load_build()
    if row.get("also"):
        return _also_on(row, device, listed, build)
    hosts = listed_hosts(build, row, with_also=with_also)
    if row["kind"] == "switch":
        service = str(row.get("service") or "")
        if service.startswith(SEED_PREFIX):
            return _seed_on(row, device, listed, hosts)
        if not fix_api._SID_RE.match(service):  # noqa: SLF001 - the same rule as its card
            return _site(row, SITE_NOT_ON, "the Bridge has no switch for it yet")
        sites = _bridge_sites(service)
        if sites is not None:
            extra = [h for h in dict.fromkeys(sites) if h not in hosts]
            if extra or not sites:
                return _site(row, SITE_NOT_ON,
                             (f"it would also reach {', '.join(extra)}, which was not on "
                              f"your list" if extra else "the Bridge does not say which "
                                                         "sites it reaches"), needs=extra)
        got, _status = fix_api.apply_switch(service, device)
        if not got.get("ok"):
            return _site(row, SITE_NOT_ON, str(got.get("message") or "it was not switched on"))
        return _site(row, SITE_ON, target=service)
    if row.get("mcp_url"):
        # A tool server: connected right after the yes, when its job is made.
        return _site(row, SITE_WHEN_BUILT, "it is connected when its job is built")
    if row.get("app_id") and row.get("host"):
        got = allow_app_host(str(row["app_id"]), str(row["host"]), device)
        if not got.get("ok"):
            return _site(row, SITE_NOT_ON, str(got.get("message") or "it was not allowed"))
        return _site(row, SITE_ON)
    if "app_id" in row and row.get("host"):
        # An app added only after the yes: its site is switched on for it then.
        return _site(row, SITE_WHEN_BUILT, "it is switched on when the app is added")
    return _site(row, SITE_NOT_ON, "the Bridge has no switch for it yet")


def _ticket_pairs(rows: list[dict]) -> list[tuple[str, str]]:
    """The exact ``(action, target)`` tickets switching *rows* on mints:
    the ones her one Yes batches into one consent record."""
    from puffpai.assemble import seed_switch
    from puffpai.security import consent_tickets, egress_doors
    pairs: list[tuple[str, str]] = []
    services = {str(r.get("service") or "") for r in rows if r.get("kind") == "switch"}
    for r in rows:
        service = str(r.get("service") or "")
        if r.get("kind") == "switch" and service and not r.get("also"):
            if service.startswith(SEED_PREFIX):
                pairs.append((ACTION, seed_switch.PREFIX + service[len(SEED_PREFIX):]))
                # the one look first, when the list names its switch too
                pairs += [("outside_on", sid) for sid, _w in seed_switch.NEEDED
                          if sid in services]
            else:
                pairs.append(("outside_on", service))
        elif r.get("kind") == "address" and r.get("app_id") and r.get("host") \
                and not r.get("mcp_url"):
            app_id = str(r["app_id"])
            hosts = [str(r["host"])]
            try:     # a site she had on is switched on again with the bigger set
                hosts += [h for h, v in egress_doors.app_web_hosts(app_id).items() if v]
            except Exception:  # noqa: BLE001
                pass
            for h in hosts:
                try:
                    pairs.append(("app_host_on", consent_tickets.target_app_host(app_id, h)))
                except ValueError:
                    continue
    return list(dict.fromkeys(pairs))


def switch_sites(device: str) -> list[dict]:
    """After her yes: switch on every switch and address row the list shows
    (that is not on yet), in order: the Bridge's own switches first (a website
    from BuildBot's list needs its look switch on before it is added), then
    websites, then app sites. Nothing that is not on the list, and no switch
    wider than the sites its row shows: a part that turns out to need another
    site gets a new row ("<job> also needs <host>") that the next yes covers.
    All the tickets are one consent record for this yes
    (``consent_tickets.yes_batch``), each still spent and audited on its own,
    with one audit line per site here too. Returns ``yes.sites``. Never
    raises."""
    from puffpai.security import consent_tickets
    build = S.load_build()
    if not covers(build):
        return []
    digest = str((build.get("yes") or {}).get("sha") or "")
    rows = _listed_rows(build)
    listed = {str(r.get("service") or "") for r in rows if r["kind"] == "switch"}
    todo = [r for r in rows if not r.get("done") and not r.get("also")]

    def order(r: dict) -> int:
        if r["kind"] == "switch":
            return 1 if str(r.get("service") or "").startswith(SEED_PREFIX) else 0
        return 2
    out: list[dict] = []
    by_app: dict[str, list[dict]] = {}
    batch = consent_tickets.yes_batch(device, _ticket_pairs(todo), label=target(digest))
    try:
        batch.__enter__()
    except consent_tickets.TicketRefused as exc:
        batch = None                # each site then mints on its own, throttled as always
        logger.info("build yes: no batch for the sites (%s)", exc.code)
    try:
        for row in sorted(rows, key=order):
            if row.get("also"):
                continue                    # with the row it belongs to
            if row.get("done"):
                out.append(_site(row, SITE_ON, "it was on already"))
                continue
            if row["kind"] == "address" and row.get("app_id") and row.get("host") \
                    and not row.get("mcp_url"):
                by_app.setdefault(str(row["app_id"]), []).append(row)   # one set per app
                continue
            try:
                out.append(_switch_one(row, device, listed, build, note=False))
            except Exception as exc:  # noqa: BLE001 - one site never stops the others
                logger.warning("build yes: a site was not switched on (%s)",
                               type(exc).__name__)
                out.append(_site(row, SITE_NOT_ON, f"it stopped ({type(exc).__name__})"))
        for app_id, app_rows in by_app.items():
            try:
                got = allow_app_hosts(app_id, [str(r["host"]) for r in app_rows], device)
            except Exception as exc:  # noqa: BLE001
                logger.warning("build yes: app sites not switched on (%s)", type(exc).__name__)
                got = {}
            for r in app_rows:
                one = got.get(str(r["host"])) or {"ok": False, "message": "it was not allowed"}
                out.append(_site(r, SITE_ON) if one.get("ok") else
                           _site(r, SITE_NOT_ON, str(one.get("message") or "it was not allowed")))
    finally:
        if batch is not None:
            batch.__exit__(None, None, None)
    on = {s["row"] for s in out if s["state"] == SITE_ON}
    needs = [(s, h) for s in out if s["state"] == SITE_NOT_ON
             for h in s.get("needs") or [] if isinstance(h, str)]
    added: list[str] = []

    def keep(b: dict) -> None:
        if sha(b) != digest or not (b.get("yes") or {}).get("given"):
            return                      # the list changed meanwhile: not recorded
        b["yes"]["sites"] = out
        for r in b.get("setup") or []:
            if r["id"] in on:
                r["done"] = True
        # A site a part turned out to need: a new row, never switched on now.
        # The changed list waits for her yes.
        added.extend(_add_needs(b, needs))
    S.update_build(keep)
    for s in out:
        _audit("site", f"{target(digest)}: {s['row']} {s['state']}"[:300])
    _audit("sites", f"{target(digest)}: {len(on)} on, "
                    f"{sum(1 for s in out if s['state'] == SITE_WHEN_BUILT)} when built, "
                    f"{sum(1 for s in out if s['state'] == SITE_NOT_ON)} not on"
                    + (f", {len(added)} new rows" if added else ""))
    return out


def switch_app_sites(ref: str, app_id: str, build: dict, *,
                     was_on: Optional[list[str]] = None) -> list[str]:
    """Make, after it added the app *ref* as *app_id*: switch on the sites
    the list showed for it (``when_built`` in ``yes.sites``), with a ticket
    for the device her yes came from. *was_on*: the sites the app had on
    before make gave it a bigger set; they are switched on again too.
    Returns the hosts now on."""
    if not covers(build):
        return []
    y = build.get("yes") or {}
    device = str(y.get("device") or "")
    wanted = [s for s in y.get("sites") or [] if isinstance(s, dict)
              and s.get("state") == SITE_WHEN_BUILT and s.get("ref") == ref
              and s.get("kind") == "address" and s.get("host")]
    if not device or not (wanted or was_on):
        return []
    from puffpai.security import consent_tickets
    pairs = []
    for h in [str(s["host"]) for s in wanted] + [str(h) for h in was_on or []]:
        try:
            pairs.append(("app_host_on", consent_tickets.target_app_host(app_id, h)))
        except ValueError:
            continue
    batch = consent_tickets.yes_batch(device, pairs, label=target(str(y.get("sha") or "")))
    try:
        batch.__enter__()
    except consent_tickets.TicketRefused:
        batch = None                # each site then mints on its own, throttled as always
    try:
        got = allow_app_hosts(app_id, [str(s["host"]) for s in wanted], device,
                              was_on=list(was_on or []))
    finally:
        if batch is not None:
            batch.__exit__(None, None, None)
    done: dict[str, dict] = {}
    for s in wanted:
        one = got.get(str(s["host"])) or {}
        done[s["row"]] = _site({"id": s["row"], "kind": "address", "ref": ref,
                                "host": s["host"], "app_id": app_id},
                               SITE_ON if one.get("ok") else SITE_NOT_ON,
                               "" if one.get("ok") else str(one.get("message") or ""))
    digest = str(y.get("sha") or "")

    def keep(b: dict) -> None:
        if sha(b) != digest:
            return
        sites = (b.get("yes") or {}).get("sites") or []
        b["yes"]["sites"] = [done.get(s.get("row"), s) for s in sites]
        for r in b.get("setup") or []:
            if r["id"] in done and done[r["id"]]["state"] == SITE_ON:
                r["done"] = True
    S.update_build(keep)
    return [h for h, one in got.items() if one.get("ok")]


def sites_public(build: dict) -> list[dict]:
    """What the pages show of ``yes.sites`` (no device, no ticket)."""
    if not covers(build):
        return []
    return [{"row": s.get("row"), "host": s.get("host"), "state": s.get("state"),
             "reason": s.get("reason") or ""}
            for s in (build.get("yes") or {}).get("sites") or [] if isinstance(s, dict)]


def public(build: dict) -> dict:
    y = build.get("yes") or {}
    return {"given": covers(build), "at": y.get("at") if covers(build) else None,
            "sha": sha(build), "button": BUTTON}


# ── pictures a source's results point to, at run time ──────────────────────

_RUNTIME_NOTED: set[tuple[str, str]] = set()


def _job_of_app(build: dict, app_id: str) -> Optional[dict]:
    from puffpai.build import make
    for j in _open_jobs(build):
        try:
            if make.app_id_of(j) == app_id:
                return j
        except Exception:  # noqa: BLE001
            continue
    return None


def _source_ref(build: dict, job: dict, door: str) -> tuple[str, str]:
    """``(ref, service)`` of the job's data source behind *door* (its first
    data source when the door is not known)."""
    from puffpai.build import sources
    from puffpai.security import egress_doors
    rows = [r for r in build.get("setup") or [] if r.get("kind") == "switch"
            and not r.get("also") and job["id"] in (r.get("job_ids") or []) and r.get("ref")]
    for r in rows:
        doors = {sources.door_id(h) for h in row_hosts(r)} \
            if sources.is_source(r.get("service")) else set()
        card = str(((build.get("parts") or {}).get(r["ref"]) or {}).get("live_card") or "")
        if card:
            try:
                doors.add(egress_doors.app_door_id(card))
            except Exception:  # noqa: BLE001 - not a usable app id
                pass
        if door and door in doors:
            return str(r["ref"]), str(r.get("service") or "")
    if rows:
        return str(rows[0]["ref"]), str(rows[0].get("service") or "")
    return "", ""


def note_runtime_need(app_id: str, host: str, door: str = "") -> Optional[dict]:
    """A source's OWN result pointed at *host* and it needs more than a
    picture: ONE "also needs" row for that job, asked once per run; only that
    job waits. The host must be one the source behind *door* named in its own
    answers for this app (assemble.server.host.result_hosts): a page can
    never name one. Returns the row, or None."""
    from puffpai.security import egress_doors
    host = str(host or "").strip().rstrip(".").lower()
    if not egress_doors.is_registrable_hostname(host) or (app_id, host) in _RUNTIME_NOTED:
        return None
    try:
        from puffpai.assemble.server import host as host_mod
        named = host_mod.result_hosts(str(app_id), str(door or ""))
    except Exception:  # noqa: BLE001 - unknown: not from its results
        named = set()
    if not door or host not in named:
        logger.info("build yes: a site not from the source's own answer was not listed")
        return None
    _RUNTIME_NOTED.add((str(app_id), host))
    got: dict = {}

    def change(b: dict) -> None:
        job = _job_of_app(b, str(app_id))
        if job is None:
            return
        ref, service = _source_ref(b, job, str(door or ""))
        if not ref:
            return
        site = {"ref": ref, "service": service}
        added = _add_needs(b, [(site, host)])
        if added:
            got.update(next(r for r in b["setup"] if r["id"] == added[0]))
    try:
        S.update_build(change)
    except Exception as exc:  # noqa: BLE001 - the refusal itself stands
        logger.info("build yes: runtime row not kept (%s)", type(exc).__name__)
        return None
    return got or None


def _ref_door(build: dict, ref: str) -> str:
    """The Bridge switch (door) the data source *ref* is read through."""
    from puffpai.build import sources
    from puffpai.security import egress_doors
    card = str(((build.get("parts") or {}).get(ref) or {}).get("live_card") or "")
    if card:
        try:
            return egress_doors.app_door_id(card)
        except Exception:  # noqa: BLE001
            return ""
    for r in build.get("setup") or []:
        if r.get("ref") == ref and not r.get("also") and r.get("host") \
                and sources.is_source(r.get("service")):
            return sources.door_id(str(r["host"]))
    return ""


def confirmed_picture_hosts(app_id: str) -> dict[str, str]:
    """``{site: source door}``: sites she switched on from an "also needs"
    row of the job that made *app_id*, each with the switch of the source
    that row is for (pictures may come from there too, under it)."""
    try:
        build = S.load_build()
    except Exception:  # noqa: BLE001
        return {}
    job = _job_of_app(build, str(app_id))
    if job is None:
        return {}
    out: dict[str, str] = {}
    for r in build.get("setup") or []:
        if r.get("also") and r.get("done") and job["id"] in (r.get("job_ids") or []):
            door = _ref_door(build, str(r.get("ref") or ""))
            for h in row_hosts(r):
                if door:
                    out[h] = door
    return out


def _reset_runtime_for_tests() -> None:
    _RUNTIME_NOTED.clear()


__all__ = ["ACTION", "BUTTON", "SITE_NOT_ON", "SITE_ON", "SITE_WHEN_BUILT", "YesError",
           "allow_app_host", "allow_app_hosts", "covers", "give", "public", "reset", "sentence", "sha",
           "sites_public", "switch_app_sites", "switch_sites", "target",
           "confirmed_picture_hosts", "note_runtime_need"]
