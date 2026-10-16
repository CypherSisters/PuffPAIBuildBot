"""HTTP routes for where the vault lives (the wizard's vault step and
Developer, Zone 1: Vault).

Registered on the backend app by ``api_server`` (one line), so every route
sits behind the same API-key check, rate limit and CORS rules as the rest of
the backend; the dashboard reaches them only through its paired proxy
(``vault`` is on its address list).

Routes::

    GET  /vault/location          where the vault lives, the question and its
                                  three answers (read-only)
    POST /vault/location/check    {folder, purpose, make_subfolder}: would
                                  this folder do? Changes nothing on disk
    GET  /vault/location/browse   ?folder=  the drives, or the folders inside
                                  one (names only, never files)
    POST /vault/location          {choice, folder, make_subfolder, pin,
                                  current_pin}: "here" or "folder" says where
                                  the NEW vault will be made; "existing" uses
                                  a BuildBot vault already in that folder
                                  (its PIN, and the PIN of the vault in use
                                  now if there is one)
    POST /vault/move              {folder, make_subfolder, pin}: move the
                                  vault there (copy, prove it opens with the
                                  PIN, switch, keep the old copy)

Rules: everything but the read needs a PAIRED device (the same gate as
switching outside services on), and a move or a switch to another vault
needs the PIN in the request body. A PIN in the address (or anything named
like a PIN, code or token) is refused and never echoed, and the server's
request log never writes the words after ``?`` on these routes
(:func:`puffpai.security.pasted_key_guard.strip_secret_queries`); no
response ever carries one. While the remembered vault's drive is not plugged
in, these routes can only point BuildBot at the SAME vault in a new place
(see :mod:`puffpai.security.vault_place`). After a change the server lets go of
its vault handle, so the next use opens the vault in its new place.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("vault_place", __name__)

_NOT_PAIRED = ("Pair this device first. Where your vault lives can only be "
               "changed from a paired device.")
#: Query-string names that could carry a PIN. Refused on every route.
_SECRET_ARGS = ("pin", "current_pin", "password", "vault_pin", "secret")

_STATUS = {"bare_drive": 409, "vault_exists": 409, "vault_missing": 409,
           "has_vault": 409, "not_empty": 409, "same": 409,
           "wrong_pin": 403, "current_pin_needed": 403, "pin_needed": 400,
           "throttled": 429, "unreachable": 503, "copy_failed": 500}


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _guard(*, change: bool = True):
    """A PIN in the address, or a change from a device that is not paired."""
    from puffpai.security.pasted_key_guard import SECRET_QUERY_NAMES
    if any(name in request.args for name in _SECRET_ARGS) or any(
            word in str(name).lower() for name in request.args
            for word in SECRET_QUERY_NAMES):
        return jsonify({"ok": False, "message": "A PIN never goes in the "
                        "address. Nothing was changed."}), 400
    if change:
        from puffpai import outside_api
        if not outside_api.paired_device_id():
            return jsonify({"ok": False, "code": "not_paired",
                            "message": _NOT_PAIRED}), 403
    return None


def _refused(exc):
    return jsonify(exc.as_dict()), _STATUS.get(exc.code, 400)


def _server_vault():
    from puffpai import api_server
    return api_server._get_vault()  # noqa: SLF001


def _reload() -> None:
    from puffpai import api_server
    api_server._reload_vault_handle("moved")  # noqa: SLF001


def _flag(body: dict, name: str) -> bool:
    return body.get(name) is True


@bp.route("/vault/location", methods=["GET"])
def vault_location():
    bad = _guard(change=False)
    if bad:
        return bad
    from puffpai import outside_api
    from puffpai.security import vault_place
    out = vault_place.location(_server_vault())
    out["paired"] = bool(outside_api.paired_device_id())
    out["ok"] = True
    return jsonify(out)


@bp.route("/vault/location/check", methods=["POST"])
def vault_location_check():
    bad = _guard()
    if bad:
        return bad
    from puffpai.security import vault_place
    body = _body()
    purpose = body.get("purpose") if body.get("purpose") in (
        "new", "existing", "move") else "new"
    try:
        out = vault_place.check_folder(body.get("folder"), purpose=purpose,
                                       make_subfolder=_flag(body, "make_subfolder"),
                                       create=False)
    except vault_place.PlaceRefused as exc:
        return _refused(exc)
    return jsonify(out)


@bp.route("/vault/location/browse", methods=["GET"])
def vault_location_browse():
    bad = _guard()
    if bad:
        return bad
    from puffpai.security import vault_place
    try:
        return jsonify(vault_place.browse(request.args.get("folder")))
    except vault_place.PlaceRefused as exc:
        return _refused(exc)


@bp.route("/vault/location", methods=["POST"])
def vault_location_set():
    bad = _guard()
    if bad:
        return bad
    from puffpai.security import vault_place
    body = _body()
    choice = body.get("choice")
    if choice not in vault_place.CHOICE_VALUES:
        return jsonify({"ok": False, "message": "Pick one of the three "
                        "answers."}), 400
    vault = _server_vault()
    try:
        if choice == "existing":
            out = vault_place.use_existing(
                body.get("folder"), body.get("pin"),
                current_pin=body.get("current_pin"), vault=vault,
                make_subfolder=_flag(body, "make_subfolder"))
        else:
            out = vault_place.set_new_place(
                None if choice == "here" else (body.get("folder") or ""),
                make_subfolder=_flag(body, "make_subfolder"), vault=vault)
    except vault_place.PlaceRefused as exc:
        return _refused(exc)
    finally:
        body = None
    _reload()
    return jsonify(out)


@bp.route("/vault/move", methods=["POST"])
def vault_move():
    bad = _guard()
    if bad:
        return bad
    from puffpai.security import vault_place
    body = _body()
    vault = _server_vault()
    if vault is None:
        return jsonify({"ok": False, "message": "The vault is not available "
                        "right now."}), 503
    try:
        out = vault_place.move_vault(vault, body.get("folder"), body.get("pin"),
                                     make_subfolder=_flag(body, "make_subfolder"),
                                     on_switched=_reload)
    except vault_place.PlaceRefused as exc:
        return _refused(exc)
    finally:
        body = None
    return jsonify(out)


__all__ = ["bp"]
