"""Build routes (a blueprint, paired devices only; GET never starts anything)::

    GET  /build/status        {"ready", "jobs": [Job], "setup": [Row],
                               "parts": [{"ref", "name", "class", "kind"}],
                               "yes": {"given", "at", "sha"}, "sites": [...],
                               "dropped": [{"ref", "name", "why"}], "running",
                               "lines": [{"agent", "text"}], "words": WORDS, "bot"}
                              ("ready": False + "line" while the start-up
                               migration runs; nothing is made from here)
    POST /build/yes           {"sha"} her one yes for exactly the list with that
                               sha: the whole setup list (the outside sites it
                               shows are switched on, each with its own ticket)
                               + making; then make + prove run in the
                               background, in order
    POST /build/knowledge     {"id", "ticked"} tick one knowledge line (a new list)
    POST /build/check/<id>    make / prove that job again (queued)
    POST /build/setup/<row>   complete one setup row ({"value"} for a key,
                               {"town"} or {"latitude", "longitude"} for the town)
    GET  /build/job/<id>      {"job", "parts", "setup"}
    POST /build/more/<id>     {"url"} a pasted project, first for that job
    POST /build/project       {"url" | "path", "name", "what", "cap"?, "job"?, "ask"?,
                               "order"?} a code project from the Add page (a GitHub
                               address) or the Developer page (a folder of her own
                               code, run in place): to the job it clearly fits or
                               ONE new job named after it, waiting for her Yes

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import re
import threading
from collections import deque
from typing import Any, Callable, Optional

from flask import Blueprint, jsonify, request

from puffpai.build import knowledge as knowledge_mod
from puffpai.build import schema as S
from puffpai.build import setup as setup_mod
from puffpai.build import yes as yes_mod
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

PAIRED_ONLY = "Open this from a browser that is paired with your bot."
TEAM = ("majordomo", "toolsmith", "drawbridge", "sentinel")
LINES_MAX = 20

_LOCK = threading.Lock()
_QUEUE: deque[str] = deque()
_running = threading.Event()
#: Jobs whose running try ended for a project she added (Run it first):
#: queued next, ahead of the others.
_FIRST: set[str] = set()


# ── the queue ──────────────────────────────────────────────────────────────

#: How long one job may take before the queue moves on to the next one (it
#: keeps going in the background and its real result still lands).
JOB_LIMIT_S = 20 * 60
TOO_LONG = ("It is taking longer than 20 minutes, so BuildBot went on with the next job. "
            "It keeps trying in the background; Check again to start it over.")


def _end_job(jid: str, reason: str) -> None:
    """A job that stopped or ran too long never stays "being built"."""
    def change(b: dict) -> None:
        j = S.find_job(b, jid)
        if j is not None and j.get("status") in (S.BUILDING, S.QUEUED):
            j.update(status=S.NOT_YET, reason=reason)
    try:
        S.update_build(change)
    except Exception:  # noqa: BLE001
        logger.exception("build: job %s status not kept", jid)


def _run_one(jid: str) -> None:
    """One job's make and prove on its own thread, with a time limit: one
    job's failure or hang never stops the jobs after it."""
    from puffpai.build import pipeline
    box: dict = {}
    done = threading.Event()

    def one() -> None:
        try:
            pipeline.run_job(jid)
        except BaseException as exc:  # noqa: BLE001 - kept, said, never lost
            box["exc"] = exc
            logger.exception("build: job %s stopped", jid)
        finally:
            done.set()
    threading.Thread(target=one, name=f"build-{jid}"[:40], daemon=True).start()
    # Time Toolsmith spends waiting for her to open the vault does not count.
    from puffpai.security import toolsmith_cloud
    step = max(0.01, min(5.0, float(JOB_LIMIT_S)))
    budget = float(JOB_LIMIT_S)
    while not done.wait(step):
        if not toolsmith_cloud.waiting_for_vault():
            budget -= step
        if budget <= 0:
            logger.warning("build: job %s still running after %s s; going on", jid,
                           JOB_LIMIT_S)
            _end_job(jid, TOO_LONG)
            return
    exc = box.get("exc")
    if exc is not None:
        _end_job(jid, f"Building it stopped ({type(exc).__name__}: {str(exc)[:160]}). "
                      "Check again to try once more.")


def _toolsmith_held() -> bool:
    """She picked Anthropic in the wizard but its key is not saved: no job
    starts Toolsmith on this computer's model without her knowing."""
    try:
        from puffpai.security import toolsmith_cloud
        return toolsmith_cloud.held()
    except Exception:  # noqa: BLE001 - unknown: hold (fail closed)
        logger.exception("build: Toolsmith hold not read; holding")
        return True


def _hold(job_ids: list[str]) -> None:
    """The jobs wait for her answer, with the one plain line."""
    from puffpai.security import toolsmith_cloud
    want = set(job_ids)

    def change(b: dict) -> None:
        for j in b.get("jobs") or []:
            if j.get("id") in want and j.get("status") in (S.WAITING_YES, S.QUEUED):
                j.update(status=S.WAITING_YES, reason=toolsmith_cloud.HOLD_LINE)
    try:
        S.update_build(change)
    except Exception:  # noqa: BLE001 - the word only; nothing started
        logger.info("build: held status not kept")
    logger.info("build: held before Toolsmith's first run: %s", toolsmith_cloud.HOLD_LINE)


