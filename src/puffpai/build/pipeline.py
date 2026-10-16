"""The one pipeline: interpret -> find -> classify -> decide -> check -> setup,
then ONE yes, then make -> prove per job in the background.

* :func:`plan` (the wizard, any goal) saves build.json with every job
  ``planned`` -> ``nothing_found`` / ``needs_setup`` / ``waiting_yes``.
* :func:`run_after_yes` makes and proves the waiting jobs, one at a time.
  The makers are other modules, imported only when called:
  ``puffpai.build.make.make(job, build) -> {"ok", "page", "reason"}`` and
  ``puffpai.build.prove.prove(job, build) -> {"state", "reason"}``; when
  they are not in this build a job stays "building: still being built".
* :func:`plan_more` (Find more): new candidates for one job, or new jobs
  from her words; the changed list needs its own single yes.

*ctx* (all optional): ``brain`` (``brain(messages, schema) -> str``),
``use_brain`` (False: word table and sim rules only), ``jobs`` (jobs she
already has, ``[{"name", "does"}]``: planned exactly), ``embed`` (texts ->
vectors; ``None`` matches by words), ``catalog`` (``(jobs, goal) -> [cand]``),
``check`` (``cand -> {"ok", "reason"}``), ``save`` (default True).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import importlib
import time
from typing import Any, Callable, Optional

from puffpai.build import decide as decide_mod
from puffpai.build import find as find_mod
from puffpai.build import interpret as interpret_mod
from puffpai.build import knowledge as knowledge_mod
from puffpai.build import schema as S
from puffpai.build import setup as setup_mod
from puffpai.build import yes as yes_mod
from puffpai.build.decide import cand_text
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

AGENT = "majordomo"
#: Rounds of check then decide again: an app that cannot start here is
#: left out and the next app that fits is checked.
CHECK_ROUNDS = 4
SKILL_MD_KEPT = 32 * 1024
TOOLSMITH_LATER = ("Toolsmith downloads it after your yes; Drawbridge checks its code before "
                   "any command runs, and it runs in its own folder.")
_MISSING = object()
#: What a part keeps in build.json (no skill text, no raw finds).
PART_KEYS = ("ref", "name", "type", "class", "source", "url", "what", "hosts", "needs",
             "service", "switch", "switch_on", "app_id", "folder", "mcp", "maybe_hosts",
             "caps", "auth", "builtin", "mode", "check", "tools", "remove",
             # check.py's card for a checked code project: make adds exactly it
             # after the Yes (route A for a fresh repo needs it).
             "prepared", "integration_id",
             # the live card her Yes made for a website from our own list
             "live_card",
             # what a website row says about its own answer (make's view)
             "sample_url", "image_template", "meanings", "about", "lookup_url",
             "time_base", "value_words", "topic",
             # her own GitHub project (github_account): where it is from, and
             # whether it is private (its own setup row, pulled after the Yes)
             "origin", "from_your_github", "private", "full_name", "own_project",
             "origin_words",
             # what the app list or code site says about how it runs
             "platforms", "listed", "stars", "pushed_at", "topics", "tags", "language",
             "demo_url",
             # a ready-made app found for its jobs by GitHub stars (build.apps):
             # the README's own run commands go on the Yes card
             "direct", "for_jobs", "job_rank", "readme_steps", "readme_runtime",
             "readme_port", "licences", "licence",
             # what its README says it needs beyond its own folder (the one
             # "Let Toolsmith set up" row her Yes covers)
             "beyond",
             # a folder of her own code on this computer, run in place
             "local_path")
#: What of a prepared card is kept (ids and hashes, never file contents).
PREPARED_KEYS = ("integration_id", "code_sha", "host_sha", "mode", "lines", "how", "needs")


def _say(text: str, *, status: str = "completed", agent: str = AGENT) -> None:
    try:
        from puffpai import agent_activity
        agent_activity.record(agent, text, status=status)
    except Exception:  # noqa: BLE001 - a line not kept never stops the build
        pass


def _optional(modname: str) -> Any:
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
        logger.warning("build: %s did not load (%s)", modname, type(exc).__name__)
        return None
    return got if callable(got) else None


def _ctx(ctx: Optional[dict]) -> dict:
    ctx = dict(ctx or {})
    ctx.setdefault("use_brain", True)
    if "brain" not in ctx:
        ctx["brain"] = interpret_mod.default_brain() if ctx["use_brain"] else None
    if "embed" not in ctx:
        ctx["embed"] = decide_mod.default_embed()
    ctx.setdefault("save", True)
    return ctx


def part_of(c: dict) -> dict:
    p = {k: c[k] for k in PART_KEYS if k in c}
    if isinstance(p.get("prepared"), dict):
        p["prepared"] = {k: p["prepared"][k] for k in PREPARED_KEYS if k in p["prepared"]}
    elif "prepared" in p:
        del p["prepared"]
    p["text"] = cand_text(c)
    f = c.get("facts")
    if isinstance(f, dict):
        p["facts"] = {k: f.get(k) for k in ("scripts", "programs", "declared", "packages",
                                            "keys", "hosts", "mcp_url", "openapi")}
    p["what"] = str(p.get("what") or "")[:300]
    if c.get("skill_md") and c.get("class") == S.TOOL:
        p["skill_md"] = str(c["skill_md"])[:SKILL_MD_KEPT]   # its setup rows, later too
    return p


def _toolsmith_app(c: dict) -> bool:
    try:
        from puffpai.build import make
        return make.toolsmith_app(c)
    except Exception:  # noqa: BLE001 - no maker here: checked now, as before
        return False


def check_all(jobs: list[dict], cands: list[dict], ctx: dict, decided: dict) -> dict:
    """Drawbridge and the apply checks on every part a job uses; a part that
    fails is left out and its job decided again."""
    check = ctx.get("check", _MISSING)
    if check is _MISSING:
        check = _seam("puffpai.build.check", "check")
    if check is None:
        return decided
    by_ref = {c["ref"]: c for c in cands}
    excluded: dict[str, str] = {}
    for _round in range(CHECK_ROUNDS):
        fresh = False
        for j in decided["jobs"]:
            for ref in j.get("ingredients") or []:
                c = by_ref.get(ref)
                if c is None or c.get("builtin") or "check" in c:
                    continue
                if c.get("direct") or _toolsmith_app(c):
                    # A ready-made app Toolsmith runs after the Yes: downloaded and
                    # checked by Drawbridge then, before any command.
                    c["check"] = {"ok": True, "later": True, "reason": TOOLSMITH_LATER}
                    continue
                try:
                    got = check(c) or {}
                except Exception as exc:  # noqa: BLE001 - a check that breaks is a no
                    got = {"ok": False, "reason": f"the check stopped ({type(exc).__name__})"}
                c["check"] = {"ok": bool(got.get("ok")), "reason": str(got.get("reason") or "")}
                if got.get("later"):
                    c["check"]["later"] = True    # downloaded and checked after the Yes
                prepared = got.get("prepared")
                if c["check"]["ok"] and isinstance(prepared, dict) \
                        and prepared.get("integration_id") and prepared.get("code_sha"):
                    c["prepared"] = {k: prepared.get(k) for k in PREPARED_KEYS
                                     if prepared.get(k) is not None}
                if not c["check"]["ok"]:
                    excluded[ref] = "Drawbridge said no: " + (c["check"]["reason"]
                                                              or "it did not pass the checks")
                    fresh = True
        if not fresh:
            break
        decided = decide_mod.decide(jobs, cands, brain=ctx.get("brain"), embed=ctx.get("embed"),
                                    excluded=excluded, judged=decided.get("judged"))
    # An app picked in the last round was never checked: it is not used
    # (make adds only what was checked or is checked after the Yes).
    unchecked = {ref: "BuildBot did not get to check it in time." for j in decided["jobs"]
                 for ref in j.get("ingredients") or []
                 if ref in by_ref and by_ref[ref].get("class") == S.APP
                 and "check" not in by_ref[ref] and not by_ref[ref].get("builtin")}
    if unchecked:
        excluded.update(unchecked)
        decided = decide_mod.decide(jobs, cands, brain=ctx.get("brain"), embed=ctx.get("embed"),
                                    excluded=excluded, judged=decided.get("judged"))
    return decided


def _keep_caps(goal: str, jobs: list[dict]) -> None:
    """The jobs are also the bot's capabilities (the brain's map, old pages)."""
    try:
        from puffpai.finding import capabilities
        capabilities.save(goal, [{"name": j["name"], "does": j.get("does", "")} for j in jobs])
        for j in jobs:
            j.setdefault("cap", capabilities.cap_id(j["name"]))
    except Exception as exc:  # noqa: BLE001
        logger.info("build: capabilities not kept (%s)", type(exc).__name__)


def _first_clause(text: str) -> str:
    import re
    head = re.split(r"[:;.!?]|, (?:and|so|but) ", " ".join(str(text or "").split()), maxsplit=1)[0]
    head = head.strip(" ,")
    return (head[:1].upper() + head[1:])[:120] if len(head.split()) >= 3 else ""


def honest_line(job: dict, by_ref: dict) -> None:
    """A job put together from data says what its sources really give (its
    pick's own words: "Stargazing forecast for your town"), and keeps what
    she asked for as ``wanted`` (the page shows both, with Find more)."""
    if job.get("route") != S.C:
        return
    clauses: list[str] = []
    for ref in job.get("ingredients") or []:
        c = by_ref.get(ref) or {}
        if c.get("class") != S.DATA or c.get("unusable"):
            continue
        clause = _first_clause(c.get("about") or c.get("what") or "")
        if clause and clause not in clauses:
            clauses.append(clause)
        if len(clauses) == 2:
            break
    if not clauses:
        return
    second = clauses[1] if len(clauses) > 1 else ""
    if second and not second[:2].isupper():
        second = second[:1].lower() + second[1:]
    line = clauses[0] + (f", and {second}" if second else "")
    if not job.get("wanted"):
        job["wanted"] = job.get("does", "")
    job["does"] = line


def assemble(goal: str, jobs: list[dict], cands: list[dict], ctx: dict,
             base: Optional[dict] = None, others: Any = ()) -> dict:
    """classify -> decide -> check -> setup over *jobs* and *cands* into a build.
    *others*: jobs kept as they are (Find more for one job); the setup list
    is made for all of them."""
    decided = decide_mod.decide(jobs, cands, brain=ctx.get("brain"), embed=ctx.get("embed"))
    decided = check_all(jobs, cands, ctx, decided)
    by_ref = {c["ref"]: c for c in cands}
    for j in decided["jobs"]:
        honest_line(j, by_ref)
    used = {r for j in decided["jobs"]
            for r in list(j.get("ingredients") or []) + list(j.get("alternates") or [])}
    parts = {c["ref"]: part_of(c) for c in cands if c["ref"] in used}
    build = dict(base) if base else S.empty_build(goal)
    build.update(goal=goal or build.get("goal", ""), made_at=int(time.time()),
                 match=decided["mode"])
    build["jobs"] = decided["jobs"]
    build["parts"] = {**(build.get("parts") or {}), **parts}
    build["dropped"] = [d for d in decided["dropped"] if d["ref"] not in build["parts"]]
    build["sims"] = decided["sims"]
    full = {**build["parts"], **{c["ref"]: c for c in cands if c["ref"] in build["parts"]}}
    rows = setup_mod.rows_for(list(others) + build["jobs"], full)
    build["setup"] = [r for r in setup_mod.merge(build.get("setup") or [], rows)
                      if r["id"] in {x["id"] for x in rows}]
    return build


def plan(goal: str, ctx: Optional[dict] = None) -> dict:
    """interpret -> find -> classify -> decide -> check -> setup; build.json saved."""
    ctx = _ctx(ctx)
    goal = " ".join(str(goal or "").split())[:600]
    _say("Working out the jobs for your bot.")
    if ctx.get("jobs"):
        # Jobs she already has (ticked on the console's jobs step, or kept by
        # the wizard): exactly those, never replaced by a fresh reading.
        jobs, how = interpret_mod.interpret_given(ctx["jobs"], goal, brain=ctx["brain"],
                                                  use_brain=ctx["use_brain"])
        # 1. the tools the goal needs: the jobs tied to her words, 1 to 3
        jobs = interpret_mod.focus(jobs, goal)
    else:
        jobs, how = interpret_mod.interpret(goal, brain=ctx["brain"],
                                            use_brain=ctx["use_brain"])
    if ctx["save"]:
        _keep_caps(goal, jobs)
    _say(f"Jobs: {', '.join(j['name'] for j in jobs)}.")
    cands = find_mod.find(jobs, goal, catalog=ctx.get("catalog"))
    _say(f"Looking at {len(cands)} finds.")
    build = assemble(goal, jobs, cands, ctx)
    build["interpreted_by"] = how
    # Books for the goal from the knowledge sites, on the same one Yes; what
    # an earlier build of this goal already added stays listed.
    if ctx["save"]:
        old = S.load_build()
        if old.get("goal") == goal:
            build["knowledge"] = [i for i in old.get("knowledge") or []
                                  if i.get("status") == knowledge_mod.ADDED]
    knowledge_mod.plan(build, jobs, goal)
    # Her first apps that fit the goal, turned on by the same one Yes.
    from puffpai import first_apps
    first_apps.plan(build, goal)
    yes_mod.reset(build)
    setup_mod.apply_statuses(build, only_open=False)
    if ctx["save"]:
        build = S.save_build(build)
    counts = {r: sum(1 for j in build["jobs"] if j["route"] == r) for r in S.ROUTES}
    _say(f"Planned: {counts['A']} used as they are, {counts['B']} connected, "
         f"{counts['C']} to put together, {counts['D']} with nothing found yet; "
         f"{len(build['dropped'])} not used.")
    return build


# ── after the yes ──────────────────────────────────────────────────────────

def _set_job(jid: str, **changes: Any) -> dict:
    def change(b: dict) -> None:
        job = S.find_job(b, jid)
        if job is not None:
            job.update(changes)
    return S.update_build(change)


def run_job(jid: str) -> dict:
    """Make and prove one job (it must be covered by her yes). Its new state."""
    build = S.load_build()
    job = S.find_job(build, jid)
    if job is None:
        return {"state": "", "reason": "there is no such job"}
    if job.get("route") == S.D:
        return {"state": S.NOTHING_FOUND, "reason": S.NOTHING_FOUND_WHY}
    if not yes_mod.covers(build):
        if job.get("status") in (S.WORKING, S.NOT_YET):
            # built under an earlier yes: it stays as it is until the new list's yes
            return {"state": job["status"], "reason": "waiting for your yes on the new list"}
        _set_job(jid, status=S.WAITING_YES, reason="")
        return {"state": S.WAITING_YES, "reason": ""}
    also = setup_mod.pending_also(job, build.get("setup") or [])
    if also:
        # A site it turned out to need waits on the list: only this job waits.
        reason = setup_mod.also_reason(also)
        _set_job(jid, status=S.NEEDS_SETUP, reason=reason)
        return {"state": S.NEEDS_SETUP, "reason": reason}
    _set_job(jid, status=S.BUILDING, reason="")
    _say(f"Building: {job['name']} (putting it together; this can take a few minutes)")
    make = _seam("puffpai.build.make", "make")
    prove = _seam("puffpai.build.prove", "prove")
    if make is None or prove is None:
        _set_job(jid, status=S.BUILDING, reason=S.STILL_BUILDING)
        return {"state": S.BUILDING, "reason": S.STILL_BUILDING}
    try:
        made = make(dict(job), S.load_build()) or {}
    except Exception as exc:  # noqa: BLE001 - one job never stops the rest
        # Kept in bot.log with its reason (the job's id and the error type and
        # words only: no keys, no addresses with secrets are part of these).
        logger.warning("build: making %s stopped: %s: %s", jid, type(exc).__name__,
                       str(exc)[:200], exc_info=True)
        made = {"ok": False, "reason": f"making it stopped ({type(exc).__name__})"}
    if not made.get("ok") and made.get("stopped"):
        # She added a project to this job to run first: this try ended and
        # the job's word is what her add set (it waits for her yes, then runs).
        _say(f"{job['name']}: {made.get('reason') or 'stopped'}")
        now = S.find_job(S.load_build(), jid) or {}
        return {"state": str(now.get("status") or ""), "reason": str(made.get("reason") or "")}
    if not made.get("ok") and made.get("waiting_yes"):
        # Toolsmith asks the owner first (a row on the setup list, one yes).
        reason = str(made.get("reason") or "waiting for your approval")
        _set_job(jid, status=S.WAITING_YES, reason=reason)
        _say(f"{job['name']}: {reason}")
        return {"state": S.WAITING_YES, "reason": reason}
    if not made.get("ok") and made.get("vault_wait"):
        # Toolsmith's key is locked away: the job waits, and goes on by itself
        # when she opens the vault (build/api.py, toolsmith_cloud.on_resume).
        reason = str(made.get("reason") or "")
        _set_job(jid, status=S.WAITING_YES, reason=reason)
        _say(f"{job['name']}: {reason}")
        return {"state": S.WAITING_YES, "reason": reason}
    if not made.get("ok"):
        reason = _slow_or(jid, str(made.get("reason") or "it could not be made"))
        _set_job(jid, status=S.NOT_YET, reason=reason)
        # "Not yet" is an outcome with its reason, not an error of the team.
        _say(f"{job['name']}: not yet: {reason}")
        return {"state": S.NOT_YET, "reason": reason}
    if made.get("page"):
        _set_job(jid, made={"page": str(made["page"])})
    shown = (made.get("screen") or {}).get("view") if isinstance(made.get("screen"), dict) else ""
    if job.get("route") == S.C and shown in S.MADE_VIEWS and shown != job.get("view"):
        # Its data decided the view (a forecast, not a gallery): said as it is.
        _set_job(jid, view=shown, view_asked=job.get("view") or "")
    try:
        got = prove(dict(S.find_job(S.load_build(), jid) or job), S.load_build()) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("build: checking %s stopped: %s: %s", jid, type(exc).__name__,
                       str(exc)[:200], exc_info=True)
        got = {"state": S.NOT_YET, "reason": f"the check stopped ({type(exc).__name__})"}
    state = S.WORKING if got.get("state") == S.WORKING else S.NOT_YET
    reason = "" if state == S.WORKING else _slow_or(jid, str(got.get("reason") or "it did not answer a real ask"))
    _set_job(jid, status=state, reason=reason)
    if state == S.WORKING:
        _set_job(jid, retries=0)
    if job.get("route") == S.C:
        _keep_report(made, state, reason)
    _say(f"{job['name']}: working" if state == S.WORKING else f"{job['name']}: not yet: {reason}")
    return {"state": state, "reason": reason}


def _keep_report(made: dict, state: str, reason: str) -> None:
    """The put-together app's row says what the proof found (chat offers a
    working app's tools only)."""
    rep = ((made.get("made") or {}).get("report") or {}) if isinstance(made, dict) else {}
    if not rep.get("cap"):
        return
    try:
        from puffpai.assemble import report
        if (report.by_cap().get(rep["cap"]) or {}).get("state") == state:
            return
        report.update(report.entry(rep, report.WORKING if state == S.WORKING
                                   else report.NOT_YET, reason,
                                   app_id=str(rep.get("app_id") or ""),
                                   decision=str(rep.get("decision") or "")))
    except Exception as exc:  # noqa: BLE001 - the job's own status stands
        logger.info("build: report not kept (%s)", type(exc).__name__)


#: A reason that is a slow or busy outside service, not a verdict.
_SLOW_RE = None
#: Minutes before each automatic re-check of a job a slow service held back.
RETRY_MINUTES = (5, 15, 30)


def _is_slow(reason: str) -> bool:
    import re
    global _SLOW_RE
    if _SLOW_RE is None:
        _SLOW_RE = re.compile(r"did not answer [(]|answered (429|5[0-9][0-9])|timed? ?out", re.I)
    return bool(_SLOW_RE.search(reason or ""))


def _slow_or(jid: str, reason: str) -> str:
    """One slow answer is never a final verdict: the job is checked again by
    itself a few minutes later (5, 15, then 30), and says so."""
    if not _is_slow(reason):
        return reason
    job = S.find_job(S.load_build(), jid) or {}
    n = int(job.get("retries") or 0)
    text = reason[len("Not yet: "):] if reason.startswith("Not yet: ") else reason
    service = text.split(": ", 1)[0] if ": " in text else "An outside service"
    if n >= len(RETRY_MINUTES):
        return (f"{service} did not answer after several tries. Check again later; the "
                "rest of your bot works without it.")
    minutes = RETRY_MINUTES[n]
    _set_job(jid, retries=n + 1)
    _schedule_recheck(jid, minutes * 60)
    logger.info("build: %s held back by a slow service; again in %s min", jid, minutes)
    return f"{service} was slow to answer, so BuildBot tries again in {minutes} minutes."


#: Tests replace this (``(job id, seconds) -> None``).
_recheck_scheduler: Optional[Callable[[str, float], None]] = None


def _schedule_recheck(jid: str, seconds: float) -> None:
    if _recheck_scheduler is not None:
        _recheck_scheduler(jid, seconds)
        return
    import threading

    def again() -> None:
        build = S.load_build()
        job = S.find_job(build, jid)
        if job is None or job.get("status") != S.NOT_YET or not yes_mod.covers(build):
            return
        try:
            from puffpai.build import api as build_api
            build_api.enqueue([jid])
        except Exception:  # noqa: BLE001
            logger.exception("build: re-check of %s not started", jid)
    timer = threading.Timer(seconds, again)
    timer.daemon = True
    timer.start()


def waiting_jobs(build: Optional[dict] = None) -> list[str]:
    build = build or S.load_build()
    return [j["id"] for j in build["jobs"] if j.get("status") == S.WAITING_YES]


def run_after_yes(job_ids: Optional[list[str]] = None) -> list[dict]:
    """Make and prove the waiting jobs (or *job_ids*), one at a time."""
    out = []
    for jid in job_ids if job_ids is not None else waiting_jobs():
        out.append({"id": jid, **run_job(jid)})
    return out


# ── Find more ──────────────────────────────────────────────────────────────

def _as_cands(build: dict) -> list[dict]:
    return [dict(p) for p in (build.get("parts") or {}).values()]


#: The outside service a pasted GitHub address is read through.
GITHUB_SWITCH = "code_search"


class SwitchOff(ValueError):
    """A step needs an outside service that is switched off. The answer
    offers its switch (her yes), the same way any switch is offered."""

    def __init__(self, service: str, message: str):
        super().__init__(message)
        self.service = service


def _is_github(url: str) -> bool:
    import re
    from puffpai.build import apps
    return bool(apps._github_of(re.sub(r"[#?].*$", "", url.strip())))  # noqa: SLF001


def _switched_on(service: str) -> bool:
    try:
        from puffpai import outside_services
        row = outside_services.row(service)
        return bool(row) and row.get("state") == "on"
    except Exception:  # noqa: BLE001 - unknown reads as off
        return False


def plan_more(target: Any, ctx: Optional[dict] = None) -> dict:
    """Find more for one job (*target* is its id) or new jobs from her words."""
    ctx = _ctx(ctx)
    build = S.load_build()
    old_sha = yes_mod.sha(build)
    goal = build.get("goal", "")
    job = S.find_job(build, target) if isinstance(target, str) else None
    if job is None and isinstance(target, dict):
        job = S.find_job(build, target.get("id"))
    if job is not None:
        jobs = [job]
        others = [j for j in build["jobs"] if j["id"] != job["id"]]
    else:
        jobs, _how = interpret_mod.interpret(str(target or ""), brain=ctx["brain"],
                                             use_brain=ctx["use_brain"],
                                             taken=[j["id"] for j in build["jobs"]])
        others = list(build["jobs"])
    known = _as_cands(build)
    pasted = ""     # the job given new code to make an app from
    if ctx.get("url") and job is not None:
        # A pasted project address: read the same way (its README's run
        # steps), first for this job; the changed list needs her one Yes.
        got_url = ctx.get("from_url")
        if got_url is None:
            from puffpai.build import apps
            got_url = apps.from_url
        cand = got_url(str(ctx["url"]))
        if not cand and _is_github(str(ctx["url"])) and not _switched_on(GITHUB_SWITCH):
            # GitHub access is off: offer the switch (her yes), never a refusal.
            raise SwitchOff(GITHUB_SWITCH, "GitHub access is switched off, so BuildBot "
                            "cannot read that project yet. Switch it on, then add the "
                            "address again.")
        if not cand:
            raise ValueError("BuildBot could not read that project. Give a GitHub project "
                             "address, like https://github.com/owner/project.")
        # Pasting a project is "try this": off the job's tried list (by its
        # ref or GitHub owner/repo), and the job waits for her Yes again.
        from puffpai.build import make as make_mod
        make_mod.drop_tried(job, cand, build.get("parts") or {})
        pasted = job["id"]
        if str(cand.get("ref") or "").startswith("code:"):
            # Code, not a whole app: first in the job's code, so Toolsmith
            # makes an app from it when no ready-made app runs (her one Yes).
            from puffpai.build.apps import CODE_MAX
            job["code"] = ([cand] + [c for c in job.get("code") or []
                                     if isinstance(c, dict) and c.get("ref") != cand["ref"]]
                           )[:CODE_MAX]
            fresh = []
        else:
            cand.update(for_jobs=[job["id"]], job_rank={job["id"]: -1})
            known = [c for c in known if c["ref"] != cand["ref"]]
            fresh = [cand]
    else:
        fresh = find_mod.find(jobs, goal, catalog=ctx.get("catalog"))
    seen = {c["ref"] for c in known}
    cands = known + [c for c in fresh if c["ref"] not in seen]
    before = {j["id"]: (j.get("route"), (j.get("pick") or {}).get("ref"),
                        list(j.get("ingredients") or [])) for j in jobs}
    old_dropped = [d for d in build.get("dropped") or []]
    got = assemble(goal, jobs, cands, ctx, base=build, others=others)
    for j in got["jobs"]:
        if j["id"] == pasted or before.get(j["id"]) != (
                j.get("route"), (j.get("pick") or {}).get("ref"),
                list(j.get("ingredients") or [])):
            j["status"] = S.PLANNED
    got["jobs"] = others + got["jobs"]
    order = {j["id"]: n for n, j in enumerate(build["jobs"])}
    got["jobs"].sort(key=lambda j: order.get(j["id"], len(order)))
    seen_drop = {d["ref"] for d in got["dropped"]}
    got["dropped"] = got["dropped"] + [d for d in old_dropped if d["ref"] not in seen_drop
                                       and d["ref"] not in got["parts"]]
    if not ctx.get("url"):
        knowledge_mod.plan(got, jobs, goal)     # books for these jobs too
    if yes_mod.sha(got) != old_sha:
        yes_mod.reset(got)
    for j in got["jobs"]:
        if j.get("status") == S.PLANNED:
            j["status"] = setup_mod.job_status(j, got["setup"])
    if pasted:
        # Where her project goes: "first" (Run it first) or "list" (after
        # the job's apps not yet tried); kept on the job.
        from puffpai.build import make as make_mod
        order = ctx.get("order") if ctx.get("order") in make_mod.HER_ORDERS else "first"
        for j in got["jobs"]:
            if j["id"] == pasted:
                j["her_order"] = order
    if ctx["save"]:
        def keep(b: dict) -> None:
            # A job added while this ran (the Add page's own job) stays.
            added = [j for j in b["jobs"] if S.find_job(got, j.get("id")) is None]
            b.clear()
            b.update(got, jobs=got["jobs"] + added)
        got = S.update_build(keep)
    _say(f"Found more: "
         f"{', '.join(j['name'] + ' (' + S.how_words(j, got['parts']) + ')' for j in jobs)}.")
    return got


def _project_words(text: Any, limit: int) -> list[str]:
    import re
    from puffpai.build import apps
    return [w for w in apps.words_of(re.sub(r"[-_.]+", " ", str(text or "")), limit=limit)
            if re.fullmatch(r"[a-z0-9'-]{2,30}", w)]


def fits_project(build: dict, name: Any, what: Any, cap: str = "") -> Optional[dict]:
    """The job of this bot a code project clearly fits: the one she was
    finding something for (*cap*), else the job sharing the most words (two
    or more) with its name and line. None when no job clearly does."""
    jobs = build.get("jobs") or []
    if cap:
        hit = next((j for j in jobs if cap in (j.get("id"), j.get("cap"))), None)
        if hit is not None:
            return hit
    have = set(_project_words(f"{name} {what}", 40))
    best, most = None, 1
    for j in jobs:
        want = set(_project_words(" ".join([str(j.get("name") or ""), str(j.get("does") or ""),
                                            " ".join(j.get("subject") or [])]), 40))
        if len(have & want) > most:
            best, most = j, len(have & want)
    return best


def project_job_name(name: Any, what: Any) -> str:
    """"<name>: <its one-line description>", at most 80 letters."""
    name = " ".join(str(name or "").split())[:60] or "A project"
    line = " ".join(str(what or "").strip().splitlines()[0].split()) if str(what or "").strip()         else ""
    full = f"{name}: {line.rstrip('.')}" if line else name
    if len(full) > 80:
        full = full[:80].rsplit(" ", 1)[0].rstrip(" ,;:")
    return full if len(full) >= 2 else f"{full} app"


def plan_project(url: str, name: Any, what: Any, job_id: str = "",
                 ctx: Optional[dict] = None) -> tuple[dict, str, bool]:
    """A code project she adds (the Add page) into the same loop as Find
    more: to *job_id* when given, else to ONE new job named after it (route A,
    her project first). Either way it waits for her one Yes. Returns (build,
    the job's id, whether the job is new); nothing is kept when the project
    cannot be read."""
    ctx = dict(ctx or {}, url=url)
    if job_id:
        return plan_more(job_id, ctx), job_id, False
    build = S.load_build()
    one_line = " ".join(str(what or "").split())[:300]
    job = S.new_job(project_job_name(name, what), one_line,
                    subject=_project_words(f"{name} {what}", 3) or ["app"],
                    taken=[j["id"] for j in build["jobs"]])
    S.update_build(lambda b: b["jobs"].append(job))
    try:
        got = plan_more(job["id"], ctx)
    except Exception:
        S.update_build(lambda b: b.update(jobs=[j for j in b["jobs"] if j["id"] != job["id"]]))
        raise
    return got, job["id"], True


__all__ = ["assemble", "check_all", "fits_project", "part_of", "plan", "plan_more",
           "plan_project", "project_job_name", "run_after_yes", "run_job", "waiting_jobs"]
