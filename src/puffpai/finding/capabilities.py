"""What this bot can do: its capabilities, and which thing does each one.

The wizard's search starts by asking the brain what the bot must be able to
do for her goal (``find.jobs_for_goal``: "Teach a piano lesson", "Make music
from a description"). Those jobs are kept here as the bot's capabilities, in
plain words, so the build is planned around them:

* every find is tagged with the capability it was found for, and an install
  keeps the tag (``install._add_entry``). Finds from the whole-bot search
  ("Everything for a ... bot") or her own words are tagged by meaning
  (:func:`match`), and so is a thing with no tag (a live card, an older
  install) when coverage is worked out;
* :func:`coverage` says, per capability, which installed thing covers it and
  how (a skill, a tool server, its own app, a live card...), or that nothing
  does yet (the dashboard's "What this bot can do" and the closing screen);
* :func:`prompt_block` gives the brain the same map in a few lines, so it
  picks the right thing for each job.

Kept in ``<state>/finding/capabilities.json`` as ``{"goal", "made_at",
"capabilities": [{"id", "name", "does"}]}``. Which thing covers what is never
stored: it is read from the install entries each time, so a thing she takes
out stops covering at once. A bot built before this file existed has no
capabilities and everything keeps working (nothing is shown).

Goal-agnostic: nothing here knows any subject.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Optional

from puffpai import fsutil
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

CAP_MAX = 10
NAME_MAX = 80
DOES_MAX = 160
#: Things named per capability in the brain's map.
PROMPT_THINGS = 3
_ID_RE = re.compile(r"^cap-[a-z0-9-]{1,60}$")
#: How close in meaning (all-MiniLM cosine) a thing must be to a capability
#: to cover it when it was not found for it. Measured on a real piano build:
#: the piano teachers score 0.52-0.68 on "teaches piano", a piano analytics
#: library 0.44 and unrelated finds under 0.40.
CAP_FIT = 0.45

#: How a kind of thing does a job, in her words.
HOW = {"skill": "a skill your bot follows", "tool_server": "a tool server",
       "app": "its own app", "data": "a ready-made list", "book": "a book it reads",
       "feed": "a live card"}
NOTHING_YET = "Nothing found for this yet."

PROMPT_HEAD = ("## What this bot does\n"
               "The jobs this bot was built for, and the thing that does each one. "
               "For one of these jobs, use its thing first.\n")
PROMPT_GAP = "nothing yet (search with find_things if asked)"


def _path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "finding", "capabilities.json")


def _plain(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    text = re.sub("\\s*[\u2014\u2013]\\s*", ", ", text)
    return text[:limit].rstrip(" ,")


def cap_id(name: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:60].strip("-")
    return f"cap-{slug}" if slug else ""


def load() -> list[dict]:
    """The bot's capabilities, ``[{"id", "name", "does"}]``; [] when none were kept."""
    data = fsutil.read_json(_path(), default=None)
    rows = data.get("capabilities") if isinstance(data, dict) else None
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and _ID_RE.match(str(row.get("id") or "")) \
                and row.get("name"):
            out.append({"id": row["id"], "name": _plain(row["name"], NAME_MAX),
                        "does": _plain(row.get("does"), DOES_MAX)})
    return out[:CAP_MAX]


def get(cap: Any) -> Optional[dict]:
    return next((c for c in load() if c["id"] == str(cap or "")), None)


def save(goal: Any, jobs: list[dict]) -> list[dict]:
    """Keep the jobs worked out from *goal* as the bot's capabilities, and tag
    each job with its capability id (``job["cap"]``) so its finds carry it.
    The whole-bot job ("Everything for a ... bot") is not a capability."""
    caps: list[dict] = []
    for job in jobs:
        if job.get("whole") or job.get("own"):
            continue    # the bot's subject itself, or her own search words
        name = _plain(job.get("name"), NAME_MAX)
        cid = cap_id(name)
        if not cid or any(c["id"] == cid for c in caps):
            continue
        job["cap"] = cid
        caps.append({"id": cid, "name": name, "does": _plain(job.get("does"), DOES_MAX)})
        if len(caps) >= CAP_MAX:
            break
    if not caps:
        return []
    try:
        fsutil.atomic_write_json(_path(), {"goal": _plain(goal, 400),
                                           "made_at": int(time.time()),
                                           "capabilities": caps})
    except OSError as exc:
        logger.warning("capabilities: not kept (%s)", type(exc).__name__)
    return caps


