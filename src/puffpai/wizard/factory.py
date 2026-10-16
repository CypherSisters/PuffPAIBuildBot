"""The factory's two ends: what the summary offers, and what "Looks good" builds.

The wizard's one summary gets its lines from the
parts of the build that find things, and the owner's "Looks good" is carried
out by the parts that make things. Nothing here knows a topic: every line
comes from a catalog lookup for the owner's own goal words.

**What the summary offers** (one provider per section, ids unique):

* knowledge -- :func:`puffpai.catalogs.knowledge_resolver.proposal_items`,
  minus any book the sources plan already lists (the plan's own line keeps its
  download path, so a book is never offered or fetched twice);
* gadgets -- the ``devices`` lines of :func:`puffpai.catalogs.live.proposal_items`;
* updates -- :func:`puffpai.offers.proposal_items`;
* private -- :func:`puffpai.security.privacy_explainer.proposal_items`.

**What "Looks good" builds** (:func:`apply_approved`, a runner entry after the
build record is written, so the terminal and the browser run the same code):
each switched-on line by its ``apply.kind``. Every outcome is written to the
run record under ``factory``; every failure becomes a plain gap on the closing
screen. Nothing is ever skipped silently.

**What it no longer makes.** Live cards. What the bot reads from
an outside service is an ingredient of a job in the one build plan
(:mod:`puffpai.build`), found and checked there and made only after the
owner's one Yes on the setup list. The factory makes books, daily updates
and the look; a ``live_feed`` / ``live_source`` line on an older approved
summary is left to the build plan and recorded as such.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import copy
import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: apply kinds the cards themselves carry out (the brain, the sources and
#: integrate cards, the hub card, the posture's switches). Nothing to do here.
CARD_KINDS = ("model", "model_pull", "source_fetch", "door", "home_assistant")

#: apply kinds that are notes, not switches.
NOTE_KINDS = ("none", "look_yourself")

#: apply kinds carried out here. Another part cannot take one of these over.
BUILT_IN_KINDS = ("knowledge.add", "routine", "theme", "key", "ha_integration")

#: apply kinds the one build plan carries out, never the factory (live cards
#: became ingredients of a job). Nothing registers them either.
BUILD_PLAN_KINDS = ("live_feed", "live_source")

#: What a plan applier may say happened to its line.
APPLIER_STATUSES = ("added", "added_off", "held", "waiting", "refused", "failed")
#: Statuses that mean the thing is really there now (switched on or off).
_ADDED_STATUSES = ("added", "added_off")

APPLIER_FAILED_LINE = "Something went wrong adding this. Nothing else changed."

#: apply kind -> fn(item, ctx). Filled by other parts of the build at start-up
#: (code projects, data imports, pictures, the locked box).
_PLAN_APPLIERS: dict[str, Any] = {}


def register_plan_applier(kind: str, fn) -> None:
    """Let another part of the build carry out summary lines of one apply kind.

    ``fn(item, ctx)`` gets a deep copy of the approved summary line and a small
    context (``ctx.shared``, ``ctx.online``, ``ctx.progress(line)``,
    ``ctx.device_id``) and returns ``{"status": "added|added_off|held|
    waiting|refused|failed", "line": str, "lines": [str]}``. It is called
    from :func:`apply_approved` only for kinds this module does not carry
    out itself.

    A module dict, like ``add_more.register_applier``: registering a kind
    again replaces the earlier function (so a reloaded module or a test fake
    just takes its place). Three things raise ``ValueError`` / ``TypeError``
    instead of being stored where they could never run: a kind that is not
    a short lower-case name, a built-in, card or note kind (those lines are
    carried out before the registry is looked at), and a *fn* that is not
    callable.
    """
    if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_.]{0,39}", kind):
        raise ValueError("A plan applier kind is a short lower-case name.")
    if (kind in BUILT_IN_KINDS or kind in CARD_KINDS or kind in NOTE_KINDS
            or kind in BUILD_PLAN_KINDS):
        raise ValueError(f"'{kind}' is carried out by the build itself.")
    if not callable(fn):
        raise TypeError("A plan applier is a function.")
    _PLAN_APPLIERS[kind] = fn


class _ApplierContext:
    """What a registered plan applier is handed."""

    def __init__(self, shared: dict, online: bool, device_id: Optional[str]) -> None:
        self.shared = shared
        self.online = bool(online)
        self.device_id = device_id
        self._progress = None

    def progress(self, line: str) -> None:
        if self._progress is not None:
            self._progress(line)


def _approving_device(ctx: Any, state: Any) -> Optional[str]:
    """The device that said "Looks good", when the run records it (else None)."""
    sources = (state if isinstance(state, dict) else {}, ctx)
    for source in sources:
        value = (source.get("device_id") if isinstance(source, dict)
                 else getattr(source, "device_id", None))
        if isinstance(value, str) and value.strip():
            return value.strip()[:80]
    return None


def _plain_line(value: Any) -> str:
    text = value if isinstance(value, str) else ""
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    return re.sub(r"\s+", " ", text).strip()[:300]


def _run_applier(fn, item: dict, actx: _ApplierContext, stream,
                 said: Optional[list] = None) -> dict:
    """Call one registered applier; anything odd is a plain failure. *said*
    is what this run has shown so far: a result line already shown (two plan
    lines served by one Mood board) is not shown a second time."""
    streamed: list[str] = []

    def progress(line: Any) -> None:
        text = _plain_line(line)
        if text:
            streamed.append(text)
            stream(text)

    actx._progress = progress
    try:
        # A deep copy: the applier may change its line however it likes, the
        # approved summary she said yes to stays exactly as she saw it.
        got = fn(copy.deepcopy(item), actx)
    except Exception as exc:  # noqa: BLE001 - one line never sinks the rest
        logger.warning("factory: applier for %s failed: %s", item.get("id"),
                       type(exc).__name__)
        got = None
    finally:
        actx._progress = None   # a late progress call after return goes nowhere
    if not isinstance(got, dict) or got.get("status") not in APPLIER_STATUSES:
        stream(APPLIER_FAILED_LINE)
        return {"status": "failed", "line": APPLIER_FAILED_LINE, "lines": []}
    raw_lines = got.get("lines")
    lines = [_plain_line(x) for x in raw_lines] if isinstance(raw_lines, (list, tuple)) else []
    lines = [x for x in lines if x and x not in streamed]
    for text in lines:
        stream(text)
    line = _plain_line(got.get("line"))
    if line and not (said is not None and line in said):
        stream(line)
    return {"status": got["status"], "line": line, "lines": lines}


# -- summary providers --------------------------------------------------------

def _knowledge_terms(goal: str) -> list[str]:
    """The goal's search words with everything personal taken out (so a
    Wikipedia book is never named "wedding, planner, sister and getting")."""
    from puffpai.catalogs import goal_words
    return list(goal_words.search_terms(goal))


def knowledge_items(sctx: Any) -> list[dict]:
    """Books from the trusted libraries the sources plan does not list yet."""
    from puffpai.catalogs import knowledge_resolver

    if getattr(sctx.args, "skip_knowledge", False):
        return []  # the owner asked for no knowledge step at all
    sources = (getattr(sctx, "previews", None) or {}).get("sources") or {}
    planned = {str(item.get("id")) for item in sources.get("items") or ()
               if isinstance(item, dict)}
    out = []
    terms = _knowledge_terms(sctx.goal)
    only_topics = _with_jobs(sctx)
    if only_topics:
        # On a plan drawn from her jobs, this word-matched list keeps only its
        # topic packs: a whole encyclopedia fits any goal, and old books and
        # textbooks come from find_parts, checked against the job by meaning
        # and the reader's level (not a 13.4 GB Wikipedia, a children's
        # encyclopedia and a rat-catcher's memoir on a job hunt).
        found = knowledge_resolver.find(sctx.goal, terms, live=False, limit=32)
        general = {str(c.get("id")) for c in found.get("candidates") or () if c.get("general")}
    for item in knowledge_resolver.proposal_items(sctx.goal, terms):
        if str(item["apply"]["ref"]) in planned:
            continue
        if only_topics:
            ref = str(item["apply"]["ref"])
            cand = knowledge_resolver.candidate_by_id(ref) or {}
            if ref in general or knowledge_resolver.tier_of(cand) in (
                    "gutenberg", "openstax", "wikipedia_topic"):
                continue
        if sctx.internet is not True:
            # Nothing is ticked for a download the build cannot make.
            item = dict(item, default_on=False)
        out.append(item)
    return out


def _with_jobs(sctx: Any) -> bool:
    """Is this plan drawn from the owner's jobs (the needs step ran, one is kept)?"""
    from puffpai.wizard import proposal
    needs = proposal.needs_of(getattr(sctx, "shared", None))
    return bool(needs) and any(isinstance(j, dict) and j.get("ticked")
                               for j in needs.get("jobs") or ())


