"""An app's outside calls: the forwarding address, the owner's yes, and the words for it.

The shim sends every call an app makes to another site to its own address
``/__puffpai/out?u=<address>`` (the page policy blocks anything sent directly).
The host has already checked that the request is the app's own page (same
origin, the app's ``Origin``, the page nonce) before :func:`handle` runs.
Here:

* the address must be https on port 443 with no sign-in part;
* the call goes through the Bridge (``egress_doors.app_web_request``):
  only sites on the app's own door that the owner switched on are reached;
  only ``content-type``, ``accept`` and ``accept-language`` go out; the answer
  comes back with only ``content-type``, ``etag`` and ``last-modified`` (no
  cookies, no page policy, no Clear-Site-Data, no redirect address), plus
  this host's own headers and ``Cache-Control: no-store``;
* a call to a site that is off is refused and, at most once a day per app and
  site and never after the owner said No, remembered as a "wants to reach"
  question for her dashboard. Only sites on the app's door can be asked
  about (the Bridge cannot open any other).

The words of the question are built here, never taken from the app:
``<App name> wants to reach <registrable domain>.`` /
``Toolsmith says it would use it for: <words>.`` / buttons ``Switch on`` and
``No``; with a folder given to the app also ``This app can also read
<folder>. With an outside service on, it could send those files there.`` The
purpose words are Toolsmith's own classification of how the scan saw the site
used (:data:`puffpai.webapps.scan.FOR_WORDS`).

When the Bridge's browser-app functions are not in this build, every call is
refused (``switched_off``) and nothing can be switched on.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Optional
from urllib.parse import urlsplit

from puffpai import webapps
from puffpai.webapps import mirror, scan as _scan, wording

MAX_OUT = 1_000_000
MAX_IN = 5_000_000
REQUEST_HEADERS = ("content-type", "accept", "accept-language")
RESPONSE_HEADERS = ("content-type", "etag", "last-modified")
METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
ASK_EVERY_S = 24 * 3600
_HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_LOCK = threading.Lock()


def _doors():
    """The browser-app Bridge functions, or None when not in this build."""
    ed = webapps._optional("puffpai.security.egress_doors")
    if ed is None:
        return None
    if not all(callable(getattr(ed, n, None)) for n in ("app_web_request", "app_web_hosts",
                                                        "set_app_web_hosts")):
        return None
    return ed


def valid_host(host: Any) -> bool:
    return isinstance(host, str) and bool(_HOST_RE.fullmatch(host))


def url_problem(url: str) -> tuple[str, str]:
    """``("", host)`` when the address may be asked for, else ``(code, host)``."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "not_allowed", ""
    host = (parts.hostname or "").rstrip(".").lower()
    if (parts.scheme or "").lower() != "https":
        return "not_https", host
    if parts.username is not None or parts.password is not None or "@" in (parts.netloc or ""):
        return "userinfo", host
    if port not in (None, 443):
        return "not_443", host
    if not valid_host(host):
        return "not_allowed", host
    return "", host


# ---------------------------------------------------------------------------
# what the app uses sites for (Toolsmith's words), and the owner's answers
# ---------------------------------------------------------------------------

def for_words(app_id: str, host: str) -> str:
    """Toolsmith's words for what the app uses ``host`` for (never the app's own)."""
    from puffpai.webapps import host as _host
    rec = _host.read_record(app_id) or {}
    for h in ((rec.get("scan") or {}).get("hosts") or []):
        if isinstance(h, dict) and h.get("host") == host:
            return _scan.FOR_WORDS.get(str(h.get("for") or ""), _scan.FOR_WORDS[""])
    return _scan.FOR_WORDS[""]


def _asks_path(app_id: str) -> str:
    return os.path.join(webapps.app_state_dir(app_id), "asks.json")


