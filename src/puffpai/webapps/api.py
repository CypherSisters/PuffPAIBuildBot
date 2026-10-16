"""HTTP routes for browser apps (registered on the backend by ``api_server``).

Behind the backend's API-key check like every other route; the dashboard
reaches them only through its paired proxy (prefix ``webapps``).

Routes::

    GET  /webapps/<id>/origin      -> {"origin": "http://<id>.localhost:<port>"}
    GET  /webapps/<id>/abilities   -> ["Add a guest", ...]  (the checked actions' words)
    GET  /webapps/<id>/hosts       -> [{"host", "for", "state", "words"}]
    POST /webapps/<id>/hosts       {"host": str, "on": bool, "ticket"?: str}
    POST /webapps/<id>/folder      {"path": str, "mode": "ro"|"rw", "ticket"?: str}; path "" takes it back
    POST /webapps/<id>/undo        {"version": int}
    GET  /webapps/<id>/examples    -> {"count", "details", "line"}  (the app's own examples)
    POST /webapps/<id>/examples/clear  -> {"ok", "removed", "details", "version", "line"}

The id must match the issued-id shape and name an installed app; anything
else is 404. The address is computed here, never taken from a manifest.

Anything that switches something ON (an outside site, a folder) needs a
paired device (``device:<id>`` as the dashboard proxy asserts it) and a
device-bound, single-use consent ticket: the first POST answers with the
question's words and a ticket, the second POST (same device, within ten
minutes) carries the ticket back. Switching a site off or taking a folder back
needs only a paired device. An undo needs a paired device.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from puffpai import webapps
from puffpai.webapps import wording

bp = Blueprint("webapps", __name__)


def _installed(app_id: str) -> bool:
    if not webapps.valid_app_id(app_id):
        return False
    from puffpai.webapps import host
    return app_id in host.installed_ids()


def _not_found():
    resp = jsonify({"error": "not_found"})
    resp.status_code = 404
    return resp


def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _device() -> str:
    """The paired device making this request, or "" (never the terminal over HTTP)."""
    try:
        from puffpai import api_server
        client = str(api_server._get_chat_client_id() or "")  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return ""
    return client if client.startswith("device:") else ""


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _answer(out: dict, *, ok_status: int = 200):
    status = ok_status if out.get("ok") else (200 if out.get("needs_yes") else
                                              {"not_in_this_build": 409, "unknown_app": 404,
                                               "not_on_list": 404}.get(str(out.get("refused")), 403))
    resp = jsonify(out)
    resp.status_code = status
    return _no_store(resp)


@bp.get("/webapps/<app_id>/origin")
def origin(app_id: str):
    if not _installed(app_id):
        return _not_found()
    from puffpai.webapps import host
    return _no_store(jsonify({"origin": host.origin_for(app_id)}))


@bp.get("/webapps/<app_id>/abilities")
def abilities(app_id: str):
    if not _installed(app_id):
        return _not_found()
    from puffpai.webapps import abilities as ab
    return _no_store(jsonify([str(s)[:120] for s in ab.says_for(app_id)][:40]))


@bp.get("/webapps/<app_id>/examples")
def examples(app_id: str):
    """How many of the app's own example entries are still in it, and the
    plain line the app's page shows (empty when none)."""
    if not _installed(app_id):
        return _not_found()
    from puffpai.webapps import examples as ex, wording
    n, details = ex.left(app_id)
    return _no_store(jsonify({"count": n, "details": details,
                              "line": wording.examples_header_line(n, details)}))


@bp.post("/webapps/<app_id>/examples/clear")
def examples_clear(app_id: str):
    """Take the app's own example entries out of the bot's copy (one tap on
    the app's page, a paired device only). Never an entry she added or
    changed; one new version, so the page's Undo puts them back."""
    if not _installed(app_id):
        return _not_found()
    if not _device():
        return _answer({"ok": False, "refused": "not_paired", "message": wording.NOT_PAIRED})
    from puffpai.webapps import examples as ex
    got = ex.clear(app_id)
    n = int(got.get("removed") or 0)
    details = int(got.get("details") or 0)
    if not got.get("ok"):
        line = (wording.EXAMPLES_NOT_OPENED if got.get("why") == "not_opened"
                else wording.EXAMPLES_NOT_CLEARED)
    else:
        line = wording.examples_cleared_line(n, details)
    return _answer({"ok": bool(got.get("ok")), "removed": n, "details": details,
                    "version": got.get("version"), "line": line, "message": line,
                    "refused": got.get("why") or None,
                    "header": wording.examples_header_line(*ex.left(app_id))})