def _work() -> None:
    while True:
        with _LOCK:
            if not _QUEUE:
                _running.clear()
                _queue_went_idle()
                return
            if _toolsmith_held():
                left = list(_QUEUE)
                _QUEUE.clear()
                _running.clear()
                _hold(left)
                return
            jid = _QUEUE.popleft()
        try:
            _run_one(jid)
        except Exception:  # noqa: BLE001 - one job never stops the queue
            logger.exception("build: job %s could not be run", jid)
            _end_job(jid, "Building it stopped. Check again to try once more.")


def _queue_went_idle() -> None:
    """Nothing running or waiting: Toolsmith's build lease on its key ends."""
    try:
        from puffpai.security import toolsmith_cloud
        toolsmith_cloud.queue_idle()
    except Exception:  # noqa: BLE001 - the lease also ends with its run and its cap
        logger.info("build: Toolsmith key lease not ended at idle")


def _queue_busy() -> bool:
    return _running.is_set()


def _resume_vault_waits() -> None:
    """The vault opened: the jobs left waiting for it start again by themselves."""
    try:
        from puffpai.security import toolsmith_cloud
        build = S.load_build()
        waits = {toolsmith_cloud.VAULT_WAIT_LINE}
        if not toolsmith_cloud.held():
            waits.add(toolsmith_cloud.HOLD_LINE)      # she saved the key or chose local
        jids = [j["id"] for j in build.get("jobs") or []
                if j.get("status") == S.WAITING_YES and j.get("reason") in waits]
        if jids and yes_mod.covers(build):
            enqueue(jids)
    except Exception:  # noqa: BLE001
        logger.exception("build: jobs waiting for the vault not started again")


def _hook_lease() -> None:
    try:
        from puffpai.security import toolsmith_cloud
        toolsmith_cloud.set_queue_probe(_queue_busy)
        toolsmith_cloud.on_resume(_resume_vault_waits)
    except Exception:  # noqa: BLE001 - without it a lease ends with its run
        logger.info("build: Toolsmith key lease hooks not set")


_hook_lease()


def _spawn() -> None:
    threading.Thread(target=_work, name="build-run", daemon=True).start()


def enqueue(job_ids: list[str]) -> bool:
    """Queue jobs to make and prove, one at a time. True when a run starts now.
    A job waiting for her yes that is queued now says "Waiting its turn"."""
    _mark_queued(job_ids)
    with _LOCK:
        for jid in job_ids:
            if jid not in _QUEUE:
                _QUEUE.append(jid)
        for jid in [j for j in job_ids if j in _FIRST]:
            _FIRST.discard(jid)
            _QUEUE.remove(jid)
            _QUEUE.appendleft(jid)
        if _running.is_set() or not _QUEUE:
            return False
        _running.set()
    _spawn()
    return True


def _mark_queued(job_ids: list[str]) -> None:
    want = set(job_ids)

    def change(b: dict) -> None:
        if not yes_mod.covers(b):
            return
        for j in b.get("jobs") or []:
            if j.get("id") in want and j.get("status") == S.WAITING_YES:
                j.update(status=S.QUEUED, reason="")
    try:
        S.update_build(change)
    except Exception:  # noqa: BLE001 - the word only
        logger.info("build: queued status not kept")


def queued() -> list[str]:
    with _LOCK:
        return list(_QUEUE)


# ── views ──────────────────────────────────────────────────────────────────

def recent_lines(limit: int = LINES_MAX) -> list[dict]:
    from puffpai import agent_activity
    try:
        with open(agent_activity.path(), encoding="utf-8") as fh:
            tail = fh.readlines()[-400:]
    except OSError:
        return []
    out = []
    for raw in tail:
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("agent") in TEAM:
            out.append({"agent": row["agent"], "text": str(row.get("task") or "")})
    return out[-limit:]


GETTING_READY = "Your bot is getting ready. This takes a moment after it starts."
#: How long a request waits for the start-up migration before it says so.
READY_WAIT_S = 1.5


class NotReady(Exception):
    """The start-up build migration (with the brain) is still running."""


def _migration_running() -> bool:
    """True while the server's start-up migration is still running. Only a
    server that started its background work has one (``api_server``); it is
    never imported from here."""
    import sys
    srv = sys.modules.get("puffpai.api_server")
    done = getattr(srv, "BUILD_MIGRATION_DONE", None) if srv is not None else None
    if done is None or not getattr(srv, "_background_services_started", False):
        return False
    return not done.wait(READY_WAIT_S)


def _build() -> dict:
    """build.json; made from an older bot's files here only when no start-up
    migration is running (that one has the brain). Raises :class:`NotReady`
    while it runs. Never starts make."""
    if not S.exists() and _migration_running():
        raise NotReady(GETTING_READY)
    from puffpai.build import migrate
    return migrate.ensure({"use_brain": False})


_FEED_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}\Z")


def _live_cards_by_host() -> dict:
    """Host -> the live card (an installed feed app) that reads it."""
    try:
        from puffpai.assemble import plan as plan_mod
        from puffpai.security import egress_doors
        return {h: egress_doors.app_of(d) for h, d in plan_mod._live_doors().items()}  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return {}


