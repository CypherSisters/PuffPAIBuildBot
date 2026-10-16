"""Assemble: the run, the routes, the brain's tools, and the seams for the
other parts of the step.

**Seams** (filled by other parts of the build; each is imported only when
called, and a part that is not in this build yet gives the plain answer
"not yet: still being built" instead of failing):

* ``puffpai.assemble.toolsmith_loop.run_toolsmith(app_id, capability, parts,
  budget) -> {"ok", "glue": [...], "reason"}`` -- connecting code where the
  declared parts cannot do the job. Each glue entry is ``{"id", "module":
  "glue/<file>.py", "fn", "fields": [field names its rows carry], "args"?}``.
* ``puffpai.assemble.server.start_assembled(app_id) -> {"ok", "port",
  "reason"?}`` -- start the app's own small server on 127.0.0.1.
* ``puffpai.assemble.server.call_action(app_id, action, input) -> {"ok",
  "rows": [...], "reason"?, "fix"?}`` -- run one of the spec's actions.

:func:`fetch_source` is the backend half the app server calls back for a
declared outside source: the spec's address, filled from the input and the
settings, fetched through the Bridge under the source's own switch, read into
rows. The server itself makes no outside calls.

**Routes** (a blueprint behind the same key check as everything else, and a
paired device for each)::

    GET  /assemble/status   {"running", "phase", "lines": [{"agent", "text"}],
                            "plan": [{"cap", "name", "how", "finds"}],
                            "waiting_yes": [card], ...}  (never starts anything)
    POST /assemble/run      {"cap"?} put it together again ("Put it together")
    GET  /assemble/yes      {"cards": [card]} the apps waiting for her yes
    POST /assemble/yes      {"app_id", "sha"?} her Start on one app's card
    GET  /assemble/report   {"apps": [{"id", "title", "thing", "caps": [{"cap",
                            "name", "state", "reason"}]}], "capabilities": [...]}

**The one yes** (:mod:`puffpai.assemble.consent`): an assembled app is not
put together until the owner taps Start on its card. Before that the run only
plans it (nothing fetched, no connecting code written or run, no server
started) and its row says "Waiting for your yes".

**The brain's tools**: one tool per action of each assembled app
(``<app id>__<action>``), offered in chat once the app's capability is proven,
and during the proof only for the app being proven.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import importlib
import os
import re
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import quote

from flask import Blueprint, jsonify, request

from puffpai import fsutil
from puffpai.assemble import APP_ID_RE, app_dir, consent, plan as plan_mod, report, shape, spec as spec_mod
from puffpai.assemble import state_dir
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

AGENT = "majordomo"
PROVIDER = "assembled"
STILL_BUILDING = report.STILL_BUILDING
#: What one capability may spend on connecting code (the Toolsmith's budget).
BUDGET = {"steps_per_try": 8, "tries": 3, "seconds": 20 * 60}
ROWS_MAX = 200

TOOLSMITH_MODULE = "puffpai.assemble.toolsmith_loop"
SERVER_MODULE = "puffpai.assemble.server"

_RUN_LOCK = threading.Lock()
_running = threading.Event()
#: Capabilities she said yes to while a run was going: run once it ends.
_QUEUED: list[str] = []


# ── the seams ───────────────────────────────────────────────────────────────

def _optional(modname: str) -> Any:
    """The module, or None when it is not in this build yet."""
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as exc:
        if exc.name is not None and (exc.name == modname or modname.startswith(exc.name + ".")):
            return None
        raise


def _seam(modname: str, fn: str) -> Optional[Callable]:
    try:
        got = getattr(_optional(modname), fn, None)
    except Exception as exc:  # noqa: BLE001 - a broken part is still not here
        logger.warning("assemble: %s did not load (%s)", modname, type(exc).__name__)
        return None
    return got if callable(got) else None


def run_toolsmith(app_id: str, capability: dict, parts: list[dict],
                  budget: Optional[dict] = None) -> dict:
    fn = _seam(TOOLSMITH_MODULE, "run_toolsmith")
    if fn is None:
        return {"ok": False, "glue": [], "reason": STILL_BUILDING}
    try:
        got = fn(app_id, capability, parts, dict(budget or BUDGET))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "glue": [],
                "reason": f"writing connecting code stopped ({type(exc).__name__})"}
    got = got if isinstance(got, dict) else {}
    glue = [g for g in got.get("glue") or [] if isinstance(g, dict)]
    return {"ok": bool(got.get("ok")) and bool(glue), "glue": glue,
            "reason": str(got.get("reason") or ("" if glue else "no connecting code worked"))}


def start_assembled(app_id: str) -> dict:
    fn = _seam(SERVER_MODULE, "start_assembled")
    if fn is None:
        return {"ok": False, "port": None, "reason": STILL_BUILDING}
    try:
        got = fn(app_id)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "port": None, "reason": f"it did not start ({type(exc).__name__})"}
    got = got if isinstance(got, dict) else {}
    return {"ok": bool(got.get("port")) and got.get("ok", True) is not False,
            "port": got.get("port"), "reason": str(got.get("reason") or "")}


def call_action(app_id: str, action: str, input: Optional[dict] = None) -> dict:  # noqa: A002
    fn = _seam(SERVER_MODULE, "call_action")
    if fn is None:
        return {"ok": False, "rows": [], "reason": STILL_BUILDING}
    try:
        got = fn(app_id, action, dict(input or {}))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "rows": [], "reason": f"it did not answer ({type(exc).__name__})"}
    got = got if isinstance(got, dict) else {}
    rows = got.get("rows") if isinstance(got.get("rows"), list) else []
    out = {"ok": bool(got.get("ok")), "rows": rows[:ROWS_MAX],
           "reason": str(got.get("reason") or "")}
    if isinstance(got.get("fix"), dict):
        out["fix"] = got["fix"]
    return out


# ── specs on disk ───────────────────────────────────────────────────────────

def spec_path(app_id: str) -> str:
    return os.path.join(app_dir(app_id), "spec.json")


def save_spec(spec: dict) -> None:
    fsutil.atomic_write_json(spec_path(spec["id"]), spec)


def load_spec(app_id: str) -> Optional[dict]:
    if not APP_ID_RE.match(str(app_id or "")):
        return None
    got = fsutil.read_json(spec_path(app_id), default=None)
    return got if isinstance(got, dict) and not spec_mod.validate(got) else None


def all_specs() -> list[dict]:
    root = state_dir()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [s for s in (load_spec(n) for n in names if APP_ID_RE.match(n)) if s]


# ── the backend half of a declared source ───────────────────────────────────

_HOLE_RE = re.compile(r"\{([a-z_]+)\}")


def fill_url(url: str, values: dict) -> tuple[str, list[str]]:
    """``{name}`` holes filled (url-quoted); returns ``(url, names left empty)``."""
    missing: list[str] = []

    def one(m: re.Match) -> str:
        name = m.group(1)
        if name == "date" and not values.get("date"):
            return time.strftime("%Y-%m-%d")
        value = values.get(name)
        if name in ("lat_round", "lon_round") and value is None:
            value = values.get(name.split("_")[0])
        if value is None or value == "":
            missing.append(name)
            return ""
        if name in ("lat_round", "lon_round", "lat", "lon"):
            try:
                value = round(float(value), 1)
            except (TypeError, ValueError):
                missing.append(name)
                return ""
        return quote(str(value), safe="")
    return _HOLE_RE.sub(one, str(url or "")), missing


#: Words a question carries that say nothing about what to look for.
_QUIET_WORDS = frozenset(
    "a an the of in on at to for from about with and or me my our your show give find get "
    "tell list see what which is are was any some all please latest newest recent new today "
    "now current last photo photos picture pictures image images item items news story "
    "stories update updates".split())


def search_words(q: str) -> list[str]:
    """The words of a search that say what to look for."""
    return [w for w in re.findall(r"[a-z0-9]+", str(q or "").lower())
            if w not in _QUIET_WORDS and len(w) > 1]


def _matches(row: dict, q: str) -> bool:
    """Any of the search's own words, anywhere in the row's text, any case."""
    words = search_words(q)
    if not words:
        return True
    text = " ".join(str(v) for v in row.values() if isinstance(v, (str, int, float))).lower()
    return any(w in text for w in words)


def fetch_source(app_id: str, source_id: str, input: Optional[dict] = None, *,  # noqa: A002
                 getter: Optional[Callable] = None) -> dict:
    """``{"ok", "rows", "reason"?, "fix"?}`` for one declared outside source
    (``http_json``, ``rss``, ``m3u``). Only the spec's own address is ever
    fetched, under the spec's own switch."""
    spec = load_spec(app_id)
    if spec is None:
        return {"ok": False, "rows": [], "reason": "there is no such app"}
    src = next((s for s in spec["sources"] if s.get("id") == source_id), None)
    if src is None or src.get("kind") not in ("http_json", "rss", "m3u"):
        return {"ok": False, "rows": [], "reason": "that is not an outside source"}
    args, missing = spec_mod.resolve_args(app_id, spec, src.get("args"))
    if missing:
        return {"ok": False, "rows": [], "reason": "your town is not set yet",
                "fix": {"kind": "setting", "app_id": app_id, "key": missing[0]}}
    q = " ".join(str((input or {}).get("q") or "").split())[:100]
    values = {**args, "q": q, "topic": q or src.get("topic") or ""}
    url, holes = fill_url(src["url"], values)
    if holes:
        return {"ok": False, "rows": [], "reason": f"it needs {holes[0]} to look anything up"}
    got = shape.fetch_sample(src.get("door") or None, url, getter=getter)
    if not got.get("ok"):
        out = {"ok": False, "rows": [], "reason": _plain_fetch_reason(src, got)}
        if got.get("switch_off"):
            out["fix"] = {"kind": "switch_on", "door": src.get("door") or "",
                          "service": src.get("service") or ""}
        return out
    _kind, data = shape.parse(got["text"])
    rows = shape.rows_from(data, src["items"], src.get("fields") or {}, limit=ROWS_MAX)
    rows = timed_rows(rows, data, src.get("time"))
    if q and "{q}" not in src["url"] and "{topic}" not in src["url"]:
        rows = [r for r in rows if _matches(r, q)] or rows   # nothing matched: the newest
    return {"ok": True, "rows": rows}


