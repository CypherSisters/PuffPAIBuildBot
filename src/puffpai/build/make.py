"""Make: build one job after the owner's one Yes (the one build foundation).

``make(job, build) -> {"ok", "page", "reason", "screen", ...}`` is the only
step that installs, connects or puts anything together, and only for a job
the build's recorded Yes covers (:func:`yes_covers`). One Yes covers the whole
list: there are no separate Start cards. By route:

* **A** an app as it is: the checked project is added exactly as its card
  showed (``integrate.apply.finish_prepared``: same files, same Drawbridge
  rows, same run plan) and, when it is a server, started by
  ``integrate.run_here`` (its own Job Object, loopback only). ``screen`` is
  its own screen.
* **B** tools: an MCP server is connected (``mcp_client.add_server``, after
  the Yes), a CLI skill gets the programs it declares (``deps.make_plan`` /
  ``deps.run_plan``, each package checked by Drawbridge) and is verified, a
  remote skill with an MCP address is connected like any MCP server, a
  reminder becomes a routine, a book or text skill is read as it is.
  ``screen`` is the shared view of tool outputs.
* **C** a view put together from data sources: ``assemble.api.make_job_app``
  (Assemble and Toolsmith), the view being ``VIEW[output]``.
* **D** nothing to make: "Nothing found yet".

``page`` is always the job's own page id (``job-<id>``); ``screen`` says
what its main area shows.

Also here, because they are about what a made thing may reach:

* :func:`setup_rows_for` (called by the build's setup step): the setup rows
  one candidate needs before the Yes: outside addresses from a web app's scan
  (including "maybe" hosts and addresses built while running), an MCP host or
  command, a remote skill's host or account, a CLI skill's programs and keys.
* :func:`note_runtime_refusal` (called by ``egress_doors`` when a browser app
  reaches a site it has no place for): ONE "Allow <app> to reach <host>" row
  per app, asked once, in ``<state>/build/outside_asks.json``.
  :func:`answer_runtime_row` records her answer; a yes puts the host on the
  app's own place on the Bridge, where it still starts off until she switches
  it on (the per-site consent ticket path is unchanged).

Rules kept: outbound only through ``egress_doors``; keys only from the vault
(nothing here reads a key value); host code in its own Job Object on
loopback; no vault keys in glue. Goal-agnostic: nothing names a subject.

``build/schema.py`` owns the enums and the Job record. When it cannot be
loaded, :func:`schema` falls back to a local copy.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from typing import Any, Callable, Optional

from puffpai import fsutil
from puffpai.logging_config import get_logger

logger = get_logger(__name__)


# ── the shared words (build/schema.py) ──────────────────────────────────────

class _FallbackSchema:
    """A local copy: ``puffpai.build.schema`` owns these."""
    CLASSES = ("APP", "TOOL", "DATA", "TEXT", "GUIDE")
    ROUTES = ("A", "B", "C", "D")
    STATUSES = ("planned", "needs_setup", "waiting_yes", "building", "working", "not_yet",
                "nothing_found")
    VIEW = {"pictures": "gallery", "numbers_over_time": "chart", "places": "map",
            "video": "player", "sound": "player", "text": "list", "action": "form"}


def schema() -> Any:
    """The build's schema module when it is in this build, else the fallback."""
    try:
        from puffpai.build import schema as mod  # type: ignore[attr-defined]
        if all(hasattr(mod, n) for n in ("CLASSES", "ROUTES", "STATUSES", "VIEW")):
            return mod
    except Exception:  # noqa: BLE001 - not merged yet
        pass
    return _FallbackSchema


def view_for(job: dict) -> str:
    """The job's view (the build's decide step sets it), else ``VIEW[output]``
    (``list`` when its output is unknown)."""
    views = schema().VIEW
    made_views = set(getattr(schema(), "MADE_VIEWS", ())) | set(views.values())
    if str(job.get("view") or "") in made_views:
        return str(job["view"])
    return str(views.get(str(job.get("output") or ""), "list"))


# ── words ───────────────────────────────────────────────────────────────────

WAITING = "waiting for your yes"
NOTHING_FOUND = "Nothing found yet"
NO_ID = "this job has no id"
SHARED = "shared"
OWN = "app"
VIEW_SCREEN = "view"
#: How long make waits for a started app to answer on its port.
READY_SECONDS = 30.0


def page_of(job: dict) -> str:
    return str(job.get("page") or f"job-{job.get('id') or ''}")


def _out(job: dict, ok: bool, reason: str = "", **extra: Any) -> dict:
    return {"ok": bool(ok), "page": page_of(job), "reason": str(reason or ""), **extra}


def _name(cand: dict) -> str:
    return str(cand.get("name") or cand.get("label") or cand.get("ref") or "it")[:80]


# ── the one Yes ─────────────────────────────────────────────────────────────

def yes_covers(job: dict, build: dict) -> bool:
    """Does the build's recorded Yes cover *job*? Fails closed.

    ``build.yes.covers(build)`` decides whether her Yes is for
    exactly this list (its sha); the job must be on that list. When that
    module is not in this build, or it fails, nothing is covered."""
    jid = str((job or {}).get("id") or "")
    if not jid or not isinstance(build, dict):
        return False
    if jid not in {str(j.get("id")) for j in build.get("jobs") or [] if isinstance(j, dict)}:
        return False
    try:
        import importlib
        fn = getattr(importlib.import_module("puffpai.build.yes"), "covers", None)
        return bool(fn(build)) if callable(fn) else False
    except Exception:  # noqa: BLE001 - no yes module, or it broke: not covered
        return False


#: The guard's plain words, by what make would have done.
_NOT_LISTED = {"app": "added", "mcp": "connected", "install": "installed",
               "sdk": "installed (the mcp package tool servers need)"}


def not_listed(build: Any, cand: dict, action: str, target: Any = None) -> str:
    """"" when the setup list her Yes covers shows a row for doing *action*
    (``setup.MAKES``) with *cand*, else the plain reason make refuses with.
    Fails closed: no build, no list, no row is a refusal (without it, tools
    get built that have no row). A tool server's row must name the exact
    address or command make would connect."""
    from puffpai.build import setup as setup_mod
    ref = str((cand or {}).get("ref") or "")
    verb = _NOT_LISTED.get(action, "made")
    why = f"it was not on your setup list, so it was not {verb}"
    if (not ref and action != setup_mod.MAKE_SDK) or not isinstance(build, dict) \
            or action not in setup_mod.MAKES:
        return why
    open_ids = {str(j.get("id")) for j in build.get("jobs") or []
                if isinstance(j, dict) and j.get("route") != "D"}
    rows = [r for r in build.get("setup") or [] if isinstance(r, dict)
            and (action == setup_mod.MAKE_SDK or str(r.get("ref") or "") == ref
                 or ref in [str(x) for x in r.get("refs") or []])
            and r.get("make") == action
            and set(str(i) for i in r.get("job_ids") or []) & open_ids]
    if action == setup_mod.MAKE_MCP and target:
        shown = " ".join(str(x) for x in target) if isinstance(target, list) else str(target)
        rows = [r for r in rows
                if shown in (str(r.get("mcp_url") or ""), str(r.get("command") or ""))]
    return "" if rows else why


def _ensure_sdk(build: Any, cand: dict, h: dict) -> str:
    """"" when the mcp package tool servers need is here, installing it now
    when the list showed its row (her Yes covers it); else why not."""
    try:
        from puffpai import mcp_client
        if mcp_client.sdk_available():
            return ""
    except Exception:  # noqa: BLE001 - cannot tell: installed below, if listed
        pass
    why = not_listed(build, cand, "sdk")
    if why:
        return why
    install = h.get("install_sdk")
    if install is None:
        from puffpai import deps
        y = build.get("yes") if isinstance(build, dict) and isinstance(build.get("yes"), dict) \
            else {}
        install = lambda: deps.install_mcp_sdk(device=str(y.get("device") or ""))  # noqa: E731
    try:
        got = install() or {}
    except Exception as exc:  # noqa: BLE001
        return f"the mcp package was not installed ({type(exc).__name__})"
    return "" if got.get("ok") else str(got.get("line") or "the mcp package was not installed")


def steps_not_listed(build: Any, cand: dict, steps: list) -> str:
    """"" when every step of an install plan for *cand* is a row the list
    showed for it (same name), else the plain reason make refuses with."""
    ref = str((cand or {}).get("ref") or "")
    names = {str(r.get("name") or "") for r in (build or {}).get("setup") or []
             if isinstance(r, dict) and str(r.get("ref") or "") == ref and r.get("name")} \
        if isinstance(build, dict) else set()
    for step in steps or []:
        name = str((step or {}).get("name") or "") if isinstance(step, dict) else ""
        if not name or name not in names:
            return (f"it needs {name or 'something'}, which was not on your setup list, so "
                    f"nothing was installed")
    return ""


def _yes_by(build: dict) -> tuple[str, str]:
    """``(who, yes ref)`` of the build's Yes, for the audit lines."""
    rec = build.get("yes") if isinstance(build, dict) and isinstance(build.get("yes"), dict) \
        else {}
    return str(rec.get("by") or "owner")[:40],         str(rec.get("sha") or rec.get("id") or rec.get("at") or "")[:80]


# ── the job's pieces ────────────────────────────────────────────────────────