def save_from_needs(needs: Any, goal: Any = "") -> list[dict]:
    """Keep the jobs she ticked on the wizard's jobs step (``needs/v1``, the
    console ``puffpai setup`` path, which never runs the web wizard's search)
    as the bot's capabilities. [] when there are none."""
    rows = needs.get("jobs") if isinstance(needs, dict) else None
    jobs = [j for j in rows if isinstance(j, dict)] if isinstance(rows, list) else []
    ticked = [j for j in jobs if j.get("ticked")]
    picked = [{"name": j.get("name"), "does": j.get("does") or ""}
              for j in (ticked or [j for j in jobs if "ticked" not in j])]
    subject = goal or (needs.get("kind") if isinstance(needs, dict) else "")
    return save(subject, picked) if picked else []


def find_text(found: dict) -> str:
    """What a find is, for :func:`match`: its name, its own short name (a
    name in another language often has an English slug) and what it does."""
    name = " ".join(str(found.get("name") or "").split())
    slug = str(found.get("id") or "").rsplit("/", 1)[-1].split(":")[-1]
    what = " ".join(str(found.get("what") or found.get("description") or "").split())
    head = f"{name} ({slug})" if slug and slug.lower() != name.lower() else name
    return f"{head}. {what}".strip(" .")[:300]


def match(texts: list[str], caps: Optional[list[dict]] = None) -> list[list[str]]:
    """For each text, the capability ids it is close to in meaning (cosine
    >= CAP_FIT), closest first. [] for each when the bot has no capabilities
    or the reading helper is not on this computer."""
    caps = load() if caps is None else caps
    texts = [str(t or "") for t in texts]
    if not caps or not texts:
        return [[] for _ in texts]
    try:
        from puffpai.catalogs import meaning
        vecs = meaning.embed([meaning.job_text("", c) for c in caps] + texts)
    except Exception as exc:  # noqa: BLE001 - no helper, no guess
        logger.info("capabilities: no meaning match (%s)", type(exc).__name__)
        return [[] for _ in texts]
    sims = vecs[len(caps):] @ vecs[:len(caps)].T
    out = []
    for row in sims:
        order = sorted(range(len(caps)), key=lambda k: -float(row[k]))
        out.append([caps[k]["id"] for k in order if float(row[k]) >= CAP_FIT])
    return out


def caps_of(entry: dict) -> list[str]:
    """The capability ids an install entry covers."""
    raw = entry.get("caps") if isinstance(entry, dict) else None
    return [str(c) for c in raw if isinstance(c, str)] if isinstance(raw, list) else []


# ── who covers what ─────────────────────────────────────────────────────────

def _things_by_id() -> dict[str, dict]:
    from puffpai import things
    try:
        return {t["id"]: t for t in things._everything()}  # noqa: SLF001
    except Exception:  # noqa: BLE001 - no list, nothing covers
        return {}


