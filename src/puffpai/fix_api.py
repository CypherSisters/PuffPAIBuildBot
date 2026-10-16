"""Fix it now: the one-tap cards a chat answer can carry, and the route they use.

Instead of a chat answer like "Weather check is switched off ... That's a
setting that needs to be enabled from the terminal.", the bot asks "would you
like to switch that on now?", and, when her town is not saved, "would you like
to enter your town now?". So a tool result that says
something is off or missing becomes a card in the chat with one button.

:func:`fixes_for` reads one tool result (goal-agnostic: it knows the outside
switches, the home town, sign-up keys and missing programs, never a topic) and
gives back ``fix`` dicts:

* ``switch_on`` -- an outside service is switched off (the service's own
  "switched off" sentence, or a Bridge refusal "egress refused [id]"). The card
  shows the server's own sentence for switching it on (what leaves, where to).
* ``setting`` -- a needed setting is not saved. Today that is the home town
  (``key: "home"``, ``input: "place"``), the one setting tools ask for.
* ``key`` -- a sign-up key is missing. The card links to the Sign-up keys page:
  a key is never typed into the chat.
* ``assemble_yes`` -- an app BuildBot put together waits for the owner's one
  yes (``puffpai.assemble.consent``). The card is that app's Start card
  (``key`` is the app id); the tap is her yes, spent the same way.
* ``install`` -- a program or package is missing. This is the existing
  "Install what's missing" card (:func:`brain_commands.missing_card`), sent as
  it always was.

A tool may also hand back its own ``fix`` dict; it is checked like the rest.
:func:`clean` is the one place a fix becomes a card: it also reads the shape
an assembled app's parts give (``puffpai.assemble``): ``{"kind": "switch_on",
"door", "service"}`` (the switch a source sits behind) and ``{"kind":
"setting", "app_id", "key"}`` (an app setting; a place that uses the bot's
home town becomes the same town card, one home town for the whole bot).

``POST /fix/card`` gives the card for a fix an assembled app's own page asks
its dashboard page to show (the app's page never holds a ticket or a key).
It only gives cards for switches the app's own sources sit behind.

``POST /fix/apply`` carries out a ``switch_on`` or a ``setting``:

* ``switch_on`` goes exactly the way the switch on "What leaves your computer"
  goes: the server's own sentence, a consent ticket minted and spent for this
  paired device and this one switch (both written to the audit trail), then
  :func:`outside_services.switch_on`. Her tap on the card is that yes.
* ``setting`` saves the home town through :mod:`puffpai.home_location`: the
  town lookup asks the one outside website for it (switched on the same way if
  it is off, the card says what that sends), or, when the bot keeps
  everything on this computer, a rough position she types.

Both need a paired device and write one audit line (never the town itself).
Then the chat page asks her last question again.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from flask import Blueprint, jsonify, request

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("fix", __name__)

KINDS = ("switch_on", "setting", "key", "install", "assemble_yes")
#: Settings a card can save. The home town is the one tools ask for.
SETTINGS = {"home": "place"}
#: At most this many cards from one tool result.
MAX_FIXES = 3

_SID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
_EGRESS_RE = re.compile(r"egress refused \[([A-Za-z0-9_.:\-]{1,64})\]")
#: "<name> is switched off" at the start of a sentence.
_OFF_RE = re.compile(
    r"(?:^|[.!?]\s+|\n|\"|:\s+)\s*(?:the\s+)?([A-Za-z0-9][A-Za-z0-9 '&()+/-]{1,60}?)"
    r"\s+(?:is|are)\s+(?:still\s+)?switched off", re.IGNORECASE)
_TOWN_RE = re.compile(
    r"(?:don'?t|do not|doesn'?t|does not)\s+know\s+(?:your|the|my|its)\s+"
    r"(?:home\s+)?(?:town|city|location)"
    r"|(?:home\s+town|town|city|location)\s+(?:is\s+not|isn'?t|is)\s+(?:not\s+)?"
    r"(?:saved|set)(?:\s+yet)?"
    r"|no\s+(?:home\s+)?(?:town|city|location)\s+(?:is\s+)?(?:saved|set)",
    re.IGNORECASE)
_KEY_RE = re.compile(
    r"([A-Z][\w .&+'-]{1,40}?) needs a free key"
    r"|Add your ([\w .&+'-]{1,40}?) key on the Sign-up keys page")
_SIGNUP_RE = re.compile(r"open (https://[^\s)]+?)[.,]?(?:\s|$)")
_MISSING_PROGRAM_RE = re.compile(
    r"command not found|is not recognized as an internal or external command"
    r"|No module named|Cannot find module|ModuleNotFoundError|is not installed",
    re.IGNORECASE)

TOWN_LABEL = "Enter your town now?"
TOWN_LOOKUP_SENDS = ("Saving it looks your town up once at "
                     "geocoding-api.open-meteo.com, sending only the name you type.")
POSITION_NEEDED = ("This bot keeps everything on this computer, so it can't look "
                   "your town up online. Type its rough position too: latitude "
                   "and longitude.")
NOT_PAIRED = ("This has to be done from a browser that is paired with your bot.")
KEY_HERE = "Keys go in on the Sign-up keys page, never in the chat."

#: What the model reads next to a tool result that got a card.
FIX_NOTE = ("The owner now sees a card in the chat with one button for this: {labels} "
            "Ask her in one short line whether she wants to do it now (like \"Would "
            "you like to switch that on now?\"). Do not send her to a terminal, a "
            "command or a settings file.")


# -- reading a tool result -------------------------------------------------------

def _text_of(got: dict) -> str:
    """Everything a tool result says, as one text."""
    parts = [str(got.get("error") or "")]
    result = got.get("result")
    if isinstance(result, str):
        parts.append(result)
    elif result is not None:
        try:
            parts.append(json.dumps(result, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            parts.append(str(result))
    return "\n".join(p for p in parts if p)


def _plain_name(value: Any, limit: int = 60) -> str:
    text = re.sub(r"[^\w .,&()'+/:-]", " ", str(value or ""))
    return " ".join(text.split())[:limit].strip(" ,.:-")


def _switch_card(row: dict) -> Optional[dict]:
    """The switch_on card for one outside service row, or None when it cannot
    be switched on from here (or is on already)."""
    if not row or row.get("state") == "on" or not row.get("can_switch"):
        return None
    try:
        from puffpai import outside_services
        detail = outside_services.proposal(row["id"])
    except Exception:  # noqa: BLE001 - no sentence, no card
        return None
    name = _plain_name(row.get("name")) or "this"
    return {"kind": "switch_on", "service": row["id"], "name": name,
            "label": f"Switch on {name} now?", "detail": detail}


def _switch_fixes(text: str) -> list[dict]:
    ids = _EGRESS_RE.findall(text)
    phrases = [m.strip().lower() for m in _OFF_RE.findall(text)]
    hints: list[str] = []
    try:
        from puffpai.security import egress_doors
        for d in egress_doors.DOORS:
            first = str(d.settings_hint or "").split(", so", 1)[0].strip()
            if first and first in text:
                hints.append(d.id)
    except Exception:  # noqa: BLE001
        pass
    if not (ids or phrases or hints):
        return []
    try:
        from puffpai import outside_services
        rows = outside_services.rows()
    except Exception:  # noqa: BLE001 - no list, no card
        return []
    by_id = {r["id"]: r for r in rows}
    picked: list[dict] = []
    for sid in ids + hints:
        row = by_id.get(sid.lower()) or by_id.get(f"card-{sid.lower()}")
        if row is not None and row not in picked:
            picked.append(row)
    for phrase in phrases:
        exact = [r for r in rows if r["name"].lower() == phrase]
        near = exact or [r for r in rows if r["name"].lower() in phrase
                         or phrase in r["name"].lower()]
        if len(near) == 1 and near[0] not in picked:
            picked.append(near[0])
    return [c for c in (_switch_card(r) for r in picked) if c]


def town_card() -> dict:
    """The setting card for the home town."""
    try:
        from puffpai import home_location
        stored = home_location.WHAT_IS_STORED
        allowed, _why = home_location.geocode_allowed()
        from puffpai.security import egress_doors
        offline = (egress_doors.run_posture().get("run_egress")
                   != egress_doors.OPEN_POSTURE)
    except Exception:  # noqa: BLE001
        stored, allowed, offline = "", False, True
    detail = stored
    if not allowed and not offline:
        detail = f"{stored} {TOWN_LOOKUP_SENDS}".strip()
    return {"kind": "setting", "key": "home", "input": "place", "label": TOWN_LABEL,
            "detail": detail, "position": bool(offline and not allowed)}


def _town_fixes(text: str) -> list[dict]:
    if not _TOWN_RE.search(text):
        return []
    try:
        from puffpai import home_location
        if home_location.get_home() is not None:
            return []
    except Exception:  # noqa: BLE001
        return []
    return [town_card()]


def _key_fixes(text: str) -> list[dict]:
    out = []
    for m in _KEY_RE.finditer(text):
        name = _plain_name(m.group(1) or m.group(2), 40)
        if not name:
            continue
        url = _SIGNUP_RE.search(text[m.end():m.end() + 300])
        out.append({"kind": "key", "name": name, "label": f"Add your {name} key now?",
                    "get_url": url.group(1) if url else ""})
    return out


def _install_fixes(text: str) -> list[dict]:
    if not _MISSING_PROGRAM_RE.search(text):
        return []
    try:
        from puffpai import brain_commands
        card = brain_commands.missing_card("", text, None)
    except Exception:  # noqa: BLE001
        card = None
    if not card:
        return []
    return [{"kind": "install", "label": "Install what's missing now?", "card": card}]


_APP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,100}\Z")
_DOOR_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,80}\Z")


def row_id_for_switch(switch: Any) -> str:
    """The outside list's row id for a switch id, or "".

    A live card's switch ``app:<id>:feed`` is the list's ``card-<id>`` row; a
    built-in switch is its own row."""
    sid = str(switch or "").strip()
    if not _DOOR_RE.match(sid):
        return ""
    try:
        from puffpai import outside_services
        from puffpai.security import egress_doors
        prefix = egress_doors.APP_DOOR_PREFIX
        if sid.startswith(prefix) and sid.endswith(":feed"):
            row_id = outside_services._CARD_PREFIX + sid[len(prefix):-len(":feed")]  # noqa: SLF001
        else:
            row_id = sid.lower()
    except Exception:  # noqa: BLE001
        return ""
    return row_id.lower() if _SID_RE.match(row_id.lower()) else ""


def _app_setting(app_id: Any, key: Any) -> Optional[dict]:
    """An assembled app's declared setting, or None."""
    if not _APP_ID_RE.match(str(app_id or "")):
        return None
    try:
        from puffpai.assemble import api as assemble_api
        spec = assemble_api.load_spec(str(app_id))
    except Exception:  # noqa: BLE001
        return None
    for st in (spec or {}).get("settings") or []:
        if isinstance(st, dict) and st.get("key") == key:
            return st
    return None


