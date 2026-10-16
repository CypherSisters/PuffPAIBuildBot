"""Offers: the bot asking "would you like a daily update?", once, in plain words.

An offer is made from what the build HAS, never from a topic table. A build
with live info cards or a library can be offered a daily update; a build with
neither is not offered one (there would be nothing to say). One offer at a
time, and a no is remembered.

Where the offer shows up:

* in the wizard's single summary, through :func:`proposal_items` (section
  ``updates``, off until the owner switches it on);
* on the dashboard's home card, through ``GET /offers``;
* once in the first chat after the build, added under the bot's answer;
* whenever the owner asks for updates in their own words ("can you send me
  daily updates?"), through :func:`chat_answer`.

Saying yes (a button, the terminal, or "yes" in chat right after the question)
creates the routine with :func:`puffpai.routines.add_routine`, which records
the yes in the consent ledger. Nothing is scheduled before that.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

STATE_FILENAME = "offers.json"
OFFER_ID = "offer:daily_update"
ROUTINE_ID = "daily_update"

#: How long a "yes" typed in chat still answers the question the bot asked.
CHAT_ANSWER_WINDOW_SECONDS = 30 * 60

_LOCK = threading.RLock()


# ── state ────────────────────────────────────────────────────────────────

def _path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), STATE_FILENAME)


def _load() -> dict:
    try:
        with open(_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("offers: state unreadable: %s", exc)
        return {}


def _save(data: dict) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _entry() -> dict:
    return dict(_load().get(ROUTINE_ID) or {})


def _set_entry(**fields: Any) -> None:
    with _LOCK:
        data = _load()
        row = dict(data.get(ROUTINE_ID) or {})
        row.update(fields)
        data[ROUTINE_ID] = row
        _save(data)


# ── what the build has ───────────────────────────────────────────────────

def build_has() -> dict:
    """What a daily update could draw on: ``{"live_info", "library", "card_names"}``."""
    from puffpai import routines
    readings, _ = routines.read_feeds()
    names = [routines._clean(r.get("name"), 40) for r in (readings or [])][:3]
    item, _ = routines.pick_library_item(_terms())
    return {"live_info": bool(readings), "library": item is not None,
            "card_names": [n for n in names if n]}


def _manifest() -> dict:
    try:
        from puffpai import build_manifest
        return build_manifest.read_manifest() or {}
    except Exception:
        return {}


def _goal() -> str:
    man = _manifest()
    goal = (man.get("surfaces") or {}).get("goal") or (man.get("build") or {}).get("goal")
    return str(goal or "").strip()


def bot_goal() -> str:
    """What the owner said this bot is for, from its build record ("" if unknown)."""
    return _goal()


def bot_topic() -> Optional[str]:
    """The bot's own topic for "a daily <topic> update", or None: the same
    words the daily update offer uses (:func:`current_offer`)."""
    entry = _entry()
    return topic_phrase(_goal(), entry.get("terms")) or entry.get("topic")


def _terms() -> list[str]:
    stored = _entry().get("terms")
    if isinstance(stored, list) and stored:
        return [str(t) for t in stored][:6]
    return goal_terms(_goal())


def _bot_name() -> str:
    try:
        from puffpai import bot_identity
        return bot_identity.bot_name()
    except Exception:
        return "your bot"


# ── words ────────────────────────────────────────────────────────────────

def topic_phrase(goal: str, terms: Optional[list] = None) -> Optional[str]:
    """The topic for "a daily <topic> update", or None when none is clean.

    The bot's one topic phrase (:func:`puffpai.bot_identity.topic_phrase`, the
    same words its greeting uses) in its "daily update" form: "dinosaur",
    "bread", "2014 Honda Civic". *terms* are read only when the goal says
    nothing. None means callers use the bot's name instead ("a daily update
    from HondaBot").
    """
    from puffpai import bot_identity
    phrase = bot_identity.topic_phrase(str(goal or ""), form="update")
    if phrase and not _plain_topic(phrase, str(goal or "")):
        # Her goal's topic holds her personal words: the update is named
        # after no topic at all, never after one loose word of the goal
        # (not "A daily turn update" or "A daily put update").
        return None
    for term in ([] if phrase else terms or []):
        phrase = bot_identity.topic_phrase(str(term or ""), form="update")
        if phrase and not _plain_topic(phrase, str(term or "")):
            phrase = ""
        if phrase:
            break
    return phrase or None


def _plain_topic(phrase: str, goal: str) -> bool:
    """Is *phrase* free of her personal words? A daily update is never named
    after them (not "A daily <a grandson's name> turns update",
    "A daily 19 weeks pregnant update", "A daily Eli update").

    An ordinary word may not be a wanting word or a life event ("going",
    "laid off"). A word the privacy marks hold is allowed only as part of a
    thing's own name: two names or more ("Honda Civic"), or after a model
    year ("2014 Honda Civic"). A lone name ("Eli"), a name followed by an
    ordinary word with no model year ("<name> turns"), an age, a relative or
    a health word never is."""
    from puffpai.catalogs import needs_rules, personal_words
    marks = needs_rules.personal_words(goal)
    events = set(needs_rules.LIFE_EVENTS) | set(personal_words.LIFE_EVENTS) | {"laid", "fired"}
    wanting = {"going", "gonna", "want", "wants", "wanted", "need", "needs", "getting", "get",
               "got", "able", "just", "really", "trying", "try"}
    small = {"and", "of", "the", "a", "an", "for", "with", "to"}
    words = [w for w in re.findall(r"[^\W_][\w'-]*", phrase) if w.lower() not in small]
    if not words or len(words) >= 3 and words[0].lower() in needs_rules.VERBS:
        return False                # "set monthly budgets" is a job, not a topic
    names, year = 0, False
    for i, w in enumerate(words):
        low = w.lower()
        mark = str(marks.get(low, ""))
        if not mark:
            if low in wanting or low in events or (names and not year):
                return False
            continue
        if mark == "held:name":
            names += 1
            continue
        if mark == "hard:number or age" and i == 0 and len(words) > 1 \
                and re.fullmatch(r"(?:19|20)\d\d", w):
            year = True             # a model year in front of a thing ("1967 Mustang")
            continue
        return False
    return not (names == 1 and len(words) == 1)


def stored_phrase() -> dict:
    """The model-proposed topic kept at build time: ``{"goal", "phrase"}`` or {}."""
    entry = _entry()
    if entry.get("phrase") and entry.get("phrase_goal"):
        return {"goal": str(entry["phrase_goal"]), "phrase": str(entry["phrase"])}
    return {}


_STOP_WORDS = frozenset(
    "a an the and or of for to in on at my me i you your we our with that this is are "
    "be it its about bot helper buddy want wants like love need help keep knows know "
    "every all everything how tos how-tos tips shows show cool stuff works work running smart "
    "please would could can".split())


def goal_terms(goal: str) -> list[str]:
    """Plain topic words from the owner's goal, for picking library items.

    Only words :func:`goal_words.search_terms` keeps (no relatives,
    names, ages, dates, places, sensitive or news words), as she typed them.
    """
    from puffpai.catalogs import goal_words
    keep = set()
    for t in goal_words.search_terms(str(goal or ""), limit=24):
        keep |= {t, goal_words.singular(t)}
    words = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", str(goal or ""))
    out: list[str] = []
    for w in words:
        lw = w.lower()
        if lw in _STOP_WORDS or lw in (x.lower() for x in out):
            continue
        if lw not in keep and goal_words.singular(lw) not in keep:
            continue
        out.append(w)
    return out[:6]


def _spoken_time(hhmm: str) -> str:
    from puffpai import home_location
    hh, mm = (int(x) for x in hhmm.split(":"))
    return home_location.spoken_time(datetime(2000, 1, 1, hh, mm))


def _question(topic: Optional[str], at: str) -> str:
    when = _spoken_time(at)
    if topic:
        return f"Would you like a daily {topic} update at {when}?"
    return f"Would you like a daily update from {_bot_name()} at {when}?"


def _contents(has: dict) -> str:
    parts = ["the date"]
    if has.get("live_info"):
        names = has.get("card_names") or []
        parts.append("the latest from " + (" and ".join(names[:2]) if names
                                            else "your live info cards"))
    if has.get("library"):
        parts.append("one thing from your bot's library")
    return "It would have " + ", ".join(parts[:-1]) + " and " + parts[-1] + "." \
        if len(parts) > 1 else "It would have the date."


# ── the offer ────────────────────────────────────────────────────────────

def _default_time() -> str:
    from puffpai import routines
    return (routines.kind(ROUTINE_ID) or {}).get("default_time") or "08:00"


def current_offer() -> Optional[dict]:
    """The one open offer, or None (already on, said no, or nothing to offer)."""
    from puffpai import routines
    if routines.get_routine(ROUTINE_ID) is not None:
        return None
    entry = _entry()
    if entry.get("status") in ("accepted", "declined"):
        return None
    has = build_has()
    if not (has["live_info"] or has["library"]):
        return None
    at = entry.get("time") or _default_time()
    topic = topic_phrase(_goal(), entry.get("terms")) or entry.get("topic")
    return {
        "id": OFFER_ID,
        "kind": ROUTINE_ID,
        "question": _question(topic, at),
        "detail": _contents(has) + " It stays on this computer; nothing new "
                  "goes online to make it.",
        "time": at,
    }


def accept(offer_id: str = OFFER_ID, *, by: str = "owner", at: Optional[str] = None
           ) -> dict:
    """The owner's yes: create the daily update routine and record the yes."""
    from puffpai import routines
    if offer_id != OFFER_ID:
        return {"ok": False, "message": "I don't have that offer any more."}
    entry = _entry()
    at = at or entry.get("time") or _default_time()
    try:
        row = routines.add_routine(ROUTINE_ID, daily_at=at, by=by,
                                   params={"terms": _terms()})
    except routines.RoutineError as exc:
        return {"ok": False, "message": str(exc)}
    _set_entry(status="accepted", decided_at=_now().isoformat(timespec="seconds"),
               decided_by=str(by)[:40], time=row["daily_at"])
    return {"ok": True, "routine": row, "message": accepted_message(row)}