def _parts_used(build: dict) -> dict:
    """Each part a job uses, by name, and ``things``: the live card that is
    one of its ingredients (it has no page or sidebar entry of its own)."""
    parts = build.get("parts") or {}
    refs = list(dict.fromkeys(r for j in build.get("jobs") or []
                              for r in [(j.get("pick") or {}).get("ref")]
                              + list(j.get("ingredients") or []) if r))
    by_host = None
    out = {}
    for r in refs:
        part = parts.get(r) if isinstance(parts.get(r), dict) else {}
        row = S.part_summary(r, part)
        card = str(part.get("live_card") or "")
        if not card and part.get("class") == S.DATA and part.get("hosts"):
            by_host = _live_cards_by_host() if by_host is None else by_host
            card = next((str(by_host[h]) for h in part.get("hosts") or []
                         if by_host.get(str(h).lower())), "")
        if _FEED_ID_RE.match(card):
            row["things"] = [f"feed-{card}"]
        out[r] = row
    return out


def _bot_name() -> str:
    try:
        from puffpai import bot_identity
        return str(bot_identity.bot_name("") or "")[:60]
    except Exception:  # noqa: BLE001
        return ""


def screen_url(job: dict) -> str:
    """The job's own screen on this computer (127.0.0.1) while it runs: the
    app BuildBot put together for it, or the app it uses as it is. "" when
    there is none or it is stopped (reading this never starts anything)."""
    if job.get("status") != S.WORKING:
        return ""
    try:
        from puffpai.build import make
        made = make.load_made(job) or {}
        screen = made.get("screen") if isinstance(made.get("screen"), dict) else {}
        url = str(screen.get("url") or "")
        hid = ""
        if screen.get("kind") == "view" and screen.get("app_id"):
            from puffpai.assemble.server import host
            hid = host.host_id(str(screen["app_id"]))
        elif screen.get("integration_id"):
            hid = str(screen["integration_id"])
        if hid:
            from puffpai.integrate import run_here
            st = run_here.status(hid)
            url = str(st.get("url") or "") if st.get("state") == "running" else ""
    except Exception:  # noqa: BLE001 - no screen known
        return ""
    return url if url.startswith(("http://127.0.0.1:", "http://localhost:")) else ""


def _unusable(parts: dict) -> dict[str, str]:
    """Parts that turned out to give nothing to show: ref -> why."""
    return {r: str(p["unusable"]) for r, p in parts.items()
            if isinstance(p, dict) and p.get("unusable")}


def _public_job(job: dict, parts: dict) -> dict:
    """A job as the pages show it: without the parts that gave nothing to
    show (they are listed under Not used with their reason)."""
    gone = _unusable(parts)
    if gone:
        job = dict(job, ingredients=[r for r in job.get("ingredients") or [] if r not in gone])
        if (job.get("pick") or {}).get("ref") in gone and job["ingredients"]:
            first = parts.get(job["ingredients"][0]) or {}
            job["pick"] = {**job["pick"], "ref": job["ingredients"][0],
                           "class": str(first.get("class") or job["pick"].get("class") or "")}
    out = S.public_job(job, parts)
    now = trying_now(job)
    if now:
        out["trying"] = now
        if not now.get("waiting"):
            # A try running for it now always wins over the word kept in
            # build.json (her new project or an old Yes can say waiting_yes).
            out.update(status=S.BUILDING, reason="")
    used = app_used(job)
    if used:
        out["app_used"] = used
    built = built_with(job, used)
    if built:
        out["built_with"] = built
    url = screen_url(job)
    if url:
        out["screen"] = url
        out["screen_framable"] = frame_allowed(url)
    return out


def trying_now(job: dict) -> dict:
    """The app Toolsmith is trying for *job* right now (``{"name", "since",
    "cost_usd"?}``), or {}."""
    try:
        from puffpai.build import toolsmith_run
        return toolsmith_run.trying(str(job.get("id") or ""))
    except Exception:  # noqa: BLE001 - a progress line only
        return {}


def app_used(job: dict) -> dict:
    """``{"ref", "name", "made_from"?}`` of the app a route A job really uses
    (the pick, the backup make used when the pick could not start here, or
    the app Toolsmith made from a code project), or {}."""
    if job.get("route") != S.A:
        return {}
    try:
        from puffpai.build import make
        made = (make.load_made(job) or {}).get("made") or {}
    except Exception:  # noqa: BLE001
        return {}
    name, ref = str(made.get("app_name") or ""), str(made.get("app_ref") or "")
    if not (name and ref):
        return {}
    out = {"ref": ref[:200], "name": name[:80]}
    if made.get("made_from"):
        out["made_from"] = str(made["made_from"])[:200]
    prog = made.get("program") if isinstance(made.get("program"), dict) else {}
    if prog.get("id") or prog.get("path"):
        # A desktop program: the pages show Open (POST /programs/<id>/launch).
        out["program"] = {"id": str(prog.get("id") or "")[:100],
                          "path": str(prog.get("path") or "")[:1000]}
    if made.get("model"):
        out["model"] = str(made["model"])[:80]
    notes = [str(n)[:200] for n in made.get("notes") or [] if str(n).strip()][:4]
    if notes:
        # For the owner, one plain line each: its README's sign-in, an outside service down.
        out["notes"] = notes
    return out


def built_with(job: dict, used: Optional[dict] = None) -> str:
    """The model Toolsmith used for *job*: its app's ("local" or the cloud
    model id), else its last try's; "" when none was recorded."""
    if (used or {}).get("model"):
        return str(used["model"])[:80]
    tried = [t for t in job.get("tried") or [] if isinstance(t, dict) and t.get("model")]
    return str(tried[-1]["model"])[:80] if tried else ""


