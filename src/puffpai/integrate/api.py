"""HTTP routes for integrated things (registered on the backend by ``api_server``).

Every route sits behind the backend's API-key hook (none is public). Routes::

    GET  /integrations                 every integration record, a bare list (no local paths)
    GET  /integrations/<id>            one record
    POST /integrations/<id>/decide     {"yes": bool} the owner's answer to a held item
    POST /integrations/<id>/remove     take it out (paired browser; the id may be the app's)
    POST /integrations/<id>/folder     {"path"} the mood board's pictures folder (paired, read only)
    POST /integrations/undo            this paired browser's last change, within 10 minutes
    GET  /integrations/voice-key       the Home Assistant voice key page (paired browser)
    POST /integrations/voice-key/new   make a new voice key (the old one stops working)
    POST /integrations/voice-key/off   no voice key: voice is ordinary chat again

A yes to a held item is accepted only from a paired device (``device:<id>``
as the dashboard proxy asserts it, see ``api_server._get_chat_client_id``):
the route mints and redeems the ``add_on`` ticket
``held:<id>:<rows_sha[:16]>`` for that device, and the files are checked again
before anything is added.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from puffpai.integrate import records, wording
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("integrate", __name__)


def _device() -> str:
    try:
        from puffpai import api_server
        client = str(api_server._get_chat_client_id() or "")  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return ""
    return client if client.startswith("device:") else ""


@bp.route("/integrations", methods=["GET"])
def list_integrations():
    """Every integration record, as the pinned bare list ``[record]``."""
    return jsonify([records.public_view(r) for r in records.all()])


@bp.route("/integrations/<integration_id>/remove", methods=["POST"])
def remove_integration(integration_id: str):
    """Take it out, from a paired browser (the app page's "Remove this app").
    The id may be the record's own or the app's install id. Uses the Add
    page's take-out path when the thing was added there (so its line in
    "What you added" goes too), else removes the record directly."""
    if not _device():
        return jsonify({"ok": False, "message": wording.REMOVE_PAIRED_ONLY}), 403
    rec = records.find(integration_id) if isinstance(integration_id, str) else None
    if rec is None:
        return jsonify({"ok": False, "message": wording.UNKNOWN_ITEM}), 404
    line = ""
    try:
        from puffpai import add_more
        row = next((r for r in add_more.added()
                    if (r.get("undo") or {}).get("kind") == "integration"
                    and (r.get("undo") or {}).get("ref") == rec["id"]), None)
        if row is not None:
            line = add_more.take_out(row["entry"], by="owner")
    except Exception as exc:  # noqa: BLE001 - fall back to the record itself
        logger.info("integrate: take-out path skipped: %s", type(exc).__name__)
        line = ""
    if not line or records.get(rec["id"]) is not None:
        got = records.remove(rec["id"], by="owner")
        if not got.get("ok"):
            return jsonify({"ok": False, "message": got.get("line")}), 409
        line = got.get("line") or line
    return jsonify({"ok": True, "line": line})


@bp.route("/integrations/<integration_id>/folder", methods=["POST"])
def choose_folder(integration_id: str):
    """Her pictures folder for the mood board (read only), from a paired browser."""
    from puffpai.integrate import palette
    device = _device()
    if not device:
        return jsonify({"ok": False, "message": wording.FOLDER_PAIRED_ONLY}), 403
    body = request.get_json(silent=True)
    path = body.get("path") if isinstance(body, dict) else None
    if not isinstance(path, str) or not path.strip() or len(path) > 1024:
        return jsonify({"ok": False, "message": wording.FOLDER_REFUSED}), 400
    if not records.valid_id(integration_id):
        return jsonify({"ok": False, "message": wording.UNKNOWN_ITEM}), 404
    try:
        got = palette.choose_folder(integration_id, path, device_id=device)
    except palette.PaletteError as exc:
        status = {"not_paired": 403, "unknown": 404}.get(exc.code, 409)
        return jsonify({"ok": False, "message": exc.line}), status
    return jsonify({"ok": True, "line": got["line"],
                    "themes": got["palette"].get("themes") or []})


@bp.route("/integrations/undo", methods=["POST"])
def undo_last():
    """Undo this paired browser's last change (within 10 minutes)."""
    from puffpai.integrate import actions
    device = _device()
    if not device:
        return jsonify({"ok": False, "message": wording.DECIDE_NOT_PAIRED}), 403
    got = actions.undo_last(device)
    return jsonify({"ok": bool(got.get("ok")), "line": got.get("line")})