def _live_catalog():
    from puffpai.catalogs import live
    return live


def candidate(ref: str) -> dict | None:
    """One live-data candidate by id, from the saved lists. Offline."""
    live = _live_catalog()
    try:
        corpus, _idf = live._live_corpus()
    except Exception:  # noqa: BLE001 - no lists means no candidate
        return None
    for cand in corpus:
        if str(cand.get("id")) == str(ref):
            return dict(cand)
    return None


def device_items(sctx: Any) -> list[dict]:
    # "Your ..., if you have Home Assistant" lines only on a build where Home
    # Assistant is in use: set up already, or its address and key given now.
    args = getattr(sctx, "args", None)
    if getattr(args, "skip_ha", False):
        return []
    stated = bool(getattr(args, "ha_url", None) and getattr(args, "ha_token", None))
    try:
        from puffpai import setup_ops
        configured = bool(setup_ops.ha_configured())
    except Exception:  # noqa: BLE001 - unreadable settings: not in use
        configured = False
    if not (stated or configured):
        return []
    live = _live_catalog()
    return [item for item in live.proposal_items(sctx.goal)
            if item.get("section") == "devices"]


#: A job that asks for something to come to her regularly (a reminder, a
#: daily note, an update): only then is a daily update offered.
_REGULAR_RE = re.compile(r"\b(?:remind\w*|reminders?|daily|weekly|nightly|each (?:day|week|"
                         r"morning|evening|night)|every (?:day|week|morning|evening|night)|"
                         r"updates?|digest|alerts?|notif\w*)\b", re.I)