def running_view() -> list[dict]:
    """The apps Toolsmith runs (``toolsmith_apps.json``), each with whether
    its page answers now: ``[{"id", "name", "url", "running", "made_from"?}]``."""
    try:
        from puffpai.build import toolsmith_run
        return [{k: a[k] for k in ("id", "name", "url", "running", "made_from")
                 if k != "made_from" or a.get(k)}
                for a in toolsmith_run.running_apps()]
    except Exception as exc:  # noqa: BLE001 - none known
        logger.info("build api: running apps not read (%s)", type(exc).__name__)
        return []


_FRAME_SEEN: dict[str, tuple[float, bool]] = {}


def frame_allowed(url: str) -> bool:
    """May the app's own page be shown in a frame on her dashboard? Its own
    headers decide: ``X-Frame-Options`` (DENY or SAMEORIGIN, the dashboard
    being another origin) or a CSP ``frame-ancestors`` that is not ``*``
    means no, and the job page offers its own window instead. BuildBot never
    strips those headers (and never serves the app from the dashboard's own
    origin, where its scripts could reach the dashboard). Only 127.0.0.1 is
    asked, without following redirects; not knowing is yes (the browser
    still enforces the app's own rule)."""
    import time
    import urllib.error
    import urllib.parse
    import urllib.request
    now = time.monotonic()
    seen = _FRAME_SEEN.get(url)
    if seen and now - seen[0] < 60:
        return seen[1]
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host not in ("127.0.0.1", "localhost"):
        return False

    class _Stay(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):  # noqa: ANN002, ANN003
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _Stay())  # lan-egress: 127.0.0.1 only (checked above), no proxy
    headers = None
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=3) as resp:
            headers = resp.headers
    except urllib.error.HTTPError as exc:
        headers = exc.headers
    except (OSError, ValueError):
        headers = None
    ok = True
    if headers is not None:
        xfo = str(headers.get("X-Frame-Options") or "").strip().lower()
        csp = str(headers.get("Content-Security-Policy") or "").lower()
        anc = next((d.strip() for d in csp.split(";") if d.strip().startswith(
            "frame-ancestors")), "")
        ok = xfo not in ("deny", "sameorigin") and (not anc or anc.split()[1:] == ["*"])
    _FRAME_SEEN[url] = (now, ok)
    return ok


def _public_rows(build: dict, jid: str = "") -> list[dict]:
    gone = _unusable(build.get("parts") or {})
    return [S.public_row(r) for r in build["setup"]
            if not r.get("unusable") and not (r.get("ref") in gone and r.get("kind") in
                                              setup_mod.YES_SWITCHES)
            and (not jid or jid in r.get("job_ids", []))]


def _public_dropped(build: dict) -> list[dict]:
    out = [{"ref": d["ref"], "name": d.get("name") or d["ref"], "why": d.get("why") or ""}
           for d in build.get("dropped") or []]
    parts = build.get("parts") or {}
    for ref, why in _unusable(parts).items():
        out.insert(0, {"ref": ref, "name": str((parts.get(ref) or {}).get("name") or ref),
                       "why": why})
    return out


STOPPED_MIDWAY = "It stopped before it was done (your bot restarted). Check again to build it."


def _unstick(build: dict) -> dict:
    """A job left "being built" with nothing running (the bot restarted
    mid-build) is said as it is, never as building forever."""
    stuck = [j["id"] for j in build.get("jobs") or [] if j.get("status") in (S.BUILDING, S.QUEUED)]
    if not stuck or _running.is_set() or queued():
        return build
    for jid in stuck:
        _end_job(jid, STOPPED_MIDWAY)
    return S.load_build()


def _hold_line() -> str:
    from puffpai.security import toolsmith_cloud
    return toolsmith_cloud.HOLD_LINE if _toolsmith_held() else ""


def _vault_wait_line(build: dict) -> str:
    """"Unlock your vault to let Toolsmith go on." while a run waits for the
    vault, or while a job is going or left waiting for it and Toolsmith's key
    is not ready: the job card and the bot page ask for her PIN right there."""
    try:
        from puffpai.security import toolsmith_cloud
        wait = toolsmith_cloud.VAULT_WAIT_LINE
        needed = _running.is_set() or any(
            j.get("status") in (S.BUILDING, S.QUEUED)
            or (j.get("status") == S.WAITING_YES and j.get("reason") == wait)
            for j in build.get("jobs") or [])
        return toolsmith_cloud.vault_wait_line(needed)
    except Exception:  # noqa: BLE001 - a status line never breaks the view
        return ""


def status_view() -> dict:
    try:
        build = _unstick(_build())
    except NotReady as exc:
        return {"ready": False, "line": str(exc), "jobs": [], "setup": [], "parts": [],
                "yes": {"given": False, "at": None, "sha": ""}, "sites": [], "dropped": [],
                "running": False, "lines": [], "words": S.WORDS, "bot": _bot_name()}
    y = yes_mod.public(build)
    parts = build.get("parts") or {}
    return {"ready": True,
            "jobs": [_public_job(j, parts) for j in build["jobs"]],
            "setup": _public_rows(build),
            # Each part a job uses, by name: the handoff and home need no
            # call per job for these.
            "parts": list(_parts_used(build).values()),
            "yes": {"given": y["given"], "at": y["at"], "sha": y["sha"]},
            "sites": yes_mod.sites_public(build),
            "dropped": _public_dropped(build),
            # Books for the goal on the same Yes, each tickable (build.knowledge).
            "knowledge": knowledge_mod.public(build),
            "running": _running.is_set(), "lines": recent_lines(), "words": S.WORDS,
            "bot": _bot_name(),
            # She picked Anthropic in the wizard, its key is not saved: the
            # setup card shows this line with Open Settings / Use this computer.
            "toolsmith_hold": _hold_line(),
            # Toolsmith waits for the vault: a PIN box right on the page.
            "vault_wait": _vault_wait_line(build)}