_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]+)?)?(Z|[+-][0-9]{2}:?[0-9]{2})$")


def local_times(rows: list) -> tuple[list, str]:
    """Rows with every moment (an ISO time with its zone) in this computer's
    own time, written plainly ("Sat Oct 10, 9:00 PM"), and that zone's name,
    so chat says the same times the page shows. ``(rows, zone)``."""
    import datetime as dt
    zone = ""
    out = []
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)
            continue
        r = dict(r)
        for k, v in r.items():
            if isinstance(v, str) and _ISO_RE.match(v.strip()):
                try:
                    at = dt.datetime.fromisoformat(v.strip().replace("Z", "+00:00")).astimezone()
                except ValueError:
                    continue
                hour = at.strftime("%I:%M %p").lstrip("0")
                r[k] = f"{at.strftime('%a %b')} {at.day}, {hour}"
                zone = zone or (at.tzname() or "")
        out.append(r)
    return out, zone


def timed_rows(rows: list, data: Any, time: Any) -> list:
    """Rows whose time is a start time in the answer plus an offset (a
    forecast's "init" and "timepoint" hours) get the real moment instead,
    as an ISO time in UTC; the page shows it in her own time."""
    import datetime as dt
    if not isinstance(time, dict) or not time.get("field") or not time.get("start"):
        return rows
    try:
        start = dt.datetime.strptime(str(shape.value_at(data, time["start"])),
                                     str(time.get("format") or "%Y%m%d%H"))
    except (TypeError, ValueError):
        return rows
    step = {"hours": 3600, "minutes": 60, "days": 86400}.get(str(time.get("unit")), 3600)
    out = []
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get(time["field"]), (int, float)):
            at = start + dt.timedelta(seconds=float(r[time["field"]]) * step)
            r = {**r, time["field"]: at.strftime("%Y-%m-%dT%H:%M:00Z")}
        out.append(r)
    return out


