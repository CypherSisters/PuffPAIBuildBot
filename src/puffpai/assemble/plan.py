"""Per capability: use a whole thing as it is, put one together, or say none.

For each of the bot's capabilities (:mod:`puffpai.finding.capabilities`) the
things that cover it are sorted into:

* **use as is** -- a whole app that is set up here (one that runs on this
  computer, a browser app, a built-in app), a skill or a tool server: it
  already does the job, so nothing is put together for it;
* **parts** -- a live card's outside service, an app's own calls (OpenAPI
  operations), a library or a command line tool that is set up but runs
  nothing by itself, a ready-made list, a stream list, and rows of our own
  hand-checked list of free websites (``config/catalog_seeds/apis.json``)
  that are close to the capability in meaning (all-MiniLM, the same reading
  helper the finds use; words only when it is not on this computer). A row
  goes only to the capabilities it fits nearly as well as its best one, so a
  photo service is not also offered for "satellite positions";
* nothing.

A live card and a website row for the same host are one part: the card's
switch and address, with what the row knows that the card does not (a
picture address template, a lookup address, what it needs, what it says
about itself).

The decision is ``use_as_is`` when anything whole covers it, ``assemble`` when
there are only parts, ``not_found`` otherwise. Kept in
``<state>/assemble/plan.json``::

    {"schema": "assemble_plan/v1", "made_at": ..., "capabilities": [
      {"cap", "name", "does", "decision", "app_id", "uses": [...],
       "parts": [{"id", "kind", "name", "door", "url", "service", ...}], "why"}]}

A part's ``door`` is the switch its outside service sits behind (a live card's
own ``app:<id>:feed``); a website row that is not a live card yet has none, and
the sample step says it is not switched on yet.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Optional

from puffpai import fsutil
from puffpai.assemble import PLAN_SCHEMA, state_dir
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

USE_AS_IS = "use_as_is"
ASSEMBLE = "assemble"
NOT_FOUND = "not_found"
SEEDS_PER_CAP = 3
PARTS_MAX = 8
#: How close in meaning (all-MiniLM cosine) a website row must be to a
#: capability: SEED_FIT by itself, or SEED_FLOOR with a shared word. Measured
#: on a real build: right rows 0.41 to 0.73, an unrelated one under 0.30.
SEED_FIT = 0.40
SEED_FLOOR = 0.28
#: Outside its best capability a row needs SEED_FIT and must be within this
#: much of its best one (a photo service is not also "satellite positions").
SEED_MARGIN = 0.12
#: What a part's words may hold (for labels and value meanings).
ABOUT_MAX = 600

_WORD_RE = re.compile(r"[a-z]{3,}")
_STOP = frozenset({"the", "and", "for", "with", "from", "that", "this", "your", "you",
                   "show", "find", "make", "get", "give", "tell", "what", "when", "where",
                   "about", "into", "each", "every", "one", "day", "new", "can", "let",
                   "them", "they", "its", "more", "some", "any", "all", "own", "use"})


def plan_path() -> str:
    return state_dir("plan.json")


def app_id_for(cap_id: str) -> str:
    """The assembled app's id for one capability: ``cap-see-the-sky`` ->
    ``see_the_sky`` (an id the app host and the Bridge accept)."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(cap_id or "").lower().removeprefix("cap-"))
    slug = slug.strip("_")[:32].strip("_")
    if not slug or not slug[0].isalpha():
        slug = f"a_{slug}"[:32].strip("_")
    return slug if len(slug) >= 2 else "app_" + slug


def _words(text: Any) -> set[str]:
    out = set()
    for w in _WORD_RE.findall(str(text or "").lower()):
        if w in _STOP:
            continue
        out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


# ── what each thing is ──────────────────────────────────────────────────────

def _use(thing: dict, how: str) -> dict:
    return {"id": thing["id"], "kind": thing["kind"], "name": thing["name"], "how": how,
            "ref": str(thing.get("ref") or "")}


def _plain_about(*texts: Any) -> str:
    out = " ".join(" ".join(str(t or "").split()) for t in texts if t).strip()
    return re.sub("\\s*[\u2014\u2013]\\s*", ", ", out)[:ABOUT_MAX]


def _card_words(card: Any) -> str:
    """What a card says about its values ("Cloud cover (1 clear, 9 cloudy)")."""
    if not isinstance(card, dict):
        return ""
    label = str(card.get("label") or card.get("title") or "")
    unit = str(card.get("unit") or "")
    return f"Its card shows {label}{f' in {unit}' if unit else ''}." if label else ""