def job_view(jid: str) -> tuple[dict, int]:
    try:
        build = _build()
    except NotReady as exc:
        return {"ok": False, "ready": False, "message": str(exc)}, 503
    job = S.find_job(build, jid)
    if job is None:
        return {"ok": False, "message": "There is no job by that name."}, 404
    parts = build.get("parts") or {}
    shown = _public_job(job, parts)
    return {"ok": True, "job": shown,
            "parts": [{**S.part_summary(r, parts.get(r)), "check": parts.get(r, {}).get("check")}
                      for r in shown.get("ingredients") or []],
            "setup": _public_rows(build, jid),
            "words": S.WORDS}, 200


# ── setup rows ─────────────────────────────────────────────────────────────

def _key(row: dict, body: dict, device: str) -> tuple[dict, int]:
    from puffpai.security import keys
    value = body.get("value")
    if not isinstance(value, str) or not value.strip():
        return {"ok": False, "message": "Please paste the key."}, 400
    try:
        if row.get("skill") and row.get("name"):
            from puffpai import deps
            keys.put_skill_key(row["skill"], row["name"], value.strip(),
                               declared=deps.key_names(row["skill"]), by="owner")
        elif row.get("service") and keys.spec(row["service"]):
            # The Sign-up keys page's own way: its sentence, a key_on ticket
            # for this device and this key, spent at once, then the vault.
            from puffpai.security import consent_tickets
            sid = row["service"]
            keys.check_shape(sid, value.strip())
            ticket = consent_tickets.mint("key_on", sid, keys.confirm_sentence(sid), device)
            ok, verdict = consent_tickets.redeem(ticket, device, "key_on", sid)
            if not ok:
                return {"ok": False, "message": consent_tickets.refusal_words(verdict)}, 403
            got = keys.add_key(sid, value.strip(), by="owner")
            if isinstance(got, dict) and got.get("ok") is False:
                return {"ok": False, "message": str(got.get("message") or "Not kept.")}, 400
        elif setup_mod.waits_for_install(row):
            return {"ok": False, "message": "This key gets its place once the skill is on "
                                            "this computer, right after your yes. Add it "
                                            "here then."}, 409
        else:
            return {"ok": False, "message": "This key gets its place when the job is built. "
                                            "Say yes first, then add it here."}, 409
    except Exception as exc:  # noqa: BLE001 - plain words from the vault
        return {"ok": False, "message": str(getattr(exc, "message", "") or
                                            "Your vault did not keep it. Is it open?")}, 400
    return {"ok": True, "line": "Your key is in your vault."}, 200


def _site_row(row: dict, build: dict, device: str) -> tuple[dict, int, bool]:
    """One switch or address row, tapped on its own: the same way her one yes
    does it (build.yes), with its own ticket. ``(answer, status, done)``."""
    listed = {str(r.get("service") or "") for r in build.get("setup") or []
              if r.get("kind") == "switch"}
    got = yes_mod._switch_one(row, device, listed)  # noqa: SLF001 - one rule for both
    if got["state"] == yes_mod.SITE_ON:
        return {"ok": True, "line": "It is switched on."}, 200, True
    if got["state"] == yes_mod.SITE_WHEN_BUILT:
        return {"ok": True, "line": "It is switched on when its job is built, with your "
                                    "yes."}, 200, False
    return {"ok": False, "message": got.get("reason") or "It was not switched on."}, 409, False


def complete_row(row_id: str, body: dict, device: str) -> tuple[dict, int]:
    from puffpai import fix_api
    try:
        build = _build()
    except NotReady as exc:
        return {"ok": False, "message": str(exc)}, 503
    row = next((r for r in build["setup"] if r["id"] == row_id), None)
    if row is None:
        return {"ok": False, "message": "There is no such step."}, 404
    kind = row["kind"]
    done = True
    if row.get("by_yes"):
        return {"ok": False, "message": "Your yes does this one."}, 409
    if kind == "key":
        got, status = _key(row, body, device)
    elif kind == "setting":
        got, status = _town(row, body, build, device)
        if got.get("pending"):
            return got, status
    elif kind in setup_mod.YES_SWITCHES:
        got, status, done = _site_row(row, build, device)
    else:
        return {"ok": False, "message": "Your yes installs this one."}, 409
    if not got.get("ok"):
        return got, status
    if not done:
        return {"ok": True, "line": got.get("line") or "Done.", "row": row_id}, 200

    def mark(b: dict) -> None:
        for r in b["setup"]:
            if r["id"] == row_id:
                r["done"] = True
        setup_mod.apply_statuses(b)
    build = S.update_build(mark)
    ready = [j["id"] for j in build["jobs"] if j.get("status") == S.WAITING_YES
             and j["id"] in row.get("job_ids", [])]
    if ready and yes_mod.covers(build):
        enqueue(ready)
    return {"ok": True, "line": got.get("line") or "Done.", "row": row_id}, 200


