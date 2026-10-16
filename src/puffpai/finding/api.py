"""Routes for finding and installing things: the wizard's "Finding things for
your bot" screen and the Add page use the same ones.

A blueprint, registered with the other blueprints in ``api_server``, so the
routes sit behind the same key check as everything else; the dashboard reaches
them through its paired proxy under the ``add`` and ``wizard`` prefixes it
already allows. Find ids travel in the request body, never in an address.

Routes::

    POST /add/things/find             {"words", "kinds"?, "cap"?} -> {"job"} (words may
                                      be a pasted link to one project; cap: one of the
                                      bot's capabilities, "Find something for this")
    GET  /add/things/jobs/<job>       lines so far, done, and the result
    POST /add/things/preview          {"id"} -> what it is, what it runs, the check
    POST /add/things/install          {"ids": [...]} -> {"job"}
    GET  /add/things/installed        what was installed, newest first
    POST /add/things/installed/<entry>/undo
    GET  /wizard/find                 the wizard's search for this run's goal, and
                                      the bot's capabilities once worked out
    POST /wizard/find                 {"install": [ids]} or {"done": true}

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from flask import Blueprint, jsonify, request

from puffpai.finding import capabilities, find, install, jobs, tools
from puffpai.finding import wording as W

bp = Blueprint("finding", __name__)
tools.register_all()

MAX_IDS = 30
FIND_ENTRY = "finding.things"
_WIZ_LOCK = threading.Lock()
#: Per wizard run (keyed by when it started): its search job and install job.
_WIZ: dict[str, dict[str, str]] = {}


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for v in value[:MAX_IDS]:
        if isinstance(v, str) and 0 < len(v) <= 200 and v not in out:
            out.append(v)
    return out


def _job_view(job_id: str) -> dict:
    st = jobs.status(job_id) or {"id": job_id, "lines": [], "done": True, "result": None,
                                 "error": "That search is gone. Look again."}
    return {"job": st["id"], "kind": st.get("kind", ""), "lines": st["lines"],
            "done": st["done"], "error": st["error"], "result": st["result"]}


def install_many(ids: list[str], say, via: str) -> dict:
    results = []
    for fid in ids:
        results.append({"id": fid, **install.install_thing(fid, progress=say, via=via)})
    if not ids:
        say(W.NOTHING_TICKED)
    return {"results": [{k: r.get(k) for k in ("id", "status", "line", "entry", "page")}
                        for r in results]}


@bp.route("/add/things/find", methods=["POST"])
def things_find():
    body = request.get_json(silent=True) or {}
    words = " ".join(str(body.get("words") or "").split())[:300]
    if not words:
        return jsonify({"success": False, "message": "Say what to look for."}), 400
    kinds = body.get("kinds") if isinstance(body.get("kinds"), list) else None
    cap = str(body.get("cap") or "")[:80]
    if cap and capabilities.get(cap) is not None:
        # One capability's search: her words, tagged, never a new plan.
        return jsonify({"success": True, "job": jobs.start(
            "find", lambda say: find.find_things(words, kinds, progress=say, cap=cap))})
    if find.is_link(words):
        # A pasted link: the one project it names, with the same Add and yes.
        return jsonify({"success": True, "job": jobs.start(
            "find", lambda say: find.find_from_link(words, progress=say))})
    goal = bool(body.get("goal", len(words.split()) > 3))
    work = (lambda say: find.find_for_goal(words, kinds=kinds, progress=say)) if goal else \
        (lambda say: find.find_things(words, kinds, progress=say))
    return jsonify({"success": True, "job": jobs.start("find", work)})


@bp.route("/add/things/jobs/<job_id>", methods=["GET"])
def things_job(job_id: str):
    if not jobs.JOB_RE.match(job_id):
        return jsonify({"success": False, "message": "There is no job by that name."}), 404
    return jsonify({"success": True, **_job_view(job_id)})


@bp.route("/add/things/preview", methods=["POST"])
def things_preview():
    body = request.get_json(silent=True) or {}
    return jsonify({"success": True, **install.preview(body.get("id"))})


@bp.route("/add/things/install", methods=["POST"])
def things_install():
    body = request.get_json(silent=True) or {}
    ids = _ids(body.get("ids"))
    job = jobs.start("install", lambda say: install_many(ids, say, "add_page"))
    return jsonify({"success": True, "job": job})


@bp.route("/add/things/installed", methods=["GET"])
def things_installed():
    return jsonify({"success": True, "installed": install.installed()})


@bp.route("/add/things/installed/<entry>/undo", methods=["POST"])
def things_undo(entry: str):
    out = install.undo(entry)
    return jsonify({"success": bool(out.get("ok")), **out}), (200 if out.get("ok") else 404)


# ── the wizard's step ───────────────────────────────────────────────────────

def _run():
    from puffpai.wizard import service
    cfg, args = service._cfg(), service._args()  # noqa: SLF001
    return service, cfg, args, service._saved(cfg, args)  # noqa: SLF001


def wizard_state() -> dict:
    """Start (once per wizard run) the search for this run's goal; say how far it got."""
    service, cfg, _args, run = _run()
    key = service._run_key(run)  # noqa: SLF001
    goal = service._goal_of(run.shared, run.sections)  # noqa: SLF001
    with _WIZ_LOCK:
        mine = _WIZ.setdefault(key, {})
        if not mine.get("find") or jobs.status(mine["find"]) is None:
            mine["find"] = jobs.start(
                "find", lambda say: find.find_for_goal(goal, cfg=cfg, progress=say,
                                                       keep=True))
        find_job, install_job = mine.get("find"), mine.get("install")
    out = {"title": W.FINDING_TITLE, "intro": W.FINDING_INTRO,
           "find": _job_view(find_job),
           "install": _job_view(install_job) if install_job else None,
           "settled": run.entries.get(FIND_ENTRY, {}).get("outcome") in ("done", "skipped")}
    if out["find"].get("done"):
        out["capabilities"] = capabilities.coverage()
    return out


