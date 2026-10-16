"""The one yes per assembled app.

The plan promised ONE yes per assembled app before anything of it starts.
Until the owner taps Start on that app's card:

* nothing is fetched for it, no connecting code is written or run, and its
  own server does not start (:func:`has_yes` is asked by the run in
  :mod:`puffpai.assemble.api`, by ``toolsmith_loop.run_toolsmith`` and
  ``run_glue``, and by ``server.host.prepare_start`` and ``call_action``,
  so every way in is refused, whoever calls it);
* the status says "Waiting for your yes".

The card (:func:`card`) says, in plain words, which sites the app's parts use,
which outside code it uses (with where it comes from and its language), and
"BuildBot writes small connecting code and tests it on this computer", with
one Start button. The same card shows on the wizard's last screen, on the
app's thing page and in the chat (``fix_api`` kind ``assemble_yes``).

Her tap (:func:`say_yes`) goes the way every yes here goes: the server's own
sentence, a consent ticket minted and spent at once for this paired device
and exactly this card (``add_on``, ``assemble:<app id>:<card sha16>``; both
written to the audit trail by :mod:`puffpai.security.consent_tickets`), then
one line in ``<state>/assemble/yes.json``. That one yes
covers Toolsmith's rewrites of the connecting code within its budget. It does
not cover a different card: when the sites or the outside code change (a new
part, or Toolsmith naming a package), the yes no longer matches and she is
asked again.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.parse
from typing import Any, Optional

from puffpai import fsutil
from puffpai.assemble import APP_ID_RE, state_dir
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

WAITING = "Waiting for your yes"
WRITES_CODE = "BuildBot writes small connecting code and tests it on this computer."
BUTTON = "Start"
ACTION = "add_on"
FIX_KIND = "assemble_yes"
_LOCK = threading.Lock()


def yes_path() -> str:
    return state_dir("yes.json")


def _host(url: Any) -> str:
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return ""
    return (parts.hostname or "").rstrip(".").lower() if parts.scheme in ("http", "https") \
        else ""


def plan_row(app_id: str) -> Optional[dict]:
    """The plan's row for this assembled app, or None."""
    if not APP_ID_RE.match(str(app_id or "")):
        return None
    from puffpai.assemble import plan as plan_mod
    for row in plan_mod.load_plan().get("capabilities") or []:
        if isinstance(row, dict) and row.get("decision") == plan_mod.ASSEMBLE \
                and row.get("app_id") == app_id:
            return row
    return None


def _code_part(part: dict) -> Optional[dict]:
    """An outside code part (a library, a command line tool, an app's calls):
    its name, where it comes from and its language."""
    if part.get("kind") not in ("library", "cli", "openapi"):
        return None
    origin, language = "", ""
    try:
        from puffpai.integrate import records
        rec = records.get(str(part.get("app") or "")) or {}
        src = rec.get("from") if isinstance(rec.get("from"), dict) else {}
        found = rec.get("origin") if isinstance(rec.get("origin"), dict) else {}
        origin = str(found.get("owner") or "")
        if found.get("location"):
            origin += f" ({found['location']})"
        origin = origin or _host(src.get("url")) or str(src.get("source") or "")
        language = str(found.get("code_language") or rec.get("language") or "")
    except Exception:  # noqa: BLE001 - unknown is shown as unknown
        pass
    return {"name": str(part.get("name") or "")[:80],
            "origin": origin[:120] or "not known", "language": language[:30] or "not known"}


def _planned(app_id: str) -> list[str]:
    try:
        from puffpai.assemble import toolsmith_loop
        return toolsmith_loop.planned_packages(app_id)
    except Exception:  # noqa: BLE001
        return []