def _town(row: dict, body: dict, build: dict, device: str) -> tuple[dict, int]:
    """Her town: looked up through the Bridge (the town lookup, switched on
    with her tap). While the bot is still set to stay off the internet and
    "Let <Bot> go online" is on the list, the town is kept on the row and
    looked up right after her Yes (:func:`lookup_pending`). Latitude and
    longitude are only the fallback when it cannot be looked up."""
    from puffpai import fix_api
    from puffpai.build import online
    town = " ".join(str(body.get("town") or body.get("value") or "").split())[:100]
    lat, lon = body.get("latitude"), body.get("longitude")
    has_pos = lat not in (None, "") and lon not in (None, "")
    waiting_online = any(r.get("id") == online.ROW_ID and not r.get("done")
                         for r in build.get("setup") or [])
    if not has_pos and waiting_online and not online.is_online():
        if len(town) < 2:
            return {"ok": False, "message": "Please type a town name."}, 400
        rid = row["id"]

        def keep(b: dict) -> None:
            for r in b["setup"]:
                if r["id"] == rid:
                    r.update(town=town, pending=True, said="")
            setup_mod.apply_statuses(b)
        S.update_build(keep)
        return {"ok": True, "pending": True, "row": rid,
                "line": f"Kept: {town}. It is looked up right after your Yes."}, 200
    got, status = fix_api.apply_setting({"key": row.get("key") or "home", "value": town,
                                         "latitude": lat, "longitude": lon}, device)
    if not got.get("ok") and got.get("position"):
        rid = row["id"]

        def ask_position(b: dict) -> None:
            for r in b["setup"]:
                if r["id"] == rid:
                    r.update(position=True, said=str(got.get("message") or ""))
        S.update_build(ask_position)
    return got, status


def lookup_pending(device: str) -> list[str]:
    """After her Yes: look up each town kept on its row (the bot may go
    online now). Returns the rows done."""
    from puffpai import fix_api
    build = S.load_build()
    done: list[str] = []
    said: dict[str, dict] = {}
    for r in build.get("setup") or []:
        if r.get("kind") == "setting" and r.get("pending") and not r.get("done"):
            got, _status = fix_api.apply_setting({"key": r.get("key") or "home",
                                                  "value": r.get("town") or ""}, device)
            if got.get("ok"):
                done.append(r["id"])
            else:
                why = str(got.get("message") or "It was not found.")
                logger.warning("build: town lookup after the yes did not work: %s", why)
                said[r["id"]] = {"said": f"Your town ({r.get('town')}) was not looked up yet: "
                                         f"{why} Save it again to retry, or type its rough "
                                         f"position.", "position": True}

    def mark(b: dict) -> None:
        for r in b["setup"]:
            if r["id"] in done:
                r.update(done=True, pending=False, said="")
            elif r["id"] in said:
                r.update(pending=False, **said[r["id"]])
        setup_mod.apply_statuses(b)
    if done or said:
        S.update_build(mark)
    return done


def nothing_to_show(reason: str) -> bool:
    """A website whose one look found nothing a page could show."""
    try:
        from puffpai import feed_generator
        return feed_generator.NOTHING_TO_SHOW in str(reason or "")
    except Exception:  # noqa: BLE001
        return False


def _plain_unusable(reason: str) -> str:
    text = str(reason or "")
    text = text.split(": ", 1)[1] if ": BuildBot" in text else text
    return text.replace(" Nothing was added.", "").strip()[:200]


def go_online_if_listed(build: dict) -> bool:
    """Her Yes covers "Let <Bot> go online" when the list showed it."""
    from puffpai.build import online
    row = next((r for r in build.get("setup") or [] if r.get("id") == online.ROW_ID), None)
    if row is None or row.get("done"):
        return False
    if not online.go_online(by="owner"):
        return False

    def mark(b: dict) -> None:
        for r in b["setup"]:
            if r.get("id") == online.ROW_ID:
                r["done"] = True
    S.update_build(mark)
    return True


# ── routes ─────────────────────────────────────────────────────────────────

bp = Blueprint("build", __name__)


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


def _body() -> dict:
    got = request.get_json(silent=True)
    return got if isinstance(got, dict) else {}


@bp.route("/build/status", methods=["GET"])
def build_status():
    return jsonify({"ok": True, **status_view()})


@bp.route("/build/apps", methods=["GET"])
def build_apps():
    return jsonify({"ok": True, "apps": running_view()})


@bp.route("/installed", methods=["GET"])
def installed_list():
    """Everything installed on this computer (``puffpai.installed``), the
    same list the brain sees: ``{"items": [{"id", "kind", "name", "what",
    "status", "url", "source_url", "brain"}]}``."""
    from puffpai import installed
    return jsonify({"items": installed.listing()})


@bp.route("/build/job/<jid>", methods=["GET"])
def build_job(jid: str):
    got, status = job_view(str(jid)[:60])
    return jsonify(got), status


_SHA_RE = re.compile(r"^[0-9a-f]{64}\Z")
SEE_THE_LIST = "Look at the list again, then say yes to what it shows."