def _plain_fetch_reason(src: dict, got: dict) -> str:
    name = src.get("name") or src.get("service") or "the outside service"
    if got.get("reason") in (shape.SWITCH_OFF, shape.NOT_ADDED):
        return shape.refusal_reason(got, name)
    return f"{name}: {got.get('reason') or 'no answer'}"


# ── the brain's tools ───────────────────────────────────────────────────────

def tool_name(app_id: str, action: Any) -> str:
    return f"{app_id}__{action}"[:128]


def app_tools(app_id: str, spec: dict) -> list:
    """One brain tool per action of this app."""
    from puffpai import brain_tools
    from puffpai.assemble import apps_note
    tools = []
    for action in spec.get("actions") or []:
        aid = str(action.get("id") or "")
        name = tool_name(app_id, aid)
        props = {k: {"type": t} for k, t in (action.get("input") or {}).items()}
        if "q" in props:
            props["q"]["description"] = "One or two words to look for."
        schema = {"type": "object", "properties": props, "required": [],
                  "additionalProperties": False}

        def run(args: dict, ctx: Any = None, _aid: str = aid, _name: str = name,
                _src: str = str(action.get("source") or "")) -> dict:
            got = call_action(app_id, _aid, args)
            if isinstance(ctx, dict) and isinstance(ctx.get("assemble_seen"), list):
                ctx["assemble_seen"].append((_name, len(got.get("rows") or [])))
            if not got["ok"]:
                out = {"error": got.get("reason") or "it did not answer"}
                # A fix (a switch to turn on, a setting to save) becomes a
                # one-tap card in her chat (puffpai.fix_api).
                if isinstance(got.get("fix"), dict):
                    out["fix"] = {**got["fix"], "app_id": app_id}
                return out
            items, zone = local_times(got["rows"][:20])
            out = {"items": items, "count": len(got["rows"]),
                   "fields": apps_note.field_words(spec, _src)}
            if zone:
                out["times_are_in"] = zone    # the same local time the page shows
            q = " ".join(str((args or {}).get("q") or "").split())[:100]
            if q and got["rows"] and not any(_matches(r, q) for r in got["rows"]
                                             if isinstance(r, dict)):
                out["note"] = f"nothing matched '{q}', here are the newest"
            return out
        try:
            tools.append(brain_tools.make(
                name, apps_note.tool_description(spec, action), schema, run,
                progress=f"Using {spec.get('title') or app_id} ...", source=PROVIDER))
        except ValueError as exc:
            logger.info("assemble: no tool for %s (%s)", aid, exc)
    return tools


def provide(ctx: Any = None) -> list:
    """The tools of proven apps; while one app is being proven, only its."""
    ctx = ctx if isinstance(ctx, dict) else {}
    only = str(ctx.get("assemble_app") or "")
    proven = report.by_cap()
    out = []
    for spec in all_specs():
        if only and spec["id"] != only:
            continue
        if not only and not any((proven.get(c) or {}).get("state") == report.WORKING
                                for c in spec.get("caps") or []):
            continue
        out.extend(app_tools(spec["id"], spec))
    if not only:
        out.extend(waiting_tool())
    return out


def waiting_tool() -> list:
    """While apps wait for her yes: one tool whose answer is their Start cards
    (the chat shows each as a card with one button; ``fix_api``)."""
    try:
        cards = consent.waiting()
    except Exception:  # noqa: BLE001 - no cards, no tool
        return []
    if not cards:
        return []
    from puffpai import brain_tools
    titles = ", ".join(c["title"] for c in cards[:6])

    def run(args: dict, ctx: Any = None) -> dict:
        now = consent.waiting()
        return {"error": f"{consent.WAITING}: " + ", ".join(c["title"] for c in now[:6])
                + ". Nothing of these apps runs until the owner taps Start on its card.",
                "fix": [consent.fix_for(c["app_id"]) for c in now[:3]]}
    try:
        return [brain_tools.make(
            "assembled_apps_waiting",
            f"Apps BuildBot put together that wait for the owner's yes before they start: "
            f"{titles}. Use it when she asks for something one of them would do.",
            {"type": "object", "properties": {}, "required": [],
             "additionalProperties": False}, run,
            progress="Looking at what waits for your yes ...", source=PROVIDER)]
    except ValueError:
        return []


def register_tools() -> None:
    from puffpai import brain_tools
    brain_tools.register_provider(PROVIDER, provide)


# ── the run ─────────────────────────────────────────────────────────────────

def status_path() -> str:
    return state_dir("status.json")


def read_status() -> dict:
    got = fsutil.read_json(status_path(), default=None)
    st = got if isinstance(got, dict) else {"state": "idle", "done": 0, "total": 0, "line": ""}
    if st.get("state") == "running" and not _running.is_set():
        st["state"] = "stopped"     # this server is not running it (a restart)
    return st


def _write_status(**changes: Any) -> dict:
    st = fsutil.read_json(status_path(), default=None)
    st = st if isinstance(st, dict) else {}
    st.update(changes)
    try:
        fsutil.atomic_write_json(status_path(), st)
    except OSError:
        pass
    return st


def _say(line: str, *, status: str = "completed", reason: str = "") -> None:
    from puffpai import agent_activity
    agent_activity.record(AGENT, line, status=status, reason=reason)


def mark_waiting() -> None:
    """The wizard's step: the plan is made; the putting together runs once the
    bot is started (it needs the bot's own server)."""
    _write_status(state="waiting", done=0, total=0,
                  line="Waiting to put it together once your bot starts.")


def _sample_url(part: dict) -> str:
    """The address one sample is taken from. A website row's own sample
    address carries nothing about the owner; a live card's address is used
    only when it has no holes left to fill."""
    url = str(part.get("sample_url") or part.get("url") or "")
    return "" if "{" in url else url


def _source_kind(kind: str) -> str:
    return {"json": "http_json", "rss": "rss", "m3u": "m3u"}.get(kind, "")