def update_items(sctx: Any) -> list[dict]:
    """The daily update line. With the owner's jobs known, only when one of
    them asks for something regular (not on every plan, named after a
    grandson's birthday)."""
    from puffpai import offers
    from puffpai.wizard import proposal
    needs = proposal.needs_of(getattr(sctx, "shared", None))
    if needs is not None:
        jobs = [j for j in needs.get("jobs") or () if isinstance(j, dict) and j.get("ticked")]
        if not any(_REGULAR_RE.search(f"{j.get('name') or ''} {j.get('does') or ''}")
                   for j in jobs):
            return []
    return offers.proposal_items(sctx.goal, list(sctx.terms or []))


def private_items(sctx: Any) -> list[dict]:
    from puffpai.security import privacy_explainer
    # These say what is always true of every build (the vault, the chat
    # scrubbing, where the model runs). Nothing on them can be switched, so
    # they are notes: "Change something" does not offer a switch that does
    # nothing, and "Looks good" has nothing to carry out.
    return [dict(item, apply={"kind": "none", "ref": ""}, needs_yes=False,
                 default_on=True)
            for item in privacy_explainer.proposal_items(
                sctx.goal, list(sctx.terms or []))]


def _owner_age(sctx: Any) -> Any:
    interview = (getattr(sctx, "shared", None) or {}).get("interview") or {}
    for key in ("builder_age", "age"):
        if interview.get(key) not in (None, ""):
            return interview[key]
    return getattr(getattr(sctx, "args", None), "age", None)


def key_items(sctx: Any) -> list[dict]:
    """Sign-up key lines ("needs a free key"), off until the owner ticks one.
    Only on a build that may use the internet: a key opens nothing at home."""
    if sctx.internet is not True:
        return []
    from puffpai.security import keys
    return keys.proposal_items(sctx.goal, list(sctx.terms or []),
                               age=_owner_age(sctx))


def looks_items(sctx: Any) -> list[dict]:
    """At most one "How it looks" line: on only for a word match in the lists."""
    from puffpai.looks import proposals
    return proposals.wizard_items(sctx)


