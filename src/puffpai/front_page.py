"""The front page: what THIS build is, has and can answer, in one read.

A stranger who has just built a bot must not open the dashboard and meet a
household board: a greeting to "Operator", a waveform, "Say 'Hey Puff'" on a
build with no microphone, and tiles for meals and cameras, none of it their
bot. This module is the one
reader the home page, and anything else that wants to say "here is your bot",
takes its words from.

Everything is assembled from what the build really has, read at request time:

* the name and the goal the owner gave (``bot_identity``, ``build_abilities``)
  and the bot's first line in its own voice (the closing screen's words);
* its live cards (``feeds.card_state``), each with its credit line;
* today's messages and what comes next (``announcer``, ``routines``, ``offers``);
* "Try asking" questions, each one checked against THIS build before it is
  shown: a card question only when the card's service is on, a book question
  only when the books' word look-up (``book_search.book_hits``, the same
  look-up chat answers from) finds that page, and "What can you do?" always.
  The questions come from the owner's goal words and the page titles the books
  return -- never from a topic table, and the model is not asked;
* what it knows (``progress.plain_lines``, the one wording source for books);
* the things the goal asked for that nothing in this build does yet
  (``build_abilities``);
* the way to add more, and the way to see what leaves the computer.

"Say 'Hey Puff'" appears only when an ability row ``voice.wake`` says a wake
word really works on this build. Nothing registers that row today, so the
line says to type -- evidence or silence.

Every string here is plain words (``plain_words.check`` runs over the whole
payload in ``tests/test_front_page.py``). Links are the dashboard's own pages;
nothing here builds a web address from outside text.

Route: ``GET /front`` (``/home`` belongs to the home-town routes).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from flask import Blueprint, jsonify, request

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

bp = Blueprint("front", __name__)

#: The ability row that says a wake word really works here. A voice part that
#: proves one registers ``{"id": "voice.wake", "status": "available", ...}``.
WAKE_ABILITY = "voice.wake"
WAKE_PHRASE = "Hey Puff"

#: Always the last "Try asking" question: the self-knowledge answer is built
#: from this build's own ability list, so it is true on every build.
ALWAYS_ASK = "What can you do?"

MAX_QUESTIONS = 5
MAX_BOOK_QUESTIONS = 2
MAX_CARD_QUESTIONS = 2
MAX_NOT_CONNECTED = 4
PURPOSE_CHARS = 160
#: The chat's own cut for a book passage to count as a source
#: (``api_server._get_rag_context``: ``RELEVANCE_MIN``).
RELEVANCE_MIN = 0.25

ADD_HREF = "/dashboard/add"
OUTSIDE_HREF = "/dashboard/outside"
LOOKS_HREF = "/dashboard/looks"
KEYS_HREF = "/dashboard/keys"
CHAT_HREF = "/dashboard/chat"
KNOWLEDGE_HREF = "/dashboard/knowledge"

#: A page title we will put in a question: words, not markup or a list page.
_TITLE_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,'()&.-]{1,58}[A-Za-z0-9)]$")
_TITLE_SKIP = re.compile(r"(?i)^(?:list|lists|index|outline|glossary|timeline|"
                         r"category|portal|template|help|talk)\b|disambiguation"
                         # a title that quotes something is a listing page
                         # ("Highest voted 'x' questions"), not a page about it
                         r"|'[^']+'|\"")
#: A page whose title is itself a question (a question-and-answer book): asked
#: as it stands.
_QUESTION_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,'()&.:-]{8,76}\?$")


# ---------------------------------------------------------------------------
# small readers (each never raises)
# ---------------------------------------------------------------------------

def _name() -> str:
    try:
        from puffpai import bot_identity
        return bot_identity.bot_name() or "your bot"
    except Exception:
        return "your bot"


def _goal() -> str:
    try:
        from puffpai import build_abilities
        return build_abilities.goal_of() or ""
    except Exception:
        return ""


def _short_purpose(goal: str, name: str) -> str:
    """"A wedding planner bot.", or "" when the goal names no subject (a line
    that only says "made from your goal" under the name tells her nothing)."""
    if not goal:
        return ""
    try:
        from puffpai import persona_scaffold
        line = persona_scaffold.tagline(goal, name)
    except Exception:
        return ""
    return "" if line.lower().endswith("made from your goal.") else line


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "..."


def hello(name: str, goal: str) -> str:
    """The bot's first line, in its own voice: the closing screen's own words."""
    from types import SimpleNamespace
    try:
        from puffpai.wizard.packages.handoff import skill as handoff
        ctx = SimpleNamespace(shared={"identity": {"name": name},
                                      "interview": {"goal": goal}},
                              read=lambda _section: {})
        return handoff.first_line(ctx) or ""
    except Exception:
        return ""


def _abilities() -> list[dict[str, Any]]:
    try:
        from puffpai import build_abilities
        return build_abilities.abilities()
    except Exception:
        logger.debug("front: abilities unreadable", exc_info=True)
        return []


def _voice(rows: list[dict[str, Any]]) -> bool:
    return any(r.get("id") == WAKE_ABILITY and r.get("status") == "available"
               for r in rows)


def _consent() -> dict[str, Any]:
    """The consent line and the on-list, from the one source the sidebar card
    and the Developer pages read (privacy_explainer.consent_summary)."""
    try:
        from puffpai.security import privacy_explainer
        return privacy_explainer.consent_summary()
    except Exception:
        return {"consent_line": "Nothing leaves your computer without your "
                                "consent.",
                "count": None, "on": [],
                "line": "I could not check which outside services are on."}


def _outside_on() -> Optional[int]:
    """How many outside services are on, or None when the list cannot be read."""
    return _consent().get("count")


def dateline(now: Optional[datetime] = None, *,
             outside_on: Optional[int] = None) -> str:
    """"Wednesday, September 24 · Leeds · 2 outside services on"."""
    parts: list[str] = []
    try:
        from puffpai import home_location
        home = home_location.get_home()
        here = home_location.local_now(home, now)
        parts.append(home_location.spoken_date(here))
        label = str((home or {}).get("label") or "").strip()
        if label:
            parts.append(_clip(label, 40))
    except Exception:
        stamp = (now or datetime.now(timezone.utc)).astimezone()
        parts.append(f"{stamp.strftime('%A')}, {stamp.strftime('%B')} {stamp.day}")
    if outside_on is not None:
        if outside_on == 0:
            parts.append("no outside services on")
        else:
            noun = "service" if outside_on == 1 else "services"
            parts.append(f"{outside_on} outside {noun} on")
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# today
# ---------------------------------------------------------------------------

def _local_clock(stamp: datetime) -> str:
    local = stamp.astimezone()
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"


def today(now: Optional[datetime] = None) -> dict[str, Any]:
    """What the bot said today, what comes next, and its one open offer."""
    now = (now or datetime.now(timezone.utc)).astimezone()
    said: list[dict[str, str]] = []
    try:
        from puffpai import announcer
        for row in announcer.recent(20):
            try:
                stamp = datetime.fromisoformat(str(row.get("ts")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if stamp.astimezone().date() != now.date():
                continue
            said.append({"time": _local_clock(stamp),
                         "text": _clip(str(row.get("text") or ""), 280),
                         "title": _clip(str(row.get("title") or ""), 80)})
    except Exception:
        logger.debug("front: announcements unreadable", exc_info=True)
    upcoming: list[dict[str, str]] = []
    try:
        from puffpai import routines
        for row in routines.list_routines():
            if not routines.is_runnable(row):
                continue
            upcoming.append({"what": _clip(str(row.get("label") or "An update"), 80),
                             "when": routines.spoken_when(row)})
    except Exception:
        logger.debug("front: routines unreadable", exc_info=True)
    offer = None
    try:
        from puffpai import offers
        found = offers.current_offer()
        if isinstance(found, dict):
            offer = {"id": str(found.get("id") or ""),
                     "question": str(found.get("question") or ""),
                     "detail": str(found.get("detail") or "")}
    except Exception:
        logger.debug("front: offer unreadable", exc_info=True)
    return {"said": said[:6], "next": upcoming[:4], "offer": offer}


# ---------------------------------------------------------------------------
# live cards
# ---------------------------------------------------------------------------

def _card_line(state: dict[str, Any]) -> str:
    card = state.get("card") or {}
    kind = card.get("kind")
    if kind == "value":
        unit = f" {card['unit']}" if card.get("unit") else ""
        return _clip(f"{card.get('label') or 'Now'}: {card.get('value')}{unit}", 140)
    if kind == "image":
        return _clip(str(card.get("headline") or card.get("caption") or "A new picture"), 140)
    items = [str(i.get("title") or "") for i in (card.get("items") or []) if i.get("title")]
    if kind == "gallery" and items:
        return _clip(f"{len(items)} pictures: " + "; ".join(items[:2]), 140)
    if items:
        return _clip("; ".join(items[:3]), 140)
    return "Nothing to show right now."


def _installed_at(app_id: str) -> float:
    """When the owner's copy of an app was written (0 for shipped apps), so the
    home page can show cards in the order they were added."""
    try:
        import os

        from puffpai import app_registry
        path = os.path.join(app_registry.resolve_within(
            app_registry.user_apps_dir(), app_id), app_registry.MANIFEST_NAME)
        return os.path.getmtime(path)
    except Exception:  # noqa: BLE001 - unknown order sorts first, never fails
        return 0.0


def cards() -> list[dict[str, Any]]:
    """One entry per installed live card, in the order they were added: on,
    off, or a problem, its credit, and its ``kind`` (``image``, ``value``,
    ``list`` or ``gallery``) so the home page can size its tile."""
    out: list[dict[str, Any]] = []
    try:
        from puffpai import feeds
        apps = feeds.feed_apps()
    except Exception:
        return out
    apps = sorted(apps, key=lambda mf: _installed_at(str(mf[0].get("id") or "")))
    for manifest, _feed in apps:
        app_id = str(manifest.get("id") or "")
        kind = str((_feed.get("card") or {}).get("kind") or "")
        try:
            state = feeds.card_state(app_id)
        except Exception:
            out.append({"app_id": app_id, "name": str(manifest.get("name") or app_id),
                        "status": "problem", "line": "This card cannot be read right now.",
                        "credit": "", "href": f"/dashboard/apps/{app_id}",
                        "kind": kind})
            continue
        if not state.get("allowed"):
            status = "off"
            line = ("Switched off. You can switch it on under What leaves your "
                    "computer.")
        elif state.get("card"):
            status, line = "on", _card_line(state)
        else:
            status = "problem" if state.get("status") == "error" else "on"
            line = _clip(str(state.get("message") or "Nothing has been fetched yet."), 140)
        out.append({"app_id": app_id, "name": str(state.get("name") or app_id),
                    "status": status, "line": line,
                    "credit": _clip(str(state.get("credit") or ""), 160),
                    "href": f"/dashboard/apps/{app_id}", "kind": kind})
    return out


# ---------------------------------------------------------------------------
# what Toolsmith added
# ---------------------------------------------------------------------------

def _app_href(rec: dict[str, Any]) -> str:
    if rec.get("mode") == "folder_palette":
        return f"/dashboard/mood-board/{rec['id']}"  # its own page
    target = rec.get("app_id") or rec.get("list_app_id") or ""
    return f"/dashboard/apps/{target}" if target else ""


def app_tiles() -> list[dict[str, Any]]:
    """One tile per ready integration: ``<name>. <purpose>. <what the bot can
    do there, from its checked actions, or "Opens inside your bot.">``. The
    ``elsewhere`` line is what a phone or another screen shows instead (round
    3 apps open on the computer BuildBot runs on). Never raises."""
    try:
        from puffpai.integrate import actions, records, wording
        recs = [r for r in records.all() if r.get("status") == "ready"]
        acts = [a for a in actions.all_actions() if actions.allowed(a)] if recs else []
    except Exception:
        logger.warning("front: app tiles failed", exc_info=True)
        return []
    out: list[dict[str, Any]] = []
    for rec in recs:
        ids = {rec["id"], rec.get("app_id") or "", rec.get("list_app_id") or ""} - {""}
        says: list[str] = []
        for a in acts:
            if a["integration"] in ids:
                s = str(a["says"]).rstrip(".")
                s = s[:1].lower() + s[1:]
                if s and s not in says:
                    says.append(s)
        can = (wording.TILE_ABILITIES.format(says=", ".join(says[:3])) if says
               else wording.TILE_OPENS)
        name = _clip(str(rec.get("name") or ""), 60)
        purpose = _clip(str(rec.get("purpose") or ""), 120).rstrip(".")
        line = wording.TILE_LINE.format(name=name, purpose=purpose, abilities=can) if purpose \
            else f"{name}. {can}"
        tile: dict[str, Any] = {"id": rec["id"], "name": name, "line": _clip(line, 240),
                                "href": _app_href(rec), "kind": str(rec.get("mode") or ""),
                                "elsewhere": wording.TILE_ELSEWHERE}
        if rec.get("mode") == "folder_palette":
            tile["ask_folder"] = not (rec.get("folder") or {}).get("granted")
            tile["ask_label"] = wording.PALETTE_ASK
            try:
                from puffpai.integrate import palette
                pal = palette.read_palette(rec["pictures_dir"]) or {}
                tile["themes"] = [{"name": str(t.get("name") or "")[:60],
                                   "colours": [c for c in (t.get("colours") or [])
                                               if isinstance(c, str)][:5]}
                                  for t in (pal.get("themes") or [])[:6]]
            except Exception:
                tile["themes"] = []
        out.append(tile)
    return out


def finish_card() -> Optional[dict[str, Any]]:
    """The Finish card when things are waiting for the locked box, else None."""
    try:
        from puffpai.integrate import optional, wording
        driver = optional("puffpai.box.driver")
        waiting = getattr(driver, "waiting", None) if driver else None
        items = list(waiting() or []) if callable(waiting) else []
    except Exception:
        logger.warning("front: finish card failed", exc_info=True)
        return None
    if not items:
        return None
    return {"line": wording.FINISH_LINE.format(n=len(items)), "label": wording.FINISH_LABEL,
            "post": "/box/finish"}


def voice_key_link() -> Optional[dict[str, Any]]:
    """The home page's way to the Home Assistant voice key page, else
    None. Shown when a voice key is on (so it can be seen,
    changed or turned off), or when Home Assistant is set up and the bot has
    app actions for voice to use. Never the key itself. Never raises."""
    try:
        from puffpai.integrate import actions, voice_key, wording
        on = voice_key.is_on()
        if not on:
            from puffpai import setup_ops
            if not setup_ops.ha_configured():
                return None
            if not any(actions.allowed(a) for a in actions.all_actions()):
                return None
    except Exception:
        logger.warning("front: voice key link failed", exc_info=True)
        return None
    return {"label": wording.VOICE_KEY_LINK_LABEL,
            "line": wording.VOICE_KEY_LINK_ON if on else wording.VOICE_KEY_LINK_OFF,
            "href": wording.VOICE_KEY_HREF, "on": bool(on)}


# ---------------------------------------------------------------------------
# what it knows
# ---------------------------------------------------------------------------

def knows(surface: str = "browser") -> dict[str, Any]:
    """The books and how far along each one is, in the learning part's words."""
    books: list[dict[str, Any]] = []
    # The browser's book lines name buttons on the Knowledge page; the home
    # page has none, so it takes the wording that points there.
    wording = "chat" if surface == "browser" else surface
    try:
        from puffpai.knowledge import progress
        for rec in progress.read_all():
            books.append({"name": str(rec.get("name") or rec.get("id") or "A book"),
                          "stage": str(rec.get("stage") or ""),
                          "lines": progress.plain_lines(rec, wording)})
    except Exception:
        logger.debug("front: learning records unreadable", exc_info=True)
    if not books:
        line = "No books yet. You can add some."
    else:
        ready = sum(1 for b in books if b["stage"] not in ("waiting", "fetching",
                                                           "fetch_stopped", "quick",
                                                           "quick_stopped", "error"))
        noun = "book" if len(books) == 1 else "books"
        line = (f"{len(books)} {noun}, {ready} ready to answer questions."
                if ready != len(books) else
                f"{len(books)} {noun}, ready to answer questions.")
    return {"books": books, "line": line, "href": KNOWLEDGE_HREF,
            "more_label": "Find more books"}