def accepted_message(row: dict) -> str:
    from puffpai import home_location, routines
    text = (f"Done. You'll get a short update {routines.spoken_when(row)}. It shows "
            "on your home page, and I'll mention it the next time we chat. Say "
            "\"stop daily updates\" any time to turn it off.")
    if not home_location.is_set():
        text += (" I don't know your home town yet, so I'm using this computer's "
                 "clock for the time. To fix that, run: puffpai home set \"Your Town\"")
    return text


def decline(offer_id: str = OFFER_ID, *, by: str = "owner") -> dict:
    if offer_id != OFFER_ID:
        return {"ok": False, "message": "I don't have that offer any more."}
    _set_entry(status="declined", decided_at=_now().isoformat(timespec="seconds"),
               decided_by=str(by)[:40])
    return {"ok": True, "message": "Okay, no daily updates. If you change your "
                                   "mind, just ask me for daily updates."}


def reopen() -> None:
    """Forget an earlier no (the owner asked for updates themselves)."""
    entry = _entry()
    if entry.get("status") == "declined":
        _set_entry(status="open")


# ── the wizard's summary ─────────────────────────────────────────────────

_UNSET: Any = object()


def proposal_items(goal: str, terms: list[str], *, topic: Any = _UNSET) -> list[dict]:
    """One line for the wizard's single summary (section ``updates``).

    Off until the owner switches it on, so "Looks good" alone does not start
    anything. It downloads nothing and reaches nothing outside, so it needs no
    separate yes; switching it on is the yes. *topic* names it outright (the
    bot's own topic, when it rides along with something being added); None
    names no topic at all.
    """
    from puffpai import routines
    k = routines.kind(ROUTINE_ID) or {}
    at = k.get("default_time") or "08:00"
    if topic is _UNSET:
        topic = topic_phrase(goal, terms)
    label = (f"A daily {topic} update at {_spoken_time(at)}" if topic
             else f"A daily update at {_spoken_time(at)}")
    return [{
        "id": f"updates:{ROUTINE_ID}",
        "section": "updates",
        "label": label[:80],
        "detail": k.get("offer_detail") or "A short note once a day.",
        "size_bytes": None,
        "outside_host": None,
        "default_on": False,
        "needs_yes": False,
        "apply": {"kind": "routine", "ref": f"{ROUTINE_ID}@{at}"},
    }]


