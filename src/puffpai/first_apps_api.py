"""HTTP routes for your first apps (:mod:`puffpai.first_apps`).

Registered on the backend app by ``api_server``, so every route sits behind
the same API-key check as the rest, and each one also needs a paired device
(the ``device:<id>`` the dashboard proxy asserts from its pairing cookie), the
same rule as the things routes. Turning an app on is local only (nothing is
downloaded, nothing reaches outside), so no consent ticket is asked for.

Routes::

    GET  /apps/first          {ok, apps: [{id, name, blurb, route, icon, on}]}
    POST /apps/first/turn     {id, on: true|false} -> {ok, app, line}

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from puffpai import first_apps

bp = Blueprint("first_apps", __name__)

PAIRED_ONLY = "Open this from a browser that is paired with your bot."


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


@bp.route("/apps/first", methods=["GET"])
def first_apps_list():
    return jsonify({"ok": True, "apps": first_apps.listing()})


@bp.route("/apps/first/turn", methods=["POST"])
def first_apps_turn():
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    app_id = body.get("id")
    on = body.get("on")
    if not isinstance(app_id, str) or not isinstance(on, bool):
        return jsonify({"ok": False, "message": "Say which app, and on or off."}), 400
    try:
        first_apps.set_on([app_id[:64]], on)
    except KeyError:
        return jsonify({"ok": False, "message": first_apps.NOT_FIRST}), 404
    except first_apps.NotSaved:
        return jsonify({"ok": False, "message": first_apps.NOT_SAVED}), 503
    try:
        from puffpai import api_server
        api_server._get_app_data().reload_manifests()  # noqa: SLF001
    except Exception:  # noqa: BLE001 - the data layer already knows every app
        pass
    row = next((a for a in first_apps.listing() if a["id"] == app_id), None)
    name = (row or {}).get("name") or app_id
    line = f"{name} is on." if on else f"{name} is off. Its saved data stays on this computer."
    return jsonify({"ok": True, "app": row, "line": line})
