"""The shared words of the build: enums, the Job record and build.json.

Other parts of BuildBot (the makers, the dashboard, the wiring) code against
exactly these names. ``<state>/build/build.json``::

    {"schema": "build/v1", "goal", "made_at",
     "jobs": [Job], "setup": [Row], "yes": {"given", "at", "sha", "by"},
     "dropped": [{"ref", "name", "why"}], "parts": {ref: Part}}

A Job::

    {"id", "name", "does", "action", "subject": [<=3 words], "output",
     "needs": [...], "ask", "route", "pick": {"ref", "class", "score", "why"},
     "ingredients": [ref], "view", "page": "job-<id>", "status", "reason"}

A setup Row: ``{"id", "kind", "label", "detail", "job_ids", "done"}`` (plus
what completing it needs: ``service``, ``skill``, ``host``, ``app_id``...).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import copy
import os
import re
import threading
import time
from typing import Any, Callable, Optional

from puffpai import fsutil
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA = "build/v1"

ACTIONS = ("show", "watch", "listen", "ask", "make", "do", "remind", "learn")
OUTPUTS = ("pictures", "video", "sound", "numbers_over_time", "places", "text", "action")
NEEDS = ("place", "time", "account_key", "none")

APP, TOOL, DATA, TEXT, GUIDE = "APP", "TOOL", "DATA", "TEXT", "GUIDE"
CLASSES = (APP, TOOL, DATA, TEXT, GUIDE)

A, B, C, D = "A", "B", "C", "D"
ROUTES = (A, B, C, D)

PLANNED, NEEDS_SETUP, WAITING_YES = "planned", "needs_setup", "waiting_yes"
#: Covered by her Yes, waiting for the job before it to finish.
QUEUED = "queued"
BUILDING, WORKING, NOT_YET, NOTHING_FOUND = "building", "working", "not_yet", "nothing_found"
STATUSES = (PLANNED, NEEDS_SETUP, WAITING_YES, QUEUED, BUILDING, WORKING, NOT_YET,
            NOTHING_FOUND)

SETUP_KINDS = ("program", "package", "key", "setting", "address", "switch")

#: The shared view a job's output is shown in (routes B and C).
VIEW = {"pictures": "gallery", "numbers_over_time": "chart", "places": "map",
        "video": "player", "sound": "player", "text": "list", "action": "form"}
#: Route A shows the app's own screen.
OWN_SCREEN = "app"
#: Views a put-together app may end up showing when its data has nothing for
#: the asked one (its data decides: a forecast has no pictures).
MADE_VIEWS = ("gallery", "table", "forecast", "map", "player", "chart", "list", "form",
              "links")

WORDS = {
    "status": {PLANNED: "Planned", NEEDS_SETUP: "Needs setup",
               WAITING_YES: "Waiting for your yes", QUEUED: "Waiting its turn",
               BUILDING: "Being built",
               WORKING: "Working", NOT_YET: "Not yet", NOTHING_FOUND: "Nothing found yet"},
    "route": {A: "Uses it as it is", B: "Connects a tool", C: "Puts together a <view>",
              D: "Nothing found yet"},
    "view": {"gallery": "picture gallery", "chart": "chart", "map": "map",
             "player": "player", "list": "list", "form": "form", "forecast": "forecast",
             "table": "table", "links": "list of links", OWN_SCREEN: "its own screen"},
    "class": {APP: "an app", TOOL: "a tool", DATA: "a source of data", TEXT: "a book",
              GUIDE: "a guide your bot reads"},
    #: A job whose pick is a book or a guide (learn/ask) answers from it.
    "answers": "Answers from <name>",
}
#: Pick classes a job answers from rather than connects.
ANSWER_CLASSES = (TEXT, GUIDE)

STILL_BUILDING = "still being built"
NOTHING_FOUND_WHY = "Nothing found yet"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
_LOCK = threading.RLock()


def how_words(job: dict, parts: Optional[dict] = None) -> str:
    """The job's route in plain words ("Puts together a picture gallery").
    A job whose pick is a book or a guide answers from it ("Answers from A
    Field Book of the Stars"); it does not connect a tool. *parts* (by ref)
    names the pick."""
    route = job.get("route")
    pick = job.get("pick") if isinstance(job.get("pick"), dict) else {}
    if route != D and pick.get("class") in ANSWER_CLASSES:
        part = (parts or {}).get(str(pick.get("ref") or "")) or {}
        name = str(part.get("name") or "").strip() or \
            ("a book" if pick["class"] == TEXT else "a guide")
        return WORDS["answers"].replace("<name>", name)
    text = WORDS["route"].get(route, "")
    if route == C:
        view = WORDS["view"].get(str(job.get("view") or ""), "view")
        text = text.replace("<view>", view)
    return text


def job_id(name: Any, taken: Any = ()) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:40].strip("-")
    slug = slug or "job"
    out, n = slug, 2
    while out in set(taken):
        out = f"{slug}-{n}"
        n += 1
    return out


def page_for(jid: str) -> str:
    return f"job-{jid}"


def new_job(name: str, does: str = "", *, action: str = "show", subject: Any = (),
            output: str = "text", needs: Any = ("none",), ask: str = "",
            taken: Any = (), cap: str = "") -> dict:
    jid = job_id(name, taken)
    job = {"id": jid, "name": name, "does": does, "action": action,
           "subject": list(subject)[:3], "output": output,
           "needs": list(needs) or ["none"], "ask": ask, "route": D,
           "pick": {"ref": "", "class": "", "score": 0.0, "why": ""},
           "ingredients": [], "view": "", "page": page_for(jid), "status": PLANNED,
           "reason": ""}
    if cap:
        job["cap"] = cap
    return job


def job_problems(job: Any) -> list[str]:
    """What is wrong with one interpreted job ([] when it is fine)."""
    if not isinstance(job, dict):
        return ["a job must be an object"]
    out = []
    name = job.get("name")
    if not isinstance(name, str) or not (2 <= len(name.strip()) <= 80):
        out.append("name must be 2 to 80 letters")
    if job.get("action") not in ACTIONS:
        out.append(f"action must be one of {', '.join(ACTIONS)}")
    if job.get("output") not in OUTPUTS:
        out.append(f"output must be one of {', '.join(OUTPUTS)}")
    needs = job.get("needs")
    if not isinstance(needs, list) or not needs or any(n not in NEEDS for n in needs) \
            or ("none" in needs and len(needs) > 1):
        out.append(f"needs must be a list from {', '.join(NEEDS)} ('none' alone)")
    subject = job.get("subject")
    if not isinstance(subject, list) or not (1 <= len(subject) <= 3) \
            or any(not isinstance(w, str) or not re.fullmatch(r"[a-z0-9'-]{2,30}", w)
                   for w in subject):
        out.append("subject must be 1 to 3 single lowercase words")
    return out


# ── build.json ─────────────────────────────────────────────────────────────

def build_dir(*parts: str) -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "build", *parts)


def build_path() -> str:
    return build_dir("build.json")


def empty_build(goal: str = "") -> dict:
    return {"schema": SCHEMA, "goal": goal, "made_at": 0, "jobs": [], "setup": [],
            "yes": {"given": False, "at": None, "sha": "", "by": ""}, "dropped": [],
            "parts": {}}


def exists() -> bool:
    return os.path.isfile(build_path())


def load_build() -> dict:
    """build.json, or an empty build when there is none (or it is not ours)."""
    with _LOCK:
        got = fsutil.read_json(build_path(), default=None)
    if not (isinstance(got, dict) and got.get("schema") == SCHEMA
            and isinstance(got.get("jobs"), list)):
        return empty_build()
    out = empty_build()
    out.update(got)
    return out


def save_build(build: dict) -> dict:
    build = dict(build)
    build["schema"] = SCHEMA
    with _LOCK:
        try:
            fsutil.atomic_write_json(build_path(), build)
        except OSError as exc:
            logger.warning("build: build.json not kept (%s)", type(exc).__name__)
    return build


def update_build(change: Callable[[dict], Any]) -> dict:
    """Read, change and write build.json under one lock."""
    with _LOCK:
        build = load_build()
        change(build)
        return save_build(build)


def find_job(build: dict, jid: Any) -> Optional[dict]:
    return next((j for j in build.get("jobs") or [] if j.get("id") == jid), None)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def public_row(row: dict) -> dict:
    out = {"id": row.get("id"), "kind": row.get("kind"), "label": row.get("label"),
           "detail": row.get("detail", ""), "job_ids": list(row.get("job_ids") or []),
           "done": bool(row.get("done"))}
    # A row her Yes does (going online); a town kept until the lookup can run
    # (shown back to her); a town that needs its rough position typed too.
    if row.get("by_yes"):
        out["by_yes"] = True
    if row.get("pending") and isinstance(row.get("town"), str):
        out["pending"] = row["town"][:100]
    if not row.get("done") and isinstance(row.get("town"), str) and row["town"]:
        out["town"] = row["town"][:100]     # what she typed: never dropped
    if row.get("position"):
        out["position"] = True
    if row.get("said"):
        out["said"] = str(row["said"])[:300]
    return out


def public_job(job: dict, parts: Optional[dict] = None) -> dict:
    out = copy.deepcopy(job)
    out["how"] = how_words(job, parts)
    return out


def part_summary(ref: str, part: Optional[dict]) -> dict:
    """What a page shows of one part: ``{"ref", "name", "class", "kind", "url"?}``."""
    part = part if isinstance(part, dict) else {}
    cls = str(part.get("class") or "")
    out = {"ref": ref, "name": str(part.get("name") or ref), "class": cls,
           "kind": WORDS["class"].get(cls, "")}
    url = str(part.get("url") or "")
    if url.startswith("https://") and len(url) <= 300:
        out["url"] = url            # its own page (an app's project page)
    return out


__all__ = ["MADE_VIEWS", "A", "ACTIONS", "ANSWER_CLASSES", "APP", "B", "BUILDING", "C", "CLASSES", "D", "DATA", "GUIDE",
           "NEEDS", "NEEDS_SETUP", "NOTHING_FOUND", "NOTHING_FOUND_WHY", "NOT_YET", "OUTPUTS", "QUEUED",
           "OWN_SCREEN", "PLANNED", "ROUTES", "SCHEMA", "SETUP_KINDS", "STATUSES",
           "STILL_BUILDING", "TEXT", "TOOL", "VIEW", "WAITING_YES", "WORDS", "WORKING",
           "build_dir", "build_path", "empty_build", "exists", "find_job", "how_words",
           "job_id", "job_problems", "load_build", "new_job", "now_iso", "page_for",
           "part_summary", "public_job", "public_row", "save_build", "update_build"]
