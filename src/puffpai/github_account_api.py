"""HTTP routes for connecting her GitHub account (puffpai.github_account).

Registered on the backend app by ``api_server`` (one line), so every route
sits behind the same API-key check, rate limit and CORS rules as the rest of
the backend, and the dashboard reaches them through its paired proxy. Every
route also needs a PAIRED device (403 otherwise). GETs change nothing.

Routes::

    GET  /github/status         where her connection stands (sends nothing)
    GET  /github/token-help     a pre-filled read-only token link + steps
    POST /github/token          {token} -> check with GitHub, lock in vault
    POST /github/device/start   device sign-in (only with a client id set)
    POST /github/device/poll    ask GitHub once whether she finished
    POST /github/disconnect     delete the token from the vault

The token travels only in the POST body: a token in the address is refused
(400) and never echoed. No answer ever carries it.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("github_account", __name__)

#: Query-string names that could carry a token. Refused on every route.
_TOKENISH_ARGS = ("token", "value", "key", "secret", "access_token", "code")


def _device() -> str:
    from puffpai import outside_api
    return outside_api.paired_device_id()


def _guard():
    if any(name in request.args for name in _TOKENISH_ARGS):
        words = "A token is never sent in the address. Nothing was kept."
        return jsonify({"ok": False, "error": words, "message": words}), 400
    if not _device():
        from puffpai import github_account
        return jsonify({"ok": False, "error": github_account.NOT_PAIRED,
                        "message": github_account.NOT_PAIRED}), 403
    return None


def _refused(exc) -> tuple:
    return jsonify({"ok": False, "code": exc.code, "error": exc.message,
                    "message": exc.message}), exc.status


@bp.route("/github/status", methods=["GET"])
def github_status():
    from puffpai import github_account
    bad = _guard()
    if bad:
        return bad
    return jsonify({"ok": True, **github_account.status()})


@bp.route("/github/token-help", methods=["GET"])
def github_token_help():
    from puffpai import github_account
    bad = _guard()
    if bad:
        return bad
    return jsonify({"ok": True, **github_account.token_help()})


@bp.route("/github/token", methods=["POST"])
def github_token():
    from puffpai import github_account
    bad = _guard()
    if bad:
        return bad
    data = request.get_json(silent=True)
    token = data.get("token") if isinstance(data, dict) else None
    data = None
    try:
        got = github_account.connect_token(token, _device())
    except github_account.GitHubError as exc:
        return _refused(exc)
    finally:
        token = None
    return jsonify({"ok": True, **got})


@bp.route("/github/device/start", methods=["POST"])
def github_device_start():
    from puffpai import github_account
    bad = _guard()
    if bad:
        return bad
    try:
        return jsonify({"ok": True, **github_account.device_start(_device())})
    except github_account.GitHubError as exc:
        return _refused(exc)


@bp.route("/github/device/poll", methods=["POST"])
def github_device_poll():
    from puffpai import github_account
    bad = _guard()
    if bad:
        return bad
    try:
        return jsonify({"ok": True, **github_account.device_poll(_device())})
    except github_account.GitHubError as exc:
        return _refused(exc)


@bp.route("/github/disconnect", methods=["POST"])
def github_disconnect():
    from puffpai import github_account
    bad = _guard()
    if bad:
        return bad
    try:
        return jsonify(github_account.disconnect(_device()))
    except github_account.GitHubError as exc:
        return _refused(exc)


__all__ = ["bp"]
