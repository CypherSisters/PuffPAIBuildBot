"""One view of everything installed on this computer, for the brain and the dashboard.

Read-only, no storage of its own: every row is read fresh from the registry
that already keeps it.

* apps Toolsmith got working (``build.toolsmith_run``, ``toolsmith_apps.json``),
  running or not;
* apps and command line tools run by ``integrate.run_here`` (status ready);
* apps BuildBot put together (``assemble.api.all_specs``);
* skills (``skills.installed``);
* tool servers (``mcp_client.servers``);
* command line tools in the locked box (``integrate.records``, mode ``cli_tool``).

Her own GitHub projects show up as the apps they were installed as (their
``what`` says so). :func:`listing` gives rows of the pinned shape
``{"id", "kind", "name", "what", "status", "url", "source_url", "brain"}``;
``kind`` is ``app``, ``skill``, ``tool_server``, ``cli`` or ``knowledge`` (a
book or library the build added to the knowledge base, ``build.knowledge``); ``status`` is
``running``, ``stopped`` or ``ready``. :func:`prompt_block` is the same list
for the system prompt, one short line each, and ``GET /installed``
(``build.api``) hands it to the dashboard.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

KINDS = ("app", "skill", "tool_server", "cli", "knowledge", "program", "desktop")
STATUSES = ("running", "stopped", "ready")
FIELDS = ("id", "kind", "name", "what", "status", "url", "source_url", "brain")
#: Lines in the prompt block before the rest is left to ``list_installed``.
PROMPT_MAX = 60
WHAT_MAX = 160
#: The prompt line keeps only this much of ``what``.
PROMPT_WHAT = 90

PROMPT_HEAD = ("## Installed on this computer\n"
               "Everything below is installed and yours to use now, on this message. "
               "Apps: read_app shows what is on one (a stopped app is started first), "
               "use_local_app calls its own API when it lists calls. Skills: load with "
               "skill_view(name) and follow them. Tool servers: call their tools. "
               "Command line tools and her own programs: run them with run_command (the "
               "owner says yes to each command unless she chose Always allow for that "
               "program). Her desktop programs: open_in_program opens a file in one.\n")


def _plain(value: Any, limit: int = WHAT_MAX) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub("\\s*[\u2014\u2013]\\s*", ", ", text)
    return text[:limit].rstrip(" ,")


def _row(id: Any, kind: str, name: Any, what: Any = "", status: str = "ready",  # noqa: A002
         url: Any = "", source_url: Any = "", brain: Any = "") -> dict:
    return {"id": str(id or "")[:120], "kind": kind, "name": _plain(name, 80) or str(id),
            "what": _plain(what), "status": status if status in STATUSES else "ready",
            "url": str(url or ""), "source_url": str(source_url or ""),
            "brain": _plain(brain, 200)}


def _web(url: Any) -> str:
    text = str(url or "").strip()
    # No "user:password@" before the host: a sign-in never goes in a shown address.
    return text if re.match(r"^https?://[^\s/?#@]+(?:[/?#]\S*)?$", text) else ""


def _github(repo: Any) -> str:
    repo = str(repo or "").strip().strip("/")
    return f"https://github.com/{repo}" if re.match(r"^[\w.-]+/[\w.-]+$", repo) else ""


def _her_login() -> str:
    try:
        from puffpai import github_account
        return str(github_account.status().get("login") or "").lower()
    except Exception:  # noqa: BLE001 - not connected
        return ""


def _jobs() -> dict:
    """``{job id: job}`` of the current build, for what an app is for."""
    try:
        from puffpai.build import schema as S
        return {j.get("id"): j for j in (S.load_build() or {}).get("jobs") or []
                if isinstance(j, dict)}
    except Exception:  # noqa: BLE001 - no build yet
        return {}


def _toolsmith_apps() -> list[dict]:
    from puffpai.build import toolsmith_run
    apps = toolsmith_run.running_apps()
    if not apps:
        return []
    jobs, login = _jobs(), _her_login()
    # One row per project: an older copy of the same repo (a retry that was
    # registered twice) is left out, the running one first.
    best: dict = {}
    for a in apps:
        key = str(a.get("repo") or "").lower() or str(a.get("id"))
        if key not in best or (a.get("running") and not best[key].get("running")):
            best[key] = a
    apps = [a for a in apps if best.get(str(a.get("repo") or "").lower() or str(a.get("id"))) is a]
    out = []
    for a in apps:
        job = jobs.get(a.get("job")) or {}
        what = "A ready-made app"
        if job.get("name"):
            what += f" for {job['name']}"
        if job.get("does"):
            what += f": {job['does']}"
        repo = str(a.get("repo") or "")
        if login and repo.split("/", 1)[0] == login:
            what = "Your own project on GitHub. " + what
        running = bool(a.get("running"))
        out.append(_row(a["id"], "app", a.get("name"), what,
                        "running" if running else "stopped",
                        a.get("url") if running else "",
                        _github(repo) or _web(a.get("ref")),
                        "read_app shows its page" + ("" if running else
                                                     " (starts it first)")
                        + "; use_local_app if it lists calls"))
    return out


def _run_here_rows() -> tuple[list[dict], dict]:
    """Rows for run_here's apps and command line tools, and ``{assembled app
    id: its status row}`` for the apps BuildBot put together."""
    from puffpai.integrate import records, run_here
    out, assembled = [], {}
    ids = run_here.all_ids()
    login = _her_login() if ids else ""
    for iid in ids:
        rec = records.get(iid)
        if not rec or rec.get("status") != "ready":
            continue
        try:
            plan = run_here.read_state(iid).get("plan") or {}
            now = run_here.status(iid)
        except Exception as exc:  # noqa: BLE001 - one app never hides the others
            logger.debug("installed: %s (%s)", iid, type(exc).__name__)
            continue
        state = {"running": "running", "set_up": "ready"}.get(str(now.get("state")), "stopped")
        url = now.get("url") if state == "running" else ""
        if plan.get("how") == "assembled":
            aid = str((plan.get("assembled") or {}).get("app_id") or "")
            assembled[aid] = {"status": state, "url": url}
            continue
        src = (rec.get("from") or {}).get("url") if isinstance(rec.get("from"), dict) else ""
        what = rec.get("purpose") or now.get("how_words") or "An app on this computer"
        if login and f"github.com/{login}/" in str(src or "").lower():
            what = f"Your own project on GitHub. {what}"
        try:
            cli = run_here.cli_commands(iid)
        except Exception:  # noqa: BLE001
            cli = {}
        if cli and not now.get("can_run"):
            out.append(_row(iid, "cli", rec.get("name"), what, "ready", "", _web(src),
                            "run_command with: " + ", ".join(list(cli)[:6])))
            continue
        brain = "read_app shows its page" if now.get("can_run") else "set up, nothing to open"
        if now.get("can_run") and state != "running":
            brain += " (starts it first)"
        if cli:
            brain += "; its commands run with run_command: " + ", ".join(list(cli)[:6])
        out.append(_row(iid, "app", rec.get("name"), what, state, url, _web(src), brain))
    return out, assembled


def _assembled_rows(status_of: dict) -> list[dict]:
    from puffpai.assemble import api
    out = []
    for spec in api.all_specs():
        aid = str(spec.get("id") or "")
        now = status_of.get(aid) or {}
        names = [api.tool_name(aid, a.get("id")) for a in (spec.get("actions") or [])[:3]
                 if isinstance(a, dict) and a.get("id")]
        out.append(_row(f"assembled:{aid}", "app", spec.get("title") or aid,
                        "An app BuildBot put together from found parts",
                        now.get("status") or "ready", now.get("url") or "", "",
                        ("its tools: " + ", ".join(names)) if names else "its own tools"))
    return out


def _skill_rows() -> list[dict]:
    from puffpai import skills
    lock: dict = {}
    try:
        from puffpai.finding import install
        lock = install._read_lock()  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass
    out = []
    for rec in skills.installed():
        got = lock.get(rec["folder"])
        origin: dict = got if isinstance(got, dict) else {}
        source = _github(origin.get("repo")) or _web(origin.get("url"))
        remote = False
        try:
            remote = skills.is_remote(rec)
        except Exception:  # noqa: BLE001
            pass
        brain = f'skill_view("{rec["folder"]}")'
        if remote:
            brain += "; an outside service, never a command"
        out.append(_row(f"skill:{rec['folder']}", "skill", rec["folder"],
                        rec.get("description"), "ready", "", source, brain))
    return out


def _tool_server_rows() -> list[dict]:
    from puffpai import mcp_client
    out = []
    for s in mcp_client.servers():
        tools = list(s.get("tools") or [])
        on = bool(s.get("switched_on", True))
        what = f"A tool server with {len(tools)} tool{'s' if len(tools) != 1 else ''}"
        if tools:
            what += ": " + ", ".join(tools[:5])
        names = ", ".join(mcp_client.tool_name(s["name"], t) for t in tools[:3])
        out.append(_row(f"mcp:{s['name']}", "tool_server", s.get("label") or s["name"], what,
                        "ready" if on else "stopped", "", _web(s.get("source")),
                        (f"its tools: {names}" if names else "its tools")
                        if on else "switched off on the What leaves your computer page"))
    return out


def _box_cli_rows() -> list[dict]:
    from puffpai.integrate import records
    out = []
    for rec in records.all():
        if rec.get("mode") != "cli_tool" or rec.get("status") != "ready":
            continue
        src = (rec.get("from") or {}).get("url") if isinstance(rec.get("from"), dict) else ""
        out.append(_row(rec["id"], "cli", rec.get("name"),
                        rec.get("purpose") or "A command line tool in the locked box",
                        "ready", "", _web(src), "its actions in the app tools"))
    return out


def _program_rows() -> list[dict]:
    """The programs she added herself (``programs.py``, ``programs.json``)."""
    from puffpai import programs
    out = []
    for r in programs.rows():
        if r["kind"] == "cli":
            how = "run_command " + r["command"] + " ..."
            if r["always_allow"]:
                how += " (Always allow: a plain call runs without asking)"
            names = [str(c.get("command") or "") for c in r["commands"][:4]]
            what = "Your own command line program"
            if any(names):
                what += ": " + ", ".join(n for n in names if n)
            out.append(_row(f"program:{r['id']}", "program", r["name"], what, "ready", "",
                            "", how))
        else:
            out.append(_row(f"program:{r['id']}", "desktop", r["name"],
                            "Your own desktop program", "ready", "", "",
                            f'can open it, and open files in it: open_in_program("{r["name"]}", '
                            f"file)"))
    return out


def _knowledge_rows() -> list[dict]:
    """Books and libraries the build added to the knowledge base."""
    from puffpai.build import schema as S
    out = []
    for i in (S.load_build() or {}).get("knowledge") or []:
        if isinstance(i, dict) and i.get("status") == "added" and i.get("id"):
            out.append(_row(f"knowledge:{i['id']}", "knowledge", i.get("title"),
                            f"In the knowledge base, from {i.get('site') or 'a library'}",
                            "ready", "", "",
                            "searched by itself in chat when a question is about it"))
    return out


def _my_data_rows() -> list[dict]:
    """"My files" and each linked folder (``knowledge/my_data.py``)."""
    from puffpai.knowledge import my_data
    return [_row(f"knowledge:{r['id']}", "knowledge", r["name"], r["what"], "ready",
                 "", "", "searched by itself in chat when a question is about it")
            for r in my_data.installed_rows()]


def _safe(fn: Callable[[], list]) -> list:
    try:
        return list(fn() or [])
    except Exception as exc:  # noqa: BLE001 - one registry never hides the others
        logger.debug("installed: %s (%s)", getattr(fn, "__name__", "?"), type(exc).__name__)
        return []


def listing() -> list[dict]:
    """Every installed thing, apps first, one row each (see the module doc)."""
    try:
        run_rows, assembled = _run_here_rows()
    except Exception as exc:  # noqa: BLE001
        logger.debug("installed: run_here (%s)", type(exc).__name__)
        run_rows, assembled = [], {}
    rows = (_safe(_toolsmith_apps) + run_rows + _safe(lambda: _assembled_rows(assembled))
            + _safe(_tool_server_rows) + _safe(_box_cli_rows) + _safe(_program_rows)
            + _safe(_skill_rows)
            + _safe(_knowledge_rows) + _safe(_my_data_rows))
    out, seen = [], set()
    for r in rows:
        if r["id"] and r["id"] not in seen:
            seen.add(r["id"])
            out.append(r)
    return out


def line(row: dict) -> str:
    """One short prompt line: name (kind, status): what. How."""
    kind = {"tool_server": "tool server", "cli": "command line tool",
            "knowledge": "knowledge", "program": "your own program",
            "desktop": "your desktop program"}.get(row["kind"],
                                                                          row["kind"])
    state = "" if row["status"] == "ready" else f", {row['status']}"
    what = _plain(row.get("what"), PROMPT_WHAT)
    return (f"- {row['name']} ({kind}{state})" + (f": {what}" if what else "")
            + (f". Use: {row['brain']}" if row.get("brain") else ""))


def prompt_block(rows: Any = None) -> str:
    """The installed things for the system prompt, or "" when there are none."""
    rows = listing() if rows is None else list(rows)
    if not rows:
        return ""
    lines = [line(r) for r in rows[:PROMPT_MAX]]
    more = len(rows) - PROMPT_MAX
    if more > 0:
        lines.append(f"- {more} more, ask list_installed")
    return PROMPT_HEAD + "\n".join(lines)


LIST_SCHEMA = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": list(KINDS),
             "description": "Only this kind (optional)."}},
    "additionalProperties": False}


def _list_tool(args: dict, ctx: Any = None) -> Any:
    kind = str((args or {}).get("kind") or "")
    return [line(r) for r in listing() if not kind or r["kind"] == kind]


def register_tool() -> None:
    """``list_installed``: the whole list, for when the prompt held only part."""
    from puffpai import brain_tools
    brain_tools.register(
        "list_installed", "List everything installed on this computer (apps, skills, tool "
        "servers, command line tools), with how to use each.", LIST_SCHEMA, _list_tool,
        progress="Looking at what is installed ...")


def cli_commands() -> dict[str, str]:
    """``{name: full command}`` of every installed command line tool that
    run_command can run on this computer (run_here's, status ready), and the
    command line programs she added herself (``programs.cli_commands``)."""
    out: dict[str, str] = {}
    try:
        from puffpai.integrate import records, run_here
        for iid in run_here.all_ids():
            rec = records.get(iid)
            if rec and rec.get("status") == "ready":
                try:
                    out.update(run_here.cli_commands(iid))
                except Exception:  # noqa: BLE001 - one tool never hides the others
                    continue
    except Exception as exc:  # noqa: BLE001
        logger.debug("installed: command line tools (%s)", type(exc).__name__)
    try:
        from puffpai import programs
        for name, how in programs.cli_commands().items():
            out.setdefault(name, how)
    except Exception as exc:  # noqa: BLE001 - her list never hides the others
        logger.debug("installed: her own programs (%s)", type(exc).__name__)
    return out


__all__ = ["FIELDS", "KINDS", "PROMPT_MAX", "STATUSES", "cli_commands", "line", "listing",
           "prompt_block", "register_tool"]