def _feed_part(thing: dict) -> Optional[dict]:
    from puffpai import feeds
    from puffpai.security import egress_doors
    ref = str(thing.get("ref") or "")
    try:
        manifest, feed = feeds._find(ref)  # noqa: SLF001
    except Exception:  # noqa: BLE001 - a card that is gone is not a part
        return None
    if not feed:
        return None
    service = feed.get("service") or {}
    card = feed.get("card") if isinstance(feed.get("card"), dict) else {}
    return {"id": thing["id"], "kind": "feed", "name": thing["name"],
            "door": egress_doors.app_door_id(ref), "url": str(feed.get("url") or ""),
            "service": ref, "host": str(service.get("host") or ""),
            "lookup_url": str(feed.get("lookup_url") or ""),
            "image_template": str(card.get("item_image_template") or ""),
            "about": _plain_about((manifest or {}).get("blurb")
                                  or (manifest or {}).get("description"), _card_words(card))}


def _app_role(thing: dict) -> tuple[str, dict]:
    """``("whole", use)`` for an app that does the job by itself, else
    ``("part", part)`` for a library, command line tool or app calls."""
    if thing.get("builtin"):
        return "whole", _use(thing, "a built-in app")
    ref = str(thing.get("ref") or "")
    try:
        from puffpai.integrate import records, run_here
        rec = records.get(ref) or {}
        if rec.get("mode") in ("static_web", "web_build"):
            return "whole", _use(thing, "its own app")
        now = run_here.status(ref)
        ops = run_here.operations(ref)
        cli = run_here.cli_commands(ref)
    except Exception as exc:  # noqa: BLE001 - unknown: treat it as a part to look at
        logger.debug("assemble plan: %s", type(exc).__name__)
        return "part", {"id": thing["id"], "kind": "library", "name": thing["name"],
                        "app": ref, "door": ""}
    if now.get("can_run"):
        use = _use(thing, "its own app")
        use["calls"] = len(ops)
        return "whole", use
    kind = "cli" if cli else ("openapi" if ops else "library")
    return "part", {"id": thing["id"], "kind": kind, "name": thing["name"], "app": ref,
                    "door": "", "calls": [f"{o['method']} {o['path']}" for o in ops[:12]],
                    "commands": sorted(cli)[:8]}


def _seed_rows() -> list[dict]:
    try:
        from puffpai.catalogs import api_seeds
        return [r for r in api_seeds.load_rows() if api_seeds.row_problem(r) is None]
    except Exception as exc:  # noqa: BLE001 - no list, no website parts
        logger.info("assemble plan: no website list (%s)", type(exc).__name__)
        return []


def _seed_part(row: dict, door: str) -> dict:
    return {"id": f"seed-{row['id']}", "kind": "seed", "name": str(row["name"]),
            "door": door, "service": str(row["id"]), "host": str(row["host"]),
            "url": str(row.get("card_url") or row["sample_endpoint"]),
            "sample_url": str(row["sample_endpoint"]),
            "image_template": str(row.get("image_template") or ""),
            "lookup_url": str(row.get("lookup_url") or ""),
            "needs": [str(n) for n in row.get("needs") or [] if isinstance(n, str)],
            "about": _seed_about(row),
            "meanings": {str(k): str(v) for k, v in (row.get("field_meanings") or {}).items()}}


def _seed_about(row: dict) -> str:
    return _plain_about(row.get("what_it_is"), _card_words(row.get("card")))


#: What a website row adds to a live card reading the same host.
_FROM_SEED = ("image_template", "lookup_url", "needs", "about", "meanings")


def _enrich(part: dict, row: dict) -> None:
    """A live card's part, with what the website row for its host knows that
    the card does not (its picture address template above all)."""
    seed = _seed_part(row, part.get("door") or "")
    for key in _FROM_SEED:
        if key == "about" and part.get(key) and seed.get(key) \
                and seed[key] not in part[key]:
            part[key] = _plain_about(part[key], seed[key])
        elif not part.get(key) and seed.get(key):
            part[key] = seed[key]
    part.setdefault("seed", str(row["id"]))


def _row_text(row: dict) -> str:
    return f"{row.get('name') or ''}. {row.get('what_it_is') or ''}".strip(" .")


def _word_score(cap: dict, row: dict) -> int:
    want = _words(f"{cap.get('name')} {cap.get('does')}")
    terms: set[str] = set()
    for t in [*row.get("match_terms", []), *row.get("topics", [])]:
        terms |= _words(t)
    return len(want & terms)