def assemble_one(row: dict, *, ask: Any = None, backend: Any = None, getter: Any = None,
                 reach: Any = None) -> dict:
    """Put one capability's app together and prove it; its report row."""
    from puffpai.assemble import accept
    cap = {"id": row["cap"], "name": row["name"], "does": row.get("does", "")}
    app_id = row.get("app_id") or plan_mod.app_id_for(row["cap"])
    if not consent.has_yes(app_id):
        # Nothing of it is fetched, written, run or started before her yes.
        return report.entry(row, report.NOT_YET, consent.WAITING, app_id=app_id,
                            decision=row["decision"], fix=consent.fix_for(app_id))
    shaped, others, blocked = [], [], []
    for n, part in enumerate(row.get("parts") or [], 1):
        url = _sample_url(part) if part.get("kind") in ("feed", "seed") else ""
        if not url:
            others.append(part)
            continue
        got = shape.fetch_sample(part.get("door") or None, url, getter=getter)
        if not got.get("ok"):
            if got.get("reason") in (shape.SWITCH_OFF, shape.NOT_ADDED):
                blocked.append((part, got))
            else:
                others.append({**part, "sample_problem": got.get("reason")})
            continue
        found = shape.shape(got["text"])
        kind = _source_kind(found["kind"])
        if not kind or not found["arrays"]:
            others.append({**part, "sample_problem": "no list of items in its answer"})
            continue
        shaped.append({"source_id": f"s{n}", "kind": kind, "url": str(part.get("url") or url),
                       "door": part.get("door") or "", "service": part.get("service") or "",
                       "found": {"arrays": found["arrays"], "fields": found["fields"]},
                       "image_template": part.get("image_template") or "",
                       "name": part.get("name") or "", "about": part.get("about") or "",
                       "meanings": part.get("meanings") or {},
                       "time_base": part.get("time_base") or {},
                       "value_words": part.get("value_words") or {},
                       "topic": part.get("topic") or ""})
    glue: list[dict] = []
    if not shaped and others:
        ts = run_toolsmith(app_id, cap, others, BUDGET)
        glue = ts["glue"]
        if not glue:
            return report.entry(row, report.NOT_YET, ts["reason"], app_id=app_id,
                                decision=row["decision"])
    if not shaped and not glue:
        part, got = blocked[0] if blocked else ({}, {})
        name = part.get("name") or "the outside service"
        reason = shape.refusal_reason(got, name) if part else "no part answered"
        fix = {"kind": "switch_on", "door": part.get("door") or "",
               "service": ("seed-" if part.get("kind") == "seed" and not part.get("door")
                           else "") + (part.get("service") or "")} \
            if part and got.get("code") != "bot_offline" else None
        return report.entry(row, report.NOT_YET, reason, app_id=app_id,
                            decision=row["decision"], fix=fix)
    if shaped:
        made = spec_mod.build(app_id, [cap], shaped, title=row["name"], ask=ask)
        spec = made["spec"]
    else:
        spec = _glue_spec(app_id, cap, glue)
    spec = named_tabs(drawable_views(with_view(spec, str(row.get("view") or ""))))
    problems = spec_mod.validate(spec)
    if problems and shaped:
        handed = _toolsmith_instead(row, app_id, cap, f"its plan did not fit ({problems[0]})",
                                    backend=backend, reach=reach)
        if handed is not None:
            return handed
    if problems:
        return report.entry(row, report.NOT_YET, f"its plan did not fit ({problems[0]})",
                            app_id=app_id, decision=row["decision"])
    save_spec(spec)
    started = start_assembled(app_id)
    if not started["ok"]:
        return report.entry(row, report.NOT_YET, started["reason"] or STILL_BUILDING,
                            app_id=app_id, decision=row["decision"])
    proof = accept.prove(app_id, spec, backend=backend, reach=reach).get(cap["id"]) or {}
    trimmed = without_failed_extras(spec, proof)
    if trimmed is not None:
        # The job's own source works; an extra one that shows nothing useful
        # leaves the page (it stays for chat) instead of holding the job back.
        spec = trimmed
        save_spec(spec)
        proof = accept.prove(app_id, spec, backend=backend, reach=reach).get(cap["id"]) or {}
    if shaped and not proof.get("ok") and _toolsmith_can_help(proof):
        # Its sources answer but not the way the page needs (a parameter, more
        # pages, two sources together): Toolsmith writes connecting code.
        handed = _toolsmith_instead(row, app_id, cap, str(proof.get("reason") or ""),
                                    backend=backend, reach=reach)
        if handed is not None and handed.get("state") == report.WORKING:
            return handed
    state = report.WORKING if proof.get("ok") else report.NOT_YET
    fix = next((c["fix"] for c in proof.get("checks") or [] if c.get("fix")), None)
    return report.entry(row, state, proof.get("reason") or ("" if proof else "it has no check"),
                        app_id=app_id, decision=row["decision"],
                        checks=proof.get("checks"), fix=fix)


_NOT_FOR_TOOLSMITH = re.compile(r"did not answer [(]|answered (429|5[0-9][0-9])|switch|"
                                r"set to stay off|not switched on|your town|key", re.I)


def _toolsmith_can_help(proof: dict) -> bool:
    """A proof that failed on what the answer holds (not on a slow service,
    a switch, a town or a key: those are said as they are)."""
    return bool(proof) and not proof.get("fix") \
        and not _NOT_FOR_TOOLSMITH.search(str(proof.get("reason") or ""))


def _toolsmith_instead(row: dict, app_id: str, cap: dict, why: str, *, backend: Any = None,
                       reach: Any = None) -> Optional[dict]:
    """Hand the job to Toolsmith: connecting code over the same parts, then
    the same proof. Its report row, or None when Toolsmith wrote nothing."""
    from puffpai.assemble import accept
    parts = [{**p, "sample_problem": why} for p in row.get("parts") or []]
    if not parts:
        return None
    ts = run_toolsmith(app_id, cap, parts, BUDGET)
    if not ts.get("glue"):
        return report.entry(row, report.NOT_YET, ts.get("reason") or why, app_id=app_id,
                            decision=row["decision"])
    spec = named_tabs(drawable_views(with_view(_glue_spec(app_id, cap, ts["glue"]),
                                               str(row.get("view") or ""))))
    if spec_mod.validate(spec):
        return None
    save_spec(spec)
    started = start_assembled(app_id)
    if not started["ok"]:
        return report.entry(row, report.NOT_YET, started["reason"] or STILL_BUILDING,
                            app_id=app_id, decision=row["decision"])
    proof = accept.prove(app_id, spec, backend=backend, reach=reach).get(cap["id"]) or {}
    state = report.WORKING if proof.get("ok") else report.NOT_YET
    return report.entry(row, state, proof.get("reason") or ("" if proof else "it has no check"),
                        app_id=app_id, decision=row["decision"], checks=proof.get("checks"))