#: (name, provider) in summary order. :mod:`puffpai.wizard.proposal` runs them
#: after its own built-ins, so a built-in line with the same id wins.
PROVIDERS = (
    ("factory:knowledge", knowledge_items),
    ("factory:updates", update_items),
    ("factory:private", private_items),
    ("factory:devices", device_items),
    ("factory:keys", key_items),
    ("factory:looks", looks_items),
)


# -- "Looks good" builds it ---------------------------------------------------

def _posture(ctx: Any) -> dict:
    shared = getattr(ctx, "shared", None) or {}
    posture = shared.get("posture")
    if not isinstance(posture, dict):
        sections = getattr(ctx, "_sections", None) or {}
        posture = (sections.get("posture") or {}).get("posture") or {}
    return posture if isinstance(posture, dict) else {}


def build_online(ctx: Any) -> bool:
    """Is this build allowed to go online while building? The recorded answer."""
    from puffpai import build_posture
    posture = _posture(ctx)
    if not posture:
        return False
    return bool(build_posture.gates(posture).get("may_download_packs"))


def _gap(what: str, why: str, fix: dict | None = None) -> dict:
    return {"skill": "factory", "what": what, "why": why,
            "impact": "Everything else was built. This one thing was not.",
            "fix": fix or {"kind": "command", "id": "setup.rerun", "args": {}}}


def _add_knowledge(ref: str, label: str) -> dict:
    from puffpai.knowledge import find_more
    if find_more.is_text_book(ref):
        # A text book (a Wikipedia topic book fetches at one article a
        # second) is never waited for: it is ordered for the background
        # learner, and the closing screen shows how far it has got.
        refused = _text_book_refusal(ref)
        if refused:
            return refused
        try:
            lid = find_more.start_in_background(ref, label, via="wizard")
        except find_more.OrderBlocked as exc:
            return {"ok": False, "why": exc.line, "fix": {"kind": "none"}}
        return {"ok": True, "learn_id": lid,
                "line": f"{label}: fetched and learned in the background the "
                        f"first time your bot runs. It can answer questions a "
                        f"few seconds after it arrives."}
    got = find_more.add(ref, size_acknowledged=True, via="wizard")
    if got.get("ok"):
        return {"ok": True, "line": f"{label}: downloaded. It is learned when "
                                     f"your bot next starts."}
    return {"ok": False, "why": str(got.get("reason") or "The download did not "
                                                          "finish."),
            "fix": {"kind": "command", "id": "source.fetch",
                    "args": {"source_id": ref}}}


def _text_book_refusal(ref: str) -> Optional[dict]:
    """Why a text book cannot be ordered now (no network asked), or None."""
    from puffpai.knowledge import find_more
    posture = find_more.recorded_posture()
    if not find_more.online_allowed(posture):
        return {"ok": False, "fix": {"kind": "none"},
                "why": "This bot was set up to stay offline while building, so "
                       "it does not fetch books."}
    if find_more.look(ref) is None and not ref.startswith("wikipedia:"):
        return {"ok": False, "fix": {"kind": "none"},
                "why": "BuildBot could not find that book on its saved lists "
                       "any more."}
    return None


def _add_routine(apply: dict, label: str) -> dict:
    from puffpai import offers
    got = offers.apply_item(apply, by="owner")
    if got.get("ok"):
        return {"ok": True, "line": f"{label}: switched on."}
    return {"ok": False, "why": str(got.get("message") or "It could not be "
                                                           "switched on.")}


def _add_look(apply: dict, label: str) -> dict:
    from puffpai.looks import proposals
    got = proposals.apply_item(apply)
    if got.get("ok"):
        return {"ok": True, "line": str(got.get("message") or label)}
    return {"ok": False, "why": str(got.get("message") or "The look could not "
                                                           "be used."),
            "fix": {"kind": "none"}}