def app_switch_rows(app_id: Any) -> set:
    """The outside list rows an assembled app's own sources sit behind."""
    if not _APP_ID_RE.match(str(app_id or "")):
        return set()
    try:
        from puffpai.assemble import api as assemble_api
        spec = assemble_api.load_spec(str(app_id)) or {}
    except Exception:  # noqa: BLE001
        return set()
    out = set()
    for src in spec.get("sources") or []:
        if isinstance(src, dict) and src.get("door"):
            row_id = row_id_for_switch(src.get("door"))
            if row_id:
                out.add(row_id)
    return out


def clean(fix: Any) -> Optional[dict]:
    """A tool's own fix dict, checked, or None."""
    if not isinstance(fix, dict) or fix.get("kind") not in KINDS:
        return None
    kind = fix["kind"]
    if kind == "switch_on" and not fix.get("door") and \
            str(fix.get("service") or "").startswith("seed-"):
        # A website on BuildBot's list that is not a live card yet.
        from puffpai.assemble import seed_switch
        return seed_switch.card(str(fix["service"])[len(seed_switch.PREFIX):])
    if kind == "switch_on":
        # An assembled app's part names the switch it sits behind (``door``);
        # that decides the row. Otherwise ``service`` is the row id itself.
        sid = row_id_for_switch(fix.get("door")) if fix.get("door") else ""
        sid = sid or str(fix.get("service") or fix.get("switch") or "").lower()
        if not _SID_RE.match(sid):
            return None
        try:
            from puffpai import outside_services
            row = outside_services.row(sid)
        except Exception:  # noqa: BLE001
            row = None
        return _switch_card(row) if row else None
    if kind == "setting":
        if fix.get("key") in SETTINGS:
            return town_card()
        # An assembled app's place setting that uses the bot's home town is
        # the same town card (one home town for the whole bot).
        st = _app_setting(fix.get("app_id"), fix.get("key"))
        if st and st.get("kind") == "place" and st.get("use") == "home_location":
            return town_card()
        return None
    if kind == "key":
        name = _plain_name(fix.get("name") or fix.get("service") or fix.get("key"), 40)
        return {"kind": "key", "name": name, "label": f"Add your {name} key now?",
                "get_url": str(fix.get("get_url") or "")[:300]} if name else None
    if kind == "assemble_yes":
        return _assemble_card(fix.get("app_id") or fix.get("key"))
    card = fix.get("card")
    return {"kind": "install", "label": "Install what's missing now?",
            "card": card} if isinstance(card, dict) else None