# -- the Home Assistant voice key -------------------------------------------------
#
# Shown, made and switched off only from a paired browser: the key lets Home
# Assistant read the apps and make undoable changes, so whoever sees it holds
# that much. Never in a web address, never logged.

def _voice_page(key: str = "", line: str = "") -> dict:
    from puffpai.integrate import voice_key
    on = bool(key) or voice_key.is_on()
    return {"title": wording.VOICE_KEY_TITLE,
            "line": wording.VOICE_KEY_LINE if on else wording.VOICE_KEY_OFF_LINE,
            "what": wording.VOICE_KEY_WHAT,
            "steps": list(wording.VOICE_KEY_STEPS),
            "on": on,
            "key": key,
            "make_label": wording.VOICE_KEY_REMAKE if on else wording.VOICE_KEY_NEW,
            "off_label": wording.VOICE_KEY_TURN_OFF,
            "message": line}


def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/integrations/voice-key", methods=["GET"])
def voice_key_page():
    from puffpai.integrate import voice_key
    if not _device():
        return jsonify({"ok": False, "message": wording.VOICE_KEY_PAIRED_ONLY}), 403
    return _no_store(jsonify({"ok": True, **_voice_page(voice_key.current())}))


@bp.route("/integrations/voice-key/new", methods=["POST"])
def voice_key_new():
    from puffpai.integrate import voice_key
    if not _device():
        return jsonify({"ok": False, "message": wording.VOICE_KEY_PAIRED_ONLY}), 403
    key = voice_key.new()
    return _no_store(jsonify({"ok": True, **_voice_page(key, wording.VOICE_KEY_MADE)}))


@bp.route("/integrations/voice-key/off", methods=["POST"])
def voice_key_off():
    from puffpai.integrate import voice_key
    if not _device():
        return jsonify({"ok": False, "message": wording.VOICE_KEY_PAIRED_ONLY}), 403
    voice_key.off()
    return _no_store(jsonify({"ok": True, **_voice_page("", wording.VOICE_KEY_TURNED_OFF)}))


@bp.route("/integrations/<integration_id>", methods=["GET"])
def get_integration(integration_id: str):
    rec = records.get(integration_id) if records.valid_id(integration_id) else None
    if rec is None:
        return jsonify({"ok": False, "message": wording.UNKNOWN_ITEM}), 404
    return jsonify(records.public_view(rec))


@bp.route("/integrations/<integration_id>/decide", methods=["POST"])
def decide_integration(integration_id: str):
    from puffpai.integrate import apply
    if not records.valid_id(integration_id):
        return jsonify({"ok": False, "message": wording.UNKNOWN_ITEM}), 404
    body = request.get_json(silent=True)
    yes = body.get("yes") if isinstance(body, dict) else None
    if not isinstance(yes, bool):
        return jsonify({"ok": False, "message": "Say yes or no."}), 400
    device = _device()
    if not device:
        return jsonify({"ok": False, "message": wording.DECIDE_NOT_PAIRED}), 403
    try:
        out = apply.decide(integration_id, yes, device_id=device)
    except apply.DecideError as exc:
        status = {"not_paired": 403, "unknown": 404}.get(exc.code, 409)
        return jsonify({"ok": False, "message": exc.line}), status
    rec = records.get(integration_id)
    return jsonify({"ok": out.get("status") in ("added", "refused"),
                    "status": out.get("status"), "line": out.get("line"),
                    "lines": out.get("lines") or [],
                    "integration": records.public_view(rec) if rec else None})


__all__ = ["bp"]