def remember_goal(goal: str, terms: Optional[list] = None) -> None:
    """Keep the topic words the wizard used, so later offers say the same thing."""
    from puffpai import bot_identity
    topic = topic_phrase(goal, terms)
    # A phrase the local model proposed (and code accepted) while the plan
    # was drawn is kept, so the running bot says the same words later.
    proposed = bot_identity.proposed_topic(goal)
    _set_entry(terms=[str(t) for t in (terms or goal_terms(goal))][:6],
               topic=topic, phrase=proposed or None,
               phrase_goal=" ".join(str(goal or "").split()) if proposed else None)


def apply_item(apply: dict, *, by: str = "owner") -> dict:
    """Carry out a switched-on summary line (``{"kind": "routine", "ref"}``)."""
    if not isinstance(apply, dict) or apply.get("kind") != "routine":
        return {"ok": False, "message": "That is not a routine."}
    rid, _, at = str(apply.get("ref") or "").partition("@")
    if rid != ROUTINE_ID:
        return {"ok": False, "message": "I don't know that routine."}
    return accept(OFFER_ID, by=by, at=at or None)


# ── chat ─────────────────────────────────────────────────────────────────

_UPDATES_WORD = r"(?:update|updates|news|briefing|briefings|summary|summaries|digest|report|recap)"
_UPDATES_PLURAL = r"(?:updates|news|briefings|summaries|digests|reports|recaps)"
#: Asking to GET updates from now on, not asking for one now. "Give me my daily
#: briefing" and "morning update" (singular, no "send"/"every") are requests
#: for a briefing right now and belong to the briefing route, not here.
_ASK_UPDATES = re.compile(
    r"(?ix)"
    r"(?<!my\s)(?<!the\s)(?<!today's\s)(?<!this\s)"
    r"\b(?:daily|nightly|morning|evening|weekly)\b(?:\s+[\w'-]+){0,3}?\s+" + _UPDATES_PLURAL + r"\b"
    r"|\b" + _UPDATES_WORD + r"\b(?:\s+[\w'-]+){0,5}?\s+(?:every|each)\s+(?:day|morning|evening|night|week)\b"
    r"|\b(?:every|each)\s+(?:day|morning|evening|night|week)\b(?:\s+[\w'-]+){0,5}?\s+" + _UPDATES_WORD + r"\b"
    r"|\b(?:keep\s+me\s+(?:posted|updated|up\s+to\s+date)|update\s+me\s+(?:every|each|daily))\b"
    r"|\b(?:subscribe|sign\s+me\s+up)\b(?:\s+[\w'-]+){0,4}?\s+" + _UPDATES_WORD + r"\b"
    r"|\bsend\s+me\s+(?:[\w'-]+\s+){0,3}" + _UPDATES_WORD + r"\b")
