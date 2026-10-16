"""The one list of things the bot's AI may do inside integrated apps and tools.

Sources register here (``web_recipe`` from the app host, ``cli`` from the
locked box, ``data_query`` and ``palette`` from this package). An action is a
checked, generated description of one thing an integrated app can do, in one
shape for every kind:

``{"id": "<integration id>__<name>", "integration", "app_name", "says",
"effect": "reads|changes|removes", "kind", "args": <JSON schema>, "undoable",
"provenance": {"by": "compiler|reader|toolsmith_cli|buildbot", ...},
"checked": {"verdict": "allow", "effect_proven", "rows_sha", "at"}}``

Rules kept here, whoever calls:

* no action without provenance from one of the four makers; there is no way
  to register a hand-written one;
* an action's effect is the stricter of what it says and what its check
  proved; a non-read action with no required argument is a ``removes``
  (it could touch more than one record), except a ``web_recipe`` button
  whose check is one stored number or yes/no of its own
  (``checked.one_value``): the app host proves on every run that exactly
  that one value changed and puts back anything more;
* :func:`run` runs only for a paired device, the terminal, or (on the voice
  surface only) Home Assistant's voice key (:mod:`.voice_key`); validates
  every argument against the schema (unknown arguments refused); never runs
  an action of an integration that is not ready (held, refused, failed,
  removed, still being checked); and never runs a ``removes`` without
  redeeming an ``action_yes`` ticket bound to this device, this action and
  these exact arguments. Voice never removes and never makes a change that
  cannot be undone.
* an action's words (app name, ``says``, every text choice of an argument)
  are short plain labels: anything that reads like an order to a model
  (:func:`artifact_review.label_orders`) and the action is refused.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from typing import Any, Callable

from puffpai.integrate import optional, records, voice_key, wording
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

KINDS = frozenset({"web_recipe", "cli", "data_query", "palette"})
PROVENANCE_BY = frozenset({"compiler", "reader", "toolsmith_cli", "buildbot"})
EFFECTS = ("reads", "changes", "removes")        # in order of strictness
ACTION_FIELDS = frozenset({"id", "integration", "app_name", "says", "effect", "kind",
                           "args", "undoable", "provenance", "checked"})
ARG_TYPES = frozenset({"string", "number", "integer", "boolean"})
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}\Z")
_ARG_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")
MAX_STRING = 500
MAX_ARGS = 12
UNDO_SECONDS = 600
MAX_CHOICE = 80
#: Kinds whose actions always belong to an integration record this package
#: wrote: they run only while that record is ``ready``. Other kinds (the
#: locked box's command-line tools) run unless a record says not ready.
RECORDED_KINDS = frozenset({"web_recipe", "data_query", "palette"})

_lock = threading.Lock()
_SOURCES: dict[str, tuple[Callable[[], list], Callable[[dict, dict, dict], dict]]] = {}
_UNDO: dict[str, list[dict[str, Any]]] = {}

#: The change log: every change to her things that really
#: ran, in memory only, for "did you add ...?" and "did that work?". A model
#: never answers those: the bot answers them from here. Kept for
#: :data:`LOG_SECONDS`, at most :data:`LOG_MAX` lines, never written to disk,
#: never shown to a caller with no key.
LOG_SECONDS = 1800
LOG_MAX = 20
_LOG: list[dict[str, Any]] = []
_LOG_SINCE = time.monotonic()


class ActionError(ValueError):
    """An action or its arguments do not have the pinned shape."""


def _stricter(a: str, b: str) -> str:
    ia = EFFECTS.index(a) if a in EFFECTS else len(EFFECTS) - 1
    ib = EFFECTS.index(b) if b in EFFECTS else len(EFFECTS) - 1
    return EFFECTS[max(ia, ib)]


def _check_schema(args: Any) -> dict[str, Any]:
    if not isinstance(args, dict) or args.get("type") != "object" \
            or args.get("additionalProperties") is not False:
        raise ActionError("args must be a closed object schema")
    props = args.get("properties")
    required = args.get("required", [])
    if not isinstance(props, dict) or len(props) > MAX_ARGS:
        raise ActionError("args.properties must be a mapping")
    if not isinstance(required, list) or not all(r in props for r in required):
        raise ActionError("args.required must name properties")
    for name, spec in props.items():
        if not isinstance(name, str) or not _ARG_RE.match(name):
            raise ActionError("argument names are short lowercase words")
        if not isinstance(spec, dict) or spec.get("type") not in ARG_TYPES:
            raise ActionError("argument types are string, number, integer or boolean")
        enum = spec.get("enum")
        if enum is not None and (not isinstance(enum, list) or not enum or len(enum) > 50
                                 or not all(isinstance(e, (str, int, float)) for e in enum)):
            raise ActionError("an enum is a short list of plain values")
        for text in [spec.get("description"), spec.get("title"), *(enum or [])]:
            if isinstance(text, str) and not _plain_label(text, MAX_CHOICE):
                raise ActionError("an argument's words must be short plain labels")
    return args


def _plain_label(text: str, limit: int) -> bool:
    """A short label a model may read in a tool's description."""
    from puffpai import artifact_review, drawbridge
    return (len(text) <= limit and "\n" not in text and "\r" not in text
            and not drawbridge.instruction_shaped(text)
            and not artifact_review.label_orders(text))


