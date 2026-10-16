"""Migrate: a bot built before build.json gets one, from what it already has.

Inputs (all read, none changed or deleted):

* ``finding/capabilities.json`` (else ``assemble/plan.json``): the jobs, by
  the word table (:func:`puffpai.build.interpret.from_words`);
* ``finding/installed.json``: skills (their SKILL.md read for how they run),
  books, tool servers (with their tools from ``mcp_servers.json``), apps
  (with their integration record: one Drawbridge refused is left out);
* ``apps/*/app.json`` with a feed: the live cards, kept as data sources with
  their switch as it is;
* ``assemble/<id>/spec.json``: an assembled app stays with its capability.

Then the same decide step as a new build. A thing no job uses is listed under
"Not used" (``dropped``) with the install entry that removes it; nothing is
removed here. Run once, when build.json is missing (:func:`ensure`).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Optional

from puffpai import fsutil
from puffpai.build import interpret as interpret_mod
from puffpai.build import schema as S
from puffpai.build import setup as setup_mod
from puffpai.build import yes as yes_mod
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

FILES_MAX = 60


def _state(*parts: str) -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), *parts)


def _read(*parts: str) -> Any:
    return fsutil.read_json(_state(*parts), default=None)


def old_jobs(brain: Any = None) -> tuple[str, list[dict]]:
    """``(goal, jobs)`` from the kept capabilities (or the old Assemble plan).
    With a *brain* each job's fields (action, output, needs, subject) are
    picked by it from the fixed lists, her names kept; else the word table."""
    data = _read("finding", "capabilities.json")
    rows = data.get("capabilities") if isinstance(data, dict) else None
    goal = str(data.get("goal") or "") if isinstance(data, dict) else ""
    if not isinstance(rows, list) or not rows:
        plan = _read("assemble", "plan.json")
        rows = [{"id": r.get("cap"), "name": r.get("name"), "does": r.get("does")}
                for r in (plan.get("capabilities") or [] if isinstance(plan, dict) else [])
                if isinstance(r, dict)]
    rows = [r for r in rows if isinstance(r, dict) and r.get("name")] \
        if isinstance(rows, list) else []
    jobs: list[dict] = []
    if brain is not None and rows:
        got, how = interpret_mod.interpret_given(
            [{"name": str(r["name"]), "does": str(r.get("does") or "")} for r in rows],
            goal, brain=brain, use_brain=True)
        if how != "given" and len(got) == len(rows):
            for job, r in zip(got, rows):
                if r.get("id"):
                    job["cap"] = str(r["id"])
            return goal, got
    for r in rows:
        job = interpret_mod.from_words(str(r["name"]), str(r.get("does") or ""),
                                       taken=[j["id"] for j in jobs], cap=str(r.get("id") or ""))
        jobs.append(job)
    return goal, jobs


def _skill(entry: dict, c: dict) -> None:
    folder = str((entry.get("undo") or {}).get("folder") or "")
    if not folder:
        return
    root = _state("skills", folder)
    try:
        with open(os.path.join(root, "SKILL.md"), encoding="utf-8", errors="replace") as fh:
            c["skill_md"] = fh.read(256 * 1024)
    except OSError:
        return
    files = []
    for base, _dirs, names in os.walk(root):
        for n in names:
            files.append(os.path.relpath(os.path.join(base, n), root).replace("\\", "/"))
            if len(files) >= FILES_MAX:
                break
    c["files"] = files
    c["folder"] = folder
    head = c["skill_md"].split("---", 2)
    if not c.get("what") or c["what"].startswith("A skill in the "):
        # its own description says what it is better than the shelf did
        m = re.search(r"(?im)^description:\s*\"?(.+?)\"?\s*$", head[1] if len(head) > 2 else "")
        if m:
            c["what"] = m.group(1)[:400]


def _mcp(entry: dict, c: dict, servers: dict) -> None:
    pinned = str(entry.get("pinned") or "")
    for s in servers.values():
        if not isinstance(s, dict):
            continue
        if s.get("url") == pinned or s.get("source") == entry.get("from") \
                or s.get("label") == entry.get("name"):
            c["mcp"] = {"url": s["url"]} if s.get("url") else {"command": str(s.get("command") or "")}
            c["tools"] = [{"name": t.get("name"), "description": t.get("description")}
                          for t in s.get("tools") or [] if isinstance(t, dict)][:20]
            c["installed_tool"] = s.get("name")
            return
    if pinned.startswith("http"):
        c["mcp"] = {"url": pinned}


def _app(entry: dict, c: dict, excluded: dict) -> None:
    integ = str((entry.get("undo") or {}).get("integration") or "")
    rec = _read("integrations", integ, "record.json") if integ else None
    chk = _read("integrations", integ, "check.json") if integ else None
    if isinstance(rec, dict):
        c["mode"] = str(rec.get("mode") or "")
        if rec.get("status") == "refused":
            excluded[c["ref"]] = "Drawbridge said no."
    ids = (chk or {}).get("app_ids") if isinstance(chk, dict) else None
    if isinstance(ids, list) and ids:
        c["app_id"] = str(ids[0])


def installed_cands(excluded: dict) -> list[dict]:
    entries = _read("finding", "installed.json")
    servers = (_read("mcp_servers.json") or {}).get("servers") or {}
    out = []
    for e in entries if isinstance(entries, list) else []:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        kind = str(e.get("type") or "")
        c = {"ref": str(e["id"]), "name": str(e.get("name") or e["id"]), "type": kind,
             "what": str(e.get("what") or ""), "source": "installed",
             "url": str(e.get("from") or ""), "hosts": [], "needs": [],
             "caps": [str(x) for x in e.get("caps") or [] if isinstance(x, str)],
             "remove": str(e.get("entry") or "")}
        if kind == "skill":
            _skill(e, c)
        elif kind == "mcp":
            _mcp(e, c, servers)
        elif kind in ("browser_app", "server_app", "app", "cli", "library", "data"):
            _app(e, c, excluded)
        out.append(c)
    return out


def feed_cands() -> list[dict]:
    doors = (_read("egress_app_doors.json") or {}).get("doors") or {}
    on = {str(d.get("app")) for d in doors.values() if isinstance(d, dict) and d.get("open")}
    out = []
    root = _state("apps")
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        m = _read("apps", name, "app.json")
        if not isinstance(m, dict) or not isinstance(m.get("feed"), dict):
            continue
        feed = m["feed"]
        host = str((feed.get("service") or {}).get("host") or "")
        words = " ".join(str(w) for w in m.get("about_words") or [])
        out.append({"ref": f"app:{m.get('id') or name}", "name": str(m.get("name") or name),
                    "type": "feed", "what": str(m.get("shows") or m.get("blurb") or ""),
                    "text": f"{m.get('name', '')}. {m.get('shows') or m.get('blurb') or ''} "
                            f"{words}".strip(),
                    "source": "live card", "url": str(feed.get("url") or ""),
                    "hosts": [host] if host else [], "needs": [],
                    "service": f"app:{m.get('id') or name}:feed", "app_id": str(m.get("id") or name),
                    "switch": True, "switch_on": str(m.get("id") or name) in on})
    return out


def assembled_cands() -> list[dict]:
    out = []
    root = _state("assemble")
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        spec = _read("assemble", name, "spec.json")
        if isinstance(spec, dict) and spec.get("id"):
            out.append({"ref": f"assembled:{spec['id']}", "name": str(spec.get("title") or name),
                        "type": "assembled", "what": str(spec.get("title") or ""),
                        "source": "assembled", "hosts": [], "needs": [],
                        "caps": [str(c) for c in spec.get("caps") or []]})
    return out


def migrate(ctx: Optional[dict] = None, *, save: bool = True) -> dict:
    """build.json from the old files (never deletes anything)."""
    from puffpai.build import pipeline
    ctx = dict(ctx or {})
    ctx.setdefault("use_brain", False)
    ctx.setdefault("check", None)     # what is here already passed its checks
    ctx = pipeline._ctx(ctx)  # noqa: SLF001
    goal, jobs = old_jobs(ctx.get("brain") if ctx.get("use_brain") else None)
    excluded: dict[str, str] = {}
    cands = installed_cands(excluded) + feed_cands() + assembled_cands()
    from puffpai.build import decide as decide_mod
    decided = decide_mod.decide(jobs, cands, brain=ctx.get("brain"), embed=ctx.get("embed"),
                                excluded=excluded)
    used = {r for j in decided["jobs"] for r in j.get("ingredients") or []}
    build = S.empty_build(goal)
    build.update(made_at=int(time.time()), match=decided["mode"],
                 migrated=True, jobs=decided["jobs"], sims=decided["sims"],
                 parts={c["ref"]: pipeline.part_of(c) for c in cands if c["ref"] in used})
    by_ref = {c["ref"]: c for c in cands}
    build["dropped"] = [{**d, **({"remove": by_ref[d["ref"]]["remove"]}
                                 if by_ref.get(d["ref"], {}).get("remove") else {})}
                        for d in decided["dropped"]]
    build["setup"] = setup_mod.rows_for(build["jobs"], build["parts"])
    yes_mod.reset(build)
    setup_mod.apply_statuses(build, only_open=False)
    if save:
        build = S.save_build(build)
    return build


def has_old() -> bool:
    return any(os.path.isfile(_state(*p)) for p in (("finding", "capabilities.json"),
                                                     ("finding", "installed.json"),
                                                     ("assemble", "plan.json")))


def ensure(ctx: Optional[dict] = None) -> dict:
    """build.json as it is; made from the old files once when it is missing."""
    if S.exists():
        return S.load_build()
    if not has_old():
        return S.empty_build()
    try:
        return migrate(ctx)
    except Exception as exc:  # noqa: BLE001 - an old bot still opens
        logger.warning("build: migration did not finish (%s)", type(exc).__name__)
        return S.empty_build()


__all__ = ["assembled_cands", "ensure", "feed_cands", "has_old", "installed_cands", "migrate",
           "old_jobs"]