_STOP_UPDATES = re.compile(
    r"(?i)\b(?:stop|turn\s+off|switch\s+off|cancel|pause|no\s+more|don't\s+send|do\s+not\s+send)\b"
    r"(?:\s+[\w'-]+){0,3}?\s+(?:daily\s+)?" + _UPDATES_WORD + r"\b")
_WHAT_ROUTINES = re.compile(
    r"(?i)\b(?:what|which|list|show)\b.{0,30}\b(?:routines|workflows|automations|"
    r"scheduled|daily\s+updates)\b")
_YES = re.compile(r"(?i)^\s*(?:yes|yeah|yep|yup|sure|ok|okay|please|yes\s+please|"
                  r"sounds\s+good|do\s+it|go\s+ahead|i'd\s+like\s+that|i\s+would)\b[\s!.]*\w*[\s!.]*$")
_NO = re.compile(r"(?i)^\s*(?:no|nope|nah|no\s+thanks|no\s+thank\s+you|not\s+now|"
                 r"maybe\s+later|don't|do\s+not)\b[\s!.,]*\w*[\s!.]*$")


def asks_for_updates(message: str) -> bool:
    return bool(_ASK_UPDATES.search(str(message or "")))


def is_routines_message(message: str) -> bool:
    """Pure check: would :func:`chat_answer` answer this? (No side effects.)"""
    text = str(message or "")
    if _STOP_UPDATES.search(text) or _WHAT_ROUTINES.search(text) or asks_for_updates(text):
        return True
    return _pending_chat_offer() and bool(_YES.match(text) or _NO.match(text))


