"""HTTP routes for looks. A blueprint, registered by ``api_server`` (one line),
so every route sits behind the same API-key check, rate limit and CORS rules
as the rest of the backend; the dashboard reaches them only through its
paired proxy (prefix ``looks``). Being paired is the gate: a look is cosmetic,
reaches nothing outside and is undone in one step, so no ticket and no PIN.

Routes::

    GET  /looks               the look in use, undo, history, which pages follow
    POST /looks/find {words, brain?}   looks for the owner's words (nothing applied)
    POST /looks/use  {id}     use one (an id only; colours in the body are ignored)
    POST /looks/background {background}   clouds, stars, plain, soft, petals or confetti (undoable)
    POST /looks/undo          back one step
    POST /looks/reset         back to the bot's own look (undoable)

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from puffpai.looks import find as F
from puffpai.looks import package as P
from puffpai.looks import store

bp = Blueprint("looks", __name__)


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _look_row(found: dict, chosen=None) -> dict:
    # Try-on shows the look wearing the background she picked, so
    # what she tries on is what "Keep it" keeps.
    pkg, note = store.wearing_chosen(found["look"], chosen)
    row = P.summary(pkg)
    row.update(kind=found["kind"], default_on=bool(found["default_on"]),
               why=found["why"])
    if note:
        row["background_note"] = note
    return row


@bp.route("/looks", methods=["GET"])
def looks_state():
    return jsonify(store.state())


@bp.route("/looks/find", methods=["POST"])
def looks_find():
    body = _body()
    words = body.get("words")
    if not isinstance(words, str) or not words.strip():
        return jsonify({"ok": False, "looks": [],
                        "note": "Say what you would like it to look like."}), 400
    if len(words) > F.MAX_WORDS_CHARS:
        return jsonify({"ok": False, "looks": [],
                        "note": "That is a lot of words. Try a shorter description."}), 400
    ask = F.default_ask() if body.get("brain") is True else None
    result = F.find(words, ask=ask)
    store.remember_found(words, [f["look"] for f in result["looks"]])
    note = result["note"]
    if body.get("brain") is True and ask is None:
        note = (note + " " if note else "") + ("The bot's brain is not running "
                                               "right now, so it was not asked.")
    chosen = store.chosen_background()
    return jsonify({"ok": True, "looks": [_look_row(f, chosen) for f in result["looks"]],
                    "chosen_background": chosen, "note": note})


def _result(got: dict):
    out = {"ok": got["ok"], "changed": got["changed"], "message": got["message"]}
    out.update(store.state())
    return jsonify(out), (200 if got["ok"] else 404)


@bp.route("/looks/use", methods=["POST"])
def looks_use():
    return _result(store.use(_body().get("id")))


@bp.route("/looks/background", methods=["POST"])
def looks_background():
    got = store.set_background(_body().get("background"))
    out = {"ok": got["ok"], "changed": got["changed"], "message": got["message"]}
    out.update(store.state())
    return jsonify(out), (200 if got["ok"] else 400)


@bp.route("/looks/undo", methods=["POST"])
def looks_undo():
    got = store.undo()
    out = {"ok": got["ok"], "changed": got["changed"], "message": got["message"]}
    out.update(store.state())
    return jsonify(out), 200


@bp.route("/looks/reset", methods=["POST"])
def looks_reset():
    return _result(store.reset())


__all__ = ["bp"]