def _set_town(state: Any) -> Optional[dict]:
    """The town typed on the plan, looked up and kept the town-level way.

    Through the town lookup on What leaves your computer (only the typed name
    is sent) and :mod:`puffpai.home_location` (a rough position, never the
    street). Before the live cards are made, so a card about the weather
    where you live gets the town. ``None`` when the plan did not ask, or
    the owner skipped (the weather card asks again, and says how).
    """
    if not isinstance(state, dict) or not state.get("town_asked"):
        return None
    from puffpai import home_location
    if home_location.is_set():
        return None
    typed = str(state.get("town") or "").strip()
    if not typed:
        home_location.note_skipped()
        return None
    label = "Your home town"
    # The why says where to type it; no command is needed for that.
    later = {"kind": "none"}
    if not home_location.allow_geocode(by="owner"):
        return {"id": "home:town", "label": label, "kind": "home_town",
                "ok": False, "fix": {"kind": "none"},
                "why": "This bot is set to keep everything on your computer, "
                       "so it does not look towns up."}
    try:
        home, others = home_location.set_home_town(typed)
    except home_location.HomeError as exc:
        return {"id": "home:town", "label": label, "kind": "home_town",
                "ok": False, "fix": later,
                "why": f"{exc} You can type your town on the weather card on "
                       f"the home page."}
    line = (f"Your home town: {home['label']}. Only a rough position is "
            f"kept (to about 11 km).")
    if others:
        line += (f" Not the right one? Type it with the state or country on "
                 f"the weather card, like {others[0]}.")
    return {"id": "home:town", "label": label, "kind": "home_town",
            "ok": True, "line": line}


def apply_approved(ctx: Any) -> dict:
    """Carry out every switched-on summary line the cards do not carry out.

    Returns a runner result: ``shared.factory`` is the record of what was
    done, and each failure is a plain gap for the closing screen.
    """
    from puffpai.wizard import proposal

    shared = getattr(ctx, "shared", None) or {}
    state = shared.get("summary")
    interview = shared.get("interview") or {}
    goal = str(interview.get("goal") or "")
    live_lines: list[str] = []

    def stream(line: str) -> None:
        # A registered applier's lines show while the build is still running.
        # An existing factory record keeps everything else it holds; only its
        # "lines" become this run's lines.
        live_lines.append(line)
        run_shared = getattr(ctx, "shared", None)
        if isinstance(run_shared, dict):
            record = run_shared.get("factory")
            if not isinstance(record, dict):
                run_shared["factory"] = {"lines": live_lines}
            elif record.get("lines") is not live_lines:
                record["lines"] = live_lines
    try:
        from puffpai import offers
        offers.remember_goal(goal, None)
    except Exception:  # noqa: BLE001 - only the wording of later offers
        pass
    online = build_online(ctx)
    try:
        # The build record was written a moment ago; what the running bot may
        # reach is read from it, not from a copy cached before it existed.
        from puffpai.security import egress_doors
        egress_doors.reset_cache()
    except Exception:  # noqa: BLE001
        pass
    done: list[dict] = []
    gaps: list[dict] = []
    try:
        town = _set_town(state)
    except Exception as exc:  # noqa: BLE001 - the town never sinks the build
        logger.warning("factory: home town failed: %s", type(exc).__name__)
        town = {"id": "home:town", "label": "Your home town",
                "kind": "home_town", "ok": False, "fix": {"kind": "none"},
                "why": "Something went wrong while looking it up. Type your "
                       "town on the weather card on the home page."}
    if town is not None:
        fix = town.pop("fix", None)
        done.append(town)
        if not town["ok"]:
            gaps.append(_gap("Your home town was not set.", town["why"], fix))
    for item in proposal.approved_items(state):
        apply = item.get("apply") or {}
        kind = str(apply.get("kind") or "")
        ref = str(apply.get("ref") or "")
        label = str(item.get("label") or ref or item.get("id") or "")
        if kind in CARD_KINDS or kind in NOTE_KINDS:
            continue
        if kind in BUILD_PLAN_KINDS:
            # An older approved summary's live card: the build plan finds
            # and makes it as an ingredient of a job, after the one Yes.
            done.append({"id": str(item.get("id") or ""), "label": label,
                         "kind": kind, "ok": False, "status": "waiting"})
            continue
        try:
            if kind == "knowledge.add":
                got = _add_knowledge(ref, label)
            elif kind == "routine":
                got = _add_routine(apply, label)
            elif kind == "theme":
                got = _add_look(apply, label)
            elif kind == "key":
                # Never a key here: the line becomes the steps to get one,
                # on the closing screen and on the Sign-up keys page.
                from puffpai.security import keys
                got = keys.apply_item(apply, by="owner", surface=(
                    "page" if getattr(ctx, "remote", False) else "terminal"))
            elif kind == "ha_integration":
                got = {"ok": False,
                       "why": "Connecting this through Home Assistant is not "
                              "built yet. Nothing was changed."}
            elif kind in _PLAN_APPLIERS:
                actx = _ApplierContext(shared if isinstance(shared, dict) else {},
                                       online, _approving_device(ctx, state))
                res = _run_applier(_PLAN_APPLIERS[kind], item, actx, stream, live_lines)
                added_now = res["status"] in _ADDED_STATUSES
                got = {"ok": added_now, "status": res["status"], "line": res["line"]}
                if not added_now:
                    got["why"] = res["line"]
            else:
                got = {"ok": False, "why": "This build does not know how to add "
                                           "that yet. Nothing was changed."}
        except Exception as exc:  # noqa: BLE001 - one line never sinks the rest
            logger.warning("factory: %s failed: %s", item.get("id"), exc)
            got = {"ok": False, "why": "Something went wrong while adding it. "
                                       "Nothing half-made was kept."}
        row = {"id": str(item.get("id") or ""), "label": label, "kind": kind,
               "ok": bool(got.get("ok"))}
        row.update({k: v for k, v in got.items()
                    if k in ("line", "why", "app_id", "draft", "service_state",
                             "learn_id", "drawbridge", "status")})
        done.append(row)
        if not got.get("ok"):
            gaps.append(_gap(f"{label} was not added.", str(got.get("why") or ""),
                             got.get("fix")))
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "build_online": online, "items": done}
    if isinstance(state, dict) and state.get("approved"):
        # What the plan did not name is off once the bot is made.
        from puffpai import plan_outside
        record["outside"] = plan_outside.settle(
            shared, proposal.approved_items(state), state.get("reach"), record)
    _fetch_waiting_books()
    if live_lines:
        record["lines"] = live_lines
    added = sum(1 for row in done if row["ok"])
    left = sum(1 for row in done if not row["ok"] and row.get("status") != "waiting")
    return {"shared": {"factory": record}, "gaps": gaps,
            "note": f"{added} added, {left} not added"}


