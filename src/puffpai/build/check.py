"""Check: may this found piece be part of the build at all? (one build foundation)

``check(candidate) -> {"ok", "reason", "verdict", ...}`` runs before the setup
list and the Yes (and again, without downloading, inside make). It uses the
checks BuildBot already has, the same way from every path:

* **Drawbridge** (``puffpai.drawbridge``): a candidate that carries its
  record (``draft``: a live card, a book, a package) is checked with
  :func:`drawbridge.check_addition`; ``block`` stops it, ``hold`` and ``warn``
  go on with the reason shown on the setup list. Words that read like orders
  to a computer stop any candidate. A stored ``block`` always stands; a
  stored ``allow`` is never trusted (fail closed).
* **integrate/apply** for a code project (an APP, a command line repo): the
  project is downloaded at its pinned commit and checked
  (``apply.prepare_code_project``, adding nothing). ``refused`` / ``failed``
  stop it; ``checked`` gives the card make adds after the Yes (``prepared``).
* **Addresses**: a data source or MCP address must be an ordinary encrypted
  connection (https on 443, no sign-in in the address); an MCP command must be
  one plain program name with plain arguments (it is shown exactly on the
  setup list and runs only after the Yes).

What this does not do: judge whether a service is honest or its data true,
or see orders written in a way Drawbridge's patterns do not know.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

_PROGRAM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
_ARG_RE = re.compile(r"^[A-Za-z0-9_./:=@,+%-][A-Za-z0-9_./:=@,+% -]{0,199}\Z")
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
STOPPED = "Drawbridge stopped it"
PRIVATE_LATER = ("It is downloaded from your GitHub and checked by Drawbridge after your "
                 "yes; it is added only when the checks pass.")
#: A found app while downloads are not on yet (they come with her one Yes).
APP_LATER = ("It is downloaded after your yes and checked by Drawbridge; it is added and "
             "started only when the checks pass.")
DOWNLOADS = "code_download"


def downloads_now() -> bool:
    """May BuildBot download a code project right now (the bot may go online
    and the app downloads switch is on)? Fails closed."""
    try:
        from puffpai import add_more
        from puffpai.security import outside_switches
        return bool(add_more.online_allowed()) and not outside_switches.is_off(DOWNLOADS)
    except Exception:  # noqa: BLE001
        return False


def _hers(c: dict) -> bool:
    """One of her own GitHub projects (puffpai.github_account)."""
    try:
        from puffpai import github_account
        return github_account.is_hers(c)
    except Exception:  # noqa: BLE001
        return False


def _no(reason: str, **extra: Any) -> dict:
    return {"ok": False, "reason": str(reason or STOPPED), **extra}


def _yes(reason: str = "", **extra: Any) -> dict:
    return {"ok": True, "reason": str(reason or ""), **extra}


def address_problem(url: Any, *, loopback_ok: bool = False) -> str:
    """"" for an ordinary encrypted address (https on 443, no sign-in part),
    else the plain reason. *loopback_ok*: http on this computer is fine (a
    tool server BuildBot started itself)."""
    try:
        parts = urlsplit(str(url or ""))
        port = parts.port
    except ValueError:
        return "its address cannot be read"
    host = (parts.hostname or "").lower()
    if not host:
        return "its address cannot be read"
    if parts.username is not None or parts.password is not None:
        return "its address has a sign-in in it"
    if loopback_ok and host in _LOOPBACK and parts.scheme in ("http", "https"):
        return ""
    if parts.scheme != "https" or port not in (None, 443):
        return "its address is not an ordinary encrypted connection"
    return ""


def _command_problem(target: Any) -> str:
    if isinstance(target, (list, tuple)):
        words = [str(w) for w in target]
    else:
        words = str(target or "").split()
    if not words:
        return "it has no command"
    if not _PROGRAM_RE.match(words[0]):
        return "its command is not one plain program"
    if any(not _ARG_RE.match(w) for w in words[1:]):
        return "its command has parts BuildBot cannot show plainly"
    return ""


def _texts(candidate: dict) -> list[str]:
    return [str(candidate.get(k) or "") for k in ("name", "label", "about", "does",
                                                    "description")]


def check(candidate: Any, *, fetch: bool = True,
          prepare: Optional[Callable[[dict, Any], dict]] = None) -> dict:
    """``{"ok", "reason", "verdict"?, "prepared"?, "hold"?}`` for one
    candidate. Never raises. *fetch* False never downloads (make's re-check:
    a code project's files are re-checked by ``finish_prepared`` itself).
    *prepare* replaces ``apply.prepare_code_project`` (tests)."""
    if not isinstance(candidate, dict):
        return _no("it was not something BuildBot can read")
    try:
        return _check(candidate, fetch=fetch, prepare=prepare)
    except Exception as exc:  # noqa: BLE001 - a check that crashed is a stop
        logger.warning("build check: stopped (%s)", type(exc).__name__)
        return _no("the checks could not finish")


def _check(c: dict, *, fetch: bool, prepare: Optional[Callable]) -> dict:
    from puffpai import drawbridge
    db = c.get("drawbridge") if isinstance(c.get("drawbridge"), dict) else {}
    stored = c.get("verdict") or db.get("verdict")
    if stored == drawbridge.BLOCK or str(c.get("status") or "") == "refused":
        return _no(str(c.get("line") or db.get("line") or STOPPED), verdict=drawbridge.BLOCK)
    earlier = c.get("check") if isinstance(c.get("check"), dict) else {}
    if earlier and earlier.get("ok") is False:
        # The pipeline keeps each part's check: a no stays a no.
        return _no(str(earlier.get("reason") or STOPPED), verdict=drawbridge.BLOCK)
    if any(drawbridge.instruction_shaped(t) for t in _texts(c) if t):
        return _no("some of its words read like orders to a computer",
                   verdict=drawbridge.BLOCK)
    out: dict = {}
    if isinstance(c.get("draft"), dict):
        v = drawbridge.check_addition(c["draft"], by="build")
        if v.get("verdict") == drawbridge.BLOCK:
            return _no(str(v.get("line") or STOPPED), verdict=drawbridge.BLOCK)
        out = {"verdict": v.get("verdict"), "line": v.get("line")}
        if v.get("verdict") == drawbridge.HOLD:
            out["hold"] = True
    kind = str(c.get("type") or c.get("kind") or "").lower()
    cls = str(c.get("class") or "").upper()
    mcp = c.get("mcp")
    target = (mcp.get("url") or mcp.get("command")) if isinstance(mcp, dict) else mcp
    if target == "stdio":
        target = ""
    if target:
        if isinstance(target, str) and re.match(r"^https?://", target, re.I):
            why = address_problem(target, loopback_ok=bool(c.get("started_here")))
        else:
            why = _command_problem(target)
        if why:
            return _no(why, verdict=drawbridge.BLOCK)
    if kind == "skill":
        why = _skill_problem(c)
        if why:
            return _no(why, verdict=drawbridge.BLOCK)
    if c.get("url") and cls == "DATA" and kind not in ("book", "zim", "skill"):
        why = address_problem(c["url"])
        if why:
            return _no(why, verdict=drawbridge.BLOCK)
    if kind in ("code_project", "repo", "github", "app", "server_app", "browser_app",
                "web_app") or (cls == "APP" and c.get("find_id")):
        if c.get("app_id") and not c.get("prepared") and not c.get("integration_id"):
            return _yes(**out)         # already added here, with its own checks then
        return _code_project(c, out, fetch=fetch, prepare=prepare)
    return _yes(str(out.get("line") or "") if out.get("hold") else "", **out)


def _skill_problem(c: dict) -> str:
    """A remote skill's address must be ordinary and encrypted."""
    from puffpai import skills
    if isinstance(c.get("text"), str):
        how = skills.how_it_runs_text(c["text"], str(c.get("folder") or ""))
    else:
        how = skills.how_it_runs(c.get("skill") or c.get("folder") or c.get("name"))
    if how["kind"] == "remote" and how["endpoint"]:
        return address_problem(how["endpoint"])
    return ""


def _added_record(iid: str) -> bool:
    """Is *iid* a project already added here (its own record says ready),
    and not one Drawbridge stopped?"""
    if not iid:
        return False
    try:
        from puffpai import drawbridge
        from puffpai.integrate import records
        rec = records.get(iid) if records.valid_id(iid) else None
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(rec, dict) or rec.get("status") != "ready":
        return False
    return (rec.get("drawbridge") or {}).get("verdict") != drawbridge.BLOCK


#: Where a find is a code project (``build.find.CODE_FIND_SITES``).
CODE_FIND_SITES = ("awesome-selfhosted", "github", "codeberg")
#: Run plans that give an app its own screen on this computer.
SCREEN_HOWS = frozenset({"node_server", "python_server", "compose", "web_build_static"})


def cannot_start(host: Optional[dict]) -> str:
    """Why a checked project cannot run here as an app with its own screen
    ("" when it can, or when it is a page that needs no start). A program
    this computer still needs (Node.js, Docker) is no reason: it is a row on
    the setup list."""
    if not isinstance(host, dict):
        return ""
    how = str(host.get("how") or "")
    if not how:
        why = str(host.get("why_not") or "it does not say how it starts")
        return f"BuildBot cannot start it on this computer: {why}"
    if how not in SCREEN_HOWS:
        return "It has no screen of its own to open."
    return ""


def _forget(iid: str) -> None:
    """A checked download that will not be used is not kept."""
    if not iid:
        return
    try:
        from puffpai.integrate import records
        records.remove(iid, by="buildbot")
    except Exception:  # noqa: BLE001
        pass


def _code_project(c: dict, out: dict, *, fetch: bool, prepare: Optional[Callable]) -> dict:
    prepared = c.get("prepared") if isinstance(c.get("prepared"), dict) else {}
    if prepared.get("code_sha") and prepared.get("integration_id"):
        # Already checked: finish_prepared re-checks files, rows and plan itself.
        return _yes(prepared=prepared, **out)
    if _added_record(str(c.get("integration_id") or "")):
        return _yes(**out)
    hers = _hers(c)
    if hers and c.get("private"):
        # A private project of hers is downloaded and checked only after the
        # build's Yes covers its own row ("Use your private project <name>").
        return _yes(PRIVATE_LATER, private=True, **out)
    code_find = not hers and str(c.get("source") or "") in CODE_FIND_SITES
    if not fetch:
        if code_find and (c.get("check") or {}).get("later"):
            return _yes(APP_LATER, later=True, **out)    # make downloads and checks it now
        return _no("it was not checked yet")
    if code_find and prepare is None and not downloads_now():
        # Downloads come with her one Yes (a row on the list): the download
        # and Drawbridge's check happen then, before anything is added.
        return _yes(APP_LATER, later=True, **out)
    if prepare is None:
        from puffpai.integrate import apply
        prepare = apply.prepare_code_project
    item_id = str(c.get("find_id") or "")
    if not item_id and code_find:
        # A code site's or the app list's find: its project is read at its
        # current commit now, so the integrate path can download exactly it.
        from puffpai.build import find as find_mod
        item_id = find_mod.code_item(c)
        if not item_id:
            return _no("BuildBot could not read that project's current version")
    item_id = item_id or str(c.get("ref") or "")
    if hers:
        from puffpai import github_account
        item_id = github_account.register_found(c)
        if not item_id:
            return _no("BuildBot could not read that project on GitHub")
    item = {"id": item_id, "label": str(c.get("name") or "")}
    got = prepare(item, {"by": "owner"}) or {}
    status = str(got.get("status") or "")
    if status != "checked":
        return _no(str(got.get("line") or "it could not be checked"), status=status)
    card = got.get("card") if isinstance(got.get("card"), dict) else {}
    host = card.get("host") if isinstance(card.get("host"), dict) else None
    why = cannot_start(host)
    if why:
        _forget(str(got.get("integration_id") or ""))
        return _no(why, status="cannot_start")
    keep = {"integration_id": got.get("integration_id"), "code_sha": card.get("code_sha"),
            "host_sha": card.get("host_sha") or "", "mode": card.get("mode"),
            "lines": list(card.get("lines") or [])}
    if host:
        keep["how"] = str(host.get("how") or "")
        keep["needs"] = [str(p) for p in host.get("missing") or []]
    from puffpai import drawbridge
    hold = card.get("verdict") not in ("", None, drawbridge.ALLOW)
    return _yes(str(card.get("hold_words") or "") if hold else "", prepared=keep,
                verdict=card.get("verdict"), hold=hold)


__all__ = ["APP_LATER", "CODE_FIND_SITES", "PRIVATE_LATER", "downloads_now", "SCREEN_HOWS", "STOPPED", "address_problem",
           "cannot_start", "check"]