def without_failed_extras(spec: dict, proof: dict) -> Optional[dict]:
    """*spec* without the views and checks of extra sources whose check
    failed, when the first source (the job's own pick) passed; else None."""
    if not isinstance(proof, dict) or proof.get("ok"):
        return None
    sources = [s.get("id") for s in spec.get("sources") or [] if isinstance(s, dict)]
    src_of = {a.get("id"): a.get("source") for a in spec.get("actions") or [] if isinstance(a, dict)}
    results = [c for c in proof.get("checks") or [] if isinstance(c, dict)]
    if not sources or not results:
        return None
    first_ok = [c.get("ok") for c in results if src_of.get(c.get("action")) == sources[0]]
    if not first_ok or not all(first_ok):
        return None
    bad = {src_of.get(c.get("action")) for c in results if not c.get("ok")} - {sources[0]}
    if not bad:
        return None
    views = [v for v in spec.get("views") or [] if not (isinstance(v, dict) and v.get("source") in bad)]
    checks = [c for c in spec.get("checks") or []
              if not (isinstance(c, dict) and src_of.get(c.get("action")) in bad)]
    trial = {**spec, "views": views, "checks": checks}
    return None if spec_mod.validate(trial) else trial


_TAB_STOP = ("latest", "upcoming", "daily", "new", "recent", "live", "current", "today's",
             "search", "the", "a", "an")
_TAB_CUT = (" from ", " of ", " with ", " collected ", " for ", " in ", " that ", " which ",
            " taken ", " by ")


def tab_name(src: dict) -> str:
    """A short tab name from what a source holds ("Upcoming rocket launches
    from around the world" -> "Rocket launches"), else its own name."""
    about = " ".join(str(src.get("about") or "").split())
    head = about.split(":")[0].split(".")[0].split(",")[0]
    low = f" {head.lower()} "
    for cut in _TAB_CUT:
        at = low.find(cut)
        if at > 0:
            head = head[:at - 1] if at - 1 <= len(head) else head
            low = f" {head.lower()} "
    words = [w for w in head.split() if w.lower() not in _TAB_STOP][:3]
    if words:
        text = " ".join(words)
        return (text[:1].upper() + text[1:])[:28]
    name = str(src.get("name") or "").split("(")[0].replace(" API", "").strip()
    return name[:28]


def named_tabs(spec: dict) -> dict:
    """Two views of the same kind ("List" and "List") get tab names by what
    their sources hold ("Rocket launches", "Space news stories")."""
    views = spec.get("views") if isinstance(spec, dict) else None
    if not isinstance(views, list):
        return spec
    kinds = [v.get("type") for v in views if isinstance(v, dict)]
    dup = {k for k in kinds if kinds.count(k) > 1 and k != "settings"}
    if not dup:
        return spec
    srcs = {s.get("id"): s for s in spec.get("sources") or [] if isinstance(s, dict)}
    out = []
    for v in views:
        if isinstance(v, dict) and v.get("type") in dup and not v.get("title"):
            name = tab_name(srcs.get(v.get("source")) or {})
            v = {**v, "title": name} if name else v
        out.append(v)
    return {**spec, "views": out}


def drawable_views(spec: dict) -> dict:
    """Views whose source has nothing they can draw (a picture gallery of a
    source with no pictures) become a list when the source has titles, else
    go, with their checks; the source stays for chat. Never leaves an app
    with no view."""
    views = spec.get("views") if isinstance(spec, dict) else None
    if not isinstance(views, list):
        return spec
    fields_of = {s.get("id"): s.get("fields") or {} for s in spec.get("sources") or []
                 if isinstance(s, dict)}
    keep, gone = [], set()
    for v in views:
        need = VIEW_NEEDS.get(str(v.get("type") or "")) if isinstance(v, dict) else None
        have = fields_of.get(v.get("source")) if isinstance(v, dict) else None
        if need and have is not None and any(f not in have for f in need):
            if "title" in have and v.get("type") != "list":
                keep.append({**v, "type": "list"})
                continue
            gone.add(v.get("source"))
            continue
        keep.append(v)
    if not [v for v in keep if isinstance(v, dict) and v.get("type") != "settings"]:
        return spec
    checks = []
    for c in spec.get("checks") or []:
        src = next((a.get("source") for a in spec.get("actions") or []
                    if isinstance(a, dict) and isinstance(c, dict) and a.get("id") == c.get("action")), None)
        if src in gone:
            continue
        if isinstance(c, dict) and isinstance(c.get("expect"), dict) and src is not None:
            types = [str(v.get("type")) for v in keep if isinstance(v, dict) and v.get("source") == src]
            if types and types != c["expect"].get("views"):
                c = {**c, "expect": spec_mod.expect_for(types, fields_of.get(src) or {})}
        checks.append(c)
    trial = {**spec, "views": keep, "checks": checks}
    return spec if spec_mod.validate(trial) else trial


#: What a view cannot be drawn without.
VIEW_NEEDS = {"gallery": ("image",), "map": ("lat", "lon"), "player": ("stream",),
              "list": ("title",)}