def _by_words(cap: dict, rows: list[dict], limit: int) -> list[dict]:
    scored = [(s, r) for r in rows if (s := _word_score(cap, r))]
    scored.sort(key=lambda sr: -sr[0])
    return [r for _s, r in scored[:limit]]


def _meaning(caps: list[dict], rows: list[dict]) -> Optional[Any]:
    """rows x caps cosines (all-MiniLM), or None without the reading helper."""
    if not caps or not rows:
        return None
    try:
        from puffpai.catalogs import meaning
        vecs = meaning.embed([meaning.job_text("", c) for c in caps] + [_row_text(r) for r in rows])
    except Exception as exc:  # noqa: BLE001 - no helper: words only
        logger.info("assemble plan: matching by words (%s)", type(exc).__name__)
        return None
    return vecs[len(caps):] @ vecs[:len(caps)].T


def seeds_for_caps(caps: list[dict], rows: list[dict],
                   limit: int = SEEDS_PER_CAP) -> dict[str, list[dict]]:
    """``{capability id: [website row]}``, best first, by meaning. At a row's
    best capability it fits with SEED_FIT, or SEED_FLOOR and a shared word;
    at any other only with SEED_FIT and within SEED_MARGIN of its best."""
    sims = _meaning(caps, rows)
    if sims is None:
        return {c["id"]: _by_words(c, rows, limit) for c in caps}
    out: dict[str, list[dict]] = {}
    for k, cap in enumerate(caps):
        scored = []
        for i, row in enumerate(rows):
            sim, best = float(sims[i, k]), float(sims[i].max())
            if sim >= best:     # its best capability: a shared word is enough above the floor
                fits = sim >= SEED_FIT or (sim >= SEED_FLOOR and _word_score(cap, row) > 0)
            else:               # another one: only a strong fit, close to its best
                fits = sim >= SEED_FIT and sim >= best - SEED_MARGIN
            if fits:
                scored.append((sim, row))
        scored.sort(key=lambda sr: -sr[0])
        out[cap["id"]] = [r for _s, r in scored[:limit]]
    return out


def matching_seeds(cap: dict, rows: list[dict], limit: int = SEEDS_PER_CAP) -> list[dict]:
    """Website rows for one capability, best first (see :func:`seeds_for_caps`;
    with one capability there is no other to compare with)."""
    return seeds_for_caps([cap], rows, limit).get(cap["id"], [])


# ── the plan ────────────────────────────────────────────────────────────────

def decide(cap: dict, things: list[dict], seeds: list[dict],
           live_doors: Optional[dict[str, str]] = None) -> dict:
    """One capability's row. *live_doors*: host -> the switch of a live card
    already reading that host (a website row is fetched under that one)."""
    live_doors = live_doors or {}
    uses: list[dict] = []
    parts: list[dict] = []
    for t in things:
        kind = t.get("kind")
        if kind == "skill":
            uses.append(_use(t, "a skill your bot follows"))
        elif kind == "tool_server":
            uses.append(_use(t, "a tool server"))
        elif kind == "book":
            uses.append(_use(t, "a book it reads"))
        elif kind == "app":
            role, got = _app_role(t)
            (uses if role == "whole" else parts).append(got)
        elif kind == "feed":
            part = _feed_part(t)
            if part:
                if isinstance(t.get("seed_row"), dict):
                    _enrich(part, t["seed_row"])
                parts.append(part)
        elif kind == "data":
            parts.append({"id": t["id"], "kind": "data", "name": t["name"],
                          "door": "", "ref": str(t.get("ref") or "")})
    by_host = {str(p.get("host")).lower(): p for p in parts if p.get("host")}
    for row in seeds:
        host = str(row["host"]).lower()
        if host in by_host:
            _enrich(by_host[host], row)
            continue
        parts.append(_seed_part(row, live_doors.get(host, "")))
    parts = parts[:PARTS_MAX]
    if uses:
        decision, why = USE_AS_IS, f"{uses[0]['name']} already does this ({uses[0]['how']})."
    elif parts:
        decision, why = ASSEMBLE, (f"Putting it together from {len(parts)} "
                                   f"part{'s' if len(parts) != 1 else ''}.")
    else:
        decision, why = NOT_FOUND, "Nothing found for this yet."
    return {"cap": cap["id"], "name": cap["name"], "does": cap.get("does", ""),
            "decision": decision, "app_id": app_id_for(cap["id"]) if decision == ASSEMBLE
            else "", "uses": uses, "parts": parts, "why": why}


