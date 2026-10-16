"""The backend side of an assembled app: its folder, its yes-covered plan, its actions.

An assembled app lives in ``<state>/assemble/<app id>/``::

    spec.json        the ``assembled_app/v1`` spec (written by the plan step)
    settings.json    her setting values (A1's store; never a key)
    glue/<name>.py   connecting code Toolsmith wrote (checked by Drawbridge)
    checks.json      the fingerprint of every glue file that passed its check
    parts.json       the outside sites (and their switches) Toolsmith was given
    server/token     the app server's token for BuildBot's gateway

It runs through :mod:`puffpai.integrate.run_here` as plan type ``assembled``:
:func:`start_assembled` writes (or refreshes) that plan and starts it, bound
to 127.0.0.1 on a free port, in a Job Object, watched like every app here.
The plan's fingerprint covers the spec and every glue file
(``plan["assembled"]``); a glue file changed by anything but a checked
Toolsmith rewrite stops the app from starting (:func:`prepare_start`).

The one yes: the owner's yes for an assembled app covers starting it and
Toolsmith's rewrites within its budget (:mod:`puffpai.assemble.consent`).
This module does not ask, but it checks: :func:`prepare_start` (every start,
whoever asks) and :func:`call_action` refuse an app without that yes.

Outside data never leaves through here by a path of its own:

* a declared source (``http_json``, ``rss``, ``m3u``) is fetched by
  :func:`puffpai.assemble.api.fetch_source` (the spec's own
  address, under the source's own Bridge switch);
* glue asking for an address (``puff_glue.fetch_json``) is fetched by
  :func:`puffpai.assemble.shape.fetch_sample` under the switch of the source
  or found part that names that site, and only for those sites; a key comes
  from the vault through ``source_fetch.fetch_small`` (bound to its own
  sites) and never comes back;
* pictures and stream pieces (:func:`media`) come from a site a declared
  source names, through ``egress_doors.egress_get`` under that source's
  switch; Range is answered here from the fetched piece (the site itself gets
  a whole-piece request).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
from typing import Any, Callable, Optional

from puffpai import fsutil
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SPEC_SCHEMA = "assembled_app/v1"
#: The assemble API's app id (``puffpai.assemble.APP_ID_RE``), restated.
_APP_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}\Z")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")
OUTSIDE_KINDS = ("http_json", "rss", "m3u")
MAX_ROWS = 200
MAX_FETCH_BYTES = 10 * 1024 * 1024
MAX_MEDIA_BYTES = 20 * 1024 * 1024
MEDIA_TIMEOUT = 20.0
_SERVER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
_SCAFFOLD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "scaffold")

_MEDIA_CACHE: dict[str, tuple[float, int, dict, bytes]] = {}
_MEDIA_LOCK = threading.Lock()
_MEDIA_KEEP = 8
_MEDIA_SECONDS = 60.0

#: Seam for the tests: the Bridge's GET for pictures and stream pieces.
_media_get = None


class AssembleError(Exception):
    """A plain sentence for the owner, a short code and an optional fix."""

    def __init__(self, line: str, code: str = "failed", fix: Optional[dict] = None):
        super().__init__(line)
        self.line = line
        self.code = code
        self.fix = fix or {}


def _lane(modname: str) -> Any:
    """The assemble API's module, or None when it is not in this build yet."""
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as exc:
        if exc.name and (exc.name == modname or modname.startswith(exc.name + ".")):
            return None
        raise


# ---------------------------------------------------------------------------
# folders, ids, tokens
# ---------------------------------------------------------------------------

def check_app_id(app_id: Any) -> str:
    if not isinstance(app_id, str) or not _APP_RE.match(app_id):
        raise AssembleError("That app is not known.", "unknown")
    return app_id


def assemble_root() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(create=True), "assemble")


def app_folder(app_id: str) -> str:
    return os.path.join(assemble_root(), check_app_id(app_id))


def glue_dir(app_id: str) -> str:
    return os.path.join(app_folder(app_id), "glue")


def spec_path(app_id: str) -> str:
    return os.path.join(app_folder(app_id), "spec.json")


def host_id(app_id: str) -> str:
    """The id :mod:`run_here` uses for the app: ``<slug>-<8 letters>``, the
    same every time for the same app id."""
    check_app_id(app_id)
    slug = re.sub(r"[^a-z0-9]+", "-", app_id).strip("-")[:29].strip("-") or "app"
    tail = base64.b32encode(hashlib.sha256(app_id.encode()).digest()).decode()[:8].lower()
    return f"{slug}-{tail}"


def server_token(app_id: str) -> str:
    """The app server's gateway token (made once, kept in its folder)."""
    path = os.path.join(app_folder(app_id), "server", "token")
    try:
        token = read_server_token(app_id)
    except (OSError, ValueError):
        token = ""
    if not token:
        token = secrets.token_urlsafe(32)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fsutil.atomic_write(path, token)
    return token