def _pool(build: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for key in ("parts", "candidates", "finds"):
        src = build.get(key) if isinstance(build, dict) else None
        if isinstance(src, dict):
            items = [{**v, "ref": v.get("ref") or k} for k, v in src.items()
                     if isinstance(v, dict)]
        elif isinstance(src, list):
            items = [c for c in src if isinstance(c, dict)]
        else:
            items = []
        for c in items:
            ref = str(c.get("ref") or "")
            if ref:
                index.setdefault(ref, c)
    return index


def candidates_of(job: dict, build: dict) -> list[dict]:
    """The job's pick first, then its ingredients, as candidate records.

    A candidate is looked up by ref in ``build["parts"]`` (the
    build.json), ``build["candidates"]`` or ``build["finds"]`` (a dict by ref
    or a list), or taken from
    ``job["candidates"]``. The pick's class wins when the record has none."""
    index = _pool(build)
    for c in job.get("candidates") or []:
        if isinstance(c, dict) and c.get("ref"):
            index.setdefault(str(c["ref"]), c)
    pick = job.get("pick") if isinstance(job.get("pick"), dict) else {}
    refs = [str(pick.get("ref") or "")] + [str(r.get("ref") if isinstance(r, dict) else r)
                                           for r in job.get("ingredients") or []]
    out = []
    for ref in dict.fromkeys(r for r in refs if r):
        c = index.get(ref)
        if c is None or c.get("unusable"):
            continue          # not there, or it gave nothing to show
        c = dict(c)
        if ref == pick.get("ref") and not c.get("class") and pick.get("class"):
            c["class"] = pick["class"]
        out.append(c)
    return out


def class_of(cand: dict) -> str:
    """The candidate's class (the build's classify step writes it); a skill without
    one is read here (:func:`puffpai.skills.how_it_runs`)."""
    cls = str(cand.get("class") or "").upper()
    if cls in schema().CLASSES:
        return cls
    if _kind(cand) == "skill":
        try:
            from puffpai import skills
            return str(skills.how_it_runs(_skill_name(cand))["class"])
        except Exception:  # noqa: BLE001
            return "GUIDE"
    return ""


def _kind(cand: dict) -> str:
    """The candidate's ``type`` (``kind`` in older records)."""
    return str(cand.get("type") or cand.get("kind") or "").lower()


def _skill_name(cand: dict) -> str:
    return str(cand.get("skill") or cand.get("folder") or cand.get("name") or "")


def _mcp_target(cand: dict) -> Any:
    facts = cand.get("facts") if isinstance(cand.get("facts"), dict) else {}
    if not cand.get("mcp") and isinstance(facts.get("mcp_url"), str) and facts["mcp_url"]:
        return facts["mcp_url"]
    mcp = cand.get("mcp")
    if isinstance(mcp, dict):
        command = mcp.get("command")
        return mcp.get("url") or (command if command != "stdio" else "") or ""
    if isinstance(mcp, (str, list)):
        return mcp
    return ""


def _row_id(*parts: Any) -> str:
    return hashlib.sha256("\n".join(str(p) for p in parts).encode()).hexdigest()[:16]


# ── setup rows (the build's setup step calls this) ──────────────────────────

_ACCOUNT_RE = re.compile(r"(?i)\b(?:account|sign[ -]?up|api[ -]?key|wallet|subscription|"
                         r"paid|credits?)\b")


#: Our row kinds -> the build's ``SETUP_KINDS`` (the one setup list's kinds).
ROW_KIND = {"outside_address": "address", "runtime_ask": "address", "mcp_host": "address",
            "mcp_command": "program", "program": "program", "account": "key", "key": "key"}


def _with_id(row: dict) -> dict:
    """One row in the setup list's shape: ``{"id", "kind", "label", "detail",
    "job_ids", "done"}`` plus ``what`` (the finer kind), ``words`` and what
    completing it needs (``host``, ``command``, ``name``, ``app``)."""
    what = str(row.get("what") or row.get("kind") or "")
    row["what"] = what
    row["kind"] = ROW_KIND.get(what, what)
    row.setdefault("id", f"{row['kind']}-" + _row_id(what, row.get("app"), row.get("host"),
                                                    row.get("name"), row.get("command")))
    row.setdefault("label", str(row.get("words") or ""))
    row.setdefault("detail", _detail(row))
    row.setdefault("job_ids", [])
    row.setdefault("done", False)
    return row


def _detail(row: dict) -> str:
    what = row.get("what")
    if what == "mcp_command":
        return f"It starts with: {row.get('command')}"
    if what == "runtime_ask":
        return "It asks for this while it runs."
    if what in ("outside_address", "mcp_host") and row.get("host"):
        return ("It asks for this while it runs." if row.get("sure") is False
                else f"It reaches {row['host']}.")
    if what in ("key", "account"):
        return "It goes into your vault."
    return ""


def setup_rows_for(candidate: Any) -> list[dict]:
    """The setup rows *candidate* needs before the build's one Yes. Never
    raises; reads files and records only, opens nothing.

    Each row: ``{"id", "kind", "app", "words", ...}`` with kind one of
    ``outside_address`` / ``runtime_ask`` (a web app's sites, from its scan:
    sure ones and "maybe" ones whose address is built while it runs),
    ``mcp_host`` / ``mcp_command`` (a tool server), ``account`` (a remote
    service that needs her account), ``program`` (a CLI skill's missing
    program), ``key`` (a key it needs, kept in the vault)."""
    if not isinstance(candidate, dict):
        return []
    rows: list[dict] = []
    name = _name(candidate)
    try:
        rows += _scan_rows(candidate, name)
        target = _mcp_target(candidate)
        if target:
            from puffpai import mcp_client
            rows.append(mcp_client.setup_row(_mcp_name(candidate), target, label=name))
        if _kind(candidate) == "skill":
            rows += _skill_rows(candidate, name)
        elif candidate.get("url") and class_of(candidate) == "DATA":
            host = _https_host(candidate.get("url"))
            if host:
                rows.append({"kind": "outside_address", "app": name, "host": host,
                             "words": f"Allow {name} to reach {host}", "learned": "find",
                             "sure": True})
        for key in candidate.get("keys") or []:
            if isinstance(key, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", key):
                rows.append({"kind": "key", "app": name, "name": key,
                             "words": f"Your key {key} for {name}, kept in your vault"})
    except Exception as exc:  # noqa: BLE001 - a row we cannot make is no row
        logger.info("build make: setup rows for %s not read (%s)", name, type(exc).__name__)
    seen: set[str] = set()
    out = []
    for row in rows:
        row = _with_id({k: v for k, v in dict(row).items() if k != "target"})
        if row["id"] not in seen:
            seen.add(row["id"])
            out.append(row)
    return out


def _scan_rows(candidate: dict, name: str) -> list[dict]:
    from puffpai.webapps import scan as scan_mod
    rows = [{"kind": "outside_address", "app": name, "host": str(h).lower(),
             "words": scan_mod.ALLOW_WORDS.format(app=name, host=str(h).lower()),
             "learned": "scan_maybe", "sure": False}
            for h in candidate.get("maybe_hosts") or [] if isinstance(h, str) and "." in h]
    return rows + _scan_result_rows(candidate, name)


def _scan_result_rows(candidate: dict, name: str) -> list[dict]:
    from puffpai.webapps import scan as scan_mod
    result = candidate.get("scan") if isinstance(candidate.get("scan"), dict) else None
    if result is None or "hosts" not in result:
        root = _files_root(candidate)
        result = scan_mod.scan(root) if root else None
    if not isinstance(result, dict):
        return []
    if "maybe_runtime_hosts" not in result:
        result = {**result, "maybe_runtime_hosts": scan_mod.maybe_runtime_hosts(
            result.get("hosts") or [], result.get("network_calls_with_runtime_address") or {})}
    return scan_mod.outside_rows(result, name)


def _files_root(candidate: dict) -> str:
    """The unpacked files of a checked web project (a static page), or ""."""
    prepared = candidate.get("prepared") if isinstance(candidate.get("prepared"), dict) else {}
    iid = str(candidate.get("integration_id") or prepared.get("integration_id") or "")
    mode = str(candidate.get("mode") or prepared.get("mode") or "")
    if not iid or mode not in ("static_web", "web_build"):
        return ""
    try:
        from puffpai.integrate import records
        rec = records.get(iid) if records.valid_id(iid) else None
    except Exception:  # noqa: BLE001
        return ""
    root = str((rec or {}).get("files_root") or "")
    return root if root and os.path.isdir(root) else ""


def _skill_rows(candidate: dict, name: str) -> list[dict]:
    from puffpai import skills
    from puffpai.build import classify
    rows: list[dict] = []
    kr = classify.key_request(candidate)
    if kr:
        # One web request to its own service: a data source, never a command
        # line (no "Install curl" row). Its sites, and its key in the vault.
        for host in kr["hosts"]:
            rows.append({"kind": "outside_address", "app": name, "host": host,
                         "words": f"Allow {name} to reach {host}", "learned": "skill",
                         "sure": True})
        for key in kr["keys"]:
            rows.append({"kind": "key", "app": name, "name": key,
                         "words": f"Your key {key} for {name}, kept in your vault"})
        return rows
    how = _skill_how(candidate)
    meta = _skill_meta(candidate)
    if how["kind"] == "remote":
        if how["endpoint_kind"] == "mcp" and how["endpoint"] and not _mcp_target(candidate):
            from puffpai import mcp_client
            rows.append(mcp_client.setup_row(_mcp_name(candidate), how["endpoint"], label=name))
        elif how["endpoint_kind"] != "mcp":
            for host in how["hosts"]:
                rows.append({"kind": "outside_address", "app": name, "host": host,
                             "words": f"Allow {name} to reach {host}", "learned": "skill",
                             "sure": True})
        said = " ".join(str(candidate.get(k) or "") for k in ("about", "does", "description"))
        said += " " + " ".join(str(meta.get(k) or "") for k in ("description", "compatibility"))
        if _ACCOUNT_RE.search(said):
            host = how["hosts"][0] if how["hosts"] else ""
            rows.append({"kind": "account", "app": name, "host": host,
                         "words": f"{name} needs your account"
                                  + (f" at {host}" if host else "")
                                  + "; its key goes into your vault"})
    if how["kind"] == "cli":
        req = skills.requirements(meta)
        for prog in skills.missing_programs(req):
            rows.append({"kind": "program", "app": name, "name": prog,
                         "words": f"Install {prog} for {name}"})
        for key in req.get("env") or []:
            rows.append({"kind": "key", "app": name, "name": key,
                         "words": f"Your key {key} for {name}, kept in your vault"})
    return rows


def _skill_meta(candidate: dict) -> dict:
    from puffpai import skills
    if isinstance(candidate.get("frontmatter"), dict):
        return candidate["frontmatter"]
    rec = skills.find(_skill_name(candidate))
    return (rec or {}).get("frontmatter") or {}


def _skill_how(candidate: dict) -> dict:
    from puffpai import skills
    text = candidate.get("skill_md") if isinstance(candidate.get("skill_md"), str)         else candidate.get("text")
    if isinstance(text, str) and text.startswith("---"):
        return skills.how_it_runs_text(text, "")
    return skills.how_it_runs(_skill_name(candidate))


def _mcp_name(candidate: dict) -> str:
    from puffpai import mcp_client
    return mcp_client.slug(candidate.get("server") or _name(candidate)) or "tool"


def _https_host(url: Any) -> str:
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(str(url or ""))
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme != "https" or (port not in (None, 443)):
        return ""
    return (parts.hostname or "").rstrip(".").lower()


# ── an app reaching a site it has no place for ──────────────────────────────

ASKS_SCHEMA = "build_outside_asks/v1"
_ASKS_LOCK = threading.Lock()
_SITES_LOCK = threading.Lock()
#: (app, host) already handed in this run: a busy app's refusals cost no disk.
_NOTED: set[tuple[str, str]] = set()
_APP_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\Z")
ASK_HOSTS_MAX = 5


def asks_path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "build", "outside_asks.json")


def _read_asks() -> dict:
    data = fsutil.read_json(asks_path(), default=None)
    if isinstance(data, dict) and data.get("schema") == ASKS_SCHEMA \
            and isinstance(data.get("apps"), dict):
        return data
    return {"schema": ASKS_SCHEMA, "apps": {}}


def _app_name(app_id: str) -> str:
    try:
        from puffpai.webapps import host as webhost
        rec = webhost.read_record(app_id) or {}
        name = str(rec.get("name") or "").strip()
        if name:
            return name[:80]
    except Exception:  # noqa: BLE001
        pass
    return app_id


def _restore_door(app_id: str, host: str) -> bool:
    """After a restart a browser app's place on the Bridge is gone (it is
    registered only when the app is added). Put it back from the app's own
    install record (its scan's sites, plus sites she said yes to here), all
    still off until her recorded yes. True when *host* is on it now."""
    try:
        from puffpai.security import egress_doors as ed
        if ed.web_app_door(app_id) is not None:
            return False
        hosts = _hosts_to_restore(app_id)
        if not hosts:
            return False
        ed.register_web_app_door(app_id, hosts)
        return host in hosts
    except Exception:  # noqa: BLE001 - nothing restored is the fail-shut outcome
        return False


def note_runtime_refusal(app_id: Any, host: Any, code: Any = "") -> Optional[dict]:
    """A browser app was refused *host* because it has no place for it
    (``unknown_app``, ``not_on_list``). Record ONE "Allow <app> to reach
    <host>" row per app, asked once: while the row waits, a new host joins
    it (up to :data:`ASK_HOSTS_MAX`); once she answered, it is never asked
    again. Returns the row, or None. Never raises, never opens anything."""
    try:
        app_id = str(app_id or "")
        host = str(host or "").strip().rstrip(".").lower()
        if not _APP_ID_RE.match(app_id) or not host or "." not in host or len(host) > 253:
            return None
        if (app_id, host) in _NOTED:
            return None
        if str(code) == "unknown_app" and _restore_door(app_id, host):
            _NOTED.add((app_id, host))
            return None
        with _ASKS_LOCK:
            data = _read_asks()
            row = data["apps"].get(app_id)
            if not isinstance(row, dict):
                name = _app_name(app_id)
                row = {"kind": "address", "what": "outside_address", "app_id": app_id,
                       "app": name,
                       "hosts": [], "learned": "runtime", "asked_at": int(time.time()),
                       "answer": None}
            if row.get("answer") is not None:
                later = [h for h in row.get("later") or [] if isinstance(h, str)]
                if host not in later and len(later) < ASK_HOSTS_MAX:
                    row["later"] = later + [host]
            elif host not in row["hosts"] and len(row["hosts"]) < ASK_HOSTS_MAX:
                row["hosts"] = list(row["hosts"]) + [host]
            row["words"] = row["label"] = _ask_words(row)
            row["id"] = "address-" + _row_id("runtime", app_id)
            row.setdefault("detail", "It asked for this while it ran.")
            row.setdefault("job_ids", [])
            row["done"] = False
            data["apps"][app_id] = row
            os.makedirs(os.path.dirname(asks_path()), exist_ok=True)
            fsutil.atomic_write_json(asks_path(), data)
            _NOTED.add((app_id, host))
            return dict(row)
    except Exception as exc:  # noqa: BLE001 - the refusal itself already stands
        logger.debug("build make: refusal not recorded (%s)", type(exc).__name__)
        return None


def _ask_words(row: dict) -> str:
    hosts = [h for h in row.get("hosts") or [] if isinstance(h, str)]
    shown = hosts[0] if len(hosts) == 1 else (", ".join(hosts[:-1]) + " and " + hosts[-1]
                                              if hosts else "a site")
    return f"Allow {row.get('app') or row.get('app_id')} to reach {shown}"


def runtime_rows(*, waiting_only: bool = True) -> list[dict]:
    """The runtime rows, one per app (only the unanswered ones by default)."""
    rows = []
    for app_id, row in sorted(_read_asks()["apps"].items()):
        if not isinstance(row, dict):
            continue
        if waiting_only and row.get("answer") is not None:
            continue
        rows.append({**row, "app_id": app_id})
    return rows


def answer_runtime_row(app_id: Any, yes: bool, *, by: str = "owner") -> dict:
    """Her answer to an app's runtime row. A No is final (never asked
    again). A Yes puts the row's hosts on the app's own place on the Bridge
    (at most two sites per app, the Bridge's bound); each site still starts
    OFF and opens only with her per-site switch (an owner consent ticket),
    so ``switch_on`` lists what she still has to switch on.

    ``{"ok", "message", "switch_on": [...]}``. Called from an owner-only
    route."""
    app_id = str(app_id or "")
    with _ASKS_LOCK:
        data = _read_asks()
        row = data["apps"].get(app_id)
        if not isinstance(row, dict):
            return {"ok": False, "message": "That app has nothing waiting.", "switch_on": []}
        if row.get("answer") is not None:
            return {"ok": False, "message": "You already answered that.", "switch_on": []}
        hosts = [h for h in row.get("hosts") or [] if isinstance(h, str)]
        if yes:
            got = _put_on_door(app_id, hosts)
            if not got["ok"]:
                return got
        row["answer"] = "yes" if yes else "no"
        row["answered_at"] = int(time.time())
        row["by"] = str(by or "owner")[:40]
        data["apps"][app_id] = row
        fsutil.atomic_write_json(asks_path(), data)
    _audit("build_outside_ask", f"{app_id}: {row['answer']} ({', '.join(hosts)})")
    if not yes:
        return {"ok": True, "message": "It stays off. You will not be asked again.",
                "switch_on": []}
    return {"ok": True, "switch_on": got["switch_on"],
            "message": "Switch each site on below to let it through."}


def _put_on_door(app_id: str, hosts: list[str]) -> dict:
    from puffpai.security import egress_doors as ed
    door = ed.web_app_door(app_id)
    have = list(door.hosts) if door is not None else []
    want = list(dict.fromkeys(have + hosts))
    if len(want) > ed.MAX_APP_DOOR_HOSTS:
        return {"ok": False, "switch_on": [],
                "message": f"An app may reach at most {ed.MAX_APP_DOOR_HOSTS} outside "
                           f"sites, so this one cannot be added."}
    try:
        if want != have:
            ed.register_web_app_door(app_id, want)
            keep_sites(app_id, want)
    except ValueError:
        return {"ok": False, "switch_on": [],
                "message": "That site cannot be added for this app."}
    on = ed.app_web_hosts(app_id)
    return {"ok": True, "switch_on": [h for h in want if not on.get(h)], "message": ""}


# ── an app's places on the Bridge, again after a restart ────────────────────
#
# The Bridge keeps a browser app's place (its site set) in memory; only her
# per-site yeses are on disk, keyed by the WHOLE site set. So the set an app
# had is kept here whenever the build gives it one, and every added app gets
# the same set back at start (``restore_web_sites``, called by the server),
# the same way a refusal restores one (``_restore_door``). A site comes back
# on only if her recorded yes for that exact set is still on disk.

def sites_path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "build", "app_sites.json")