def validate(action: Any, kind: str) -> dict[str, Any]:
    """A checked copy of *action* with its effective effect, or ActionError."""
    if not isinstance(action, dict):
        raise ActionError("an action is a mapping")
    prov = action.get("provenance")
    if not isinstance(prov, dict) or prov.get("by") not in PROVENANCE_BY:
        raise ActionError("an action needs provenance from the compiler, the reader, "
                          "the command-line maker or BuildBot")
    extra = set(action) - ACTION_FIELDS
    if extra:
        raise ActionError(f"fields not in the action shape: {sorted(extra)}")
    if action.get("kind") != kind:
        raise ActionError("an action's kind must be its source's kind")
    aid = action.get("id")
    integ = action.get("integration")
    if not (isinstance(aid, str) and isinstance(integ, str) and records.valid_id(integ)
            and aid.startswith(integ + "__") and _NAME_RE.match(aid[len(integ) + 2:])):
        raise ActionError("an action id is <integration id>__<name>")
    for key, limit in (("says", 120), ("app_name", 80)):
        text = action.get(key)
        if not isinstance(text, str) or not text.strip() or not _plain_label(text, limit):
            raise ActionError(f"{key} must be a short plain line")
    if action.get("effect") not in EFFECTS:
        raise ActionError("effect is reads, changes or removes")
    if not isinstance(action.get("undoable"), bool):
        raise ActionError("undoable is true or false")
    checked = action.get("checked")
    if not isinstance(checked, dict):
        raise ActionError("an action carries its check")
    args = _check_schema(action.get("args"))
    out = json.loads(json.dumps(action))
    effect = _stricter(action["effect"], str(checked.get("effect_proven") or "removes"))
    if effect != "reads" and not args.get("required") and not (
            kind == "web_recipe" and effect == "changes" and checked.get("one_value") is True):
        effect = "removes"  # could touch more than one record
    out["effect"] = effect
    return out


def allowed(action: dict) -> bool:
    return (action.get("checked") or {}).get("verdict") == "allow"


def caller_ok(device_id: Any, surface: str) -> bool:
    """May *device_id* use app actions on *surface*? A paired device or the
    terminal on any surface; Home Assistant's voice key only on ``voice``."""
    from puffpai.security import consent_tickets
    if consent_tickets.usable_device(device_id):
        return True
    return surface == "voice" and voice_key.is_identity(device_id)


