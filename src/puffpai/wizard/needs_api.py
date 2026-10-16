"""HTTP routes for "Finding out what a <kind> bot needs".

A blueprint, registered by ``api_server`` (one line), so both routes
sit behind the same API-key check, rate limit and CORS rules as the rest of the
backend; the dashboard reaches them through its paired proxy under the
``wizard`` prefix it already allows.

Routes::

    GET  /wizard/needs   start finding out what the bot needs (once per run)
                         and say how far it has got
    POST /wizard/needs   {"ticked": [job ids], "added": [str],
                          "allowed_words": [str], "look": "online|here",
                          "get_helper": bool}

Both answer the same shape::

    {"title", "lines", "jobs", "held", "private_left_out",
     "subject": {"phrase", "notes"}, "sends", "helper", "done",
     "phase": "needs|parts", "not_reached"}

plus the words the screen draws (``intro``, ``before_button``, ``buttons``
...). ``look`` starts looking for things that fit the jobs; the plan is then
served by the existing ``/wizard/summary``. The engine work runs on a thread
kept per wizard run by :mod:`puffpai.wizard.service`; nothing here logs the
goal or anything the owner typed.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from puffpai.wizard import cards, service

bp = Blueprint("r3_needs", __name__)


def _refused(exc: Exception, status: int):
    return jsonify({"success": False, "field": getattr(exc, "field", None),
                    "message": str(exc)}), status


def _answer(call):
    try:
        result = call()
    except service.SkillNotConverted as exc:
        return _refused(exc, 404)
    except service.RunInProgress as exc:
        return _refused(exc, 409)
    except cards.CardError as exc:
        return _refused(exc, 400)
    result["success"] = True
    return jsonify(result)


@bp.route("/wizard/needs", methods=["GET"])
def wizard_needs():
    return _answer(service.needs)


def _device() -> str:
    """The paired device asking (``device:<id>``, as the dashboard proxy
    asserts it), or ``""``. Only a paired device may say "Get it"."""
    try:
        from puffpai import api_server
        client = str(api_server._get_chat_client_id() or "")  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return ""
    return client if client.startswith("device:") else ""


@bp.route("/wizard/needs", methods=["POST"])
def wizard_needs_choose():
    body = request.get_json(silent=True)
    return _answer(lambda: service.needs_choose(
        body if body is not None else {}, device_id=_device() or None))


__all__ = ["bp"]
