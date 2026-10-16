"""HTTP routes for announcements, offers, home and routines.

Registered on the backend app by ``api_server`` (one line), so every route
here sits behind the same API-key check, rate limit and CORS rules as the rest
of the backend. Every string a person sees is plain words; internal ids stay
in the ``id`` fields.

Routes::

    GET    /announcements            recent messages the bot sent on its own
    GET    /offers                   the one open offer, or null
    POST   /offers/<id>/accept       the owner's yes   {"time"?: "08:00"}
    POST   /offers/<id>/decline      the owner's no
    GET    /home                     where home is (rough), and what is stored
    POST   /home                     {"town", "allow_lookup"?, "ticket"?} or
                                     {"latitude", "longitude", "label"?, "timezone"?}
    DELETE /home                     forget home
    GET    /routines                 every routine, plus what the bot can do
    POST   /routines                 add one {"kind", "at"?, "every_minutes"?, ...}
    POST   /routines/<id>/on|off     switch one on (a fresh yes) or off
    DELETE /routines/<id>            remove one
    POST   /routines/speak           {"on": bool}  say updates out loud
    GET    /routines/waiting         questions a routine run is waiting on (her
                                     yes to a command); answered with POST
                                     /chat/approve from any paired device

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("routines", __name__)


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _row_view(row: dict) -> dict:
    from puffpai import routines
    return {
        "id": row.get("id"),
        "kind": row.get("kind"),
        "label": row.get("label"),
        "when": routines.spoken_when(row),
        "on": routines.is_runnable(row),
        "last_text": row.get("last_text"),
        "last_status": row.get("last_status"),
        **({"prompt": (row.get("params") or {}).get("prompt"),
            "skill": (row.get("params") or {}).get("skill"),
            "skill_note": row.get("skill_note") or "",
            "results": (row.get("results") or [])[:3]}
           if row.get("kind") == "daily_prompt" else {}),
    }


# ── announcements ────────────────────────────────────────────────────────

@bp.route("/announcements", methods=["GET"])
def announcements_list():
    from puffpai import announcer, routines
    try:
        limit = int(request.args.get("limit", 10))
    except ValueError:
        limit = 10
    box, why = announcer.box_voice_status() if routines.settings()["speak_aloud"] \
        else (False, "")
    return jsonify({
        "announcements": announcer.recent(max(1, min(limit, 50))),
        "speak_aloud": routines.settings()["speak_aloud"],
        "computer_speaks": box,
        "voice_note": why,
    })


# ── offers ───────────────────────────────────────────────────────────────

@bp.route("/offers", methods=["GET"])
def offers_current():
    from puffpai import offers
    return jsonify({"offer": offers.current_offer()})


@bp.route("/offers/<offer_id>/accept", methods=["POST"])
def offers_accept(offer_id):
    from puffpai import offers
    result = offers.accept(offer_id, by="owner", at=_body().get("time") or None)
    return jsonify(result), (200 if result.get("ok") else 400)


@bp.route("/offers/<offer_id>/decline", methods=["POST"])
def offers_decline(offer_id):
    from puffpai import offers
    result = offers.decline(offer_id, by="owner")
    return jsonify(result), (200 if result.get("ok") else 400)


# ── home ─────────────────────────────────────────────────────────────────

def _home_view(extra: dict | None = None) -> dict:
    from puffpai import home_location
    allowed, why = home_location.geocode_allowed()
    out = {"home": home_location.get_home(),
           "what_is_stored": home_location.WHAT_IS_STORED,
           "town_lookup_allowed": allowed,
           "town_lookup_note": why}
    out.update(extra or {})
    return out


def _switch_town_lookup_on(ticket):
    """Turn the town lookup on for ``allow_lookup``, or a refusal response.

    Switching an outside service on needs a consent ticket minted by
    ``POST /outside/geocode/confirm`` on the same paired device -- the one rule
    every "switch on" follows (puffpai.security.consent_tickets). A bot set to
    stay off the internet is not widened from here: that answer stays 409 with
    the plain reason, exactly as before.
    """
    from puffpai import outside_api, outside_services
    from puffpai.security import egress_doors
    if egress_doors.run_posture().get("run_egress") != egress_doors.OPEN_POSTURE:
        return None  # the caller's 409 says why, in plain words
    ok, why = outside_api.redeem_for(ticket, "outside_on", "geocode")
    if not ok:
        return jsonify(_home_view({"ok": False, "message": why})), 403
    try:
        outside_services.switch_on("geocode", by="owner")
    except outside_services.OutsideError as exc:
        return jsonify(_home_view({"ok": False, "message": exc.message})), 409
    return None


@bp.route("/home", methods=["GET"])
def home_get():
    return jsonify(_home_view())


@bp.route("/home", methods=["POST"])
def home_set():
    from puffpai import home_location
    data = _body()
    try:
        if data.get("town"):
            allowed, why = home_location.geocode_allowed()
            if not allowed and data.get("allow_lookup") is True:
                refused = _switch_town_lookup_on(data.get("ticket"))
                if refused is not None:
                    return refused
                allowed, why = home_location.geocode_allowed()
            if not allowed:
                return jsonify(_home_view({"ok": False, "message": why})), 409
            home, others = home_location.set_home_town(str(data["town"]))
            return jsonify(_home_view({"ok": True, "other_matches": others}))
        home = home_location.set_home_coords(
            data.get("latitude"), data.get("longitude"),
            label=str(data.get("label") or ""),
            timezone_name=str(data.get("timezone") or ""))
        return jsonify(_home_view({"ok": True}))
    except home_location.HomeError as exc:
        return jsonify(_home_view({"ok": False, "message": str(exc)})), 400


@bp.route("/home", methods=["DELETE"])
def home_clear():
    from puffpai import home_location
    home_location.clear_home()
    return jsonify(_home_view({"ok": True}))


# ── routines ─────────────────────────────────────────────────────────────

@bp.route("/routines", methods=["GET"])
def routines_list():
    from puffpai import routines
    return jsonify({
        "routines": [_row_view(r) for r in routines.list_routines()],
        "settings": routines.settings(),
        "abilities": routines.ability_rows(),
        # The skills a daily task can use (its card has a picker).
        "skills": routines.skill_choices(),
    })


@bp.route("/routines", methods=["POST"])
def routines_add():
    from puffpai import routines
    data = _body()
    params = {k: data[k] for k in ("message", "feed_id", "above", "below", "new_items",
                                    "prompt", "skill")
              if k in data}
    try:
        row = routines.add_routine(
            str(data.get("kind") or ""), routine_id=data.get("id") or None,
            label=str(data.get("label") or ""), daily_at=data.get("at") or None,
            every_minutes=data.get("every_minutes"), once_at=data.get("once_at") or None,
            params=params, by="owner")
    except routines.RoutineError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    return jsonify({"ok": True, "routine": _row_view(row)})


@bp.route("/routines/waiting", methods=["GET"])
def routines_waiting():
    from puffpai import brain_loop
    return jsonify({"waiting": brain_loop.waiting_for_anyone()})


@bp.route("/routines/speak", methods=["POST"])
def routines_speak():
    from puffpai import announcer, routines
    s = routines.set_speak_aloud(bool(_body().get("on")))
    box, why = announcer.box_voice_status() if s["speak_aloud"] else (False, "")
    return jsonify({"ok": True, "speak_aloud": s["speak_aloud"],
                    "computer_speaks": box, "voice_note": why})


@bp.route("/routines/<routine_id>/skill", methods=["POST"])
def routines_skill(routine_id):
    """Her pick of the skill a daily task uses. Body ``{"skill": "<folder>"}``,
    or ``""`` for none."""
    from puffpai import routines
    try:
        row = routines.set_skill(routine_id, _body().get("skill"))
    except routines.RoutineError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    if row is None:
        return jsonify({"ok": False, "message": "There is no daily task with that name."}), 404
    return jsonify({"ok": True, "routine": _row_view(row)})


@bp.route("/routines/<routine_id>/<action>", methods=["POST"])
def routines_switch(routine_id, action):
    from puffpai import routines
    if action not in ("on", "off"):
        return jsonify({"ok": False, "message": "Use on or off."}), 404
    row = routines.set_enabled(routine_id, action == "on", by="owner")
    if row is None:
        return jsonify({"ok": False, "message": "There is no routine with that name."}), 404
    return jsonify({"ok": True, "routine": _row_view(row)})


@bp.route("/routines/<routine_id>", methods=["DELETE"])
def routines_remove(routine_id):
    from puffpai import routines
    if not routines.remove_routine(routine_id, by="owner"):
        return jsonify({"ok": False, "message": "There is no routine with that name."}), 404
    return jsonify({"ok": True})


# ── chat helpers the backend's chat routes call ──────────────────────────

def chat_answer(message: str):
    """The real answer to an updates question, or None. Never raises."""
    try:
        from puffpai import offers
        return offers.chat_answer(message)
    except Exception as exc:
        logger.warning("routines: chat answer failed: %s", exc)
        return None


def claims(message: str) -> bool:
    try:
        from puffpai import offers
        return offers.is_routines_message(message)
    except Exception:
        return False


def reply_extras() -> tuple[str, bool]:
    """(prefix, offer_due) for a chat reply: "While you were away", and
    whether the first offer may still go under it (see :func:`reply_suffix`)."""
    prefix = ""
    due = False
    try:
        from puffpai import announcer
        prefix = announcer.take_chat_catchup() or ""
    except Exception as exc:
        logger.warning("routines: catch-up failed: %s", exc)
    try:
        from puffpai import offers
        due = offers.followup_due()
    except Exception as exc:
        logger.warning("routines: offer check failed: %s", exc)
    return prefix, due


def reply_suffix(reply: str) -> str:
    """The first offer under *reply*, or "" (also when *reply* asks a question)."""
    try:
        from puffpai import offers
        return offers.chat_followup(reply) or ""
    except Exception as exc:
        logger.warning("routines: offer follow-up failed: %s", exc)
        return ""


def start(bus_getter=None) -> None:
    """Start the scheduler thread (``PUFFPAI_SCHEDULER=0`` keeps it off)."""
    from puffpai import routines, scheduler
    if bus_getter is not None:
        routines.set_bus_getter(bus_getter)
    started = scheduler.start_scheduler()
    print("[Startup] Routines clock " + ("running." if started else
                                         "off (PUFFPAI_SCHEDULER=0)."))


__all__ = ["bp", "chat_answer", "claims", "reply_extras", "reply_suffix", "start"]
