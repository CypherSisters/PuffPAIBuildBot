"""HTTP routes for "What leaves your computer".

Registered on the backend app by ``api_server`` (one line), so every route
here sits behind the same API-key check, rate limit and CORS rules as the
rest of the backend. The dashboard reaches them only through its paired proxy.

Routes::

    GET  /outside                    the list, in the browser's wording
    GET  /outside/log?hours=24       the outside-traffic log, plain words
    POST /outside/<id>/off           switch one off (nothing else needed)
    POST /outside/<id>/confirm       the server's sentence + a consent ticket
    POST /outside/<id>/on            {"ticket"} switch one on
    POST /outside/notice/ok          hide the "switch list was damaged" note
                                     (switches nothing on)

The address's switch id is named ``service_id``: it is a program-made id
(for an app's outside site or a locked-box project, a 24-letter hash), so
``pasted_key_guard`` reads it as an id, not as words a person typed. It is
only ever checked against the id shape and looked up in the list: an unknown
one gets "There is no outside service by that name." and nothing else.

Who may switch something ON
---------------------------
No PIN; being paired is the gate. ``confirm`` and ``on``
need the caller's paired-device identity -- ``device:<id>`` from the dashboard
proxy's ``X-Puff-Device-Id`` (``api_server._get_chat_client_id``). A request
with no paired identity (a direct script, an unpaired browser) cannot mint or
redeem a ticket and is told so. The ticket is single-use, lasts 10 minutes,
and is bound to that device and to exactly this switch
(:mod:`puffpai.security.consent_tickets`).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("outside", __name__)

_SID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")

_NOT_PAIRED = ("Switching an outside service on has to be done from a browser "
               "that is paired with your bot.")


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def paired_device_id() -> str:
    """This request's paired-device id (``device:<id>``), or ''."""
    try:
        from puffpai import api_server
        client = api_server._get_chat_client_id()  # noqa: SLF001
    except Exception:
        return ""
    return client if isinstance(client, str) and client.startswith("device:") \
        else ""


def _bad_sid(sid: str):
    if not _SID_RE.match(str(sid or "")):
        return jsonify({"ok": False,
                        "message": "There is no outside service by that name."}), 404
    return None


def _error(exc) -> tuple:
    status = {"unknown": 404, "already": 409, "cannot": 409,
              "not_set_up": 409}.get(getattr(exc, "code", ""), 400)
    return jsonify({"ok": False, "message": getattr(exc, "message", str(exc))}), status


@bp.route("/outside", methods=["GET"])
def outside_list():
    from puffpai import outside_services
    return jsonify(outside_services.page("browser"))


@bp.route("/outside/log", methods=["GET"])
def outside_log():
    from puffpai import outside_services
    try:
        hours = float(request.args.get("hours", "24"))
    except ValueError:
        hours = 24.0
    hours = max(1.0, min(hours, 24.0 * 30))
    service = str(request.args.get("service") or "")
    if service and not _SID_RE.match(service):
        service = ""
    return jsonify({"rows": outside_services.log_rows(hours, service=service),
                    "hours": hours,
                    "kept_for": "30 days, on this computer only"})


@bp.route("/outside/notice/ok", methods=["POST"])
def outside_notice_ok():
    """The owner saw the "your switch list was damaged" note. Hides it only:
    every service stays off until it is switched on, one at a time."""
    from puffpai import outside_services
    try:
        done = outside_services.acknowledge_notice()
    except outside_services.OutsideError as exc:
        return _error(exc)
    return jsonify({"ok": True, "hidden": bool(done)})


@bp.route("/outside/<service_id>/off", methods=["POST"])
def outside_off(service_id):
    sid = service_id
    from puffpai import outside_services
    bad = _bad_sid(sid)
    if bad:
        return bad
    try:
        row = outside_services.switch_off(sid, by="owner")
    except outside_services.OutsideError as exc:
        return _error(exc)
    return jsonify({"ok": True, "service": row})


def mint_for(action: str, target: str, sentence: str) -> tuple:
    """Mint a ticket for this request's paired device. (response, status)."""
    from puffpai.security import consent_tickets
    device = paired_device_id()
    if not device:
        return jsonify({"ok": False, "message": _NOT_PAIRED}), 403
    try:
        ticket = consent_tickets.mint(action, target, sentence, device)
    except consent_tickets.TicketRefused as exc:
        status = 429 if exc.code == "throttled" else 403
        return jsonify({"ok": False, "message": exc.message}), status
    return jsonify({"ok": True, "ticket": ticket, "sentence": sentence,
                    "expires_in": consent_tickets.TTL_SECONDS}), 200


def redeem_for(ticket, action: str, target: str) -> tuple[bool, str]:
    """Spend *ticket* for this request's paired device. (ok, plain refusal)."""
    from puffpai.security import consent_tickets
    device = paired_device_id()
    if not device:
        return False, _NOT_PAIRED
    ok, verdict = consent_tickets.redeem(ticket, device, action, target)
    return ok, ("" if ok else consent_tickets.refusal_words(verdict))


@bp.route("/outside/<service_id>/confirm", methods=["POST"])
def outside_confirm(service_id):
    sid = service_id
    from puffpai import outside_services
    bad = _bad_sid(sid)
    if bad:
        return bad
    try:
        sentence = outside_services.proposal(sid)
    except outside_services.OutsideError as exc:
        return _error(exc)
    return mint_for("outside_on", sid, sentence)


@bp.route("/outside/<service_id>/on", methods=["POST"])
def outside_on(service_id):
    sid = service_id
    from puffpai import outside_services
    bad = _bad_sid(sid)
    if bad:
        return bad
    ok, why = redeem_for(_body().get("ticket"), "outside_on", sid)
    if not ok:
        return jsonify({"ok": False, "message": why}), 403
    try:
        row = outside_services.switch_on(sid, by="owner")
    except outside_services.OutsideError as exc:
        return _error(exc)
    return jsonify({"ok": True, "service": row})


__all__ = ["bp", "mint_for", "paired_device_id", "redeem_for"]