def with_view(spec: dict, view: str) -> dict:
    """*spec* showing *view* first (the job's ``VIEW[output]``, set by the
    build): its first view's type becomes *view* when the spec has none of
    that type and the changed spec still passes :func:`spec_mod.validate`.
    Otherwise *spec* as it was; the proof then says plainly what is missing."""
    views = spec.get("views") if isinstance(spec, dict) else None
    if not view or view not in spec_mod.VIEWS or not isinstance(views, list) or not views:
        return spec
    if any(isinstance(v, dict) and v.get("type") == view for v in views):
        # The job's own view opens first (a list for "upcoming launches",
        # not the pictures one of its sources also has).
        at = next(n for n, v in enumerate(views) if isinstance(v, dict) and v.get("type") == view)
        return {**spec, "views": [views[at]] + views[:at] + views[at + 1:]} if at else spec
    first = dict(views[0]) if isinstance(views[0], dict) else {}
    old_type = first.get("type")
    fields_of = {s.get("id"): s.get("fields") or {} for s in spec.get("sources") or []
                 if isinstance(s, dict)}
    need = VIEW_NEEDS.get(view, ())
    if any(f not in (fields_of.get(first.get("source")) or {}) for f in need):
        return spec      # its data has nothing that view can show: its own view stays
    first["type"] = view
    trial = {**spec, "views": [first] + list(views[1:])}
    # Its checks ask for what the view now shows (a list needs no pictures).
    checks = []
    for c in spec.get("checks") or []:
        c = dict(c) if isinstance(c, dict) else c
        exp = c.get("expect") if isinstance(c, dict) else None
        if isinstance(exp, dict) and old_type in (exp.get("views") or []):
            src = str(first.get("source") or "")
            types = [view if v == old_type else v for v in exp.get("views") or []]
            c["expect"] = spec_mod.expect_for(types, fields_of.get(src) or {})
        checks.append(c)
    if checks:
        trial["checks"] = checks
    return spec if spec_mod.validate(trial) else trial


# ── the build's make step (puffpai.build.make) ─────────────────────────────
#
# The one build: ONE Yes covers the whole list,
# so there is no separate Start card. Route C reaches Assemble only through
# :func:`make_job_app`, called by ``puffpai.build.make`` after it has checked
# that the build's recorded Yes covers the job. The Yes is written into the
# same yes file the per-app card used (bound to the card's sha), so every
# check further down (Toolsmith, the glue runner, the app server) still asks
# :func:`consent.has_yes` and still refuses when the sites or outside code
# change later (then that change gets its own single Yes).

BUILD_YES = "build_yes"


def record_build_yes(app_id: str, *, by: str = "owner", yes_ref: str = "") -> bool:
    """Record that the build's one Yes covers assembled app *app_id* as its
    card reads now. False when the plan has no such app. Writes one audit
    line. Only :func:`make_job_app` calls this."""
    card = consent.card(app_id)
    if card is None:
        return False
    with consent._LOCK:  # noqa: SLF001 - the same lock as say_yes
        data = consent._yeses()  # noqa: SLF001
        data[app_id] = {"sha": card["sha"], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                time.gmtime()),
                        "by": BUILD_YES, "who": str(by or "owner")[:40],
                        "yes": str(yes_ref or "")[:80]}
        fsutil.atomic_write_json(consent.yes_path(), data)
    consent._audit(BUILD_YES, app_id)  # noqa: SLF001
    return True


def put_plan_row(row: dict) -> None:
    """Put one job's row into ``plan.json`` (replacing a row for the same app
    or capability), so its card and every consent check can find it."""
    plan = plan_mod.load_plan()
    rows = [r for r in plan.get("capabilities") or []
            if isinstance(r, dict) and r.get("app_id") != row.get("app_id")
            and r.get("cap") != row.get("cap")]
    rows.append(dict(row))
    plan["capabilities"] = rows
    plan["made_at"] = int(time.time())
    fsutil.atomic_write_json(plan_mod.plan_path(), plan)


def make_job_app(row: dict, *, by: str = "owner", yes_ref: str = "", ask: Any = None,
                 backend: Any = None, getter: Any = None, reach: Any = None) -> dict:
    """Put one job's app together after the build's one Yes; its report row.

    *row* is a plan row (``cap``, ``name``, ``does``, ``app_id``, ``parts``,
    ``decision`` = assemble, ``view``). The caller has checked the Yes."""
    row = {**row, "decision": plan_mod.ASSEMBLE}
    if not APP_ID_RE.match(str(row.get("app_id") or "")):
        row["app_id"] = plan_mod.app_id_for(row.get("cap"))
    put_plan_row(row)
    if not record_build_yes(row["app_id"], by=by, yes_ref=yes_ref):
        got = report.entry(row, report.NOT_YET, "its plan could not be kept",
                           app_id=row["app_id"], decision=row["decision"])
    else:
        got = assemble_one(row, ask=ask, backend=backend, getter=getter, reach=reach)
    # Kept, so chat offers a working app's tools (``provide``) and the pages
    # read the same state.
    report.update(got)
    return got


def _glue_spec(app_id: str, cap: dict, glue: list[dict]) -> dict:
    """A spec over connecting code only: a gallery when its rows have
    pictures, else a table; one action and one check per capability."""
    sources, views, actions = [], [], []
    for n, g in enumerate(glue[:4], 1):
        sid = re.sub(r"[^a-z0-9_]", "_", str(g.get("id") or f"g{n}").lower())[:40] or f"g{n}"
        if not sid[0].isalpha():
            sid = f"g{sid}"[:40]
        src = {"id": sid, "kind": "glue", "module": str(g.get("module") or ""),
               "fn": str(g.get("fn") or "")}
        if isinstance(g.get("args"), dict):
            src["args"] = g["args"]
        sources.append(src)
        fields = [str(f) for f in g.get("fields") or []]
        views.append({"type": "gallery" if "image" in fields else "table", "source": sid,
                      **({"lightbox": True} if "image" in fields else {})})
        actions.append({"id": f"get_{sid}"[:40], "source": sid, "input": {}})
    first = glue[0] if glue else {}
    expect = [f for f in ("image", "stream", "title") if f in (first.get("fields") or [])][:1]
    checks = [{"cap": cap["id"], "ask": f"Show me {cap['name'].lower()}",
               "action": actions[0]["id"] if actions else "", "input": {},
               "expect": {"min_items": 1, "fields": expect}}]
    return spec_mod._assemble(app_id, cap["name"], [cap["id"]], sources, views,  # noqa: SLF001
                              actions, checks)