@bp.route("/build/yes", methods=["POST"])
def build_yes():
    # Her yes covers exactly the list she was shown: the page sends that
    # list's sha (GET /build/status yes.sha). No sha, no yes (fail closed).
    seen = str(_body().get("sha") or "")
    if not _SHA_RE.match(seen):
        return jsonify({"ok": False, "message": SEE_THE_LIST}), 409
    device = _device()
    try:
        build = yes_mod.give(device, seen_sha=seen)
    except yes_mod.YesError as exc:
        return jsonify({"ok": False, "message": exc.line}), exc.status
    # The bot's own go-online setting first (when the list showed it), then
    # any town she typed while the bot was still offline (looked up now, so
    # nothing switched on next reads "no town yet"), then the outside sites
    # the list showed, each with its own ticket.
    go_online_if_listed(build)
    lookup_pending(device)
    sites = yes_mod.switch_sites(device)
    why = {s["row"]: s.get("reason") or "" for s in sites if s.get("state") == yes_mod.SITE_NOT_ON}

    def settle(b: dict) -> None:
        for r in b["setup"]:
            if why.get(r["id"]):
                r["said"] = why[r["id"]]     # why it is not on yet, in its own words
                if nothing_to_show(why[r["id"]]) and r.get("ref"):
                    # It gives nothing to show: out of its job, under Not used.
                    r["unusable"] = True
                    part = (b.get("parts") or {}).get(r["ref"])
                    if isinstance(part, dict):
                        part["unusable"] = _plain_unusable(why[r["id"]])
        from puffpai.build import pipeline as pipeline_mod
        for j in b["jobs"]:
            if j.get("wanted"):
                pipeline_mod.honest_line(j, b.get("parts") or {})   # without what left
        setup_mod.apply_statuses(b)
    S.update_build(settle)
    from puffpai.build import pipeline
    todo = pipeline.waiting_jobs(S.load_build())
    held = bool(todo) and _toolsmith_held()
    if held:
        _hold(todo)              # released by Open Settings (key saved) or Use this computer
        started = False
    else:
        started = enqueue(todo)
    # The ticked knowledge lines download beside the jobs, one at a time.
    knowledge_mod.start_downloads()
    off = [s for s in sites if s["state"] == yes_mod.SITE_NOT_ON]
    line = (_hold_line() if held else
            f"Building {len(todo)} job{'s' if len(todo) != 1 else ''} now."
            if todo else "Nothing is waiting to be built.")
    if off:
        line += f" {len(off)} outside site{'s are' if len(off) != 1 else ' is'} not on yet; " \
                f"{'they stay' if len(off) != 1 else 'it stays'} on the list."
    return jsonify({"ok": True, "started": started, "queued": [] if held else todo,
                    "line": line, "held": held,
                    "sites": yes_mod.sites_public(S.load_build())}), 202


@bp.route("/build/knowledge", methods=["POST"])
def build_knowledge():
    """Tick or untick one knowledge line ``{"id", "ticked"}``; a change is
    a new list that needs her Yes."""
    body = _body()
    ident = str(body.get("id") or "")[:200]
    try:
        got = knowledge_mod.set_tick(ident, body.get("ticked") is True)
    except KeyError:
        return jsonify({"ok": False, "message": "That is not on the list."}), 404
    return jsonify({"ok": True, "knowledge": knowledge_mod.public(got),
                    "yes": {"given": bool((got.get("yes") or {}).get("given")),
                            "sha": yes_mod.sha(got)}}), 200


@bp.route("/build/check/<jid>", methods=["POST"])
def build_check(jid: str):
    try:
        build = _build()
    except NotReady as exc:
        return jsonify({"ok": False, "message": str(exc)}), 503
    job = S.find_job(build, str(jid)[:60])
    if job is None:
        return jsonify({"ok": False, "message": "There is no job by that name."}), 404
    if job.get("route") == S.D:
        return jsonify({"ok": False, "message": "Nothing was found for it yet. "
                                                "Try Find more."}), 409
    if not yes_mod.covers(build):
        return jsonify({"ok": False, "message": "Say yes to the setup list first; "
                                                "then it is built."}), 409
    if any(setup_mod.her_part(r) for r in build["setup"] if job["id"] in r.get("job_ids", [])):
        return jsonify({"ok": False, "message": "Something on the setup list needs you "
                                                "first."}), 409
    jid = job["id"]
    # {"again": true}: the same job from the top (her project first, then
    # its pick and alternates, ones tried before too); the old reasons stay
    # on the job as "tried before".
    again = _body().get("again") is True

    def building(b: dict) -> None:
        j = S.find_job(b, jid)
        if j is not None:
            j.update(status=S.BUILDING, reason="")
            if again:
                start_over(j)
    S.update_build(building)
    started = enqueue([jid])
    return jsonify({"ok": True, "started": started,
                    "line": ("Checking it again from the top now." if again else
                             "Checking it again now.") if started else
                    "It is next, right after what is being built now."}), 202


#: How many earlier tries a job keeps to show as "Tried before".
TRIED_BEFORE_MAX = 20


def start_over(job: dict) -> None:
    """Check again: *job*'s tried list is cleared for this run; its entries
    move to ``tried_before`` (newest last), which the pages still show."""
    old = [t for t in job.get("tried") or [] if isinstance(t, dict) and t.get("ref")]
    if old:
        refs = {t["ref"] for t in old}
        job["tried_before"] = ([t for t in job.get("tried_before") or []
                                if isinstance(t, dict) and t.get("ref") not in refs]
                               + [dict(t) for t in old])[-TRIED_BEFORE_MAX:]
    job["tried"] = []


