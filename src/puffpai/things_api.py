"""HTTP routes for your bot's things (:mod:`puffpai.things`).

Registered on the backend app by ``api_server``, so every route sits behind
the same API-key check as the rest, and each one also needs a paired device
(the ``device:<id>`` the dashboard proxy asserts from its pairing cookie).

Routes::

    GET    /things                    [{id, kind, name, icon, blurb, on_home, page}]
    GET    /things/capabilities       what this bot can do: each capability and the
                                      things that cover it, or a gap ("Find something
                                      for this" searches its words)
    GET    /things/<id>               its page: recipe, tools, tables, where it came from;
                                      for a live card, app or data set that is an
                                      ingredient of a job: {ok, id, redirect} (its job's
                                      page, or the "Not used" list)
    POST   /things/<id>/home          {on: true|false} on the home page or not
    DELETE /things/<id>               take it out, through the existing undo
    GET    /things/<id>/media         [{name, kind, size, modified}] pictures, sound
                                      and video it made in its folder
    GET    /things/<id>/media/file?name=<name>  one of them, read-only
    POST   /things/<id>/run           {tool, args, from_app} a tool server's tool, from its
                                      form, or from its app screen after her yes on the
                                      page (from_app: only tools visible to apps); a tool
                                      with an app screen also sends back {ui: {uri, html},
                                      app_result} for the page's locked frame
    POST   /things/<id>/key           {name, value} "Put the key in my vault": a key the
                                      skill declares, kept for that skill only (vault open)
    DELETE /things/<id>/key           {name} take that key out of the vault
    POST   /things/<id>/host          {action: start|stop|restart} an app that runs on
                                      this computer (integrate.run_here), bound to 127.0.0.1
    GET    /things/<id>/host/logs?which=run|install  its last log lines, secrets hidden
    POST   /things/install-missing    {plan_id, yes: true} "Install what's missing": her one yes
                                      to a plan shown on a skill's page or a chat card
                                      (puffpai.deps); runs exactly its steps, each checked
                                      by Drawbridge first
    POST   /things/<id>/lookup        {topic} a live card that can search looks one plain
                                      subject up on its own outside service
    POST   /things/<id>/ask           {text, history} "Try it" and "Ask": the dashboard
                                      chat's stream (its steps and Yes/No questions,
                                      answered with POST /chat/approve), this thing
                                      loaded first; history is this page's last few
                                      turns, [{user, assistant}], oldest first

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from puffpai import things

bp = Blueprint("things", __name__)

PAIRED_ONLY = "Open this from a browser that is paired with your bot."
ASK_MAX = 1000
#: The page's talk so far that goes with an ask: this many turns, each side cut.
HISTORY_TURNS = 4
HISTORY_CHARS = 2000
_STATUS = {"unknown": 404, "book": 409, "failed": 409, "locked": 409}
#: A key is short; a body larger than this is not one.
KEY_BODY_MAX = 8192


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
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _reply(out: dict):
    code = 200 if out.get("ok") else _STATUS.get(out.get("code", ""), 400)
    return jsonify(out), code


@bp.route("/things", methods=["GET"])
def things_list():
    return jsonify(things.listing())


@bp.route("/things/capabilities", methods=["GET"])
def things_capabilities():
    from puffpai.finding import capabilities
    return jsonify({"ok": True, "capabilities": capabilities.coverage()})


@bp.route("/things/<thing_id>", methods=["GET"])
def thing_page(thing_id: str):
    out = things.page(thing_id)
    if out is None:
        return jsonify({"ok": False, "message": things.NO_THING}), 404
    return jsonify({"ok": True, **out})


@bp.route("/things/<thing_id>/home", methods=["POST"])
def thing_home(thing_id: str):
    on = _body().get("on")
    if not isinstance(on, bool):
        return jsonify({"ok": False, "message": "Say on or off."}), 400
    out = things.set_home(thing_id, on)
    return _reply(out if out.get("ok") else {**out, "code": "unknown"})


@bp.route("/things/<thing_id>", methods=["DELETE"])
def thing_remove(thing_id: str):
    return _reply(things.remove(thing_id))


@bp.route("/things/<thing_id>/media", methods=["GET"])
def thing_media(thing_id: str):
    from puffpai import thing_media as media
    thing = things.get(thing_id)
    if thing is None:
        return jsonify({"ok": False, "message": things.NO_THING}), 404
    return jsonify({"ok": True, "files": media.listing(thing)})


@bp.route("/things/<thing_id>/media/file", methods=["GET"])
def thing_media_file(thing_id: str):
    """One picture, sound or video the thing made, read-only. The name is
    checked against the thing's own list and its folder (puffpai.thing_media)."""
    from flask import send_file

    from puffpai import thing_media as media
    got = media.file_for(things.get(thing_id), request.args.get("name"))
    if got is None:
        return jsonify({"ok": False, "message": "There is no such file on this page."}), 404
    path, mimetype = got
    resp = send_file(path, mimetype=mimetype, conditional=True, max_age=0)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return resp