def run_all(*, cap: str = "", ask: Any = None, backend: Any = None, getter: Any = None,
            reach: Any = None) -> dict:
    """Plan, put together and prove every capability (or just *cap*)."""
    _running.set()
    try:
        _write_status(state="running", done=0, total=0, line="Looking at what was found.",
                      started_at=int(time.time()), finished_at=None)
        plan = plan_mod.build_plan()
        rows = plan["capabilities"]
        todo = [r for r in rows if not cap or r["cap"] == cap]
        total = len(todo)
        out = []
        for i, row in enumerate(todo, 1):
            line = f"Putting together: {i} of {total}"
            _write_status(done=i - 1, total=total, line=line, current=row["name"])
            _say(f"{line} ({row['name']})")
            if row["decision"] == plan_mod.USE_AS_IS:
                got = report.entry(row, report.USES, row["why"])
            elif row["decision"] == plan_mod.NOT_FOUND:
                got = report.entry(row, report.NOT_YET, "found nothing that does this"
                                   if row.get("left_out") else "nothing was found for it yet")
            else:
                try:
                    got = assemble_one(row, ask=ask, backend=backend, getter=getter, reach=reach)
                except Exception as exc:  # noqa: BLE001 - one capability never stops the rest
                    logger.warning("assemble: %s stopped (%s)", row["cap"], type(exc).__name__)
                    got = report.entry(row, report.NOT_YET,
                                       f"putting it together stopped ({type(exc).__name__})",
                                       app_id=row.get("app_id") or "")
            out.append(got)
            if got["state"] == report.NOT_YET and row["decision"] == plan_mod.ASSEMBLE:
                _say(f"{row['name']}: not yet", status="error", reason=got["reason"])
        data = report.load() if cap else report.write(out)
        for got in out if cap else ():
            data = report.update(got)
        working = sum(1 for r in out if r.get("state") == report.WORKING)
        waits = [r for r in out if r.get("reason") == consent.WAITING]
        if waits:
            line = (f"{consent.WAITING}: {len(waits)} app{'s' if len(waits) != 1 else ''} "
                    f"to start ({', '.join(r['name'] for r in waits[:4])}).")
            _write_status(state="waiting_yes", done=total, total=total, line=line,
                          finished_at=int(time.time()), current="")
        else:
            line = (f"Finished: {working} of {total} capabilities working, proven with a "
                    f"real question.")
            _write_status(state="done", done=total, total=total, line=line,
                          finished_at=int(time.time()), current="")
        _say(line)
        return data
    finally:
        _running.clear()


PENDING = "putting it together again"


def _mark_pending(cap: str, reason: str) -> None:
    """Her request is taken: the job's report row says so at once (no stale
    "not switched on yet" while it waits for its turn)."""
    row = report.by_cap().get(cap)
    if row:
        report.update(report.entry(row, report.NOT_YET, reason or PENDING,
                                   app_id=str(row.get("app_id") or ""),
                                   decision=str(row.get("decision") or "")))


def start_background(*, pending: str = "", **kw: Any) -> bool:
    """Run :func:`run_all` on a thread. True when it starts now. A request for
    one capability that comes while a run is going is queued (once) and runs
    right after it; a request for everything is not queued (False)."""
    cap = str(kw.get("cap") or "")
    if cap:
        _mark_pending(cap, pending)
    with _RUN_LOCK:
        if _running.is_set():
            if cap and cap not in _QUEUED:
                _QUEUED.append(cap)
            return False
        _running.set()
        if cap in _QUEUED:
            _QUEUED.remove(cap)
    _spawn(kw)
    return True


def queued() -> list[str]:
    with _RUN_LOCK:
        return list(_QUEUED)


def _spawn(kw: dict) -> None:
    """The run's thread (``_running`` is already set). When it ends it takes
    the next capability she said yes to meanwhile."""
    def work() -> None:
        args = dict(kw)
        while True:
            try:
                run_all(**args)
            except Exception as exc:  # noqa: BLE001
                logger.warning("assemble run stopped: %s", type(exc).__name__)
                _write_status(state="stopped", line="Putting it together stopped. Try again.")
            with _RUN_LOCK:
                if not _QUEUED or _running.is_set():
                    return
                args = {**kw, "cap": _QUEUED.pop(0)}
                _running.set()
    threading.Thread(target=work, name="assemble-run", daemon=True).start()


def start_after_yes(cap: str) -> bool:
    """Her yes for one app: put it together now, or right after the run that
    is going. True when it starts now."""
    with _RUN_LOCK:
        if cap and cap not in _QUEUED:
            _QUEUED.append(cap)
        if _running.is_set() or not _QUEUED:
            return False
        _running.set()
        first = _QUEUED.pop(0)
    _spawn({"cap": first})
    return True


def resume_waiting(delay: float = 0.0) -> bool:
    """Start the run the wizard left waiting (the bot's server is up now).
    *delay*: seconds to wait first, on the run's own thread, so the server
    is answering before the app servers call back."""
    st = fsutil.read_json(status_path(), default=None)
    if not (isinstance(st, dict) and st.get("state") == "waiting"):
        return False
    if delay > 0:
        threading.Timer(delay, start_background).start()
        return True
    return start_background()


# ── routes ──────────────────────────────────────────────────────────────────

bp = Blueprint("assemble", __name__)
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


#: How each decision reads on the "Building now" panel.
HOW_WORDS = {plan_mod.USE_AS_IS: "Uses what was found as it is",
             plan_mod.ASSEMBLE: "Putting it together from parts",
             plan_mod.NOT_FOUND: "Nothing found yet"}
#: Team members whose lines the panel shows.
TEAM = ("majordomo", "toolsmith", "drawbridge", "sentinel")
LINES_MAX = 20


def recent_lines(limit: int = LINES_MAX) -> list[dict]:
    """The team's newest lines, oldest first: ``[{"agent", "text", "status", "at"}]``."""
    from puffpai import agent_activity
    import json as _json
    try:
        with open(agent_activity.path(), encoding="utf-8") as fh:
            tail = fh.readlines()[-400:]
    except OSError:
        return []
    out = []
    for raw in tail:
        try:
            row = _json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("agent") in TEAM:
            out.append({"agent": row["agent"], "text": str(row.get("task") or ""),
                        "status": str(row.get("status") or ""),
                        "at": str(row.get("timestamp") or "")})
    return out[-limit:]