# ---------------------------------------------------------------------------
# not connected yet
# ---------------------------------------------------------------------------

def _chosen_but_absent(ba: Any, rows: list[dict[str, Any]],
                       pages: Optional[list[str]]) -> list[dict[str, Any]]:
    """Absent rows behind the pages this build chose for the goal.

    The build already linked the goal to its pages ("you asked about 'garden'
    and that comes with the weather card"). A page it chose that nothing
    works behind is an honest gap even when no goal word names it.
    """
    if pages is None:
        try:
            pages = ba.page_list()
        except Exception:
            pages = None
    out: list[dict[str, Any]] = []
    for sid in pages or ():
        try:
            r = ba.page_row(sid, rows)
        except Exception:
            r = None
        # A made-up "can't tell" row for a page nothing reports on is not a gap.
        if (r and r.get("status") in ba.ABSENT and r not in out
                and (r in rows or r.get("status") != ba.UNKNOWN)):
            out.append(r)
    return out


def not_connected(rows: list[dict[str, Any]], goal: str,
                  surface: str = "browser", name: str = "your bot",
                  pages: Optional[list[str]] = None
                  ) -> list[dict[str, str]]:
    """What the owner's goal asked for that nothing in this build does yet.

    Three honest sources: the owner's own things (goal rows), the pages this
    build chose for the goal that nothing works behind, and absent abilities
    the goal's words name. The library is left out: "What <bot> knows" says
    how far each book is.
    """
    try:
        from puffpai import build_abilities as ba
        from puffpai import plain_words
    except Exception:
        return []
    chosen = _chosen_but_absent(ba, rows, pages)
    try:
        from puffpai.offers import goal_terms
        topic = goal_terms(goal)
    except Exception:
        topic = []
    # The goal's own topic words ("telescope", "recalls"), not its filler
    # ("every", "know"), so a row is named only when the goal asked for it.
    goal_words = {ba._stem(w) for t in topic for w in ba.words(t)} - {""}  # noqa: SLF001
    have = {str(r.get("id") or "") for r in rows}
    extra = [r for r in chosen if str(r.get("id") or "") not in have]
    chosen_ids = {str(r.get("id") or "") for r in chosen}
    picked = []
    for r in ba.goal_order(ba.absent(rows) + extra, goal):
        rid = str(r.get("id") or "")
        if rid == "knowledge_library":
            continue
        if rid.startswith("goal:") or rid in chosen_ids or (ba.terms(r) & goal_words):
            picked.append(r)
        if len(picked) >= MAX_NOT_CONNECTED:
            break
    out = []
    for r in picked:
        line = ba.absent_line(r, with_how=False)
        act = r.get("action")
        if isinstance(act, dict):
            try:
                line = f"{line} {plain_words.render_action(act, surface)}"
            except Exception:
                pass
        elif r.get("status") == ba.NOT_IN_THIS_BUILD:
            line = f"{line} You can ask for it under Add to {name}."
        line = line[:1].upper() + line[1:]
        out.append({"thing": str(r.get("label") or ""), "line": line})
    return out