@bp.get("/webapps/<app_id>/hosts")
def hosts(app_id: str):
    if not _installed(app_id):
        return _not_found()
    from puffpai.webapps import forward
    return _no_store(jsonify(forward.hosts(app_id)))


@bp.post("/webapps/<app_id>/hosts")
def set_host(app_id: str):
    if not _installed(app_id):
        return _not_found()
    from puffpai.webapps import forward
    body = _body()
    host_name = str(body.get("host") or "").strip().rstrip(".").lower()
    on = body.get("on")
    if not isinstance(on, bool) or not forward.valid_host(host_name):
        return _answer({"ok": False, "refused": "bad_request", "message": wording.REFUSED})
    device = _device()
    if not device:
        return _answer({"ok": False, "refused": "not_paired", "message": wording.NOT_PAIRED})
    if not on:
        return _answer(forward.set_host(app_id, host_name, False, by=device))
    ticket = body.get("ticket")
    if not isinstance(ticket, str) or not ticket:
        # the first tap: the question's words and a ticket for exactly this site
        listed = {h["host"]: h for h in forward.hosts(app_id)}
        if host_name not in listed:
            if forward._doors() is None:
                return _answer({"ok": False, "refused": "not_in_this_build",
                                "message": wording.NOT_IN_BUILD})
            return _answer({"ok": False, "refused": "not_on_list", "message": wording.NOT_THIS_SITE})
        from puffpai.security import consent_tickets
        words = listed[host_name]["words"]
        try:
            secret = consent_tickets.mint("app_host_on",
                                          consent_tickets.target_app_host(app_id, host_name),
                                          " ".join(words), device)
        except (consent_tickets.TicketRefused, ValueError) as exc:
            return _answer({"ok": False, "refused": getattr(exc, "code", "refused"),
                            "message": getattr(exc, "message", wording.REFUSED)})
        return _answer({"ok": False, "needs_yes": True, "ticket": secret, "words": words,
                        "buttons": [wording.SWITCH_ON, wording.NO]})
    return _answer(forward.set_host(app_id, host_name, True, by=device, ticket=ticket))


@bp.post("/webapps/<app_id>/folder")
def folder(app_id: str):
    if not _installed(app_id):
        return _not_found()
    from puffpai.webapps import files
    body = _body()
    device = _device()
    if not device:
        return _answer({"ok": False, "refused": "not_paired", "message": wording.NOT_PAIRED})
    path = body.get("path")
    if path == "":
        return _answer(files.take_back(app_id))
    if not isinstance(path, str) or len(path) > 1024:
        return _answer({"ok": False, "refused": "bad_request", "message": wording.REFUSED})
    ticket = body.get("ticket") if isinstance(body.get("ticket"), str) else None
    return _answer(files.give(app_id, path, str(body.get("mode") or ""), device=device,
                              ticket=ticket))


@bp.post("/webapps/<app_id>/undo")
def undo(app_id: str):
    if not _installed(app_id):
        return _not_found()
    from puffpai.webapps import mirror
    if not _device():
        return _answer({"ok": False, "refused": "not_paired", "message": wording.NOT_PAIRED})
    version = _body().get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        return _answer({"ok": False, "refused": "bad_request", "message": wording.REFUSED})
    got = mirror.restore(app_id, version)
    line = {"": wording.UNDONE, "newer_write": wording.UNDO_NEWER,
            "busy": wording.UNDO_BUSY}.get(got.get("why") or "", wording.UNDO_GONE)
    return _answer({"ok": bool(got.get("ok")), "why": got.get("why") or "", "line": line,
                    "refused": got.get("why") or None, "message": line})


__all__ = ["bp"]