def integration_state(integration_id: str) -> str:
    """``ready``, ``not_ready`` or ``unknown`` for the integration an action
    names (its record id, or the install id of the app it drives)."""
    found = None
    for rec in records.all():
        if not isinstance(rec, dict):
            continue
        if rec.get("id") == integration_id or (
                rec.get("app_id") and rec.get("app_id") == integration_id):
            found = rec
            if rec.get("status") == "ready":
                return "ready"
    return "unknown" if found is None else "not_ready"


def runnable(action: dict) -> bool:
    """False for an action whose integration is not ready (fail closed for
    the kinds this package records)."""
    state = integration_state(str(action.get("integration") or ""))
    if state == "ready":
        return True
    if state == "not_ready":
        return False
    return action.get("kind") not in RECORDED_KINDS


def register_source(kind: str, list_fn: Callable[[], list],
                    run_fn: Callable[[dict, dict, dict], dict]) -> None:
    """Add a source of actions. Refuses (ValueError) a bad kind, a second
    source for a kind, or any listed action without valid provenance."""
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not an action source kind")
    if not callable(list_fn) or not callable(run_fn):
        raise ValueError("an action source is two callables")
    listed = list_fn() or []
    for action in listed:
        validate(action, kind)  # raises ActionError (a ValueError)
    with _lock:
        if kind in _SOURCES:
            raise ValueError(f"an action source for {kind!r} is already registered")
        _SOURCES[kind] = (list_fn, run_fn)


def all_actions() -> list[dict]:
    """Every valid action of every source, with its effective effect."""
    with _lock:
        sources = list(_SOURCES.items())
    out: list[dict] = []
    seen: set[str] = set()
    for kind, (list_fn, _run) in sources:
        try:
            listed = list_fn() or []
        except Exception as exc:  # noqa: BLE001 - one source never sinks the list
            logger.warning("integrate: action source %s failed: %s", kind,
                           type(exc).__name__)
            continue
        for action in listed:
            try:
                clean = validate(action, kind)
            except ActionError as exc:
                logger.warning("integrate: an action from %s was left out: %s", kind, exc)
                continue
            if clean["id"] in seen:
                continue
            seen.add(clean["id"])
            out.append(clean)
    return out


def find(action_id: str) -> dict | None:
    return next((a for a in all_actions() if a["id"] == action_id), None)