def keep_sites(app_id: str, hosts: list[str]) -> None:
    """Remember *app_id*'s site set (the one its yeses are recorded for)."""
    if not _APP_ID_RE.match(str(app_id or "")):
        return
    with _SITES_LOCK:      # its own lock: answer_runtime_row holds _ASKS_LOCK here
        data = fsutil.read_json(sites_path(), default=None)
        data = data if isinstance(data, dict) and isinstance(data.get("apps"), dict) \
            else {"schema": "build_app_sites/v1", "apps": {}}
        data["apps"][app_id] = [str(h) for h in hosts][:4]
        os.makedirs(os.path.dirname(sites_path()), exist_ok=True)
        fsutil.atomic_write_json(sites_path(), data)


def kept_sites(app_id: str) -> list[str]:
    data = fsutil.read_json(sites_path(), default=None)
    got = (data.get("apps") or {}).get(app_id) if isinstance(data, dict) else None
    return [h for h in got if isinstance(h, str)] if isinstance(got, list) else []


def _hosts_to_restore(app_id: str) -> list[str]:
    """The site set *app_id* had: the one the build kept, else the one its
    install gave it (its scan's sites needed while running, in the install's
    own order), plus sites she said yes to on a runtime ask."""
    from puffpai.security import egress_doors as ed
    kept = kept_sites(app_id)
    if kept:
        hosts = kept
    else:
        from puffpai.webapps import host as webhost
        rec = webhost.read_record(app_id) or {}
        scan = rec.get("scan") if isinstance(rec.get("scan"), dict) else {}
        try:
            from puffpai.webapps import install as web_install
            order = getattr(web_install, "_DOOR_ORDER", {})
        except Exception:  # noqa: BLE001
            order = {}
        wanted = [h for h in scan.get("hosts") or []
                  if isinstance(h, dict) and h.get("needed_while_running") and h.get("host")]
        wanted.sort(key=lambda h: (order.get(str(h.get("for") or ""), 3), str(h.get("host"))))
        hosts = [str(h["host"]) for h in wanted][:ed.MAX_APP_DOOR_HOSTS]
        row = _read_asks()["apps"].get(app_id) or {}
        if row.get("answer") == "yes":
            hosts += [h for h in row.get("hosts") or [] if isinstance(h, str)]
    hosts = list(dict.fromkeys(h for h in hosts if ed.is_registrable_hostname(h)))
    return hosts[:ed.MAX_APP_DOOR_HOSTS]


def restore_web_sites() -> list[str]:
    """At start: give every added browser app back its place on the Bridge
    (all sites as off as her recorded yeses say). Returns the app ids given
    one. Never raises; opens nothing."""
    out: list[str] = []
    try:
        from puffpai.security import egress_doors as ed
        from puffpai.webapps import host as webhost
        ids = webhost.installed_ids()
    except Exception as exc:  # noqa: BLE001 - no browser apps here
        logger.info("build make: sites not restored (%s)", type(exc).__name__)
        return out
    for app_id in ids:
        try:
            if ed.web_app_door(app_id) is not None:
                continue
            hosts = _hosts_to_restore(app_id)
            if hosts:
                ed.register_web_app_door(app_id, hosts)
                out.append(app_id)
        except Exception as exc:  # noqa: BLE001 - one app never stops the rest
            logger.info("build make: %s not restored (%s)", app_id, type(exc).__name__)
    return out


def _audit(action: str, details: str) -> None:
    try:
        from puffpai.security.audit_log import AuditLog
        AuditLog().log(action=action, user="owner", result="recorded", details=details[:300])
    except Exception:  # noqa: BLE001 - an audit line never stops the step
        pass