def read_server_token(app_id: str) -> str:
    path = os.path.join(app_folder(app_id), "server", "token")
    with open(path, "r", encoding="ascii") as fh:
        token = fh.read().strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,100}", token):
        raise ValueError("token")
    return token


def owner_fix(app_id: str, fix: Any) -> Optional[dict]:
    """A fix as her pages show it: the chat's card shape (:func:`puffpai.fix_api.clean`,
    words and ids only), or None when there is nothing she can do from here."""
    if not isinstance(fix, dict):
        return None
    try:
        from puffpai import fix_api
        card = fix_api.clean({**fix, "app_id": fix.get("app_id") or app_id})
        return fix_api.public(card) if card else None
    except Exception:  # noqa: BLE001 - a card we cannot make is no card
        return None


def make_fix(kind: str, **fields: Any) -> dict:
    """A fix the page and the chat can offer (the assemble API's shape):
    ``{"kind": "switch_on", "door", "service"}``, ``{"kind": "setting", "key"}``,
    ``{"kind": "key", "key"}``."""
    return {"kind": kind, **{k: v for k, v in fields.items() if v is not None}}


# ---------------------------------------------------------------------------
# the spec, its sites, and the parts Toolsmith was given
# ---------------------------------------------------------------------------

def load_spec(app_id: str) -> dict:
    data = fsutil.read_json(spec_path(app_id), default=None)
    if not isinstance(data, dict) or data.get("schema") != SPEC_SCHEMA:
        raise AssembleError("This app has no plan BuildBot can read yet.", "no_spec")
    spec_mod = _lane("puffpai.assemble.spec")
    if spec_mod is not None and spec_mod.validate(data):
        raise AssembleError("This app's plan did not pass its own check.", "bad_spec")
    return data


def _host_of(url: Any) -> str:
    try:
        parts = urllib.parse.urlsplit(str(url or "").replace("{", "x").replace("}", "x"))
    except ValueError:
        return ""
    if parts.scheme != "https":
        return ""
    return (parts.hostname or "").rstrip(".").lower()


def record_parts(app_id: str, parts: Any) -> None:
    """Keep which sites (and switches) the found parts came with, so glue can
    ask for those sites and no others."""
    rows = []
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        host = _host_of(p.get("url"))
        door = str(p.get("door") or "")
        if host and door and len(rows) < 12:
            rows.append({"host": host, "door": door[:120],
                         "service": str(p.get("service") or "")[:60]})
    os.makedirs(app_folder(app_id), exist_ok=True)
    fsutil.atomic_write_json(os.path.join(app_folder(app_id), "parts.json"), {"sites": rows})


def _sites(app_id: str, spec: dict) -> list[dict]:
    """``[{"host", "door", "service", "source"}]``: declared sources first,
    then the found parts Toolsmith was given."""
    out = []
    for src in spec.get("sources") or []:
        if isinstance(src, dict) and src.get("kind") in OUTSIDE_KINDS and _host_of(src.get("url")):
            out.append({"host": _host_of(src.get("url")), "door": str(src.get("door") or ""),
                        "service": str(src.get("service") or ""),
                        "source": str(src.get("id") or "")})
    data = fsutil.read_json(os.path.join(app_folder(app_id), "parts.json"), default={}) or {}
    for row in (data.get("sites") or []) if isinstance(data, dict) else []:
        if isinstance(row, dict) and row.get("host") and row.get("door"):
            out.append({"host": str(row["host"]), "door": str(row["door"]),
                        "service": str(row.get("service") or ""), "source": ""})
    return out


def spec_sites(app_id: str, spec: dict) -> list[str]:
    return list(dict.fromkeys(s["host"] for s in _sites(app_id, spec)))


# ---------------------------------------------------------------------------
# glue fingerprints (what the yes covers)
# ---------------------------------------------------------------------------

def glue_files(app_id: str) -> dict[str, str]:
    """``{"glue/<name>.py": sha256}`` for every glue file now on disk."""
    out: dict[str, str] = {}
    folder = glue_dir(app_id)
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return out
    for name in names:
        full = os.path.join(folder, name)
        if name.endswith(".py") and os.path.isfile(full) and not os.path.islink(full):
            with open(full, "rb") as fh:
                out[f"glue/{name}"] = hashlib.sha256(fh.read()).hexdigest()
    return out