def _assemble_card(app_id: Any) -> Optional[dict]:
    """An assembled app's Start card, while it still waits for her yes."""
    if not _APP_ID_RE.match(str(app_id or "")):
        return None
    try:
        from puffpai.assemble import consent
        got = consent.card(str(app_id))
    except Exception:  # noqa: BLE001
        return None
    if not got or got["said_yes"]:
        return None
    return {"kind": "assemble_yes", "key": got["app_id"], "name": got["title"],
            "label": got["lines"][0], "detail": " ".join(got["lines"][1:])}


def _key(fix: dict) -> tuple:
    return (fix["kind"], fix.get("service") or fix.get("key") or fix.get("name") or "")


def fixes_for(got: Any) -> list[dict]:
    """The fix-it-now cards for one tool result (``{"ok", "result", "error"}``).

    Pops a tool's own ``fix`` from its result. Never raises."""
    if not isinstance(got, dict):
        return []
    out: list[dict] = []
    result = got.get("result")
    own = result.pop("fix", None) if isinstance(result, dict) else None
    for item in (own if isinstance(own, list) else [own]):
        fixed = clean(item)
        if fixed:
            out.append(fixed)
    try:
        text = _text_of(got)
        for finder in (_switch_fixes, _town_fixes, _key_fixes):
            out.extend(finder(text))
        if not (isinstance(result, dict) and result.get("missing")):
            out.extend(_install_fixes(text))
    except Exception:  # noqa: BLE001 - a card we cannot make is no card
        logger.info("fix cards: could not read a tool result", exc_info=True)
    seen: set = set()
    unique = []
    for fix in out:
        if _key(fix) not in seen:
            seen.add(_key(fix))
            unique.append(fix)
    return unique[:MAX_FIXES]