def _reset_for_tests() -> None:
    _NOTED.clear()


# ── make ────────────────────────────────────────────────────────────────────

def make(job: Any, build: Any, *, hooks: Optional[dict] = None,
         progress: Callable[[str], None] = lambda _l: None) -> dict:
    """Make one job after the build's one Yes. ``{"ok", "page", "reason",
    "screen", "made"}``. Never raises. *hooks* replaces the real makers
    (tests): ``finish``, ``start``, ``status``, ``add_server``,
    ``register_mcp``, ``run_plan``, ``make_plan``, ``assemble``,
    ``add_routine``, ``check``."""
    job = job if isinstance(job, dict) else {}
    build = build if isinstance(build, dict) else {}
    if not job.get("id"):
        return _out(job, False, NO_ID)
    if not yes_covers(job, build):
        return _out(job, False, WAITING)
    from puffpai.build import setup as setup_mod
    also = setup_mod.pending_also(job, build.get("setup") or [])
    if also:
        return _out(job, False, setup_mod.also_reason(also), status="needs_setup")
    h = _hooks(hooks)
    route = str(job.get("route") or "")
    cands = candidates_of(job, build)
    try:
        from puffpai.build import check as check_mod
        checker = h.get("check") or (lambda c: check_mod.check(c, fetch=False))
        for c in cands:
            verdict = checker(c)
            if not verdict.get("ok"):
                c["_refused"] = str(verdict.get("reason") or "Drawbridge stopped it")
        usable = [c for c in cands if not c.get("_refused")]
        refused = [c for c in cands if c.get("_refused")]
        if route == "A":
            got = _make_app(job, build, usable, h, progress)
        elif route == "B":
            got = _make_tools(job, build, usable, h, progress)
        elif route == "C":
            got = _make_data(job, build, usable, h, progress)
        else:
            got = _out(job, False, NOTHING_FOUND, screen={"kind": "none"})
    except Exception as exc:  # noqa: BLE001 - one job never stops the rest
        logger.warning("build make: %s stopped (%s)", job.get("id"), type(exc).__name__)
        return _out(job, False, f"making it stopped ({type(exc).__name__})")
    if refused:
        got["refused"] = [{"ref": c.get("ref"), "name": _name(c), "why": c["_refused"]}
                          for c in refused]
        if not got.get("ok") and not got.get("reason"):
            got["reason"] = f"{_name(refused[0])}: {refused[0]['_refused']}"
    save_made(job, got)
    return got


# -- what was made, for prove(job, build) -----------------------------------------

_MADE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\Z")


def made_path(job_id: Any) -> str:
    from puffpai.config import get_state_dir
    jid = str(job_id or "")
    if not _MADE_ID.match(jid):
        raise ValueError("not a job id")
    return os.path.join(get_state_dir(), "build", "made", f"{jid}.json")