def check_args(action: dict, args: Any) -> dict[str, Any]:
    """*args* checked against the action's schema; unknown ones refused."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ActionError("arguments are a mapping")
    schema = action["args"]
    props = schema.get("properties") or {}
    unknown = [k for k in args if k not in props]
    if unknown:
        raise ActionError("unknown argument")
    out: dict[str, Any] = {}
    for name, value in args.items():
        spec = props[name]
        kind = spec.get("type")
        if kind == "string":
            if not isinstance(value, str):
                raise ActionError(f"{name} must be words")
            limit = min(int(spec.get("maxLength") or MAX_STRING), MAX_STRING)
            if len(value) > limit or any(ord(c) < 32 for c in value):
                raise ActionError(f"{name} is too long or has control characters")
            if action.get("kind") == "cli" and value.startswith("-"):
                raise ActionError(f"{name} may not start with '-'")
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise ActionError(f"{name} must be yes or no")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(float(value)):
                raise ActionError(f"{name} must be a number")
            if kind == "integer" and float(value) != int(value):
                raise ActionError(f"{name} must be a whole number")
        enum = spec.get("enum")
        if enum is not None and value not in enum:
            raise ActionError(f"{name} must be one of the app's own choices")
        out[name] = value
    missing = [r for r in schema.get("required") or [] if r not in out]
    if missing:
        raise ActionError("missing argument")
    return out


def _source_run(kind: str) -> Callable[[dict, dict, dict], dict] | None:
    with _lock:
        pair = _SOURCES.get(kind)
    return pair[1] if pair else None


def _refused(action: dict | None, line: str) -> dict[str, Any]:
    return {"ok": False, "effect": (action or {}).get("effect", "reads"),
            "evidence": None, "line": line, "undo_token": None}


def run(action_id: str, args: dict, ctx: dict) -> dict:
    """Run one action. ``ctx``: ``device_id``, ``surface``, and ``ticket``
    for a removes. Returns ``{"ok", "effect", "data"|"evidence", "line",
    "undo_token"}``."""
    from puffpai.security import consent_tickets
    ctx = ctx if isinstance(ctx, dict) else {}
    device = ctx.get("device_id")
    surface = str(ctx.get("surface") or "")
    if not caller_ok(device, surface):
        return _refused(None, wording.DECIDE_NOT_PAIRED)
    voice = not consent_tickets.usable_device(device)   # the voice key
    action = find(str(action_id or ""))
    if action is None or not allowed(action):
        return _refused(action, wording.UNKNOWN_ITEM)
    if not runnable(action):
        return _refused(action, wording.NOT_READY.format(app=action["app_name"]))
    try:
        clean = check_args(action, args)
    except ActionError:
        return _refused(action, wording.ACTION_FAILED.format(app=action["app_name"]))
    effect = action["effect"]
    owner_yes = False
    if (surface == "voice" or voice) and effect == "removes":
        return _refused(action, wording.VOICE_REMOVE)
    if (surface == "voice" or voice) and effect == "changes" and not action.get("undoable"):
        return _refused(action, wording.VOICE_CHANGE)
    if effect == "removes":
        target_fn = getattr(consent_tickets, "target_action_yes", None)
        if target_fn is None or "action_yes" not in getattr(consent_tickets, "ACTIONS", ()):
            return _refused(action, wording.NOT_IN_BUILD)
        ok, verdict = consent_tickets.redeem(ctx.get("ticket"), device, "action_yes",
                                             target_fn(action["id"], clean))
        if not ok:
            return _refused(action, consent_tickets.refusal_words(verdict).replace(
                "Nothing was switched on", "Nothing was removed"))
        # The owner's yes, redeemed just now for this device, this action and
        # these exact arguments (never on voice: refused above). The app
        # runner needs it to run a removes (without it, "yes, remove" never
        # removes anything).
        owner_yes = True
    run_fn = _source_run(action["kind"])
    if run_fn is None:
        return _refused(action, wording.NOT_IN_BUILD)
    safe_ctx = {"device_id": device, "surface": surface}
    if owner_yes:
        safe_ctx["yes"] = True
    before = _app_copy(action) if effect != "reads" else None
    try:
        got = run_fn(action, clean, safe_ctx)
    except Exception as exc:  # noqa: BLE001 - an app that failed changed nothing we claim
        logger.warning("integrate: action %s failed: %s", action["kind"], type(exc).__name__)
        return _refused(action, wording.ACTION_FAILED.format(app=action["app_name"]))
    got = got if isinstance(got, dict) else {}
    out = {"ok": bool(got.get("ok")), "effect": effect, "line": str(got.get("line") or ""),
           "undo_token": got.get("undo_token") if effect in ("changes", "removes") else None}
    if out["ok"] and before is not None and _app_copy(action) == before:
        # The effect proof: the app said it was done, but the bot's copy of
        # its data is exactly what it was before, so nothing changed. Never
        # "Done" for that.
        return {"ok": False, "effect": effect, "evidence": got.get("evidence"),
                "line": wording.NOTHING_CHANGED.format(app=action["app_name"]),
                "undo_token": None, "unchanged": True}
    if effect == "reads":
        out["data"] = got.get("data")
        # how many rows came with the app as its own examples
        n = got.get("examples")
        out["examples"] = n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0
    else:
        out["evidence"] = got.get("evidence")
        if out["ok"]:
            note_change(_log_line(action, clean), app_name=action["app_name"])
    # a remove she said yes to is undone from the app's own copy
    undo_ok = action.get("undoable") or (effect == "removes" and owner_yes)
    if out["ok"] and out["undo_token"] is not None and undo_ok:
        with _lock:
            rows = [r for r in _UNDO.get(device, [])
                    if time.monotonic() - r["at"] < UNDO_SECONDS][-9:]
            rows.append({"at": time.monotonic(), "action": action["id"],
                         "kind": action["kind"], "integration": action["integration"],
                         "app_name": action["app_name"], "token": out["undo_token"]})
            _UNDO[device] = rows
            if len(_UNDO) > 64:
                for key in list(_UNDO)[:16]:
                    _UNDO.pop(key, None)
    elif out["undo_token"] is not None and not undo_ok:
        out["undo_token"] = None
    return out


def _log_line(action: dict, args: dict) -> str:
    """The change log's line for one action that ran: the app, what the
    action does, and her words that went in (never a value of the app's)."""
    pairs = []
    for key, value in (args or {}).items():
        if isinstance(value, bool):
            value = "yes" if value else "no"
        if isinstance(value, (str, int, float)) and str(value).strip():
            pairs.append(f"{key.replace('_', ' ')} {' '.join(str(value).split())[:60]}")
    says = str(action.get("says") or "").rstrip(".")
    line = f"In {action.get('app_name')}: {says}"
    return (line + f" ({', '.join(pairs[:4])})." if pairs else line + ".")


def _app_copy(action: dict) -> Any:
    """The bot's copy of the data of the web app *action* drives (a deep
    copy), or None when there is none to compare (other kinds, an app that
    is not installed on this computer)."""
    if action.get("kind") != "web_recipe":
        return None
    try:
        from puffpai.webapps import host, mirror
        app_id = str(action.get("integration") or "")
        if app_id not in host.installed_ids():
            return None
        got = mirror.get(app_id)
        return json.loads(json.dumps((got or {}).get("data")))
    except Exception:  # noqa: BLE001 - no copy: no claim can rest on it
        return None


def note_change(line: Any, *, app_name: Any = "") -> None:
    """Add one change that really ran to the change log (see :data:`_LOG`).
    *line* is what the bot told her when it ran ("Done in Big Day: Add a
    guest. What I put in: guest name Leo Park.")."""
    text = " ".join(str(line or "").split())[:300]
    if not text:
        return
    with _lock:
        now = time.monotonic()
        keep = [r for r in _LOG if now - r["at"] < LOG_SECONDS][-(LOG_MAX - 1):]
        keep.append({"at": now, "line": text, "app_name": str(app_name or "")[:80]})
        _LOG[:] = keep


def recent_changes() -> tuple[list[str], int]:
    """``(lines, minutes)``: the changes that ran in the last *minutes*
    minutes, oldest first. *minutes* is never more than the time this
    process has been keeping the log, so "nothing in the last 30 minutes" is
    never said over a restart that forgot an earlier change."""
    with _lock:
        now = time.monotonic()
        rows = [r for r in _LOG if now - r["at"] < LOG_SECONDS]
        since = now - _LOG_SINCE
    minutes = max(1, int(min(LOG_SECONDS, since) // 60))
    return [r["line"] for r in rows], minutes


def forget_changes() -> None:
    """Empty the change log (tests; a restart does it too)."""
    global _LOG_SINCE
    with _lock:
        _LOG.clear()
        _LOG_SINCE = time.monotonic()


def last_change_app(device_id: str) -> str:
    """The app name of this device's last undoable change (10 minutes), or ""."""
    with _lock:
        rows = [r for r in _UNDO.get(device_id or "", [])
                if time.monotonic() - r["at"] < UNDO_SECONDS]
    return str(rows[-1].get("app_name") or "") if rows else ""


def _remember(device: str, row: dict) -> None:
    with _lock:
        rows = [r for r in _UNDO.get(device, [])
                if time.monotonic() - r["at"] < UNDO_SECONDS][-9:]
        rows.append(row)
        _UNDO[device] = rows
        if len(_UNDO) > 64:
            for key in list(_UNDO)[:16]:
                _UNDO.pop(key, None)


def remember_list_change(device_id: str, app_id: str, before: Any, after: Any, *,
                         store: Any) -> None:
    """A declarative list change made for *device_id* (the voice key's adds),
    so "undo" can put *before* back while the list still holds *after*.
    *store* is the backend's list store (``get_data``/``set_data``)."""
    if not isinstance(device_id, str) or not device_id:
        return
    _remember(device_id, {"at": time.monotonic(), "action": f"list:{app_id}",
                          "kind": "list", "integration": "", "app_name": "",
                          "token": {"app_id": app_id, "before": json.loads(json.dumps(before)),
                                    "after": json.loads(json.dumps(after)), "store": store}})


def _undo_list(token: Any) -> dict:
    store = token.get("store") if isinstance(token, dict) else None
    if store is None:
        return {"ok": False, "line": wording.NO_UNDO}
    try:
        if store.get_data(token["app_id"]) != token["after"]:
            return {"ok": False, "line": wording.UNDO_CHANGED}
        if not store.set_data(token["app_id"], token["before"]) \
                or store.get_data(token["app_id"]) != token["before"]:
            return {"ok": False, "line": wording.LIST_NOT_SAVED}
    except Exception as exc:  # noqa: BLE001
        logger.warning("integrate: list undo failed: %s", type(exc).__name__)
        return {"ok": False, "line": wording.LIST_NOT_SAVED}
    return {"ok": True, "line": wording.LIST_UNDONE}


def has_undo(device_id: str) -> bool:
    with _lock:
        return any(time.monotonic() - r["at"] < UNDO_SECONDS
                   for r in _UNDO.get(device_id or "", []))


def undo_last(device_id: str) -> dict:
    """Revert this device's last change within 10 minutes: ``{"ok", "line"}``."""
    from puffpai.security import consent_tickets
    if not (consent_tickets.usable_device(device_id) or voice_key.is_identity(device_id)):
        return {"ok": False, "line": wording.DECIDE_NOT_PAIRED}
    with _lock:
        rows = [r for r in _UNDO.get(device_id, [])
                if time.monotonic() - r["at"] < UNDO_SECONDS]
        last = rows.pop() if rows else None
        _UNDO[device_id] = rows
    if last is None:
        return {"ok": False, "line": wording.NO_UNDO}
    if last["kind"] == "list":
        got = _undo_list(last["token"])
        if got.get("ok"):
            note_change(got.get("line"))
        return got
    if last["kind"] != "web_recipe":
        return {"ok": False, "line": wording.NO_UNDO}
    mirror = optional("puffpai.webapps.mirror")
    restore = getattr(mirror, "restore", None) if mirror else None
    token = last["token"]
    app_id = token.get("app_id") if isinstance(token, dict) else None
    version = token.get("version") if isinstance(token, dict) else None
    if restore is None:
        return {"ok": False, "line": wording.NOT_IN_BUILD}
    if not (records.valid_id(app_id) and isinstance(version, int)):
        return {"ok": False, "line": wording.NO_UNDO}
    try:
        got = restore(app_id, version)
    except Exception as exc:  # noqa: BLE001
        logger.warning("integrate: undo failed: %s", type(exc).__name__)
        got = {}
    if isinstance(got, dict) and got.get("ok"):
        line = wording.UNDONE.format(app=last["app_name"])
        note_change(line, app_name=last["app_name"])
        return {"ok": True, "line": line}
    return {"ok": False, "line": wording.UNDO_CHANGED}


__all__ = ["ActionError", "EFFECTS", "KINDS", "PROVENANCE_BY", "RECORDED_KINDS",
           "all_actions", "allowed", "caller_ok", "check_args", "find", "forget_changes",
           "has_undo", "integration_state", "last_change_app", "note_change",
           "recent_changes", "register_source", "run", "runnable", "undo_last",
           "validate"]