def _covering(have: Optional[dict[str, dict]] = None,
              caps: Optional[list[dict]] = None) -> dict[str, list[dict]]:
    """``{capability id: [thing]}`` from the install entries still there,
    then the live cards and installs with no tag, matched by meaning."""
    from puffpai import things
    from puffpai.finding import install
    have = _things_by_id() if have is None else have
    out: dict[str, list[dict]] = {}

    def add(cap: str, thing: dict) -> None:
        if all(t["id"] != thing["id"] for t in out.setdefault(cap, [])):
            out[cap].append(thing)

    untagged: dict[str, dict] = {}
    for e in install.entries():
        thing = have.get(things.id_for_install(e.get("undo")) or "")
        if thing is None:
            continue
        tags = caps_of(e)
        if not tags:
            untagged[thing["id"]] = thing
        for cap in tags:
            add(cap, thing)
    untagged = {tid: t for tid, t in untagged.items()
                if not any(t2["id"] == tid for ts in out.values() for t2 in ts)}
    for t in have.values():
        if t.get("kind") == "feed":
            untagged.setdefault(t["id"], t)
    if untagged:
        rows = list(untagged.values())
        got = match([f"{t['name']}. {t.get('blurb') or ''}".strip(" .") for t in rows], caps)
        for thing, ids in zip(rows, got):
            for cap in ids:
                add(cap, thing)
    return out


def covering(caps: Optional[list[dict]] = None) -> dict[str, list[dict]]:
    """``{capability id: [thing]}``: the things that cover each capability."""
    return _covering(caps=caps)


def _proofs() -> dict[str, dict]:
    """The Assemble step's report: capability id -> its row (working / not yet)."""
    try:
        from puffpai.assemble import report
        return report.by_cap()
    except Exception:  # noqa: BLE001 - no report, nothing proven
        return {}


# ── the one build (puffpai.build) ───────────────────────────────────────────

def _build_jobs() -> list[dict]:
    """The jobs of build.json when the bot has one (it then decides who does
    what, not the install tags below); [] otherwise."""
    try:
        from puffpai.build import schema
        if not schema.exists():
            return []
        return [j for j in schema.load_build()["jobs"] if isinstance(j, dict)]
    except Exception:  # noqa: BLE001 - no build, the old map
        return []


def _job_pick(job: dict) -> dict:
    from puffpai.build import schema
    try:
        part = (schema.load_build().get("parts") or {}).get((job.get("pick") or {}).get("ref"))
    except Exception:  # noqa: BLE001
        part = None
    return part if isinstance(part, dict) else {}


def _coverage_from_build(jobs: list[dict]) -> list[dict]:
    from puffpai.build import schema
    rows = []
    for j in jobs:
        pick = _job_pick(j)
        named = pick.get("class") in schema.ANSWER_CLASSES   # "Answers from <name>"
        how = schema.how_words(j, {pick.get("ref"): pick} if pick else None)
        mine = ([{"id": pick.get("ref"), "name": pick.get("name") or pick.get("ref"),
                  "kind": pick.get("class", ""), "how": how,
                  "page": f"/dashboard/jobs/{j['id']}"}] if pick else [])
        rows.append({"id": j.get("cap") or j["id"], "name": j["name"], "does": j.get("does", ""),
                     "covered": j.get("route") != schema.D, "things": mine,
                     "line": f"{how} ({pick['name']})" if pick.get("name") and not named
                     else how,
                     "find": j["name"], "working": j.get("status") == schema.WORKING,
                     "proof": "" if j.get("status") == schema.WORKING or not j.get("reason")
                     else f"Not yet: {j['reason']}"})
    return rows


def coverage() -> list[dict]:
    """``[{"id", "name", "does", "covered", "things": [{"id", "name", "kind",
    "how", "page"}], "line", "find", "working", "proof"}]``, one row per
    capability. *find* is the words "Find something for this" searches with.
    *working* is True only when the Assemble step proved it with a real
    question; *proof* is that step's line ("Not yet: ..."), "" before it ran.
    A bot with build.json: one row per job, from it."""
    from puffpai import things
    jobs = _build_jobs()
    if jobs:
        return _coverage_from_build(jobs)
    caps = load()
    if not caps:
        return []
    by_cap = _covering(caps=caps)
    proofs = _proofs()
    rows = []
    for c in caps:
        mine = [{"id": t["id"], "name": t["name"], "kind": t["kind"],
                 "how": HOW.get(t["kind"], "a thing it has"),
                 "page": t.get("open") or things.PAGE.format(id=t["id"])}
                for t in by_cap.get(c["id"], [])]
        line = ("; ".join(f"{t['name']} ({t['how']})" for t in mine) if mine
                else NOTHING_YET)
        proof = proofs.get(c["id"]) or {}
        rows.append({**c, "covered": bool(mine), "things": mine, "line": line,
                     "find": c["name"], "working": proof.get("state") == "working",
                     "proof": str(proof.get("line") or "")})
    return rows