def _fetch_waiting_books() -> None:
    """Books she said yes to while finding things waited for the bot to be
    made: now they are fetched and learned in the background."""
    try:
        from puffpai.knowledge import find_more
        if find_more.pending_orders():
            find_more._learn_rest_in_background(  # noqa: SLF001 - the one learner
                find_more.knowledge_folder(create=True))
    except Exception as exc:  # noqa: BLE001 - the next start fetches them
        logger.warning("factory: waiting books not started (%s)", type(exc).__name__)


def added_lines(shared: Any) -> list[str]:
    """The closing screen's "Added for you" lines, from the run record."""
    record = (shared or {}).get("factory") if isinstance(shared, dict) else None
    rows = (record or {}).get("items") or []
    lines = [re.sub(r"\s+", " ", str(row.get("line") or row.get("label")))
             for row in rows if isinstance(row, dict) and row.get("ok")]
    return list(dict.fromkeys(lines))   # one line for one thing


def learning_lines(shared: Any, surface: str = "terminal") -> list[str]:
    """How far the books this build ordered have got (progress.plain_lines)."""
    record = (shared or {}).get("factory") if isinstance(shared, dict) else None
    ids = [str(row.get("learn_id")) for row in (record or {}).get("items") or []
           if isinstance(row, dict) and row.get("ok") and row.get("learn_id")]
    if not ids:
        return []
    from puffpai.knowledge import progress
    out: list[str] = []
    for lid in ids:
        rec = progress.read(lid)
        if rec:
            out.extend(progress.plain_lines(rec, surface))
    return out


__all__ = ["PROVIDERS", "apply_approved", "added_lines", "build_online",
           "register_plan_applier", "APPLIER_STATUSES", "APPLIER_FAILED_LINE",
           "candidate", "device_items", "key_items", "knowledge_items",
           "learning_lines", "looks_items",
           "private_items", "update_items"]