@bp.route("/build/more/<jid>", methods=["POST"])
def build_more(jid: str):
    """Find more for one job from a pasted GitHub project address: read the
    same way as any find (its README's run steps), it becomes the job's
    first app, and the changed list waits for her one Yes."""
    body = _body()
    url = str(body.get("url") or "").strip()[:300]
    if not url:
        return jsonify({"ok": False, "message": "Paste a GitHub project address."}), 400
    try:
        build = _build()
    except NotReady as exc:
        return jsonify({"ok": False, "message": str(exc)}), 503
    job = S.find_job(build, str(jid)[:60])
    if job is None:
        return jsonify({"ok": False, "message": "There is no job by that name."}), 404
    from puffpai.build import pipeline
    order = _order(body)
    got, said = _with_url(lambda: pipeline.plan_more(job["id"], {"url": url, "order": order}))
    if said is not None:
        return said
    j = S.find_job(got, job["id"]) or {}
    stopped = _her_turn(job["id"], order)
    return jsonify({"ok": True, "line": "Added to the list: " + (
                        STOPPED_FOR_HERS if stopped else "say yes to it to start it."),
                    "job": _public_job(j, got.get("parts") or {})}), 200


STOPPED_FOR_HERS = "the try that was running for this job stopped; yours runs next after your Yes."


def _order(body: dict) -> str:
    """Where her project goes: "first" (Run it first, the default) or "list"."""
    return "list" if body.get("order") == "list" else "first"


def _her_turn(jid: str, order: str) -> bool:
    """Run it first: a Toolsmith try running for this same job ends now
    (cleanly, as on give up) and the job goes to the front of the queue.
    True when a try was stopped. Add to the list stops nothing."""
    if order != "first":
        return False
    try:
        from puffpai.build import toolsmith_run
        stopped = toolsmith_run.stop_for(jid, "you added a project to run first")
    except Exception:  # noqa: BLE001 - her add stands
        logger.exception("build: running try for %s not stopped", jid)
        return False
    if stopped:
        with _LOCK:
            _FIRST.add(jid)
    return stopped


def _with_url(plan: Callable[[], Any]) -> tuple[Any, Any]:
    """Run a plan with a pasted project address: (its result, None), or
    (None, the reply) when GitHub access is off (its switch offered as a
    card, POST /fix/apply, never a refusal) or the project cannot be read."""
    from puffpai.build import pipeline
    try:
        return plan(), None
    except pipeline.SwitchOff as exc:
        from puffpai import fix_api
        card = fix_api.clean({"kind": "switch_on", "service": exc.service})
        return None, (jsonify({"ok": False, "message": str(exc), "switch": exc.service,
                               "fix": fix_api.public(card) if card else None}), 409)
    except ValueError as exc:
        return None, (jsonify({"ok": False, "message": str(exc)}), 422)


@bp.route("/build/project", methods=["POST"])
def build_project():
    """A code project from the Add page, into the same loop as Find more:
    ``{"url", "name", "what", "cap"?, "job"?, "ask"?}``. ``ask`` only says
    where it would go: the job it clearly fits (``fits``) and the new job's
    name. ``job`` is a job's id, or "new" for ONE new job named after the
    project; left out, the job it clearly fits, else a new one. Either way
    her project goes first and the job waits for her one Yes."""
    body = _body()
    url = str(body.get("url") or "").strip()[:300]
    path = str(body.get("path") or "").strip()[:1000]
    local = None
    if path and not url:
        # A folder of her own code on this computer: read now, run in place.
        from puffpai.build import apps as apps_mod
        try:
            local = apps_mod.from_local_folder(path)
        except ValueError as exc:
            return jsonify({"ok": False, "message": str(exc)}), 422
        url = str(local["url"])
    if not url:
        return jsonify({"ok": False, "message": "Give a GitHub project address or a "
                                               "folder on this computer."}), 400
    name = " ".join(str(body.get("name") or "").split())[:100]
    what = str(body.get("what") or "")[:300]
    if local is not None:
        name = name or str(local.get("name") or "")
        what = what or str(local.get("what") or "")
    try:
        build = _build()
    except NotReady as exc:
        return jsonify({"ok": False, "message": str(exc)}), 503
    from puffpai.build import pipeline
    asked = str(body.get("job") or "")[:60]
    if asked and asked != "new":
        target = S.find_job(build, asked)
        if target is None:
            return jsonify({"ok": False, "message": "There is no job by that name."}), 404
    elif asked == "new":
        target = None
    else:
        target = pipeline.fits_project(build, name, what, str(body.get("cap") or "")[:80])
    if body.get("ask") is True:
        return jsonify({"ok": True, "new_name": pipeline.project_job_name(name, what),
                        "fits": {"id": target["id"], "name": target["name"]}
                        if target else None}), 200
    order = _order(body)
    ctx: dict = {"order": order}
    if local is not None:
        ctx["from_url"] = lambda _u: local
    got, said = _with_url(lambda: pipeline.plan_project(
        url, name, what, target["id"] if target else "", ctx=ctx))
    if said is not None:
        return said
    got_build, jid, new = got
    j = S.find_job(got_build, jid) or {}
    stopped = not new and _her_turn(jid, order)
    return jsonify({"ok": True, "new": new, "page": f"/dashboard/jobs/{jid}",
                    "line": f"Added to job {j.get('name') or jid}; "
                            + (STOPPED_FOR_HERS if stopped else "it starts after your Yes."),
                    "job": _public_job(j, got_build.get("parts") or {})}), 200


@bp.route("/build/setup/<row_id>", methods=["POST"])
def build_setup(row_id: str):
    got, status = complete_row(str(row_id)[:100], _body(), _device())
    return jsonify(got), status


__all__ = ["bp", "complete_row", "enqueue", "job_view", "queued", "recent_lines",
           "running_view", "status_view"]