def _pending_chat_offer() -> bool:
    entry = _entry()
    asked = entry.get("chat_asked_at")
    if not asked or entry.get("status") in ("accepted", "declined"):
        return False
    if entry.get("chat_superseded") == asked:
        return False          # the bot asked something newer since

    try:
        age = (_now() - datetime.fromisoformat(asked)).total_seconds()
    except ValueError:
        return False
    return 0 <= age <= CHAT_ANSWER_WINDOW_SECONDS


def expire_chat_offer(before: float) -> bool:
    """The bot asked a newer question in a chat turn that started at
    *before* (a time.time()): an offer asked in chat before that turn no
    longer takes her next "yes" (the newest question wins). The offer itself
    stays on the home page. True when one was expired."""
    entry = _entry()
    asked = entry.get("chat_asked_at")
    if not asked or entry.get("chat_superseded") == asked or \
            entry.get("status") in ("accepted", "declined"):
        return False
    try:
        when = datetime.fromisoformat(asked).timestamp()
    except ValueError:
        return False
    if when >= int(before):    # asked in this same turn (kept to the second)
        return False
    _set_entry(chat_superseded=asked)
    return True


def _ask_in_chat(offer: dict) -> str:
    _set_entry(chat_asked_at=_now().isoformat(timespec="seconds"), status="open")
    return f"{offer['question']} {offer['detail']} Just say yes or no."


def routines_summary() -> str:
    from puffpai import routines
    rows = routines.list_routines()
    if not rows:
        return ("You have no routines yet: nothing runs on its own. Ask me for "
                "daily updates if you'd like one.")
    lines = [routines.describe(r) for r in rows]
    return "Here is what runs on its own:\n" + "\n".join(f"- {line}" for line in lines)


def chat_answer(message: str, *, by: str = "owner") -> Optional[str]:
    """The real answer to an updates question in chat, or None if it isn't one."""
    from puffpai import routines
    text = str(message or "")
    if _pending_chat_offer():
        if _YES.match(text):
            return accept(OFFER_ID, by=by)["message"]
        if _NO.match(text):
            return decline(OFFER_ID, by=by)["message"]
    if _STOP_UPDATES.search(text):
        row = routines.get_routine(ROUTINE_ID)
        if row is None or not routines.is_runnable(row):
            return "Daily updates are already off."
        routines.set_enabled(ROUTINE_ID, False, by=by)
        return ("Okay, I stopped your daily updates. Ask me for daily updates "
                "to turn them back on.")
    if _WHAT_ROUTINES.search(text):
        return routines_summary()
    if not asks_for_updates(text):
        return None
    row = routines.get_routine(ROUTINE_ID)
    if row is not None:
        if routines.is_runnable(row):
            return (f"You already get a daily update {routines.spoken_when(row)}. "
                    "Say \"stop daily updates\" to turn it off.")
        routines.set_enabled(ROUTINE_ID, True, by=by)
        return (f"Okay, your daily update is back on, {routines.spoken_when(row)}.")
    reopen()
    offer = current_offer()
    if offer is None:
        return ("I can't send daily updates yet: this bot has no live info cards "
                "and nothing in its library, so there would be nothing to put in "
                "them. Once you add some, ask me again.")
    return _ask_in_chat(offer)


def followup_due() -> bool:
    """Pure check: could :func:`chat_followup` still add the offer?"""
    entry = _entry()
    return not (entry.get("chat_asked_at") or entry.get("status") in ("accepted", "declined"))


def chat_followup(reply: str = "") -> Optional[str]:
    """The offer, added once under the first chat answer after the build.

    One question per reply: when the bot's own answer (*reply*) already asks
    her something ("Want it on your home page?"), the offer waits for a later
    answer, so her next "yes" answers the bot's question, not this one."""
    if not followup_due() or "?" in str(reply or ""):
        return None
    offer = current_offer()
    if offer is None:
        return None
    return _ask_in_chat(offer)


__all__ = [
    "OFFER_ID", "accept", "apply_item", "asks_for_updates", "build_has", "chat_answer",
    "chat_followup", "current_offer", "decline", "expire_chat_offer", "followup_due", "goal_terms", "is_routines_message",
    "proposal_items", "remember_goal", "routines_summary", "stored_phrase",
    "topic_phrase",
]