def public(fix: Any) -> dict:
    """A fix as the chat page gets it (words and ids only)."""
    fix = fix if isinstance(fix, dict) else {}
    out = {k: str(fix.get(k) or "")[:600] for k in
           ("kind", "label", "service", "name", "detail", "key", "input", "get_url")}
    out["position"] = bool(fix.get("position"))
    return out


def note_for(fixes: list[dict]) -> str:
    """What the model reads beside the tool result that got these cards."""
    labels = " ".join(str(f.get("label") or "") for f in fixes)
    return FIX_NOTE.format(labels=labels)


# -- carrying one out -------------------------------------------------------------

def _audit(action: str, result: str, details: str) -> None:
    try:
        from puffpai.security.audit_log import AuditLog
        AuditLog().log(action=action, user="owner", result=result, details=details)
    except Exception as exc:  # noqa: BLE001 - the ticket's own lines are kept
        logger.debug("fix audit line skipped: %s", exc)


def _paired() -> str:
    from puffpai import outside_api
    return outside_api.paired_device_id()


def _switch_on(sid: str, device: str) -> tuple[dict, int]:
    """Switch *sid* on the way the outside page does: the server's sentence,
    a ticket for this device and this switch, spent at once, then the switch."""
    from puffpai import outside_services
    from puffpai.security import consent_tickets
    try:
        sentence = outside_services.proposal(sid)
    except outside_services.OutsideError as exc:
        return {"ok": False, "message": exc.message}, 409
    try:
        ticket = consent_tickets.mint("outside_on", sid, sentence, device)
    except consent_tickets.TicketRefused as exc:
        return {"ok": False, "message": exc.message}, (429 if exc.code == "throttled"
                                                      else 403)
    ok, verdict = consent_tickets.redeem(ticket, device, "outside_on", sid)
    if not ok:
        return {"ok": False, "message": consent_tickets.refusal_words(verdict)}, 403
    try:
        row = outside_services.switch_on(sid, by="owner")
    except outside_services.OutsideError as exc:
        return {"ok": False, "message": exc.message}, 409
    return {"ok": True, "service": row}, 200