def covers(thing_id: Any) -> list[str]:
    """The names of the capabilities one thing covers (its page says so)."""
    caps = load()
    if not caps:
        return []
    by_cap = _covering(caps=caps)
    return [c["name"] for c in caps
            if any(t["id"] == thing_id for t in by_cap.get(c["id"], []))]


def report_lines() -> list[str]:
    """One line per capability for the closing screen, with what the Assemble
    step proved when it has run ("Working: ..." or "Not yet: ...")."""
    out = []
    for r in coverage():
        proof = r["proof"] if r["working"] or (
            r["covered"] and r["proof"].startswith("Not yet")) else ""
        out.append(f"{r['name']}: {r['line']}" + (f" {proof}" if proof else ""))
    return out


def _use(thing: dict) -> str:
    """How the brain uses one thing, in a few words."""
    kind, ref = thing["kind"], str(thing.get("ref") or "")
    if kind == "skill":
        return f'skill {ref} (load it with skill_view("{ref}"))'
    if kind == "tool_server":
        try:
            from puffpai import mcp_client
            prefix = mcp_client.tool_name(ref, "x")[:-1]
            return f"tool server {thing['name']} (its tools start with {prefix})"
        except Exception:  # noqa: BLE001
            return f"tool server {thing['name']}"
    if kind == "feed":
        try:
            from puffpai import feeds
            _m, feed = feeds._find(ref)  # noqa: SLF001
            if feeds.lookup_template(feed):
                return (f"live card {thing['name']} (look a subject up with "
                        f"{feeds.lookup_tool_name(ref)})")
        except Exception:  # noqa: BLE001
            pass
        return f"live card {thing['name']} (its newest items only, no search)"
    if kind == "book":
        return f"the book {thing['name']} (search your knowledge)"
    if kind == "app":
        return f"the app {thing['name']} (the owner opens it on its page)"
    return f"{thing['name']} ({HOW.get(kind, 'a thing it has')})"


def _assembled_use(cap: str) -> str:
    """The proven assembled app for a capability, as the brain uses it."""
    try:
        from puffpai.assemble import apps_note
        tools = apps_note.tools_for_cap(cap)
    except Exception:  # noqa: BLE001 - no Assemble step, nothing to name
        return ""
    return f"its own app (call {tools[0]})" if tools else ""


def prompt_block() -> str:
    """The brain's map: each capability and the thing that does it, or ""
    when the bot has no capabilities kept."""
    jobs = _build_jobs()
    if jobs:
        lines = []
        for r in _coverage_from_build(jobs):
            lines.append(f"- {r['name']}: {r['line'] if r['covered'] else PROMPT_GAP}")
        return PROMPT_HEAD + "\n".join(lines)
    caps = load()
    if not caps:
        return ""
    have = _things_by_id()
    by_cap = _covering(have, caps)
    lines = []
    for c in caps:
        mine = by_cap.get(c["id"], [])[:PROMPT_THINGS]
        uses = [_use(t) for t in mine]
        app = _assembled_use(c["id"])
        if app:
            uses.insert(0, app)
        lines.append(f"- {c['name']}: {'; '.join(uses) if uses else PROMPT_GAP}")
    return PROMPT_HEAD + "\n".join(lines)


__all__ = ["CAP_FIT", "HOW", "NOTHING_YET", "cap_id", "caps_of", "coverage", "covering", "covers",
           "find_text", "get", "load", "match", "prompt_block", "report_lines", "save",
           "save_from_needs"]