def glue_sha(app_id: str) -> str:
    blob = json.dumps(glue_files(app_id), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _checks_path(app_id: str) -> str:
    return os.path.join(app_folder(app_id), "checks.json")


def checked_files(app_id: str) -> dict[str, str]:
    data = fsutil.read_json(_checks_path(app_id), default={})
    return data if isinstance(data, dict) else {}


def record_checked(app_id: str, rel: str, sha: str) -> None:
    """Drawbridge passed *rel* at *sha* (called by the Toolsmith loop)."""
    data = checked_files(app_id)
    data[rel] = sha
    fsutil.atomic_write_json(_checks_path(app_id), data)


def unchecked_glue(app_id: str) -> list[str]:
    """Glue files whose bytes are not the ones Drawbridge passed."""
    done = checked_files(app_id)
    return [rel for rel, sha in glue_files(app_id).items() if done.get(rel) != sha]


def _spec_sha(app_id: str) -> str:
    try:
        with open(spec_path(app_id), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# the run_here plan
# ---------------------------------------------------------------------------

def _dashboard_port() -> int:
    """The dashboard's port: the only page that may show an app in a frame."""
    try:
        from puffpai import webapps
        return int(webapps.dashboard_port())
    except Exception:  # noqa: BLE001
        return 3003


def make_plan(app_id: str, port: int) -> dict:
    from puffpai.assemble.server import gateway
    from puffpai.integrate import run_here
    spec = load_spec(app_id)
    folder = app_folder(app_id)
    return {
        "schema": run_here.SCHEMA, "how": "assembled", "runtime": "python",
        "steps": [],
        "start": {"tool": "assembled_server", "args": [
            "--port", str(int(port)), "--app-id", app_id, "--folder", folder,
            "--gateway-file", gateway.gateway_file(), "--scaffold", _SCAFFOLD_DIR,
            "--dashboard-port", str(_dashboard_port())]},
        "port": int(port), "interfaces": {"openapi": "runtime:/openapi.json"},
        "keys": [], "outside_parts": [],
        "notes": ["BuildBot put this app together. Its own server makes no outside "
                  "call; BuildBot fetches for it through the Bridge, only from the "
                  "sites its parts name, while each one is switched on."],
        "assembled": {"app_id": app_id, "spec_sha": _spec_sha(app_id),
                      "glue_sha": glue_sha(app_id), "sites": spec_sites(app_id, spec)},
    }


def prepare_start(iid: str, plan: dict) -> None:
    """Called by ``run_here.start`` for an ``assembled`` plan, before anything
    runs: the spec and every glue file are exactly what the yes (and later
    checked rewrites) covered, and the gateway is up. Raises
    :class:`AssembleError`."""
    info = plan.get("assembled") if isinstance(plan.get("assembled"), dict) else {}
    app_id = check_app_id(info.get("app_id"))
    if host_id(app_id) != iid:
        raise AssembleError("This app's plan does not match it.", "changed")
    _need_yes(app_id)
    if _spec_sha(app_id) != info.get("spec_sha") or glue_sha(app_id) != info.get("glue_sha"):
        raise AssembleError("Its plan or its connecting code changed after it was "
                            "checked, so BuildBot did not start it. Put it together "
                            "again.", "changed")
    if unchecked_glue(app_id):
        raise AssembleError("Some of its connecting code was not checked by Drawbridge, "
                            "so BuildBot did not start it.", "unchecked")
    load_spec(app_id)
    from puffpai.assemble.server import gateway
    gateway.ensure_running()
    server_token(app_id)


def _need_yes(app_id: str) -> None:
    """Raise unless the owner's one yes covers this app as it is now."""
    from puffpai.assemble import consent
    if not consent.has_yes(app_id):
        raise AssembleError(f"{consent.WAITING}: nothing of this app starts until you "
                            f"tap Start on its card.", "waiting_yes",
                            consent.fix_for(app_id))


def install(app_id: str, *, by: str = "owner") -> dict:
    """Write (or refresh) the app's run_here plan. Keeps its port when it has one.

    A refresh happens after a checked Toolsmith rewrite: the plan's
    fingerprint then covers the new glue."""
    from puffpai.integrate import run_here
    check_app_id(app_id)
    if unchecked_glue(app_id):
        raise AssembleError("Some of its connecting code was not checked by Drawbridge.",
                            "unchecked")
    hid = host_id(app_id)
    old = run_here.read_state(hid)
    port = (old.get("plan") or {}).get("port") or run_here.free_port()
    plan = make_plan(app_id, int(port))
    os.makedirs(run_here.app_dir(hid), exist_ok=True)
    state = dict(old)
    state.update(plan=plan, plan_sha=run_here.plan_sha(plan))
    state.setdefault("wanted_on", False)
    state.setdefault("state", "stopped")
    state.setdefault("set_up_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    run_here._save_state(hid, state)  # noqa: SLF001 - the assembled plan type's own writer
    run_here._security_log("install", hid, "done", details="assembled", by=by)  # noqa: SLF001
    return plan


def start_assembled(app_id: str) -> dict:
    """Start the app (after her one yes). ``{"ok", "port", "reason"?, "url"}``."""
    from puffpai.integrate import run_here
    try:
        install(app_id, by="owner")
        hid = host_id(app_id)
        now = run_here.status(hid)
        if now["state"] in ("running", "starting") and now.get("port"):
            return {"ok": True, "port": int(now["port"]), "url": now.get("url") or ""}
        got = run_here.start(hid, by="owner", wait=15.0)
    except AssembleError as exc:
        return {"ok": False, "port": None, "reason": exc.line, "code": exc.code}
    st = got.get("status") or {}
    port = st.get("port") or (run_here.read_state(hid).get("plan") or {}).get("port")
    out = {"ok": bool(got.get("ok")), "port": int(port) if port else None,
           "url": st.get("url") or ""}
    if not got.get("ok"):
        out.update(reason=str(got.get("line") or ""), code=got.get("code", ""))
    return out


# ---------------------------------------------------------------------------
# glue asking for an address (puff_glue.fetch_json / fetch_text)
# ---------------------------------------------------------------------------

def _key_service(spec: dict, setting: str) -> str:
    """The vault service a ``kind: key`` setting names, or ""."""
    if not setting:
        return ""
    for s in spec.get("settings") or []:
        if isinstance(s, dict) and s.get("key") == setting and s.get("kind") == "key":
            sid = str(s.get("service") or "")
            if re.fullmatch(r"[a-z][a-z0-9_]{1,40}", sid):
                from puffpai.security import keys as keys_mod
                if sid not in keys_mod.listed() and keys_mod.spec(sid) is not None:
                    # A sign-in added on its own page (her GitHub account) is
                    # used only by BuildBot itself, never by an app's code.
                    raise AssembleError("That key is not one an app may use.", "key")
                return sid
    raise AssembleError("That key is not one this app's plan names.", "key",
                        make_fix("key", key=setting))


def backend_fetch(app_id: str, url: str, *, key: str = "", accept: str = "") -> dict:
    """For ``puff_glue.fetch_json`` / ``fetch_text``: ``{"ok", "text"}`` or
    ``{"ok": False, "line", "fix"?}``. Only sites a declared source or a found
    part names, under that site's own switch. The key is added on the way out
    and never comes back."""
    try:
        spec = load_spec(app_id)
        host = _host_of(url)
        site = next((s for s in _sites(app_id, spec) if s["host"] == host), None)
        if site is None:
            raise AssembleError("That site is not one this app's parts name.", "not_listed")
        service = _key_service(spec, key)
        if service:
            from puffpai import source_fetch
            got = source_fetch.fetch_small(url, allowed_hosts={host}, max_bytes=MAX_FETCH_BYTES,
                                           service=service, key_id=service,
                                           honour_robots=False)
            if got.get("reason") != "ok" or got.get("body") is None:
                raise AssembleError(f"The keyed request was not sent or not answered "
                                    f"({got.get('reason')}).", str(got.get("reason") or ""),
                                    make_fix("key", key=key)
                                    if got.get("reason") in ("key_unavailable", "key_host")
                                    else None)
            status = int(got.get("status") or 0)
            if not 200 <= status < 300:
                raise AssembleError(f"The site answered {status}.", "status")
            text = bytes(got["body"]).decode("utf-8", "replace")
            note_result_pictures(app_id, site["door"], text)
            return {"ok": True, "text": text}
        shape = _lane("puffpai.assemble.shape")
        if shape is None:
            raise AssembleError("Not yet: this part of BuildBot is still being built.",
                                "not_in_build")
        got = shape.fetch_sample(site["door"] or None, url, max_bytes=MAX_FETCH_BYTES)
        if not got.get("ok"):
            raise AssembleError(f"Not fetched: {got.get('reason') or 'no answer'}.",
                                "switched_off" if got.get("switch_off") else "fetch",
                                make_fix("switch_on", door=site["door"],
                                         service=site["service"])
                                if got.get("switch_off") else None)
    except AssembleError as exc:
        out = {"ok": False, "line": exc.line, "error": exc.line, "code": exc.code}
        if exc.fix:
            out["fix"] = exc.fix
        return out
    note_result_pictures(app_id, site["door"], str(got.get("text") or ""))
    return {"ok": True, "text": str(got.get("text") or "")}


def fetch_source(app_id: str, source_id: str, input: Any = None) -> dict:  # noqa: A002
    """A declared outside source, by the assemble API (``api.fetch_source``)."""
    api = _lane("puffpai.assemble.api")
    if api is None:
        return {"ok": False, "rows": [], "reason": "Not yet: this part of BuildBot is "
                                                   "still being built."}
    got = api.fetch_source(app_id, str(source_id or ""), input if isinstance(input, dict)
                           else {})
    return got if isinstance(got, dict) else {"ok": False, "rows": [], "reason": "no answer"}


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------

def _clean_input(declared: Any, given: Any) -> dict:
    declared = declared if isinstance(declared, dict) else {}
    given = given if isinstance(given, dict) else {}
    out: dict = {}
    for name, kind in declared.items():
        if name not in given or given[name] in (None, ""):
            continue
        value = given[name]
        if kind in ("number", "integer"):
            try:
                value = float(value) if kind == "number" else int(value)
            except (TypeError, ValueError):
                raise AssembleError(f"{name} should be a number.", "input") from None
        elif kind == "boolean":
            value = str(value).lower() in ("1", "true", "yes", "on") \
                if not isinstance(value, bool) else value
        else:
            value = " ".join(str(value).split())[:200]
        out[str(name)] = value
    return out


def _glue_args(app_id: str, spec: dict, src: dict, inp: dict) -> dict:
    """The glue source's ``args`` with settings filled (A1's rule) and
    ``input:<name>`` taken from the action's input."""
    raw = src.get("args") if isinstance(src.get("args"), dict) else {}
    spec_mod = _lane("puffpai.assemble.spec")
    if spec_mod is not None:
        args, missing = spec_mod.resolve_args(app_id, spec, raw)
        if missing:
            raise AssembleError(f"It needs {missing[0]} first. Set it in this app's "
                                "settings.", "setting", make_fix("setting", key=missing[0]))
    else:
        args = dict(raw)
        if any(isinstance(v, str) and v.startswith("setting:") for v in args.values()):
            raise AssembleError("Not yet: this part of BuildBot is still being built.",
                                "not_in_build")
    out = {}
    for k, v in args.items():
        out[str(k)] = inp.get(v[6:]) if isinstance(v, str) and v.startswith("input:") else v
    for k, v in inp.items():
        out.setdefault(k, v)
    return out


def _media_link(url: Any) -> Any:
    if isinstance(url, str) and url.startswith("https://") and (
            ".m3u8" in url or url.split("?", 1)[0].endswith((".mp3", ".aac", ".mp4",
                                                              ".ts", ".ogg"))):
        return "/media?" + urllib.parse.urlencode({"url": url})
    return url


def call_action(app_id: str, action: str, input: Any) -> dict:  # noqa: A002 - pinned name
    """Run one spec action. ``{"ok", "rows", "count"}`` or
    ``{"ok": False, "rows": [], "reason", "error", "fix"?}``."""
    try:
        _need_yes(app_id)
        spec = load_spec(app_id)
        act = next((a for a in spec.get("actions") or []
                    if isinstance(a, dict) and a.get("id") == action), None)
        if act is None or not _NAME_RE.match(str(action or "")):
            raise AssembleError("That is not one of this app's actions.", "unknown_action")
        src = next((s for s in spec.get("sources") or []
                    if isinstance(s, dict) and s.get("id") == act.get("source")), None)
        if src is None:
            raise AssembleError("That action has no source in the plan.", "no_source")
        inp = _clean_input(act.get("input"), input)
        kind = src.get("kind")
        if kind in OUTSIDE_KINDS:
            got = fetch_source(app_id, str(src.get("id")), inp)
            if not got.get("ok"):
                raise AssembleError(str(got.get("reason") or "It was not fetched."),
                                    "fetch", got.get("fix") if isinstance(got.get("fix"), dict)
                                    else None)
            rows = [r for r in got.get("rows") or [] if isinstance(r, dict)]
            # The source's own answer: its picture addresses, under its switch.
            note_result_pictures(app_id, str(src.get("door") or ""), rows)
        elif kind == "glue":
            from puffpai.assemble import toolsmith_loop
            m = re.fullmatch(r"glue/([a-z][a-z0-9_]{0,39})\.py", str(src.get("module") or ""))
            if not m:
                raise AssembleError("Its connecting code is not named the usual way.",
                                    "glue_name")
            ran = toolsmith_loop.run_glue(app_id, m.group(1),
                                          _glue_args(app_id, spec, src, inp))
            if not ran.get("ok"):
                raise AssembleError(str(ran.get("line") or "Its connecting code did not "
                                        "work."), "glue_failed")
            rows = list(ran.get("rows") or [])
        else:
            raise AssembleError("Not yet: BuildBot cannot use that kind of source here.",
                                "kind")
    except AssembleError as exc:
        out = {"ok": False, "rows": [], "count": 0, "reason": exc.line, "error": exc.line,
               "code": exc.code, "action": str(action or "")[:40]}
        fix = owner_fix(app_id, exc.fix) if exc.fix else None
        if fix:
            out["fix"] = fix
        return out
    rows = [{k: _media_link(v) for k, v in r.items()} if isinstance(r, dict) else r
            for r in rows[:MAX_ROWS]]
    return {"ok": True, "rows": rows, "count": len(rows), "action": action}


# ---------------------------------------------------------------------------
# settings (A1's store; a key is only ever "set" or not)
# ---------------------------------------------------------------------------

def _declared(spec: dict) -> dict:
    return {str(s.get("key")): s for s in spec.get("settings") or []
            if isinstance(s, dict) and _NAME_RE.match(str(s.get("key") or ""))}


def _stored(app_id: str) -> dict:
    data = fsutil.read_json(os.path.join(app_folder(app_id), "settings.json"), default={})
    return data if isinstance(data, dict) else {}


def settings_view(app_id: str) -> dict:
    """``{"values": {key: value}}``; a key setting is ``{"set": bool}``."""
    try:
        spec = load_spec(app_id)
    except AssembleError as exc:
        return {"error": exc.line}
    stored = _stored(app_id)
    spec_mod = _lane("puffpai.assemble.spec")
    values: dict = {}
    for key, st in _declared(spec).items():
        if st.get("kind") == "key":
            try:
                from puffpai.security import keys
                state = (keys.key_status(str(st.get("service") or "")) or {}).get("state")
            except Exception:  # noqa: BLE001 - unreadable: not set
                state = ""
            values[key] = {"set": state not in ("", None, "missing")}
        elif st.get("kind") == "place" and spec_mod is not None:
            place = spec_mod.resolve_place(app_id, st)
            if place:
                values[key] = place
        elif key in stored:
            values[key] = stored[key]
    return {"values": values}


def _place(value: Any) -> dict:
    if not isinstance(value, dict):
        raise AssembleError("A place is a town name, or a latitude and longitude.", "input")
    out: dict = {"name": " ".join(str(value.get("name") or "").split())[:80]}
    lat, lon = value.get("lat"), value.get("lon")
    if lat is not None or lon is not None:
        try:
            la, lo = float(lat), float(lon)
        except (TypeError, ValueError):
            raise AssembleError("Latitude and longitude are numbers.", "input") from None
        if not (-90 <= la <= 90 and -180 <= lo <= 180):
            raise AssembleError("Latitude goes from -90 to 90 and longitude from -180 "
                                "to 180.", "input")
        # A rough position only (about 11 km), as the assemble API keeps it.
        out.update(lat=round(la, 1), lon=round(lo, 1))
    if not out["name"] and "lat" not in out:
        raise AssembleError("Type a town, or both numbers.", "input")
    return out


def _save_home(place: dict) -> dict:
    from puffpai import home_location
    try:
        if "lat" in place:
            home = home_location.set_home_coords(place["lat"], place["lon"],
                                                 label=place.get("name") or "")
        else:
            allowed, why = home_location.geocode_allowed()
            if not allowed:
                return {"error": f"{why} Type its latitude and longitude too.",
                        "code": "lookup_off"}
            home, _others = home_location.set_home_town(place["name"])
    except home_location.HomeError as exc:
        return {"error": str(exc), "code": "input"}
    return {"ok": True, "value": {"lat": home.get("latitude"), "lon": home.get("longitude"),
                                  "name": str(home.get("label") or "")}}


def save_setting(app_id: str, key: Any, value: Any) -> dict:
    """Keep one of her settings. ``{"ok", "value"}`` or ``{"error", "fix"?}``.
    A key is never saved here: it lives in the vault."""
    try:
        spec = load_spec(app_id)
        st = _declared(spec).get(str(key or ""))
        if st is None:
            raise AssembleError("That is not one of this app's settings.", "unknown_setting")
        kind = st.get("kind")
        if kind == "key":
            raise AssembleError("A key is kept in your vault, never in the app. Put it "
                                "in your vault from this app's page on your dashboard.",
                                "key", make_fix("key", key=str(key),
                                                name=st.get("label") or st.get("service")))
        if kind == "place" and st.get("use") == "home_location":
            # The bot's one home town (the same store the chat's town card
            # and the setup use). A typed position is saved as is; a town
            # name is looked up only while that lookup is already switched
            # on (this page never switches anything on).
            return _save_home(_place(value))
        if kind == "place":
            clean: Any = _place(value)
        elif kind == "choice":
            choices = [str(c.get("value") if isinstance(c, dict) else c)
                       for c in st.get("choices") or []]
            clean = str(value or "")
            if clean not in choices:
                raise AssembleError("Pick one of the choices.", "input")
        else:
            clean = " ".join(str(value or "").split())[:200]
        spec_mod = _lane("puffpai.assemble.spec")
        if spec_mod is not None:
            try:
                spec_mod.save_setting(app_id, str(key), clean)
            except ValueError as exc:
                raise AssembleError("That setting could not be kept.", "input") from exc
        else:
            data = _stored(app_id)
            data[str(key)] = clean
            fsutil.atomic_write_json(os.path.join(app_folder(app_id), "settings.json"), data)
    except AssembleError as exc:
        out = {"error": exc.line, "code": exc.code}
        fix = owner_fix(app_id, exc.fix) if exc.fix else None
        if fix:
            out["fix"] = fix
        return out
    return {"ok": True, "value": clean}


# ---------------------------------------------------------------------------
# pictures and stream pieces
# ---------------------------------------------------------------------------

def _rewrite_playlist(text: str, base: str) -> str:
    def via(ref: str) -> str:
        return "/media?" + urllib.parse.urlencode({"url": urllib.parse.urljoin(base, ref)})
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            out.append(via(stripped))
        elif stripped.startswith("#") and 'URI="' in stripped:
            out.append(re.sub(r'URI="([^"]+)"', lambda m: f'URI="{via(m.group(1))}"', stripped))
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def _range(spec_text: str, size: int) -> Optional[tuple[int, int]]:
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", spec_text.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return None
    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    else:
        start, end = max(0, size - int(m.group(2))), size - 1
    if start >= size or end < start:
        return (-1, -1)
    return start, min(end, size - 1)


def _bridge_bytes(door: str, url: str) -> tuple[int, dict, bytes]:
    """One GET under *door* (the Bridge), at most :data:`MAX_MEDIA_BYTES`."""
    from puffpai.security import egress_doors
    get = _media_get or egress_doors.egress_get
    resp = get(door, url, timeout=MEDIA_TIMEOUT, stream=True)
    try:
        status = int(getattr(resp, "status_code", 0) or 0)
        headers = {str(k).lower(): str(v) for k, v in
                   dict(getattr(resp, "headers", {}) or {}).items()}
        chunks, total = [], 0
        it = getattr(resp, "iter_content", None)
        for chunk in (it(65536) if callable(it) else [getattr(resp, "content", b"") or b""]):
            total += len(chunk or b"")
            if total > MAX_MEDIA_BYTES:
                raise AssembleError("That piece was too big.", "too_big")
            chunks.append(bytes(chunk or b""))
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    return status, headers, b"".join(chunks)


# ── the picture addresses each source's own results named ────────────────────
#
# Her yes to a source covers "<source> and the picture sites its results
# point to" (build.setup). So each picture address a source's own answer
# names is kept here with that source's switch, per app, in memory; /media
# lets exactly those addresses through the Bridge's picture door
# (egress_doors.result_picture_get: pictures only, size-capped, one audit
# line per site). Anything else becomes an "also needs" row for the job.

_RESULT_PICS: dict[str, dict[str, str]] = {}
_RESULT_PICS_KEEP = 2000
_PIC_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp|avif)(?:$|[?#])", re.I)
_PIC_KEY = re.compile(r"image|photo|thumb|picture|img|poster|icon|logo", re.I)
_URL_IN_TEXT = re.compile(r"https://[^\s\"'<>\\]+")


def _picture_urls(payload: Any, key: str = "", out: Optional[list] = None,
                  budget: Optional[list] = None) -> list[str]:
    """Picture addresses in a source's answer: https strings that end like a
    picture, or sit under a picture-like key."""
    out = [] if out is None else out
    budget = [5000] if budget is None else budget
    if budget[0] <= 0 or len(out) >= 400:
        return out
    budget[0] -= 1
    if isinstance(payload, dict):
        for k, v in payload.items():
            _picture_urls(v, str(k), out, budget)
    elif isinstance(payload, list):
        for v in payload:
            _picture_urls(v, key, out, budget)
    elif isinstance(payload, str):
        text = payload.strip()
        if text.startswith("https://") and len(text) <= 2000 and " " not in text \
                and (_PIC_EXT.search(text) or _PIC_KEY.search(key)) and _host_of(text):
            out.append(text)
    return out


def note_result_pictures(app_id: str, door: str, payload: Any) -> int:
    """Keep the picture addresses *door*'s source answered with. Returns how
    many. Never raises."""
    if not door:
        return 0
    try:
        if isinstance(payload, (str, bytes)):
            text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
            try:
                payload = json.loads(text)
                urls = _picture_urls(payload)
            except ValueError:
                urls = [u.rstrip(".,;)") for u in _URL_IN_TEXT.findall(text[:2_000_000])
                        if _PIC_EXT.search(u)][:400]
        else:
            urls = _picture_urls(payload)
        if not urls:
            return 0
        with _MEDIA_LOCK:
            kept = _RESULT_PICS.setdefault(str(app_id), {})
            for u in urls:
                kept.pop(u, None)
                kept[u] = str(door)
            while len(kept) > _RESULT_PICS_KEEP:
                kept.pop(next(iter(kept)))
        return len(urls)
    except Exception:  # noqa: BLE001 - nothing kept: those pictures ask first
        return 0


def _result_picture_door(app_id: str, url: str) -> str:
    with _MEDIA_LOCK:
        return (_RESULT_PICS.get(str(app_id)) or {}).get(str(url), "")


def _picture_from_results(app_id: str, url: str, host: str) -> tuple[int, dict, bytes]:
    """A picture a source's own results named, through the picture door; or
    a refusal that puts the site on an "also needs" row for the job."""
    from puffpai.security import egress_doors
    door = _result_picture_door(app_id, url)
    confirmed: dict = {}
    try:
        from puffpai.build import yes as yes_mod
        confirmed = yes_mod.confirmed_picture_hosts(app_id)
    except Exception:  # noqa: BLE001
        pass
    if not door and confirmed.get(host):
        door = confirmed[host]          # she switched that site on for this source
    if not door:
        # An address a page named, not one the source's own answer named: it
        # is refused, and a page can never put a site on her list.
        return 403, {"Content-Type": "text/plain"}, b"not a site this app's sources named"
    try:
        status, headers, body = (_picture_get or egress_doors.result_picture_get)(
            door, url, {host} | {h for h, d in confirmed.items() if d == door},
            timeout=MEDIA_TIMEOUT)
    except egress_doors.EgressBlocked as exc:
        if getattr(exc, "code", "") == "not_a_picture":
            # The source's OWN result pointed there and it is not a picture:
            # a full-site need, for that job's "also needs" row.
            _runtime_need(app_id, host, door)
        return 502, {"Content-Type": "text/plain"}, str(
            getattr(exc, "user_message", "") or "The Bridge is closed for it.").encode()
    except Exception as exc:  # noqa: BLE001
        return 502, {"Content-Type": "text/plain"}, \
            f"not fetched ({type(exc).__name__})".encode()
    return status, {"Content-Type": headers.get("content-type", "application/octet-stream"),
                    "Cache-Control": "no-store"}, body


def result_hosts(app_id: str, door: str) -> set[str]:
    """Sites the source behind *door* named in its own answers for this app."""
    with _MEDIA_LOCK:
        kept = dict(_RESULT_PICS.get(str(app_id)) or {})
    return {_host_of(u) for u, d in kept.items() if d == str(door) and _host_of(u)}


def _runtime_need(app_id: str, host: str, door: str) -> None:
    try:
        from puffpai.build import yes as yes_mod
        yes_mod.note_runtime_need(app_id, host, door)
    except Exception as exc:  # noqa: BLE001 - the refusal itself stands
        logger.info("assemble media: also-needs row not kept (%s)", type(exc).__name__)


#: Tests replace the Bridge's picture GET here.
_picture_get: Optional[Callable[..., tuple[int, dict, bytes]]] = None


def media(app_id: str, url: str, range_header: str = "") -> tuple[int, dict, bytes]:
    """A picture or stream piece from a site a declared source names, under
    that source's switch; or a picture the source's own results named, under
    the same switch (the picture door): ``(status, headers, body)``."""
    try:
        spec = load_spec(app_id)
    except AssembleError:
        return 404, {"Content-Type": "text/plain"}, b"unknown app"
    host = _host_of(url)
    site = next((s for s in _sites(app_id, spec) if s["host"] == host and s["source"]
                 and s["door"]), None)
    if site is None:
        if not host:
            return 403, {"Content-Type": "text/plain"}, b"not a site this app names"
        return _picture_from_results(app_id, url, host)
    key = f"{app_id}|{url}"
    now = time.monotonic()
    with _MEDIA_LOCK:
        hit = _MEDIA_CACHE.get(key)
    if hit and now - hit[0] < _MEDIA_SECONDS:
        _t, status, headers, body = hit
    else:
        from puffpai.security import egress_doors
        try:
            status, headers, body = _bridge_bytes(site["door"], url)
        except egress_doors.EgressBlocked as exc:
            return 502, {"Content-Type": "text/plain"}, str(
                getattr(exc, "user_message", "") or "The Bridge is closed for it.").encode()
        except AssembleError as exc:
            return 502, {"Content-Type": "text/plain"}, exc.line.encode()
        except Exception as exc:  # noqa: BLE001 - a site that fails is "not fetched"
            return 502, {"Content-Type": "text/plain"}, \
                f"not fetched ({type(exc).__name__})".encode()
        ctype = headers.get("content-type", "")
        if "mpegurl" in ctype.lower() or url.split("?", 1)[0].endswith(".m3u8"):
            body = _rewrite_playlist(body.decode("utf-8", "replace"), url).encode()
            headers["content-type"] = "application/vnd.apple.mpegurl"
        with _MEDIA_LOCK:
            _MEDIA_CACHE[key] = (now, status, headers, body)
            for old in sorted(_MEDIA_CACHE, key=lambda k: _MEDIA_CACHE[k][0])[:-_MEDIA_KEEP]:
                _MEDIA_CACHE.pop(old, None)
    out = {"Content-Type": headers.get("content-type", "application/octet-stream"),
           "Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    if status == 200 and range_header:
        rng = _range(range_header, len(body))
        if rng == (-1, -1):
            out["Content-Range"] = f"bytes */{len(body)}"
            return 416, out, b""
        if rng:
            start, end = rng
            out["Content-Range"] = f"bytes {start}-{end}/{len(body)}"
            return 206, out, body[start:end + 1]
    return status, out, body


__all__ = ["AssembleError", "SPEC_SCHEMA", "app_folder", "assemble_root", "backend_fetch",
           "call_action", "check_app_id", "checked_files", "fetch_source", "glue_dir",
           "glue_files", "glue_sha", "host_id", "install", "load_spec", "make_fix",
           "make_plan", "media", "note_result_pictures", "prepare_start", "result_hosts", "read_server_token", "record_checked",
           "record_parts", "save_setting", "server_token", "settings_view", "spec_path",
           "spec_sites", "start_assembled", "unchecked_glue"]