def apply_switch(sid: Any, device: str, *, reassemble: bool = True) -> tuple[dict, int]:
    sid = str(sid or "").strip().lower()
    if not _SID_RE.match(sid):
        return {"ok": False, "message": "There is no outside service by that name."}, 404
    if sid.startswith("seed-"):
        from puffpai.assemble import seed_switch
        got, status = seed_switch.apply(sid[len(seed_switch.PREFIX):], device,
                                        reassemble=reassemble)
        if got.get("ok"):
            _audit("chat_fix_switch_on", "on", sid)
        return got, status
    got, status = _switch_on(sid, device)
    if not got.get("ok"):
        return got, status
    name = _plain_name((got.get("service") or {}).get("name")) or "It"
    _audit("chat_fix_switch_on", "on", sid)
    return {"ok": True, "line": f"{name} is switched on. Asking again now.",
            "again": True}, 200


def apply_setting(data: dict, device: str) -> tuple[dict, int]:
    from puffpai import home_location
    if data.get("key") not in SETTINGS:
        return {"ok": False, "message": "That setting cannot be saved from here."}, 400
    town = " ".join(str(data.get("value") or data.get("town") or "").split())[:100]
    lat, lon = data.get("latitude"), data.get("longitude")
    try:
        if lat not in (None, "") and lon not in (None, ""):
            home = home_location.set_home_coords(lat, lon, label=town)
        else:
            if len(town) < 2:
                return {"ok": False, "message": "Please type a town name."}, 400
            allowed, _why = home_location.geocode_allowed()
            if not allowed:
                from puffpai.security import egress_doors
                if (egress_doors.run_posture().get("run_egress")
                        != egress_doors.OPEN_POSTURE):
                    return {"ok": False, "message": POSITION_NEEDED,
                            "position": True}, 409
                got, status = _switch_on(home_location.GEOCODE_DOOR, device)
                if not got.get("ok"):
                    return got, status
            home, _others = home_location.set_home_town(town)
    except home_location.HomeError as exc:
        return {"ok": False, "message": str(exc)}, 400
    _audit("chat_fix_setting", "saved", "home town")
    return {"ok": True, "line": f"Saved your town: {home.get('label') or town}. "
                                f"Asking again now.", "again": True}, 200


def apply(data: Any, device: str) -> tuple[dict, int]:
    """Carry out one fix card for *device*. ``(answer, status)``."""
    data = data if isinstance(data, dict) else {}
    if not device:
        return {"ok": False, "message": NOT_PAIRED}, 403
    kind = data.get("kind")
    if kind == "switch_on":
        return apply_switch(data.get("service"), device)
    if kind == "setting":
        return apply_setting(data, device)
    if kind == "key":
        return {"ok": False, "message": KEY_HERE}, 400
    if kind == "assemble_yes":
        from puffpai.assemble import api as assemble_api
        got, status = assemble_api.yes_and_start(str(data.get("key") or "")[:40], device)
        if got.get("ok"):
            _audit("chat_fix_assemble_yes", "yes", str(data.get("key") or "")[:40])
            got = {"ok": True, "line": got["line"], "again": False}
        return got, status
    return {"ok": False, "message": "That is not something this card can do."}, 400


@bp.route("/fix/apply", methods=["POST"])
def fix_apply():
    body = request.get_json(silent=True)
    got, status = apply(body, _paired())
    return jsonify(got), status


def card_for_app(data: Any, device: str) -> tuple[dict, int]:
    """The card an assembled app's page asked its dashboard page to show.

    *data* is ``{"app_id", "kind", "service"?, "name"?}``; the dashboard page
    sends the app id it shows, never the frame's. Only switches the app's own
    sources sit behind, and a key's name (never a link from the frame)."""
    data = data if isinstance(data, dict) else {}
    if not device:
        return {"ok": False, "message": NOT_PAIRED}, 403
    app_id, kind = str(data.get("app_id") or ""), data.get("kind")
    if not _APP_ID_RE.match(app_id):
        return {"ok": False, "message": "There is no such app."}, 404
    if kind == "switch_on":
        sid = str(data.get("service") or "").lower()
        if sid not in app_switch_rows(app_id):
            return {"ok": False, "message": "That is not a switch this app uses."}, 404
        card = clean({"kind": "switch_on", "service": sid})
    elif kind == "key":
        card = clean({"kind": "key", "name": data.get("name")})
    else:
        card = None
    if not card:
        return {"ok": False, "message": "There is nothing to switch on for that now."}, 404
    return {"ok": True, "fix": public(card)}, 200


@bp.route("/fix/card", methods=["POST"])
def fix_card():
    got, status = card_for_app(request.get_json(silent=True), _paired())
    return jsonify(got), status


__all__ = ["FIX_NOTE", "KINDS", "app_switch_rows", "apply", "bp", "card_for_app", "clean",
           "fixes_for", "note_for", "public", "row_id_for_switch", "town_card"]
