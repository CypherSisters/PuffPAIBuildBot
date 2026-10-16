"""HTTP routes for the dashboard's "Add to <bot>" page (:mod:`puffpai.add_more`).

Registered on the backend app by ``api_server`` (one line), so every route
sits behind the same API-key check, rate limit and CORS rules as the rest of
the backend. The dashboard reaches them only through its paired proxy.

Routes::

    GET  /add                          the page's title and lines
    POST /add/find                     {"words", "online"?} -> the finds
    GET  /add/looking                  how the online search is going, per library
    GET  /add/<session>                the finds, with how far each has got
    POST /add/<session>/confirm        {"ids"} -> the server's sentence + a yes
    POST /add/<session>/approve        {"ids", "ticket", "home"?} -> one result per line
    POST /add/switch-on/<service>/confirm   sentence + a yes for one website
    POST /add/switch-on/<service>           {"ticket"} switch that website on
    GET  /add/added                    what was added since the build
    POST /add/added/<entry>/remove     take one out (no yes needed)
    POST /add/added/<entry>/home       {"on"} put it on the home page, or take it off
    GET  /add/notes                    the words nothing was found for
    POST /add/notes/clear              forget them

Who may say yes
---------------
Only a paired device (``device:<id>`` from the dashboard proxy's
``X-Puff-Device-Id``). The yes is a consent ticket for exactly the lines
shown, on that device, used once, within ten minutes. No PIN: being paired
is the gate. A request that carries anything but item ids from its own
session -- an address, a catalog record -- is ignored or refused.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re
import threading
import time

from flask import Blueprint, jsonify, request

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("add_more", __name__)

_SERVICE_RE = re.compile(r"^card-[a-z0-9][a-z0-9_\-]{0,63}$")
_ENTRY_RE = re.compile(r"^(?:a_[0-9a-f]{8}|\d{1,4})$")
#: Searches per client per minute (each may ask the book libraries online).
FINDS_PER_MINUTE = 6
_finds: dict[str, list[float]] = {}
_finds_lock = threading.Lock()

_NOT_PAIRED = ("Adding things has to be done from a browser that is paired "
               "with your bot.")


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _client() -> str:
    try:
        from puffpai import api_server
        return str(api_server._get_chat_client_id() or "")  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return ""


def _device() -> str:
    client = _client()
    return client if client.startswith("device:") else ""


def _refuse(exc) -> tuple:
    status = {"gone": 404, "unknown_item": 400, "none_picked": 400,
              "too_many": 400, "unknown": 404, "cannot": 409, "already": 409,
              "not_set_up": 409, "throttled": 429}.get(getattr(exc, "code", ""), 403)
    return jsonify({"ok": False, "message": getattr(exc, "message", str(exc))}), status


def _throttled(client: str) -> bool:
    now = time.monotonic()
    with _finds_lock:
        recent = [t for t in _finds.get(client, []) if now - t < 60]
        if len(recent) >= FINDS_PER_MINUTE:
            _finds[client] = recent
            return True
        recent.append(now)
        _finds[client] = recent
        if len(_finds) > 256:
            for key in list(_finds)[:64]:
                _finds.pop(key, None)
    return False


def _ids(body: dict):
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return None
    return ids


@bp.route("/add", methods=["GET"])
def add_page():
    from puffpai import add_more
    return jsonify({"title": f"Find more for {add_more.bot_name()}",
                    "bot_name": add_more.bot_name(),
                    "line": "Type what you want your bot to know or show. "
                            "It looks in the lists saved on this computer "
                            "first.",
                    "paired": bool(_device())})


@bp.route("/add/find", methods=["POST"])
def add_find():
    from puffpai import add_more
    body = _body()
    words = body.get("words")
    if not isinstance(words, str) or not words.strip():
        return jsonify({"ok": False, "message": "Type what you want to add."}), 400
    if len(words) > 200:
        return jsonify({"ok": False, "message": "That is too long. Use a few "
                                                "words."}), 400
    client = _client()
    if not client or client.startswith("anon:"):
        return jsonify({"ok": False, "message": _NOT_PAIRED}), 403
    if _throttled(client):
        return jsonify({"ok": False, "message": "Too many searches in a minute. "
                                                "Wait a moment."}), 429
    session = add_more.find(words, via="page", client=client,
                            online=body.get("online") is True)
    return jsonify({"ok": True, **add_more.public(session, "browser")})


@bp.route("/add/looking", methods=["GET"])
def add_looking():
    """The Add page asks this while "Look online too" runs: which libraries
    have answered so far. Nothing here leaves the computer."""
    from puffpai import add_more
    client = _client()
    if not client or client.startswith("anon:"):
        return jsonify({"ok": False, "message": _NOT_PAIRED}), 403
    return jsonify({"ok": True, **add_more.looking(client)})


@bp.route("/add/<session_id>", methods=["GET"])
def add_session(session_id):
    from puffpai import add_more
    try:
        session = add_more.get_session(session_id, _client())
    except add_more.AddError as exc:
        return _refuse(exc)
    out = add_more.public(session, "browser")
    out["progress"] = add_more.progress_lines(session, "browser")
    return jsonify({"ok": True, **out})


@bp.route("/add/<session_id>/confirm", methods=["POST"])
def add_confirm(session_id):
    from puffpai import add_more
    device = _device()
    if not device:
        return jsonify({"ok": False, "message": _NOT_PAIRED}), 403
    ids = _ids(_body())
    if ids is None:
        return jsonify({"ok": False, "message": "Pick at least one thing to "
                                                "add."}), 400
    try:
        return jsonify(add_more.confirm(session_id, ids, client=_client(),
                                        device=device))
    except add_more.AddError as exc:
        return _refuse(exc)


@bp.route("/add/<session_id>/approve", methods=["POST"])
def add_approve(session_id):
    from puffpai import add_more
    device = _device()
    if not device:
        return jsonify({"ok": False, "message": _NOT_PAIRED}), 403
    body = _body()
    ids = _ids(body)
    if ids is None:
        return jsonify({"ok": False, "message": "Pick at least one thing to "
                                                "add."}), 400
    home = body.get("home")
    if home is not None and not (isinstance(home, list)
                                 and all(isinstance(i, str) for i in home)):
        home = None
    try:
        results = add_more.approve(session_id, ids, client=_client(),
                                   device=device, surface="browser",
                                   ticket=body.get("ticket"), home=home)
    except add_more.AddError as exc:
        return _refuse(exc)
    return jsonify({"ok": True, "results": results})


@bp.route("/add/switch-on/<service>/confirm", methods=["POST"])
def add_switch_confirm(service):
    from puffpai import add_more, outside_api
    if not _SERVICE_RE.match(str(service or "")):
        return jsonify({"ok": False, "message": "There is no card by that "
                                                "name."}), 404
    try:
        said = add_more.switch_on_sentence(service)
    except add_more.AddError as exc:
        return _refuse(exc)
    return outside_api.mint_for("outside_on", service, said)


@bp.route("/add/switch-on/<service>", methods=["POST"])
def add_switch_on(service):
    from puffpai import add_more
    if not _SERVICE_RE.match(str(service or "")):
        return jsonify({"ok": False, "message": "There is no card by that "
                                                "name."}), 404
    device = _device()
    if not device:
        return jsonify({"ok": False, "message": _NOT_PAIRED}), 403
    try:
        line = add_more.switch_on(service, ticket=_body().get("ticket"),
                                  device=device, surface="browser")
    except add_more.AddError as exc:
        return _refuse(exc)
    return jsonify({"ok": True, "line": line})


@bp.route("/add/added", methods=["GET"])
def add_added():
    from puffpai import add_more
    rows = [{"n": r["n"], "entry": r["entry"], "label": r["label"],
             "added_on": str(r.get("at") or "")[:10],
             "from": r.get("device") or "", "websites": r.get("hosts") or [],
             "home": add_more.home_state(r)}
            for r in add_more.added()]
    return jsonify({"ok": True, "rows": rows})


@bp.route("/add/added/<entry>/remove", methods=["POST"])
def add_remove(entry):
    from puffpai import add_more
    if not _ENTRY_RE.match(str(entry or "")):
        return jsonify({"ok": False, "message": "There is nothing added under "
                                                "that name."}), 404
    try:
        line = add_more.take_out(entry, by="owner")
    except add_more.AddError as exc:
        return _refuse(exc)
    return jsonify({"ok": True, "line": line})


@bp.route("/add/added/<entry>/home", methods=["POST"])
def add_home(entry):
    """Put an addition on the home page, or take it off. A local choice about
    this bot's own page: nothing is fetched or sent, so no yes is needed."""
    from puffpai import add_more
    if not _ENTRY_RE.match(str(entry or "")):
        return jsonify({"ok": False, "message": "There is nothing added under "
                                                "that name."}), 404
    if not _device():
        return jsonify({"ok": False, "message": _NOT_PAIRED}), 403
    try:
        line = add_more.set_home(entry, _body().get("on") is True)
    except add_more.AddError as exc:
        return _refuse(exc)
    return jsonify({"ok": True, "line": line})


@bp.route("/add/notes", methods=["GET"])
def add_notes():
    from puffpai import add_more
    return jsonify({"ok": True, "notes": add_more.wishes(),
                    "line": "Words you asked for that nothing was found for. "
                            "They stay on this computer."})


@bp.route("/add/notes/clear", methods=["POST"])
def add_notes_clear():
    from puffpai import add_more
    return jsonify({"ok": True, "cleared": add_more.forget_wishes()})


__all__ = ["bp"]