def card(app_id: str) -> Optional[dict]:
    """The Start card for one assembled app, or None when the plan has no such
    app. ``{"app_id", "cap", "title", "sites", "packages", "lines",
    "button", "sha", "said_yes"}``."""
    row = plan_row(app_id)
    if row is None:
        return None
    sites: list[str] = []
    packages: list[dict] = []
    for part in row.get("parts") or []:
        if not isinstance(part, dict):
            continue
        for key in ("url", "sample_url", "lookup_url"):
            host = _host(part.get(key))
            if host and host not in sites:
                sites.append(host)
        if part.get("host") and str(part["host"]).lower() not in sites:
            sites.append(str(part["host"]).lower())
        code = _code_part(part)
        if code:
            packages.append(code)
    for name in _planned(app_id):
        packages.append({"name": name, "origin": "the Python Package Index (pypi.org)",
                         "language": "Python"})
    sites.sort()
    packages.sort(key=lambda p: (p["name"], p["origin"]))
    title = str(row.get("name") or app_id)[:80]
    sha = hashlib.sha256(json.dumps({"app": app_id, "sites": sites, "packages": packages},
                                    sort_keys=True).encode("utf-8")).hexdigest()
    lines = [f"Start {title}?"]
    lines.append("Sites it uses: " + (", ".join(sites) if sites else "none") + ".")
    if packages:
        lines.append("Outside code it uses: " + "; ".join(
            f"{p['name']} (from {p['origin']}, written in {p['language']})"
            for p in packages) + ".")
    else:
        lines.append("Outside code it uses: none.")
    lines.append(WRITES_CODE)
    return {"app_id": app_id, "cap": str(row.get("cap") or ""), "title": title,
            "sites": sites, "packages": packages, "lines": lines, "button": BUTTON,
            "sha": sha, "said_yes": _said(app_id) == sha}


def _yeses() -> dict:
    data = fsutil.read_json(yes_path(), default={})
    return data if isinstance(data, dict) else {}


def _said(app_id: str) -> str:
    row = _yeses().get(app_id)
    return str(row.get("sha") or "") if isinstance(row, dict) else ""


def has_yes(app_id: Any) -> bool:
    """True only when her recorded yes is for exactly this app's card now."""
    if not isinstance(app_id, str) or not APP_ID_RE.match(app_id):
        return False
    said = _said(app_id)
    if not said:
        return False
    now = card(app_id)
    return bool(now) and now["sha"] == said


def waiting() -> list[dict]:
    """Every assembled app in the plan that waits for her yes (its card)."""
    from puffpai.assemble import plan as plan_mod
    out = []
    for row in plan_mod.load_plan().get("capabilities") or []:
        if isinstance(row, dict) and row.get("decision") == plan_mod.ASSEMBLE \
                and row.get("app_id"):
            got = card(str(row["app_id"]))
            if got and not got["said_yes"]:
                out.append(got)
    return out


def target(app_id: str, sha: str) -> str:
    return f"assemble:{app_id}:{sha[:16]}"


def sentence(got: dict) -> str:
    return " ".join(got["lines"])


def _audit(result: str, app_id: str) -> None:
    try:
        from puffpai.security.audit_log import AuditLog
        AuditLog().log(action="assemble_yes", user="owner", result=result, details=app_id)
    except Exception as exc:  # noqa: BLE001 - the ticket's own lines are kept
        logger.debug("assemble yes audit line skipped: %s", exc)


class YesError(Exception):
    def __init__(self, line: str, status: int = 400):
        super().__init__(line)
        self.line = line
        self.status = status


def say_yes(app_id: Any, device_id: str, *, sha: str = "") -> dict:
    """Her Start on one app's card, from a paired device. Raises
    :class:`YesError`. *sha*: the card she was shown; when given and the card
    changed since, nothing is recorded and she is shown the new card."""
    from puffpai.security import consent_tickets
    if not isinstance(app_id, str) or not APP_ID_RE.match(app_id):
        raise YesError("There is no such app.", 404)
    if not consent_tickets.usable_device(device_id):
        raise YesError("Open this from a browser that is paired with your bot.", 403)
    got = card(app_id)
    if got is None:
        raise YesError("That app is not in your bot's plan.", 404)
    if sha and sha != got["sha"]:
        raise YesError("What this app uses changed since you saw it. Look again.", 409)
    tgt = target(app_id, got["sha"])
    try:
        ticket = consent_tickets.mint(ACTION, tgt, sentence(got), device_id)
    except consent_tickets.TicketRefused as exc:
        raise YesError(exc.message, 429 if exc.code == "throttled" else 403) from None
    ok, verdict = consent_tickets.redeem(ticket, device_id, ACTION, tgt)
    if not ok:
        raise YesError(consent_tickets.refusal_words(verdict), 403)
    with _LOCK:
        data = _yeses()
        data[app_id] = {"sha": got["sha"], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                               time.gmtime()),
                        "by": "owner"}
        fsutil.atomic_write_json(yes_path(), data)
    _audit("yes", app_id)
    return {**got, "said_yes": True}


def fix_for(app_id: str) -> dict:
    """The chat's card shape for this app's yes (``fix_api``)."""
    return {"kind": FIX_KIND, "app_id": app_id}


__all__ = ["BUTTON", "FIX_KIND", "WAITING", "WRITES_CODE", "YesError", "card", "fix_for",
           "has_yes", "plan_row", "say_yes", "target", "waiting", "yes_path"]