def _asks(app_id: str) -> dict:
    try:
        with open(_asks_path(app_id), encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {h: v for h, v in got.items() if valid_host(h) and isinstance(v, dict)} \
        if isinstance(got, dict) else {}


def _save_asks(app_id: str, asks: dict) -> None:
    path = _asks_path(app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(dict(list(asks.items())[-100:]), fh)
    os.replace(tmp, path)


def note_wanted(app_id: str, host: str) -> bool:
    """Remember that the app wanted ``host``; True when that raises a question now.

    At most once a day per app and site, never after a remembered No, and only
    for a site on the app's door (the only sites the owner could switch on).
    """
    if not webapps.valid_app_id(app_id) or not valid_host(host):
        return False
    ed = _doors()
    if ed is None:
        return False
    try:
        door = ed.app_web_hosts(app_id)
    except Exception:  # noqa: BLE001 - unknown means no question
        return False
    if host not in (door or {}) or door.get(host) is True:
        return False
    now = time.time()
    with _LOCK:
        asks = _asks(app_id)
        rec = asks.get(host) or {}
        if rec.get("answer") == "no":
            return False
        if now - float(rec.get("asked_at") or 0) < ASK_EVERY_S:
            return False
        asks[host] = {"asked_at": now, "answer": None}
        _save_asks(app_id, asks)
    return True


def remember_no(app_id: str, host: str) -> None:
    if not webapps.valid_app_id(app_id) or not valid_host(host):
        return
    with _LOCK:
        asks = _asks(app_id)
        asks[host] = {"asked_at": time.time(), "answer": "no"}
        _save_asks(app_id, asks)


def _forget_answer(app_id: str, host: str) -> None:
    with _LOCK:
        asks = _asks(app_id)
        if host in asks:
            asks.pop(host)
            _save_asks(app_id, asks)


def question_lines(app_id: str, host: str) -> list[str]:
    """The owner's question for switching ``host`` on, in the pinned words."""
    from puffpai.webapps import host as _host
    from puffpai.webapps.wiring import registrable_domain
    rec = _host.read_record(app_id) or {}
    name = str(rec.get("name") or app_id)
    lines = [wording.WANTS_TO_REACH.format(app=name, domain=registrable_domain(host)),
             wording.WOULD_USE_FOR.format(words=for_words(app_id, host))]
    files = webapps._optional("puffpai.webapps.files")
    folder = files.granted_name(app_id) if files is not None and hasattr(files, "granted_name") else None
    if folder:
        lines.append(wording.FOLDER_AND_OUTSIDE.format(folder=folder))
    return lines


def hosts(app_id: str) -> list[dict]:
    """``[{"host", "for", "state", "words"}]`` for the app's door sites.

    ``state`` is ``on``, ``off``, or ``asked`` (the app wanted it and the owner
    has not answered). Empty when the Bridge's browser-app part is not in
    this build (nothing can be on then).
    """
    ed = _doors()
    if ed is None or not webapps.valid_app_id(app_id):
        return []
    try:
        door = ed.app_web_hosts(app_id) or {}
    except Exception:  # noqa: BLE001
        return []
    asks = _asks(app_id)
    out = []
    for h in sorted(door):
        if not valid_host(h):
            continue
        on = door.get(h) is True
        asked = (asks.get(h) or {}).get("answer") is None and h in asks
        out.append({"host": h, "for": for_words(app_id, h),
                    "state": "on" if on else "asked" if asked else "off",
                    "words": question_lines(app_id, h)})
    return out


def set_host(app_id: str, host: str, on: bool, *, by: str, ticket: Optional[str] = None) -> dict:
    """Switch one site of the app's door on (with the owner's ticket) or off.

    ``set_app_web_hosts`` takes the whole list of sites that are on; the
    Bridge spends the ``app_host_on`` ticket itself for the one being
    switched on. Switching off needs nothing and is remembered as her No.
    """
    ed = _doors()
    if ed is None:
        return {"ok": False, "refused": "not_in_this_build", "message": wording.NOT_IN_BUILD}
    if not webapps.valid_app_id(app_id) or not valid_host(host):
        return {"ok": False, "refused": "not_on_list", "message": wording.NOT_THIS_SITE}
    try:
        door = ed.app_web_hosts(app_id) or {}
    except Exception:  # noqa: BLE001
        door = {}
    if host not in door:
        return {"ok": False, "refused": "not_on_list", "message": wording.NOT_THIS_SITE}
    now_on = {h for h, v in door.items() if v is True}
    want = sorted(now_on | {host}) if on else sorted(now_on - {host})
    try:
        got = ed.set_app_web_hosts(app_id, want, by=by, ticket=ticket if on else None)
    except Exception:  # noqa: BLE001 - nothing switched
        return {"ok": False, "refused": "cannot", "message": wording.REFUSED}
    if not isinstance(got, dict):
        return {"ok": False, "refused": "cannot", "message": wording.REFUSED}
    if got.get("ok"):
        if on:
            _forget_answer(app_id, host)
        else:
            remember_no(app_id, host)
    return got


# ---------------------------------------------------------------------------
# the forwarding address
# ---------------------------------------------------------------------------

def _refuse(handler: Any, label: str, method: str, host: str, code: str) -> None:
    mirror.watch(label, kind="asked", host=host, method=method, reason=code)
    handler._json(403, {"refused": code, "host": host, "message": wording.OUTSIDE_NOT_ON})


def handle(handler: Any, label: str, method: str, target: str) -> None:
    """Answer ``/__puffpai/out?u=<target>`` for app ``label`` (checks already passed)."""
    code, host = url_problem(target)
    if not webapps.valid_app_id(label):
        return _refuse(handler, label, method, host, "switched_off")
    if code:
        return _refuse(handler, label, method, host, code)
    if method not in METHODS:
        return _refuse(handler, label, method, host, "not_allowed")
    ed = _doors()
    if ed is None:
        return _refuse(handler, label, method, host, "switched_off")
    body = None
    if method not in ("GET", "HEAD"):
        body = handler._body(MAX_OUT)
        if body is None:
            return _refuse(handler, label, method, host, "too_big")
    sent_headers = {}
    for name in REQUEST_HEADERS:
        value = handler.headers.get(name)
        if isinstance(value, str) and value and len(value) <= 200 and "\n" not in value:
            sent_headers[name] = value
    try:
        got = ed.app_web_request(label, method, target, body=body, headers=sent_headers,
                                 max_out=MAX_OUT, max_in=MAX_IN)
    except Exception:  # noqa: BLE001 - never a trace to the page
        got = {"refused": "no_answer"}
    if not isinstance(got, dict) or "refused" in got or not isinstance(got.get("status"), int):
        refused = str((got or {}).get("refused") or "no_answer") if isinstance(got, dict) else "no_answer"
        if refused == "switched_off":
            note_wanted(label, host)
        return _refuse(handler, label, method, host, refused[:40])
    status = got["status"]
    if not 200 <= status < 600 or 300 <= status < 400:
        status = 502
    extra = {"Cache-Control": "no-store"}
    for k, v in (got.get("headers") or {}).items():
        if str(k).lower() in RESPONSE_HEADERS and isinstance(v, str) and "\n" not in v:
            extra[str(k).lower()] = v
    ctype = extra.pop("content-type", "application/octet-stream")
    data = got.get("body") if isinstance(got.get("body"), (bytes, bytearray)) else b""
    mirror.watch(label, kind="sent", host=host, method=method, reason=str(status))
    handler._send(status, bytes(data), ctype, extra)


__all__ = ["handle", "hosts", "set_host", "note_wanted", "remember_no", "question_lines",
           "for_words", "url_problem", "valid_host"]
