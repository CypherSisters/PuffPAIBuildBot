"""HTTP routes for tool servers: "Connect my Home Assistant" and "Add my own tool".

Registered on the backend app by ``api_server`` (one line), so every route
sits behind the same API-key check as the rest of the backend; on top of
that each one needs a real paired device (the dashboard proxy's
``X-Puff-Device-Id``, checked against the pairing store), because adding a
tool server can start a command on this computer.

Routes::

    GET  /mcp/servers                     the saved tool servers (never a password)
    POST /mcp/servers                     {name, target, password?, label?} add one
                                          (a password is sent as a Bearer header)
    POST /mcp/servers/<name>/remove       remove one (tools, password, switch)
    POST /mcp/servers/<name>/refresh      ask it for its tools again
    POST /mcp/home-assistant              {address, token, keep_working_locked?}
    GET  /mcp/learn                       drafted skills waiting for a yes
    POST /mcp/learn                       {url?, text?, name?} draft a skill
    POST /mcp/learn/<name>/keep           {text?} her yes: add it
    POST /mcp/learn/<name>/discard        throw the draft away

The password or token travels only in the POST body, goes into the vault and
is never echoed. Pressing the button is her yes (BUILD_BRIEF.md: no new
gates beyond showing what it is and asking).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

bp = Blueprint("mcp", __name__)

_STATUS = {"unknown": 404, "locked": 409, "password": 400, "missing": 404,
           "unreachable": 502, "timeout": 504, "no_sdk": 501, "name": 400,
           "empty": 400, "address": 400, "command": 400, "headers": 400,
           "token": 400, "blocked": 409, "brain": 503, "status": 502}

_SECRETISH_ARGS = ("token", "password", "secret", "key")


PAIRED_ONLY = "Open this from a browser that is paired with your bot."


@bp.before_request
def _paired_only():
    """Every tool-server route needs the bot's own key (never the voice key)
    AND a real paired device (``api_server._get_chat_client_id`` checks the
    pairing store). Adding a tool server can start a command on this
    computer, so a loopback caller with only the key is not enough."""
    try:
        from puffpai import api_server
        owner = api_server._app_caller_class() == "owner"  # noqa: SLF001
        client = str(api_server._get_chat_client_id() or "")  # noqa: SLF001
    except Exception:  # noqa: BLE001 - not knowing who asks: nothing opens
        owner, client = False, ""
    if not owner or not client.startswith("device:"):
        return jsonify({"ok": False, "code": "not_paired", "message": PAIRED_ONLY}), 403
    return None


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _reply(out: dict):
    code = 200 if out.get("ok") else _STATUS.get(out.get("code", ""), 400)
    return jsonify(out), code


def _secret_in_address():
    if any(name in request.args for name in _SECRETISH_ARGS):
        return jsonify({"ok": False, "message": "A password is never sent in "
                        "the address. Nothing was kept."}), 400
    return None


@bp.route("/mcp/servers", methods=["GET"])
def mcp_servers():
    from puffpai import mcp_client
    return jsonify({"servers": mcp_client.servers(),
                    "ready": mcp_client.sdk_available(),
                    "home_assistant_address": mcp_client.home_assistant_address_hint()})


@bp.route("/mcp/servers", methods=["POST"])
def mcp_add():
    from puffpai import mcp_client
    bad = _secret_in_address()
    if bad:
        return bad
    body = _body()
    label = str(body.get("label") or body.get("name") or "").strip()[:60]
    password = str(body.get("password") or "").strip()
    headers = None
    if password:
        headers = {"Authorization": password if password.lower().startswith(
            "bearer ") else f"Bearer {password}"}
    name = mcp_client.slug(body.get("name") or label) \
        or mcp_client.slug(str(body.get("target") or "")) or "my_tool"
    return _reply(mcp_client.add_server(name, body.get("target"), headers,
                                        label=label or None, by="owner",
                                        source=str(body.get("source") or "")))


@bp.route("/mcp/servers/<name>/remove", methods=["POST"])
def mcp_remove(name):
    from puffpai import mcp_client
    return _reply(mcp_client.remove_server(name, by="owner"))


@bp.route("/mcp/servers/<name>/refresh", methods=["POST"])
def mcp_refresh(name):
    from puffpai import mcp_client
    return _reply(mcp_client.refresh_server(name))


@bp.route("/mcp/home-assistant", methods=["POST"])
def mcp_home_assistant():
    from puffpai import mcp_client
    bad = _secret_in_address()
    if bad:
        return bad
    body = _body()
    keep = body.get("keep_working_locked")
    return _reply(mcp_client.connect_home_assistant(
        body.get("address"), body.get("token"), by="owner",
        keep_working_locked=True if keep is None else bool(keep)))


@bp.route("/mcp/learn", methods=["GET"])
def mcp_drafts():
    from puffpai import skill_learn
    return jsonify({"drafts": skill_learn.drafts()})


@bp.route("/mcp/learn", methods=["POST"])
def mcp_learn():
    from puffpai import skill_learn
    body = _body()
    return _reply(skill_learn.draft(url=str(body.get("url") or ""),
                                    text=str(body.get("text") or ""),
                                    name=str(body.get("name") or "")))


@bp.route("/mcp/learn/<name>/keep", methods=["POST"])
def mcp_learn_keep(name):
    from puffpai import skill_learn
    text = _body().get("text")
    return _reply(skill_learn.approve(name, text if isinstance(text, str) else None))


@bp.route("/mcp/learn/<name>/discard", methods=["POST"])
def mcp_learn_discard(name):
    from puffpai import skill_learn
    return _reply(skill_learn.discard(name))