@bp.route("/things/<thing_id>/run", methods=["POST"])
def thing_run(thing_id: str):
    body = _body()
    return _reply(things.run_tool(thing_id, body.get("tool"), body.get("args"),
                                  from_app=body.get("from_app") is True))


@bp.route("/things/<thing_id>/key", methods=["POST", "DELETE"])
def thing_key(thing_id: str):
    if (request.content_length or 0) > KEY_BODY_MAX:
        return jsonify({"ok": False, "message": "That is too long to be a key."}), 413
    body = _body()
    if request.method == "DELETE":
        return _reply(things.remove_key(thing_id, body.get("name")))
    return _reply(things.put_key(thing_id, body.get("name"), body.get("value")))


@bp.route("/things/<thing_id>/host", methods=["POST"])
def thing_host(thing_id: str):
    return _reply(things.host_action(thing_id, _body().get("action")))


@bp.route("/things/<thing_id>/host/logs", methods=["GET"])
def thing_host_logs(thing_id: str):
    return _reply(things.host_logs(thing_id, request.args.get("which")))


@bp.route("/things/install-missing", methods=["POST"])
def thing_install_missing():
    if (request.content_length or 0) > KEY_BODY_MAX:
        return jsonify({"ok": False, "message": "That is too long."}), 413
    body = _body()
    # "plan_id": an id field, so the pasted-key guard reads it as one.
    return _reply(things.install_missing(body.get("plan_id"), said_yes=body.get("yes") is True,
                                         device=_device()))


@bp.route("/things/<thing_id>/lookup", methods=["POST"])
def thing_lookup(thing_id: str):
    return _reply(things.lookup(thing_id, _body().get("topic")))


def _history(raw) -> list[dict]:
    """The page's last few turns as the chat keeps them ({user, assistant})."""
    if not isinstance(raw, list):
        return []
    turns = []
    for turn in raw[-HISTORY_TURNS * 4:]:
        if not isinstance(turn, dict):
            continue
        user = str(turn.get("user") or "").strip()[:HISTORY_CHARS]
        assistant = str(turn.get("assistant") or "").strip()[:HISTORY_CHARS]
        if user or assistant:
            turns.append({"user": user, "assistant": assistant})
    return turns[-HISTORY_TURNS:]


@bp.route("/things/<thing_id>/ask", methods=["POST"])
def thing_ask(thing_id: str):
    from puffpai import api_server
    thing = things.get(thing_id)
    if thing is None:
        return jsonify({"ok": False, "message": things.NO_THING}), 404
    body = _body()
    text = " ".join(str(body.get("text") or "").split())[:ASK_MAX]
    if not text:
        return jsonify({"ok": False, "message": "Type what to ask."}), 400
    device = _device()
    if api_server._loop_ctx(device=device) is None:  # noqa: SLF001
        return jsonify({"ok": False, "message": "Your bot cannot use its tools right now, "
                                                "so ask in chat instead."}), 503
    return api_server._loop_openai_stream(  # noqa: SLF001
        text, _history(body.get("history")), "", device, None,
        extra_core=[things.ask_note(thing)])