def save_made(job: dict, got: dict) -> None:
    """Keep make's answer for the job (``<state>/build/made/<id>.json``), so
    ``prove(job, build)`` knows what to ask. Never raises."""
    try:
        path = made_path(job.get("id"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fsutil.atomic_write_json(path, {**got, "route": job.get("route"),
                                        "at": int(time.time())})
    except Exception as exc:  # noqa: BLE001 - prove then says it was not made
        logger.info("build make: not kept (%s)", type(exc).__name__)


def load_made(job: dict) -> Optional[dict]:
    """What make made for *job* last (same route), or None."""
    try:
        got = fsutil.read_json(made_path(job.get("id")), default=None)
    except ValueError:
        return None
    if not isinstance(got, dict) or got.get("route") != job.get("route"):
        return None
    return got


def _hooks(hooks: Optional[dict]) -> dict:
    return dict(hooks or {})


def _hers(c: dict) -> bool:
    try:
        from puffpai import github_account
        return github_account.is_hers(c)
    except Exception:  # noqa: BLE001
        return False


def _first(cands: list[dict], cls: str) -> Optional[dict]:
    return next((c for c in cands if class_of(c) == cls), None)


# -- A: an app as it is ----------------------------------------------------------

#: A project address Toolsmith downloads (build.toolsmith_run download).
PROJECT_URL = re.compile(r"^https://(github\.com|codeberg\.org|gitlab\.com)/[\w.-]+/[\w.-]+/?$")
#: Apps Toolsmith tries for one job, in order (the pick, then the next ones).
TRIES_MAX = 3


def toolsmith_app(c: dict) -> bool:
    """An app Toolsmith runs as its makers say: one found by stars, or any
    app whose code is a public project address (an app list row too). Her
    private projects and apps already added keep their own way."""
    if not isinstance(c, dict) or class_of(c) != "APP":
        return False
    if c.get("direct"):
        return True
    if c.get("app_id") or c.get("private"):
        return False
    return bool(PROJECT_URL.match(str(c.get("url") or "").strip()))


def _make_app(job: dict, build: dict, cands: list[dict], h: dict,
              progress: Callable[[str], None]) -> dict:
    app = _first(cands, "APP")
    if app is None and job.get("code"):
        # No ready-made app for this job: Toolsmith makes one from its code.
        return _make_from_code(job, build, progress)
    if app is None:
        return _out(job, False, "nothing found here runs as an app")
    if _her_project_next(job, build):
        # A project she pasted (Find more) runs before any app that is
        # still on the job, and an app that already gave up is never retried
        # ahead of it.
        return _make_from_code(job, build, progress)
    if toolsmith_app(app):
        return _make_with_toolsmith(job, build, app, progress)
    prepared = app.get("prepared") if isinstance(app.get("prepared"), dict) else {}
    iid = str(app.get("integration_id") or prepared.get("integration_id") or "")
    if not iid and app.get("app_id"):
        # Already added (its own record exists): its screen is used as it is.
        web_id = str(app["app_id"])
        screen = {"kind": OWN, "app_id": web_id, "route": f"/dashboard/apps/{web_id}"}
        made = {"app_id": web_id}
        door = _door_for_web_app(web_id, app, build)
        if door:
            made["switch_on"] = door
        return _out(job, True, "", screen=screen, made=made)
    if not iid and app.get("private") and _hers(app):
        # Her private project: its own row on the list her Yes covers, then
        # downloaded through her account and checked now (nothing earlier).
        why = not_listed(build, app, "private_repo")
        if why:
            return _out(job, False, f"{_name(app)}: {why}")
        pull = h.get("pull_private")
        if pull is None:
            from puffpai import github_account
            pull = github_account.pull_private
        pulled = pull(app) or {}
        if not pulled.get("ok"):
            reason = pulled.get("reason") or "it could not be downloaded"
            return _out(job, False, f"{_name(app)}: {reason}")
        prepared = dict(pulled.get("prepared") or {})
        iid = str(prepared.get("integration_id") or "")
        if not iid or not prepared.get("code_sha"):
            return _out(job, False, f"{_name(app)} was not checked yet")
    elif not iid and (app.get("check") or {}).get("later"):
        # Downloaded and checked now, after her Yes; the next apps that fit
        # (named on the same row) are tried when one cannot be added or
        # cannot start here.
        return _make_later(job, build, app, h, progress)
    elif not iid:
        return _out(job, False, f"{_name(app)} was not checked yet")
    else:
        why = not_listed(build, app, "app")
        if why:
            return _out(job, False, f"{_name(app)}: {why}", made={"integration_id": iid})
    return _finish_and_screen(job, build, app, iid, prepared, h, progress)


#: A setup row whose yes lets Toolsmith run commands beyond an app's folder.
APPROVE = "approve"


def approve_row_id(ref: Any) -> str:
    """The one setup row whose yes covers an app's steps beyond its folder."""
    return "program-approve-" + re.sub(r"[^a-z0-9]+", "-", str(ref).lower())[:60]


def ask_approval(job: dict, app: dict, pending: list[dict]) -> None:
    """Put the commands Toolsmith wants to run beyond the app's folder on the
    setup list, all on one row (one yes covers them); the list needs a new yes.
    A step that runs only with her admin yes is marked (and said) on the row."""
    from puffpai.build import schema as S
    from puffpai.build import toolsmith_run
    from puffpai.build import yes as yes_mod
    rid = approve_row_id(app.get("ref"))
    commands = [p["command"] for p in pending][:12]
    detail = "Toolsmith asks to run, for " + _name(app) + ", exactly as written: " + "; ".join(
        f"{p['command']} ({p['why']}"
        + ("; asks for your admin yes in the normal prompt" if toolsmith_run.needs_admin(
            p["command"]) else "") + ")" for p in pending[:12]) + "."

    def change(b: dict) -> None:
        rows = [r for r in b.get("setup") or [] if r.get("id") != rid]
        old = next((r for r in b.get("setup") or [] if r.get("id") == rid), {})
        keep = [c for c in old.get("commands") or [] if c not in commands]
        words = detail if not keep else f"{old.get('detail') or ''} Also: {detail}".strip()
        # Only a command she can read in full on the row is covered by her yes.
        allc = [c for c in keep + commands if c in words[:4000]]
        rows.append({"id": rid, "kind": "program", "label": f"Let Toolsmith set up {_name(app)}"
                     " beyond its own folder", "detail": words[:4000],
                     "job_ids": list(dict.fromkeys(list(old.get("job_ids") or [])
                                                   + [job["id"]])),
                     "done": False, "make": APPROVE, "ref": app.get("ref"),
                     "commands": allc,
                     "admin": [c for c in allc if toolsmith_run.needs_admin(c)
                               or c in (old.get("admin") or [])]})
        b["setup"] = rows
        yes_mod.reset(b)
    S.update_build(change)


def one_per_repo(cands: list[dict]) -> list[dict]:
    """*cands* in order, the first of each GitHub owner/repo only."""
    from puffpai.build import toolsmith_run
    seen: set[str] = set()
    out = []
    for c in cands:
        key = toolsmith_run.repo_key(c) or str(c.get("ref") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def ask_each(job: dict, app: dict, tries: list[dict], pending: list[dict]) -> None:
    """Each app's own steps beyond its folder on its own row: a backup's
    install never lands on another app's row (a step with no app named goes
    on *app*'s)."""
    by_ref = {c.get("ref"): c for c in tries if isinstance(c, dict)}
    groups: dict[str, list[dict]] = {}
    for p in pending:
        ref = p.get("ref") if p.get("ref") in by_ref else app.get("ref")
        groups.setdefault(str(ref), []).append(p)
    for ref, items in groups.items():
        ask_approval(job, by_ref.get(ref) or app, items)


def keep_beyond(job: dict, items: list[dict]) -> None:
    """What Toolsmith installed or switched on beyond the app's folder, kept
    in the job's own state (the job page lists it, each with its undo)."""
    if not items or not job.get("id"):
        return
    from puffpai.build import schema as S

    def change(b: dict) -> None:
        j = S.find_job(b, job["id"])
        if j is None:
            return
        have = [i for i in j.get("beyond_done") or [] if isinstance(i, dict)]
        seen = {i.get("what") for i in have}
        j["beyond_done"] = (have + [dict(i) for i in items
                                    if isinstance(i, dict) and i.get("what") not in seen])[-60:]
    try:
        S.update_build(change)
    except Exception as exc:  # noqa: BLE001 - the run itself stands
        logger.info("build make: undo list not kept (%s)", type(exc).__name__)


def _make_with_toolsmith(job: dict, build: dict, app: dict,
                         progress: Callable[[str], None]) -> dict:
    """Toolsmith gets one of the job's ready-made apps running, the way a
    person would (build.toolsmith_run): its own screen opens in her browser."""
    why = not_listed(build, app, "app")
    if why:
        return _out(job, False, f"{_name(app)}: {why}")
    pool = _pool(build)
    order = [app] + [dict(pool[r], ref=r) for r in job.get("alternates") or []
                     if r in pool and r != app.get("ref") and toolsmith_app(dict(pool[r], ref=r))]
    # An app she pasted for this job (Find more) goes first, ahead of the pick;
    # with "Add to the list" it goes after the job's own apps instead.
    hers = [dict(c, ref=r) for r, c in pool.items()
            if pasted_app(c, job) and toolsmith_app(dict(c, ref=r))
            and not not_listed(build, dict(c, ref=r), "app")]
    her_refs = {c["ref"] for c in hers}
    late: list[dict] = []
    if not her_first(job):
        order = [c for c in order if c["ref"] not in her_refs] + hers
        late, hers, her_refs = hers, [], set()
    else:
        order = hers + [c for c in order if c["ref"] not in her_refs]
    from puffpai.build import toolsmith_run
    tried = _tried(job, build)
    failed = {t["ref"] for t in tried}
    # One app that does several jobs runs once: a job whose pick, her own
    # project or an app it used before already runs (and answers) uses it as
    # it is, matched by ref or GitHub owner/repo. An alternate that runs for
    # another job waits its turn in the order (_try_group), like any app.
    # While her pasted app was not tried yet, only that app may be reused.
    untried_hers = [c for c in hers if c["ref"] not in failed]
    running = toolsmith_run.running_for(untried_hers or [app] + hers + late
                                        + _used_before(job))
    if running:
        progress(f"{running.get('name')} already runs at {running['url']}; using it.")
        return _working_app(job, running)
    # Try next app: the ones that already gave up for this job are skipped
    # (they stay on the job with their reason).
    # One try per project: the same GitHub owner/repo under two refs (a
    # search result and an app list row) is tried once.
    gone = {toolsmith_run.repo_key(dict(pool.get(r) or {}, ref=r)) for r in failed} - {""}
    tries = one_per_repo([c for c in order if c["ref"] not in failed
                          and toolsmith_run.repo_key(c) not in gone])[:TRIES_MAX]
    # Add to the list: her project still runs in this go, after the others.
    tries += [c for c in late if c["ref"] not in failed and c["ref"] not in
              {t["ref"] for t in tries} and toolsmith_run.repo_key(c) not in gone]
    if not tries:
        names = ", ".join(t.get("name") or t["ref"] for t in tried
                          if not str(t["ref"]).startswith("code:"))
        if job.get("code"):
            # Every app gave up: Toolsmith makes one from the job's code.
            return _make_from_code(job, build, progress)
        return _out(job, False, f"Every app found for this job was tried and none started "
                                f"here ({names}). Try Find more on its page.")
    # Her pasted apps first, on their own; then the rest.
    first = [c for c in tries if c["ref"] in her_refs]
    rest = [c for c in tries if c["ref"] not in her_refs]
    reason = ""
    for group in (first, rest):
        if not group:
            continue
        got = _try_group(job, build, app, group, progress, own=her_refs | {app.get("ref")})
        if got.get("ok") or got.get("waiting_yes") or got.get("vault_wait") or got.get("stopped"):
            return got
        reason = str(got.get("reason") or reason)
        if group is first:
            # Her pasted app did not start here: an app this job used before
            # (or one of its own apps) that still answers is kept as it was.
            kept = _kept_app(job, build)
            if kept:
                line = (f"Kept {kept.get('name')}: "
                        f"{', '.join(_name(c) for c in first)} did not start here.")
                progress(line)
                return _working_app(job, kept, kept=line)
    if job.get("code"):
        # Every app gave up: Toolsmith makes one from the job's code.
        return _make_from_code(job, build, progress, before=reason)
    return _out(job, False, reason or "none of its apps started here")


#: Why a kept alternate is not used for a job: its page is not a main page.
NOT_MAIN_HERE = "it runs here, but its page is not a main page for this job"


def _try_group(job: dict, build: dict, app: dict, group: list[dict],
               progress: Callable[[str], None], own: Any = ()) -> dict:
    """Get one of *group* running, in its order: an app already kept here
    with its folder is started again (never downloaded again), the others go
    to Toolsmith. A kept app that is not in *own* (the job's pick, her
    projects) is an alternate: the apps before it are tried first, and it is
    used only when its page passes the main-page check."""
    from puffpai.build import toolsmith_run
    fresh: list[dict] = []
    last: Optional[dict] = None
    for c in group:
        app_id = toolsmith_run.kept_for(c)
        live = None if app_id else toolsmith_run.running_for([c])
        if not app_id and not live:
            fresh.append(c)
            continue
        alternate = c.get("ref") not in set(own or ())
        if alternate and fresh:
            # Its turn comes after the apps before it, like any app.
            last = _run_fresh(job, build, app, fresh, progress)
            if not _go_on(last):
                return last
            fresh = []
        if live:
            # It runs (for another job): used when it is this job's own or
            # its page is a main page.
            if not alternate or toolsmith_run.main_page_ok(live):
                progress(f"{live.get('name')} already runs at {live['url']}; using it.")
                return _working_app(job, live)
            keep_tried(job, [{"ref": str(c["ref"]), "name": _name(c), "why": NOT_MAIN_HERE}])
            continue
        progress(f"{_name(c)} is already on this computer; starting it again.")
        got = toolsmith_run.restart(app_id)
        entry = toolsmith_run.registry().get(app_id)
        if got.get("ok") and isinstance(entry, dict):
            if not alternate or toolsmith_run.main_page_ok({"app_id": app_id, **entry}):
                return _working_app(job, {"app_id": app_id, **entry})
            got = {"said": NOT_MAIN_HERE}
        keep_tried(job, [{"ref": str(c["ref"]), "name": _name(c),
                          "why": str(got.get("said") or "it did not start again here")[:300]}])
    if not fresh:
        return last or _out(job, False, f"{', '.join(_name(c) for c in group)} did not start "
                                        "again here.")
    return _run_fresh(job, build, app, fresh, progress)


def _go_on(got: dict) -> bool:
    """A group try that ended with no app and nothing to wait for: the next
    app in the order may go."""
    return not (got.get("ok") or got.get("waiting_yes") or got.get("vault_wait")
                or got.get("stopped"))


def _run_fresh(job: dict, build: dict, app: dict, fresh: list[dict],
               progress: Callable[[str], None]) -> dict:
    """Toolsmith downloads and runs *fresh* (in order) for *job*."""
    from puffpai.build import toolsmith_run
    got = toolsmith_run.run_job(job, fresh, progress=progress, **_approved(build, fresh))
    keep_beyond(job, list(got.get("beyond_done") or []))
    keep_tried(job, list(got.get("tried") or []))
    if not got.get("ok") and got.get("stopped"):
        # She added a project to run first: nothing more of this try.
        return _out(job, False, str(got.get("reason")), stopped=True)
    if not got.get("ok") and got.get("vault_wait"):
        # Paused for her vault: the same app goes on when it opens; nothing next.
        return _out(job, False, str(got.get("reason")), vault_wait=True)
    if not got.get("ok") and got.get("pending"):
        # Steps beyond the app's folder: one row on the setup list, one yes.
        ask_each(job, app, fresh, got["pending"])
        return _out(job, False, str(got.get("reason")), waiting_yes=True)
    if not got.get("ok"):
        return _out(job, False, str(got.get("reason") or "none of its apps started here"))
    return _working_app(job, got["app"])


def _used_before(job: dict) -> list[dict]:
    """The apps Toolsmith kept for this job before (its registry entries)."""
    from puffpai.build import toolsmith_run
    return [{"ref": e.get("ref"), "name": e.get("name")}
            for e in toolsmith_run.registry().values()
            if isinstance(e, dict) and e.get("job") == job.get("id") and e.get("ref")]


def _kept_app(job: dict, build: dict) -> Optional[dict]:
    """An app this job used before, or one in its pick or alternates, that
    still runs and answers (matched by ref or GitHub owner/repo), else None."""
    from puffpai.build import toolsmith_run
    pool = _pool(build)
    pick = (job.get("pick") or {}).get("ref")
    refs = list(job.get("alternates") or []) + list(job.get("ingredients") or [])
    ranked = [dict(pool.get(r) or {}, ref=r) for r in dict.fromkeys(refs) if r and r != pick]
    mine = _used_before(job) + ([dict(pool.get(pick) or {}, ref=pick)] if pick else [])
    # The job's own app (used before, its pick) as it is; an app only ranked
    # for it, when its page passes the main-page check.
    return toolsmith_run.running_for(mine) or toolsmith_run.running_for(
        ranked, accept=toolsmith_run.main_page_ok)


def _approved(build: dict, tries: list[dict]) -> dict:
    """What her Yes covers beyond the folders of *tries* (the approve rows):
    the exact commands, the admin ones among them, and the programs for the
    whole computer the rows name (a runtime, a CLI)."""
    refs = {c["ref"] for c in tries}
    rows = [r for r in build.get("setup") or [] if r.get("make") == APPROVE
            and r.get("ref") in refs]
    approved = [cmd for r in rows for cmd in r.get("commands") or []]
    admin = [cmd for r in rows for cmd in r.get("admin") or [] if cmd in approved]
    programs = [str(i.get("tool")) for r in rows for i in r.get("items") or []
                if isinstance(i, dict) and i.get("kind") == "tool" and i.get("tool")]
    return {"approved": approved, "admin": admin,
            **({"programs": programs} if programs else {})}


def pasted_app(c: dict, job: dict) -> bool:
    """*c* is an app she pasted for *job* (Find more, ``pipeline.plan_more``
    ranks it -1 for that job)."""
    rank = (c.get("job_rank") or {}) if isinstance(c, dict) else {}
    return isinstance(rank, dict) and rank.get(job.get("id")) == -1


#: Where a project she adds to a job goes: "first" (Run it first, the
#: default) or "list" (Add to the list: after the job's apps not yet tried).
HER_ORDERS = ("first", "list")


def her_first(job: dict) -> bool:
    """Does the project she added to *job* run before its other apps?"""
    return (job or {}).get("her_order") != "list"


def _her_project_next(job: dict, build: dict) -> bool:
    """The job's first code is a project she pasted and it has not been
    tried for this job yet (and she chose Run it first)."""
    from puffpai.build import decide
    if not decide._pasted(job) or not her_first(job):  # noqa: SLF001 - decide's rule
        return False
    return job["code"][0]["ref"] not in {t["ref"] for t in _tried(job, build)}


def _make_from_code(job: dict, build: dict, progress: Callable[[str], None],
                    before: str = "") -> dict:
    """No ready-made app runs for *job*: Toolsmith makes a small one from the
    job's code (``job["code"]``, a library or tool found for it, best first;
    at most ``setup.MAKE_TRIES``), each on the row her Yes covers. The made
    app is kept, opened and reused like any app Toolsmith runs."""
    from puffpai.build import setup as setup_mod
    from puffpai.build import toolsmith_run
    lead = f"{before.rstrip('.')}. " if before else ""
    code = [dict(c) for c in job.get("code") or [] if isinstance(c, dict) and c.get("ref")
            and c.get("url")][:setup_mod.MAKE_TRIES]
    listed = [c for c in code if not not_listed(build, c, setup_mod.MAKE_CODE)]
    if not listed:
        return _out(job, False, lead + "Making an app from its code was not on your setup "
                                       "list, so it was not made.")
    failed = {t["ref"] for t in _tried(job, build)}
    # Her pasted project, not tried yet: only it may be reused, nothing else first.
    hers_first = _her_project_next(job, build) and listed[0]["ref"] == job["code"][0]["ref"]
    running = toolsmith_run.running_for(listed[:1] if hers_first else listed)
    if running:
        progress(f"{running.get('name')} already runs at {running['url']}; using it.")
        return _working_app(job, running)
    tries = [c for c in listed if c["ref"] not in failed]
    if not tries:
        names = ", ".join(c["full_name"] for c in listed)
        return _out(job, False, lead + f"Toolsmith tried making an app from {names} and it "
                                       "did not work here. Try Find more on its page.")
    progress(f"Making an app from {tries[0]['full_name']}.")
    got = toolsmith_run.run_job(job, tries, progress=progress, make=True,
                                **_approved(build, tries))
    keep_beyond(job, list(got.get("beyond_done") or []))
    keep_tried(job, list(got.get("tried") or []))
    if not got.get("ok") and got.get("stopped"):
        return _out(job, False, str(got.get("reason")), stopped=True)
    if not got.get("ok") and got.get("vault_wait"):
        return _out(job, False, str(got.get("reason")), vault_wait=True)
    if not got.get("ok") and got.get("pending"):
        ask_each(job, tries[0], tries, got["pending"])
        return _out(job, False, str(got.get("reason")), waiting_yes=True)
    kept = None if got.get("ok") or not hers_first else _kept_app(job, build)
    if kept:
        # Her pasted project made nothing here: the app this job had stays.
        line = f"Kept {kept.get('name')}: {tries[0]['full_name']} did not start here."
        progress(line)
        return _working_app(job, kept, kept=line)
    if not got.get("ok"):
        return _out(job, False, lead + str(got.get("reason") or "no app could be made here"))
    return _working_app(job, got["app"])


def _working_app(job: dict, a: dict, kept: str = "") -> dict:
    """The job works on Toolsmith's app *a*; *kept*: the plain line when it
    went back to an app it had (the app is off the job's tried list)."""
    screen = {"kind": OWN, "url": a["url"], "toolsmith_app": a["app_id"]}
    made = {"toolsmith_app": a["app_id"], "app_name": a["name"],
            "app_ref": str(a.get("ref") or ""), "model": str(a.get("model") or model_now())}
    if a.get("made_from"):
        made["made_from"] = str(a["made_from"])
    if a.get("notes"):
        # For the owner: its README's sign-in, an outside service down.
        made["notes"] = [str(n) for n in a["notes"]]
    pid = str(a.get("program_id") or "")
    if not pid and str(a.get("app_id") or "").startswith("program:"):
        pid = str(a["app_id"]).split(":", 1)[1]
    if a.get("desktop") or pid:
        # A desktop program (no page): she opens it in its own window.
        made["program"] = {"id": pid[:100], "path": str(a.get("path") or "")[:1000]}
    if kept:
        forget_tried(job, a)
        from puffpai.build.apps import say
        say(kept)       # bot.log and the team page (make's progress may be silent)
        return _out(job, True, "", screen=screen, made=made, kept=kept)
    return _out(job, True, "", screen=screen, made=made)


def _tried(job: dict, build: dict) -> list[dict]:
    """The apps that already gave up for *job*: ``[{"ref", "name", "why"}]``."""
    from puffpai.build import schema as S
    j = S.find_job(build, job.get("id")) if isinstance(build, dict) else None
    got = (j or {}).get("tried") or job.get("tried") or []
    # An app "tried" only because Toolsmith's key was locked away never ran:
    # it is tried again (Try next app).
    return [t for t in got if isinstance(t, dict) and t.get("ref")
            and not vault_pause(t.get("why"))]


#: The words of a try that only paused for the locked vault (old and new).
_VAULT_PAUSE = ("paused: your vault is locked", "unlock your vault to let toolsmith go on")


def vault_pause(why: Any) -> bool:
    """*why* says the try only waited for the vault, so the app never ran."""
    text = " ".join(str(why or "").split()).lower()
    return any(p in text for p in _VAULT_PAUSE)


def model_now() -> str:
    """The model Toolsmith works with now: "local" (on this computer) or the
    Anthropic model id she chose. Kept on each try and app used, so the pages
    can offer to check jobs again after she switches."""
    try:
        from puffpai.security import toolsmith_cloud
        st = toolsmith_cloud.read_setting()
        if st.get("where") == "cloud":
            return str(st.get("model") or toolsmith_cloud.DEFAULT_MODEL)
    except Exception:  # noqa: BLE001 - unreadable is local
        pass
    return "local"


def keep_tried(job: dict, items: list[dict]) -> None:
    """The apps that gave up for this job, with why, kept on the job (the
    next try starts after them; the page lists them), each with the model
    Toolsmith used."""
    if not items or not job.get("id"):
        return
    now = model_now()
    items = [dict(i, model=str(i.get("model") or now)) for i in items]
    from puffpai.build import schema as S

    def change(b: dict) -> None:
        j = S.find_job(b, job["id"])
        if j is None:
            return
        new = {i["ref"] for i in items}
        j["tried"] = ([t for t in j.get("tried") or [] if isinstance(t, dict)
                       and t.get("ref") not in new] + [dict(i) for i in items])[-20:]
    try:
        S.update_build(change)
    except Exception as exc:  # noqa: BLE001 - the run itself stands
        logger.info("build make: tried list not kept (%s)", type(exc).__name__)


def forget_tried(job: dict, cand: Any) -> None:
    """Take *cand* (by ref or GitHub owner/repo) off the job's tried list."""
    if not job.get("id"):
        return
    from puffpai.build import schema as S

    def change(b: dict) -> None:
        j = S.find_job(b, job["id"])
        if j is not None:
            drop_tried(j, cand, _pool(b))
    try:
        S.update_build(change)
    except Exception as exc:  # noqa: BLE001 - the run itself stands
        logger.info("build make: tried list not changed (%s)", type(exc).__name__)


def drop_tried(job: dict, cand: Any, pool: dict) -> None:
    """*job*'s tried list without *cand* (its ref, or the same GitHub
    owner/repo under any ref)."""
    from puffpai.build import toolsmith_run
    ref = cand.get("ref") if isinstance(cand, dict) else cand
    repo = toolsmith_run.repo_key(cand)
    job["tried"] = [t for t in job.get("tried") or [] if isinstance(t, dict)
                    and t.get("ref") != ref and not (repo and toolsmith_run.repo_key(
                        dict(pool.get(t.get("ref")) or {}, ref=t.get("ref"))) == repo)]


def _finish_and_screen(job: dict, build: dict, app: dict, iid: str, prepared: dict, h: dict,
                       progress: Callable[[str], None]) -> dict:
    """Add the checked app exactly as checked and open its own screen."""
    added: dict = {}
    if prepared.get("code_sha"):
        finish = h.get("finish")
        if finish is None:
            from puffpai.integrate import apply
            finish = apply.finish_prepared
        added = finish(iid, code_sha=str(prepared.get("code_sha") or ""),
                       host_sha=str(prepared.get("host_sha") or ""), progress=progress) or {}
        if added.get("status") != "added":
            return _out(job, False, _named(app, str(added.get("line") or "it was not added")),
                        made={"integration_id": iid, "status": added.get("status")})
    screen = _app_screen(iid, added, app, h)
    made = {"integration_id": iid, "app_id": screen.get("app_id", ""),
            "app_ref": str(app.get("ref") or ""), "app_name": _name(app)}
    if screen.get("problem"):
        return _out(job, False, _named(app, screen["problem"]), screen=screen, made=made)
    door = _door_for_web_app(screen.get("app_id", ""), app, build)
    if door:
        made["switch_on"] = door
    return _out(job, True, "", screen=screen, made=made)


def _drop_attempt(iid: str, h: dict) -> None:
    """An app that was tried and did not work is taken back out (its own
    server stopped, its files and record removed)."""
    if not iid or h.get("finish"):
        return
    try:
        from puffpai.integrate import records, run_here
        try:
            run_here.remove(iid, by="buildbot")
        except Exception:  # noqa: BLE001 - not a host app
            pass
        records.remove(iid, by="buildbot")
    except Exception as exc:  # noqa: BLE001
        logger.info("build make: a tried app was not removed (%s)", type(exc).__name__)


def _needs_docker_now(prepared: dict) -> bool:
    """It needs Docker, which is not here AND BuildBot cannot install here."""
    if "docker" not in [str(p) for p in prepared.get("needs") or []]:
        return False
    try:
        from puffpai import deps
        return not deps.can_install("docker")
    except Exception:  # noqa: BLE001
        return True


def _named(cand: dict, reason: str) -> str:
    """The reason with the app's name in front ("Speedtest Tracker: ...")."""
    reason = str(reason or "")
    name = _name(cand)
    return reason if reason.startswith(name) else f"{name}: {reason}"


def _page_problem(out: dict, h: dict) -> str:
    """An app that runs its own server must show real content on its page
    (not an empty shell or an error) before it counts; "" when it does."""
    url = str((out.get("screen") or {}).get("url") or "")
    if not url or h.get("finish"):
        return ""
    page = h.get("page")
    if page is None:
        from puffpai.build import prove
        page = prove.page_answers
    try:
        return str(page(url) or "")
    except Exception as exc:  # noqa: BLE001
        return f"its page could not be checked ({type(exc).__name__})"


def _make_later(job: dict, build: dict, app: dict, h: dict,
                progress: Callable[[str], None]) -> dict:
    """The job's pick and its alternates in order: each is downloaded,
    checked by Drawbridge, added and started; the first whose own screen
    opens is used. One that needs Docker (not on this computer) is kept as
    the last resort, so the job still says plainly what it needs."""
    from puffpai.build import check as check_mod
    pool = _pool(build)
    tries = [app] + [dict(pool[r], ref=r) for r in job.get("alternates") or [] if r in pool]
    whys: list[str] = []
    for cand in tries:
        # One app that does several jobs is added once (another job's make
        # added it already): its screen is used as it is.
        iid = _added_from(cand)
        if iid and not not_listed(build, cand, "app"):
            got = _finish_and_screen(job, build, dict(cand, integration_id=iid), iid, {}, h,
                                     progress)
            if got.get("ok"):
                return got
    check = h.get("check_now") or (lambda c: check_mod.check(c, fetch=True))
    fallback: Optional[tuple[dict, dict]] = None
    for cand in tries:
        why = not_listed(build, cand, "app")
        if why:
            whys.append(f"{_name(cand)}: {why}")
            continue
        progress(f"Downloading {_name(cand)} and checking it.")
        fresh = {k: v for k, v in cand.items() if k not in ("check", "prepared")}
        got = check(fresh) or {}
        prepared = got.get("prepared") if isinstance(got.get("prepared"), dict) else {}
        if not (got.get("ok") and prepared.get("integration_id") and prepared.get("code_sha")):
            why = str(got.get("reason") or "it did not pass the checks")
            whys.append(why if why.startswith(_name(cand)) else f"{_name(cand)}: {why}")
            continue
        iid = str(prepared["integration_id"])
        if _needs_docker_now(prepared):
            if fallback is None:
                fallback = (cand, prepared)
            else:
                _drop_attempt(iid, h)
            continue
        out = _finish_and_screen(job, build, dict(cand, prepared=prepared), iid, prepared, h,
                                 progress)
        if out.get("ok"):
            progress(f"Waiting for {_name(cand)}'s page to show real content.")
            why = _page_problem(out, h)
            if why:
                out = _out(job, False, why)
        if out.get("ok"):
            if fallback is not None:
                _drop_attempt(str(fallback[1].get("integration_id") or ""), h)
            return out
        whys.append(_named(cand, str(out.get("reason") or "it did not start")))
        progress(f"{_name(cand)} did not work here; trying the next app.")
        _drop_attempt(iid, h)
    if fallback is not None:
        cand, prepared = fallback
        out = _finish_and_screen(job, build, dict(cand, prepared=prepared),
                                 str(prepared["integration_id"]), prepared, h, progress)
        if not out.get("ok"):
            out["reason"] = "; ".join([_named(cand, str(out.get("reason") or ""))]
                                      + whys)[:1200]
        return out
    return _out(job, False, "; ".join(whys)[:1200] or "nothing found here runs as an app")


def _added_from(cand: dict) -> str:
    """The id of a ready integration added from the same project address as
    *cand* (by an earlier job of this build), or ""."""
    url = str(cand.get("url") or "").rstrip("/").lower()
    if not url:
        return ""
    try:
        from puffpai.integrate import records
        for rec in records.all():
            if rec.get("status") == "ready" and                     str((rec.get("from") or {}).get("url") or "").rstrip("/").lower() == url:
                return str(rec.get("id") or "")
    except Exception:  # noqa: BLE001 - not known: it is downloaded
        return ""
    return ""


def _app_screen(iid: str, added: dict, app: dict, h: dict) -> dict:
    """Its own screen: a server it runs (started now, loopback only), or the
    browser app's page."""
    status = h.get("status")
    start = h.get("start")
    if status is None or start is None:
        from puffpai.integrate import run_here
        status = status or run_here.status
        start = start or (lambda i: run_here.start(i, by="owner", wait=READY_SECONDS))
    try:
        now = status(iid)
    except Exception:  # noqa: BLE001 - not a host app (a browser app)
        now = {}
    if now.get("can_run"):
        if now.get("state") not in ("running", "starting"):
            got = start(iid) or {}
            if not got.get("ok"):
                return {"kind": OWN, "problem": str(got.get("line") or "it did not start")}
            now = got.get("status") or now
            if now.get("state") not in ("running", "starting"):
                # Started, then stopped by itself (or was stopped): no screen.
                return {"kind": OWN, "problem": str(now.get("why") or "it did not start")}
        return {"kind": OWN, "integration_id": iid, "url": str(now.get("url") or ""),
                "state": str(now.get("state") or "")}
    web_id = str(added.get("app_id") or app.get("app_id") or app.get("webapp_id") or "")
    if web_id:
        return {"kind": OWN, "app_id": web_id, "route": f"/dashboard/apps/{web_id}"}
    if now.get("state") == "set_up":
        return {"kind": OWN, "integration_id": iid, "route": "", "state": "set_up"}
    return {"kind": OWN, "problem": "it has no screen of its own"}


def _door_for_web_app(web_id: str, app: dict, build: Optional[dict] = None) -> list[str]:
    """A browser app's sites from the setup list (sure and "maybe" ones) on
    its own place on the Bridge; each still off until its own yes. The sites
    the build's list SHOWED for it are switched on now under her one Yes
    (``build.yes.switch_app_sites``); the rest stay off. Returns the sites
    she still has to switch on."""
    if not web_id:
        return []
    hosts = [r["host"] for r in setup_rows_for(app)
             if r.get("what") == "outside_address" and r.get("host")]
    if not hosts:
        return []
    try:
        from puffpai.security import egress_doors as ed
        # A bigger site set turns its sites off (her yeses are kept per set):
        # the ones on now are switched on again below, under her Yes.
        was_on = [h for h, v in ed.app_web_hosts(web_id).items() if v]
        got = _put_on_door(web_id, hosts)
    except Exception:  # noqa: BLE001 - the Bridge part may not be in this build
        return []
    left = list(got.get("switch_on") or [])
    if build and app.get("ref"):
        try:
            from puffpai.build import yes as yes_mod
            now_on = yes_mod.switch_app_sites(str(app["ref"]), web_id, build, was_on=was_on)
        except Exception as exc:  # noqa: BLE001 - they stay off, on her list
            logger.info("build make: sites not switched on (%s)", type(exc).__name__)
            now_on = []
        left = [h for h in left if h not in now_on]
    return left


# -- B: tools ----------------------------------------------------------------------

def _make_tools(job: dict, build: dict, cands: list[dict], h: dict,
                progress: Callable[[str], None]) -> dict:
    action = str(job.get("action") or "")
    made: dict[str, list] = {"servers": [], "skills": [], "routines": [], "texts": []}
    problems: list[str] = []
    if action == "remind" or any(c.get("builtin") == "routine" for c in cands):
        got = _make_routine(job, h)
        if got.get("ok"):
            made["routines"].append(got["id"])
        else:
            problems.append(got["reason"])
    for c in cands:
        if c.get("builtin"):
            continue
        cls = class_of(c)
        if cls in ("TEXT", "GUIDE"):
            if _kind(c) == "skill":
                # A guide the bot reads must be on this computer to be read.
                folder, why = _ensure_skill(c, build, h)
                if not folder:
                    problems.append(f"{_name(c)}: {why}")
                    continue
            made["texts"].append(str(c.get("ref") or ""))
            continue
        if cls != "TOOL":
            continue
        got = _make_one_tool(c, h, build)
        if got.get("ok"):
            made[got["where"]].append(got["id"])
        else:
            problems.append(f"{_name(c)}: {got['reason']}")
    working = made["servers"] or made["skills"] or made["routines"]
    if not working and action in ("learn", "ask") and made["texts"]:
        working = made["texts"]
    screen = {"kind": SHARED, "view": view_for(job)}
    if not working:
        return _out(job, False, problems[0] if problems else "no tool here could be made",
                    screen=screen, made=made)
    return _out(job, True, "", screen=screen, made=made, problems=problems)


def _make_one_tool(c: dict, h: dict, build: Optional[dict] = None) -> dict:
    kind = _kind(c)
    if kind == "skill":
        from puffpai.build import classify
        if classify.key_request(c):
            return {"ok": False, "reason": "it is one request to its own service, so it "
                                           "is used as a data source, never a command"}
    plan = c.get("plan") if isinstance(c.get("plan"), dict) else {}
    iid = str(c.get("integration_id") or "")
    if iid and (plan.get("interfaces") or {}).get("mcp"):
        why = not_listed(build, c, "mcp") or _ensure_sdk(build, c, h)
        if why:
            return {"ok": False, "reason": why}
        register = h.get("register_mcp")
        if register is None:
            from puffpai.integrate import run_here
            register = lambda i, n: run_here.register_mcp(i, n, by="owner")  # noqa: E731
        got = register(iid, _mcp_name(c)) or {}
        return ({"ok": True, "where": "servers", "id": _mcp_name(c)} if got.get("ok")
                else {"ok": False, "reason": str(got.get("line") or "it did not connect")})
    target = _mcp_target(c)
    how: dict = {}
    if kind == "skill":
        how = _skill_how(c)
        if how["kind"] == "remote":
            if how["endpoint_kind"] != "mcp" or not how["endpoint"]:
                return {"ok": False, "reason": "it is an outside service with no tool "
                                               "address, so it is used as a data source"}
            target = target or how["endpoint"]
    if target:
        why = not_listed(build, c, "mcp", target) or _ensure_sdk(build, c, h)
        if why:
            return {"ok": False, "reason": why}
        return _connect(c, target, h)
    if kind == "skill" and how.get("kind") == "cli":
        folder, why = _ensure_skill(c, build or {}, h)
        if not folder:
            return {"ok": False, "reason": why}
        return _cli_skill({**c, "folder": folder, "skill": folder}, h, build)
    return {"ok": False, "reason": "BuildBot does not know how to connect it"}


def listed_companions(build: dict, ref: str) -> list[str]:
    """The sibling skills the setup list offered for the skill *ref* (her
    Yes covers exactly these, nothing else)."""
    return [str(r.get("name")) for r in (build.get("setup") or []) if isinstance(r, dict)
            and r.get("step") == "skill" and r.get("ref") == ref and r.get("name")]


def _ensure_skill(c: dict, build: dict, h: dict) -> tuple[str, str]:
    """``(folder, "")`` for a skill on this computer, installing a found one
    now (the build's Yes is the yes: the normal install and its checks, plus
    only the sibling skills the list showed), or ``("", why)``."""
    from puffpai import skills
    have = str(c.get("folder") or c.get("skill") or "")
    if have and skills.find(have) is not None:
        return have, ""
    ref = str(c.get("ref") or "")
    if not ref:
        return "", "it is not on this computer and BuildBot does not know where it came from"
    why = not_listed(build, c, "install")
    if why:
        return "", why
    install = h.get("install")
    if install is None:
        from puffpai.finding import install as install_mod
        install = lambda r, only: install_mod.install_thing(  # noqa: E731
            r, via="build", companions=only)
    try:
        got = install(ref, listed_companions(build, ref)) or {}
    except Exception as exc:  # noqa: BLE001 - one install never stops the rest
        return "", f"it could not be installed ({type(exc).__name__})"
    folder = str(got.get("folder") or "")
    if not got.get("ok") or not folder:
        return "", str(got.get("line") or "it could not be installed")
    _note_skill_folder(ref, folder)
    return folder, ""


def _note_skill_folder(ref: str, folder: str) -> None:
    """The installed folder on the build's record: its part, and its key rows
    (which can now take the key into the vault)."""
    try:
        from puffpai.build import schema as S

        def change(b: dict) -> None:
            part = (b.get("parts") or {}).get(ref)
            if isinstance(part, dict):
                part["folder"] = folder
            for r in b.get("setup") or []:
                if r.get("skill_key") and r.get("ref") == ref and not r.get("skill"):
                    r["skill"] = folder
        S.update_build(change)
    except Exception as exc:  # noqa: BLE001 - the install itself stands
        logger.info("build make: folder not noted (%s)", type(exc).__name__)


def _connect(c: dict, target: Any, h: dict) -> dict:
    """An MCP server, connected after the Yes. A server that needs a
    password is not sent one from here: its key goes into the vault on its
    setup row first (keys only from the vault)."""
    mcp = c.get("mcp") if isinstance(c.get("mcp"), dict) else {}
    if mcp.get("needs_password"):
        return {"ok": False, "reason": "it needs its password, kept in your vault, first"}
    add = h.get("add_server")
    if add is None:
        from puffpai import mcp_client
        add = mcp_client.add_server
    name = _mcp_name(c)
    got = add(name, target, label=_name(c), by="owner",
              source=f"build:{c.get('ref') or name}"[:200]) or {}
    if not got.get("ok"):
        return {"ok": False, "reason": str(got.get("message") or "it did not answer")}
    return {"ok": True, "where": "servers",
            "id": str((got.get("server") or {}).get("name") or name)}


def _cli_skill(c: dict, h: dict, build: Optional[dict] = None) -> dict:
    """A CLI skill: install what it declares (one plan, each package checked
    by Drawbridge; the build's Yes is the yes, for exactly the steps its list
    showed), then verify every declared program is here now."""
    from puffpai import skills
    folder = _skill_name(c)
    req = skills.requirements(_skill_meta(c))
    missing = skills.missing_programs(req)
    if missing:
        make_plan = h.get("make_plan")
        run_plan = h.get("run_plan")
        if make_plan is None or run_plan is None:
            from puffpai import deps
            make_plan = make_plan or (lambda s: deps.make_plan(s, thing=f"skill-{s}"))
            run_plan = run_plan or (lambda pid: deps.run_plan(pid, said_yes=True))
        plan = make_plan(folder) or {}
        if plan.get("steps"):
            why = steps_not_listed(build, c, list(plan.get("steps") or []))
            if why:
                return {"ok": False, "reason": why}
            run_plan(plan.get("id"))
        missing = skills.missing_programs(req)
    if missing:
        return {"ok": False, "reason": f"it still needs {', '.join(missing)}"}
    return {"ok": True, "where": "skills", "id": folder}


def _make_routine(job: dict, h: dict) -> dict:
    add = h.get("add_routine")
    if add is None:
        from puffpai import routines
        add = routines.add_routine
    prompt = " ".join(str(job.get("ask") or job.get("does") or job.get("name") or "").split())
    if not prompt:
        return {"ok": False, "reason": "it does not say what to remind you of"}
    at = str(job.get("time") or job.get("daily_at") or "") or None
    rid = re.sub(r"[^a-z0-9_]", "_", f"job_{job.get('id')}".lower())[:40]
    try:
        row = add("daily_prompt", routine_id=rid, label=str(job.get("name") or "")[:80],
                  daily_at=at, params={"prompt": prompt}, by="owner") or {}
    except Exception as exc:  # noqa: BLE001 - its own plain words
        return {"ok": False, "reason": str(exc)[:200] or "the reminder could not be kept"}
    return {"ok": True, "id": str(row.get("id") or rid)}


# -- C: a view put together from data sources ---------------------------------

def _cap_of(job: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(job.get("id") or "").lower()).strip("-")[:56] or "job"
    return f"cap-{slug}"


def app_id_of(job: dict) -> str:
    """The id of the app a route-C job is put together as."""
    from puffpai.assemble import plan as plan_mod
    return plan_mod.app_id_for(_cap_of(job))


def assemble_row(job: dict, cands: list[dict]) -> dict:
    """The Assemble plan row for one route-C job."""
    from puffpai.assemble import plan as plan_mod
    cap = _cap_of(job)
    parts = [_part(c) for c in cands if class_of(c) in ("DATA", "TOOL")]
    return {"cap": cap, "name": str(job.get("name") or cap)[:80],
            "does": str(job.get("does") or "")[:300], "decision": plan_mod.ASSEMBLE,
            "app_id": plan_mod.app_id_for(cap), "uses": [], "parts": [p for p in parts if p],
            "why": "", "job": str(job.get("id") or ""), "view": view_for(job)}


def _part(c: dict) -> dict:
    kind = _kind(c)
    part = {"id": str(c.get("ref") or ""), "kind": kind if kind in ("feed", "seed") else
            ("seed" if c.get("url") and kind in ("api", "stream", "", "live_data") else
             kind or "seed"),
            "name": _name(c)}
    for key in ("url", "sample_url", "door", "service", "about", "meanings", "image_template",
                "host", "app", "lookup_url", "time_base", "value_words", "topic"):
        if c.get(key):
            part[key] = c[key]
    if part["kind"] == "seed" and part.get("url") and not part.get("sample_url"):
        part["sample_url"] = part["url"]
    if not part.get("door") and kind == "feed" and c.get("app_id"):
        # A live card the bot already has (a migrated build): its own switch.
        from puffpai.security import egress_doors
        part["door"] = egress_doors.app_door_id(str(c["app_id"]))
    if not part.get("door") and (part["kind"] == "seed" or c.get("live_card")
                                 or str(c.get("service") or "").startswith("seed:")):
        # Her Yes switched the website on as a live card (build.yes): its
        # switch is the live card's, found by the host it reads.
        door = _live_door(c)
        if door:
            part["door"] = door
    if not part.get("door"):
        # Any other data source: its own switch on the Bridge (build.sources),
        # one per host the list showed for it (src_hosts, from its rows).
        from puffpai.build import sources
        hosts = [h for h in c.get("src_hosts") or [] if isinstance(h, str)] or \
            (sources.part_hosts(c) if class_of(c) == "DATA" else [])
        if hosts:
            part["door"] = sources.door_id(hosts[0])
            if len(hosts) > 1:
                part["doors"] = {h: sources.door_id(h) for h in hosts}
            part.setdefault("url", f"https://{hosts[0]}/")
    if kind == "skill":
        from puffpai.build import classify
        part.update({"skill": _skill_name(c),
                     "hosts": list(c.get("src_hosts") or []) or classify.skill_hosts(c)})
        names = classify.skill_keys(c)
        if names:
            part["keys"] = names      # names only; the value stays in the vault
    return part


def _live_door(c: dict) -> str:
    if c.get("live_card"):
        from puffpai.security import egress_doors
        return egress_doors.app_door_id(str(c["live_card"]))
    hosts = [str(h).lower() for h in c.get("hosts") or [] if h] or \
        [_https_host(c.get("url"))]
    try:
        from puffpai.assemble import plan as plan_mod
        doors = plan_mod._live_doors()  # noqa: SLF001 - host -> live card switch
    except Exception:  # noqa: BLE001
        return ""
    return next((doors[h] for h in hosts if h and h in doors), "")


def _make_data(job: dict, build: dict, cands: list[dict], h: dict,
               progress: Callable[[str], None]) -> dict:
    from puffpai.build import sources
    for c in cands:
        listed = [str(r.get("host")) for r in build.get("setup") or [] if isinstance(r, dict)
                  and r.get("ref") == c.get("ref") and r.get("kind") == "switch"
                  and sources.is_source(r.get("service")) and r.get("host")]
        if listed:
            c["src_hosts"] = list(dict.fromkeys(listed))
    row = assemble_row(job, cands)
    screen = {"kind": VIEW_SCREEN, "view": row["view"], "app_id": row["app_id"]}
    if not row["parts"]:
        return _out(job, False, "no data source here could be used", screen=screen)
    assemble = h.get("assemble")
    if assemble is None:
        from puffpai.assemble import api as assemble_api
        assemble = assemble_api.make_job_app
    who, ref = _yes_by(build)
    got = assemble(row, by=who, yes_ref=ref) or {}
    from puffpai.assemble import report
    ok = got.get("state") == report.WORKING
    try:
        # The view its data really gives (a forecast has no pictures): the
        # job's page and its words say that one, never the asked one.
        from puffpai.assemble import api as assemble_api
        spec = assemble_api.load_spec(row["app_id"]) if h.get("assemble") is None else None
        shown = [str(v.get("type")) for v in (spec or {}).get("views") or []
                 if isinstance(v, dict) and v.get("type") not in ("settings", None)]
        if shown:
            screen["view"] = shown[0]
    except Exception:  # noqa: BLE001 - the asked view stays
        pass
    return _out(job, ok, "" if ok else str(got.get("reason") or "it is not working yet"),
                screen=screen, made={"app_id": row["app_id"], "report": got})


__all__ = ["NOTHING_FOUND", "ROW_KIND", "WAITING", "answer_runtime_row", "assemble_row",
           "asks_path", "candidates_of", "class_of", "keep_sites", "kept_sites",
           "listed_companions", "load_made", "made_path", "make", "note_runtime_refusal",
           "page_of", "restore_web_sites", "save_made", "runtime_rows", "schema",
           "setup_rows_for", "sites_path", "view_for", "yes_covers"]