def _live_doors() -> dict[str, str]:
    """Host -> the switch of the live card that reads it."""
    out: dict[str, str] = {}
    try:
        from puffpai import feeds
        from puffpai.security import egress_doors
        for manifest, feed in feeds.feed_apps():
            app_id = str(manifest.get("id") or "")
            host = str((feed.get("service") or {}).get("host") or "").lower()
            if app_id and host:
                out.setdefault(host, egress_doors.app_door_id(app_id))
    except Exception:  # noqa: BLE001
        pass
    return out


def _enrich_feeds_everywhere(by_cap: dict, rows: list[dict]) -> None:
    """A live card's things carry their host's website row, whichever
    capability the row itself went to (the card's own picture template)."""
    by_host = {str(r.get("host") or "").lower(): r for r in rows}
    for things in by_cap.values():
        for t in things:
            if t.get("kind") == "feed" and "seed_row" not in t:
                try:
                    from puffpai import feeds
                    _m, feed = feeds._find(str(t.get("ref") or ""))  # noqa: SLF001
                    host = str(((feed or {}).get("service") or {}).get("host") or "").lower()
                except Exception:  # noqa: BLE001
                    host = ""
                t["seed_row"] = by_host.get(host)


NOTHING_DOES_WHY = "Found nothing that does this yet."
#: Among one job's outside parts, only those this close (all-MiniLM) to the
#: best: a space weather service 0.51 keeps an Earth weather forecast 0.41 out.
PART_MARGIN = 0.08


def only_fitting(row: dict, cap: dict, caps: list[dict], fit: Any = None) -> dict:
    """The row with only the outside parts whose own words do this job
    (:func:`puffpai.assemble.accept.source_reason`: close enough in meaning,
    and not a better fit for another of the bot's jobs). Nothing left is
    ``not_found``: found nothing that does this, never a stand-in."""
    if row["decision"] != ASSEMBLE:
        return row
    from puffpai.assemble import accept
    kept = [p for p in row["parts"] if p.get("kind") not in ("feed", "seed") or not
            accept.source_reason(cap, accept.source_words(p), caps, fit)]
    sims = {id(p): accept.source_fit(cap, accept.source_words(p), fit) for p in kept
            if p.get("kind") in ("feed", "seed")}
    best = max((v for v in sims.values() if v is not None), default=None)
    if best is not None:     # among this job's parts, only those close to the best one
        kept = [p for p in kept if sims.get(id(p)) is None or sims[id(p)] >= best - PART_MARGIN]
    if len(kept) == len(row["parts"]):
        return row
    out = dict(row, parts=kept, left_out=[p.get("name") for p in row["parts"] if p not in kept])
    if not kept:
        out.update(decision=NOT_FOUND, app_id="", why=NOTHING_DOES_WHY)
    else:
        out["why"] = (f"Putting it together from {len(kept)} "
                      f"part{'s' if len(kept) != 1 else ''}.")
    return out


def build_plan(caps: Optional[list[dict]] = None, *, save: bool = True) -> dict:
    """Decide every capability and keep the plan."""
    from puffpai.finding import capabilities
    caps = capabilities.load() if caps is None else caps
    by_cap = capabilities.covering(caps=caps) if caps else {}
    rows = _seed_rows()
    doors = _live_doors()
    seeds = seeds_for_caps(caps, rows) if caps else {}
    _enrich_feeds_everywhere(by_cap, rows)
    plan = {"schema": PLAN_SCHEMA, "made_at": int(time.time()),
            "capabilities": [only_fitting(decide(c, by_cap.get(c["id"], []),
                                                 seeds.get(c["id"], []), doors), c, caps)
                             for c in caps]}
    if save:
        try:
            fsutil.atomic_write_json(plan_path(), plan)
        except OSError as exc:
            logger.warning("assemble plan: not kept (%s)", type(exc).__name__)
    return plan


def load_plan() -> dict:
    got = fsutil.read_json(plan_path(), default=None)
    if isinstance(got, dict) and got.get("schema") == PLAN_SCHEMA \
            and isinstance(got.get("capabilities"), list):
        return got
    return {"schema": PLAN_SCHEMA, "made_at": 0, "capabilities": []}


def exists() -> bool:
    return os.path.isfile(plan_path())


__all__ = ["ASSEMBLE", "NOT_FOUND", "USE_AS_IS", "app_id_for", "build_plan", "decide",
           "exists", "load_plan", "matching_seeds", "only_fitting", "plan_path",
           "seeds_for_caps"]