# ---------------------------------------------------------------------------
# try asking
# ---------------------------------------------------------------------------

def _sentence(text: str) -> str:
    text = " ".join(str(text or "").split()).rstrip(".")
    return text[:1].upper() + text[1:] if text else text


def _clean_question(text: str) -> bool:
    """A question we are willing to show: short, plain, no address, no orders."""
    from puffpai import artifact_review, honesty, plain_words
    return (bool(text) and len(text) <= 80
            and not honesty.urls_in(text)
            and not artifact_review.reads_like_orders(text)
            and plain_words.is_plain(text))


def _card_answers(question: str, name: str, feed: dict[str, Any],
                  about: str = "") -> bool:
    """True when the chat's own card matcher answers *question* from this card."""
    try:
        from puffpai import app_fastpath
        asked = app_fastpath._asked_feed(question)  # noqa: SLF001 - the chat's matcher
        return bool(asked) and asked in app_fastpath._feed_names(  # noqa: SLF001
            name, feed.get("card") or {}, about)
    except Exception:
        return False


def card_questions(card_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """A question each switched-on card answers, in the card's own name.

    "Show me the latest from <card>" first, then the card's own example; a
    phrasing is kept only when the chat's card matcher answers it from this
    card, so the question is checked against the build, not assumed.
    """
    out: list[dict[str, str]] = []
    on = {c["app_id"] for c in card_rows if c.get("status") == "on"}
    if not on:
        return out
    try:
        from puffpai import feeds
        apps = feeds.feed_apps()
    except Exception:
        return out
    for manifest, feed in apps:
        if str(manifest.get("id") or "") not in on:
            continue
        name = " ".join(str(manifest.get("name") or "").split())
        vocab = manifest.get("vocabulary")
        examples = vocab.get("examples") if isinstance(vocab, dict) else None
        about = " ".join(str(manifest.get("shows") or "").split())
        tries = [f"Show me {about[:1].lower()}{about[1:]}"] if about else []
        tries += [f"Show me the latest from {name}"] if name else []
        if isinstance(examples, list) and examples:
            tries.append(_sentence(str(examples[0])))
        for text in tries:
            if _clean_question(text) and _card_answers(text, name, feed, about):
                out.append({"text": text, "why": "card", "from": name})
                break
        if len(out) >= MAX_CARD_QUESTIONS:
            break
    return out


def _default_hits(question: str) -> list[dict[str, Any]]:
    from puffpai.knowledge import book_search
    return book_search.book_hits(question, n=10)


def _chat_hits(question: str) -> list[dict[str, Any]]:
    """The look-up exactly as chat calls it (its own default count of pages).

    A page the chat would not reach is no answer, however well it scores
    further down (a page 8th of 10 is no answer when chat reads 5).
    """
    from puffpai.knowledge import book_search
    return book_search.book_hits(question)


def _answers(question: str, title: str,
             hits: Callable[[str], list[dict[str, Any]]]) -> bool:
    """True when the books' look-up for *question* finds the page *title*."""
    try:
        found = hits(question) or []
    except Exception:
        return False
    want = title.strip().lower()
    return any(str(r.get("title") or "").strip().lower() == want
               and float(r.get("relevance") or 0) >= RELEVANCE_MIN
               for r in found)


def book_questions(goal: str, *, hits: Optional[Callable[[str], list]] = None,
                   limit: int = MAX_BOOK_QUESTIONS) -> list[dict[str, str]]:
    """"Tell me about <page>" for pages the books really hold, from goal words.

    Each goal word is looked up in the books; a page that comes back becomes a
    question only when asking that very question finds the same page again
    above the chat's own relevance cut. No page, no question.
    """
    check = hits or _chat_hits
    hits = hits or _default_hits
    try:
        from puffpai.offers import goal_terms
        terms = goal_terms(goal)[:4]
    except Exception:
        terms = []
    try:
        from puffpai.catalogs import goal_words, knowledge_resolver
        shelves = knowledge_resolver.shelf_words(goal, goal_words.goal_terms(goal))
        # The same bar as everything else the bot offers: the page's title
        # or first paragraph holds one of the goal's phrases as the owner
        # typed them ("Honda Civic"), not one stray word of it.
        def fits(title: str, lead: str) -> bool:
            return goal_words.fits_request(goal, f"{title} {lead}",
                                           shelves=shelves, title=title)
    except Exception:
        fits = lambda title, lead: True  # noqa: E731 - no word lists, no filter
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for term in terms:
        try:
            found = hits(term) or []
        except Exception:
            continue
        for row in found[:10]:
            title = " ".join(str(row.get("title") or "").split())
            if (not title or title.lower() in seen or _TITLE_SKIP.search(title)
                    or float(row.get("relevance") or 0) < RELEVANCE_MIN):
                continue
            if _QUESTION_OK.match(title):
                question = title[:1].upper() + title[1:]
            elif _TITLE_OK.match(title):
                question = f"Tell me about {title}"
            else:
                continue
            if not _clean_question(question) or not _answers(question, title, check):
                continue
            # The page must be about the goal ('Toyota Corolla' is not a
            # question for a Honda Civic bot).
            if not fits(title, str(row.get("lead") or "")):
                continue
            seen.add(title.lower())
            out.append({"text": question, "why": "book",
                        "from": _clip(str(row.get("library") or ""), 80)})
            break  # one page per goal word, so the questions cover the goal
        if len(out) >= limit:
            break
    return out


def learning_question() -> list[dict[str, str]]:
    """"Are you done learning?" while a book is still being learned."""
    try:
        from puffpai.knowledge import progress
        recs = progress.read_all()
    except Exception:
        return []
    if any(str(r.get("stage") or "") != "done" for r in recs):
        text = "Are you done learning?"
        if progress.asks_about_learning(text):
            return [{"text": text, "why": "learning", "from": ""}]
    return []


_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()


def _fingerprint(goal: str, card_rows: list[dict[str, Any]]) -> str:
    try:
        from puffpai.knowledge import progress
        books = [(r.get("id"), r.get("stage"), r.get("quick_ready"))
                 for r in progress.read_all()]
    except Exception:
        books = []
    shelf: list = []
    try:
        import os
        from puffpai.knowledge import book_search
        folder = book_search._books_dir(None)  # noqa: SLF001 - the look-up's own folder
        shelf = sorted((n, os.path.getmtime(os.path.join(folder, n)))
                       for n in os.listdir(folder) if n.endswith(".db"))
    except Exception:
        shelf = []
    blob = json.dumps({"goal": goal, "cards": [(c["app_id"], c["status"]) for c in card_rows],
                       "books": books, "shelf": shelf}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def try_asking(goal: str, card_rows: list[dict[str, Any]], *,
               hits: Optional[Callable[[str], list]] = None,
               use_cache: bool = True) -> list[dict[str, str]]:
    """3 to 5 questions this build can answer, "What can you do?" last."""
    key = _fingerprint(goal, card_rows) if use_cache else ""
    if key:
        with _CACHE_LOCK:
            if _CACHE.get("key") == key:
                return list(_CACHE["value"])
    picked = card_questions(card_rows)
    picked += book_questions(goal, hits=hits,
                             limit=min(MAX_BOOK_QUESTIONS,
                                       max(1, MAX_QUESTIONS - 1 - len(picked))))
    picked += learning_question()
    picked = picked[:MAX_QUESTIONS - 1]
    picked.append({"text": ALWAYS_ASK, "why": "general", "from": ""})
    if key:
        with _CACHE_LOCK:
            _CACHE.update({"key": key, "value": list(picked)})
    return picked


def forget_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def front(now: Optional[datetime] = None, surface: str = "browser") -> dict[str, Any]:
    """The whole front page. Never raises; a part that cannot be read is empty."""
    name = _name()
    goal = _goal()
    rows = _abilities()
    voice = _voice(rows)
    consent = _consent()
    on_count = consent.get("count")
    card_rows = cards()
    try:
        questions = try_asking(goal, card_rows)
    except Exception:
        logger.warning("front: questions failed", exc_info=True)
        questions = [{"text": ALWAYS_ASK, "why": "general", "from": ""}]
    talk_line = (f"Say \"{WAKE_PHRASE}\", or type below to talk to {name}."
                 if voice else f"Type below to talk to {name}.")
    # The consent line and the same on-list and count as "What
    # leaves your computer", never "stays on this computer" on its own.
    outside_line = str(consent.get("line") or "")
    return {
        "name": name,
        # The bot's short description ("A wedding planner bot."), the same
        # line its own page shows; never her whole goal sentence.
        "purpose": _clip(_short_purpose(goal, name), PURPOSE_CHARS),
        "hello": hello(name, goal),
        "dateline": dateline(now, outside_on=on_count),
        "talk": {"voice": voice, "line": talk_line, "href": CHAT_HREF},
        "today": today(now),
        "cards": card_rows,
        # What Toolsmith added (apps, lists, pictures, the mood board), and
        # the Finish card when things wait for the locked box.
        "apps": app_tiles(),
        "finish": finish_card(),
        # The way to the Home Assistant voice key page.
        "voice_key": voice_key_link(),
        # What the owner put on the home page, in order (home_placement).
        "placed": _placed(card_rows),
        # Cards added from the Add page but left off the home page.
        "off_home": _off_home(card_rows),
        "try_asking": questions,
        "knows": knows(surface),
        "not_connected": not_connected(rows, goal, surface, name),
        "add": {"label": f"Add to {name}", "href": ADD_HREF,
                "line": f"Tell {name} what else it should know or show, in your "
                        f"own words."},
        "outside": {"label": "What leaves your computer", "href": OUTSIDE_HREF,
                    "line": outside_line,
                    "consent_line": consent.get("consent_line"),
                    "count": on_count, "on": list(consent.get("on") or [])},
        "looks": {"label": "Looks", "href": LOOKS_HREF,
                  "line": f"Change the colours of {name}'s pages from your own "
                          f"words. One step takes it back."},
        "keys": {"label": "Sign-up keys", "href": KEYS_HREF,
                 "line": keys_line(name, _web_search_on())},
    }


def _web_search_on() -> bool:
    """True only when the web search outside service is switched on. Unknown
    reads as off, so the line never promises a search that is not there."""
    try:
        from puffpai import outside_services
        return any(r.get("id") == "web_search" and r.get("state") == "on"
                   for r in outside_services.rows())
    except Exception:
        return False


def keys_line(name: str, web_on: bool) -> str:
    """The Sign-up keys line. It says Wikipedia search works only when this
    build's web search is on (never while it is off)."""
    if web_on:
        return (f"{name} searches Wikipedia with no sign-up. A free key from a "
                f"search service lets it search the whole web too.")
    return (f"Web search is off for {name}. A free key from a search service "
            f"is kept here for when you switch it on.")


def _placed(card_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        from puffpai import home_placement
        return home_placement.front_rows(card_rows)
    except Exception:
        logger.warning("front: placed failed", exc_info=True)
        return []


def _off_home(card_rows: list[dict[str, Any]]) -> list[str]:
    try:
        from puffpai import home_placement
        return home_placement.off_home(card_rows)
    except Exception:
        logger.warning("front: off_home failed", exc_info=True)
        return []


def owner_text(page: dict[str, Any]) -> str:
    """Every string on the page, one per line (for the plain-words check)."""
    lines: list[str] = []

    def walk(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key not in ("href", "app_id", "id", "why",
                                                   "stage", "kind", "off_home"):
            lines.append(node)
    walk(page)
    return "\n".join(lines)


@bp.route("/front", methods=["GET"])
def front_get():
    surface = request.args.get("surface", "browser")
    surface = surface if surface in ("browser", "terminal", "chat") else "browser"
    return jsonify(front(surface=surface))


__all__ = ["ALWAYS_ASK", "WAKE_ABILITY", "app_tiles", "book_questions", "bp", "card_questions",
           "cards", "dateline", "finish_card", "forget_cache", "front", "hello", "knows",
           "voice_key_link",
           "not_connected", "owner_text", "today", "try_asking"]
