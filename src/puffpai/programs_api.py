"""HTTP routes for her own programs and for opening her files (Developer page).

Registered on the backend app by ``api_server`` (one line), so every route
sits behind the same API-key check, rate limit and CORS rules as the rest;
and, like the ``/build`` routes, every route here needs a paired device. The
logic is :mod:`puffpai.programs`.

Routes::

    GET    /programs                {"programs": [row], "installed_desktop": [{name, path}]}
    POST   /programs                {"kind", "path" | "command", "name"?} -> {"program": row}
    DELETE /programs/<id>
    PATCH  /programs/<id>           {"always_allow": bool} -> {"program": row, "warning"?}
    POST   /programs/<id>/launch    {"file"?} -> {"ok": true}
    POST   /files/open              {"path"} -> {"ok": true}

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("programs", __name__)

PAIRED_ONLY = "Open this from a browser that is paired with your bot."
_ID = re.compile(r"^p[0-9a-f]{8}$")


def _device() -> str:
    try:
        from puffpai import api_server
        client = str(api_server._get_chat_client_id() or "")  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return ""
    return client if client.startswith("device:") else ""


@bp.before_request
def _paired_only():
    if not _device():
        return jsonify({"ok": False, "message": PAIRED_ONLY}), 403
    return None


def _body() -> dict:
    got = request.get_json(silent=True)
    return got if isinstance(got, dict) else {}


def _refused(exc: Exception, status: int = 400):
    return jsonify({"ok": False, "message": str(exc)}), status


def _no_such():
    from puffpai import programs
    return jsonify({"ok": False, "message": programs.NO_SUCH}), 404


@bp.route("/programs", methods=["GET"])
def programs_list():
    from puffpai import programs
    return jsonify({"ok": True, "programs": programs.rows(),
                    "installed_desktop": programs.installed_desktop()})


@bp.route("/programs", methods=["POST"])
def programs_add():
    from puffpai import programs
    body = _body()
    try:
        row = programs.register(str(body.get("kind") or ""), path=str(body.get("path") or ""),
                                command=str(body.get("command") or ""),
                                name=str(body.get("name") or ""))
    except programs.ProgramError as exc:
        return _refused(exc)
    return jsonify({"ok": True, "program": row})


@bp.route("/programs/<pid>", methods=["DELETE"])
def programs_remove(pid):
    from puffpai import programs
    if not _ID.match(str(pid)) or not programs.remove(pid):
        return _no_such()
    return jsonify({"ok": True})


@bp.route("/programs/<pid>", methods=["PATCH"])
def programs_change(pid):
    from puffpai import programs
    if not _ID.match(str(pid)):
        return _no_such()
    body = _body()
    if not isinstance(body.get("always_allow"), bool):
        return jsonify({"ok": False, "message": "Say on or off for Always allow."}), 400
    try:
        out = programs.set_always_allow(pid, body["always_allow"])
    except programs.ProgramError as exc:
        return _refused(exc, 404)
    return jsonify({"ok": True, **out})


@bp.route("/programs/<pid>/launch", methods=["POST"])
def programs_launch(pid):
    from puffpai import programs
    if not _ID.match(str(pid)):
        return _no_such()
    try:
        return jsonify(programs.launch(pid, str(_body().get("file") or "")))
    except programs.ProgramError as exc:
        return _refused(exc)
    except OSError as exc:
        return jsonify({"ok": False, "message": f"It did not start ({type(exc).__name__})."}), 500


@bp.route("/files/open", methods=["POST"])
def files_open():
    from puffpai import programs
    try:
        return jsonify(programs.open_file(str(_body().get("path") or "")))
    except programs.ProgramError as exc:
        return _refused(exc)
    except OSError as exc:
        return jsonify({"ok": False, "message": f"It did not open ({type(exc).__name__})."}), 500
