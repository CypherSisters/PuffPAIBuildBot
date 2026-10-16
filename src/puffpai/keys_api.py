"""HTTP routes for the Sign-up keys page.

Registered on the backend app by ``api_server`` (one line), so every route
sits behind the same API-key check, rate limit and CORS rules as the rest of
the backend; the dashboard reaches them only through its paired proxy.

Routes::

    GET  /keys                      every curated key, its state, the steps
    GET  /keys/<id>/how             the steps to get one (public information)
    POST /keys/<id>/confirm         {background} or {only_locked} -> the
                                    server's sentence + a key_on ticket for
                                    this device
    POST /keys/<id>                 {value, background, ticket} -> check, store
    POST /keys/<id>/check           check the stored key again (one call)
    POST /keys/<id>/locked          {on, ticket} keep a FREE key working while
                                    the vault is locked (on needs a ticket)
    POST /keys/<id>/remove          remove it (one step, no ticket)

Rules: no PIN and never ``check_pin`` (pairing is
the gate). Switching a key ON redeems a single-use, device-bound ``key_on``
ticket whose sentence the server built. Storing needs the vault OPEN, because
storing writes the vault. The key travels only in the POST body: a value in
the address is refused (400) and never echoed. No response ever carries it.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("keys", __name__)

_SID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
#: Query-string names that could carry a key. Refused on every route.
_KEYISH_ARGS = ("value", "key", "token", "secret", "api_key", "apikey")

_STATUS = {"unknown": 404, "missing": 404, "locked": 409, "no_vault": 409,
           "cannot": 409, "already": 409, "not_set_up": 409,
           "switched_off": 409, "shape": 400, "test_key": 400,
           "rejected": 400, "busy": 429, "unreachable": 502,
           "stopped_working": 400, "paused_limit": 409}

TITLE = "Sign-up keys"
PAGE_LINE = ("Some services need a free key before your bot can use them. A "
             "key goes straight into your vault; your bot keeps only a note "
             "that says which key to use. No screen shows a key again.")


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _refuse_key_in_address():
    if any(name in request.args for name in _KEYISH_ARGS):
        return jsonify({"ok": False,
                        "message": "A key is never sent in the address. "
                                   "Nothing was kept."}), 400
    return None


def _bad(sid: str):
    from puffpai.security import keys
    if not _SID_RE.match(str(sid or "")) or keys.spec(sid) is None:
        return jsonify({"ok": False,
                        "message": "There is no sign-up key by that name."}), 404
    if sid not in keys.listed():
        # Added on its own page (GitHub: the Developer page and the wizard).
        return jsonify({"ok": False,
                        "message": "That sign-in is added on its own page."}), 404
    return None


def _refused(exc) -> tuple:
    return jsonify({"ok": False, "message": getattr(exc, "message", str(exc))}), \
        _STATUS.get(getattr(exc, "code", ""), 400)


def _target(sid: str, background: bool) -> str:
    return f"{sid}:locked" if background else sid


@bp.route("/keys", methods=["GET"])
def keys_list():
    from puffpai.security import keys
    bad = _refuse_key_in_address()
    if bad:
        return bad
    vault = keys._vault()  # noqa: SLF001 - the backend's own vault
    return jsonify({"title": TITLE, "line": PAGE_LINE,
                    "vault_open": bool(getattr(vault, "is_unlocked", False)),
                    "vault_set_up": bool(getattr(vault, "is_initialized", False)),
                    "keys": keys.list_keys(vault=vault, surface="browser")})


@bp.route("/keys/<sid>/how", methods=["GET"])
def keys_how(sid):
    from puffpai.security import keys
    bad = _bad(sid)
    if bad:
        return bad
    return jsonify({"ok": True, **keys.walk_through(sid, "browser")})


@bp.route("/keys/<sid>/confirm", methods=["POST"])
def keys_confirm(sid):
    from puffpai import outside_api
    from puffpai.security import keys
    bad = _bad(sid) or _refuse_key_in_address()
    if bad:
        return bad
    body = _body()
    if body.get("only_locked") is True:
        # The later switch: keep an already-added FREE key working while the
        # vault is locked. Its own sentence, its own ticket target.
        try:
            sentence = keys.locked_sentence(sid)
        except keys.KeyRefused as exc:
            return _refused(exc)
        return outside_api.mint_for("key_on", f"{sid}:keeplocked", sentence)
    background = body.get("background") is True
    try:
        sentence = keys.confirm_sentence(sid, background=background)
    except keys.KeyRefused as exc:
        return _refused(exc)
    return outside_api.mint_for("key_on", _target(sid, background), sentence)


@bp.route("/keys/<sid>", methods=["POST"])
def keys_add(sid):
    from puffpai import outside_api
    from puffpai.security import keys
    bad = _bad(sid) or _refuse_key_in_address()
    if bad:
        return bad
    body = _body()
    value = body.get("value")
    background = body.get("background") is True
    try:
        keys.check_shape(sid, value)  # nothing is sent if this fails
    except keys.KeyRefused as exc:
        return _refused(exc)
    vault = keys._vault()  # noqa: SLF001
    if vault is None or not getattr(vault, "is_initialized", False):
        from puffpai.security.vault import missing_words
        missing = missing_words(vault)
        if missing:
            return jsonify({"ok": False, "vault_missing": True,
                            "message": missing}), 409
        return jsonify({"ok": False, "message": "A key needs the vault. Set "
                        "your vault PIN first, then add the key."}), 409
    if not getattr(vault, "is_unlocked", False):
        # Checked BEFORE the yes is spent, so the owner does not start again.
        return jsonify({"ok": False, "message": "Your vault is locked. Open it "
                        "on the Vault page, then paste the key again."}), 409
    ok, why = outside_api.redeem_for(body.get("ticket"), "key_on",
                                     _target(sid, background))
    if not ok:
        return jsonify({"ok": False, "message": why}), 403
    try:
        result = keys.add_key(sid, value, vault=vault, background=background,
                              by="owner")
    except keys.KeyRefused as exc:
        return _refused(exc)
    finally:
        value = None
        body = None
    return jsonify(result)


@bp.route("/keys/<sid>/check", methods=["POST"])
def keys_check(sid):
    from puffpai.security import keys
    bad = _bad(sid) or _refuse_key_in_address()
    if bad:
        return bad
    try:
        return jsonify(keys.check_key(sid, by="owner"))
    except keys.KeyRefused as exc:
        return _refused(exc)


@bp.route("/keys/<sid>/locked", methods=["POST"])
def keys_locked(sid):
    from puffpai import outside_api
    from puffpai.security import keys
    bad = _bad(sid) or _refuse_key_in_address()
    if bad:
        return bad
    body = _body()
    on = body.get("on") is True
    if on:
        ok, why = outside_api.redeem_for(body.get("ticket"), "key_on",
                                         f"{sid}:keeplocked")
        if not ok:
            return jsonify({"ok": False, "message": why}), 403
    try:
        row = keys.set_background(sid, on, by="owner")
    except keys.KeyRefused as exc:
        return _refused(exc)
    return jsonify({"ok": True, "key": row})


@bp.route("/keys/<sid>/remove", methods=["POST"])
def keys_remove(sid):
    from puffpai.security import keys
    bad = _bad(sid) or _refuse_key_in_address()
    if bad:
        return bad
    try:
        return jsonify(keys.remove_key(sid, by="owner"))
    except keys.KeyRefused as exc:
        return _refused(exc)


__all__ = ["PAGE_LINE", "TITLE", "bp"]