def settle(outcome: str = "done") -> bool:
    """Mark the wizard's find step done, so the plan comes next. The
    needs step is parked: it is settled as skipped with it."""
    from puffpai.wizard import service

    def change(run) -> None:
        now = time.time()
        run.entries[FIND_ENTRY] = {"outcome": outcome, "at": now}
        run.log.append({"entry": FIND_ENTRY, "skill": "", "part": "find",
                        "outcome": outcome, "started_at": now, "finished_at": now,
                        "note": "finding things for the bot", "refusal": ""})
        if run.entries.get(service.NEEDS_ENTRY, {}).get("outcome") not in (
                "done", "skipped", "not_built"):
            service._settle_needs(run, "skipped",  # noqa: SLF001
                                  "parked: Finding things for your bot replaced it")
    return service._update_run(service._cfg(), service._args(), change)  # noqa: SLF001


@bp.route("/wizard/find", methods=["GET"])
def wizard_find():
    return jsonify({"success": True, **wizard_state()})


@bp.route("/wizard/find", methods=["POST"])
def wizard_find_choose():
    body = request.get_json(silent=True) or {}
    if body.get("done"):
        find.switch_on_shelves(by="wizard")
        ok = settle("done" if install.entries() else "skipped")
        if not ok:
            return jsonify({"success": False,
                            "message": "The wizard is busy. Try again in a moment."}), 409
        return jsonify({"success": True, "settled": True})
    ids = _ids(body.get("install"))
    service, _cfg, _args, run = _run()
    key = service._run_key(run)  # noqa: SLF001
    job = jobs.start("install", lambda say: install_many(ids, say, "wizard"))
    with _WIZ_LOCK:
        _WIZ.setdefault(key, {})["install"] = job
    return jsonify({"success": True, "install": _job_view(job)})


__all__ = ["FIND_ENTRY", "bp", "install_many", "settle", "wizard_state"]