def status_view() -> dict:
    """``{"running", "phase", "lines", "plan", "waiting_yes", "status"}`` for the
    panel. Reading it never starts anything."""
    st = read_status()
    rows = plan_mod.load_plan()["capabilities"]
    return {"running": st.get("state") == "running", "phase": str(st.get("line") or ""),
            "state": st.get("state"), "done": st.get("done", 0), "total": st.get("total", 0),
            "lines": recent_lines(),
            "plan": [{"cap": r.get("cap"), "name": r.get("name"),
                      "how": HOW_WORDS.get(r.get("decision"), ""),
                      "decision": r.get("decision"), "why": r.get("why"),
                      "app_id": r.get("app_id"),
                      "finds": [x.get("name") for x in [*(r.get("uses") or []),
                                                        *(r.get("parts") or [])]]}
                     for r in rows],
            "waiting_yes": _cards(),
            "queued": [{"cap": c, "name": next((str(r.get("name") or "") for r in rows
                                                if r.get("cap") == c), c)} for c in queued()],
            "status": st}


def _cards() -> list[dict]:
    try:
        return [public_card(c) for c in consent.waiting()]
    except Exception as exc:  # noqa: BLE001 - the panel still shows the rest
        logger.info("assemble: yes cards not read (%s)", type(exc).__name__)
        return []


def public_card(c: dict) -> dict:
    """A Start card as her pages get it (words and ids only)."""
    return {k: c.get(k) for k in ("app_id", "cap", "title", "sites", "packages", "lines",
                                  "button", "sha", "said_yes")}


def _fix_card(fix: Any) -> Optional[dict]:
    """A report row's fix as the one-tap card the Assembled panel shows."""
    if not isinstance(fix, dict):
        return None
    try:
        from puffpai import fix_api
        got = fix_api.clean(fix)
        return fix_api.public(got) if got else None
    except Exception:  # noqa: BLE001 - no card, the reason still shows
        return None


def report_view() -> dict:
    """``{"apps": [{"id", "title", "thing", "caps": [{"cap", "name", "state",
    "reason"}]}], "capabilities": [...]}``. *thing* is the assembled app's id
    (the thing that shows it is named after it); capabilities used as they are
    or with nothing found are in *capabilities* only."""
    data = report.load()
    apps: dict[str, dict] = {}
    for row in data["capabilities"]:
        aid = str(row.get("app_id") or "")
        if not aid:
            continue
        if aid not in apps:
            spec = load_spec(aid) or {}
            apps[aid] = {"id": aid, "title": spec.get("title") or row.get("name") or aid,
                         "thing": f"assembled-{aid}", "caps": []}
        apps[aid]["caps"].append({"cap": row.get("cap"), "name": row.get("name"),
                                  "state": row.get("state"), "reason": row.get("reason"),
                                  "line": row.get("line"), "fix": row.get("fix"),
                                  "card": _fix_card(row.get("fix"))})
    return {**data, "apps": list(apps.values())}


@bp.route("/assemble/status", methods=["GET"])
def assemble_status():
    return jsonify({"ok": True, **status_view()})


@bp.route("/assemble/run", methods=["POST"])
def assemble_run():
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    cap = str(body.get("cap") or body.get("capability") or "")[:80]
    app = str(body.get("app_id") or body.get("thing") or "")[:80].removeprefix("assembled-")
    if app and not cap:     # one app (its thing page): its own capability only
        cap = next((str(r.get("cap")) for r in plan_mod.load_plan()["capabilities"]
                    if r.get("app_id") == app), "")
        if not cap:
            return jsonify({"ok": False, "message": "There is no app by that name."}), 404
    if cap and not re.match(r"^cap-[a-z0-9-]{1,60}$", cap):
        return jsonify({"ok": False, "message": "There is no capability by that name."}), 400
    started = start_background(cap=cap)
    waits = bool(cap) and not started
    return jsonify({"ok": True, "started": started, "queued": waits,
                    "message": "Putting it together now." if started else
                    "It is next, right after the one being put together now." if waits
                    else "It is already being put together."}), (202 if started or waits else 200)


@bp.route("/assemble/report", methods=["GET"])
def assemble_report():
    return jsonify({"ok": True, **report_view()})


@bp.route("/assemble/yes", methods=["GET"])
def assemble_yes_cards():
    app = str(request.args.get("app") or "")
    cards = _cards()
    if app:
        cards = [c for c in cards if c["app_id"] == app]
    return jsonify({"ok": True, "cards": cards})


def yes_and_start(app_id: Any, device: str, sha: str = "") -> tuple[dict, int]:
    """Her Start on one card (the thing page, the wizard's last screen, or
    the chat's card through ``fix_api``). ``(answer, status)``."""
    try:
        got = consent.say_yes(app_id, device, sha=sha)
    except consent.YesError as exc:
        return {"ok": False, "message": exc.line}, exc.status
    now = start_after_yes(got["cap"])
    line = (f"Putting {got['title']} together now." if now
            else f"{got['title']} is next, right after what is being put together now.")
    return {"ok": True, "line": line, "card": public_card(got), "started": now}, 202


@bp.route("/assemble/yes", methods=["POST"])
def assemble_yes():
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    got, status = yes_and_start(str(body.get("app_id") or "")[:40], _device(),
                                str(body.get("sha") or "")[:64])
    return jsonify(got), status


register_tools()


__all__ = ["BUDGET", "BUILD_YES", "STILL_BUILDING", "all_specs", "app_tools", "assemble_one", "bp",
           "call_action", "fetch_source", "fill_url", "load_spec", "make_job_app",
           "mark_waiting", "provide", "put_plan_row", "record_build_yes", "with_view",
           "read_status", "register_tools", "resume_waiting", "run_all", "run_toolsmith",
           "save_spec", "start_after_yes", "start_assembled", "start_background",
           "tool_name", "waiting_tool", "yes_and_start"]
