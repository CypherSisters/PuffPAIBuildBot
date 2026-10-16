"""The chat step that drives integrated apps: one bounded step, one action.

:func:`try_answer` is called by every chat path right after the app fastpath.
``None`` means "not for us" and ordinary chat continues. The rules (design
2.11, M1, M2, invariants 3-5):

0. **Identity.** Nothing happens unless ``device_id`` is a paired device or
   this terminal (``consent_tickets.usable_device``), or, on the voice
   surface only, Home Assistant's voice key (:mod:`.voice_key`: reads and
   undoable changes, never removes). No action, no
   read, no app data, no model call otherwise. The backend decides the
   identity (``api_server._actions_identity``); a keyless caller gets None.
1. **Cheap gate.** The message must name an installed app, match an action's
   words (``goal_words.match_strength`` at least 1) or read like a
   do-something request (``router._looks_like_action_request``); else None
   with no model call. A QUESTION (who, what, how many, ...; no change verb)
   is a read: rules first (the read whose list the question names, "who"
   meaning a list of people), and when no rule picks one and the question is
   about the owner's own things ("so far", "my", "left"), the one model call
   is asked with the READ actions only.
2. **The tool-choosing call sees no untrusted text.** Exactly two messages:
   the fixed system prompt and the owner's current message. No history, no
   earlier replies, no library passages, no app data, no README. The tools are
   the checked actions of the matching apps. Temperature 0, one call, only the
   first tool call is used.
3. **Arguments are grounded** in the owner's words: numbers and dates must be
   said, free text must mostly appear; the rest is dropped (then asked for).
4. **One action per owner message.** ``reads`` answer from a template over
   the returned rows, every value neutralised; no model sees app data.
   ``changes`` answer from the run's own evidence and offer "undo".
   ``removes`` never run without an ``action_yes`` ticket minted for this
   device, this action and these arguments: the bot asks first.
5. Two matching apps for the same job: ask which one.
6. Voice: reads and undoable changes only.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import contextvars
import inspect
import json
import re
import threading
import time
from datetime import date, timedelta
from typing import Any, Optional

from puffpai.integrate import actions, grounding, records, voice_key, wording
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SURFACES = ("chat", "voice", "terminal")
PENDING_SECONDS = 600
MAX_MESSAGE = 2000
MAX_TOOLS = 40

#: The one system prompt of the tool-choosing call. Fixed text: nothing from
#: an app, a README, a search or an earlier turn is ever added to it.
SYSTEM_PROMPT = (
    "You are the owner's own bot. You can operate the apps added to this bot "
    "with the tools listed. Use a tool only when the owner asks for something a "
    "tool does, and take every argument from the owner's own words. If no tool "
    "fits, answer normally without a tool. Use at most one tool.")

_YES_REMOVE = re.compile(r"^\s*yes,?\s+remove(?:\s+(?:it|that|them))?\s*[.!]?\s*$", re.I)
_KEEP = re.compile(r"^\s*(?:no|keep|keep it|no,? keep it|cancel|don'?t)\s*[.!]?\s*$", re.I)
#: "undo the last thing you added", "revert that", "take back the last change".
_UNDO_ASK = re.compile(
    r"(?ix)^\W*(?:(?:please|pls|ok|okay|hey|puff|can\s+you|could\s+you|would\s+you)\W+)*"
    r"(?:undo|revert|reverse|roll\s+back|take\s+(?:it|that|this|back)\s*(?:back)?)\b")
_UNDO_RE = re.compile(r"^\s*(?:undo|undo (?:that|it|please)|please undo|take it back)"
                      r"\s*[.!]?\s*$", re.I)
_READ_RE = re.compile(r"\b(?:what|who|which|show|list|read|how many|tell me)\b", re.I)
#: A question: a question word first, or a question mark at the end.
_QUESTION_RE = re.compile(
    r"^\s*(?:(?:so|and|ok|okay|hey|please)[\s,]+)?(?:who|whom|whose|what|what's|whats|which|"
    r"how\s+many|how\s+much|how\s+long|when|where|is|are|was|were|do|does|did|have|has|any|"
    r"list|show|tell\s+me|read|give\s+me|can\s+(?:you|i)\s+(?:see|show|list|tell|read)|"
    r"let\s+me\s+see)\b", re.I)
#: Words that say the question is about the owner's own things, not the world.
_OWN_RE = re.compile(
    r"\b(?:my|mine|we|us|our|ours|so\s+far|yet|left|still|already|remaining|"
    r"coming|going|attending|invited|signed\s+up|booked|due|pending|overdue|planned|"
    r"saved|logged|recorded|kept|tracked|added|listed|got)\b|"
    r"\b(?:did|have|had|do|am|was)\s+(?:i|we)\b|\b(?:i|we)\s*(?:'ve|'d|\s+have|\s+had|\s+did)\b",
    re.I)
#: "who is coming?" asks for a list of people: attendance words only. Any
#: other "who" question ("who have I played?") is left to the model.
_ATTEND_RE = re.compile(
    r"\b(?:coming|attending|invited|going|joining|rsvp\w*|signed\s+up|showing\s+up|"
    r"turning\s+up|bringing|confirmed|said\s+yes)\b", re.I)
#: A "who" question asks for a list of people.
_PEOPLE = frozenset({
    "guest", "person", "people", "member", "player", "student", "contact", "attendee",
    "invitee", "friend", "family", "customer", "client", "patient", "volunteer",
    "participant", "name", "team", "kid", "child", "relative", "speaker", "musician",
    "parent", "staff", "employee", "worker", "neighbor", "neighbour", "visitor", "rsvp",
    "reader", "helper", "vendor", "supplier", "coach", "teacher", "tutor", "partner"})
_FILLER = frozenset({"read", "the", "list", "a", "an", "all", "of", "your", "my", "show",
                     "every", "in", "on", "to", "and", "for", "from", "with"})
_CHANGE_RE = re.compile(r"\b(?:add|put|remove|delete|take|change|set|clear|mark|move|"
                        r"update|edit|erase|wipe|reset|cancel|drop|replace|import)\b", re.I)

_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}


def _backend():
    """The local model, or None. Tests replace this."""
    try:
        from puffpai.config import get_config
        from puffpai.inference.ollama_backend import OllamaBackend
        cfg = get_config()
        return OllamaBackend(url=cfg.ollama.url, model=cfg.ollama.model,
                             num_ctx=4096, num_predict=256, timeout=60)
    except Exception:  # noqa: BLE001 - no brain means the word-match path
        return None


def _supports_tools(backend: Any) -> bool:
    try:
        params = inspect.signature(backend.chat).parameters
    except (TypeError, ValueError, AttributeError):
        return False
    return "tools" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD
                                    for p in params.values())


def _reply(text: str, pending: Optional[dict] = None) -> dict[str, Any]:
    return {"reply": text, "pending": pending}


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _names(low: str, name: str) -> bool:
    name = _norm(name)
    return len(name) >= 3 and re.search(r"(?<![a-z0-9])" + re.escape(name)
                                        + r"(?![a-z0-9])", low) is not None


def _score(message: str, says: str) -> int:
    """How well the message and an action's words fit, 0 to 3. A side with
    no content words at all ("how are you?") fits nothing: match_strength
    counts that as a weak fit, which must not open the gate."""
    from puffpai.catalogs import goal_words
    try:
        if not goal_words.request_runs(message) or not goal_words.request_runs(says):
            return 0
        return max(goal_words.match_strength(message, says)[0],
                   goal_words.match_strength(says, message)[0])
    except Exception:  # noqa: BLE001
        return 0


def _looks_like_action(low: str) -> bool:
    try:
        from puffpai import router
        return bool(router._looks_like_action_request(low))  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return False


# -- grounding (ported from the app-bridge prototype, llm_drive.ground) ----------
#
# The prototype only asked "did she say some date?" (any digit pair, any word
# starting like a month). Here the VALUE is compared with her words: the
# model's date must be a date she named, its numbers numbers she said, its
# free text mostly her own words.

_MONTH_WORDS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9, "october": 10,
    "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
             "sunday")
_MONTH_ALT = "|".join(sorted(_MONTH_WORDS, key=len, reverse=True))
_ORD = r"(?:st|nd|rd|th)?"
_DATE_VALUE_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?")
_SAID_ISO_RE = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_SAID_NUMERIC_RE = re.compile(
    r"(?<![\d/.-])(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{4}|\d{2}))?(?![\d/.-]*\d)")
_SAID_MONTH_DAY_RE = re.compile(
    rf"\b({_MONTH_ALT})\.?\s+(\d{{1,2}}){_ORD}\b(?:,?\s+(\d{{4}})\b)?")
_SAID_DAY_MONTH_RE = re.compile(
    rf"\b(\d{{1,2}}){_ORD}\s+(?:of\s+)?({_MONTH_ALT})\b\.?(?:,?\s+(\d{{4}})\b)?")
_SAID_WEEKDAY_RE = re.compile(rf"\b({'|'.join(_WEEKDAYS)})\b")
_SAID_TIME_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)|\b(\d{1,2}):(\d{2})\b")
_WORD_RE = re.compile(r"[a-z0-9']+")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _named_dates(low: str, today: date) -> tuple[set, set]:
    """What dates the message names: ``(exact, loose)``. *exact* holds
    ``date`` values; *loose* holds ``(month, day)`` said without a year."""
    exact: set = set()
    loose: set = set()

    def add(y: Optional[int], m: int, d: int) -> None:
        if not (1 <= m <= 12 and 1 <= d <= 31):
            return
        if y is None:
            loose.add((m, d))
            return
        if y < 100:
            y += 2000
        try:
            exact.add(date(y, m, d))
        except ValueError:
            pass

    for y, m, d in _SAID_ISO_RE.findall(low):
        add(int(y), int(m), int(d))
    for a, b, y in _SAID_NUMERIC_RE.findall(low):
        year = int(y) if y else None
        add(year, int(a), int(b))          # month/day
        add(year, int(b), int(a))          # day/month
    for mon, d, y in _SAID_MONTH_DAY_RE.findall(low):
        add(int(y) if y else None, _MONTH_WORDS[mon], int(d))
    for d, mon, y in _SAID_DAY_MONTH_RE.findall(low):
        add(int(y) if y else None, _MONTH_WORDS[mon], int(d))
    if re.search(r"\bday after tomorrow\b", low):
        exact.add(today + timedelta(days=2))
    elif re.search(r"\btomorrow\b", low):
        exact.add(today + timedelta(days=1))
    if re.search(r"\b(?:today|tonight)\b", low):
        exact.add(today)
    for day in _SAID_WEEKDAY_RE.findall(low):
        want = _WEEKDAYS.index(day)
        for ahead in range(0, 14):
            when = today + timedelta(days=ahead)
            if when.weekday() == want:
                exact.add(when)
    return exact, loose


def _time_said(low: str, hour: int, minute: int) -> bool:
    if (hour, minute) == (0, 0):
        return True                          # a date with no time of day
    if (hour, minute) == (12, 0) and re.search(r"\bnoon\b|\bmidday\b", low):
        return True
    for h12, m12, half, h24, m24 in _SAID_TIME_RE.findall(low):
        if half:
            h, mm = int(h12), int(m12 or 0)
            if not 1 <= h <= 12:
                continue
            h = (h % 12) + (12 if half.startswith("p") else 0)
        else:
            h, mm = int(h24), int(m24)
        if (h, mm) == (hour, minute):
            return True
    return False


def _date_grounded(low: str, value: str, today: date) -> bool:
    m = _DATE_VALUE_RE.fullmatch(value.strip())
    if m is None:
        return False
    try:
        when = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return False
    if m.group(4) is not None and not _time_said(low, int(m.group(4)), int(m.group(5))):
        return False
    exact, loose = _named_dates(low, today)
    if when in exact:
        return True
    # "October 1" with no year: this year or the next one, never another.
    return (when.month, when.day) in loose and when.year in (today.year, today.year + 1)


def _numbers_in(text: str) -> list[float]:
    return [float(x) for x in _NUM_RE.findall(text)]


def _number_said(value: Any, said: list[float]) -> bool:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return any(abs(v - n) < 0.005 for n in said)


def _word_said(word: str, tokens: set) -> bool:
    if word in tokens:
        return True
    # plain plurals only: "cake"/"cakes", "dish"/"dishes"
    return (word + "s") in tokens or (word + "es") in tokens or \
        (word.endswith("s") and word[:-1] in tokens) or \
        (word.endswith("es") and word[:-2] in tokens)


def _text_grounded(low: str, value: Any) -> bool:
    text = str(value).lower()
    tokens = set(_WORD_RE.findall(low))
    said_nums = _numbers_in(low)
    # every number in free text must be a number she said
    if any(not _number_said(n, said_nums) for n in _numbers_in(text)):
        return False
    words = _WORD_RE.findall(text)
    if not words:
        return False                         # nothing she said
    content = [w for w in words if len(w) > 2 and not w.isdigit()]
    if not content:
        # only short words or numbers ("ok", "a b", "12"): the whole value
        # must be her own words, in her order.
        return re.search(r"(?<![a-z0-9'])" + r"\W+".join(map(re.escape, words))
                         + r"(?![a-z0-9'])", low) is not None
    hit = sum(1 for w in content if _word_said(w, tokens))
    return hit * 2 > len(content)            # most of it is her own words


#: Words and marks that turn a tick box's meaning around.
_YN_NEGATION = re.compile(
    r"(?ix)\b(?:not|no|nope|never|none|without|unpaid|undone|unticked|unchecked|false|off|"
    r"untick|uncheck|unmark|unset|clear|remove|cancel)\b|n't\b|n[’ʼ]t\b|"
    r"\b(?:is|was|are|has|have|had|does|did|do)nt\b"
    r"|[❌❎\U0001f6ab≠¬~✗✘⛔✖☒]")


def _is_yes_no(enum: Any) -> bool:
    try:
        return {str(v).strip().lower() for v in enum} == {"yes", "no"}
    except TypeError:
        return False


def _yes_no_said(text: str, value: Any) -> bool:
    negated = bool(_YN_NEGATION.search(text))
    return negated if str(value).strip().lower() == "no" else not negated


def ground(text: str, args: dict, specs: dict, *,
           today: Optional[date] = None) -> tuple[dict, list]:
    """Keep only arguments that come from the owner's words.

    Enum values are the app's own words and are kept. A number must be a
    number she said. A date (or a date-shaped value) must be a date she named:
    the same day ("October 1", "10/1", "2026-10-01", "tomorrow", "Friday"),
    with a said year matching, an unsaid year this year or next, and a time of
    day only if she said that time. Free text needs most of its words in her
    message and every number in it said. Anything else is dropped.
    """
    low = str(text or "").lower()
    today = today or date.today()
    said_nums = _numbers_in(low)
    kept: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in (args or {}).items():
        spec = specs.get(key) or {}
        if spec.get("enum"):
            ok = value in spec["enum"]
            if ok and _is_yes_no(spec["enum"]):
                # A tick box: "yes" only when her words say it with no
                # negation, "no" only when they negate or untick ("Sam
                # hasn't paid, untick paid" must never run ticked=yes). Anything else is dropped (asked for).
                ok = _yes_no_said(str(text or ""), value)
        elif spec.get("type") in ("number", "integer"):
            ok = not isinstance(value, bool) and _number_said(value, said_nums)
        elif spec.get("type") == "boolean":
            ok = isinstance(value, bool)
        elif spec.get("format") in ("date", "date-time") \
                or re.match(r"\s*\d{4}-\d{2}-\d{2}", str(value)):
            ok = isinstance(value, str) and _date_grounded(low, value, today)
        elif isinstance(value, (dict, list)):
            ok = False                       # nested values are never her words
        else:
            ok = _text_grounded(low, value)
            from puffpai.webapps import actions as web_actions
            if ok and web_actions.is_choice_words(spec):
                others = [v for k, v in (args or {}).items() if k != key and isinstance(v, str)]
                ok = web_actions.choice_said(text, value, others, web_actions.label_words(spec))
        (kept.__setitem__(key, value) if ok else dropped.append(key))
    return kept, dropped


# -- the model call ----------------------------------------------------------------

def _tools(pool: list[dict]) -> list[dict]:
    return [{"type": "function", "function": {
        "name": a["id"],
        "description": f"{a['app_name']}: {a['says']}",
        "parameters": a["args"]}} for a in pool[:MAX_TOOLS]]


def _tool_calls(out: Any) -> list:
    if isinstance(out, list):
        return out
    if isinstance(out, dict):
        calls = out.get("tool_calls")
        if calls is None and isinstance(out.get("message"), dict):
            calls = out["message"].get("tool_calls")
        return calls if isinstance(calls, list) else []
    return []


def choose(backend: Any, message: str, pool: list[dict]) -> Optional[tuple[str, dict]]:
    """The one tool-choosing call. Returns ``(action id, args)`` of the FIRST
    tool call, or None."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": message}]
    try:
        out = backend.chat(messages, temperature=0, max_tokens=256, tools=_tools(pool))
    except Exception as exc:  # noqa: BLE001 - no pick is ordinary chat
        logger.info("integrate chat: the tool call failed: %s", type(exc).__name__)
        return None
    calls = _tool_calls(out)
    if not calls:
        return None
    first = calls[0] if isinstance(calls[0], dict) else {}
    fn = first.get("function") if isinstance(first.get("function"), dict) else first
    name = str(fn.get("name") or "")
    raw = fn.get("arguments")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    return name, raw if isinstance(raw, dict) else {}


# -- pending removes -----------------------------------------------------------------

def _set_pending(device_id: str, rec: dict) -> None:
    with _lock:
        _pending[device_id] = rec
        if len(_pending) > 64:
            for key in list(_pending)[:16]:
                _pending.pop(key, None)


def _take_pending(device_id: str) -> Optional[dict]:
    with _lock:
        return _pending.pop(device_id, None)


def _has_pending(device_id: str) -> bool:
    with _lock:
        return device_id in _pending


def _confirm_remove(device_id: str, surface: str) -> dict:
    pend = _take_pending(device_id)
    if pend is None:
        return _reply(wording.REMOVE_EXPIRED)
    if time.monotonic() - pend["at"] > PENDING_SECONDS:
        return _reply(wording.REMOVE_EXPIRED)
    got = actions.run(pend["action_id"], pend["args"],
                      {"device_id": device_id, "surface": surface,
                       "ticket": pend["ticket"]})
    if not got.get("ok"):
        return _reply(str(got.get("line") or wording.ACTION_FAILED.format(app=pend["app"])))
    line = _evidence_line(pend["app"], pend["says"], got)
    if got.get("undo_token") is not None:                # undo a remove
        line = f"{line} {wording.CHANGED_UNDO}"
    return _reply(line)


# -- answers -------------------------------------------------------------------------

def _clean(value: Any, limit: int = 80) -> str:
    from puffpai import artifact_review
    text = artifact_review.neutralise_passage(str(value), limit)
    return " ".join(text.split())


def _rows_of(data: Any) -> list:
    if isinstance(data, dict):
        for key in ("rows", "records", "items"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return data if isinstance(data, list) else []


_NAME_FIELDS = ("item", "name", "title", "label", "text", "description")


def _key_value(row: Any) -> str:
    """What one row IS: its name-like field, else its first words, else its
    first value (a row's id number says nothing to the owner)."""
    if isinstance(row, dict):
        lowered = {str(k).lower(): v for k, v in row.items()}
        for key in _NAME_FIELDS:
            value = lowered.get(key)
            if isinstance(value, str) and value.strip():
                return value
        for value in row.values():
            if isinstance(value, str) and value.strip() and re.search(r"[A-Za-z]", value):
                return value
        for value in row.values():
            if isinstance(value, (str, int, float)) and not isinstance(value, bool) \
                    and str(value).strip():
                return str(value)
        return ""
    return str(row) if row is not None else ""


def read_reply(app: str, data: Any) -> str:
    """Count and key values, each neutralised. Never a model's words."""
    rows = _rows_of(data)
    if not rows:
        return wording.READ_NONE.format(app=app)
    values = [v for v in (_clean(_key_value(r)) for r in rows[:8]) if v]
    text = ", ".join(values) if values else ""
    if len(rows) > 8:
        text = f"{text}, {wording.READ_MORE.format(n=len(rows) - 8)}"
    return wording.READ_ROWS.format(n=len(rows), things="things" if len(rows) != 1 else "thing",
                                    app=app, values=text)


def _evidence_line(app: str, says: str, got: dict) -> str:
    line = _clean(got.get("line") or "", 240)
    return line or wording.DONE_CHANGE.format(app=app, says=says.rstrip("."))


def _integration_index() -> tuple[dict[str, dict], set[str]]:
    """Ready integration records by every id an action may name them by, and
    the ids of integrations that are not ready.

    An action's ``integration`` is the integration record id (the pinned
    shape) or, for an app it drives, that app's install id (``app_id``; the
    app host only knows that one, and a static app gets a fresh install id
    per attempt). Both resolve to the same record. An action naming a record
    that is held, refused, failed or removed is never offered."""
    ready: dict[str, dict] = {}
    not_ready: set[str] = set()
    for rec in records.all():
        if not isinstance(rec, dict) or not records.valid_id(rec.get("id")):
            continue
        ids = [rec["id"]]
        if records.valid_id(rec.get("app_id")):
            ids.append(rec["app_id"])
        if rec.get("status") == "ready":
            for i in ids:
                ready[i] = rec
        else:
            not_ready.update(ids)
    return ready, not_ready - set(ready)


def is_question(text: str) -> bool:
    """A question that only asks (no change verb): it is answered by a read."""
    text = str(text or "").strip()
    if not text or _CHANGE_RE.search(text):
        return False
    return text.endswith("?") or bool(_QUESTION_RE.match(text))


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith(("ses", "xes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(text: str) -> set[str]:
    return {_singular(w) for w in _WORD_RE.findall(str(text or "").lower())}


def read_pick(text: str, reads: list[dict]) -> Optional[dict]:
    """The rule for a question: the read whose list the question names, or,
    for a "who" question, the one read that lists people. None when no rule
    picks exactly one (then the model may be asked)."""
    top = read_top(text, reads)
    return top[0] if len(top) == 1 else None


def read_top(text: str, reads: list[dict]) -> list[dict]:
    """The reads that fit a question best by rule (empty when none fits)."""
    if not reads:
        return []
    said = _words(text)
    # "who" means a list of people only when the question is about her own
    # things ("who is coming so far?"), never "who wrote Hamlet?".
    who = bool(re.match(r"^\s*(?:(?:so|and|ok|okay)[\s,]+)?(?:who|whom|whose)\b", text, re.I)
               and (_ATTEND_RE.search(text) or _ANSWERED_RE.search(text)))
    scored = []
    for a in reads:
        nouns = _words(a["says"]) - _FILLER
        score = len(nouns & said) * 2
        if who and nouns & _PEOPLE:
            score += 1
        scored.append((score, a))
    best = max(s for s, _a in scored)
    return [a for s, a in scored if s == best] if best >= 1 else []


def try_answer(message: str, *, surface: str, device_id: str | None) -> dict | None:
    """The answer when *message* is for an integrated app, else None.

    Returns ``{"reply": str, "pending": {...} | None}``.
    """
    token = _turn_recipes.set({})
    try:
        return _try_answer(message, surface=surface, device_id=device_id)
    finally:
        _turn_recipes.reset(token)


def _try_answer(message: str, *, surface: str, device_id: str | None) -> dict | None:
    if surface not in SURFACES:
        return None
    if not actions.caller_ok(device_id, surface):
        return None                                   # 0. identity
    text = str(message or "").strip()[:MAX_MESSAGE]
    if not text:
        return None
    low = _norm(text)
    voice = surface == "voice" or voice_key.is_identity(device_id)
    if voice and _YES_REMOVE.match(text):
        # Voice never removes and never confirms a removal asked for anywhere
        # else: no pending is looked at, no model is asked.
        _take_pending(device_id)
        return _reply(wording.VOICE_REMOVE)
    if _has_pending(device_id):
        if _YES_REMOVE.match(text):
            return _confirm_remove(device_id, surface)
        _take_pending(device_id)   # any other answer is not a yes
        if _KEEP.match(text):
            return _reply(wording.KEPT)
    if status_turn(text):
        # "did you add ...?" is answered from the change
        # log, never by picking (and running) the action it names.
        return _reply(status_answer())
    ready, not_ready = _integration_index()
    listed = [a for a in actions.all_actions()
              if actions.allowed(a) and a["integration"] not in not_ready
              and actions.runnable(a)]
    # "more": the next part of a list she asked to hear whole.
    if _MORE_RE.match(text):
        page = _next_page(device_id)
        if page is not None:
            return _reply(page)
    if _UNDO_RE.match(text) or _UNDO_ASK.match(text):
        if actions.has_undo(device_id):
            last = actions.last_change_app(device_id)
            other = sorted({a["app_name"] for a in listed if _names(low, a["app_name"])
                            and _norm(a["app_name"]) != _norm(last)})
            if last and other:
                # "undo the last thing you added to the Pantry" while the
                # last change was in another app: never undo the wrong one.
                return _reply(wording.UNDO_OTHER_APP.format(last=last, named=other[0]))
            return _reply(actions.undo_last(device_id)["line"])
        return _reply(wording.NO_UNDO) if listed else None
    if not listed:
        return None

    def owner(a: dict) -> str:
        rec = ready.get(a["integration"])
        return rec["id"] if rec else a["integration"]

    # 1. the cheap gate
    recs = {rec["id"]: rec for rec in ready.values()}
    names = {}
    for a in listed:
        names.setdefault(owner(a), a["app_name"])
    for iid, rec in recs.items():
        names.setdefault(iid, rec.get("name") or "")
    named = sorted(i for i, n in names.items() if _names(low, n))
    scores: dict[str, int] = {}
    for a in listed:
        s = _score(text, a["says"])
        scores[owner(a)] = max(scores.get(owner(a), 0), s)
    word_hit = sorted(i for i, s in scores.items() if s >= 1)
    # "was Leo Park removed from the guest list?", "has Nana been
    # added yet?": whether a change happened is the change log's to say,
    # never a read's or the model's. Not a question about someone outside
    # her apps ("was Obama elected?") unless it names one of them.
    if grounding.change_question(text) and (
            named or word_hit
            or not grounding.proper_name_not_hers(text, [n for n in names.values() if n])):
        return _reply(status_answer())
    question = is_question(text)
    if question:
        # A question only reads. Rules first, then (about her own things only)
        # the model with the read actions and nothing else.
        in_scope = [a for a in listed if not named or owner(a) in named]
        reads = [a for a in in_scope if a["effect"] == "reads"
                 and not (a.get("args") or {}).get("required")]
        top = read_top(text, reads)
        if len(top) > 1 and (_ATTEND_RE.search(text) or _ANSWERED_RE.search(text)):
            # "who is coming?" while guests and vendors both list
            # people: the list whose own choices say who is coming.
            top = _attend_reads(top, in_scope) or top
        rule = top[0] if len(top) == 1 else None
        if len({owner(a) for a in top}) >= 2:
            # 5. the same list in two apps: ask which one
            first, second = sorted({names[owner(a)] for a in top})[:2]
            return _reply(wording.WHICH_APP.format(a=first, b=second))
        if rule is None and named and len(reads) == 1:
            rule = reads[0]
        if rule is None:
            # "is Leo on the list?" names one of
            # the app's own rows (by its first name too): that app's read
            # answers it, from the rows, with no model pick.
            rule = _read_for_row(text, in_scope, reads)
        if rule is not None:
            return _run_one(rule, {}, text, surface, device_id)
        # "Is Zanzibar Quill declined?": a yes or no about
        # someone she named is about her things too (reads only).
        if reads and (named or word_hit or _OWN_RE.search(low) or (
                _YES_NO_Q.match(text) and re.search(r"\s[A-Z][a-z]", text))):
            return _choose_and_run(text, reads, surface, device_id)
        return None
    # "Leo Park is attending and his meal
    # is chicken." names one of the app's own rows and fields of it: that is
    # its edit, with no change word and no model needed.
    # A command ("add Leo Park ...", "change ...") goes to the model's pick
    # first; this is for a plain statement only.
    if not _CHANGE_RE.search(text):
        stmt = _row_statement(text, listed)
        if stmt is not None:
            return _run_one(stmt, {}, text, surface, device_id)
    # A change ask goes to the actions even when it names no
    # app and shares no word with one ("Change Leo Park to attending, and
    # his meal is chicken" while the app says "Edit a guest").
    asks_change = change_turn(text, sorted({n for n in names.values() if n}))
    if not named and not word_hit and not _looks_like_action(low) and not asks_change:
        return None
    # 5. two apps for the same job: ask
    if named:
        pool_ids = named
    elif word_hit:
        best = max(scores[i] for i in word_hit)
        pool_ids = [i for i in word_hit if scores[i] == best]
        if len(pool_ids) >= 2:
            needs = [(recs.get(i) or {}).get("need") for i in pool_ids]
            if any(n and needs.count(n) >= 2 for n in needs) or not any(needs):
                a, b = sorted(names[i] for i in pool_ids)[:2]
                return _reply(wording.WHICH_APP.format(a=a, b=b))
    else:
        pool_ids = sorted(names)
    pool = [a for a in listed if owner(a) in pool_ids]
    if not pool:
        return None
    got = _choose_and_run(text, pool, surface, device_id)
    if got is None and asks_change:
        # The model picked nothing for "Change Leo Park to attending, and his
        # meal is chicken.": the one edit whose row and fields she named.
        stmt = _row_statement(text, pool)
        if stmt is not None:
            return _run_one(stmt, {}, text, surface, device_id)
    if got is None and asks_change and (named or word_hit):
        # Nothing could run for an ask that is plainly about her apps: a
        # plain reason and a next step, never the model. (Any other change
        # ask goes on to the backend's other steps, and then to its own
        # honest line before the model.)
        apps = sorted({names[owner(a)] for a in pool})
        if len(apps) == 1:
            can = [a["says"].rstrip(".") for a in pool if a["effect"] != "reads"]
            if can:
                return _reply(wording.NOTHING_FITS.format(
                    app=apps[0], can=_join_words([c[:1].lower() + c[1:] for c in can[:6]])))
        return _reply(wording.NO_CHANGE_UNKNOWN)
    return got


def _join_words(items: list[str], last: str = "or") -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {last} " + items[-1]


def _choose_and_run(text: str, pool: list[dict], surface: str,
                    device_id: str) -> Optional[dict]:
    # 2. the one tool-choosing call (or the word-match path without tools)
    backend = _backend()
    if backend is not None and _supports_tools(backend):
        picked = choose(backend, text, pool)
    else:
        picked = _word_pick(text, pool)
    if picked is None:
        return None
    action = next((a for a in pool if a["id"] == picked[0]), None)
    if action is None:
        return None                                   # a name we did not offer
    return _run_one(action, picked[1], text, surface, device_id)


#: An action that changes a record she already has ("Edit a guest").
_EDIT_LIKE = re.compile(r"(?i)^\s*(?:edit|change|update|set|mark|rename|move|fix|correct)\b")
#: Words of her message that say nothing about a value.
_LEFT_STOP = frozenset("""
add put remove delete change set mark edit update make please pls list lists app apps want
wants wanted like would could should will can also too and but the his her their its our
your my for with from into onto that this these those there here then than now just really
him them she they you yes okay sure thanks thank new one ones some any all
""".split())


def _field_words(keys: list[str], props: Optional[dict] = None) -> str:
    """The fields in the app's own words ("meal choice and RSVP status")."""
    props = props or {}
    return _join_words([_field_label(k, props.get(k)) for k in keys], "and")


def _values_line(args: dict, props: dict) -> str:
    """What went in, in her own words: "guest name Leo Park, meal chicken"."""
    pairs = []
    for key in props:
        if key in args and args[key] not in (None, ""):
            value = args[key]
            if isinstance(value, bool):
                value = "yes" if value else "no"
            shown = _clean(value, 60)
            if shown:
                pairs.append(f"{_field_label(key, props.get(key))} {shown}")
    return wording.VALUES_SET.format(values=", ".join(pairs)) if pairs else ""


def _left_out(text: str, action: dict, args: dict, dropped: list, props: dict,
              used: Optional[set] = None) -> list[str]:
    """Fields she may have meant that were not set: the ones the model gave
    that her words did not hold, and (when her message says more than went
    in) the ones left empty."""
    required = set((action.get("args") or {}).get("required") or [])
    out = [k for k in dropped if k in props]
    skip = (_folded_words(" ".join(str(v) for v in args.values()))
            | _folded_words(action.get("says") or "") | _folded_words(action.get("app_name") or "")
            # the field names themselves ("set the meal to Fish")
            | _folded_words(" ".join(k.replace("_", " ") for k in props))
            | _folded_words(" ".join(_field_label(k, props.get(k)) for k in props))
            | {_fold(w) for w in (used or ())})
    leftover = [w for w in _WORD_RE.findall(_plain(text).lower())
                if len(_fold(w)) >= 3 and _fold(w) not in skip and w not in _STOP
                and _fold(w) not in _LEFT_STOP and not _THING_WORD.fullmatch(w)]
    if leftover:
        empty = [k for k in props if k not in args and k not in required and k not in out]
        # Name only the fields her left-over words are
        # about (their label words, or a "coming" for an attendance choice),
        # when that can be told; never fields she did not mention.
        said = {_fold(w) for w in leftover}
        about = [k for k in empty if {_fold(w) for w in _label_words(k, props.get(k))
                                      - _FIELD_GENERIC} & said]
        if said & {_fold(w) for w in _ATTEND_YES}:
            about += [k for k in empty if k not in about and any(
                set(_WORD_RE.findall(str(c).lower())) & _ATTEND_YES
                for c in (_field_choices(action, k) or ()))]
        if about:
            empty = about
        # a field the app lets her pick words for comes first ("meal", "rsvp")
        empty.sort(key=lambda k: 0 if (props.get(k) or {}).get("description") else 1)
        out.extend(empty)
    return out[:3]


def _run_one(action: dict, raw_args: dict, text: str, surface: str,
             device_id: str) -> dict:
    # 3. ground the arguments
    props = (action.get("args") or {}).get("properties") or {}
    args, dropped = ground(text, {k: v for k, v in (raw_args or {}).items() if k in props},
                           props)
    from puffpai.webapps import actions as web_actions   # an app choice
    args, dropped = web_actions.ground_choices(action, text, raw_args, args, dropped)
    used: set = set()
    if action.get("effect") == "changes":
        # The row she named and the fields her clauses name reach the
        # action (each value through the choice checks in webapps.actions).
        args, dropped, used = _edit_fill(action, text, args, dropped, props)
    required = list((action.get("args") or {}).get("required") or [])
    app = action["app_name"]
    if voice_key.is_identity(device_id):
        surface = "voice"                             # the voice key is always voice
    effect = action["effect"]
    # Voice rules first: voice is never asked for a detail of something it
    # cannot do ("Tell me the guest name" for a removal would be a promise).
    if surface == "voice" and effect == "removes":
        return _reply(wording.VOICE_REMOVE)
    if surface == "voice" and effect == "changes" and not action.get("undoable"):
        return _reply(wording.VOICE_CHANGE)
    # An edit or a removal names one of the app's own rows. A first
    # name that starts one row is that row ("Leo" -> "Leo Park"), so the
    # removal she confirms is the one that runs; a name that is no row is
    # said plainly (never "tell me the guest name" after she gave one).
    row_line = _check_row(action, args, text)
    if row_line is not None:
        return _reply(row_line)
    missing = [r for r in required if r not in args]
    if missing:
        from puffpai.webapps import actions as web_actions
        box = web_actions.tick_box_field(props.get(missing[0]))
        if box:
            return _reply(wording.NEED_TICK.format(field=box, app=app))
        return _reply(wording.NEED_MORE.format(field=_field_label(missing[0],
                                                                  props.get(missing[0])),
                                               app=app))
    # 4. exactly one action
    if effect == "removes":
        return _ask_remove(action, args, device_id)
    optional = [k for k in props if k not in required]
    if effect == "changes" and optional and _EDIT_LIKE.match(action.get("says") or "") \
            and not any(k in args for k in optional):
        # An edit that would set nothing: nothing would change, so nothing
        # runs, and she hears what to say instead of "Done".
        what = next((_clean(args[r], 60) for r in required
                     if isinstance(args.get(r), str) and args[r].strip()), "") or "that"
        fields = _field_words((_mentioned_fields(action, text, props, required)
                               or dropped or optional)[:3], props)
        return _reply(wording.EDIT_NOTHING.format(app=app, what=what, fields=fields))
    got = actions.run(action["id"], args, {"device_id": device_id, "surface": surface})
    if not got.get("ok"):
        line = str(got.get("line") or wording.ACTION_FAILED.format(app=app))
        if got.get("unchanged") and dropped:
            line = f"{line} {wording.NOT_SET.format(fields=_field_words(dropped[:3], props))}"
        return _reply(line)
    if effect == "reads":
        reply = read_answer(action, got.get("data"), text, device_id)
        n = got.get("examples")
        if isinstance(n, int) and not isinstance(n, bool) and n > 0 and _rows_of(got.get("data")):
            from puffpai.webapps import wording as web_wording   # example rows
            reply = f"{reply} {web_wording.examples_read_line(n)}"
        return _reply(reply)
    line = _evidence_line(app, action["says"], got)
    values = _values_line(args, props)
    if values:
        line = f"{line} {values}"
    left = _left_out(text, action, args, dropped, props, used)
    if left:
        line = f"{line} {wording.NOT_SET.format(fields=_field_words(left, props))}"
    if got.get("undo_token") is not None:
        line = f"{line} {wording.CHANGED_UNDO}"
    return _reply(line)


# -- edits that reach the row and the fields she named ---------------------------
#
# With "Edit a guest" compiled, each of these can fail a different way: "Leo
# Park is attending and his meal is chicken." (the model asks questions back),
# "Change Leo Park to attending, and his meal is chicken." ("Tell me the new
# meal choice and rsvp status") and "Set Leo Park's rsvp status to attending
# and his meal choice to chicken." ("Tell me the guest name"). Checked against
# her WHOLE message, where "and his meal is chicken" stands beside
# "attending", the model's arguments get no value through the choice word
# check (rightly). So:
#
# * the row is one of the app's own rows she named (from the bot's copy of
#   the app, read through the recipe's own check), or the model's grounded
#   value, or the capitalised name she gave;
# * her message is cut into clauses ("Leo Park is attending", "his meal is
#   chicken"); a clause belongs to the field whose own label words it holds
#   ("meal"), else to the one choice field that her words resolve to;
# * each value goes through the choice checks on its own clause
#   (:func:`webapps.actions.choice_said`, then
#   :func:`webapps.actions.resolve_choice` against the app's choices), so a
#   "no" on one side, or a word that could turn a meaning around, still sets
#   nothing and she is asked.
#
# No app's words are in here: field labels and choices come from the app.

#: Label words that say nothing about which field: "rsvp STATUS", "meal CHOICE".
_FIELD_GENERIC = frozenset("status choice choices name names type value values option "
                           "options the a an of info details detail".split())
#: Little words of hers around a value that never change what it means.
_EDIT_LINK = frozenset("""
and also plus with is are was be been being to as set change changed make mark put update
updated edit now his her their its the a an he she they it him them we i you please pls
should will would wants want like likes prefers prefer gets get getting having have has
for of s re m ll ve d just too so then that this new
""".split())
#: Her words for "is coming": matched only to the app's own choice of the same
#: meaning, and only with no "no" anywhere in the clause.
_ATTEND_YES = frozenset("attending attend attends coming going joining confirmed accepted "
                        "accept yes rsvpd".split())
_CLAUSE_CUT = re.compile(r"(?i)\s*(?:[,;:]|\.(?:\s+|$)|\s(?:and|but|also|plus|while)\s)\s*")


def _field_label(key: str, spec: Any) -> str:
    """The app's own words for a field ("RSVP status"), not its raw name."""
    from puffpai.webapps import actions as web_actions
    text = str((spec or {}).get("description") or "") if isinstance(spec, dict) else ""
    hint = ", " + web_actions._CHOICE_HINT                       # noqa: SLF001
    if text.endswith(hint):
        text = text[:-len(hint)]
    text = re.sub(r",\s*(?:YYYY-MM-DD|HH:MM)$", "", text).strip()
    if text.startswith("the ") and text.endswith("tick box"):
        text = text[4:-9].strip() or key.replace("_", " ")
    text = text.split(",")[0].strip()        # "Meal, in the owner's words" -> "Meal"
    if not text or len(text) > 40 or not re.search(r"[A-Za-z]", text):
        text = key.replace("_", " ")
    words = []
    for w in text.split():
        # an acronym ("RSVP", or "rsvp" from a field name) stays capitals;
        # any other word is said the way it reads mid-sentence ("meal choice")
        if (2 <= len(w) <= 5 and w.isalpha() and not re.search(r"[aeiouy]", w.lower())) \
                or (len(w) > 1 and w.isupper()):
            w = w.upper()
        else:
            w = w.lower()
        words.append(w)
    return " ".join(words)


def _label_words(key: str, spec: Any) -> set:
    return set(_WORD_RE.findall(_field_label(key, spec).lower())) | set(
        w for w in key.lower().split("_") if w)


def _recipe_of(action: dict) -> Optional[dict]:
    """The compiled recipe behind a web app action, or None."""
    if action.get("kind") != "web_recipe":
        return None
    try:
        app_id, _, name = str(action.get("id") or "").partition("__")
        return next((r for r in _recipes_of(app_id) if r.get("name") == name), None)
    except Exception:  # noqa: BLE001 - no recipe: nothing is guessed
        return None


#: One turn reads an app's recipes many times (its choices, rows and
#: lists for every field); each load hashes the app's files. Within ONE call
#: of :func:`try_answer` they are read once (only for what chat says; every
#: RUN still goes through the recipe checks in webapps.actions).
_turn_recipes: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar(
    "puffpai_turn_recipes", default=None)


def _recipes_of(app_id: str) -> list:
    from puffpai.webapps import recipes
    cache = _turn_recipes.get()
    if cache is not None and app_id in cache:
        return cache[app_id]
    got = list(recipes.load(app_id) or [])
    if cache is not None:
        cache[app_id] = got
    return got


def _field_choices(action: dict, key: str) -> Optional[list]:
    """The app's own choices for one field of *action*, or None when they
    are not known (never shown to the model; only matched to her words)."""
    recipe = _recipe_of(action)
    spec = ((recipe or {}).get("args") or {}).get(key)
    enum = spec.get("enum") if isinstance(spec, dict) else None
    return [str(c) for c in enum] if isinstance(enum, list) and enum else None


def _row_keys(action: dict) -> list[str]:
    """The names of the rows *action* can pick ("Leo Park", "Sahib"), read
    from the bot's copy of the app through the recipe's own check, or []."""
    recipe = _recipe_of(action)
    check = (recipe or {}).get("check") or {}
    match = check.get("match") or {}
    if not check.get("storage_key") or not isinstance(match, dict) or len(match) != 1:
        return []
    field = next(iter(match))
    try:
        from puffpai.webapps import mirror, recipe_check
        data = (mirror.get(str(action.get("integration") or "")) or {}).get("data") or {}
        raw = data.get(check["storage_key"])
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        items = recipe_check.pick(parsed, check.get("list") or "")
    except Exception:  # noqa: BLE001 - no copy: no row is named from it
        return []
    items = items if isinstance(items, list) else []
    return [str(it.get(field)).strip() for it in items[:2000]
            if isinstance(it, dict) and isinstance(it.get(field), str) and it.get(field).strip()]


def _attend_reads(reads: list[dict], pool: list[dict]) -> list[dict]:
    """The *reads* whose list has a choice that means coming ("Attending"),
    from the app's own row actions for the same list."""
    out = []
    for r in reads:
        said = _words(r.get("says"))
        for a in pool:
            if a.get("effect") == "reads" or a.get("integration") != r.get("integration"):
                continue
            if not (_words(row_thing(a)) - _FILLER) & said:
                continue
            props = (a.get("args") or {}).get("properties") or {}
            for k in props:
                choices = _field_choices(a, k) or []
                if {str(c).strip().lower() for c in choices} <= {"yes", "no"}:
                    continue                     # a tick box is not "who is coming"
                if _attend_value("coming", choices):
                    out.append(r)
                    break
            if r in out:
                break
    return out


def _read_for_row(text: str, pool: list[dict], reads: list[dict]) -> Optional[dict]:
    """The one read whose list holds the one row her question names, or None.
    The rows come from the app's own row actions (edit, remove, add); the
    read is the one of the same app whose words share that row's noun
    ("Read the guest list" for "Edit a guest")."""
    hits: dict[str, tuple[dict, str]] = {}
    for a in pool:
        if a.get("effect") == "reads":
            continue
        rows = _row_keys(a)
        if not rows:
            continue
        row = _named_row(text, rows)
        if row is None:
            continue
        noun = _words(row_thing(a)) - _FILLER
        for r in reads:
            if r.get("integration") == a.get("integration") and noun & _words(r.get("says")):
                hits[r["id"]] = (r, row)
    return next(iter(hits.values()))[0] if len(hits) == 1 else None


def _row_prop(action: dict) -> Optional[str]:
    """The argument that names the row *action* works on ("guest_name"), from
    its recipe's own check, or None."""
    recipe = _recipe_of(action)
    match = ((recipe or {}).get("check") or {}).get("match") or {}
    if not isinstance(match, dict) or len(match) != 1:
        return None
    prop = next(iter(match.values()))
    return prop if isinstance(prop, str) else None


def row_thing(action: dict, n: int = 1) -> str:
    """What one row of *action* is called, from its own words: "Edit a
    guest" -> "guest" ("guests" for several); "thing" when they do not say."""
    m = re.search(r"(?i)\b(?:a|an)\s+([a-z][\w-]*(?:\s+[a-z][\w-]*)?)\s*\.?$",
                  str(action.get("says") or "").strip())
    if not m or len(m.group(1)) > 30:
        return "thing" if n == 1 else "things"
    words = m.group(1).lower().split()
    return " ".join(words[:-1] + [words[-1] if n == 1 else _plural(words[-1])])


def _check_row(action: dict, args: dict, text: str) -> Optional[str]:
    """None when the row of an edit or a removal is one of the app's rows (a
    first name is put back as the whole one), else the plain line to say."""
    effect = action.get("effect")
    if not (effect == "removes" or (effect == "changes"
                                    and _EDIT_LIKE.match(action.get("says") or ""))):
        return None
    prop = _row_prop(action)
    if not prop:
        return None
    rows = _row_keys(action)
    if not rows:
        return None                     # no copy to check against: the app decides
    app = action.get("app_name") or ""
    given = args.get(prop)
    said = given if isinstance(given, str) and given.strip() else ""
    if not said:
        said = re.sub(r"'s$", "", _asked_name(text, app, action.get("says") or ""))
        if not said:
            return None                 # no name at all: she is asked for it
    if said.strip().casefold() in {r.casefold() for r in rows}:
        args[prop] = next(r for r in rows if r.casefold() == said.strip().casefold())
        return None
    whole = _whole_row(said, rows)
    if whole is not None:
        args[prop] = whole
        return None
    firsts = first_name_rows(said, rows) or (first_name_rows(text, rows) if not given else [])
    if len(firsts) == 1:
        args[prop] = firsts[0]
        return None
    if len(firsts) > 1:
        return wording.WHICH_ROW.format(app=app, n=len(firsts), things=row_thing(action, 2),
                                        first=_clean(said.split()[0], 40),
                                        names=_join_words([_clean(f, 60) for f in firsts[:6]]))
    return wording.NO_SUCH_ROW.format(thing=row_thing(action), app=app, name=_clean(said, 60))


def _named_row(text: str, rows: list[str]) -> Optional[str]:
    """The one row of *rows* her message names: the whole name (the longest
    when one name holds another: "Leo Park" over "Leo"), else a first name
    that starts exactly one row ("Leo" -> "Leo Park").
    None when no row, or more than one, fits."""
    whole = _whole_row(text, rows)
    if whole is not None:
        return whole
    hits = first_name_rows(text, rows)
    return hits[0] if len(hits) == 1 else None


def first_name_rows(text: Any, rows: list[str]) -> list[str]:
    """The rows of more than one word whose FIRST word she wrote as a name
    ("Leo" for "Leo Park", "leo" too): a word of hers that is capitalised or
    is not an everyday English word. Only rows written like a name count,
    and never one whose first word is an everyday English word ("Will
    Smith" needs his whole name)."""
    raw = _plain(text)
    said = set()
    found = list(re.finditer(r"[^\W\d_]+(?:'[^\W\d_]+)?", raw))
    for i, m in enumerate(found):
        w = re.sub(r"'s$", "", m.group(0))
        nxt = found[i + 1] if i + 1 < len(found) else None
        if (w[:1].isupper() and nxt is not None and nxt.group(0)[:1].isupper()
                and not raw[m.end():nxt.start()].strip()):
            continue    # "Leo Parker": a whole name of its own, not "Leo" alone
        if w[:1].isupper() or w.lower() not in grounding._EN:  # noqa: SLF001
            said.add(w.lower())
    out = []
    for r in rows or ():
        # Only a row written like a name ("Leo Park", "Mary-Kate O'Neil"; two
        # or three capitalised words, the first no everyday English word) is
        # named by its first word: never "Set the date & overall budget"
        # for "Set Leo Park's rsvp ...".
        parts = str(r).split()
        if not (2 <= len(parts) <= 3 and all(
                re.fullmatch(r"[^\W\d_]+(?:['-][^\W\d_]+)*", p) and p[:1].isupper()
                for p in parts)):
            continue
        first = parts[0].lower()
        if len(first) >= 3 and first not in grounding._EN and first in said:  # noqa: SLF001
            out.append(str(r))
    seen = []
    for r in out:
        if r.lower() not in {s.lower() for s in seen}:
            seen.append(r)
    return seen


def _whole_row(text: str, rows: list[str]) -> Optional[str]:
    """The one row of *rows* her message names in full, or None."""
    low = " " + " ".join(_WORD_RE.findall(_plain(text).lower().replace("'s ", " "))) + " "
    hits = []
    for r in rows:
        words = _WORD_RE.findall(r.lower())
        if words and len("".join(words)) >= 3 and (" " + " ".join(words) + " ") in low:
            hits.append(r)
    if not hits:
        return None
    longest = max(len(h) for h in hits)
    top = {h.lower() for h in hits if len(h) == longest}
    return next(h for h in hits if len(h) == longest) if len(top) == 1 else None


def _attend_choice(choices: Optional[list], clause_words: list[str], clause: str) -> Optional[str]:
    """"he's coming" -> the app's own "Attending": only one of its choices
    that means coming, and only with no "no" in her clause."""
    from puffpai.webapps import actions as web_actions
    if not choices or web_actions._NEGATION.search(                 # noqa: SLF001
            web_actions._choice_form(clause)):                      # noqa: SLF001
        return None
    if not clause_words or not all(w in _ATTEND_YES or w in _EDIT_LINK for w in clause_words):
        return None
    if not any(w in _ATTEND_YES for w in clause_words):
        return None
    yes = [c for c in choices
           if set(_WORD_RE.findall(c.lower())) & _ATTEND_YES
           and not web_actions._NEGATION.search(web_actions._choice_form(c))]  # noqa: SLF001
    return yes[0] if len(yes) == 1 else None


def _edit_fill(action: dict, text: str, args: dict, dropped: list,
               props: dict) -> tuple[dict, list, set]:
    """*args* with the row and the fields her clauses name filled in, the
    fields still *dropped*, and the words of hers that were used."""
    from puffpai.webapps import actions as web_actions
    args = dict(args)
    used: set = set()
    required = list((action.get("args") or {}).get("required") or [])
    keys = [k for k in required if (props.get(k) or {}).get("type") == "string"
            and not web_actions.is_choice_words(props.get(k))]
    edit = bool(_EDIT_LIKE.match(action.get("says") or ""))
    # 1. the row
    if len(keys) == 1 and edit:
        k = keys[0]
        rows = _row_keys(action)
        named = _named_row(text, rows) if rows else None
        if named is not None:
            args[k] = named
        elif k not in args:
            asked = re.sub(r"'s$", "", _asked_name(text, action.get("app_name") or "",
                                                   action.get("says") or ""))
            if asked and (not rows or _named_row(asked, rows)):
                args[k] = _named_row(asked, rows) if rows else asked
        if k in args:
            dropped = [d for d in dropped if d != k]
    names = [str(args[k]) for k in keys if isinstance(args.get(k), str)]
    for n in names:
        used |= set(_WORD_RE.findall(n.lower()))
    # 2. her clauses, without the row's name
    body = _plain(text)
    for n in names:
        body = re.sub(r"(?i)\b" + re.escape(n) + r"(?:'s)?\b", " ", body)
        # The row named by its first name alone ("Leo" for "Leo Park").
        first = (n.split() or [""])[0]
        if len(n.split()) >= 2 and len(first) >= 3:
            body = re.sub(r"\b" + re.escape(first) + r"(?:'s)?\b", " ", body)
    clauses = [c.strip() for c in _CLAUSE_CUT.split(body) if c and c.strip()]
    says_words = set(_WORD_RE.findall(str(action.get("says") or "").lower())) | set(
        _WORD_RE.findall(str(action.get("app_name") or "").lower()))
    # A tick box is the choice checks' alone: the model's words for it, read by
    # its own check, or she is asked (never filled from a clause here).
    open_fields = [k for k in props if k not in keys and k not in args
                   and web_actions.tick_box_field(props.get(k)) is None]
    labels = {k: _label_words(k, props.get(k)) for k in open_fields}
    for clause in clauses:
        # "he's coming": the contraction is two little words, as the
        # choice check reads it.
        words = _WORD_RE.findall(re.sub(r"'(s|re|m|ll|ve|d)\b", r" \1", clause.lower()))
        if not words:
            continue
        mine = [k for k in open_fields if k not in args
                and (labels[k] - _FIELD_GENERIC) & set(words)]
        if len(mine) > 1:
            continue                                   # two fields in one clause: ask
        harmless = _EDIT_LINK | says_words
        rest = [w for w in words if w not in harmless
                and not (mine and w in labels[mine[0]])]
        if not rest:
            continue
        value = " ".join(rest)
        targets = mine or [k for k in open_fields if k not in args
                           and web_actions.is_choice_words(props.get(k))]
        picked: list[tuple[str, str]] = []
        for k in targets:
            spec = props.get(k) or {}
            if web_actions.is_choice_words(spec):
                choices = _field_choices(action, k)
                attend = _attend_choice(choices, [w for w in words if w not in labels[k]], clause)
                if attend is not None:
                    picked.append((k, attend))
                    continue
                ok = web_actions.choice_said(clause, value, names,
                                             frozenset(labels[k] | harmless))
                if ok and (choices is None and mine or
                           choices is not None and web_actions.resolve_choice(
                               choices, value, names) is not None):
                    picked.append((k, value))
                elif choices is not None:
                    # "she can't make it", "wants the fish
                    # instead", "on the groom's side" (her clause, the app's choice)
                    own = web_actions.choice_in_clause(choices, clause, says_words, names,
                                                       label=labels[k], need_evidence=not mine,
                                                       whole=text)
                    if own is not None:
                        picked.append((k, own))
            elif mine and spec.get("type") == "string" and not spec.get("enum") \
                    and not spec.get("pattern"):
                picked.append((k, " ".join(w for w in clause.split()
                                           if _fold(w.lower().strip(".,")) not in
                                           {_fold(x) for x in labels[k] | _EDIT_LINK})[:120]))
        if len(picked) == 1 and picked[0][1].strip():
            k, v = picked[0]
            args[k] = v
            dropped = [d for d in dropped if d != k]
            used |= set(words)
    return args, dropped, used


def _row_statement(text: str, pool: list[dict]) -> Optional[dict]:
    """"Leo Park is attending and his meal is chicken.": a sentence that
    names one row of an app with an edit action, and at least one of that
    row's fields in her words, is that edit (no change word needed). The
    one edit action, or None."""
    edits = [a for a in pool if a.get("effect") == "changes"
             and _EDIT_LIKE.match(a.get("says") or "")]
    found = []
    for a in edits:
        rows = _row_keys(a)
        if not rows or _named_row(text, rows) is None:
            continue
        props = (a.get("args") or {}).get("properties") or {}
        filled, _d, _u = _edit_fill(a, text, {}, [], props)
        required = list((a.get("args") or {}).get("required") or [])
        if not all(r in filled for r in required):
            continue
        # "Actually Leo wants the fish instead,
        # and he's on the groom's side." names the row and a field ("side")
        # or one of a field's own choices ("fish"): that is her edit, even
        # when no value could be set from her words yet (she then hears
        # which ones, never the model).
        if any(k not in required for k in filled) or _names_a_field(a, text, props, required):
            found.append(a)
    return found[0] if len(found) == 1 else None


def _names_a_field(action: dict, text: str, props: dict, required: list) -> bool:
    """Her words hold a field's own label ("side", "meal") or one of a
    field's own choices ("fish") of *action*, besides the row's name."""
    return bool(_mentioned_fields(action, text, props, required))


def _mentioned_fields(action: dict, text: str, props: dict, required: list) -> list[str]:
    """The fields of *action* (not its row's name) whose own label or one
    of whose own choices her words hold, in the app's order."""
    words = set(_WORD_RE.findall(re.sub(r"'s\b", " ", _plain(text).lower())))
    out = []
    for k, spec in props.items():
        if k in required:
            continue
        if (_label_words(k, spec) - _FIELD_GENERIC) & words:
            out.append(k)
            continue
        for c in _field_choices(action, k) or ():
            cw = _WORD_RE.findall(str(c).lower())
            if cw and len("".join(cw)) >= 3 and all(w in words for w in cw) \
                    and not set(cw) <= {"yes", "no", "other"}:
                out.append(k)
                break
    return out


# -- reads that answer the question -----------------------------------------------

PAGE = 40
_pages: dict[str, dict[str, Any]] = {}
_MORE_RE = re.compile(
    r"(?ix)^\W*(?:(?:ok|okay|yes|yeah|and|please)\W+)*(?:more|next|keep\s+going|go\s+on|"
    r"continue|the\s+rest|read\s+(?:me\s+)?(?:more|the\s+rest)|next\s+ones?|what\s+else)"
    r"(?:\W+please)?\W*$")
_WHOLE_RE = re.compile(r"(?i)\b(?:whole|all|every|entire|full|everyone|everybody|everything|"
                       r"complete)\b")
_YES_NO_Q = re.compile(r"(?i)^\W*(?:so\s+|and\s+|hey\s+)?(?:is|are|was|were|does|do|did|has|"
                       r"have|can)\b")
_LIST_WORDS = frozenset("list lists checklist log board chart tracker schedule planner sheet "
                        "table calendar roster".split())


def _plural(word: str) -> str:
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"
    if re.search(r"(?:s|x|ch|sh)$", word):
        return word + "es"
    return word + "s"


def list_noun(says: str, n: int) -> str:
    """The list's own noun from its read's words: "Read the guest list" ->
    "guests" (or "guest" for one). "things" when the words do not say."""
    t = _norm(says).rstrip(".")
    t = re.sub(r"^(?:read|show|list|see|get|view|check|look\s+at|tell\s+me)\s+", "", t)
    t = re.sub(r"^(?:me\s+)?(?:all\s+the|the|all|every|your|my|our)\s+", "", t)
    t = re.sub(r"\s+(?:list|lists|entries|items|rows|records)$", "", t)
    words = t.split()
    if not words or len(words) > 2 or not all(re.fullmatch(r"[a-z]{3,20}", w) for w in words) \
            or words[-1] in _LIST_WORDS:
        return "thing" if n == 1 else "things"
    last = _singular(words[-1])
    return " ".join(words[:-1] + [last if n == 1 else _plural(last)])


def _row_fields(row: Any) -> dict[str, str]:
    if not isinstance(row, dict):
        return {}
    return {str(k): str(v) for k, v in row.items()
            if isinstance(v, (str, int, float)) and not isinstance(v, bool) and str(v).strip()}


def _choice_fields(rows: list, key_of) -> dict[str, list[str]]:
    """Fields whose values repeat like an app's choices (2 to 12 of them)."""
    seen: dict[str, dict[str, str]] = {}
    for r in rows:
        key = key_of(r)
        for f, v in _row_fields(r).items():
            if v == key or len(v) > 30:
                continue
            seen.setdefault(f, {})
            seen[f].setdefault(v.strip().lower(), v.strip())
    out = {}
    for f, vals in seen.items():
        if 2 <= len(vals) <= 12 and len(rows) > len(vals):
            out[f] = sorted(vals.values())
    return out


def _said_value(question: str, values: list[str]) -> Optional[str]:
    words = _folded_words(question)
    hits = [v for v in values if (_folded_words(v) and _folded_words(v) <= words)]
    return hits[0] if len(hits) == 1 else None


def _asked_name(question: str, app: str, says: str) -> str:
    """A name she asked about ("Is Leo Park on the guest list?"): the run of
    capitalised words after the first word, not the app's own words."""
    skip = _folded_words(app) | _folded_words(says) | {"i"}
    # The first word is capitalised as any sentence's first word is.
    rest = re.sub(r"^\W*\S+", "", question, count=1)
    for m in re.finditer(r"(?:[A-Z][\w'-]*)(?:\s+[A-Z][\w'-]*){0,3}", rest):
        words = [w for w in m.group(0).split() if _fold(w) not in skip]
        if words:
            return " ".join(words)
    return ""


def _values_of(rows: list) -> list[str]:
    return [v for v in (_clean(_key_value(r)) for r in rows) if v]


def _page_out(device_id: str, values: list[str], app: str, noun) -> str:
    shown = values[:PAGE]
    line = wording.READ_ROWS.format(n=len(values), things=noun(len(values)), app=app,
                                    values=", ".join(shown))
    if len(values) > PAGE:
        with _lock:
            _pages[device_id] = {"values": values, "offset": PAGE, "at": time.monotonic(),
                                 "app": app}
            if len(_pages) > 64:
                for key in list(_pages)[:16]:
                    _pages.pop(key, None)
        line = f"{line} {wording.READ_PAGE_MORE.format(shown=len(shown), n=len(values))}"
    return line


def _next_page(device_id: str) -> Optional[str]:
    with _lock:
        page = _pages.get(device_id or "")
        if page is None or time.monotonic() - page["at"] > PENDING_SECONDS:
            _pages.pop(device_id or "", None)
            return None
        start = page["offset"]
        page["offset"] = start + PAGE
        page["at"] = time.monotonic()
        if page["offset"] >= len(page["values"]):
            _pages.pop(device_id, None)
    chunk = page["values"][start:start + PAGE]
    if not chunk:
        return None
    line = wording.READ_PAGE.format(first=start + 1, last=start + len(chunk),
                                    n=len(page["values"]), app=page["app"],
                                    values=", ".join(chunk))
    if start + PAGE < len(page["values"]):
        line = (f"{line} "
                f"{wording.READ_PAGE_MORE.format(shown=start + len(chunk), n=len(page['values']))}")
    return line


def _attend_value(question: str, values: list[str]) -> Optional[str]:
    """The one value of the app's own choices that means "coming" ("Attending",
    "Yes", "Going"), for a question that asks who is coming; None when the
    question says "no" anywhere or no single value fits."""
    from puffpai.webapps import actions as web_actions
    if web_actions._NEGATION.search(web_actions._choice_form(question)):  # noqa: SLF001
        return None
    fits = [v for v in values if set(_WORD_RE.findall(v.lower())) & _ATTEND_YES
            and not web_actions._NEGATION.search(web_actions._choice_form(v))]  # noqa: SLF001
    return fits[0] if len(fits) == 1 else None


#: A "no" to coming, in an app's own choice words ("Declined",
#: "Not attending", "Regrets"): with the "coming" choice, the choices that
#: mean she has an answer. A short closed set; a choice not matched here is
#: simply not counted as an answer.
_DECLINE_WORDS = frozenset("declined decline declines declining regrets regret cancelled "
                           "canceled absent unable".split())
#: "who hasn't answered yet?": the question is about having an answer.
_ANSWERED_RE = re.compile(r"(?i)\b(?:answer\w*|respon\w*|repl\w*|rsvp\w*|heard|hear)\b")
#: "is Leo off the list?", "is Leo gone?": asked the other way round.
_ABSENT_RE = re.compile(r"(?i)\b(?:off|out|gone|removed|deleted|missing|dropped)\b")


def _schema_choices(action: dict, rows: list) -> dict[str, list[str]]:
    """``{row field: the app's own choices}`` for the rows a read returned,
    from the app's other actions (their recipe's check says which row field
    each choice argument fills). Empty when the app does not say."""
    fields = set()
    for r in rows[:50]:
        if isinstance(r, dict):
            fields |= {str(k) for k in r}
    if not fields:
        return {}
    out: dict[str, list[str]] = {}
    try:
        siblings = [a for a in actions.all_actions()
                    if a.get("integration") == action.get("integration")
                    and a.get("effect") != "reads"]
    except Exception:  # noqa: BLE001 - no actions: nothing to read choices from
        return {}
    for a in siblings:
        recipe = _recipe_of(a)
        check = (recipe or {}).get("check") or {}
        expect = check.get("expect") or {}
        match = check.get("match") or {}
        if not isinstance(expect, dict) or not isinstance(match, dict):
            continue
        if not set(map(str, match)) <= fields:
            continue                    # another list of the same app
        for row_field, prop in expect.items():
            if str(row_field) not in fields or str(row_field) in out:
                continue
            choices = _field_choices(a, str(prop))
            if choices and 2 <= len(choices) <= 30 and not set(
                    c.strip().lower() for c in choices) <= {"yes", "no"}:
                out[str(row_field)] = [str(c) for c in choices]
    return out


def _row_value(row: Any, field: str) -> str:
    return str((_row_fields(row) or {}).get(field) or "").strip()


def _positive(question: str) -> str:
    """*question* with its "not" words taken out ("who isn't coming" -> "who is
    coming"), to find the choice it is about."""
    from puffpai.webapps import actions as web_actions
    return web_actions._NEGATION.sub(" ", web_actions._choice_form(question))  # noqa: SLF001


def _not_value_line(n: int, noun, app: str, value: str, groups: str) -> str:
    if n == 1:
        return wording.READ_NOT_VALUE_ONE.format(thing=noun(1), app=app, value=value,
                                                 groups=groups)
    return wording.READ_NOT_VALUE.format(n=n, things=noun(n), app=app, value=value,
                                         groups=groups)


def _negated(question: str) -> bool:
    from puffpai.webapps import actions as web_actions
    return bool(web_actions._NEGATION.search(web_actions._choice_form(question)))  # noqa: SLF001


def _field_of(value: str, choices: dict[str, list[str]]) -> Optional[str]:
    hits = [f for f, vals in choices.items() if any(v.lower() == value.lower() for v in vals)]
    return hits[0] if len(hits) == 1 else None


def _grouped(rows: list, field: str, skip: set[str], limit: int = PAGE) -> tuple[int, str]:
    """``(n, "Declined: Maya Lee; Awaiting Response: Ann, Bo")``: the rows whose
    *field* is none of *skip*, by the value they do have (or "not set")."""
    groups: dict[str, list[str]] = {}
    n = 0
    for r in rows:
        v = _row_value(r, field)
        if v.lower() in skip:
            continue
        name = _clean(_key_value(r))
        if not name:
            continue
        n += 1
        groups.setdefault(v or "not set", []).append(name)
    parts = []
    shown = 0
    for v, names in groups.items():
        take = names[:max(0, limit - shown)]
        shown += len(take)
        more = len(names) - len(take)
        text = ", ".join(take) + (f", {wording.READ_MORE.format(n=more)}" if more else "")
        parts.append(f"{_clean(v, 30)}: {text}")
    return n, "; ".join(parts)


def read_answer(action: dict, data: Any, question: str = "", device_id: str = "") -> str:
    """The answer to her question from the rows a read returned, every value
    neutralised, never a model's words: the list's own noun, filtered by an
    app choice she named ("who is attending?"), everyone the app does NOT
    mark that way ("who isn't coming?"), the ones with no answer yet ("who
    hasn't answered?"), a yes or no about one row she named (by its first
    name too), or the whole list (in parts of 40) when she asks for all of it.
    The choices are the app's own (from its schema), so a list of two guests
    is answered by its choices as well as a list of two hundred."""
    app = action["app_name"]
    says = action.get("says") or ""
    rows = _rows_of(data)
    if not rows:
        return wording.READ_NONE.format(app=app)

    def noun(n: int) -> str:
        return list_noun(says, n)

    q = str(question or "")
    schema = _schema_choices(action, rows)
    inferred = _choice_fields(rows, _key_value)
    choices = dict(schema)
    for f, vals in inferred.items():
        choices.setdefault(f, vals)
    all_values = sorted({v for vals in choices.values() for v in vals})
    value = _said_value(q, all_values) if q else None
    attend = None
    if q and _ATTEND_RE.search(q):
        attend = _attend_value(_positive(q), all_values) if _negated(q) \
            else _attend_value(q, all_values)
        if value is None and not _negated(q):
            # "who is coming so far?"
            # while the app marks each guest Attending: that choice.
            value = attend
    list_q = bool(q and re.match(r"(?i)^\W*(?:(?:so|and|ok|okay)[\s,]+)?(?:who|whom|whose|how\s+many|"
                                 r"which|what|list|show|tell)\b", q))
    # A yes or no (or any question) about one row she named.
    if q and (_YES_NO_Q.match(q) or not list_q):
        keys = [_key_value(r) for r in rows]
        named = _named_row(q, [k for k in keys if len(_norm(k)) >= 3])
        asked = _asked_name(q, app, says)
        hits = [r for r in rows if named is not None and _key_value(r) == named]
        if not hits and asked and named is None:
            first = _WORD_RE.findall(asked.lower())
            hits = [r for r in rows
                    if first and first[0] in _WORD_RE.findall(_key_value(r).lower())]
        absent = bool(_ABSENT_RE.search(q)) and value is None
        if len(hits) == 1:
            row = hits[0]
            name = _clean(_key_value(row), 60)
            fields = _row_fields(row)
            if value is not None:
                if any(v.strip().lower() == value.lower() for v in fields.values()):
                    return wording.READ_YES_VALUE.format(name=name, value=_clean(value, 30),
                                                         app=app)
                actual = next((fields[f] for f in choices
                               if f in fields and value in choices[f]), "")
                if actual:
                    return wording.READ_NO_VALUE.format(name=name, actual=_clean(actual, 30),
                                                        value=_clean(value, 30), app=app)
            details = [d for d in (_clean(fields[f], 30) for f in choices if f in fields) if d]
            line = wording.READ_STILL_IN if absent else wording.READ_YES
            return line.format(name=name, app=app,
                               details=f" ({', '.join(details)})" if details else "")
        if len(hits) > 1:
            return wording.READ_SEVERAL.format(n=len(hits), things=noun(len(hits)), app=app,
                                               name=_clean(asked or _key_value(hits[0]), 60),
                                               values=", ".join(_values_of(hits[:PAGE])))
        if asked and _YES_NO_Q.match(q):
            line = wording.READ_NOT_IN if absent else wording.READ_NO
            return line.format(name=_clean(asked, 60), app=app)
    # "who isn't coming?", "who is not Attending?": everyone the
    # app does not mark that way, by what it does mark them.
    if q and _negated(q) and list_q:
        target = value or attend
        field = _field_of(target, choices) if target else None
        if target and field:
            n, groups = _grouped(rows, field, {target.lower()})
            if not n:
                return wording.READ_NOT_VALUE_NONE.format(things=noun(2), app=app,
                                                          value=_clean(target, 30))
            return _not_value_line(n, noun, app, _clean(target, 30), groups)
        # "who hasn't answered yet?": not marked coming and not marked as a
        # no (the app's own choice that declines).
        if _ANSWERED_RE.search(q):
            yes = _attend_value("coming", all_values)
            field = _field_of(yes, choices) if yes else None
            if field:
                answers = [yes] + [
                    v for v in choices[field] if v != yes and (
                        set(_WORD_RE.findall(v.lower())) & _DECLINE_WORDS
                        or (_negated(v) and set(_WORD_RE.findall(v.lower())) & _ATTEND_YES))]
                said = {v.lower() for v in answers}
                n, groups = _grouped(rows, field, said)
                if not n:
                    return wording.READ_FILTERED_NONE.format(
                        things=noun(2), app=app, value=_join_words(
                            [c for c in choices[field] if c.lower() not in said], "or"))
                return _not_value_line(n, noun, app,
                                       _join_words([_clean(v, 30) for v in answers], "or"),
                                       groups)
    # Filtered by a choice she named: "who is attending?".
    if value is not None:
        picked = [r for r in rows if any(v.strip().lower() == value.lower()
                                         for v in _row_fields(r).values())]
        if not picked:
            return wording.READ_FILTERED_NONE.format(things=noun(2), app=app,
                                                     value=_clean(value, 30))
        values = _values_of(picked)
        shown = ", ".join(values[:PAGE])
        if len(values) > PAGE:
            shown = f"{shown}, {wording.READ_MORE.format(n=len(values) - PAGE)}"
        if len(picked) == 1:
            return wording.READ_FILTERED_ONE.format(thing=noun(1), app=app,
                                                    value=_clean(value, 30), values=shown)
        return wording.READ_FILTERED.format(n=len(picked), things=noun(len(picked)), app=app,
                                            value=_clean(value, 30), values=shown)
    values = _values_of(rows)
    if q and _WHOLE_RE.search(q):
        return _page_out(device_id or "", values, app, noun)
    head = ", ".join(values[:8])
    line = wording.READ_ROWS.format(n=len(rows), things=noun(len(rows)), app=app,
                                    values=(f"{head}, {wording.READ_MORE.format(n=len(rows) - 8)}"
                                            if len(rows) > 8 else head))
    # "who is coming?" when the app keeps choices she did not name: say
    # which ones it keeps (the field her words are nearest to), so she can
    # ask by them.
    if q and choices and (_ATTEND_RE.search(q) or re.match(r"(?i)^\W*(?:who|how\s+many)\b", q)):
        field = _field_of(attend, choices) if attend else None
        if field is None:
            said_words = _folded_words(q)
            field = max(choices, key=lambda f: (
                len(said_words & _folded_words(" ".join(choices[f]))), f in schema))
        opts = [c for c in (_clean(v, 30) for v in choices[field]) if c][:6]
        if opts:
            line = f"{line} {wording.READ_CHOICES.format(choices=_join_words(opts), first=opts[0])}"
    elif len(rows) > 8:
        line = f"{line} {wording.READ_WHOLE_HINT}"
    return line


def _word_pick(text: str, pool: list[dict]) -> Optional[tuple[str, dict]]:
    """Without a tool-capable brain: one read with no required argument whose
    words the message holds exactly, or nothing."""
    if not _READ_RE.search(text) or _CHANGE_RE.search(text):
        return None
    hits = [a for a in pool if a["effect"] == "reads"
            and not (a.get("args") or {}).get("required") and _score(text, a["says"]) >= 3]
    if len(hits) != 1:
        return None
    return hits[0]["id"], {}


def _ask_remove(action: dict, args: dict, device_id: str) -> dict:
    from puffpai.security import consent_tickets
    app = action["app_name"]
    target_fn = getattr(consent_tickets, "target_action_yes", None)
    if target_fn is None or "action_yes" not in getattr(consent_tickets, "ACTIONS", ()):
        return _reply(wording.NOT_IN_BUILD)
    props = (action.get("args") or {}).get("properties") or {}
    what = next((str(args[r]) for r in (action.get("args") or {}).get("required") or []
                 if r in args and props.get(r, {}).get("type") == "string"), "")
    what = _clean(what, 80) or action["says"].rstrip(".").lower()
    sentence = wording.CONFIRM_REMOVE.format(what=what, app=app)
    try:
        ticket = consent_tickets.mint("action_yes", target_fn(action["id"], args),
                                      sentence, device_id)
    except consent_tickets.TicketRefused as exc:
        return _reply(exc.message)
    _set_pending(device_id, {"action_id": action["id"], "args": dict(args),
                             "ticket": ticket, "at": time.monotonic(),
                             "app": app, "says": action["says"]})
    return _reply(sentence, {"kind": "remove", "action": action["id"],
                             "says": action["says"], "app": app})


# -- honesty: no reply claims an app action that did not run ---------------------
#
# Every app action is answered HERE (or by the declarative-list fastpath)
# before the model is asked, from the run's own evidence. So a model answer
# never follows an app action in the same turn, and any sentence in it that
# says a thing was added, removed or changed in a list or app is not true.
# The backend runs every model answer through :func:`scrub_app_claims`
# (streamed ones through :func:`guard_app_claims`).

_APP_DEED = (
    r"add(?:ed|ing)?|put(?:ting)?|plac(?:e|ed|ing)|remov(?:e|ed|ing)|delet(?:e|ed|ing)|"
    r"took|tak(?:e|en|ing)\s+(?:\w+\s+){0,3}off|updat(?:e|ed|ing)|chang(?:e|ed|ing)|"
    r"mark(?:ed|ing)?|sav(?:e|ed|ing)|record(?:ed|ing)?|log(?:ged|ging)?|"
    r"wr(?:ote|itten|ite|iting)(?:\s+down)?|mov(?:e|ed|ing)|cross(?:ed|ing)?\s+(?:\w+\s+){0,3}off|"
    r"includ(?:e|ed|ing)|insert(?:ed|ing)?|cancel(?:l?ed|l?ing)?|book(?:ed|ing)?|"
    r"creat(?:e|ed|ing)|made|mak(?:e|ing)|enter(?:ed|ing)?|jot(?:ted|ting)?(?:\s+down)?|"
    r"not(?:e|ed|ing)(?:\s+down)?|check(?:ed|ing)?\s+(?:\w+\s+){0,3}off|"
    r"tick(?:ed|ing)?\s+(?:\w+\s+){0,3}off|scratch(?:ed|ing)?\s+(?:\w+\s+){0,3}off|"
    r"clear(?:ed|ing)?|set(?:ting)?\s+up|updat(?:e|ed)")
_THINGS = (r"(?:list|lists|app|apps|charts?|tracker|planner|log|board|checklist|sheet|table|"
           r"calendar|schedule|budget|notes?|records?|inventory|collection|library|"
           r"journal|diary|registry|timeline|tab)")
_PLACE = (r"\b(?:to|on|onto|into|in|from|off|under)\s+(?:the|your|my|our|his|her|"
          r"their|this|that)\b[^.!?\n]{0,60}\b" + _THINGS + r"\b")
#: The list or app itself as the thing changed: "updated your budget sheet".
_DIRECT = r"\s+(?:the|your|my|our)\s+(?:[\w'-]+\s+){0,3}" + _THINGS + r"\b"
_FIRST_PERSON = re.compile(
    r"(?ix)\bI\s*(?:'ve|\s+have(?!\s+not\b)(?!n't)|'ll|\s+will|\s+shall|'m|\s+am(?!\s+not\b)|"
    r"\s+did|\s+just|\s+already|\s+now)?\s*(?:just\s+|now\s+|already\s+|also\s+|"
    r"gone\s+ahead\s+and\s+|went\s+ahead\s+and\s+|successfully\s+)?(?:" + _APP_DEED + r")\b"
    r"(?:" + _DIRECT + r"|[^.!?\n]{0,120}?" + _PLACE + r")")
_PASSIVE = re.compile(
    r"(?ix)\b(?:has|have|had)\s+(?:now\s+|just\s+|also\s+)?been\s+(?:successfully\s+)?"
    r"(?:added|put|placed|removed|deleted|taken\s+off|updated|changed|marked|saved|"
    r"recorded|logged|written\s+down|moved|included|inserted|cancell?ed|booked|created|"
    r"entered|noted|crossed\s+off|checked\s+off|ticked\s+off|cleared)\b"
    r"|\b(?:is|are)\s+now\s+(?:on|in|off|part\s+of|listed|marked|saved|recorded)\b"
    r"[^.!?\n]{0,60}\b(?:list|app|tracker|planner|log|checklist|budget|calendar|schedule)\b")
_LEAD_DEED = re.compile(
    r"(?ix)^\s*(?:done[!.,:]?\s+|all\s+set[!.,:]?\s+|great[!.,:]?\s+|okay[!.,:]?\s+|ok[!.,:]?\s+|"
    r"sure[!.,:]?\s+)?(?:added|removed|deleted|updated|saved|logged|marked|put|recorded|"
    r"moved|cancell?ed|booked|created|entered|noted|cleared|crossed\s+off|checked\s+off)\b"
    r"(?:" + _DIRECT + r"|[^.!?\n]{0,120}?" + _PLACE + r")")
_NOT_A_CLAIM = re.compile(
    r"(?ix)\b(?:can(?:'|no)?t|cannot|could\s+not|couldn't|won'?t|will\s+not|do\s+not|"
    r"don'?t|does\s+not|doesn'?t|did\s+not|didn'?t|have\s+not|haven'?t|has\s+not|"
    r"hasn'?t|am\s+not|unable|not\s+able|if\s+you|would\s+you|shall\s+i|should\s+i|"
    r"do\s+you\s+want|want\s+me\s+to|like\s+me\s+to|how\s+about|what\s+about)\b")
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]*\n?|\n")

# -- after an ask to change a list or an app --------------------------------------
#
# The rules above need a list or app noun ("to the guest list") because they
# also run on answers to ordinary questions, where "I've added some ideas" is
# true. When the owner's own message asked for a change to her things ("mark
# the oil change as done", "remove Aunt May"), no app action ran (a real one
# is answered before the model, from its own evidence), so ANY sentence where
# the bot says it did, is doing or will do that kind of change is untrue,
# whatever noun follows: "I've marked the oil change as done", "I'll remove
# Aunt May for you", "Leo Park's been added", "Leo Park is on your guest list
# now", "Leo Park added to the guest list", a bare "Done!".

#: A change to the owner's things, as she asks for it.
_ASK_DEED = re.compile(
    r"(?ix)\b(?:add|put|place|remove|delete|erase|drop|mark|log|record|save|update|"
    r"change|edit|rename|move|cancel|book|schedule|track|include|insert|enter|"
    r"invite|uninvite|clear|empty|wipe|tick|cross|scratch|check\s+(?:\S+\s+){0,4}?off|"
    r"take\s+(?:\S+\s+){0,4}?(?:off|out)|write\s+(?:\S+\s+){0,4}?down|jot|"
    r"note\s+(?:\S+\s+){0,3}?down|sign\s+(?:\S+\s+){0,3}?up|set\s+(?:\S+\s+){0,4}?(?:as|to)|"
    r"done|finished|completed|rsvp'?d?)\b")
#: The same changes, as the bot might claim them (any tense).
_DEED_WORDS = (
    r"add(?:s|ed|ing)?|put(?:s|ting)?|plac(?:e|es|ed|ing)|remov(?:e|es|ed|ing)|"
    r"delet(?:e|es|ed|ing)|eras(?:e|es|ed|ing)|drop(?:s|ped|ping)?|mark(?:s|ed|ing)?|"
    r"log(?:s|ged|ging)?|record(?:s|ed|ing)?|sav(?:e|es|ed|ing)|updat(?:e|es|ed|ing)|"
    r"chang(?:e|es|ed|ing)|edit(?:s|ed|ing)?|renam(?:e|es|ed|ing)|mov(?:e|es|ed|ing)|"
    r"cancel(?:s|l?ed|l?ing)?|book(?:s|ed|ing)?|schedul(?:e|es|ed|ing)|track(?:s|ed|ing)?|"
    r"includ(?:e|es|ed|ing)|insert(?:s|ed|ing)?|enter(?:s|ed|ing)?|invit(?:e|es|ed|ing)|"
    r"uninvit(?:e|es|ed|ing)|clear(?:s|ed|ing)?|empt(?:y|ies|ied|ying)|wip(?:e|es|ed|ing)|"
    r"tick(?:s|ed|ing)?|cross(?:es|ed|ing)?|scratch(?:es|ed|ing)?|"
    r"check(?:s|ed|ing)?\s+(?:\S+\s+){0,4}?off|"
    r"t(?:ake|akes|ook|aken|aking)\s+(?:\S+\s+){0,4}?(?:off|out)|"
    r"wr(?:ite|ites|ote|itten|iting)\s+(?:\S+\s+){0,4}?down|jot(?:s|ted|ting)?|"
    r"not(?:e|es|ed|ing)\s+(?:\S+\s+){0,3}?down|sign(?:s|ed|ing)?\s+(?:\S+\s+){0,3}?up|"
    r"set(?:s|ting)?\s+(?:\S+\s+){0,4}?(?:as|to)|ma(?:ke|kes|de|king)\s+sure|"
    r"g(?:et|ot|ets|etting)\s+(?:\S+\s+){0,3}?(?:added|removed|marked|logged|on|off)|"
    r"t(?:ake|aken|aking|ook)\s+care\s+of|handl(?:e|ed|ing)")
_PARTICIPLES = (
    r"added|put|placed|removed|deleted|erased|dropped|marked|logged|recorded|saved|"
    r"updated|changed|edited|renamed|moved|cancell?ed|booked|scheduled|tracked|included|"
    r"inserted|entered|invited|uninvited|cleared|emptied|wiped|ticked(?:\s+off)?|"
    r"crossed(?:\s+off|\s+out)?|scratched(?:\s+off)?|checked\s+off|taken\s+(?:off|out)|"
    r"written\s+down|jotted(?:\s+down)?|noted(?:\s+down)?|signed\s+up|listed|done|"
    r"taken\s+care\s+of|handled|all\s+set|set\s+(?:as|to)")
_CTX_FIRST_PERSON = re.compile(
    r"(?ix)\b(?:I|we|let'?s|let\s+us|let\s+me)(?:'ve|'ll|'m|'re)?"
    r"(?:\s+(?:have|has|had|will|shall|am|are|did|do|just|already|now|also|then|"
    r"successfully|quickly|gone|went|go|going|gonna|ahead|and|to|been|straight|right|"
    r"away|certainly|definitely))*"
    r"\s+(?:" + _DEED_WORDS + r")\b")
_CTX_STATE = re.compile(
    r"(?ix)(?:\b(?:has|have|had)|'s|'ve)\s+(?:now\s+|just\s+|also\s+|already\s+)?been\s+"
    r"(?:successfully\s+)?(?:" + _PARTICIPLES + r")\b"
    r"|(?:\b(?:is|are|was|were)|'s|'re)\s+(?:now\s+|all\s+|already\s+|officially\s+)?"
    r"(?:(?:" + _PARTICIPLES + r")\b|(?:on|in|off|onto|into|out\s+of)\s+"
    r"(?:the|your|my|our|his|her|their)\b)")
_CTX_BARE = re.compile(
    r"(?ix)^\s*(?!you\b|if\b|when\b|once\b|before\b|after\b|how\b|what\b|why\b)"
    r"(?:[\w'-]+,?\s+){0,5}?(?:" + _PARTICIPLES + r")\s+(?:to|from|as|on|onto|into|off|in|for)\b")
#: "Sure thing, mark the oil change as done!": reads as a yes that it is done.
_CTX_YES_DEED = re.compile(
    r"(?ix)^\s*(?:of\s+course|sure(?:\s+thing)?|okay|ok|got\s+it|will\s+do|no\s+problem|"
    r"absolutely|right\s+away|certainly|alright|all\s+right|you\s+got\s+it|done)"
    r"\s*[,:;-]\s*(?:" + _DEED_WORDS + r")\b")
_CTX_DONE = re.compile(
    r"(?ix)^\s*(?:(?:all\s+)?done|all\s+set|finished|there\s+you\s+go|you'?re\s+all\s+set|"
    r"that'?s\s+done|it'?s\s+done|consider\s+it\s+done)\s*[!.]*\s*$")
#: A sentence about the answer itself, not about her things.
_ABOUT_THE_REPLY = re.compile(
    r"(?ix)\b(?:below|above|in\s+(?:this|my)\s+(?:reply|answer|message)|"
    r"to\s+this\s+(?:reply|answer)|here(?:'s|\s+is|\s+are)\s+(?:a|an|some|the\s+(?:idea|ideas|"
    r"steps|recipe|plan|summary|list\s+of\s+ideas)|my|what|how)\b)")
#: A short line that only says it happened: "Added!", "Noted.", "Leo Park: added.",
#: "Done and done!", "Consider Aunt May removed.", "Voila, basil is there."
_CTX_DONE_MORE = re.compile(
    r"(?ix)^\s*(?:[\w' -]{1,40}:\s*)?(?:(?:" + _PARTICIPLES + r"|noted|sorted|done\s+and\s+done|"
    r"voila|there\s+we\s+go|you'?re\s+good(?:\s+to\s+go)?)\b[^.!?\n]{0,40}|"
    r"consider\s+(?:\S+\s+){1,5}?(?:" + _PARTICIPLES + r"))\s*[!.]*\s*$")
#: Her things said to be in a new state: "Your guest list now includes Leo Park",
#: "Aunt May is no longer on the guest list", "Leo Park has joined the guest list",
#: "Here's your updated guest list", "Okay, Leo Park is in!".
_CTX_NEW_STATE = re.compile(
    r"(?ix)\bnow\s+(?:includes?|has|have|contains?|lists?|shows?|reads?)\b"
    r"|\bno\s+longer\s+(?:on|in|part\s+of|listed|invited|coming|there)\b"
    r"|\b(?:has|have)\s+(?:now\s+)?joined\s+(?:the|your|my|our)\b"
    r"|\b(?:updated|new|current)\s+(?:[\w'-]+\s+){0,3}" + _THINGS + r"\b"
    r"|\b(?:is|are|'s|'re)\s+(?:now\s+|all\s+)?(?:in|on|off|out|there|down|booked|set)\s*[!.]*\s*$")

# -- the owner's ask: a change to her things ---------------------------------------
#
# Reading the model's reply for claims can never be complete: a model says a
# change happened in endless shapes ("Basil added to your Garden list",
# "Wonderwall is on for the show!", "Budget updated! +$40"). The owner's own
# message is the bounded input, so the decision is made there. When it asks
# for a change to her things and the turn reaches the model, nothing ran:
# every real app action is answered before the model from its own evidence,
# and the model is given no tools. So the backend does not ask the model at
# all (:func:`change_turn`), and a reply to such an ask that does reach this
# guard is replaced whole. The sentence rules above stay as the second net
# for every other turn.

_CLAUSE_SPLIT = re.compile(r"[,;.!?\n]+|\s+(?:and\s+then|then|and\s+also)\s+", re.I)
_LEAD_IN = re.compile(
    r"(?ix)^(?:(?:please|pls|kindly|ok|okay|alright|so|and|also|then|now|just|oh|yes|"
    r"yeah|yep|sure|hey|hi|quickly|go\s+ahead\s+and|let'?s|let\s+us|can\s+you|could\s+you|would\s+you|"
    r"will\s+you|can\s+u|could\s+u|i\s+need\s+you\s+to|i\s+want\s+you\s+to|"
    r"i'?d\s+like\s+you\s+to|you\s+can|you\s+should)\s+)+")
#: A verb that is a change to a record on its own ("add Leo", "mark it").
_STRONG_VERB = re.compile(
    r"(?ix)^(?:add|put(?!\s+(?:on|together|simply|differently|forward|up\s+with|"
    r"another\s+way|yourself|myself|ourselves|me|us|your\s+feet|my\s+feet|"
    r"it\s+(?:simply|another\s+way|differently))\b)|"
    # "file this under Vet visits", "make a note in the Pet Log"
    r"file(?=\s+(?:\S+\s+){0,5}?(?:under|in|into|away|with)\b)|make\s+a\s+note\b|"
    r"undo|revert|"
    r"remove(?!\s+(?:\S+\s+){0,3}?(?:stains?|rust|mou?ld|mildew|grease|wax|paint|odou?rs?|"
    r"smells?|scratch(?:es)?|ink|gum|dents?|hair|wrinkles|makeup|nail\s+polish|tarnish|"
    r"limescale|splinters?|ticks?|blood|sweat|glue|stickers?|residue)\b)|delete|erase|"
    r"uninvite|invite|rsvp|"
    # "mark this moment with a poem", "clear my head",
    # "remove the stain from my shirt" are not records.
    r"mark(?!\s+(?:my\s+words|(?:this|the|that|my|our|your)\s+(?:moment|occasion|milestone|"
    r"anniversary|words|day|date|week|year|event|time)\b))|"
    # "tick it", "check that", "note in the Garden Log: ..."
    r"(?:tick|untick|uncheck|star|unstar|flag)\s+(?:it|that|this|them|him|her)\s*$|"
    r"note\s+(?=(?:in|on|under|to|into)\s+\S)|"
    r"log(?!\s+(?:in|on|out|into|onto|off)\b)|record|save|"
    r"clear(?!\s+(?:up|the\s+air|(?:my|your|our|his|her|the)\s+(?:head|mind|throat|thoughts|"
    r"skin|sinuses|nose|air|conscience|name)\b))|empty|wipe|include|"
    r"scratch(?!\s+(?:that|this|it|what|my\s+last|the\s+last|everything)\b)|"
    r"(?:check|tick)\s+(?:\S+\s+){0,5}?off|(?:cross|scratch|strike)\s+(?:\S+\s+){0,5}?(?:off|out)|"
    r"take\s+(?:\S+\s+){1,5}?(?:off|out)|(?:write|jot|note)\s+(?:\S+\s+){0,5}?down|jot|"
    r"note\s+(?:it|that|this|him|her|them)\s*$|make\s+a\s+note\s+of\s+(?:it|that|this|him|her|them)|"
    r"sign\s+(?:\S+\s+){0,4}?up|"
    # "note that Leo is coming", "record that Dune got 5 stars".
    r"(?:note|record|log|jot\s+down|write\s+down|mark\s+down)\s+that\s+\S|"
    r"get\s+rid\s+of)\b")
#: A verb that is a change to a record only when her things are named.
_SOFT_VERB = re.compile(
    r"(?ix)^(?:place|insert|enter|drop|move|track|schedule|book|reschedule|"
    r"cancel|change|edit|update|rename|set|swap|replace|switch|reset)\b")
#: Words that name one of her things (a list, an app, a record).
_THING_WORD = re.compile(
    r"(?ix)\b(?:lists?|apps?|tracker|planner|board|checklist|sheet|table|calendar|"
    r"schedule|budget|inventory|collection|library|journal|diary|registry|timeline|"
    r"guests?|setlist|playlist|roster|logbook|records?|chores?|tasks?|to-?dos?|entr(?:y|ies)|"
    r"shelf|shelves|rsvps?|headcount|seating|charts?|groceries|shopping|queue)\b")
#: The object is text in this chat, not a record: "add a summary", "remove the jargon".
_CONTENT_WORD = re.compile(
    r"(?ix)\b(?:summary|summaries|details?|examples?|jokes?|emojis?|tips?|ideas?|"
    r"paragraphs?|sentences?|lines?|words?|sections?|titles?|headings?|headlines?|"
    r"conclusion|intro|introduction|explanations?|context|sources?|citations?|"
    r"references?|colou?rs?|humou?r|jargon|formatting|bullets?|commas?|typos?|rhymes?|"
    r"verses?|chorus|stanzas?|steps?|more)\b")
_TEXT_THING = re.compile(
    r"(?ix)\b(?:this|that|the|your|my)\s+(?:answer|reply|response|message|text|poem|"
    r"story|essay|email|e-mail|letter|draft|code|script|function|post|tweet|caption|bio|"
    r"speech|toast|lyrics|translation|haiku|limerick|sonnet|song|rap|riddle|joke|drawing|"
    r"picture|photo|paragraph|sentence)\b")
#: Text pasted into the ask ("fix this list: a, b, c"): it is text work.
_PASTED = re.compile(r"(?s)(?:[:\n]\s*\S.{15,})|[\"“][^\"”]{20,}[\"”]")
_PASTED_THING = re.compile(
    r"(?ix)\b(?:this|these|those|the\s+following)\s+(?:list|items|names|words|lines|"
    r"numbers|things)\b")
_MATH = re.compile(
    r"(?ix)^(?:add|take)\s+[-+$£€\d.,]+\s*%?\s*(?:and|plus|\+|to|from|away\s+from)"
    r"\s+[-+$£€\d.,]+\s*%?\s*$")
#: What a record is put on or taken off: "on the guest list", "in for Big Day".
_PLACE_PREP = (r"(?:on|onto|in|into|off|off\s+of|out\s+of|down\s+(?:on|for|in)|in\s+for|"
               r"on\s+for|to|from|for)\s+")
#: The thing moved: a few words, not help or ideas ("I need help on the budget"),
#: not an infinitive ("I want to stick to the budget").
_PLACE_OBJ = (r"(?!(?:to|with|on|in|up|out|off|into|onto|at|some|more|help|advice|ideas?|"
              r"tips?|feedback|input|thoughts?|info|information|suggestions?|guidance|"
              r"a\s+hand|an\s+eye|tabs|track|started|going|working|busy|ready|back|"
              r"hold|ahead|a\s+look|a\s+go|a\s+break|rid)\b)(?:[\w'$.,-]+\s+){1,5}?")
#: "I need Leo Park on the guest list", "we'd like basil in Plantings".
_WANT_PLACE = re.compile(
    r"(?ix)^(?:i|we)(?:'d|\s+would|\s+do|\s+really|\s+also|\s+still)*\s+"
    r"(?:need|want|like|wanna)\s+" + _PLACE_OBJ + _PLACE_PREP)
#: "stick Leo on the guest list", "count Leo Park in for Big Day", "pencil Leo in".
_PLACE_VERB = re.compile(
    r"(?ix)^(?:stick|pop|slot|squeeze|pencil|chuck|shove|throw|get|have|keep|bump|count|"
    r"bring|slip|fit|stuff|plonk|toss|pin)\s+" + _PLACE_OBJ
    + r"(?:" + _PLACE_PREP + r"|(?:in|out|down|up|on|off)\b)")
#: Participles a request puts a record into ("Leo Park should be invited").
_ASK_PARTICIPLE = (r"(?:added|removed|invited|uninvited|included|deleted|erased|crossed\s+off|"
                   r"taken\s+off|taken\s+out|ticked\s+off|checked\s+off|scratched|marked|"
                   r"logged|recorded|listed|put\s+(?:on|in|down)|signed\s+up|booked|"
                   r"cancell?ed|saved)")
#: "Leo Park should be invited", "can Leo Park be on the list?", "Leo needs adding".
_SHOULD_BE = re.compile(
    r"(?ix)^(?:(?:[\w'$.-]+\s+){1,5}?(?:should|must|needs?\s+to|has\s+to|have\s+to|"
    r"ought\s+to|is\s+to|are\s+to)\s+be\s+" + _ASK_PARTICIPLE + r"\b"
    r"|(?:[\w'$.-]+\s+){1,5}?needs?\s+(?:adding|removing|inviting|crossing\s+off|"
    r"taking\s+off|marking|logging)\b"
    r"|(?:can|could|should|may|will|would)\s+(?:[\w'$.-]+\s+){1,5}?be\s+"
    r"(?:" + _ASK_PARTICIPLE + r"|on|in|off)\b"
    # "Leo Park should be on the guest list", "Mia needs to be on
    # the homework chart".
    r"|(?:[\w'$.-]+\s+){1,5}?(?:should|must|needs?\s+to|has\s+to|have\s+to|ought\s+to|"
    r"is\s+to|are\s+to|gotta|oughta)\s+(?:be|go|get)\s+(?:on|in|onto|into|off|out\s+of|"
    r"added\s+to|put\s+on)\s+(?:the|my|your|our)\s+(?:[\w'-]+\s+){0,3}?" + _THINGS + r"\b)")
#: The modal ask "can Leo Park be on ..." (not "the dishes need crossing off").
_MODAL_FIRST = re.compile(r"(?i)^(?:can|could|should|may|will|would)\b")
#: A "can ... be on/in/off" ask with no list named: not a change on its own.
_PLACE_END = re.compile(r"\b(?:on|in|off)\s*$")

# A record verb with a thing and
# a target ("assign laundry to Sam", "bump Dune to next month", "rename laundry
# to wash clothes", "set the dishes to Sam", "change Dune's rating to 5", "swap
# Dune for Emma") and "Dune goes on the reading list" are change asks when she
# has apps, though no list is named.

#: Verbs that only ever change a record when they take a thing and a target.
_RECORD_VERB = re.compile(
    r"(?ix)^(?:assign|reassign|unassign|bump|rename|reschedule|postpone|re-?rate|rate|"
    r"re-?book|re-?add|re-?list|re-?log|re-?enter|re-?tag|re-?date|re-?slot|re-?file|"
    r"re-?order|reorder|re-?name)\s+(?P<obj>(?:[\w'$.-]+\s+){1,5}?)"
    r"(?:to|for|with|as|into|onto|over\s+to|back\s+to|up\s+to|down\s+to|until|till|at)\s+\S")
#: Everyday verbs that change a record only with a target that reads like
#: one: a day or date, a person's name, a number, or a thing's own detail
#: ("Dune's rating").
_EVERYDAY_VERB = re.compile(
    r"(?x)^(?:swap|switch|replace|set|move|change|shift|push|update|edit|give|hand)\s+"
    r"(?P<obj>(?:[\w'$.-]+\s+){1,5}?)(?:to|for|with|as|into|onto|over\s+to|back\s+to|"
    r"up\s+to|down\s+to)\s+(?P<target>\S.*)$", re.I)
_TARGET_LIKE = re.compile(
    r"(?x)^(?:(?:next|this|last)\s+(?:week|month|year|weekend|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday)|tomorrow|today|tonight|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|"
    r"oct|nov|dec)[a-z]*\b|[-+$£€]?\d)", re.I)
#: The object or target is a setting, a device or text, not one of her records.
_NOT_RECORD = re.compile(
    r"(?ix)\b(?:lights?|lamps?|thermostat|temperature|heat(?:ing|er)?|a/?c|air|fan|volume|"
    r"music|songs?|radio|station|tv|television|timer|alarm|reminder|brightness|colou?r|"
    r"scene|mode|voice|tone|style|language|subject|topic|format|font|theme|recipe|units?|"
    r"celsius|fahrenheit|time\s*zone|wallpaper|screen|speakers?|input|channel|chat|"
    r"conversation|file|document|folder|values?|variables?|functions?|class|type|keys?|"
    r"port|ip|address|password|oven|stove|microwave|washer|dryer|dishwasher|fridge|"
    r"freezer|blinds|shades|curtains|locks?|garage|car|(?:my|your|our)\s+(?:nick)?name)\b")
#: An object that is only a pronoun or a particle ("set it to 5", "move on to").
_PRONOUN_OBJ = re.compile(r"(?i)^(?:it|that|this|these|those|me|us|you|them|him|her|on|"
                          r"up|off|out|back|down|over|away|everything|something)\b")
_POSSESSIVE = re.compile(r"\b[\w-]+'s\b")
#: "Dune goes on the reading list", "the florist belongs in the budget".
_GOES_PLACE = re.compile(
    r"(?ix)^(?!(?:what|which|who|whom|where|how|when|why|whatever|anything|something|"
    r"nothing|everything|does|do|did|is|are)\b)"
    r"(?:[\w'$.-]+\s+){1,5}?(?:goes|go|belongs?|should\s+go|can\s+go|will\s+go|"
    r"is\s+going|are\s+going|needs\s+to\s+go|has\s+to\s+go)\s+(?:on|in|onto|into|to|under)\s+")


#: An object that is a new thing, not an existing record: "a goal", "some ideas".
_NEW_THING = re.compile(r"(?i)^(?:a|an|some|another|any|one|new)\s")

# A command whose verb no list has ("kick Leo off the guest list",
# "star Dune", "nix Leo", "seat Leo at table 4", "enroll Sam in chores",
# "shelve Dune", "make Leo Park a guest"). Verbs are an open class, so this
# fails closed: ANY command aimed at her thing (a name she wrote with a
# capital, one of her list or app names, a list word) is read as a change
# ask, unless the verb is one of the everyday ones below, which still reach
# the model. A verb missing from this list gets the honest line ("I have not
# changed anything in your apps"), never a made-up change.

#: Everyday commands that take a person or a title and change no record.
_EVERYDAY_COMMAND = frozenset("""
tell write ask call text email message phone ring ping remind wish thank greet congratulate
compare translate explain describe define summarize summarise show list find search look
help teach recommend suggest play sing read say spell quote imagine pretend plan brainstorm
give hand hug kiss love miss meet visit see watch hear listen drive walk pick feed bring
take wake cook bake buy order get let keep make create check try stop start pause resume
skip repeat open close turn dim brighten warm cool heat run review praise celebrate
welcome introduce name pronounce draw paint sketch design research outline rewrite fix
proofread edit convert calculate estimate guess quiz test cheer comfort surprise thank
wake congratulate forgive trust follow like share do go come unlock arm disarm mute
unmute shuffle stream cast locate charge vacuum mop clean google dial wash water
rank
""".split())
_PREPOSITIONS = frozenset(
    "to on onto in into at for with from about up down over off out by through around "
    "away back along across".split())
#: "get started on", "have a look at": no thing is put anywhere.
_PLACE_OBJ_EXCLUDED = re.compile(
    r"(?i)(?:started|going|working|busy|ready|back|rid|a\s+look|a\s+go|a\s+break|"
    r"help|some|more|ideas?|tips?|advice|an\s+eye|tabs|track)\b")
#: Words a command never starts with.
_NOT_A_COMMAND = frozenset("""
i i'm im i've i'd i'll we we're we've you you're he she they it it's this that these those
there here the a an my your our his her their its what who why when where how which whose
is are was were do does did have has had can could would should will shall may might must
if so and but or not no yes yeah yep ok okay hi hey hello thanks thank please just also
then now today tonight tomorrow yesterday oh wow well sorry great good nice cool maybe
""".split())
#: A word right after a name that makes the name the subject: "Leo Park is coming".
_SUBJECT_VERB = re.compile(
    r"(?i)^(?:is|are|was|were|'s|has|have|had|will|would|can|could|should|must|may|might|"
    r"did|does|do|got|gets|needs?|wants?|says?|said|thinks?|loves?|likes?|won'?t|can'?t|"
    r"isn'?t|wasn'?t|didn'?t|doesn'?t|[a-z]+ed|[a-z]+'d|[a-z]{3,}s)$")
_CALENDAR_WORDS = frozenset(
    "i monday tuesday wednesday thursday friday saturday sunday january february march april "
    "may june july august september october november december christmas easter".split())


def _command_on_thing(raw: str, names: Any) -> bool:
    """*raw* (one clause, lead-in words gone, her capitals kept) is a command
    aimed at one of her things with a verb no other rule reads."""
    words = raw.split()
    if len(words) < 2:
        return False
    # This reads English commands with an unknown verb; a clause in
    # another language ("Wie ist das Wetter?") is not one (its own change
    # asks are read by _ASK_ELSEWHERE).
    if not grounding.english(raw):
        return False
    verb = words[0].lower().strip(",.!:;")
    low = raw.lower()
    if _SUBJECT_VERB.match(words[1].strip(",.!:;").lower()):
        return False  # "practice moved to thursday": a thing, then its verb
    rest = " ".join(words[1:])
    # "make Leo Park a guest", "get Leo onto the guest list": an everyday verb
    # that puts her thing into a state, when a list word or her name says so.
    if (verb in ("make", "get", "have", "let") and not _NEW_THING.match(rest + " ")
            and not _PLACE_OBJ_EXCLUDED.match(rest)
            and (_THING_WORD.search(" ".join(words[2:]))
                 or _names_hit_folded(" ".join(words[2:]), names))):
        return True
    # "draft a toast for Big Day": a new thing, made here.
    if _NEW_THING.match(rest + " ") and verb not in ("make", "get", "have", "let"):
        return False
    if (not re.fullmatch(r"[a-z]{3,}", verb) or verb in _NOT_A_COMMAND
            or verb in _EVERYDAY_COMMAND or verb.endswith(("ing", "ly"))
            or _SOFT_VERB.match(low) or _RECORD_VERB.match(low) or _EVERYDAY_VERB.match(low)):
        return False
    if words[1].lower() in _PREPOSITIONS:
        return False  # "switch to Spanish": no thing is acted on
    # "Leo Park is coming", "Mom called": a name, then its verb.
    i = 0
    while (i < len(words) and words[i][:1].isupper()
           and not (i and _SUBJECT_VERB.match(words[i].strip(",.!:;").lower()))):
        i += 1
    if i >= 1 and words[0][:1].isupper() and i < len(words) \
            and _SUBJECT_VERB.match(words[i].strip(",.!:;")):
        return False
    if verb.endswith("ed") and not words[0][:1].isupper():
        return False  # "finished Dune": a past deed, said as a statement
    obj = " ".join(words[1:7])
    if _NOT_RECORD.search(obj) or _TEXT_THING.search(obj) or _CONTENT_WORD.search(obj):
        return False
    proper = any(w[:1].isupper() and w.strip(",.!:;'").lower() not in _CALENDAR_WORDS
                 for w in words[1:5])
    # "slide taco night to Wednesday": a thing moved to a day, when she has apps.
    moved = bool(names) and not _NEW_THING.match(rest + " ") and re.search(
        r"(?i)\s(?:to|until|till|back\s+to)\s+(?!me\b|us\b|you\b)", " " + obj) is not None \
        and _TARGET_LIKE.match(re.split(r"(?i)\s(?:to|until|till|back\s+to)\s+", " " + obj,
                                        maxsplit=1)[-1].strip())
    return bool(proper or moved or _names_hit_folded(obj, names) or _THING_WORD.search(obj))


def _record_target(clause: str, names: Any) -> bool:
    """*clause* (lead-in words gone, her own capitals kept) is a record verb
    with a thing and a target. Only when she has apps (*names*)."""
    if _NOT_RECORD.search(clause):
        return False
    if not names:
        # Keyless too: "bump the vet visit to Thursday",
        # "rename Movie Night to Film Friday" are record changes whoever asks.
        m = _RECORD_VERB.match(clause)
        if not m:
            # "move soccer practice to Thursday", "set Leo's meal to fish": an
            # everyday verb whose target is a day or date, or whose thing is
            # someone's own detail.
            e = _EVERYDAY_VERB.match(clause)
            if not e or _PRONOUN_OBJ.match(e.group("obj")) or _NEW_THING.match(e.group("obj")) \
                    or _TEXT_THING.search(clause) or _CONTENT_WORD.search(e.group("obj")):
                return False
            target = e.group("target").strip()
            return bool(_TARGET_LIKE.match(target) and not re.match(r"[-+$£€]?\d", target)
                        or re.search(r"\b[A-Z][a-z]+'s\b", e.group("obj"))
                        or re.match(r"[A-Z][a-z]", e.group("obj")))
        if _PRONOUN_OBJ.match(m.group("obj")) or _NEW_THING.match(m.group("obj")) \
                or _TEXT_THING.search(clause) or _CONTENT_WORD.search(m.group("obj")):
            return False
        return not re.match(r"(?i)\s*(?:to|for|with|as)\s+(?:me|us|myself|you)\b",
                            clause[m.end("obj"):])
    m = _RECORD_VERB.match(clause) or _EVERYDAY_VERB.match(clause)
    if m and not _names_hit_folded(m.group("obj"), names):
        # A new, made-up thing ("set a goal for Monday", "give a
        # toast to Sam") or help for herself ("assign homework help to me")
        # is not one of her records.
        if _NEW_THING.match(m.group("obj")):
            return False
        rest = clause[m.end("obj"):]
        if re.match(r"(?i)\s*(?:to|for|with|as)\s+(?:me|us|myself|you)\b", rest):
            return False
    m = _RECORD_VERB.match(clause)
    if m:
        return not _PRONOUN_OBJ.match(m.group("obj"))
    m = _EVERYDAY_VERB.match(clause)
    if not m or _PRONOUN_OBJ.match(m.group("obj")):
        return False
    target = m.group("target").strip()
    # "Change Leo Park to attending": a record she keeps,
    # named with a capital, given a new value.
    return bool(_TARGET_LIKE.match(target) or _POSSESSIVE.search(m.group("obj"))
                or re.match(r"[A-Z][a-z]", target) or re.match(r"[A-Z][a-z]", m.group("obj")))


#: Anything that is not a word, a space or ordinary punctuation (emoji, symbols).
_SYMBOLS = re.compile(r"[^\w\s'.,!?:;$%&()/+\"-]")


def _plain(text: Any) -> str:
    """*text* with emoji and symbols turned into spaces, curly quotes straight."""
    body = str(text or "").replace("’", "'").replace("‘", "'")
    return re.sub(r"[ \t]+", " ", _SYMBOLS.sub(" ", body))


def _names_hit(text: str, names: Any) -> bool:
    low = text.lower()
    for name in names or ():
        word = re.sub(r"\s+", " ", str(name or "")).strip().lower()
        if len(word) >= 3 and re.search(r"(?<![\w])" + re.escape(word) + r"(?![\w])", low):
            return True
    return False


#: A change ask in Spanish, French, German, Portuguese or Italian.
_ASK_ELSEWHERE = re.compile(
    r"(?ix)^\W*(?:por\s+favor\s+|s'il\s+te\s+pla[iî]t\s+|bitte\s+|per\s+favore\s+)?"
    r"(?:añade|añadir|agrega|agregar|pon|poner|apunta|anota|quita|elimina|borra|tacha|marca|"
    r"ajoute|ajouter|ajoutez|mets|mettez|notez|supprime|supprimez|enlève|enlevez|retire|"
    r"retirez|coche|cochez|raye|füge|fügen|setze|setzen|schreib|schreibe|lösche|löschen|"
    r"entferne|streiche|hake|trag|trage|adiciona|adicione|coloca|coloque|põe|ponha|anote|"
    r"remova|apaga|apague|risca|marque|aggiungi|metti|segna|togli|rimuovi|cancella|spunta)\b")
#: "swap Dune for Emma in Book Club": capitals on both sides, inside a named thing.
_KEYLESS_SWAP = re.compile(
    r"(?x)^(?i:swap|switch|replace|trade|exchange|change|move)\s+(?:[A-Z][\w'-]*\s+){1,3}"
    r"(?i:for|with|to|into)\s+(?:[A-Z][\w'-]*\s*){1,3}"
    r"(?i:in|on|within|inside)\s+(?:(?i:the|my|our)\s+)?[A-Z][\w'-]*")


# -- asks said as questions, and a yes to an offer ------------------------------------

#: A question or a suggestion that is a request: "is it possible to add ...",
#: "how about adding ...", "why don't you add ...", "mind adding ...".
_ASK_LEAD = re.compile(
    r"(?ix)^(?:is\s+it\s+possible\s+(?:for\s+you\s+)?to|would\s+it\s+be\s+possible\s+"
    r"(?:for\s+you\s+)?to|(?:is\s+there\s+)?any\s+chance\s+(?:you\s+)?(?:can|could|would)|"
    r"(?:how|what)\s+about|why\s+(?:don'?t|do\s+not)\s+(?:you|we)|why\s+not|"
    r"(?:do|would)\s+you\s+mind|mind|(?:could|can)\s+i\s+get\s+you\s+to|"
    r"i'?d\s+(?:love|like|appreciate)(?:\s+it)?\s+if\s+you(?:'d|\s+(?:could|would|can))?|"
    r"(?:be\s+)?able\s+to|(?:is\s+it\s+)?(?:ok|okay|alright)\s+(?:if\s+you|to)|"
    r"(?:it'?s\s+)?time\s+to|remember\s+to|don'?t\s+forget\s+to|be\s+sure\s+to|"
    r"make\s+sure\s+(?:to|you)|feel\s+free\s+to|go\s+on\s+and|(?:would|are|will)\s+you\s+be\s+"
    r"able\s+to|are\s+you\s+able\s+to|do\s+me\s+a\s+favou?r\s+and|maybe|perhaps|"
    r"you\s+(?:could|might|may|can)|we\s+(?:should|could|need\s+to|gotta)|"
    r"(?:can|could|would|will)\s+(?:you|u|we)|please|pls|plz|kindly|just)\s+")
#: A change verb said as "-ing" after "how about", "mind": its plain form.
_GERUND_BASE = {
    "adding": "add", "putting": "put", "removing": "remove", "deleting": "delete",
    "erasing": "erase", "taking": "take", "marking": "mark", "checking": "check",
    "ticking": "tick", "crossing": "cross", "scratching": "scratch", "striking": "strike",
    "logging": "log", "recording": "record", "saving": "save", "clearing": "clear",
    "emptying": "empty", "wiping": "wipe", "including": "include", "writing": "write",
    "jotting": "jot", "noting": "note", "signing": "sign", "inviting": "invite",
    "uninviting": "uninvite", "placing": "place", "inserting": "insert",
    "entering": "enter", "dropping": "drop", "moving": "move", "tracking": "track",
    "scheduling": "schedule", "booking": "book", "rescheduling": "reschedule",
    "cancelling": "cancel", "canceling": "cancel", "changing": "change",
    "editing": "edit", "updating": "update", "renaming": "rename", "setting": "set",
    "swapping": "swap", "replacing": "replace", "switching": "switch",
    "resetting": "reset", "assigning": "assign", "giving": "give", "sticking": "stick",
    "popping": "pop", "getting": "get", "bumping": "bump", "counting": "count",
    "starring": "star", "pencilling": "pencil", "penciling": "pencil",
    "handing": "hand", "slotting": "slot", "ticking off": "tick off"}
#: Words said after a request that change nothing: "eggs on the list pls".
_ASK_TAIL = re.compile(
    r"(?i)[\s,]*(?:pls|plz|please|thanks|thank\s+you|thx|ty|for\s+me|asap|now|real\s+quick|"
    r"quick(?:ly)?|when\s+you\s+can|if\s+you\s+can|if\s+you\s+could|for\s+us|too|as\s+well|"
    r"right\s+away|today)\s*$")
#: She asks how, not for the change: "remove the noise in my recording, how?".
_HOW_ASK = re.compile(
    r"(?ix)(?:^|[,;:\s])how\s*\?*\s*$|^\W*how\s+(?:do|does|did|can|could|should|would|might|"
    r"to|is|are)\b|^\W*(?:what'?s|what\s+is)\s+the\s+(?:best\s+|easiest\s+|right\s+)?way\s+to\b|"
    r"^\W*any\s+tips\s+(?:on|for)\b")
#: A thing said to go on or off her thing with no verb: "eggs on the list",
#: "Leo off the guest list".
_BARE_PLACE = re.compile(
    r"(?ix)^(?!(?:what|which|who|whom|where|how|when|why|is|are|was|were|any|anything|"
    r"something|nothing|everything|does|do|did|there|it|that|this|i|we|you|he|she|they|"
    r"my|your|our|the\s+best|best)\b)(?P<obj>(?:[\w'$.-]+\s+){1,4}?)"
    r"(?:on|onto|in|into|off|off\s+of|out\s+of|to)\s+(?:the|my|our|your)\s+"
    r"(?:[\w'-]+\s+){0,2}?" + _THINGS[:-1] + r"|groceries|grocery|shopping)\s*$")
_BARE_STOP = re.compile(
    r"(?i)\b(?:is|are|was|were|'s|'re|be|been|being|have|has|had|do|does|did|can|could|will|"
    r"would|should|may|might|must|stay|stays|remain|remains|already|still|not|never|no|"
    r"n't|nothing|anything|everything|help|tips?|advice|ideas?)\b|n't\b")
#: "give Sam the dishes", "assign Mia the trash": a chore handed to someone.
_GIVE_CHORE = re.compile(
    r"(?x)^(?i:give|assign|hand)\s+(?-i:[A-Z])[a-z]+\s+(?i:the|a|an|this\s+week'?s|"
    r"today'?s|tonight'?s)?\s*(?P<obj>(?:[\w'-]+\s+){0,2}[\w'-]+)\s*$")
_GIVE_NOT_CHORE = re.compile(
    r"(?i)\b(?:call|ring|hug|kiss|break|chance|hand|minute|second|try|hint|clue|ride|lift|"
    r"shout|shout-?out|idea|ideas|tip|tips|advice|compliment|present|gift|card|update|"
    r"heads|high\s+five|pep\s+talk|piece|benefit|doubt|credit|space|time|hope|lesson|look|"
    r"bath|treat|snack|bottle|medicine|pill|book|link|recipe|answer|message|note|hello|"
    r"hi|kudos|thanks|birthday|wave|smile|name|nickname|hand)\b")
#: Asks that are text work or sums, not changes: "add up these numbers", "put
#: together a packing list", "add a twist to this recipe", "delete that thought".
_ASK_NOT_CHANGE = re.compile(
    r"(?ix)^(?:(?:add|sum|total)\s+(?:up|together)\b|"
    r"add\s+(?:these|those|the|all\s+(?:the|these|those)?|my)?\s*(?:numbers|figures|values|"
    r"amounts|prices|costs|digits|fractions|columns|scores)\b|"
    r"(?:put\s+together|come\s+up\s+with|draw\s+up|write\s+up|make\s+up|dream\s+up|"
    r"think\s+up|whip\s+up)\b|"
    r"(?:add|put|throw|give|remove|take\s+out|drop)\s+(?:(?:a|an|some|more|less|the|"
    r"little|bit\s+of|touch\s+of|few)\s+)*(?:twist|touch|pinch|dash|spin|flair|kick|zing|"
    r"splash|zest|flavou?r|spice|garnish|variation|sparkle|pizzazz|punch|colou?r|emphasis|"
    r"drama|suspense|humou?r|personality|warmth)\b|"
    r"(?:delete|scratch|forget|erase|remove|drop|ignore|disregard|strike)\s+(?:that|this|my|"
    r"the)\s+(?:last\s+)?(?:thought|thoughts|comment|remark|question|idea|bit|part|"
    r"sentence|line|paragraph|message|reply|answer))")
#: A bare yes: "sure", "ok go for it", "yes please", "sounds good".
_GO_AHEAD_ANY = re.compile(
    r"(?ix)^\W*(?:(?:ok|okay|k|kk|yes|yeah|yea|yep|yup|ya|sure|sure\s+thing|please|pls|"
    r"alright|all\s+right|great|perfect|absolutely|definitely|of\s+course|certainly|cool|"
    r"fine|go\s+ahead|go\s+for\s+it|do\s+it|do\s+that|do\s+so|please\s+do|yes\s+please|"
    r"sounds\s+good|sounds\s+great|that\s+works|that'?d\s+be\s+(?:great|nice|good|perfect|"
    r"lovely|awesome)|why\s+not|let'?s\s+do\s+(?:it|that)|make\s+it\s+so|you\s+bet|"
    r"thanks|thank\s+you|i'?d\s+like\s+that|i\s+would\s+like\s+that|good\s+idea|great\s+idea|"
    r"go\s+on|yes\s+do|yes\s+you\s+can|you\s+can|you\s+may|please\s+and\s+thank\s+you|"
    r"that\s+one|both|all\s+of\s+them|the\s+first\s+one|do\s+both|mhm|uh\s+huh|affirmative|"
    r"indeed|right|correct|exactly|ya\s+know\s+it)\W*)+$")
#: The bot offering a change: "Want me to add eggs to your Grocery list?".
_OFFER = re.compile(
    r"(?ix)\b(?:want\s+me\s+to|would\s+you\s+like\s+me\s+to|should\s+i|shall\s+i|"
    r"do\s+you\s+want\s+me\s+to|i\s+can|i\s+could|i'?ll|let\s+me|may\s+i|can\s+i|"
    r"how\s+about\s+i|like\s+me\s+to|happy\s+to|glad\s+to|want\s+to|would\s+you\s+like\s+to|"
    r"ready\s+to|okay\s+to|ok\s+to|go\s+ahead\s+and)\s+(?P<ask>[^?.!\n]{2,200})")


#: An amount or points given to a person: "give Ava $5 in allowance".
_GIVE_AMOUNT = re.compile(
    r"(?x)^(?i:give|pay|credit|award|dock|charge|owe)\s+(?-i:[A-Z])[a-z]+\s+"
    r"(?:[-+$£€]?\d[\d.,]*|(?i:a|one|two|three|another|an\s+extra)\s+(?i:points?|stars?|"
    r"dollars?|bucks|credits?|ticks?))")
#: Two of her things swapped inside a named one: "swap Coco and Luca in the queue".
_SWAP_IN = re.compile(
    r"(?i)^(?:swap|switch|trade|exchange|flip|reverse)\s+(?:\S+\s+){1,3}?(?:and|with|for)\s+"
    r"(?:\S+\s+){1,3}?(?:in|on|within)\s+(?:the|my|our|your)\s+\S")


#: Verbs that lead a clause about her thing without putting anything on it:
#: "keep an eye on the budget", "get started on the guest list".
_BARE_VERBS = frozenset("""
keep get stay focus work start go come look check help plan think talk chat read see
dive jump hop move catch sign log follow count bet rely depend lean agree decide
""".split())


def _bare_obj_has_verb(obj: str) -> bool:
    """The words before "on the list" start with a verb, or are one of the
    shapes that put nothing anywhere ("an eye", "started", "help")."""
    words = obj.split()
    if not words:
        return False
    first = words[0].strip("'").lower()
    return (first in _BARE_VERBS or first in _EVERYDAY_COMMAND
            or bool(_PLACE_OBJ_EXCLUDED.search(obj)))


def _offers_change(before: Any, names: Any) -> bool:
    """The bot's previous message offered a change to her things."""
    text = _plain(before).strip()
    if not text:
        return False
    for sentence in _SENTENCE_RE.findall(text[-1200:]):
        s = sentence.strip()
        if not s:
            continue
        for m in _OFFER.finditer(s):
            if change_turn(m.group("ask"), names):
                return True
        if s.endswith("?") and change_turn(re.sub(r"(?i)^\W*(?:want|wanna|need)\s+", "", s),
                                           names):
            return True
    return False


def change_turn(asked: Any, names: Any = (), before: Any = None) -> bool:
    """True when the owner's message asks for a change to her things: a
    clause starts with a change verb ("add Leo Park to the guest list",
    "Sam did the dishes, mark it", "can you take tomatoes off the plantings").

    *names* are her list and app names (never their contents); with them a
    softer verb counts too ("change basil to kale in Plantings"). Text work
    is not a change: "add a summary", "remove the jargon from this email",
    "add 5 and 7", or a pasted text ("fix this list: ...").

    *before* is the bot's previous message, when the path has one: a bare
    yes ("sure", "ok go for it") to a change it offered is a change ask."""
    # No length cutoff (a long, chatty message that ends
    # "please add oat milk to the grocery list" is still a change ask).
    if before is not None and yes_to_her_things(asked, before, names):
        return True
    text = _plain(asked).strip()
    if not text:
        return False
    if before is not None and _GO_AHEAD_ANY.match(text) and _offers_change(before, names):
        return True
    # "undo the last thing you added to the pantry", "revert that".
    if _UNDO_ASK.match(text):
        return True
    # "remove the background noise in my recording, how?": she asks how.
    if _HOW_ASK.search(text):
        return False
    things = bool(_THING_WORD.search(text)) or _names_hit(text, names)
    pasted = bool(_PASTED.search(text))
    # The message ends in a question of its own ("..., what does it mean?").
    wh_tail = text.endswith("?") and bool(re.search(
        r"(?i)[,;:.!]\s*(?:so\s+|and\s+)?(?:what|why|how|who|when|where|which)\b[^,;:.!]*\?$", text))
    if pasted and _PASTED_THING.search(text):
        return False
    # "Update the Soccer Schedule: game moved to 10am", "file
    # this under Vet visits: rabies booster". Her thing is named BEFORE the
    # colon, so what follows is the change, not a text to work on.
    if pasted and ":" in text:
        head = _LEAD_IN.sub("", text.split(":", 1)[0].strip()).strip()
        for _ in range(3):
            lead = _ASK_LEAD.match(head)
            if not lead:
                break
            head = _LEAD_IN.sub("", head[lead.end():].strip()).strip()
        if re.match(r"(?i)file\s+(?:\S+\s+){0,4}?(?:under|in|into)\s+\S", head):
            return True
        if (_STRONG_VERB.match(head.lower()) or _SOFT_VERB.match(head.lower())) and (
                _THING_WORD.search(head) or _names_hit(head, names)
                or _fc_hers_named(head, names)) and not _TEXT_THING.search(head):
            return True
    if _TEXT_THING.search(text) and not _names_hit(text, names):
        return False
    # "añade huevos a la lista", "ajoute du lait à la liste".
    if _ASK_ELSEWHERE.match(text) and (_LIST_ELSEWHERE.search(text) or things):
        return True
    for clause in _CLAUSE_SPLIT.split(text):
        raw = _LEAD_IN.sub("", clause.strip()).strip()
        # "is it possible to add ...", "how about adding ...",
        # "why don't you add ...", "eggs on the list pls".
        for _ in range(3):
            lead = _ASK_LEAD.match(raw)
            if not lead:
                break
            raw = _LEAD_IN.sub("", raw[lead.end():].strip()).strip()
        first = raw.split(" ", 1)
        if first and first[0].lower() in _GERUND_BASE:
            raw = _GERUND_BASE[first[0].lower()] + (" " + first[1] if len(first) > 1 else "")
        for _ in range(3):
            raw = _ASK_TAIL.sub("", raw).strip()
        c = raw.lower()
        if not c or _MATH.match(c) or _ASK_NOT_CHANGE.match(c):
            continue
        m = _BARE_PLACE.match(c)
        if m and not _BARE_STOP.search(m.group("obj")) and not text.endswith("?") \
                and not _bare_obj_has_verb(m.group("obj")):
            return True
        if names and not text.endswith("?"):
            m = re.match(r"(?i)^(?P<obj>(?:[\w'$.-]+\s+){1,4}?)(?:on|onto|in|into|off|to)\s+"
                         r"(?:the\s+)?(?P<name>.+?)\s*$", raw)
            if m and not _BARE_STOP.search(m.group("obj")) \
                    and not _bare_obj_has_verb(m.group("obj")) and not re.match(
                    r"(?i)(?:what|which|who|how|when|why|where|i|we|you|it|that|this)\b", raw) \
                    and _names_hit(" " + m.group("name") + " ", names) \
                    and len(m.group("name").split()) <= 4:
                return True
        # "give Ava $5 in allowance", "pay Sam 3 points";
        # "swap Coco and Luca in the queue"; "queue up Luca next".
        if _GIVE_AMOUNT.match(raw) or (_SWAP_IN.match(raw) and not _NOT_RECORD.search(raw)) \
                or re.match(r"(?i)queue\s+(?:up\s+)?\S", raw):
            return True
        m = _GIVE_CHORE.match(raw)
        if m and not _GIVE_NOT_CHORE.search(m.group("obj")) \
                and not _CONTENT_WORD.search(m.group("obj")):
            return True
        # "remove the stain from my shirt how?": she asks how, not for a change.
        if re.search(r"\bhow\s*$|^how\b|\bhow\s+(?:do|can|should|would|to)\b", c):
            continue
        # Keyless, "swap Dune for Emma in Book Club": a
        # named thing swapped inside a named thing is a record change.
        if not names and _KEYLESS_SWAP.match(raw) and not _NOT_RECORD.search(raw):
            return True
        if not pasted and not (_CONTENT_WORD.search(c) or _TEXT_THING.search(c)):
            if _record_target(raw, names):
                return True
            if things and _GOES_PLACE.match(c):
                return True
        if _STRONG_VERB.match(c):
            if (_CONTENT_WORD.search(c[:60]) or pasted) and not things:
                continue
            # "put the kettle on is what my gran says, what does
            # it mean?", "mark this day in history, what happened?": a saying
            # she asks about, naming none of her things.
            if wh_tail and not things:
                continue
            return True
        if _SOFT_VERB.match(c) and things and not pasted:
            return True
        if pasted:
            continue
        # "Leo Park is attending, update it": a record verb on "it" after she
        # said what changed, when she has apps.
        if names and _SOFT_VERB.match(c) and re.fullmatch(
                r"(?i)[a-z]+\s+(?:it|that|this|them|him|her)(?:\s+(?:please|for\s+me|now))?", c):
            return True
        if _command_on_thing(raw, names):
            return True
        if things and (_WANT_PLACE.match(c) or _PLACE_VERB.match(c)):
            return True
        m = _SHOULD_BE.match(c)
        if m and (things or not (_MODAL_FIRST.match(c) and _PLACE_END.search(m.group(0)))):
            return True
    return False


def asks_for_change(message: Any) -> bool:
    """True when the owner's message asks for a change to her things (a
    list, an app, a record): "add ...", "mark ... as done", "take ... off"."""
    return bool(_ASK_DEED.search(str(message or "")))


_CONTENT_ASK = re.compile(
    r"(?ix)^\s*(?:please\s+)?(?:tell|write|compose|draft|explain|describe|"
    r"summari[sz]e|translate|list|show|generate|imagine|pretend|plan|suggest|recommend|"
    r"help|teach|find|search|look|compare|brainstorm|outline|rewrite|define)\b"
    # "can you write a poem about Friday" is writing
    # too. Only writing verbs take the polite lead ("can you help me move the
    # dentist" stays a change ask), and never with one of her lists as the
    # place ("could you write milk on the grocery list").
    r"|^\s*(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
    r"(?:i'?d|i\s+would)\s+like\s+you\s+to\s+|i\s+want\s+you\s+to\s+)"
    r"(?:(?:explain|describe|summari[sz]e|translate|imagine|pretend|teach|brainstorm|outline|"
    r"define)\b|(?:write|compose|draft|rewrite)\s+(?:me\s+|us\s+|him\s+|her\s+|them\s+)?"
    r"(?:[\w'-]+\s+){0,3}?(?:poems?|stor(?:y|ies)|songs?|jokes?|haikus?|limericks?|letters?|"
    r"emails?|notes?\s+to|cards?|toasts?|speech(?:es)?|essays?|captions?|summar(?:y|ies)|"
    r"paragraphs?|lyrics|raps?|riddles?|messages?\s+to|invitations?\s+(?:for|to)|bio|"
    r"thank[\s-]you|sonnets?|verses?|scripts?)\b)"
    r"(?!.*\b(?:to|on|in|into|onto|down\s+on)\s+(?:the|my|our|your)\s+(?:[\w'-]+\s+){0,2}"
    r"(?:lists?|calendar|schedule|log|chart|tracker|board|notes|planner|journal)\b)")
#: "make", "create", "give" ask for writing only with a piece of writing or an
#: idea as the thing made: "make a poem", "give me ideas". "make Leo Park a
#: guest" is not writing (otherwise the live model says "Leo Park is added
#: to the guest list!" and no rule looks at it).
_MADE_CONTENT = re.compile(
    r"(?ix)^\s*(?:please\s+)?(?:make|create|give|come\s+up\s+with|put\s+together|build)\s+"
    r"(?:me\s+|us\s+|him\s+|her\s+|them\s+)?(?:(?:a|an|some|the|my|our|few|couple\s+of|"
    r"quick|short|long|simple|fun|new|good|\d+)\s+){0,3}"
    r"(?:poems?|stor(?:y|ies)|songs?|jokes?|lists?\s+of|plans?|summar(?:y|ies)|recipes?|menus?|"
    r"toasts?|speech(?:es)?|cards?|messages?|captions?|names?\s+for|ideas?|tips?|examples?|"
    r"suggestions?|outlines?|drafts?|letters?|emails?|quiz(?:zes)?|riddles?|haikus?|"
    r"limericks?|raps?|playlists?\s+of|schedules?\s+for|pep\s+talk|advice|overview|"
    r"explanations?|descriptions?|comparisons?|charts?\s+of|tables?\s+of|word\s+search|"
    r"pictures?|drawings?|images?|sketch(?:es)?|hug|high\s+five|compliment|shout-?out)\b")


def _content_ask(text: str) -> bool:
    """Her message asks for writing, an answer or ideas, not a change."""
    return bool(_CONTENT_ASK.match(text) or _MADE_CONTENT.match(text))
#: "I've added Leo Park as a guest": the bot saying it did something to a record.
_I_DID = re.compile(
    r"(?ix)\b(?:I|we)(?:'ve|'ll|'m|'re)?"
    r"(?:\s+(?:have|has|had|will|shall|am|are|did|just|already|now|also|then|"
    r"successfully|gone|went|go|going|gonna|ahead|and|to|been|right|away))*"
    r"\s+(?:" + _DEED_WORDS + r")\b")


# -- after a statement or a command that is not a change ask ------------------------
#
# "Leo Park is coming too", "Leo cancelled", "we invited Leo Park", "count Leo
# in": she may mean a change, the tool chooser may pick nothing, and the model
# then says "Leo Park has been invited". So after such a turn a reply sentence
# that puts one of HER things (a word from her message, a pronoun for it, or
# a list word) into a new state is a claim. The anchor keeps sentences about
# anything else untouched ("They're done just in time" after "I baked cookies").

#: States a record is said to be in after a change.
_STATE_PART = (_PARTICIPLES + r"|confirmed|registered|rsvp'?d|counted(?:\s+in)?|"
               r"pencill?ed\s+in|locked\s+in|sorted|squared\s+away|good\s+to\s+go|"
               r"on\s+board|accounted\s+for|reserved|noted|in\s+the\s+books|set\s+up")
_ST_IS = re.compile(
    r"(?ix)(?:\b(?:is|are|was|were)|'s|'re)\s+(?:now\s+|all\s+|already\s+|officially\s+|"
    r"also\s+|definitely\s+|successfully\s+)*(?:" + _STATE_PART + r")\b")
_ST_BEEN = re.compile(
    r"(?ix)(?:\b(?:has|have|had)|'s|'ve)\s+(?:now\s+|just\s+|also\s+|already\s+|"
    r"officially\s+)*been\s+(?:\w+\s+)?(?:" + _STATE_PART + r")\b")
#: "Leo Park is in.", "he's on.", "Leo's in for Big Day", "Leo is on the list".
_ST_WHERE = re.compile(
    r"(?ix)(?:\b(?:is|are)|'s|'re)\s+(?:now\s+|all\s+|officially\s+|also\s+)*"
    r"(?:(?:in|on|off|out|down|there|set)(?=\s*(?:[!.,;]|$|\s+(?:now|too|as\s+well|"
    r"then|for\s+(?:the|your|my|our)\b|with\s+(?:the|your|my|our)\b)))"
    r"|(?:on|in|off|onto|into|out\s+of)\s+(?:the|your|my|our|his|her|their)\s+"
    r"(?:[\w'-]+\s+){0,3}?" + _THINGS + r"\b)")
#: "The guest count is now 43", "the total now stands at 12".
_ST_NOW = re.compile(
    r"(?ix)\b(?:is|are|'s)\s+now\s+[-+$£€\d]|\bnow\s+(?:at|up\s+to|down\s+to|totals?|"
    r"stands?\s+at|comes?\s+to)\b")
#: "Leo won't be on the list anymore": said as done, not as a refusal.
_ST_NO_MORE = re.compile(
    r"(?ix)\b(?:won'?t|will\s+not|isn'?t|is\s+not|aren'?t|are\s+not|not)\s+(?:be\s+)?"
    r"(?:on|in|coming|attending|invited|listed|there)\b[^.!?\n]{0,40}\b"
    r"(?:anymore|any\s+more|any\s+longer)\b")
#: The bot saying it did one of these to a record, in any tense.
_I_DID_MORE = re.compile(
    r"(?ix)\b(?:I|we)(?:'ve|'ll|'m|'re|'d)?"
    r"(?:\s+(?:have|has|had|will|shall|am|are|did|just|already|now|also|then|"
    r"successfully|gone|went|go|going|gonna|ahead|and|to|been|right|away))*"
    r"\s+(?:not(?:ed|ing)|rsvp'?d|rsvp(?:ing)?|register(?:ed|ing)?|confirm(?:ed|ing)?|"
    r"count(?:ed|ing)?|pencill?(?:ed|ing)?|lock(?:ed|ing)?\s+in|sort(?:ed|ing)|"
    r"reserv(?:ed|ing)|set\s+(?:\S+\s+){0,3}?up|stuck|pop(?:ped|ping)|slott(?:ed|ing))\b")
#: Something else said to have done it: "Big Day app just updated his name".
_ST_THIRD = re.compile(
    r"(?ix)\b(?:just|already|now|has|have|had|got|successfully)\s+(?:just\s+|now\s+|"
    r"already\s+|been\s+)*(?:" + _PARTICIPLES + r"|confirmed|registered|rsvp'?d|counted|"
    r"noted|reserved)\b")
#: Words that stand for her thing in a reply: "he", "it", "them", "that".
_PRONOUN = re.compile(r"(?ix)\b(?:he|she|they|him|her|them|it|that|this|everyone|"
                      r"everything|both|all\s+of\s+them)\b")
_STOP = frozenset((
    "the and for with that this there their they them then than what when where who why "
    "how you your yours our ours mine his her hers its been being was were are is get got "
    "gets have has had can could will would should shall may might must just also too "
    "really very some any all not now today tonight tomorrow yesterday yes yeah okay please "
    "thanks thank from into onto about over under after before again still even much many "
    "more most other such only own same well back out off down here one two three").split())


def _carried(asked: Any) -> set[str]:
    """Content words of her message (three letters or more, not common words)."""
    return {w for w in _WORD_RE.findall(_plain(asked).lower().replace("'s", ""))
            if len(w) >= 3 and w not in _STOP}


#: General states that say nothing about a list on their own ("they're done").
_GENERAL_STATE = re.compile(
    r"(?ix)\b(?:all\s+)?(?:done|finished|ready|set|sorted|handled|good\s+to\s+go|"
    r"taken\s+care\s+of|squared\s+away)\b")


def _anchored(body: str, carried: set[str], *, pronoun_ok: bool = True,
              names: Any = ()) -> bool:
    """The sentence talks about her thing: a word from her message, a list or
    app word, one of her list or app names, or (when *pronoun_ok*) a pronoun
    for it."""
    words = set(_WORD_RE.findall(body.lower().replace("'s", "")))
    if words & carried or _THING_WORD.search(body) or _names_hit(body, names):
        return True
    return pronoun_ok and bool(_PRONOUN.search(body))


def _state_said(body: str) -> bool:
    """A record said to be in a new state, or a change said or promised
    ("let's update the guest list")."""
    return bool(_CTX_STATE.search(body) or _ST_IS.search(body) or _ST_BEEN.search(body)
                or _ST_WHERE.search(body) or _ST_NOW.search(body) or _ST_THIRD.search(body)
                or _CTX_NEW_STATE.search(body) or _CTX_BARE.search(body)
                or _CTX_FIRST_PERSON.search(body))


# After a statement, when she
# has apps, a status line about her thing is a claim in shapes the rules
# above do not read: "Guest list updated.", "Leo removed.", "Leo Park:
# attending.", "Laundry: complete.", "That brings you to 43.", "Your guest
# list reflects that now.", "Dune's rating is 5 now.", "Swapped! Emma
# replaces Dune.", "I'll remember Leo is coming." These rules are anchored on
# her own words and names, so they fail toward the honest line: a friendly
# echo such as "Leo Park, wonderful!" is replaced too. No list of reply
# shapes is complete; the ask side (change_turn) is the bound, these are the
# second net.

#: Words of her message that name a state, not a thing: they do not anchor.
_STATE_WORDS = frozenset(
    "done gone come comes coming go goes going did does got made set due over off "
    "finished cancelled canceled attending complete completed ready".split())
#: A subject word that makes a sentence a full clause, not a headline.
_CLAUSE_WORD = re.compile(
    r"(?i)^(?:i|we|you|he|she|they|it|is|are|was|were|am|has|have|had|will|would|"
    r"did|do|does|can|could|should|may|might|must|not|never|if|when|once|before|after|"
    r"how|what|why|who|where|which|there|here|let's|lets|let)$")
_HEAD_PARTS = (_STATE_PART + r"|assigned|reassigned|unassigned|swapped|bumped|replaced|"
               r"renamed|rescheduled|postponed|shifted|pushed|archived|completed?|attending|"
               r"called|named|rated|[a-z]{3,}ed")
#: What follows a headline's participle: the end, a place or time word, or
#: "to" that is not an infinitive. Not an object ("Dune changed my life").
_HEAD_AFTER = (r"(?=\s*(?:[.!,:;–—-]|$)|\s+(?:from|as|for|on|onto|in|into|off|"
               r"out|at|by|with|today|tonight|now|already|just|successfully|and|too|"
               r"to\s+(?!(?:be|go|come|see|do|have|get|make|know|meet|say|try|help|join|"
               r"attend|visit|stay|read|play|win|hear|love|like|keep|find|leave|take)\b))\b)")
_HEADLINE = re.compile(
    r"(?i)^\s*(?P<lead>(?:[\w'$-]+[,:]?\s+){1,6}?)(?:" + _HEAD_PARTS + r")\b" + _HEAD_AFTER)
#: A greeting or a cheer opens the line: "Yay, Leo Park!", "Dune, what a ride!".
_CHATTER = frozenset(
    "oh ah aw aww yay yey wow woo woohoo hooray hurray congrats congratulations welcome hi "
    "hello hey thanks thank great nice awesome amazing lovely wonderful good well what how "
    "so such enjoy have say tell give sorry bummer no yes yeah ok okay sure cheers bravo "
    "love happy lucky poor dear go here there clean fresh".split())
_FRAGMENT = re.compile(
    r"(?i)^\s*(?P<lead>(?:[\w'$-]+\s+){0,3}[\w'$-]+)\s*[:,;–—-]\s*"
    r"(?P<tail>(?:[\w'$%-]+\s*){1,3})[.!]*\s*$")
_COUNT = re.compile(
    r"(?ix)\b(?:brings?|bringing|brought|puts?|putting|takes?|took|gets?|got|makes?|made)\s+"
    r"(?:you|us|it|them|(?:the|your|our)\s+(?:[\w'-]+\s+){0,2}(?:total|count|number|tally|"
    r"headcount|list))\s+(?:up\s+|down\s+|back\s+|right\s+)?(?:to|at)\s+[-+$£€]?\d"
    r"|\b(?:one|two|three|four|five|a\s+few|\d+)\s+(?:less|fewer|more)\s+(?:[\w'-]+\s+){0,2}"
    r"(?:on|in|off|to\s+do|left)\b")
_REFLECT = re.compile(
    r"(?ix)\breflect(?:s|ed|ing)?\b|\bwill\s+(?:now\s+)?(?:show|appear|be\s+(?:listed|marked|"
    r"shown))\b|\b(?:is|are)\s+(?:now\s+)?(?:up\s+to\s+date|current)\b")
_NOW_STATE = re.compile(
    r"(?ix)(?:\b(?:is|are|has|have|had|got|gets)|'s|'re)\s+(?:[\w'$-]+\s+){0,3}?now\s*[!.]*\s*$"
    r"|\bnow\s+(?:called|named|titled|assigned|belongs?|goes|go|sits?|lives?|reads?|says?|"
    r"shows?|lists?|has|have|holds?|set|due|on|in|at)\b")
_SWAP_STATE = re.compile(
    r"(?ix)\b(?:replaces?|replacing|swaps?\s+(?:in|out|places)|takes?\s+(?:over\s+)?"
    r"(?:the\s+|his\s+|her\s+|its\s+|their\s+)?(?:place|spot|slot)|goes\s+(?:on|in|into|onto|"
    r"to)\s+(?:the|your|my|our)|moves?\s+(?:to|up|down|over)|is\s+(?:moving|going)\s+"
    r"(?:to|on|in)\s+(?:the|your))\b")
_ONE_DEED = re.compile(r"(?i)^\s*[a-z]{3,}ed\s*[!.]*\s*$")
_REMEMBER = re.compile(
    r"(?ix)\b(?:I|we)(?:'ll|\s+will|\s+shall|'m\s+going\s+to|\s+am\s+going\s+to|"
    r"'re\s+going\s+to|\s+are\s+going\s+to|'ll\s+be\s+sure\s+to|\s+will\s+be\s+sure\s+to|"
    r"'ll\s+make\s+sure\s+to)\s+(?:\w+\s+){0,2}?(?:remember|keep\s+(?:track|tabs|a\s+record|"
    r"a\s+note|note|(?:[\w'-]+\s+){1,3}?in\s+mind)|make\s+(?:a\s+)?note|note|"
    r"jot|write|save|store|file|log|track)\b")
#: The sentence says the change was NOT made, or offers it.
_DISOWN = re.compile(
    r"(?ix)\b(?:have\s+not|haven'?t|has\s+not|hasn'?t|did\s+not|didn'?t|cannot|can'?t|"
    r"could\s+not|couldn'?t|won'?t|will\s+not|not\s+able|unable|am\s+not|isn'?t|is\s+not|"
    r"aren'?t|are\s+not|not\s+yet|would\s+you|shall\s+i|should\s+i|want\s+me\s+to|"
    r"like\s+me\s+to|if\s+you|you\s+(?:can|could|might|may|need\s+to|want\s+to)|"
    r"you'll\s+(?:need|want)|just\s+say|try\s+saying|'m\s+not|not\s+sure|unsure|"
    r"no\s+idea|don'?t\s+know|do\s+not\s+know|could\s+you|can\s+you|please\s+(?:clarify|"
    r"tell|provide|explain)|not\s+much|nothing\s+(?:much|to\s+do|for\s+me))\b")
#: Her thing placed on, in or off one of her lists or apps: "Leo Park appears
#: on the guest list", "No more Leo on the guest list", "Leo gone from the list".
_ON_THING = (r"\b(?:on|in|onto|into|off|from|to|for|under|out\s+of)\s+(?:the\s+|your\s+|my\s+|"
             r"our\s+|this\s+)?(?!(?:any|every|a|an|some|most|many|all|other|their)\b)"
             r"(?:[\w'-]+\s+){0,3}?" + _THINGS + r"\b")
_ON_THING_RE = re.compile(r"(?i)" + _ON_THING)
#: "One more guest for Big Day", "no more Leo on the list".
_COUNT_WORD = re.compile(r"(?i)\b(?:one|two|three|a|\d+|no)\s+(?:more|less|fewer)\b")
_SUBJECT_PRONOUN = re.compile(r"(?i)\b(?:he|she|they|him|her|them|it)(?:'s|'ll|'re|'d)?\b"
                              r"|\bthere(?:'s|\s+is|\s+are)\b")
#: A list shown as it now stands: "Guest list: Aunt May, Leo Park."
_LIST_SHOWN = re.compile(r"(?i)^\s*(?:(?:the|your|my|our)\s+)?(?:[\w'-]+\s+){0,3}?"
                         + _THINGS + r"\s*:\s*\S")


def _item_words(asked: Any) -> set[str]:
    """Her message's words that can name a thing: not common words, not a
    state ("done", "coming"), not a verb form ending -ed or -ing."""
    return {w for w in _carried(asked)
            if w not in _STATE_WORDS and not w.endswith(("ed", "ing"))}


def _first_word(text: str) -> str:
    words = _WORD_RE.findall(text.lower())
    return words[0] if words else ""


#: A sentence that tells her what to do: "Add him to the guest list."
_TELLS_HER = re.compile(
    r"(?ix)^\W*(?:(?:just|please|so|then|now)\s+)?(?:add|put|remove|mark|update|take|cross|"
    r"check|tick|write|note|log|record|include|move|change|pop|stick|drop|jot|sign)\s+"
    r"(?!(?:it|that|this)\s+(?:is|was)\b)")


def _status_claim(body: str, asked: Any, names: Any) -> bool:
    """A status line about her thing after a statement, when she has apps."""
    if not names or _DISOWN.search(body) or body.strip().endswith("?"):
        return False
    # "Add him to the guest list.": an instruction to her, not a status
    # (seen live with qwen2.5:7b).
    if _TELLS_HER.match(body):
        return False
    if wording.NO_APP_CHANGE.lower() in body.lower():
        return False
    items = _item_words(asked)

    def anchored(text: str) -> bool:
        words = set(_WORD_RE.findall(text.lower().replace("'s", "")))
        return bool(words & items) or _names_hit(text, names)

    if _ONE_DEED.match(body) or _COUNT.search(body):
        return True
    if _REMEMBER.search(body) and (anchored(body) or _PRONOUN.search(body)):
        return True
    m = _HEADLINE.match(body)
    if m and (anchored(m.group("lead")) or _THING_WORD.search(m.group("lead"))) and not any(
            _CLAUSE_WORD.match(w.strip(",:")) for w in m.group("lead").split()):
        return True
    m = _FRAGMENT.match(body)
    if (m and (anchored(m.group("lead")) or anchored(m.group("tail")))
            and not ({_first_word(m.group("lead")), _first_word(m.group("tail"))}
                     & _CHATTER)
            # "Dune: a classic." is praise, not a status; "Way to go, Mia!" is
            # a cheer.
            and not (_T_PRAISE.search(m.group("tail")) and not _T_STILL_CLAIM.search(body))
            and not _t_cheer_only(body, _t_anchor_words(asked), names)
            and not any(_CLAUSE_WORD.match(w) for w in _WORD_RE.findall(
                (m.group("lead") + " " + m.group("tail")).lower()))):
        return True
    if _LIST_SHOWN.match(body) or (_names_hit(body.split(":")[0], names) and ":" in body):
        return True
    placed = _ON_THING_RE.search(body) or any(
        re.search(r"(?i)\b(?:on|in|onto|into|off|from|to|for|out\s+of)\s+(?:the\s+)?"
                  + re.escape(str(n).strip()) + r"\b", body)
        for n in names if len(str(n).strip()) >= 3)
    # Her item (not only the list's name) or a pronoun for it, placed there.
    if placed and (set(_WORD_RE.findall(body.lower().replace("'s", ""))) & items
                   or _SUBJECT_PRONOUN.search(body) or _COUNT_WORD.search(body)):
        return True
    if not (anchored(body) or _THING_WORD.search(body)):
        return False
    return bool(_REFLECT.search(body) or _NOW_STATE.search(body)
                or (anchored(body) and _SWAP_STATE.search(body)))


def _statement_claim(body: str, asked: Any, names: Any = ()) -> bool:
    """A reply sentence after a statement turn that says her thing changed."""
    if _ABOUT_THE_REPLY.search(body):
        return False
    if _status_claim(body, asked, names):
        return True
    if _ST_NO_MORE.search(body) and _anchored(body, _carried(asked), names=names):
        return True
    if _NOT_A_CLAIM.search(body):
        return False
    if (_I_DID.search(body) or _I_DID_MORE.search(body) or _CTX_DONE.match(body)
            or _CTX_DONE_MORE.match(body) or _CTX_YES_DEED.match(body)):
        return True
    if not _state_said(body):
        return False
    carried = _carried(asked)
    if _anchored(body, carried, pronoun_ok=False, names=names):
        return True
    # A pronoun alone ("he's on", "they've been added") counts when the state
    # is more than a general word: "they're done just in time" is not a list.
    # A short line that is only that ("He's all set!") still says it.
    if not _anchored(body, carried, names=names):
        return False
    return len(_WORD_RE.findall(body.lower())) <= 5 or _state_said(
        _GENERAL_STATE.sub(" ", body))


def _statement_ask(asked: Any) -> bool:
    """A short statement from her ("Leo Park is coming too"): not a question
    and not a request for writing. A reply that says "I've added ..." to it
    claims a change."""
    text = _plain(asked).strip()
    if not text or text.endswith("?") or len(text.split()) > 30:
        return False
    # "hi there", "thanks so much" say nothing about her things.
    if _SMALL_TALK.match(text):
        return False
    if _GO_AHEAD.match(text):
        return True  # "ok do it": a yes, not a question
    return not (is_question(text) or _QUESTION_RE.match(text) or _content_ask(text))


#: A yes to something the bot offered: "ok do it", "go ahead", "yes please".
_GO_AHEAD = re.compile(
    r"(?ix)^(?:(?:ok|okay|yes|yeah|yep|sure|please|alright|great|perfect)[\s,!.]*)*"
    r"(?:do\s+(?:it|that|this)|go\s+(?:ahead|for\s+it)|yes\s+please|please\s+do|"
    r"make\s+it\s+so|sounds\s+good|that'?s\s+fine|that\s+works)\b")


# -- fail closed after a statement -------------------------------------------------
#
# A list of the SHAPES of a false claim ("has been added", "is on the list",
# "Leo Park: attending", ...) always misses more ("Leo is struck.", "Dune has
# a star.", "Headcount: 43."). A list of claim shapes fails OPEN: a shape
# nobody listed passes.
#
# So after a statement or a command that is not a change ask, the list is
# turned round. A clause that is about HER thing (a word of her message, one
# of her list or app names, a list word, a pronoun for it) and says something
# IS so about it ("is", "has", "shows", "got", a past participle, a status
# after a colon, a bare "Starred.") is a claim, UNLESS it has one of the few
# harmless shapes listed below (praise, a feeling, a guess, a question, an
# offer, a place she named, a trip). What this list misses is replaced by the
# honest line (fail closed): the owner reads "I have not changed anything in
# your apps." in place of a friendly sentence, never a made-up change.
#
# It runs without her names too (a keyless caller gets names=[]), anchored on
# her own words, so what a keyless caller hears does not depend on her lists.

_STEM_ES = re.compile(r"(?:ches|shes|xes|sses|zes|oes)$")


def _fold(word: str) -> str:
    """A word in its one-thing form: "dishes" -> "dish", "chores" -> "chore"."""
    w = word.lower().strip("'")
    if w.endswith("'s"):
        w = w[:-2]
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and _STEM_ES.search(w):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _folded_words(text: str) -> set[str]:
    return {_fold(w) for w in _WORD_RE.findall(_plain(text).lower())}


def _names_hit_folded(text: str, names: Any) -> bool:
    """One of her names is in *text*, one-thing and many-things forms alike
    ("chore" names "Chores")."""
    if _names_hit(text, names):
        return True
    words = [_fold(w) for w in _WORD_RE.findall(_plain(text).lower())]
    joined = " " + " ".join(words) + " "
    for name in names or ():
        parts = [_fold(w) for w in _WORD_RE.findall(str(name or "").lower())]
        if parts and len("".join(parts)) >= 3 and (" " + " ".join(parts) + " ") in joined:
            return True
    return False


#: The possessive "'s" ("Biscuit's big day") is not "is": a contraction only
#: before these.
_T_CONTRACTION = re.compile(
    r"(?ix)'(?:s|re)\s+(?:in|on|out|off|up|down|at|a|an|the|now|all|been|got|done|set|good|"
    r"ready|no|yes|not|so|very|really|officially|already|also|still|just|definitely|"
    r"going|coming|there|here|gone|free|open|closed|full|empty|next|first|last|"
    r"[a-z]{2,}ed|[a-z]{2,}en)\b|'(?:s|re)\s+[-+$£€]?\d|'(?:s|re)\s*[!.]*\s*$|'(?:ve|ll)\b")
#: Words a record's status is said with. Broad on purpose: anything said to
#: BE so about her thing.
_T_PLAIN_VERBS = re.compile(
    r"(?ix)\b(?:is|are|was|were|been|being|has|had|got|gets|getting|holds|shows|"
    r"showing|lists|listing|includes|contains|reads|sits|sitting|stands|joins|"
    r"joined|appears|counts|counted|earns|earned|receives|received|now|went|goes|"
    r"became|becomes|takes|took|makes|made|brings|brought|puts|leaves|left|"
    r"falls|fell|rises|rose|drops|dropped|will\s+be|have|says|said|"
    r"states|notes|mentions|indicates|marks|keeps|stays|remains|totals|equals)\b")
#: Past participles that do not end in -ed.
_T_IRREGULAR = frozenset(
    "done gone set put cut struck taken written made sold paid bought won lost kept left "
    "held sent met seen begun drawn grown shown known thrown sorted chosen given hidden "
    "shut split spent stuck swept torn worn broken frozen stolen sworn fed read".split())
#: Praise, feelings and opinions: "is a classic", "is so much fun", "is lovely".
_T_PRAISE = re.compile(
    r"(?ix)\b(?:great|good|nice|lovely|wonderful|fantastic|amazing|awesome|fun|delicious|"
    r"tasty|yummy|beautiful|perfect|classic|favou?rite|excellent|brilliant|sweet|cute|"
    r"special|exciting|excited|important|healthy|popular|easy|hard|tough|lucky|welcome|"
    r"gripping|impressive|cozy|cosy|happy|proud|sad|shame|pity|bummer|best|worst|hit|joy|"
    r"pleasure|blessing|treat|news|gem|keeper|win|milestone|achievement|relief|thrilled|"
    r"glad|sorry|thoughtful|kind|generous|smart|clever|funny|brave|strong|busy|cool|"
    r"interesting|beloved|famous|well[\s-]known|fascinating|inspiring|timeless|must[\s-]read|"
    r"page[\s-]turner|masterpiece|lifesaver|life[\s-]saver|helpful|useful|worth\s+it|"
    r"fast|quick|speedy|incredible|unbelievable|remarkable|wild|crazy|huge|tiny|adorable|"
    r"magical|delightful|precious|perfect|hilarious|epic|legendary|stellar|superb|"
    r"terrific|marvel(?:l)?ous|splendid|gorgeous|stunning|charming|relaxing|tricky|"
    r"stressful|exhausting|frustrating|annoying|scary|nerve[\s-]wracking|normal|common|"
    r"natural|understandable|real|rewarding|satisfying|fulfilling|enjoyable|refreshing|"
    r"meaningful|memorable|peaceful|productive|smart\s+move|good\s+idea|great\s+idea|"
    r"handy|versatile|essential|staple|go-?to|so\s+many|lots\s+of|plenty\s+of|"
    r"unclear|confusing|ambiguous|vague|a\s+bit\s+\w+|a\s+little\s+\w+|"
    # a describing word with an intensifier or an article and a noun: "such
    # a captivating story", "so delicious", "a gripping read"
    r"(?:such\s+an?|so|very|really|truly|quite|super|pretty|too)\s+"
    r"(?![a-z]+(?:ed|en|ing)\b)(?!(?:set|done|gone|in|on|out|off|booked|full)\b)[a-z]{3,}|"
    r"an?\s+[a-z]{3,}(?:ing|ful|ous|ive|able|ible|ic)\s+[a-z]+|"
    r"such\s+a|so\s+much|one\s+of|kind\s+of|sort\s+of)\b")
#: Seeing, feeling, guessing: "sounds like", "must be", "will love it".
_T_FEEL = re.compile(
    r"(?ix)\b(?:sounds?|looks?|seems?|feels?|smells?|tastes?|must\s+(?:be|feel|have)|"
    r"will\s+(?:love|enjoy|like|have\s+fun|be\s+(?:so\s+|very\s+|really\s+)?(?:glad|happy|"
    r"thrilled|excited|missed|fine|ok|okay|great|lovely|wonderful))|would\s+love|"
    r"(?:might|may|could|would|can|should)\b|deserves?|hopes?|wish(?:es)?|loves?|enjoys?|"
    r"likes?|misses|bet|guess|imagine|suppose)\b")
#: A trip or a plan, said back to her: "Nana is coming", "Leo's going too".
_T_TRIP = re.compile(
    r"(?ix)(?:\b(?:is|are)|'s|'re)\s+(?:also\s+|really\s+|finally\s+)?(?:coming|going|"
    r"visiting|travell?ing|flying|driving|staying|heading|arriving|making\s+the\s+trip)\b")
#: What makes a guess or praise a claim after all: "now", "officially",
#: "updated", placed on her list.
_T_STILL_CLAIM = re.compile(
    r"(?ix)\b(?:now|already|officially|updated|reflects?|recorded|logged|saved|marked|"
    r"added|removed|listed|confirmed|registered|checked\s+off|ticked)\b")
#: A place she named: "Leo is in Paris", "is at Grandma's".
_T_PLACE = re.compile(r"(?:\b(?:is|are)|'s|'re)\s+(?:in|at|near|from)\s+(?P<place>[A-Z][\w'-]+)")
#: "is where", "is when": an explanation, not a status.
_T_EXPLAIN = re.compile(r"(?ix)(?:\b(?:is|are|was)|'s)\s+(?:where|when|why|how|what|who)\b")
#: A line that is a greeting or a cheer, whatever follows.
_T_CHEER = frozenset(
    "oh ah aw aww yay yey wow woo woohoo hooray hurray congrats congratulations welcome hi "
    "hello hey thanks thank great nice awesome amazing lovely wonderful good well what how "
    "so such enjoy sorry bummer cheers bravo love happy lucky poor dear go way proud yum "
    "yummy exciting fun cool sweet brilliant fantastic perfect excellent phew aha haha "
    "hmm ooh ouch uh oops".split())
#: A line that says it is handled, with no thing named: "Got it!", "Noted.".
_T_DONE_LINE = re.compile(
    r"(?ix)^\s*(?:got\s+it|noted|on\s+it|will\s+do|sorted|done|all\s+done|all\s+set|"
    r"you\s+got\s+it|you\s+bet|consider\s+it\s+done|it'?s\s+done|that'?s\s+done|"
    r"(?:all\s+)?taken\s+care\s+of|all\s+good|good\s+to\s+go|in\s+the\s+books|"
    r"there\s+you\s+go|voila|boom|check|you'?re\s+(?:all\s+)?set|you'?re\s+good\s+to\s+go|"
    r"that'?s\s+(?:sorted|handled|taken\s+care\s+of)|it'?s\s+(?:sorted|handled))\s*[!.]*\s*$")
#: A colon line that is a tip or a note, not a status: "Tip: water deeply."
_T_TIP = re.compile(r"(?ix)^\s*(?:pro\s+)?(?:tip|fun\s+fact|note|reminder|idea|bonus|p\.?s|"
                    r"fyi|hint|heads\s+up|quick\s+tip|suggestion|question|recipe|step\s+\d+)"
                    r"\s*:")
#: Who did it, when the subject is a person: "You planted tomatoes!".
_T_PERSON_DID = re.compile(r"(?i)\b(?:you|i|we|they|he|she)\s+(?:just\s+|really\s+|all\s+)?"
                           r"[a-z]{3,}ed\b")
_T_YOUR_THING_DONE = re.compile(
    r"(?ix)\b(?:your|the|my|our)\s+(?:[\w'-]+\s+){0,3}?(?:is|are|was|were|'s|'re|"
    r"has\s+been|have\s+been|got|gets)\s+(?:now\s+|all\s+|already\s+|officially\s+|just\s+)*"
    r"(?:saved|logged|recorded|added|removed|deleted|updated|marked|noted|entered|stored|"
    r"filed|tracked|checked\s+off|ticked(?:\s+off)?|crossed\s+off|listed|registered|"
    r"scheduled|booked|cancell?ed|archived|pinned|flagged|tagged|starred|synced|in\s+the\s+books)\b")
_T_COUNT = re.compile(r"(?i)\b(?:no|one|two|three|a\s+few|\d+)\s+(?:more|less|fewer)\b")
_T_CLAUSES = re.compile(
    r"\s*(?:[,;]|\s[-–—]\s|[–—]|\b(?:so|but|and)\b(?=\s+(?:the|your|my|our|he|she|they|it|we|i|"
    r"you|that|there|this|now|is|are|was|were|has|have|had|got|gets|will|now)\b|\s+(?-i:[A-Z]))|"
    r"\band\s+then\b)\s*", re.I)
#: A clause that starts with its verb ("... and is attending"): its subject
#: is the clause before.
_T_VERB_FIRST = re.compile(r"(?i)^(?:is|are|was|were|has|have|had|got|gets|will|now)\b")
_T_LEAD_PRONOUN = re.compile(r"(?i)^(?:he|she|they|it|that|this|everyone|everything|both|"
                             r"there|all|them|his|her|their|its)\b")


#: A list word that means HER list: "the guest list", "your chores", "all
#: chores", "Guest list:" (not "a rewarding task", "a list of ideas").
_T_THING_HERS = re.compile(
    r"(?ix)(?:\b(?:the|your|my|our|his|her|their|this|that|all|every|no|any)\s+"
    r"(?:[\w'-]+\s+){0,2}?|'s\s+|^\s*)" + _THING_WORD.pattern.replace("(?ix)", "", 1))
#: "I'm glad to hear the dishes are done!": a feeling about what she said.
_T_FEELING_ECHO = re.compile(
    r"(?ix)^\W*(?:(?:oh|wow|great|yay|aw+|ah)\W+)?(?:I'?m|I\s+am|that'?s|it'?s|so|how)\s+"
    r"(?:so\s+|really\s+|very\s+|super\s+|truly\s+)?(?:glad|happy|pleased|thrilled|great|good|"
    r"nice|wonderful|lovely|sorry|sad|excited|relieved|proud|amazing|awesome)\s+"
    r"(?:to\s+(?:hear|see|know|read|learn)|that|you|about|for)\b")


def _t_anchor_words(asked: Any) -> set[str]:
    """Her message's words that can name a thing, in their one-thing form."""
    return {_fold(w) for w in _item_words(asked)}


def _t_anchored(clause: str, anchors: set[str], names: Any, *, short: bool) -> bool:
    """*clause* is about her thing."""
    if _folded_words(clause) & anchors:
        return True
    if _names_hit_folded(clause, names) or _T_THING_HERS.search(clause):
        return True
    return short and bool(_T_LEAD_PRONOUN.match(clause.strip()))


def _t_asserts(clause: str) -> bool:
    """*clause* says something IS so (a state), not what to do or how it feels."""
    body = clause.strip()
    if _T_PLAIN_VERBS.search(body) or _T_CONTRACTION.search(body) or _T_COUNT.search(body):
        return True
    words = _WORD_RE.findall(body.lower())
    if not words:
        return False
    # The bot telling what it is doing: "Adding June to Big Day", "Marking the
    # dishes done", "Updating the guest list".
    if re.match(r"(?i)[a-z]{3,}ing\b", body) and (
            re.match(r"(?i)(?:" + _DEED_WORDS + r")\b", body)
            or re.search(r"(?i)\b(?:to|onto|into|on|in|from|off|as|under)\b", body)):
        return True
    # A participle as the verb: "Kicked Leo off", "Tomatoes, planted today",
    # "Dune shelved". Not a person's own deed ("You planted tomatoes!"), not
    # "wanted to come", not "changed my life".
    for m in re.finditer(r"(?i)\b([a-z]{3,}ed|" + "|".join(sorted(_T_IRREGULAR)) + r")\b", body):
        before = body[:m.start()]
        if _T_PERSON_DID.search(body[:m.end()]) and not re.search(r"(?i)'s\s*$|\bis\s*$", before):
            continue
        after = body[m.end():]
        if re.match(r"(?i)\s+(?:to\s+(?:be|go|come|see|do|have|get|make|know|meet|say|try|"
                    r"help|join|attend|visit|stay|read|play|win|hear|love|like|keep|find|leave|"
                    r"take)\b|(?:my|your|our|his|her|their|me|us|you|him|them|a|an)\b)", after):
            continue
        return True
    return False


def _t_harmless(clause: str, names: Any) -> bool:
    """*clause* has a shape that says nothing about her records."""
    body = clause.strip()
    if not body or body.endswith("?") or _DISOWN.search(body) or _NOT_A_CLAIM.search(body):
        return True
    thing = bool(_ON_THING_RE.search(body) or _THING_WORD.search(body)
                 or _names_hit_folded(body, names))
    # About HER, not her records: "You're a tax pro now", "you did great".
    # A plan to do something together: "let's start planning" (a "let's add
    # ..." is a claim by the older rules, which run first).
    if re.match(r"(?i)(?:let's|lets|let\s+us)\b", body) or (
            re.match(r"(?i)(?:you|you're|youre|you've|you'll)\b", body) and not thing):
        return True
    if _T_STILL_CLAIM.search(body) or _ON_THING_RE.search(body):
        return False
    if _T_PRAISE.search(body) or _T_FEEL.search(body) or _T_TRIP.search(body):
        return True
    if _T_EXPLAIN.search(body):
        return True
    m = _T_PLACE.search(body)
    if m and not (_THING_WORD.search(m.group("place"))
                  or _names_hit_folded(m.group("place"), names)):
        return True
    return False


#: Small words a cheer is built with: "Way to go", "the best of luck".
_T_SMALL = frozenset("to of a an at my me you your her his our its that's thats it's "
                     "what how".split())
#: Words that are a record's status on their own: "Leo: yes.", "Rent: paid."
_T_STATUS_WORDS = frozenset(
    "yes no in out off on done set ok okay maybe next first last full paid due open "
    "closed free booked back up down now there".split())


def _t_cheer_only(text: str, anchors: set[str], names: Any, *, needs_cheer: bool = False) -> bool:
    """Only a cheer and her thing's name: "Yay, Leo Park!", "Poor Biscuit!".
    With *needs_cheer*, a cheer word must be there: after a colon, her
    thing's name alone is a status ("Ava's cake: unicorn.")."""
    words = [w for w in _WORD_RE.findall(text.lower())]
    if any(w in _T_STATUS_WORDS or re.fullmatch(r"\d+", w) for w in words):
        return False
    if needs_cheer and not any(w in _T_CHEER or _T_PRAISE.search(w) for w in words):
        return False
    rest = [w for w in words if _fold(w) not in anchors and w not in _T_CHEER
            and w not in _STOP and w not in _T_SMALL and not _names_hit_folded(w, names)]
    return bool(words) and not rest and not _T_DONE_LINE.match(text)


def _record_state_claim(body: str, asked: Any, names: Any = ()) -> bool:
    """After a statement turn: a reply sentence that says her thing IS in some
    state, failing closed (see the section note above)."""
    text = _plain(body).strip()
    if not text or text.endswith("?") or _ABOUT_THE_REPLY.search(text):
        return False
    anchors = _t_anchor_words(asked)
    words = _WORD_RE.findall(text.lower())
    if not words:
        return False
    # "Got it!", "Noted.", "All taken care of.": handled, with nothing named.
    if _T_DONE_LINE.match(text):
        return True
    # "Your run is saved.", "The entry has been updated.": a record word said
    # to be saved or changed needs no anchor.
    if _T_YOUR_THING_DONE.search(text) and not _DISOWN.search(text):
        return True
    # A bare deed: "Scrapped!", "Starred.", "Shots recorded!", "Nothing
    # checked out anymore."
    if (len(words) <= 4 and not _t_cheer_only(text, anchors, names) and not _T_TIP.match(text)
            and not _T_PERSON_DID.search(text)
            and not (len(words) == 4 and _t_harmless(text, names))):
        if any(re.fullmatch(r"[a-z]{3,}ed", w) or w in _T_IRREGULAR for w in words):
            return True
    # A status line: "Dune: finished.", "Headcount: 43.", "Next up in Book Club: Emma."
    if ":" in text and not _T_TIP.match(text):
        lead, _, tail = text.partition(":")
        if (tail.strip() and len(lead.split()) <= 6 and len(_WORD_RE.findall(tail)) <= 8
                and not _t_cheer_only(tail, anchors, names, needs_cheer=True)
                and not _t_harmless(tail, names)):
            return True
    # An amount or a time given to her thing, with no verb: "$5 to Jonah.",
    # "Vet visit for Rex, today.", "5 miles, in the tracker!"
    if len(words) <= 8 and not _T_PLAIN_VERBS.search(text) and not _T_CONTRACTION.search(text) \
            and not any(_ASK_DEED.match(c.strip()) for c in _T_CLAUSES.split(text)) \
            and _t_anchored(
            text, anchors, names, short=False) and not _t_harmless(text, names) and (
            re.search(r"(?i)(?:^|\s)[-+$£€]?\d[\d.,]*\s*(?:[a-z]+\s+)?(?:to|for|in|on|off)\b", text)
            or _ON_THING_RE.search(text)
            or re.search(r"(?i)\b(?:today|tonight|tomorrow|this\s+(?:week|month|morning|"
                         r"evening)|next\s+\w+)\s*[.!]*\s*$", text) and "," in text):
        return True
    prev_anchored = False
    for clause in _T_CLAUSES.split(text.replace(":", ",")):
        clause = clause.strip(" .!")
        if not clause:
            continue
        # "Got it, Sam can have the window seat!": the "got it" says it is handled.
        if _T_DONE_LINE.match(clause):
            return True
        n = len(_WORD_RE.findall(clause))
        anchored = _t_anchored(clause, anchors, names, short=n <= 7) or (
            prev_anchored and bool(_T_VERB_FIRST.match(clause)))
        prev_anchored = anchored
        if not anchored:
            continue
        if _t_cheer_only(clause, anchors, names) or _t_harmless(clause, names):
            continue
        if _t_asserts(clause):
            return True
    return False


#: A question back to her ("Could you tell me what was cancelled?", "Were
#: there plans that were cancelled?"): it asks, it does not say.
_ASKS_HER = re.compile(
    r"(?ix)^\W*(?:(?:sorry|so|and|but|oh|hmm|okay|ok|also)\W+)*(?:could|can|would|will|do|does|"
    r"did|is|are|was|were|should|shall|may|what|how|when|where|who|which|why|any|have|has|"
    r"had)\b[^.!\n]*\?\s*$")
#: Words that name a change made (not "now", which a question uses freely).
_T_CHANGED_WORD = re.compile(
    r"(?ix)\b(?:updated|reflects?|recorded|logged|saved|marked|added|removed|listed|"
    r"confirmed|registered|checked\s+off|ticked|deleted|booked|scheduled|entered|noted)\b")
_CHECK_MARKS = re.compile("[✓✔✅☑\U0001f5f8]️?")
_QUOTED = re.compile(r"[\"“]([^\"”\n]{2,200})(?:[\"”]|$)")


def _unquote_hers(body: str, asked: Any) -> str:
    """*body* with every quoted span that is her own message (most of its
    words hers) read as "that"."""
    mine = {w for w in _WORD_RE.findall(_plain(asked).lower())}
    if not mine:
        return body

    def swap(m: re.Match) -> str:
        words = _WORD_RE.findall(m.group(1).lower())
        if words and sum(w in mine for w in words) >= max(1, int(0.8 * len(words))):
            return "that"
        return m.group(0)

    return _QUOTED.sub(swap, body)


# -- on EVERY turn -----------------------------------------------------------------
#
# The rules above ran their fail-closed half only after a statement or a
# change ask. After a question or small talk ("what should we cook tonight?",
# "thanks!") a model can still say it changed her things: "Tacos! I also
# popped tortillas onto your Grocery list.", "Leo Park is on the guest list
# now.", "I've made a note of it." These rules run whatever she asked. They
# read the SHAPE of a change, not its verb (any verb, placed on her thing),
# so a verb nobody listed still counts. What they leave alone: advice
# ("you could add it to your list"), a guess ("might", "should"), a
# question back, a general fact ("is usually on the guest list").

#: A thing of hers: a list word said as hers or as "the" one.
_HER_THING = (r"(?:(?:the|your|my|our|his|her|their|this|that)\s+(?:[\w'-]+\s+){0,3}?"
              r"(?:" + _THINGS[3:-1] + r"|groceries|grocery|shopping|guests?|chores?|to-?dos?|"
              r"tasks?|roster|setlist|playlist|shelf|shelves|headcount)"
              r"|(?-i:[A-Z])[\w'-]*\s+(?:(?-i:[A-Z])[\w'-]*\s+){0,2}(?:" + _THINGS[3:-1]
              + r"))\b")
_INTO_PREP = r"(?:to|onto|into|on|in|from|off(?:\s+of)?|out\s+of|under|for)"
#: Advice, a guess, an offer or a general fact: not a claim.
_ADVICE = re.compile(
    r"(?ix)\b(?:could|might|(?-i:may)|would|should|shall|can|maybe|perhaps|suggest\w*|"
    r"recommend\w*|consider|try|remember\s+to|don'?t\s+forget|you'?ll\s+want|"
    r"usually|typically|generally|normally|often|commonly|traditionally|sometimes|rarely|"
    r"tends?\s+to|if|whether|once|when\s+you|want\s+(?:me|to)|like\s+me|how\s+about|"
    r"what\s+about|'d)\b")
#: The words that make advice a claim after all.
_NOW_WORDS = re.compile(r"(?ix)\b(?:now|already|officially|all\s+set|updated)\b")
#: Negation in the languages a household is likely to use.
_ANY_NEG = re.compile(r"(?ix)\b(?:not|no|never|n't|pas|nicht|kein\w*|não|nao|non|nunca|jamais)\b"
                      r"|n't\b")

#: I or we did something that puts a thing on, in or off her thing (any verb).
_I_PLACED = re.compile(
    r"(?ix)\b(?:I|we)(?:'ve|'m|'re|'ll|\s+have|\s+had|\s+am|\s+are|\s+will)?"
    r"(?:\s+(?:just|also|already|now|then|quickly|successfully|gone\s+ahead\s+and|"
    r"went\s+ahead\s+and|went\s+and|go\s+ahead\s+and|going\s+to|gonna))*"
    r"\s+[a-z]+(?:\s+(?!(?:at|about|like|through|over|with)\b)[\w'-]+){0,6}?\s+"
    + _INTO_PREP + r"\s+" + _HER_THING)
#: I or we recorded it: a deed that is a record on its own.
_I_RECORDED = re.compile(
    r"(?ix)\b(?:I|we)(?:'ve|'ll|'m|\s+have|\s+had|\s+will|\s+am)?"
    r"(?:\s+(?:just|also|already|now|then|quickly|successfully|gone\s+ahead\s+and|"
    r"went\s+ahead\s+and|going\s+to|gonna))*\s+"
    r"(?:(?:made|make|making|taken|took|take|taking)\s+(?:a\s+)?(?:quick\s+|mental\s+)?note|"
    r"(?:kept|keep|keeping)\s+(?:track|tabs|a\s+record|a\s+note)|"
    r"(?:noted|jotted|logged|recorded|saved|stored|filed|remembered|memori[sz]ed|"
    r"pencill?ed|tallied|registered|entered|ticked|checked|crossed|starred|flagged|"
    r"added|put|popped|stuck|slotted|marked|counted|booked|scheduled|"
    r"note|jot|log|record|save|store|file|remember|memori[sz]e|tally|register|tick|"
    r"add|pop|stick|slot|mark|count|book|schedule|"
    r"(?:wr(?:ote|itten|ite)|got|get|put|jotted|noted)\s+(?:it|that|this|them|him|her)\s+down)"
    r")\b(?P<rest>[^.!?\n]*)")
#: What follows "I noted": a pronoun, a name, nothing, or a particle at the end.
_REC_REST_OK = re.compile(r"(?i)^\s*(?:(?:it|that|this|them|him|her|down|of\s+(?:it|that|this|"
                          r"them|him|her)|each|both|everything|all|as|in|on)\b|[.!]*\s*$)")
#: What follows "I added" that is text work: "a few ideas", "you a seat", "below".
_REC_REST_TEXT = re.compile(
    r"(?ix)^\s*(?:a|an|some|you|your|the\s+following|below|here|more|extra|another|"
    r"a\s+few|details?|notes?\s+(?:below|above)|to\s+(?:the|this|my)\s+(?:reply|answer|"
    r"recipe|message|plan|draft|story|poem))\b")
#: Her thing said to be on, in or off one of her things, or in a new state
#: there: "Leo Park is on the guest list now", "Eggs are on your grocery list".
_IS_PLACED = re.compile(
    r"(?ix)(?:\b(?:is|are|was|were|be|been|got|gets|getting|sits?|stays?)|'s|'re)\s+"
    r"(?:now\s+|all\s+|already\s+|officially\s+|also\s+|safely\s+|right\s+|just\s+)*"
    r"(?:[a-z]+ed\s+|put\s+|set\s+|stuck\s+|down\s+)?" + _INTO_PREP + r"\s+" + _HER_THING)
#: A diff line: "+ eggs (Grocery list)", "+ Leo Park".
_PLUS_LINE = re.compile(r"^\s*(?:\+|➕)\s*(?!\d)[A-Za-z]")
#: A tally after a statement: "One for Sam.", "+1 Sam", "Another point to Mia".
_TALLY = re.compile(
    r"(?x)^\W*(?i:another|one|two|three|four|five|a|\+\s*\d+|\d+)\s+"
    r"(?:(?i:more|extra|fewer|less)\s+)?(?:(?i:points?|stars?|ticks?|checks?|marks?|pts?|"
    r"gold\s+stars?)\s+)?(?:(?i:for|to)\s+)?[A-Z][a-z]")
#: A count said as changed: "One fewer guest", "now 41", "down to 41".
_COUNT_CHANGE = re.compile(
    r"(?ix)\b(?:one|two|three|four|five|a\s+few|\d+)\s+(?:more|fewer|less)\s+"
    r"(?!(?:thing|things|reason|reasons|step|steps|worry|worries|chance|try|go|round)\b)"
    r"[a-z]+(?!\s+than\b)(?!\s+(?:minutes?|hours?|days?|weeks?|times?|years?)\b)"
    r"|(?:^|,)\s*(?:and\s+)?(?:now|that\s+makes|making\s+it|bringing\s+it\s+to|"
    r"down\s+to|up\s+to)\s+(?:at\s+)?[-+$£€]?\d+(?:\s+[a-z]+)?\s*[.!]*\s*$")
#: A short done word in other languages: "¡Listo!", "C'est noté.", "Erledigt!".
_DONE_ELSEWHERE = re.compile(
    r"(?ix)^\W*(?:listo|hecho|anotado|apuntado|añadido|agregado|c'?est\s+(?:fait|noté)|"
    r"noté|ajouté|fait|erledigt|notiert|eingetragen|gemacht|feito|pronto|anotado|"
    r"adicionado|fatto|segnato|aggiunto|fertig)\W*$")
#: A change said done in other languages (Spanish, French, German,
#: Portuguese, Italian). A list of words, so it fails OPEN for any other
#: language.
_DEED_ELSEWHERE = re.compile(
    r"(?ix)\b(?:añad[íi]|agregu[ée]|anot[ée]|apunt[ée]|puse|quit[ée]|elimin[ée]|borr[ée]|"
    r"marqu[ée]|guard[ée]|tach[ée]|(?:he|hemos|ha)\s+(?:añadido|agregado|anotado|apuntado|"
    r"puesto|quitado|eliminado|borrado|marcado|guardado)|"
    r"(?:j'ai|nous\s+avons|a\s+été|ont\s+été|est)\s+(?:bien\s+)?(?:ajouté|noté|mis|supprimé|"
    r"retiré|enlevé|coché|enregistré|inscrit|rayé)e?s?|"
    r"(?:habe|haben|wurde|wurden|ist|sind)\s+(?:\w+\s+){0,4}?(?:hinzugefügt|eingetragen|"
    r"notiert|gelöscht|entfernt|gespeichert|abgehakt|gestrichen|gesetzt)|"
    r"hinzugefügt|eingetragen|abgehakt|"
    r"adicionei|anotei|coloquei|removi|apaguei|marquei|salvei|risquei|"
    r"(?:ho|abbiamo|è\s+stat[oa]|sono\s+stat[ie])\s+(?:aggiunt[oaie]|segnat[oaie]|"
    r"mess[oaie]|tolt[oaie]|rimoss[oaie]|cancellat[oaie]|salvat[oaie]|spuntat[oaie]))")
_LIST_ELSEWHERE = re.compile(r"(?i)\b(?:lista|listas|liste|listen|lijst|elenco|compra|"
                             r"courses|einkauf\w*|tareas|tâches|aufgaben)\b")


#: "Your grocery list: eggs, milk." (not "A good grocery list: ...", advice).
_HER_LIST_SHOWN = re.compile(
    r"(?i)(?:^|[.!]\s+|^\W*\w+[!,.]\s+)(?:your|my|our|the\s+current|the\s+updated)\s+"
    r"(?:[\w'-]+\s+){0,3}?" + _THINGS + r"\s*(?:now\s+)?(?:reads\s*)?:\s*\S")
#: A status word alone after a colon: "Tortillas: sorted.", "Leo: added!".
_STATUS_AFTER_COLON = re.compile(
    r"(?i)^\W*(?:[\w'-]+\s+){0,4}[\w'-]+\s*:\s*(?:all\s+)?(?:sorted|done|added|booked|"
    r"confirmed|logged|noted|ticked(?:\s+off)?|checked(?:\s+off)?|crossed\s+off|removed|"
    r"gone|saved|updated|in|on|off|out|covered|handled|scheduled|locked\s+in|"
    r"pencill?ed\s+in|listed|marked|invited|uninvited)\s*[.!]*\s*$")
#: "Leo and Mia are in.", "Leo's out!": named people said to be in or out.
_NAMES_IN = re.compile(
    r"(?:^|[,.!]\s+|^\W*\w+[,!]\s+)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"
    # one name with "is" ("Leo is out"), or names joined ("Leo and Mia are
    # in"); one capital word with "are" is a thing ("Tomatoes are in").
    r"(?:(?:\s+is|'s)|(?:(?:,\s*|\s+and\s+|\s*&\s*)[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)+"
    r"(?:\s+are|'re))\s+(?:now\s+|all\s+|officially\s+|definitely\s+)?"
    r"(?:in|out|on|off)\s*[.!]*\s*$")


def _any_turn_claim(body: str, asked: Any, names: Any) -> bool:
    """A reply sentence that says one of her things changed, whatever she
    asked (a question and small talk included)."""
    text = body.strip()
    if not text or text.endswith("?"):
        return False
    advice = _ADVICE.search(text) and not _NOW_WORDS.search(text)
    negated = bool(_NOT_A_CLAIM.search(text) or _DISOWN.search(text))
    if not negated and not advice:
        if _I_PLACED.search(text) or _IS_PLACED.search(text):
            return True
        # ("Let's use Big Day for the guest list" is a plan, not "is".)
        if names and re.search(r"(?i)(?:\b(?:I|we)\b|\b(?:is|are)\s+(?:now\s+)?|"
                               r"(?<!\blet)(?<!\bthat)(?<!\bwhat)(?<!\bhere)(?<!\bthere)"
                               r"'s\s+(?:now\s+)?)"
                               r"[^.!?\n]{0,80}?\b" + _INTO_PREP + r"\s+(?:the\s+)?(?:"
                               + "|".join(re.escape(str(n).strip()) for n in names
                                          if len(str(n).strip()) >= 3) + r")\b", text):
            return True
    if not negated:
        m = _I_RECORDED.search(text)
        if m and not (_ADVICE.search(text[:m.start()]) or re.search(
                r"(?i)\b(?:would|could|might|should|can|shall|want\s+me|like\s+me)\b",
                text[:m.end()])):
            rest = m.group("rest")
            if _REC_REST_OK.match(rest) or (
                    not _REC_REST_TEXT.match(rest)
                    and (re.search(r"\s(?-i:[A-Z])[a-z]", rest)
                         or _names_hit_folded(rest, names)
                         or re.search(r"(?i)\b" + _INTO_PREP + r"\s", rest)
                         or len(_WORD_RE.findall(rest)) <= 3)):
                return True
    if not negated:
        # "Your grocery list: eggs, milk, tortillas.": her list shown as it
        # stands, which only a run could know.
        if _HER_LIST_SHOWN.search(text) or (
                names and ":" in text
                and _names_hit_folded(text.split(":")[0][-40:], names)
                and len(text.split(":")[0].split()) <= 6
                and not re.match(r"(?i)\W*(?:a|an|some|any|every|each|one|good|great|"
                                 r"typical|perfect|ideal|sample|example|basic|simple)\b",
                                 text.split(":")[0])):
            return True
        # "Tortillas: sorted.", "Leo: added!": a status after a colon.
        if _STATUS_AFTER_COLON.search(text) and not _T_TIP.match(text):
            return True
        # "Leo and Mia are in.": named people said to be in or out.
        if _NAMES_IN.search(text):
            return True
    if _PLUS_LINE.match(text) and (
            _THING_WORD.search(text) or _names_hit_folded(text, names)
            or _folded_words(text[1:]) & _t_anchor_words(asked)):
        return True
    if not _ANY_NEG.search(text):
        if _DONE_ELSEWHERE.match(text):
            return True
        if _DEED_ELSEWHERE.search(text) and (
                _LIST_ELSEWHERE.search(text) or _THING_WORD.search(text)
                or _names_hit_folded(text, names)
                or _folded_words(text) & _t_anchor_words(asked)
                or _DEED_ELSEWHERE.search(_plain(asked))):
            return True
        # "¡Listo! Añadí huevos a tu lista.": a deed with nothing else to it.
        if _DEED_ELSEWHERE.match(text) or re.match(
                r"(?i)^\W*(?:listo|hecho)\W", text) and _DEED_ELSEWHERE.search(text):
            return True
    return False


def _statement_tally(body: str) -> bool:
    """After a statement: "Tick!", "One for Sam.", "One fewer guest, now 41."."""
    text = _plain(body).strip()
    if not text or text.endswith("?") or _DISOWN.search(text):
        return False
    if re.match(r"(?i)^\W*(?:tick(?:ed)?(?:\s+off)?|check(?:ed)?(?:\s+off)?|ding|bam|"
                r"logged|jotted|counted|scored|booked|locked\s+in|added|marked|"
                r"in\s+it\s+goes|on\s+(?:the|your)\s+list)\W*$", text):
        return True
    return bool(_TALLY.match(text) or _COUNT_CHANGE.search(text))


# -- a report that a change is DONE, on every turn -----------------------------------
#
# The rules above are keyed to what she asked: after a question or small talk
# they let through "Removed Leo from Big Day.", "Added!", "Consider it done.",
# "The item was successfully added." and "Dishes checked off for Sam.". A
# report that a change is done has a small grammar whatever the verb's object:
# I or we did it (or will do it now), it has been / was / is now done, a
# participle standing alone ("Eggs added.", "Removed Leo ..."), a done phrase
# ("Consider it done", "All set"), a count said to be new, a status after a
# colon or an arrow. These rules read that grammar on every turn, whatever she
# asked. They leave alone: a hedge ("once you add eggs", "if you want, I'll"),
# a question back, a negation ("I haven't added anything"), text work in this
# chat ("I added a pinch of humor to that toast"), a general fact ("most
# weddings have ..."), history ("was added in 1998"), advice.

#: Change verbs said as done. STRONG ones are a report on their own; WEAK ones
#: have everyday meanings ("the team moved to Denver") and need an anchor.
_DONE_STRONG = (
    r"added|removed|deleted|erased|updated|saved|logged|marked|jotted(?:\s+down)?|"
    r"noted(?:\s+down)?|checked\s+off|ticked(?:\s+off)?|crossed\s+(?:off|out)|"
    r"struck\s+(?:off|out|through)|scratched\s+(?:off|out)|assigned|reassigned|unassigned|"
    r"uninvited|disinvited|booked|scheduled|rescheduled|written\s+down|wrote\s+down|"
    r"taken\s+care\s+of|took\s+care\s+of|taken\s+(?:off|out)|pencill?ed\s+in|"
    r"popped\s+(?:on|in|onto|into)|slotted\s+in|starred|flagged|tallied|cancell?ed|"
    r"logged|synced|processed|queued|archived|appended|inserted|signed\s+up|enrolled|"
    r"refreshed|gone\s+(?:from|off)")
_DONE_WEAK = (
    r"recorded|stored|filed|registered|entered|renamed|put\s+(?:down|in|on)|placed|swapped|"
    r"moved|invited|included|tracked|reserved|confirmed|completed|changed|created|"
    r"replaced|set\s+up|listed|counted|dropped|bumped|edited|adjusted|modified|restored|"
    r"stocked|sorted|handled|applied|submitted|put")
_DONE_VERB = r"(?:" + _DONE_STRONG + r"|" + _DONE_WEAK + r")"
_DONE_STRONG_RE = re.compile(r"(?i)^(?:" + _DONE_STRONG + r")\b")
#: The same verbs as she would ask for them, for "I'll add ..." and "I'm adding ...".
_DO_BASE = (
    r"add|remove|delete|erase|update|save|log|mark|jot|note\s+(?:\S+\s+){0,3}?down|"
    r"check\s+(?:\S+\s+){0,4}?off|tick|cross\s+(?:\S+\s+){0,4}?off|assign|reassign|"
    r"uninvite|invite|book|schedule|reschedule|write\s+(?:\S+\s+){0,4}?down|"
    r"take\s+care\s+of|take\s+(?:\S+\s+){0,4}?(?:off|out)|pencil|pop|slot|star|flag|"
    r"cancel|record|store|file|register|enter|rename|put|place|swap|move|include|track|"
    r"reserve|list|count|drop|bump|stick|get\s+(?:\S+\s+){0,3}?(?:on|in|off|added|down)")
_DO_ING = (
    r"adding|removing|deleting|erasing|updating|saving|logging|marking|jotting|noting|"
    r"checking\s+(?:\S+\s+){0,4}?off|ticking|crossing\s+(?:\S+\s+){0,4}?off|assigning|"
    r"uninviting|inviting|booking|scheduling|writing\s+(?:\S+\s+){0,4}?down|"
    r"taking\s+care\s+of|taking\s+(?:\S+\s+){0,4}?(?:off|out)|popping|slotting|putting|"
    r"placing|recording|moving|including|tracking|entering|cancell?ing|getting\s+"
    r"(?:\S+\s+){0,3}?(?:on|in|off|added|down)|sticking|filing|storing")
_ADV = (r"(?:just|already|now|also|then|quickly|successfully|officially|all|right|"
        r"safely|gone\s+ahead\s+and|went\s+ahead\s+and|went\s+and|go\s+ahead\s+and|"
        r"went\s+on\s+and|gone\s+and|gone\s+on\s+and|have|has|had|been|got|gotten)")
_R2_FIRST_DONE = re.compile(
    r"(?ix)\b(?:I|we)(?:'ve|\s+have|\s+had)?(?:\s+" + _ADV + r")*\s+"
    r"(?P<v>" + _DONE_VERB + r")\b(?P<rest>[^.!?\n]*)")
_R2_FIRST_WILL = re.compile(
    r"(?ix)\b(?:I|we)(?:'ll|\s+will|\s+shall|'m\s+going\s+to|\s+am\s+going\s+to|"
    r"'re\s+going\s+to|\s+are\s+going\s+to|'m\s+gonna|\s+gonna|'m\s+about\s+to|"
    r"\s+am\s+about\s+to)(?:\s+(?:just|also|now|quickly|right\s+away|go\s+ahead\s+and|"
    r"get\s+(?:right\s+)?on\s+it\s+and))*\s+(?P<v>" + _DO_BASE + r")\b(?P<rest>[^.!?\n]*)")
_R2_FIRST_ING = re.compile(
    r"(?ix)\b(?:I|we)(?:'m|'re|\s+am|\s+are)(?:\s+(?:just|also|now|already|busy))*\s+"
    r"(?P<v>" + _DO_ING + r")\b(?P<rest>[^.!?\n]*)")
#: "Adding eggs to your list now." (not "Adding eggs to a batter makes it richer").
_R2_LEAD_ING = re.compile(
    r"(?ix)^\W*(?:(?:ok|okay|sure|got\s+it|on\s+it|yes|yep|great|perfect|will\s+do)\W+)*"
    r"(?P<v>" + _DO_ING + r")\b(?P<rest>[^.!?\n]*)")
_R2_ING_NOW = re.compile(
    r"(?ix)\b(?:now|for\s+you|right\s+away|as\s+we\s+speak|straight\s+away)\b|"
    r"\b" + _INTO_PREP + r"\s+(?:your|the|my|our)\s+(?:[\w'-]+\s+){0,3}?" + _THINGS + r"\b")
#: A general verb after the deed: the sentence is about the world.
_R2_WORLD_VERB = re.compile(r"(?i)\b(?:makes?|helps?|gives?|keeps?|tends?|works?|brings?|"
                            r"is|are|can|will|adds?|means?|lets?)\b")
_R2_PASSIVE = re.compile(
    r"(?ix)(?:(?:\b(?:has|have)|'s|'ve)\s+(?:(?:now|just|already|also|all|officially|"
    r"successfully|finally)\s+)*been|\b(?:was|were|got|gets|get)|\b(?:will|'ll)\s+be|"
    r"(?:\b(?:is|are)|'s|'re)\s+(?:now|all|already|officially|also|safely|finally|"
    r"successfully)(?:\s+(?:now|all|already|officially|also|safely|finally|successfully))*)"
    r"\s+(?:(?:successfully|now|just|already|also|all|officially|"
    r"safely|finally)\s+)*(?P<v>" + _DONE_VERB + r")\b(?P<rest>[^.!?\n]*)")
#: "Sam's chore is checked off", "Your guest list is updated": "is" with a
#: participle that only a list change makes.
_R2_IS_DONE = re.compile(
    r"(?ix)(?:\b(?:is|are)|'s|'re)\s+(?P<v>checked\s+off|ticked(?:\s+off)?|crossed\s+(?:off|out)|"
    r"updated|added|removed|deleted|saved|logged|booked|scheduled|assigned|uninvited|"
    r"disinvited|jotted\s+down|noted\s+down|written\s+down|taken\s+care\s+of|sorted|"
    r"marked\s+(?:as\s+)?(?:done|read|complete|finished|off)|penciled\s+in|pencilled\s+in)\b"
    r"(?P<rest>[^.!?\n]*)")
_INTERJ = (r"(?:ok|okay|sure|sure\s+thing|great|perfect|awesome|yay|hooray|alright|all\s+right|"
           r"got\s+it|gotcha|you\s+got\s+it|no\s+problem|will\s+do|of\s+course|absolutely|"
           r"certainly|yes|yep|yup|and|also|plus|then|now|boom|bam|ta-?da|voil[aà]|success|"
           r"done|all\s+done|all\s+set|noted|there\s+you\s+go|good\s+news|nice|cool|excellent|"
           r"consider\s+it\s+done|here\s+you\s+go|easy|easy\s+peasy|just|already|all\s+good|"
           r"we'?re\s+all\s+set|you'?re\s+all\s+set|great\s+news|update)")
#: A participle first: "Added!", "Removed Leo from Big Day.", "Updated: Leo ...".
_R2_LEAD = re.compile(
    r"(?ix)^\W*(?:" + _INTERJ + r"\W+)*(?P<v>" + _DONE_VERB + r")\b(?P<rest>[^.!?\n]*)")
#: A thing, then its participle, and nothing else: "Eggs added.", "Dishes
#: checked off for Sam.", "3 tomatoes recorded.", "Leo : removed".
_R2_OBJ_DONE = re.compile(
    r"(?ix)^\W*(?:" + _INTERJ + r"\W+)*(?P<subj>(?:[\w'$.:-]+\s+){1,4}?)"
    r"(?P<v>" + _DONE_VERB + r")(?:\s+(?:off|out|down|in|up))?"
    r"(?P<rest>\s*(?:[.!:;,]|$|(?:to|from|on|onto|in|into|off|for|as|under|with|at|and|"
    r"now|already|too|successfully)\b)[^.!?\n]*)[.!?]*\s*$")
_SUBJ_STOP = re.compile(
    r"(?ix)\b(?:i|you|we|they|he|she|it|who|that|which|what|is|are|was|were|has|have|had|"
    r"be|been|being|will|would|can|could|should|may|might|must|do|does|did|not|never|if|"
    r"when|once|before|after|until|to|n't|would|may|how|why|where|there|this|these|those|"
    r"get|got|gets|let's|lets)\b|n't\b")
#: Said as a whole sentence (or its opening), a done phrase is a report.
_R2_DONE_PHRASE = re.compile(
    r"(?ix)^\W*(?:" + _INTERJ + r"\W+)*(?:consider\s+(?:it|that|this|them|(?:\S+\s+){1,4}?)"
    r"(?:done|handled|sorted|taken\s+care\s+of|added|removed|updated)|done\s+and\s+(?:dusted|done)|"
    r"all\s+done|all\s+set|(?:you|we)'?re\s+(?:all\s+set|good\s+to\s+go|set)|all\s+sorted|sorted|"
    r"(?:it'?s|that'?s|this\s+is|everything'?s|everything\s+is|it\s+is|that\s+is)\s+"
    r"(?:all\s+)?(?:done|sorted|handled|taken\s+care\s+of|set|good\s+to\s+go|in\s+there|"
    r"on\s+there|in|on|official)|mission\s+accomplished|success(?:fully\s+done)?|booked|saved|noted|"
    r"logged|added|done|in\s+it\s+goes|on\s+it|finished|complete|completed|request\s+"
    r"(?:processed|complete|completed|done|received))\s*(?:[!.]+|,|$)")
#: The phrases above that also end a piece of writing ("Done! Here's your poem").
_R2_WRITING_DONE = re.compile(
    r"(?ix)^\W*(?:" + _INTERJ + r"\W+)*(?:all\s+done|done|all\s+set|finished|complete|"
    r"consider\s+it\s+done|there\s+you\s+go|here\s+you\s+go)\s*(?:[!.]+|,|$)")
#: "Gotcha! Eggs are on it.", "I'm on it.", "Sam's on dishes now."
_R2_ON_IT = re.compile(
    r"(?ix)(?:\b(?:is|are|am)|'s|'re|'m)\s+(?:now\s+|all\s+)?on\s+it\s*[.!]*\s*$"
    r"|(?:\b(?:is|are)|'s|'re)\s+(?:now\s+)?(?:on|off)\s+(?:[\w'-]+\s+){0,2}?"
    r"(?:dish(?:es)?|laundry|trash|garbage|recycling|bins|vacuuming|mowing|chores?|duty|"
    r"cooking|dinner|lunch|breakfast|cleanup|clean-up|feeding|walking|homework)"
    r"(?:\s+(?:now|today|tonight|this\s+week))?\s*[.!]*\s*$")
#: Her things in a new state: "Leo is no longer invited", "The list now includes eggs",
#: "The guest count is now 41", "Eggs are in your cart".
_R2_NEW_STATE = re.compile(
    r"(?ix)\bno\s+longer\s+(?:on|in|part\s+of|listed|invited|assigned|scheduled|booked|"
    r"down\s+for|signed\s+up|registered|included)\b"
    r"|\b(?:count|total|tally|headcount|number|balance|score|points?|budget|guests?)\s+"
    r"(?:is|are|stands\s+at|'s|sits\s+at)\s+(?:now|currently)\s+(?:at\s+)?[-+$£€]?\d"
    r"|(?:\b(?:is|are)|'s|'re)\s+(?:now\s+|all\s+|already\s+|safely\s+)?(?:on|in|off|out\s+of)\s+"
    r"(?:your|the|my|our)\s+(?:[\w'-]+\s+){0,2}?(?:cart|basket|order|queue|wishlist|"
    r"agenda|lists?|charts?|log|logs|calendar|schedule|planner|tracker|roster|board)\b"
    # "Dune is now in your finished pile": "now" in HER place, any noun.
    r"|(?:\b(?:is|are)|'s|'re)\s+now\s+(?:on|in|off|out\s+of|under)\s+(?:your|my|our)\b"
    # "milk's on there now": on the list she just spoke of.
    r"|(?:\b(?:is|are)|'s|'re)\s+(?:now\s+|all\s+|already\s+|safely\s+)?(?:on|in)\s+there\b"
    r"|\b(?:your|my|our|the)\s+(?:request|order|change|changes|update|updates|entry|"
    r"entries|edit|edits)\s+(?:has|have|was|were|is|are)\s+(?:been\s+)?(?:\w+ly\s+)?"
    r"(?:processed|complete|completed|done|saved|applied|made|submitted|received|recorded|"
    r"through|in)\b"
    r"|\b(?:your|the|my|our)\s+(?:newly\s+)?(?:updated|revised|edited|changed)\s+"
    r"(?:[\w'-]+\s+){0,3}?(?:lists?|charts?|log|logs|calendar|schedule|planner|tracker|"
    r"roster|board|app|cart|records?)\b")
#: "The list now includes eggs" (her thing named before "now").
_R2_NOW_HAS = re.compile(r"(?ix)\b(?:now|currently)\s+(?:includes?|has|have|contains?|lists?|"
                         r"shows?|reads?)\b")
#: Colon or arrow status: "Leo : removed", "eggs : grocery list: done".
_R2_COLON_DONE = re.compile(
    r"(?ix):\s*(?:all\s+)?(?:" + _DONE_VERB + r"|done|sorted|gone|in|out|on|off)"
    r"(?:\s+(?:off|out|in))?\s*[.!]*\s*$"
    # "Eggs: on the list!"
    r"|:\s*(?:now\s+|all\s+)?(?:on|in|off|onto|into|off\s+of)\s+(?:the|your|my|our)\s+"
    r"(?:[\w'-]+\s+){0,2}?(?:" + _THINGS[3:-1] + r"|groceries|grocery|shopping)\b")
#: "Got you down for Saturday", "I got eggs on the list": recorded.
_R2_GOT = re.compile(
    r"(?ix)^\W*(?:" + _INTERJ + r"\W+)*(?:(?:I|we)(?:'ve)?\s+)?(?:just\s+|already\s+)?got\s+"
    r"(?:[\w'-]+,?\s+){1,3}?(?:down(?:\s+for)?\b|pencill?ed\s+in\b|in\s+for\b|"
    r"(?:on|in|onto|into)\s+(?:the|your|my|our)\s+(?:[\w'-]+\s+){0,2}?(?:" + _THINGS[3:-1]
    + r"|groceries|grocery|shopping)\b)")
#: Her thing said to be changed, with any "-ed" word: "Your RSVP list has been
#: refreshed", "The chore chart is now reshuffled".
_R2_THING_CHANGED = re.compile(
    r"(?ix)^\W*(?:" + _INTERJ + r"\W+)*(?:your|the|my|our)\s+(?:[\w'-]+\s+){0,3}?"
    r"(?:" + _THINGS[3:-1] + r"|groceries|grocery|shopping|chores?)\s+"
    r"(?:(?:has|have)\s+(?:(?:just|now|also|all|officially)\s+)*been|(?:is|are)\s+(?:(?:now|all|"
    r"officially)\s+)+|got|was|were)\s+[a-z]+(?:ed|en)\b")
#: "the guest list shows 42 now", "the chart says Sam now", "Your chore chart
#: reflects that now".
_R2_THING_NOW = re.compile(
    r"(?ix)\b(?:" + _THINGS[3:-1] + r"|groceries|grocery|shopping|count|total|headcount)\s+"
    r"(?:now\s+)?(?:shows?|has|have|includes?|reads?|lists?|says|contains?|totals?|"
    r"reflects?|matches|stands\s+at|is\s+at)\b[^.!?\n]*\bnow\s*[.!]*\s*$")
#: Her thing said to have grown or shrunk: "The guest list is one shorter now",
#: "Your grocery list got a little longer".
_R2_THING_SIZE = re.compile(
    r"(?ix)\b(?:your|the|my|our)\s+(?:[\w'-]+\s+){0,3}?(?:" + _THINGS[3:-1]
    + r"|groceries|grocery|shopping|chores?|headcount|count|total)\s+"
    r"(?:is|are|'s|got|gets|just\s+got|has\s+gotten|has\s+got|grew|shrank|became|went)\s+"
    r"(?:now\s+|just\s+)?(?:(?:a|one|two|three|\d+|a\s+little|a\s+bit|slightly|much)\s+)?"
    r"(?:bit\s+)?(?:longer|shorter|bigger|smaller|fuller|emptier|lighter|heavier|leaner|"
    r"longer|lower|higher|up|down)\b")
#: A count moved to a number: "Tomato count bumped to 4", "Total now at 42".
_R2_COUNT_TO = re.compile(
    r"(?ix)\b(?:count|total|tally|headcount|score|points?|number|guests?|rsvps?)\s+"
    r"(?:(?:is\s+)?now\s+|just\s+)?(?:[a-z]+ed|went|goes|jumped|dropped|rose|fell|sits|"
    r"stands|is\s+now|now)\s+(?:up\s+|down\s+)?(?:to|at)\s+[-+$£€]?\d")
#: Someone said to be down for, in for or booked for something: "You're down
#: for Saturday", "Leo is booked for the 4th".
_R2_DOWN_FOR = re.compile(
    r"(?ix)(?:\byou'?re|\byou\s+are|\bhe'?s|\bshe'?s|\bthey'?re|'s|\bis|\bare)\s+(?:now\s+|all\s+)?"
    r"(?:down|signed\s+up|booked|pencill?ed\s+in|registered|confirmed|in|on\s+the\s+list)\s+"
    r"for\b(?!\s+(?:a|an)\s+(?:treat|surprise|ride|shock|while|bit)\b)(?!\s+(?:it|that|anything|"
    r"something|good|now)\b)")
#: "Eggs have made it onto your list", "Leo landed on the guest list".
_R2_MADE_IT = re.compile(
    r"(?ix)\b(?:made\s+it|found\s+(?:its|their|his|her)\s+way|landed|ended\s+up|joined)\s+"
    r"(?:on|onto|in|into|to)\s+(?:the|your|my|our)\s+(?:[\w'-]+\s+){0,2}?(?:" + _THINGS[3:-1]
    + r"|groceries|grocery|shopping|cart)\b")
#: I or we did some verb with a particle to her thing: "I've squeezed Leo in at
#: table 2", "we jotted it down". Idioms that change nothing are left alone.
_R2_FIRST_PARTICLE = re.compile(
    r"(?ix)\b(?:I|we)(?:'ve|\s+have|\s+just|\s+also|\s+already|\s+went\s+ahead\s+and)*\s+"
    r"(?!(?:narrowed|boiled|broke|broken|toned|calmed|watered|slowed|cut|wound|thought|"
    r"looked|worked|figured|summed|turned|dialed|dialled|zoned|honed|settled|dug|calmed|"
    r"backed|ruled|laid|spelled|spelt|written|wrote|typed|read|played|sounded|"
    r"weighed|measured|filled|blocked|tuned)\b)(?P<v>[a-z]+ed)\s+(?:\S+\s+){1,3}?"
    r"(?:in|down|off|on)\b(?P<rest>(?:\s+(?:at|to|for|from|on|with|the)\b[^.!?\n]*|\s*[.!]*\s*$))")
#: Done idioms: "You're all squared away", "Sam's walk is in the books".
_R2_IDIOM_DONE = re.compile(
    r"(?ix)(?:\b(?:is|are|am)|'s|'re|'m)\s+(?:now\s+|all\s+|officially\s+)*(?:squared\s+away|"
    r"in\s+the\s+books|on\s+the\s+books|locked\s+in|nailed\s+down|sorted\s+out|taken\s+care\s+of|"
    r"good\s+to\s+go|in\s+the\s+system|on\s+record|on\s+file|accounted\s+for)\b")
#: A point or star given: "Sam gets a point for the dog walk".
_R2_POINT = re.compile(
    r"(?ix)\b(?:gets?|got|earns?|earned|scores?|scored|wins?|won)\s+(?:a|one|\d+|another|an\s+extra|"
    r"two|three)\s+(?:more\s+)?(?:points?|stars?|checks?|ticks?|credits?|gold\s+stars?|stickers?)\b")
#: A status after a colon or a dash: "Walk the dog: completed by Sam", "Milk - done",
#: "Book Club: Dune is next".
_R2_DASH_DONE = re.compile(
    r"(?ix)(?::|\s[-–—])\s*(?:all\s+)?(?:" + _DONE_VERB + r"|done|sorted|gone|finished|completed)"
    r"(?:\s+(?:off|out|in))?(?:\s+by\s+[\w'-]+)?\s*[.!]*\s*$"
    r"|^\W*(?-i:[A-Z])[\w'-]*(?:\s+(?-i:[A-Z])[\w'-]*){0,2}\s*:\s*[^.!?\n]{1,40}\b(?:is|are)\s+"
    r"(?:now\s+)?(?:next|up\s+next|in|out|on|off|done|added|removed|confirmed|booked)\s*[.!]*\s*$")
#: "Tuesday is now taco night on your calendar": now, then her place.
_R2_NOW_ON_HERS = re.compile(
    r"(?ix)(?:\b(?:is|are)|'s|'re)\s+now\b[^.!?\n]{0,40}\b(?:on|in)\s+(?:your|my|our)\s+"
    r"(?:[\w'-]+\s+){0,2}?(?:" + _THINGS[3:-1] + r"|groceries|grocery|shopping|cart)\b")
#: A participle used as an adjective: "Updated guidelines suggest ...".
_R2_ADJ_USE = re.compile(
    r"(?i)^\s*[a-z]+\s+(?:suggest|say|show|recommend|indicate|mean|help|make|let|allow|"
    r"require|give|keep|look|seem|sound|work|tell|note|state|point)s?\b")
#: A hedge before I or we: an offer or a condition ("if you want, I'll add").
_R2_HEDGE_OFFER = re.compile(
    r"(?ix)\b(?:if|once(?!\s+(?:again|more)\b)|when|whenever|after|until|unless|"
    r"as\s+soon\s+as|want\s+me\s+to|would\s+you\s+like|like\s+me\s+to|should\s+i|shall\s+i|"
    r"can\s+i|could\s+i|may\s+i|let\s+me\s+know|happy\s+to|glad\s+to|able\s+to|how\s+to|"
    r"ready\s+to|whether|in\s+case|either\s+way|otherwise)\b")
#: A hedge before a state: the offer ones, a modal, a negation, a reason.
_R2_HEDGE_BEFORE = re.compile(
    r"(?ix)\b(?:if|once(?!\s+(?:again|more)\b)|when|whenever|after|before|until|unless|"
    r"as\s+soon\s+as|want\s+me\s+to|would\s+you\s+like|like\s+me\s+to|should\s+i|shall\s+i|"
    r"can\s+i|could\s+i|may\s+i|let\s+me\s+know|happy\s+to|glad\s+to|able\s+to|how\s+to|"
    r"ready\s+to|whether|since|because|in\s+case|to\s+be|needs?\s+to|has\s+to|have\s+to|"
    r"should|could|would|might|must|can|may|cannot|won't|wouldn't|shouldn't|"
    r"couldn't|never|not|your\s+(?:job|turn)|tip|remember|try|hope|wish)\b|n't\b")
#: A hedge after the deed: "I'll add eggs if you want", "... once you confirm".
_R2_HEDGE_AFTER = re.compile(
    r"(?ix)\b(?:if|once|when|whenever|as\s+soon\s+as|after\s+you|unless|should\s+you|"
    r"just\s+say|let\s+me\s+know|want\s+me|ok\s+with\s+you|okay\s+with\s+you)\b")
#: History or a general fact: not a report on her things.
_R2_HISTORY = re.compile(
    r"(?ix)\b(?:1[0-9]\d\d|20[0-2]\d)s?\b|\bcentur(?:y|ies)\b|\bhistor\w*|"
    r"\boriginally\b|\bback\s+in\b|\bin\s+the\s+past\b|\bancient\b|\blegend\b|"
    r"\bonce\s+upon\b|\bfamously\b")
#: A general fact ("Most weddings have ...", "Usually the list ..."), not "All
#: chores are complete" (which is a report).
_R2_GENERAL = re.compile(
    r"(?ix)^\W*(?:most|many|some|few|a\s+typical|typical|typically|the\s+average|an\s+average|"
    r"on\s+average|in\s+general|generally|usually|often|traditionally|normally|people|folks)\b")
#: Text in this chat or in the world, as the place a deed is done: "to that
#: toast", "in the poem", "to the recipe".
_R2_TEXT_PLACE = re.compile(
    r"(?ix)\b(?:answer|reply|response|message|text|poem|story|essay|email|e-mail|letter|"
    r"draft|code|script|post|tweet|caption|bio|speech|toast|lyrics|translation|haiku|"
    r"limerick|sonnet|song|rap|riddle|joke|paragraph|sentence|recipe|batter|dough|sauce|"
    r"soup|version|summary|outline|below|above|chorus|verse|title|intro|ending|opening|"
    r"conclusion|wording)\b")
#: Text as the thing a deed moves: "some ideas", "a pinch of humor" (but "to
#: gift ideas" is a place, one of her lists).
_R2_TEXT_OBJ = re.compile(
    r"(?ix)\b(?:ideas?|tips?|suggestions?|steps?|mind|thoughts?|humou?r|flavou?r|twist|"
    r"touch|pinch|dash|spin|emojis?|rhymes?|jargon|typos?|formatting|details?|examples?|"
    r"tone|words?|lines?|sentences?)\b")
_R2_TEXT_NOUN = _R2_TEXT_PLACE
#: Verbs that store or mark a record: never text work in a chat.
_STORE_VERB = re.compile(
    r"(?ix)^(?:saved|logged|marked|jotted|noted|checked|ticked|crossed|struck|scratched|"
    r"assigned|reassigned|unassigned|uninvited|disinvited|invited|booked|scheduled|"
    r"rescheduled|written|wrote|pencill?ed|popped|slotted|starred|flagged|tallied|"
    r"cancell?ed|synced|processed|queued|archived|recorded|stored|filed|registered|"
    r"entered|tracked|reserved|confirmed|counted|stocked|save|log|mark|jot|note|check|"
    r"tick|cross|assign|reassign|uninvite|invite|book|schedule|reschedule|write|pencil|"
    r"pop|slot|star|flag|cancel|record|store|file|register|enter|track|reserve|count|"
    r"saving|logging|marking|jotting|noting|checking|ticking|crossing|assigning|"
    r"uninviting|inviting|booking|scheduling|writing|popping|slotting|recording|tracking|"
    r"entering|cancell?ing|filing|storing)\b")
#: A thing of hers named in the sentence: a pronoun for it or a word that
#: names a record, used to anchor the WEAK verbs.
_R2_ANCHOR = re.compile(
    r"(?ix)\b(?:it|that|this|them|him|her|everything|all\s+of\s+(?:it|them)|request|item|"
    r"items|entry|entries|change|changes|order|record|records|finished|done|read|complete|"
    r"pending|archive|later|tomorrow|next\s+week|shelf|your|my|our)\b")
_DONE_GERUND = re.compile(r"(?i)^\W*(?:done|finished|all\s+done)\s+[a-z]+ing\b")
_ADJ_ADDED = re.compile(r"(?i)\badded\s+(?:bonus|benefit|plus|touch|value|sugars?|salt|"
                        r"flavou?r|protection|fiber|fibre|vitamins?|preservatives?|"
                        r"cost|weight|layer|texture|crunch|richness)\b")


def _content_turn(asked: Any) -> bool:
    """She asked for writing in this chat: "make the poem shorter"."""
    text = _plain(asked).strip()
    return bool(text) and bool(_content_ask(text) or _TEXT_THING.search(text))


def _r2_anchored(text: str, asked: Any, names: Any) -> bool:
    """*text* names one of her things: a name, a list word, a word of her
    message, a capitalized name, or a pronoun for a record."""
    if _names_hit_folded(text, names) or _THING_WORD.search(text) or _R2_ANCHOR.search(text):
        return True
    # A named place: "... for Emma in Book Club".
    if re.search(r"\b(?:in|on|into|onto|off|from)\s+(?:the\s+)?[A-Z][a-z]+", text):
        return True
    words ={w for w in _folded_words(text) if len(w) >= 4} - _STOP
    return bool(words & _t_anchor_words(asked)) if asked is not None else False


def _text_work(rest: str, asked: Any, names: Any, verb: str = "") -> bool:
    """The deed's object is text in this chat or the world, not her records.
    A verb that stores or marks a record ("saved", "logged", "booked") is
    never text work, unless it points at this reply ("noted a few ideas below")."""
    if re.search(r"(?i)\b(?:below|above)\b", rest) and not _THING_WORD.search(rest):
        return True
    if verb and _STORE_VERB.match(verb.strip()):
        return False
    # "I added it up and it comes to 42": a sum.
    if re.match(r"(?i)\s*(?:(?:it|them|this|that|these|those|everything|all|the\s+\w+)\s+)?up\b",
                rest) and verb.strip().lower().startswith("add"):
        return True
    if _names_hit_folded(rest, names) or _THING_WORD.search(rest):
        return False
    obj = re.split(r"(?i)\b" + _INTO_PREP + r"\s", rest, maxsplit=1)[0]
    if (_R2_TEXT_PLACE.search(rest) or _R2_TEXT_OBJ.search(obj) or _REC_REST_TEXT.match(rest)
            or _ABOUT_THE_REPLY.search(rest)):
        return True
    # "I've updated it" after "make the poem shorter": the poem.
    return _content_turn(asked) and bool(
        re.fullmatch(r"(?i)\s*(?:it|that|this|them)\b[\s\w']{0,30}", rest)) \
        and not re.search(r"(?i)\b" + _INTO_PREP + r"\s+(?:the|your|my|our)\b", rest)


def _r2_done_report(body: str, asked: Any, names: Any) -> bool:
    """True when *body* (one sentence) reports a change as done or under way."""
    text = re.sub(r"\s*(?:->|→|=>|⇒|➡)\s*", " : ", body).strip()
    text = _plain(text).strip()
    if not text or text.rstrip().endswith("?"):
        return False
    # A year or "originally" makes a was/were sentence history, never an I/we one.
    history = bool(_R2_HISTORY.search(text))
    content_turn = _content_turn(asked)

    def hedged(m: re.Match, rx: re.Pattern = _R2_HEDGE_BEFORE) -> bool:
        return bool(rx.search(text[:m.start()])
                    or _R2_HEDGE_AFTER.search(text[m.end("v"):]))

    def hedged_any(t: str) -> bool:
        return bool(_R2_HEDGE_OFFER.search(t) or re.search(
            r"(?i)\b(?:could|might|would|should|may|can|not|never)\b|n't\b", t))

    # I or we did it, will do it, or are doing it.
    for rx in (_R2_FIRST_DONE, _R2_FIRST_WILL, _R2_FIRST_ING):
        for m in rx.finditer(text):
            if hedged(m, _R2_HEDGE_OFFER):
                continue
            rest = m.group("rest")
            if _text_work(rest, asked, names, m.group("v")) or _ADJ_ADDED.search(text[m.start("v"):]):
                continue
            weak = not _DONE_STRONG_RE.match(re.sub(r"\s+", " ", m.group("v")))
            if rx is _R2_FIRST_DONE and weak and not _r2_anchored(rest, asked, names):
                continue
            return True
    m = _R2_LEAD_ING.match(text)
    if m and not hedged(m) and _R2_ING_NOW.search(m.group("rest")) \
            and not _R2_WORLD_VERB.search(m.group("rest")) \
            and not _text_work(m.group("rest"), asked, names, m.group("v")):
        return True
    general = bool(_R2_GENERAL.match(text))
    # It has been done, was done, is now done, will be done.
    for rx in (_R2_PASSIVE, _R2_IS_DONE):
        for m in rx.finditer(text):
            if general or hedged(m) or history and re.match(r"(?i)(?:was|were|got)\b", m.group(0)):
                continue
            subject = text[:m.start()]
            if _text_work(subject + " " + m.group("rest"), asked, names, m.group("v")):
                continue
            weak = not _DONE_STRONG_RE.match(re.sub(r"\s+", " ", m.group("v")))
            # "has been bumped": the present perfect says it just happened,
            # whatever the verb; "was moved" needs one of her things.
            perfect = re.match(r"(?i)(?:has|have|'s|'ve)\s+(?:\w+\s+){0,3}?been\b", m.group(0))
            # "are all confirmed", "is now listed": a state said as new.
            intensified = re.search(r"(?i)\b(?:now|all|officially|already|successfully)\b",
                                    m.group(0)[:m.start("v") - m.start()])
            if weak and not (perfect or intensified) \
                    and not _r2_anchored(subject + m.group("rest"), asked, names):
                continue
            # "Salt is added to water": a plain "is" needs her thing as subject.
            if rx is _R2_IS_DONE and not re.match(
                    r"(?i)(?:checked|ticked|crossed|marked|jotted|noted|written|pencill?ed|"
                    r"uninvited|disinvited|taken\s+care)\b", m.group("v")) and not (
                    _r2_anchored(subject, asked, names) or re.search(r"\w's\b", subject)):
                continue
            return True
    if not general and not hedged_any(text) and (
            _R2_GOT.match(text) or _R2_THING_CHANGED.match(text) or _R2_THING_NOW.search(text)
            or _R2_THING_SIZE.search(text) or _R2_COUNT_TO.search(text)
            or _R2_DOWN_FOR.search(text) or _R2_MADE_IT.search(text)
            or _R2_IDIOM_DONE.search(text) or _R2_POINT.search(text)
            or _R2_NOW_ON_HERS.search(text)
            or (_R2_DASH_DONE.search(text) and not _T_TIP.match(text))):
        return True
    for m in _R2_FIRST_PARTICLE.finditer(text):
        if not hedged(m, _R2_HEDGE_OFFER) and not _text_work(m.group("rest"), asked, names):
            return True
    # "Book Club now reads Dune next": a named thing, "now", a state.
    if not general and re.match(r"^\W*(?:[A-Z][\w'-]*\s+){1,3}now\s+(?:reads?|has|includes?|"
                                r"shows?|lists?|features?|picks?|holds?|contains?)\b", text):
        return True
    # A participle first, or a thing and its participle and nothing else.
    m = _R2_LEAD.match(text)
    if m and not history and not _ADJ_ADDED.search(text) and not _text_work(m.group("rest"), asked, names, m.group("v")) \
            and not _ABOUT_THE_REPLY.search(text) and not _R2_ADJ_USE.match(m.group("rest")) \
            and not re.match(r"(?i)put\s+(?:simply|another\s+way|differently|briefly)",
                             text[m.start("v"):]):
        weak = not _DONE_STRONG_RE.match(re.sub(r"\s+", " ", m.group("v")))
        if not weak or _r2_anchored(m.group("rest"), asked, names) \
                or len(_WORD_RE.findall(text)) <= 6:
            return True
    m = _R2_OBJ_DONE.match(text)
    if m and not general and not history and not _SUBJ_STOP.search(m.group("subj")) \
            and not _R2_WORLD_VERB.search(m.group("rest")) \
            and not _text_work(m.group("subj") + m.group("rest"), asked, names, m.group("v")):
        weak = not _DONE_STRONG_RE.match(re.sub(r"\s+", " ", m.group("v")))
        if not weak or not m.group("rest").strip(" .!") \
                or _r2_anchored(m.group("rest"), asked, names):
            return True
    # "Done!", "Consider it done.", "All set", "Noted!", "Booked!".
    if not _DONE_GERUND.match(text) and _R2_DONE_PHRASE.match(text):
        if not (content_turn and _R2_WRITING_DONE.match(text)):
            return True
    if not general and (_R2_ON_IT.search(text) or _R2_COLON_DONE.search(text)
                        and not _T_TIP.match(text)):
        return True
    if not general and not _ABOUT_THE_REPLY.search(text):
        m = _R2_NEW_STATE.search(text)
        if m and not _R2_HEDGE_BEFORE.search(text[:m.start()]):
            return True
        m = _R2_NOW_HAS.search(text)
        if m and not _R2_HEDGE_BEFORE.search(text[:m.start()]) and (
                _THING_WORD.search(text[:m.start()]) or _names_hit_folded(text, names)
                or re.search(r"(?i)\b(?:it|that|this)\s*$", text[:m.start()])):
            return True
    return False


#: Words that make a reply sentence about the world, not her things, for the
#: statement rules: a general fact, a condition, a gerund subject, text work.
_R2_WORLD = re.compile(
    r"(?ix)^\W*(?:once(?!\s+(?:again|more)\b)|if|when|whenever|after|before|as\s+soon\s+as|"
    r"until|unless)\b"
    r"|^\W*[a-z]+ing\s+(?:[\w'-]+\s+){0,6}?(?:makes?|helps?|is|are|gives?|keeps?|can|will|"
    r"adds?|works?|tends?|brings?)\b")


def _r2_world_sentence(body: str, asked: Any, names: Any) -> bool:
    """A sentence the statement rules must leave alone:
    "Most weddings have about 100 guests on the guest list.", "Once you add
    eggs, the list will show them", "Adding eggs to a batter makes it
    richer.", "I added a pinch of humor to that toast", "That's all set up in
    the recipe", "Done thinking it over: I'd go with tacos."."""
    text = _plain(body).strip()
    lead = re.match(r"(?i)^\W*[a-z]+ed\b", text)
    if lead and _R2_ADJ_USE.match(text[lead.end():]):
        return True     # "Updated guidelines suggest 8 hours of sleep."
    # "I added it up and it comes to 42.": a sum.
    if re.search(r"(?i)\b(?:I|we)\s+(?:\w+\s+)?add(?:ed)?\s+(?:it|them|this|that|these|those|"
                 r"everything|all|the\s+\w+)\s+up\b", text):
        return True
    # "Here are three dinner ideas: ...": the answer itself.
    if re.match(r"(?i)^\W*here(?:'s|\s+is|\s+are)\s+(?:a|an|some|few|\d+|one|two|three|four|"
                r"five|six|seven|eight|nine|ten|my|a\s+few|several)\b", text) \
            and not re.search(r"(?i)\b(?:your|updated|new|current)\b", text):
        return True
    # "The guest list for a small wedding is usually under 50.": a general fact.
    if re.search(r"(?i)\b(?:usually|typically|generally|normally|tends?\s+to|on\s+average|"
                 r"in\s+general|as\s+a\s+rule)\b", text) \
            and not re.search(r"(?i)\b(?:your|my|our|now|already|I|we|I've|we've)\b", text):
        return True
    if not text or _NOW_WORDS.search(text) and not re.search(r"(?i)\ball\s+set\s+up\b", text):
        return False
    if _R2_GENERAL.match(text) or _R2_WORLD.match(text) or _DONE_GERUND.match(text) or (
            _R2_HISTORY.search(text) and re.search(r"(?i)\b(?:was|were)\b", text)
            and not re.search(r"(?i)\b(?:I|we)\b", text)):
        return True
    if ((_TEXT_THING.search(text) or re.search(r"(?i)\b(?:in|to|into)\s+(?:the|this|that)\s+"
                                               r"recipe\b", text))
            and not _names_hit_folded(text, names) and not _THING_WORD.search(text)):
        return True
    return False


#: Small talk: nothing in it is a statement about her things.
_SMALL_TALK = re.compile(
    r"(?ix)^\W*(?:(?:hi|hello|hey|hiya|howdy|yo|good\s+(?:morning|afternoon|evening|night)|"
    r"thanks|thank\s+you|thx|ty|cheers|much\s+appreciated|appreciate\s+it|love\s+you|"
    r"you'?re\s+(?:the\s+best|awesome|great)|nice|cool|lol|haha|wow|bye|goodbye|see\s+ya|"
    r"night|morning)(?:\s+(?:so|very)\s+much|\s+there|\s+puff|\s+again|\s+a\s+lot|\s+buddy|"
    r"\s+friend)?\W*)+$")


def claims_app_action(sentence: Any, asked: Any = None, names: Any = ()) -> bool:
    """True when one sentence says a thing was added, removed or changed in a
    list or an app (and does not disown it).

    *asked* is the owner's message the sentence answers. When it asked for a
    change (:func:`asks_for_change`), no list or app noun is needed: any claim
    of that kind of change is untrue, because a real action is answered from
    its own evidence before any model is asked. After a short statement
    ("Leo Park is coming too"), "I've added ..." is a claim whatever follows."""
    # A check mark is a status ("eggs ✔ on the grocery run"): read it as a word.
    body = _plain(_CHECK_MARKS.sub(" checked ", str(sentence or "")))
    if not body.strip():
        return False
    if _FIRST_PERSON.search(body) or _PASSIVE.search(body) or _LEAD_DEED.search(body):
        if not _NOT_A_CLAIM.search(body):
            return True
    if asked is None:
        return _any_turn_claim(body, None, names) or _r2_done_report(body, None, names)
    # Her own words quoted back ('what you mean by "Leo is a
    # yes."') are hers, not the bot's claim; a feeling about what she said
    # ("I'm glad to hear the dishes are done!") is not one either, unless it
    # also says the thing was put somewhere or changed.
    body = _unquote_hers(body, asked)
    if not body.strip():
        return False
    # A report that a change is done, whatever she asked.
    if _r2_done_report(body, asked, names):
        return True
    # ... and a sentence about the world or this chat is left to the rules
    # that read claims only.
    if _r2_world_sentence(body, asked, names):
        return False
    # A question back to her is not a claim, unless it carries one ("Anything
    # else, now that Leo is on the guest list?").
    if _ASKS_HER.search(body) and not (
            _T_CHANGED_WORD.search(body) or _ON_THING_RE.search(body)
            or _T_YOUR_THING_DONE.search(body)):
        return False
    if (_T_FEELING_ECHO.match(body) and not change_turn(asked, names)
            and not (_T_STILL_CLAIM.search(body) or _ON_THING_RE.search(body)
                     or _names_hit_folded(body, names) or _I_DID.search(body)
                     or _I_DID_MORE.search(body))):
        return False
    if _any_turn_claim(body, asked, names):
        return True
    if _statement_ask(asked) and _statement_tally(body):
        return True
    # "the dishes are done" is a statement too, though it has a change word.
    if (_statement_ask(asked) and not _ABOUT_THE_REPLY.search(body)
            and (_status_claim(body, asked, names)
                 or _record_state_claim(body, asked, names))):
        return True
    if not asks_for_change(asked):
        return _statement_ask(asked) and _statement_claim(body, asked, names)
    if _CTX_DONE.match(body) or _CTX_YES_DEED.match(body) or _CTX_DONE_MORE.match(body):
        return not _NOT_A_CLAIM.search(body)
    if not (_CTX_FIRST_PERSON.search(body) or _CTX_STATE.search(body)
            or _CTX_BARE.search(body) or _CTX_NEW_STATE.search(body)):
        return False
    return not (_NOT_A_CLAIM.search(body) or _ABOUT_THE_REPLY.search(body))


# -- read the reply the other way round --------------------------------------------
#
# A list of the SHAPES of a false claim always misses some: a fresh set of
# replies held 27-32% more ("I tidied up your grocery list", "I shifted the game to
# Sunday", "Leo is now RSVP'd as attending"). So on every turn that reaches
# the model (no app action ran: every real one is answered before the model,
# from its own evidence) the reply is read the other way round. A sentence is
# REPLACED by the honest line when one of its clauses
#
#   * names one of her apps or lists ("your grocery list", "the Pet Log", one
#     of her list or app names, "on there"), unless it is advice to her ("you
#     could ...") or a general fact ("guest lists usually ..."); or
#   * uses a change verb in a past or perfect form, or says a thing IS in a
#     state now, about the bot itself (I, we), one of her things, a word of her
#     own message or one of her rows ("I shifted the game", "Leo is now
#     RSVP'd", "Oat milk is all set", "Consider your fridge restocked");
#
# and a sentence that OFFERS a change to her things ("Want me to add oat milk
# to your grocery list?") is taken out, so a yes to an offer cannot arise.
# What stays: negations ("I have not changed anything"), questions that offer
# nothing, advice, general facts, praise with no change verb.
#
# The verb list below is only a floor: ANY verb in a past or perfect form
# counts when the bot (I, we) or one of her own words or rows is its subject,
# so a verb nobody listed ("tidied", "shifted") still fails closed.

#: Change verbs said as done (a floor, not the bound; see above).
_FC_LEX_PAST = (
    r"added|removed|deleted|erased|updated|changed|moved|shifted|swapped|switched|replaced|"
    r"renamed|marked|logged|recorded|saved|stored|noted|jotted|tracked|entered|inserted|"
    r"included|listed|booked|scheduled|rescheduled|cancell?ed|postponed|bumped|pushed|"
    r"assigned|reassigned|unassigned|invited|uninvited|disinvited|confirmed|registered|"
    r"enrolled|checked\s+off|ticked|crossed|struck|scratched|cleared|emptied|wiped|reset|tidied|"
    r"cleaned|organi[sz]ed|sorted|arranged|rearranged|reordered|grouped|merged|combined|"
    r"de-?duped|de-?duplicated|trimmed|pruned|archived|filed|tagged|flagged|starred|pinned|"
    r"highlighted|synced|refreshed|restocked|stocked|filled|topped|placed|popped|stuck|"
    r"slotted|tucked|squeezed|dropped|tossed|thrown|created|adjusted|modified|edited|fixed|"
    r"corrected|revised|tweaked|reserved|queued|appended|seated|tallied|totall?ed|completed|"
    r"handled|rsvp'?d|pencill?ed|penciled|shelved|nixed|scrapped|axed|ditched|kicked|"
    r"planned\s+(?:in|out)|set\s+(?:up|aside|as|to|for)|put\s+(?:down|in|on|onto|into|away|back)|"
    r"taken\s+(?:off|out|care)|took\s+(?:off|out|care)|got\s+rid|gotten\s+rid|made\s+(?:a\s+)?note")
_FC_LEX_PAST_RE = re.compile(r"(?i)\b(?:" + _FC_LEX_PAST + r")\b")
#: Any past or perfect verb form: "-ed", "RSVP'd", or an irregular one.
_FC_IRREGULAR = (
    r"done|gone|went|set|put|cut|struck|taken|took|written|wrote|made|sold|paid|bought|won|lost|"
    r"kept|left|held|sent|met|seen|begun|drawn|grown|shown|known|thrown|threw|chosen|chose|"
    r"given|gave|hidden|hid|shut|split|spent|stuck|swept|torn|tore|worn|broken|broke|frozen|"
    r"stolen|fed|got|gotten|brought|caught|dealt|found|laid|led|let|meant|built|sent|"
    r"begun|became|become")
_FC_PAST_RE = re.compile(r"(?i)\b(?:[a-z]{2,}ed|[a-z]+'d|" + _FC_IRREGULAR + r")\b")
#: The same change verbs as a promise or under way: "I'll add", "I'm adding".
_FC_LEX_BASE = (
    r"add|remove|delete|erase|update|change|move|shift|swap|switch|replace|rename|mark|log|"
    r"record|save|store|note|jot|track|enter|insert|include|list|book|schedule|reschedule|"
    r"cancel|postpone|bump|push|assign|reassign|unassign|invite|uninvite|confirm|register|"
    r"enrol+|check|tick|cross|strike|scratch|clear|empty|wipe|reset|tidy|clean|organi[sz]e|"
    r"sort|arrange|rearrange|reorder|group|merge|combine|trim|prune|archive|file|tag|flag|"
    r"star|pin|sync|refresh|restock|stock|fill|top|place|pop|stick|slot|tuck|squeeze|drop|"
    r"toss|throw|create|adjust|modify|edit|fix|correct|revise|tweak|reserve|queue|append|"
    r"seat|tally|total|complete|handle|rsvp|pencil|shelve|nix|scrap|ditch|kick|put|set|"
    r"take\s+care|get\s+rid|make\s+(?:a\s+)?note|keep\s+(?:track|a\s+note|a\s+record)")
_FC_LEX_ING = (
    r"adding|removing|deleting|erasing|updating|changing|moving|shifting|swapping|switching|"
    r"replacing|renaming|marking|logging|recording|saving|storing|noting|jotting|tracking|"
    r"entering|inserting|including|listing|booking|scheduling|rescheduling|cancell?ing|"
    r"postponing|bumping|pushing|assigning|inviting|uninviting|confirming|registering|"
    r"checking|ticking|crossing|striking|scratching|clearing|emptying|wiping|resetting|"
    r"tidying|cleaning|organi[sz]ing|sorting|arranging|archiving|filing|tagging|flagging|"
    r"starring|pinning|syncing|refreshing|restocking|stocking|filling|placing|popping|"
    r"sticking|slotting|tucking|squeezing|dropping|tossing|throwing|creating|adjusting|"
    r"modifying|editing|fixing|correcting|reserving|queueing|queuing|appending|seating|"
    r"tallying|completing|handling|rsvping|pencill?ing|putting|setting|taking\s+care|"
    r"getting\s+rid|making\s+(?:a\s+)?note|keeping\s+(?:track|a\s+note|a\s+record)")
#: The bot as the subject: "I", "we", "I've", "let's".
_FC_FIRST = re.compile(r"(?:\bI\b|\b(?i:we)\b|\b(?i:let'?s)\b|\b(?i:let\s+me)\b)")
_FC_FIRST_WILL = re.compile(
    r"(?x)(?:\bI|\b(?i:we))(?i:'ll|\s+will|\s+shall|'m\s+going\s+to|\s+am\s+going\s+to|"
    r"'re\s+going\s+to|\s+are\s+going\s+to|'m\s+gonna|\s+gonna|'m\s+about\s+to|\s+am\s+about\s+to)"
    r"(?i:\s+(?:just|also|now|quickly|go\s+ahead\s+and|right\s+away))*\s+(?i:" + _FC_LEX_BASE
    + r")\b")
_FC_FIRST_ING = re.compile(
    r"(?x)(?:\bI|\b(?i:we))(?i:'m|'re|\s+am|\s+are)(?i:\s+(?:just|also|now|already|busy))*\s+"
    r"(?i:" + _FC_LEX_ING + r")\b")
#: "Adding eggs to your list now." (a gerund lead said as under way).
_FC_LEAD_ING = re.compile(r"(?i)^\W*(?:(?:ok|okay|sure|on\s+it|got\s+it|yes)\W+)*(?:"
                          + _FC_LEX_ING + r")\b")
#: Thinking and feeling verbs: "I've heard", "I thought", "I loved it".
_FC_MIND = re.compile(
    r"(?ix)\b(?:I|we)(?:'ve|'d|\s+have|\s+had|\s+just|\s+also|\s+always|\s+never|\s+really)*\s+"
    r"(?:heard|thought|wondered|noticed|understood|loved|enjoyed|liked|learned|learnt|read|"
    r"seen|saw|found|missed|guessed|imagined|figured|assumed|wanted|hoped|meant|mentioned|"
    r"said|asked|tried|looked|been|had|felt|believed|known|knew|picked|chose|chosen|"
    r"focused|focussed|used|gone\s+with|went\s+with|kept\s+it|made\s+(?:it|this|sure)|"
    r"pulled\s+together|put\s+together|come\s+up|came\s+up|got\s+(?:it|you|this|that)|"
    r"got\s+your\s+back|mixed\s+up|messed\s+up|misread|misunderstood|forgot|forgotten|"
    r"checked|looked\s+(?:it\s+)?up|searched|googled|double-?checked)\b")
#: Text in this chat: "a few ideas below", "this poem", "here is".
_FC_TEXT = re.compile(
    r"(?ix)\b(?:below|above|here(?:'s|\s+is|\s+are)|the\s+following|following|this\s+"
    r"(?:reply|answer|message|list\s+of)|poem|story|toast|speech|essay|email|letter|draft|"
    r"summary|recipe|joke|haiku|lyrics|caption|paragraph|sentence|version|outline|ideas?|"
    r"tips?|suggestions?|steps?|options?|examples?)\b|:\s*$")
#: Her thing in a state now: "is now", "now says", "all set", "... now."
_FC_NOW = re.compile(
    r"(?ix)(?:\b(?:is|are|was|were|has|have)|'s|'re|'ve)\s+(?:now|all|officially|already|"
    r"successfully)\b(?!\s+(?:about|like|a\s+(?:good|great)))"
    r"|\bnow\s+(?:is|are|has|have|says?|shows?|includes?|lists?|reads?|holds?|contains?|totals?|"
    r"stands?|sits?|appears?|lives?|belongs?|goes|features?|reflects?)\b"
    r"|\bnow\s*[.!]*\s*$|\ball\s+set\b|\bis\s+(?:in|on)\s+there\b|\bin\s+there\b"
    r"|\b(?:ready|waiting|queued|lined\s+up)\s+(?:for|on|in)\s+(?:your|the)\b"
    r"|\bno\s+longer\b|\bone\s+(?:less|fewer|more)\b")
#: Advice to her or a general fact: the clause names a list without saying
#: it changed ("You could group items by aisle on your grocery list").
_FC_ADVICE = re.compile(
    r"(?ix)^\W*(?:(?:so|and|but|also|then|maybe|perhaps|tip|pro\s+tip|or)\W+)*"
    r"(?:you\s+(?:could|might|may|can|should|would|will\s+want|'ll\s+want|want\s+to|"
    r"need\s+to|may\s+want|might\s+want|could\s+also|can\s+also)|try|consider(?!\s+(?:it|that|"
    r"this|them|your)\b)|remember\s+to|don'?t\s+forget\s+to|feel\s+free\s+to|"
    r"(?:if|once|when|whenever|after|before)\s+you)\b"
    r"|\b(?:usually|typically|generally|normally|often|traditionally|commonly|sometimes|"
    r"rarely|most\s+people|many\s+people|tends?\s+to)\b")
#: A pure negation or refusal: nothing is said to have happened.
_FC_NEGATION = re.compile(
    r"(?ix)\b(?:not|never|no\s+(?:longer\s+)?(?:change|changes)|nothing|n't|cannot|can'?t|"
    r"couldn'?t|won'?t|unable)\b|n't\b")
_FC_CLAUSES = re.compile(r"\s*(?:;|:(?=\s)|,\s*(?:and|but|so|plus)\s+|\s+(?:but|and\s+then|"
                         r"and\s+also)\s+|,\s+(?=(?:I|we|it|he|she|they|the|your|my|our)\b)"
                         r"|,\s+(?=(?i:add|put|remove|mark|update|take|cross|check|tick|write|"
                         r"note|log|record|include|move|change|pop|stick|drop|jot|sign|try|"
                         r"consider|remember|let)\b))\s*")
#: A sentence that opens with a condition is read whole: "Once you add eggs,
#: the list will show them at the top."
_FC_CONDITION = re.compile(r"(?i)^\W*(?:if|once|when|whenever|after|before|as\s+soon\s+as|"
                           r"unless|until)\s+(?:you|she|he|they|we|it)\b")
#: An offer of a change to her things: "Want me to add ...", "Shall we cross
#: ... off", "I can move the game ...", "Let me know if you want me to ...".
_FC_OFFER = re.compile(
    r"(?ix)\b(?:want\s+me\s+to|would\s+you\s+like\s+(?:me\s+to|it|them|that|this|those)|"
    r"(?:shall|should)\s+(?:i|we)|do\s+you\s+want\s+(?:me\s+to|it|them|that|this|those|"
    r"(?:\S+\s+){1,4}?(?:added|on|in|off|removed|moved|marked))|"
    r"(?:i|we)\s+(?:can|could|would\s+be\s+(?:happy|glad)\s+to|'d\s+be\s+(?:happy|glad)\s+to|"
    r"am\s+(?:happy|glad)\s+to|'m\s+(?:happy|glad)\s+to|'d\s+love\s+to|'ll\s+gladly|"
    r"will\s+gladly|may)|happy\s+to|glad\s+to|let\s+me|how\s+about\s+(?:i|we)|"
    r"would\s+it\s+help\s+if\s+(?:i|we)|let\s+me\s+know\s+if|if\s+you(?:'d|\s+would)?\s+"
    r"(?:like|want)|say\s+the\s+word|just\s+say|want\s+(?:it|them|that|this|those)\s+(?:on|in|"
    r"off|added|removed)|ready\s+(?:to|when))\b")
_FC_OFFER_VERB = re.compile(r"(?i)\b(?:" + _FC_LEX_BASE + r"|" + _FC_LEX_ING + r"|"
                            + _FC_LEX_PAST + r")\b")
#: Determiners that make a list word hers ("the", "your") or not ("a", "any").
_FC_NOT_HERS = frozenset("a an any every each some most many no another such typical good "
                         "great simple basic sample example perfect ideal".split())
_FC_HERS_DET = frozenset("the your my our his her their this that".split())
_FC_PLACE_THERE = re.compile(r"(?i)\b(?:on|in|off|onto|into|from)\s+(?:there|it)\b")
_FC_SUBJECT_YOU = re.compile(r"(?i)^\W*(?:[\w']+\W+){0,3}?(?:you|you're|you've|you'll|youre)\b")
_FC_HISTORY = re.compile(r"(?i)\b(?:1[0-9]\d\d|20[0-2]\d)s?\b|\bcentur(?:y|ies)\b|\bhistor\w*|"
                         r"\boriginally\b|\bback\s+in\b|\bancient\b|\bfamously\b")


#: Words that name a list or an app on their own ("the list", "the tracker").
_FC_LIST_WORD = (r"(?:lists?|checklists?|to-?do(?:\s+list)?s?|trackers?|planners?|rosters?|"
                 r"registr(?:y|ies)|inventor(?:y|ies)|spreadsheets?|apps?|setlists?|playlists?|"
                 r"headcount|guest\s*lists?|groceries|shopping|rsvps?)")
#: Words that name one only as hers ("your budget", "the Pet Log"), since
#: "the table", "a log on the fire" and "the calendar year" are everyday.
_FC_SOFT_WORD = (r"(?:log|logs|logbook|schedule|calendar|budget|charts?|board|notes|journal|"
                 r"diary|library|collection|timeline|records?|entries|sheet|tab|shelf|shelves|"
                 r"chores|tasks|agenda|pantry|cart)")
_FC_GAP = (r"(?:(?!(?:a|an|the|to|is|are|was|were|be|been|of|for|and|or|in|on|at|with|from|"
           r"by|as|it|that|this|so|very|really|added|removed|made|put)\b)[\w'-]+\s+){0,2}")
_FC_HERS_RE = re.compile(
    r"(?i)\b(?:the|your|my|our|this|that)\s+" + _FC_GAP + _FC_LIST_WORD + r"\b"
    r"|\b(?:your|my|our)\s+" + _FC_GAP + _FC_SOFT_WORD + r"\b"
    r"|(?!(?:A|An|The|This|That|Each|Every|Any|Some|One|Most|Many|No|Our|Your|My)\b)"
    r"(?-i:\b[A-Z][\w'-]*)\s+(?:(?-i:[A-Z])[\w'-]*\s+){0,2}(?:" + _FC_LIST_WORD[3:-1] + r"|"
    + _FC_SOFT_WORD[3:-1] + r")\b"
    r"|\b(?:on|in|off|onto|into|from)\s+there\b")
#: What the bot says about how it feels or what it advises: not a deed.
_FC_FIRST_SOFT = re.compile(
    r"(?ix)\b(?:I|we)(?:'m|\s+am|'re|\s+are)?\s+(?:so\s+|really\s+|very\s+|truly\s+)?"
    r"(?:glad|happy|pleased|thrilled|sorry|excited|proud|relieved|delighted|honou?red|here)\b"
    r"|\b(?:I|we)(?:'d|\s+would)?\s+(?:really\s+|also\s+|strongly\s+)?(?:recommend|suggest|"
    r"advise|think|believe|guess|hope|bet|love|like|wish|imagine|reckon|feel|agree|"
    r"understand|see|know|mean|say)\b"
    # "I'd put basil in a sunny window": would, advice (not "I'd added", had).
    r"|\b(?:I|we)(?:'d|\s+would)\s+(?!(?:[a-z]+ed|been|already|just|done|gone|made|"
    r"taken|put\s+(?:it|that|them)\s+(?:on|in))\b)[a-z]+\b"
    # "I added it up and it comes to 42": a sum.
    r"|\b(?:I|we)(?:'ve|\s+have)?\s+(?:just\s+)?add(?:ed)?\s+(?:it|them|this|that|these|those|"
    r"everything|all|the\s+\w+)\s+up\b")
#: A person's own deed in her words, said back: "you finished Dune".
_FC_PLACED_ON = re.compile(
    r"(?i)\b(?:on|in|onto|into|off|from|to|under)\s+(?:the|your|my|our)\s+" + _FC_GAP
    + r"(?:" + _FC_LIST_WORD[3:-1] + r"|" + _FC_SOFT_WORD[3:-1] + r")\b")


def _fc_hers_named(clause: str, names: Any) -> bool:
    """*clause* names one of her apps or lists: one of her names (a one-word
    name only after "the", "your", ...), a list word said as hers ("the guest
    list", "your budget", "Pet Log"), or "on there"."""
    low = " " + " ".join(_WORD_RE.findall(_plain(clause).lower())) + " "
    for name in names or ():
        parts = _WORD_RE.findall(str(name or "").lower())
        if not parts or len("".join(parts)) < 3:
            continue
        for m in re.finditer(r"(?<= )(" + re.escape(" ".join(parts)) + r")s?(?= )", low):
            prev = low[:m.start()].split()
            det = prev[-1] if prev else ""
            if det in _FC_NOT_HERS:
                continue
            if len(parts) >= 2 or det in _FC_HERS_DET:
                return True
            # a one-word name written as a name: "added to Pantry"
            if str(name).strip()[:1].isupper() and re.search(
                    r"(?<=\s)" + re.escape(str(name).strip()) + r"(?![\w])", clause):
                return True
    return bool(_FC_HERS_RE.search(clause))


def _fc_anchors(clause: str, asked: Any, names: Any, rows: Any) -> dict:
    """What *clause* is about: the bot, her things, her own words, her rows."""
    words = _folded_words(clause)
    # For a writing ask ("tell me a story about a knight") her words are the
    # story's subject, not her records.
    items = ({w for w in _t_anchor_words(asked)}
             if asked is not None and not _content_turn(asked) else set())
    row_hit = False
    low = " " + " ".join(_WORD_RE.findall(_plain(clause).lower())) + " "
    for value in rows or ():
        v = " ".join(_WORD_RE.findall(str(value or "").lower()))
        if len(v) >= 3 and not v.isdigit() and (" " + v + " ") in low:
            row_hit = True
            break
    caps = re.findall(r"(?<=[\w,;] )[A-Z][a-z]{1,}", clause)
    # A name that opens the clause as the subject of a state ("Leo is now
    # RSVP'd"), not a word that opens any sentence ("The", "This").
    first = re.match(r"\W*([A-Z][a-z]+)(?:\s+[A-Z][a-z]+)?(?:'s|\s+(?:is|was|are|were|has|had|"
                     r"got|gets|now))\b", clause)
    lead_name = bool(first) and first.group(1).lower() not in _NOT_A_COMMAND \
        and first.group(1).lower() not in _STOP and first.group(1).lower() not in _T_CHEER
    deeds = _FC_FIRST_SOFT.sub(" ", clause)
    return {
        "name": bool(caps) or lead_name,
        "first": bool(_FC_FIRST.search(deeds)),
        "hers": _fc_hers_named(clause, names),
        "item": bool(words & items),
        "row": row_hit,
        "pron": bool(re.search(r"(?i)\b(?:it|that|them|these|those|everything|all\s+of\s+"
                               r"(?:it|them)|he|she|they|him|his)\b", clause)),
        "cap": bool(caps),
        "you": bool(_FC_SUBJECT_YOU.match(_FC_FIRST_SOFT.sub(" ", clause))),
        "deeds": deeds,
    }


def _fc_negated(clause: str) -> bool:
    return bool(_FC_NEGATION.search(clause) or _DISOWN.search(clause)
                or _NOT_A_CLAIM.search(clause))


_OFFER_CONDITION = re.compile(r"(?i)\b(?:if|when|whenever|once|as\s+soon\s+as|after|before|"
                             r"unless|so\s+that|while)\s+(?:you|she|he|they|we)\b")
_CLAUSE_AFTER = re.compile(r"(?i)\s+(?:out\s+)?(?:if|whether|who|what|which|how|where|when)\b")


def _fc_is_offer(sentence: str, asked: Any, names: Any) -> bool:
    """*sentence* offers a change to her things (it is taken out)."""
    text = _plain(sentence).strip()
    if not text or wording.NO_APP_CHANGE.lower() in text.lower():
        return False
    m = _FC_OFFER.search(text)
    if m:
        whole = rest = text[m.start():]
        # What is offered is the act right after "I can"; an "if",
        # "when" or "once" clause after it is HER part ("I can check if you
        # open the list app" offers a check, not a change).
        cut = _OFFER_CONDITION.search(rest, m.end() - m.start())
        if cut:
            rest = rest[:cut.start()]
        # A verb that opens a question clause ("check if", "see whether",
        # "find out who") looks something up; it changes nothing.
        acts = [v for v in _FC_OFFER_VERB.finditer(rest)
                if not _CLAUSE_AFTER.match(whole, v.end())]
        if acts or _fc_hers_named(rest, names) \
                or _FC_PLACE_THERE.search(rest):
            # "I can't add things here": a refusal, not an offer.
            return not re.search(r"(?i)\b(?:can(?:no|')?t|cannot|could\s+not|couldn't|won't|"
                                 r"unable)\b|\bcan\s+not\b|n't\b", text[:m.end() + 8])
    # "Oat milk on the grocery list?", "Add oat milk to your list?": a question
    # that puts something on one of her things.
    if text.endswith("?") and (_FC_PLACED_ON.search(text) or _names_placed(text, names) or (
            _FC_OFFER_VERB.match(text) and (_fc_hers_named(text, names)
                                            or _FC_PLACE_THERE.search(text)))):
        return True
    return False


def _names_placed(text: str, names: Any) -> bool:
    """One of her names after on, in, to, off: "on the Pet Log", "to Pantry"."""
    for name in names or ():
        n = str(name or "").strip()
        if len(n) >= 3 and re.search(r"(?i)\b(?:on|in|onto|into|to|off|from|under)\s+(?:the\s+|your\s+|"
                                     r"my\s+|our\s+)?" + re.escape(n) + r"\b", text):
            return True
    return False


#: Her thing said to hold something: "The guest list so far includes Leo".
_FC_STATE_VERB = re.compile(r"(?i)\b(?:includes?|contains?|has|have|holds?|shows?|lists?|says|"
                            r"reads|totals?|stands|sits|counts?|keeps)\b")
_FC_FUTURE_OR_FEEL = re.compile(
    r"(?ix)\b(?:going\s+to\s+be|gonna\s+be|will\s+be|'ll\s+be|can'?t\s+wait|look(?:ing)?\s+"
    r"forward|enjoy|good\s+luck|have\s+fun)\b")


def _fc_clause_claims(clause: str, asked: Any, names: Any, rows: Any,
                      question_turn: bool) -> bool:
    body = clause.strip(" \t.!,;:-")
    if not body or body.endswith("?"):
        return False
    if _fc_negated(body) and not re.search(r"(?i)\b(?:now|already|officially)\b", body):
        return False
    a = _fc_anchors(body, asked, names, rows)
    deeds = a["deeds"]
    lex = _FC_LEX_PAST_RE.search(deeds)
    past = _FC_PAST_RE.search(deeds)
    now = bool(re.search(r"(?i)\b(?:now|already|officially|all\s+set|updated)\b", body))
    history = bool(_FC_HISTORY.search(body)) and not a["first"]
    advice = bool(_FC_ADVICE.search(body) or _R2_GENERAL.match(body) or re.search(
        r"(?i)\b(?:I|we)(?:'d|\s+would)?\s+(?:really\s+|also\s+|strongly\s+)?(?:recommend|"
        r"suggest|advise)\b", body)) and not a["first"] and not now \
        and not (lex and re.search(r"(?i)\b(?:has|have|had|'ve|'s|is|are|was|were|been)\s+"
                                   r"(?:\w+\s+){0,2}?" + re.escape(lex.group(0)), body))
    text_work = bool(_FC_TEXT.search(body)) and not (a["hers"] or a["row"])
    # "based on your budget", "depends on your list": nothing is put there.
    spot = re.sub(r"(?i)\b(?:based|depend(?:s|ing)?|rel(?:y|ies)|focus(?:es|ing)?|"
                  r"spend(?:s|ing)?|keep\s+an\s+eye|work(?:s|ing)?)\s+on\b", " ", body)
    placed = bool(_FC_PLACED_ON.search(spot) or _names_placed(spot, names)
                  or _FC_PLACE_THERE.search(spot))
    # 1. It names one of her apps or lists (fail closed), unless it is advice
    # to her, a plain instruction to her ("Add him to the guest list."), or a
    # wish or praise that puts nothing anywhere ("Big Day is going to be fun").
    if a["hers"] and not history and not _ABOUT_THE_REPLY.search(body):
        harmless = advice or (_TELLS_HER.match(body) and not now) or (
            not a["first"] and not now and not placed and not lex
            and not _FC_PAST_RE.search(_FC_FIRST_SOFT.sub(" ", spot))
            and not _FC_STATE_VERB.search(body)
            and bool(_T_PRAISE.search(body) or _T_FEEL.search(body)
                     or _FC_FUTURE_OR_FEEL.search(body)
                     # "it can vary based on your family's budget": a maybe
                     or re.search(r"(?i)\b(?:can|could|might|may|would|should|depends?|"
                                  r"based\s+on|varies|vary)\b", body)))
        if not harmless:
            return True
    # 2. The bot says it did, is doing or will do a change.
    if a["first"] and not text_work:
        if _FC_FIRST_WILL.search(deeds) or _FC_FIRST_ING.search(deeds):
            return True
        if (past or lex) and not _FC_MIND.search(deeds):
            if re.search(r"(?i)\b(?:I|we)(?:'ve|'d|'ll|'m|'re)?\b[^.!?]*?\b(?:[a-z]{2,}ed|[a-z]+'d|"
                         + _FC_IRREGULAR + r"|" + _FC_LEX_PAST + r")\b", deeds):
                return True
    if _FC_LEAD_ING.match(body) and (a["hers"] or a["row"] or (a["item"] and now)
                                     or re.search(r"(?i)\bnow\b", body)) and not text_work:
        return True
    # Her own words said back ("I'm glad to hear the dishes are done") are
    # hers, unless the bot adds a change word or "now".
    her_words = set(_WORD_RE.findall(_plain(asked).lower())) if asked is not None else set()
    echo = past is not None and past.group(0).lower() in her_words and not lex and not now
    # 5. Shapes that need none of her names (a keyless caller gets none):
    # "Your dentist visit is rebooked", "The recipe box now has 42 recipes",
    # "Kai's swim times are all current", "Two plants got watered today".
    if not a["you"] and not echo and not history and not advice:
        if _FC_YOUR_DONE.search(body) or _FC_COUNT_NOW.search(body) \
                or (_FC_YOUR_STATE.search(body) and not question_turn):
            return True
        if _FC_ALL_STATE.search(body) and (a["name"] or a["row"] or a["hers"]
                                           or (a["item"] and not question_turn)):
            return True
        if past and _FC_RECENT.search(body) and _FC_PASSIVE.search(body):
            return True
    if history or advice or text_work:
        return False
    # 3. A change verb said as done about her own words, her rows, a name,
    # or (after a statement or a yes) a pronoun for them.
    # After a question her words are what she asked about ("explain compound
    # interest" -> "Interest is added to the balance"), so they anchor a
    # change verb only after a statement, a command or a yes.
    about_hers = (a["item"] and not question_turn) or a["row"] \
        or (a["pron"] and not question_turn and not a["you"])
    # A capitalised word ("Leo", "Salt", "Denver") anchors only with "now":
    # "Leo is now RSVP'd", never "Salt is added to water".
    named_now = a["name"] and now
    if lex and (about_hers or named_now or len(_WORD_RE.findall(body)) <= 5) \
            and not (a["you"] and not (a["item"] or a["row"])) \
            and not re.match(r"(?i)\s+(?:my|me|us|our|your|you)\b", deeds[lex.end():]):
        return True
    # "Leo is now RSVP'd", "the cake has been sorted": any past form in a
    # perfect or passive, about her own words or rows.
    if past and not echo and ((a["item"] and not question_turn) or a["row"] or named_now) and re.search(
            r"(?i)(?:\b(?:has|have|had|is|are|was|were|been|got|gets)|'s|'ve|'re)\s+"
            r"(?:(?:now|all|just|already|officially|also|been|successfully)\s+)*"
            r"(?:[a-z]{2,}ed|[a-z]+'d|" + _FC_IRREGULAR + r")\b", body) \
            and not (a["you"] and not (a["item"] or a["row"])):
        return True
    # 4. Her thing said to be in a state now.
    if _FC_NOW.search(body) and not a["you"] and (about_hers or named_now):
        return True
    # "Consider your fridge restocked": a done state given to anything.
    if re.match(r"(?i)^\W*consider\s+", body) and (past or lex):
        return True
    return False


_FC_PASSIVE = re.compile(
    r"(?i)(?:\b(?:has|have|had|is|are|was|were|been|got|gets)|'s|'ve|'re)\s+"
    r"(?:(?:now|all|just|already|officially|also|been|successfully)\s+)*"
    r"(?:[a-z]{2,}ed|[a-z]+'d|" + _FC_IRREGULAR + r")\b")
#: "Your dentist visit is rebooked": her thing ("your ...") in a new state.
_FC_YOUR_DONE = re.compile(
    r"(?i)\byour\s+(?:[\w'-]+\s+){0,3}?(?:is|are|was|were|has\s+been|have\s+been|got|gets)\s+"
    r"(?:(?:now|all|just|already|officially|also|successfully)\s+)*"
    r"(?:[a-z]{3,}ed|[a-z]+'d|" + _FC_IRREGULAR + r")\b")
#: Her thing said to be on its way or in place: "Your allowance is on its way".
_FC_YOUR_STATE = re.compile(
    r"(?i)\byour\s+(?:[\w'-]+\s+){0,3}?(?:is|are|'s|'re)\s+(?:now\s+|all\s+)?(?:on\s+(?:its|their|"
    r"the)\s+way|on\s+track|ready|set|in|there|sorted|queued|lined\s+up|in\s+place|"
    r"good\s+to\s+go|up\s+to\s+date|coming|covered|handled)\b")
#: A count said as new: "now has 42", "now at 43".
_FC_COUNT_NOW = re.compile(
    r"(?i)\bnow\s+(?:has|have|holds?|contains?|lists?|shows?|totals?|includes?|reads|says|at|"
    r"stands\s+at|sits\s+at|up\s+to|down\s+to)\s+[-+$£€]?\d")
#: "are all current", "is all set".
_FC_ALL_STATE = re.compile(
    r"(?i)(?:\b(?:is|are)|'s|'re)\s+all\s+(?:set|done|current|up\s+to\s+date|updated|sorted|"
    r"squared\s+away|ready|in|on|there|good|clear|caught\s+up|booked|logged|saved|prepped|packed|"
    r"stocked|taken\s+care\s+of|accounted\s+for)\b")
#: A change said to have just happened.
_FC_RECENT = re.compile(r"(?i)\b(?:today|tonight|this\s+(?:morning|afternoon|evening|week)|"
                        r"just\s+now|a\s+(?:moment|minute)\s+ago|earlier\s+today|yesterday)\b")


# Three shapes that need none of her
# names, so a model that has the chat history cannot slip a done change past
# a greeting or a question: her ask said back as carried out ("moved to the
# 14th, as requested", "the swap you asked for"), a plan moved to a day or a
# time ("now starts on the 14th"), and a change said to be in place ("the
# swap went through", "reflects your latest changes").

#: Her ask said back as carried out. "As you said" is left out: people say it
#: about opinions ("As you said, music matters").
_FC_AS_ASKED = re.compile(
    r"(?ix)\b(?:(?:just\s+|exactly\s+)?(?:as|like)\s+(?:you\s+)?(?:requested|asked|instructed|"
    r"specified|wanted|ordered)"
    r"|the\s+way\s+you\s+(?:asked|wanted|requested)"
    r"|(?:per|at)\s+your\s+(?:request|ask|instructions?|wish(?:es)?)"
    r"|you\s+(?:asked|requested|wanted)\s+(?:for|me\s+to)"
    r"|(?:that|which|what)\s+you\s+(?:requested|wanted)"
    r"|(?:requested|asked-for)\s+(?:change|swap|update|edit|move|date|time|fix)e?s?)\b")
#: What makes the said-back ask a done change rather than a promise or a
#: piece of writing: a done or state word.
_FC_DONE_SIGNAL = re.compile(
    r"(?ix)\b(?:reflects?|shows?|includes?|contains?|has|have|holds?|lists?|done|sorted|"
    r"handled|set|ready|in\s+place|taken\s+care\s+of|live|through|now|already|all\s+set|"
    r"is|are|was|were)\b")
_FC_WEEKDAY = r"(?:" + "|".join(_WEEKDAYS) + r")s?"
#: A day or a time said as when her plan now is.
_FC_WHEN = (
    r"(?:(?:the\s+)?\d{1,2}(?:st|nd|rd|th)\b(?:\s+(?:" + _FC_WEEKDAY + r"))?"
    r"|(?:" + _FC_WEEKDAY + r")\b"
    r"|(?:" + _MONTH_ALT + r")\.?\s+\d{1,2}(?:st|nd|rd|th)?\b"
    r"|\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)"
    r"|\d{1,2}:\d{2}\b|noon\b|midnight\b"
    r"|(?:next|this|the\s+following)\s+(?:week|weekend|month|year|" + _FC_WEEKDAY + r")\b"
    r"|tomorrow\b|tonight\b|the\s+weekend\b)")
_FC_WHEN_RE = re.compile(r"(?i)\b" + _FC_WHEN)
#: A plan moved to a day or a time: "moved to the 14th", "pushed to Friday".
_FC_MOVED_TO = re.compile(
    r"(?ix)\b(?:moved|shifted|pushed(?:\s+back)?|bumped|rescheduled|postponed|delayed|"
    r"brought\s+forward|pulled\s+forward|changed|switched|reset|updated|re-?booked|"
    r"slotted|pencill?ed(?:\s+in)?)\s+(?:[\w'-]+\s+){0,3}?(?:to|for|on|at|until|till|into)\s+"
    + _FC_WHEN)
#: "it's 3pm now", "this is at noon now": the clock, not her plan.
_FC_CLOCK_SUBJECT = re.compile(
    r"(?i)^\W*(?:(?:hi|hey|hello|ok|okay|so|oh|well|yes|no|sure|morning)\W+)*"
    r"(?:it|it's|its|that|that's|this|there|here|time|today|tonight|right)\b")
#: Words that make "now" mean "at this moment", not "changed".
_FC_NOW_MOMENT = re.compile(r"(?i)\b(?:right|just|for|until|by|up\s+to|as\s+of)\s+now\b")
_FC_CHANGE_NOUN = (r"(?:swap|switch|change|update|edit|correction|fix|tweak|addition|removal|"
                   r"reschedul(?:e|ing)|reassignment|new\s+(?:date|time|order|entry|entries))(?:e?s)?")
_FC_CHANGE_DET = (r"(?:the|that|this|your|those|these|my|our)\s+(?:latest\s+|recent\s+|new\s+|"
                  r"requested\s+|last\s+)?")
#: A change said to be in place: "the swap went through", "the update is in",
#: "reflects your latest changes". "The graph shows the change in
#: temperature" is a fact (a change *in*, *of*, *over* something).
_FC_CHANGE_IN_PLACE = re.compile(
    r"(?ix)\b" + _FC_CHANGE_DET + _FC_CHANGE_NOUN
    + r"\s+(?:is|are|was|were|has\s+been|have\s+been|went|got|'s|'re)\s+"
    r"(?:now\s+|all\s+|already\s+|officially\s+|successfully\s+)*(?:done|in|through|live|"
    r"complete(?:d)?|made|applied|saved|in\s+place|reflected|showing|set|finished|working|"
    r"active|in\s+effect|taken\s+care\s+of)\b"
    r"|\b(?:reflects?|reflecting|shows?|showing|includes?|including|has|have|got|picked\s+up|"
    r"captured)\s+" + _FC_CHANGE_DET + _FC_CHANGE_NOUN
    + r"\b(?!\s+(?:in|of|between|from|over|to)\b)")
#: Her own plan or thing as the subject: "your", "my", "our".
_FC_HER_SUBJECT = re.compile(r"(?i)\b(?:your|my|our)\s+[\w'-]+")
_FC_EDITS_LIVE = re.compile(
    r"(?i)\byour\s+(?:latest\s+|recent\s+|new\s+)?(?:edits?|changes?|updates?|swaps?|"
    r"corrections?|fix(?:es)?)\s+(?:is|are|'s|'re)\s+(?:now\s+)?(?:live|in|through|done|"
    r"in\s+place|in\s+effect|saved|applied|active)\b")


def _fc_no_name_claim(text: str, asked: Any, names: Any, rows: Any,
                      question_turn: bool) -> bool:
    """One whole reply sentence says a change to her things happened, in a
    shape that needs none of her names."""
    body = text.strip()
    if not body or body.rstrip().endswith("?"):
        return False
    if _FC_NEGATION.search(body) or _DISOWN.search(body) or _NOT_A_CLAIM.search(body):
        return False
    if _FC_HISTORY.search(body) or _FC_CONDITION.match(body) or _FC_ADVICE.search(body) \
            or _R2_GENERAL.match(body) or _fc_is_offer(body, asked, names):
        return False
    hers = _fc_hers_named(body, names) or bool(_FC_HER_SUBJECT.search(body))
    if not hers:
        low = " " + " ".join(_WORD_RE.findall(body.lower())) + " "
        hers = any(len(v) >= 3 and (" " + v + " ") in low for v in (
            " ".join(_WORD_RE.findall(str(r or "").lower())) for r in rows or ()))
    writing = (_FC_TEXT.search(body) or _ABOUT_THE_REPLY.search(body)
               or (asked is not None and _content_turn(asked))) and not hers
    # 1. Her ask said back as carried out.
    if _FC_AS_ASKED.search(body) and not writing and (
            _FC_DONE_SIGNAL.search(body) or _FC_PAST_RE.search(body)
            or _FC_LEX_PAST_RE.search(body)):
        return True
    if _FC_EDITS_LIVE.search(body):
        return True
    # 2. A plan moved to a day or a time ("pushed to Friday"), after any
    # turn (fail closed: the model cannot know). A plan said to be at a day
    # "now" needs her own thing after a question: "the store opens at 9" is
    # not hers.
    if _FC_MOVED_TO.search(body) and not writing:
        return True
    now_when = (bool(re.search(r"(?i)\bnow\b", body)) and not _FC_NOW_MOMENT.search(body)
                and bool(_FC_WHEN_RE.search(body)) and not _FC_CLOCK_SUBJECT.match(body))
    if now_when and not writing and (not question_turn or hers):
        return True
    # 3. A change said to be in place.
    if _FC_CHANGE_IN_PLACE.search(body) and not writing and (not question_turn or hers):
        return True
    return False


def _after_refusal(before: Any) -> bool:
    """The bot's previous message said no change ran: a model that has the
    chat history may now say the change is done, so a question is read as
    strictly as a statement."""
    return "have not changed anything in your apps" in str(before or "").lower()


# -- the backstop -------------------------------------------------------------------
#
# Lists of claim SHAPES always let some lies slip ("Hi! Max now handles the
# dishes.", "Not anymore, it's the 14th.", "Dentist -> Friday", a claim in
# Spanish). So the last rule is read from the other
# end: a sentence on a no-action turn is replaced when it is ABOUT HER LIFE and
# says something CHANGED, whatever the shape. "About her life" is wide on
# purpose: her names and rows, "your/my/our ...", a plan, visit, chore or list
# word ("the dentist", "the reunion", "dishes"), a word back to what she said
# ("what you mentioned earlier"), or her own message asking about one of
# those ("is the wedding still on the 7th?"). After a greeting, or right after
# the honest no-change line, any change word is enough. "Changed" is wide
# too: any passive or perfect verb, "now", "instead of", "no longer", "not
# anymore", "from now on", a done or state word ("set", "sorted", "gone",
# "official"), or a change verb anywhere. What stays: questions, offers,
# negations, advice, general facts ("Meetings like that are often moved"),
# history, news ("per the news"), writing she asked for (a poem about
# Friday), and anything with none of her life in it.
#
# Fail closed where the guard cannot read: an arrow status line, and a
# non-English sentence that carries a day, a date, a time or a change verb
# (or any non-English sentence when she wrote in English).

#: Plan, visit, chore and list words: a sentence about one is about her life.
_FC_LIFE_WORD = (
    r"(?:appointments?|appts?|visits?|check-?ups?|dentists?|doctors?|doctor's|vets?|"
    r"orthodontists?|pediatricians?|therap(?:y|ist|ists)|physio|haircuts?|reminders?|"
    r"reservations?|bookings?|meetings?|part(?:y|ies)|weddings?|reunions?|birthdays?|"
    r"anniversar(?:y|ies)|showers?|recitals?|rehearsals?|practices?|lessons?|class(?:es)?|"
    r"tutoring|playdates?|sleepovers?|carpools?|pick-?ups?|drop-?offs?|trips?|vacations?|"
    r"flights?|events?|plans|chores?|dishes|laundry|trash|garbage|recycling|vacuuming|"
    r"mowing|lawn|dut(?:y|ies)|allowances?|rota|rosters?|guests?|rsvps?|invites?|"
    r"invitations?|headcount|seating|lists?|to-?dos?|tasks?|groceries|grocery|pantry|"
    r"shopping|budgets?|bills?|payments?|rent|deadlines?|homework|medications?|meds|"
    r"pills?|prescriptions?|refills?|doses?|entr(?:y|ies)|calendars?|schedules?|agendas?|"
    r"charts?|trackers?|logs?|journals?)")
#: Life words that are also verbs ("she visits", "the menu lists", "it
#: charts"): hers only with a determiner that makes them hers.
_FC_LIFE_VERBISH = re.compile(
    r"(?i)^(?:visits|plans|lists|logs|charts|invites|schedules|bills|practices|showers|"
    r"books|doses|refills|trips|tasks|events|lessons|turns|shifts|swaps)$")
_FC_LIFE_RE = re.compile(r"(?i)\b" + _FC_LIFE_WORD + r"\b")
#: A determiner that makes the word general, not hers: "a dentist", "most meetings".
_FC_GENERIC_DET = re.compile(
    r"(?i)\b(?:a|an|any|every|each|most|many|some|such|no|typical|other|another|all\s+the)\s+"
    r"(?:[\w'-]+\s+)?$")
#: A determiner that makes the word hers: "the dentist", "Ava's chores".
_FC_HERS_DET_NEAR = re.compile(
    r"(?i)(?:\b(?:the|your|my|our|this|that|his|her|their)|[A-Za-z]'s)\s+(?:[\w'-]+\s+)?$")
#: A whole sentence that is a general fact or the news, not her life.
_FC_WORLD_FACT = re.compile(
    r"(?ix)\b(?:usually|typically|generally|normally|often|traditionally|commonly|"
    r"every\s+(?:year|spring|fall|autumn|winter|summer)|each\s+year|as\s+(?:it|they)\s+do(?:es)?|"
    r"in\s+general|most\s+people|many\s+people|tends?\s+to|like\s+that|"
    r"per\s+the\s+(?:news|forecast|report|league|team|school\s+district|weather\s+service)|"
    r"according\s+to\s+(?:the\s+)?(?:news|forecast|reports?|league|weather\s+service|officials)|"
    r"(?:news|reports?|the\s+forecast)\s+(?:says?|said|show|shows))\b")
#: Words back to what she said earlier: "what you mentioned", "as you asked".
_FC_BACK_REF = re.compile(
    r"(?ix)\b(?:you\s+(?:mentioned|asked|wanted|requested|said|told\s+me|brought\s+up|"
    r"wrote|meant)|we\s+(?:discussed|talked\s+about|spoke\s+about)|earlier|last\s+time|"
    r"from\s+before)\b")
#: A change spoken of as a thing: "the change", "the trash duty swap".
_FC_CHANGE_REF = re.compile(
    r"(?ix)\b(?:the|that|this|your|those|these|my|our)\s+(?:[\w'-]+\s+){0,2}?"
    r"(?:changes?|updates?|swaps?|switch(?:es)?|edits?|moves?|reschedul(?:e|ing)|fix(?:es)?|"
    r"tweaks?|corrections?|adjustments?|reassignments?)\b"
    r"(?!\s+(?:in|of|between|from|over|to|for|on)\b)")
_FC_AFTER_CHANGE = re.compile(
    r"(?i)\b(?:after|since|with|following|per)\s+(?:the|that|this|your|my)\s+"
    r"(?:[\w'-]+\s+)?(?:changes?|updates?|swaps?|switch|edits?|moves?|fix)\b")
#: A change said with a negation: "no longer", "not anymore".
_FC_NO_MORE = re.compile(r"(?i)\b(?:no\s+longer|not\s+any\s*more|any\s*more)\b")
#: Change and state words (wide on purpose; see above).
_FC_STATE_WORD = re.compile(
    r"(?ix)(?:\b(?:is|are|was|were|be|been)|'s|'re)\s+(?:now\s+|all\s+|already\s+|officially\s+|"
    r"also\s+|finally\s+)*(?:set|sorted|done|official|booked|off|gone|in|out|handled|ready|"
    r"confirmed|cancell?ed|over|through|complete|finished|fixed|squared\s+away|"
    r"taken\s+care\s+of|covered|locked\s+in|good\s+to\s+go|live|on\s+there|history|a\s+go)\b"
    r"|\b(?:gone|off|out)\s+(?:from|of)\b|\binstead\s+of\b|\bfrom\s+now\s+on\b"
    r"|\bgoing\s+forward\b|\bfor\s+good\b|\b(?:took|take|taken)\s+(?:it|them|that)\s+off\b"
    r"|\bno\s+more\b|\ball\s+(?:done|set|sorted|good)\b|\bwent\s+through\b")
#: A state dated from a moment near now: "as of this morning", "starting
#: tomorrow", "from here on". Grammar, not a claim shape: an aspect that says
#: something is different from a point on.
_FC_SINCE = re.compile(
    r"(?ix)\b(?:as\s+of|starting|beginning|effective|from)\s+(?:right\s+)?(?:now|today|"
    r"tonight|tomorrow|yesterday|this\s+(?:morning|afternoon|evening|week|weekend|month)|"
    r"next\s+\w+|last\s+\w+|(?:mon|tues|wednes|thurs|fri|satur|sun)days?|"
    r"the\s+\d{1,2}(?:st|nd|rd|th)?)\b(?:\s+on(?:wards?)?)?"
    r"|\bfrom\s+(?:here|there|then)\s+(?:on|out)\b|\beffective\s+immediately\b")
#: "now" meaning "changed", not "at this moment".
_FC_NOW_WORD = re.compile(r"(?i)\bnow\b")
#: The bot's own past deed, any verb: "I made the switch", "we bumped it".
_FC_FIRST_PAST = re.compile(
    r"(?x)(?:\bI|\b(?i:we))(?i:'ve|'d|\s+have|\s+had|\s+just|\s+already|\s+also|\s+then|"
    r"\s+went\s+ahead\s+and|\s+quickly|\s+finally)*\s+(?i:(?:" + _FC_LEX_PAST + r")|"
    r"[a-z]{3,}ed|" + _FC_IRREGULAR + r")\b")
#: A sentence that opens with a change verb, no subject: "Bumped the vet".
_FC_LEAD_PAST = re.compile(
    r"(?i)^\W*(?:(?:ok|okay|sure|done|yep|yes|hi|hey|hello|good|great|all\s+good)\W+)*"
    r"(?:" + _FC_LEX_PAST + r")\b")
#: A status line drawn with an arrow: "Dentist -> Friday".
_FC_ARROW = re.compile(r"(?:->|=>|→|⇒|➡|⟶|➔|➜|⇨)")
#: Small talk: after it, any change word in the reply is a claim.
_FC_GREETING = re.compile(
    r"(?ix)^\W*(?:hi|hello|hey|hiya|howdy|yo|sup|good\s+(?:morning|afternoon|evening|night|day)|"
    r"morning|evening|how\s+are\s+(?:you|things)|how'?s\s+it\s+going|how\s+(?:are|r)\s+u|"
    r"what'?s\s+up|whats\s+up|thanks|thank\s+you|ty|what\s+(?:day|time)\s+is\s+it|"
    r"what'?s\s+the\s+(?:date|time|day|weather)|what'?s\s+new|how\s+was\s+your\s+day)\b")
#: A question about the state of one of her things: "is milk still on ...".
_FC_STILL = re.compile(r"(?i)\bstill\b|\bany\s+(?:news|change|update)s?\b|\bdid\s+(?:that|it)\b")
#: Common English words: a sentence with none of these is not English.
_FC_EN_WORDS = frozenset((
    "the a an and or but to of in on at for with is are was were be been it its it's this that "
    "i you your we our my me he she they them his her their there here what when where who why "
    "how not no yes do does did have has had will would can could should all just so now up out "
    "hi hey hello thanks ok okay sure good great nice well day time today tomorrow by from as "
    "if than then about into over after before again too very more some any one two"
).split())
#: Days, months and change verbs in the languages a household is likeliest
#: to use (es, de, fr, it, pt, nl): a non-English sentence with one is read as
#: a possible claim (the guard cannot read it).
_FC_FOREIGN_SIGNAL = re.compile(
    r"(?ix)\b(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|montag|dienstag|"
    r"mittwoch|donnerstag|freitag|samstag|sonntag|lundi|mardi|mercredi|jeudi|vendredi|samedi|"
    r"dimanche|luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|venerd[iì]|sabato|domenica|"
    r"segunda|ter[cç]a|quarta|quinta|sexta|maandag|dinsdag|woensdag|donderdag|vrijdag|"
    r"zaterdag|zondag|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|"
    r"noviembre|diciembre|januar|februar|m[aä]rz|juni|juli|oktober|dezember|janvier|f[eé]vrier|"
    r"mars|avril|juin|juillet|ao[uû]t|octobre|d[eé]cembre|ma[nñ]ana|morgen|demain|domani|"
    r"amanh[aã]|hoy|heute|aujourd'hui|oggi|hoje|"
    r"mov[ií]\w*|movid[oa]s?|movimos|cambi[eéoa]\w*|añad\w*|agregu?[eéoa]\w*|quit[eé]|"
    r"quitad[oa]s?|borr[eéa]\w*|eliminad[oa]s?|elimin[eé]|reprogram\w*|"
    r"verschob\w*|verschieb\w*|ge[aä]ndert|hinzugef[uü]gt|entfernt|gel[oö]scht|eingetragen|"
    r"déplac\w*|changé\w*|ajouté\w*|supprimé\w*|retiré\w*|reporté\w*|spostat\w*|"
    r"aggiunt\w*|rimoss\w*|modificat\w*|alterad\w*|adicionad\w*|removid\w*|remarcad\w*|"
    r"verplaatst|toegevoegd|verwijderd|gewijzigd)\b")


#: "your ..." / "our ..." in a reply ("my" there is the bot's own).
_FC_YOUR_OUR = re.compile(r"(?i)\b(?:your|our)\s+[\w'-]+")
#: A list edit said outright: "crossed off", "taken off the list".
_FC_LIST_EDIT = re.compile(
    r"(?ix)\b(?:crossed|checked|ticked|struck|scratched|marked)\s+off\b"
    r"|\b(?:took|taken)\s+(?:the\s+|your\s+|our\s+)?(?:[\w'-]+\s+){1,3}?off\b"
    r"|\b(?:taken|took|came|come|gone|went|dropped|removed|deleted)\s+off\s+"
    r"(?:the|your|our|it|there)\b"
    r"|(?:\b(?:is|are)|'s|'re)\s+(?:now\s+)?off\s+(?:the|your|our)\s+(?:[\w'-]+\s+){0,2}?"
    + _FC_LIST_WORD + r"\b")
#: Perfect or past passive: "has been moved", "have traded", "got reassigned".
_FC_PERFECT = re.compile(
    r"(?i)(?:\b(?:has|have|had|was|were|been|got|gotten)|'ve)\s+"
    r"(?:(?:now|all|just|already|officially|also|been|successfully|finally)\s+)*"
    r"(?:[a-z]{2,}ed|[a-z]+'d|" + _FC_IRREGULAR + r")\b")
_FC_INSTEAD = re.compile(r"(?i)\binstead\b|\bfrom\s+now\s+on\b|\bgoing\s+forward\b")
#: An amount or a switch moved: "went up to $10", "the reminder is on".
_FC_AMOUNT_MOVED = re.compile(
    r"(?ix)\b(?:went|gone|goes|is|are|was|been)\s+(?:up|down)\s+(?:to|by)\b"
    r"|'s\s+(?:up|down)\s+(?:to|by)\b"
    r"|\b(?:raised|lowered|increased|decreased|bumped\s+up)\s+(?:to|by)\b"
    r"|\b(?:turned|switched)\s+(?:on|off)\b|(?:\b(?:is|are)|'s|'re)\s+(?:now\s+)?on\s*[.!]*$")
#: What a subjectless change verb acts on: "Took the bananas off", "Moved it".
_FC_LEAD_OBJECT = re.compile(
    r"(?i)\s*(?:the|your|our|my|it|them|that|this|those|these|him|her|everything|all|"
    r"off|up|out|over|in|on|to|for|back)\b|\s*(?-i:[A-Z])|\s*[.!]*\s*$")
#: A year, or plain history: "removed from the list in 2006". Only a year
#: before this one counts (see :func:`_fc_past_year`): "moved to Jan 2027"
#: is her plan.
_FC_PAST_YEAR = re.compile(r"(?i)\b(?:1[0-9]\d\d|20\d\d)s?\b|\bcentur(?:y|ies)\b|"
                           r"\bhistor\w*|\boriginally\b|\bback\s+in\b|\bancient\b")


def _fc_past_year(text: str) -> bool:
    """*text* has a year before this one, or a history word."""
    this_year = date.today().year
    for m in _FC_PAST_YEAR.finditer(text):
        y = re.match(r"\d{4}", m.group(0))
        if not y or int(y.group(0)) < this_year:
            return True
    return False


def _fc_life_named(text: str, names: Any, rows: Any, reply: bool = False) -> bool:
    """*text* names one of her things: her names or rows, "your/our ...", a
    word back to what she said, or a plan, visit, chore or list word that is
    not said generally. In a *reply* "my" is the bot's ("Dune changed my
    life"); in her own message it is hers."""
    own = _FC_YOUR_OUR if reply else _FC_HER_SUBJECT
    if _fc_hers_named(text, names) or own.search(text) or _FC_BACK_REF.search(text):
        return True
    return _fc_life_word(text, rows)


def _fc_life_word(text: str, rows: Any) -> bool:
    """*text* has one of her rows or a plan, visit, chore or list word said
    as hers ("the dentist", "Ava's chores", "Dishes"), not generally ("a
    dentist", "meetings like that", "the list of planets")."""
    low = " " + " ".join(_WORD_RE.findall(text.lower())) + " "
    if any(len(v) >= 3 and (" " + v + " ") in low for v in (
            " ".join(_WORD_RE.findall(str(r or "").lower())) for r in rows or ())):
        return True
    general = bool(_FC_WORLD_FACT.search(text))
    for m in _FC_LIFE_RE.finditer(text):
        before = text[:m.start()]
        if _FC_GENERIC_DET.search(before):
            continue
        # "Meetings like that are often moved": a bare word in a general
        # sentence is general; "the meeting", "Ava's meeting" stay hers.
        if (general or _FC_LIFE_VERBISH.match(m.group(0))) \
                and not _FC_HERS_DET_NEAR.search(before):
            continue
        # "the list of planets", "a schedule of events": a list OF something
        # general, unless it is "your/our list of ...".
        if re.match(r"\s+of\b", text[m.end():]) and not re.search(
                r"(?i)\b(?:your|our)\s+(?:[\w'-]+\s+)?$", before):
            continue
        return True
    return False


def _fc_ask_about_life(asked: Any, names: Any, rows: Any) -> bool:
    """Her question is about one of her things ("when is the dentist?", "is
    milk still on the pantry run?"): a short answer to it inherits the anchor."""
    text = _plain(asked).strip()
    if not text or _FC_GREETING.match(text):
        return False
    # Only a question: "I finally finished my taxes" is her news, and the
    # model may cheer it ("You're a tax pro now").
    if not (text.endswith("?") or is_question(text) or _QUESTION_RE.match(text)):
        return False
    return _fc_life_named(text, names, rows) or bool(_FC_STILL.search(text))


def _fc_foreign(raw: str, asked: Any) -> bool:
    """A sentence the guard cannot read: not English, and it carries a day, a
    month or a change verb in another language ("Movi al dentista al
    viernes"). Names do not count as words: "Hope Jonah feels better soon"
    is English."""
    if not _FC_FOREIGN_SIGNAL.search(raw):
        return False
    words = re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", raw)
    common = [w.lower() for i, w in enumerate(words) if i == 0 or not w[:1].isupper()]
    if len(common) < 2:
        return False
    english = sum(1 for w in common if w in _FC_EN_WORDS)
    return english * 3 < len(common)


def _fc_first_signal(body: str, ask_life: bool) -> int:
    """Where the first change or state word of *body* starts, or -1."""
    hits = []
    for rx in (_FC_NO_MORE, _FC_STATE_WORD, _FC_PERFECT, _FC_PASSIVE, _FC_LEX_PAST_RE,
               _FC_AMOUNT_MOVED, _FC_INSTEAD, _FC_SINCE):
        m = rx.search(body)
        if m:
            hits.append(m.start())
    m = _FC_NOW_WORD.search(body)
    if m and not _FC_NOW_MOMENT.search(body) and not (
            _FC_CLOCK_SUBJECT.match(body) and not ask_life and not re.search(
                r"(?i)\b(?:on|for|moved|set|booked)\b", body)):
        hits.append(m.start())
    return min(hits) if hits else -1


def _fc_backstop(raw: str, text: str, asked: Any, names: Any, rows: Any,
                 strict: bool) -> bool:
    """True when one reply sentence on a no-action turn is about her life and
    says something changed (see above)."""
    body = text.strip()
    if not body:
        return False
    if _FC_ARROW.search(raw) and re.search(r"[A-Za-z]", raw):
        return True
    if _fc_foreign(raw, asked):
        return True
    if body.endswith("?"):
        return False
    no_more = bool(_FC_NO_MORE.search(body))
    if not no_more and (_FC_NEGATION.search(body) or _DISOWN.search(body)):
        return False
    if _FC_CONDITION.match(body) or _fc_is_offer(body, asked, names):
        return False
    # A list edit said outright: "Milk has been crossed off".
    if _FC_LIST_EDIT.search(body):
        return True
    named = (_fc_hers_named(body, names) or bool(_FC_BACK_REF.search(body))
             or _fc_life_word(body, rows))
    hers = named or bool(_FC_YOUR_OUR.search(body))
    if not hers and (_FC_WORLD_FACT.search(body) or _FC_ADVICE.search(body)):
        return False
    # History: "Pluto was removed from the list of planets in 2006".
    if _fc_past_year(body) and not (_FC_YOUR_OUR.search(body)
                                    or _names_hit_folded(body, names)
                                    or _FC_FIRST.search(body)):
        return False
    writing = bool(_FC_TEXT.search(body) or _ABOUT_THE_REPLY.search(body)
                   or (asked is not None and _content_turn(asked)))
    if writing and not named:
        return False
    deed = bool(_FC_FIRST_PAST.search(body)) and not (_FC_MIND.search(body)
                                                       or _FC_FIRST_SOFT.search(body))
    # Her own news said back with a feeling ("I'm glad to hear the dishes
    # are done!" after "the dishes are done"): her words, not the bot's deed.
    if (_T_FEELING_ECHO.match(body) and not deed and not _T_STILL_CLAIM.search(body)
            and asked is not None and not _FC_GREETING.match(_plain(asked).strip())):
        ask_words = set(_WORD_RE.findall(_plain(asked).lower()))
        life = {m.group(0).lower() for m in _FC_LIFE_RE.finditer(body)}
        if not life or life <= ask_words:
            return False
    # The bot's own deed, or a change verb with no subject ("Bumped the vet
    # to Thursday", "Took the bananas off"): a claim.
    if deed:
        return True
    lead = _FC_LEAD_PAST.match(body)
    if lead:
        rest = body[lead.end():]
        if (len(_WORD_RE.findall(body.lower())) <= 4 or _FC_LEAD_OBJECT.match(rest)
                or named or _FC_PLACE_THERE.search(rest)):
            return True
    # A change spoken of as a thing ("after the change", "the swap is official").
    if _FC_AFTER_CHANGE.search(body):
        return True
    change_ref = bool(_FC_CHANGE_REF.search(body))
    ask_life = asked is not None and _fc_ask_about_life(asked, names, rows)
    at = _fc_first_signal(body, ask_life)
    if at < 0:
        return False
    # "you've done a lot today": about her, not her things (unless it says
    # she is all set, or names one of her things).
    said = [c for c in _FC_CLAUSES.split(body) if _fc_first_signal(c, ask_life) >= 0]
    about_you = bool(said) and all(_FC_SUBJECT_YOU.match(c) for c in said) and not (
        _FC_STATE_WORD.search(body) or change_ref or named or _FC_YOUR_STATE.search(body))
    # Right after the honest line a change word alone is
    # not enough when the sentence names none of her things and says nothing
    # is different in her life: "The Lakers won after the game was delayed
    # by rain." is news (u9 controls).
    strict_hit = strict and (hers or deed or _st_state_signal(body)
                             or bool(_FC_AMOUNT_MOVED.search(body)))
    if (change_ref or ask_life or strict_hit) and not about_you:
        return True
    # Her thing named before the change word ("the dentist is moved"), or
    # "now", "no longer", "instead" or a change verb anywhere ("Max now
    # handles the dishes"). "They're done just in time for the party" names
    # the party only after: it is not the party that changed.
    if hers:
        anywhere = bool(no_more or _FC_NOW_WORD.search(body) or _FC_INSTEAD.search(body)
                        or _FC_LEX_PAST_RE.search(body) or _FC_AMOUNT_MOVED.search(body))
        head = body[:at]
        if anywhere or _fc_hers_named(head, names) or _FC_BACK_REF.search(head) \
                or _FC_YOUR_OUR.search(head) or _fc_life_word(head, rows):
            return True
    # After small talk a STRONG change form is a claim ("Hi! Milk has been
    # crossed off"), unless it is about her ("you've earned it") or plain
    # present ("Salt is added to water").
    greeting = asked is not None and bool(_FC_GREETING.match(_plain(asked).strip()))
    if greeting and not _FC_SUBJECT_YOU.match(body):
        now = (bool(_FC_NOW_WORD.search(body)) and not _FC_NOW_MOMENT.search(body)
               and not _FC_CLOCK_SUBJECT.match(body))
        return bool(no_more or now or _FC_PERFECT.search(body) or _FC_STATE_WORD.search(body)
                    or _FC_INSTEAD.search(body) or _FC_AMOUNT_MOVED.search(body))
    return False


def _fc_sentence(sentence: str, asked: Any, names: Any, rows: Any,
                 strict: bool = False) -> str:
    """``"claim"``, ``"offer"`` or ``"ok"`` for one reply sentence on a turn
    where no app action ran. *strict*: the bot just said no change ran."""
    text = _plain(_CHECK_MARKS.sub(" checked ", str(sentence or ""))).strip()
    if not text:
        return "ok"
    low = text.lower()
    if wording.NO_APP_CHANGE.lower() in low or "have not changed anything" in low:
        return "ok"
    if _fc_is_offer(text, asked, names):
        return "offer"
    raw = str(sentence or "")
    # An arrow status line or a sentence the guard cannot read
    # fails closed before anything else.
    if (_FC_ARROW.search(raw) and re.search(r"[A-Za-z]", raw)) or _fc_foreign(raw, asked):
        return "claim"
    # A general fact, the news or history that names none of her things is
    # never a claim, even right after the honest line ("the game was moved to
    # Sunday because of rain, per the news"; "meetings like that are often
    # moved to Friday").
    if not _fc_life_named(text, names, rows, reply=True) and not _FC_FIRST.search(text) and (
            _FC_WORLD_FACT.search(text) or _fc_past_year(text)):
        return "ok"
    asked_q = asked is not None and (is_question(str(asked)) or bool(
        _QUESTION_RE.match(_plain(asked).strip())) or str(asked).strip().endswith("?"))
    # Right after the honest line a question is read as strictly as a
    # statement, unless the sentence names none of her things and says
    # nothing is different (news passes).
    question_turn = asked_q and (not strict or not (
        _fc_life_named(text, names, rows, reply=True) or _FC_FIRST.search(text)
        or _st_state_signal(text) or _FC_AMOUNT_MOVED.search(text)))
    if _fc_no_name_claim(text, asked, names, rows, question_turn):
        return "claim"
    clauses = [text] if _FC_CONDITION.match(text) else _FC_CLAUSES.split(text)
    for clause in clauses:
        if _fc_clause_claims(clause, asked, names, rows, question_turn):
            return "claim"
    if _fc_backstop(raw, text, asked, names, rows, strict):
        return "claim"
    return "ok"


#: Words that open a yes: "yes", "sure", "ok", "please", "sí", "oui".
_YES_OPEN = re.compile(
    r"(?ix)^\W*(?:y|ye|yes+|yea+h?|yeh|yep|yup|ya|yas|yess|sure|ok(?:ay)?|k|kk|please|pls|plz|"
    r"alright|all\s+right|absolutely|definitely|certainly|of\s+course|go\s+(?:ahead|for\s+it|on)|"
    r"do\s+it|do\s+that|sounds\s+(?:good|great|perfect)|perfect|great|awesome|cool|fine|"
    r"s[ií]|oui|ja|jawohl|sim|claro|vale|d'accord|genau|certo|va\s+bene|affirmative|indeed|"
    r"correct|right|exactly|mhm|uh\s+huh|thanks|thank\s+you|yes\s+please)\b")
_YES_MARKS = frozenset("\U0001f44d\u2705\U0001f44c\U0001f64f\u2714\u2611\U0001f4af\U0001f197"
                       "\u2764\U0001f64c\U0001f44f\u2665\U0001f60a\U0001f600\U0001f642\ufe0f"
                       "\U0001f3fb\U0001f3fc\U0001f3fd\U0001f3fe\U0001f3ff\u200d")


def is_yes(text: Any) -> bool:
    """*text* is a yes, as people say it: "yes!! thank you", "Sure, go
    ahead", "y", a thumbs-up, "sí", "yes and almond milk"."""
    raw = str(text or "").strip()
    if not raw:
        return False
    marks = [c for c in raw if not c.isspace()]
    if marks and all(c in _YES_MARKS or not (c.isalnum() or c in "'?") for c in marks) \
            and any(c in _YES_MARKS for c in marks):
        return True
    plain = _plain(raw).strip()
    if not plain or plain.endswith("?") or len(plain.split()) > 10:
        return False
    # Thanks is not a yes: "thanks", "ok thanks", "great, thank you" after a
    # read ("19 guests in Big Day: ...") is gratitude, and the model answers it.
    if _THANKS_ONLY.match(plain):
        return False
    return bool(_YES_OPEN.match(plain) or _GO_AHEAD_ANY.match(plain))


#: Gratitude with at most an okay or a cheer in front, and no go-ahead word.
_THANKS_ONLY = re.compile(
    r"(?ix)^\W*(?:(?:ok|okay|great|perfect|cool|awesome|nice|lovely|got\s+it|alright)\W+)*"
    r"(?:thanks|thank\s+you|thx|ty|cheers|much\s+appreciated|appreciate\s+it)"
    r"(?:\s+(?:so\s+much|a\s+lot|again|puff|buddy|very\s+much))*\W*$")


def mentions_her_things(before: Any, names: Any = ()) -> bool:
    """The bot's previous message names one of her apps or lists, a list or
    app word, or offers a change: a yes after it is a change ask."""
    text = _plain(before).strip()
    if not text:
        return False
    # A question that puts something on, in or off a named thing ("Want the
    # sitter's 4 hours on the Babysitter Hours?") or asks about a change.
    asks = any(s.strip().endswith("?") and (
        _FC_OFFER_VERB.search(s) or re.search(
            r"\b(?:on|in|onto|into|to|off|from|under)\s+(?:the\s+|your\s+|my\s+)?[A-Z][a-z]", s))
        for s in _SENTENCE_RE.findall(text[-1500:]))
    return bool(asks or _names_hit_folded(text, names) or _THING_WORD.search(text)
                or _fc_hers_named(text, names) or _FC_PLACE_THERE.search(text)
                or any(_fc_is_offer(s, None, names) for s in _SENTENCE_RE.findall(text[-1500:]))
                or _offers_change(before, names))


def yes_to_her_things(asked: Any, before: Any, names: Any = ()) -> bool:
    """A yes said right after the bot talked about her apps or lists."""
    return before is not None and is_yes(asked) and mentions_her_things(before, names)


def _row_strings(obj: Any, out: list, depth: int = 0, limit: int = 600) -> None:
    if len(out) >= limit or depth > 6:
        return
    if isinstance(obj, str):
        s = obj.strip()
        if s[:1] in "[{" and len(s) < 200_000:
            try:
                _row_strings(json.loads(s), out, depth + 1, limit)
                return
            except ValueError:
                pass
        if 3 <= len(s) <= 60 and re.search(r"[A-Za-z]{3}", s) and not re.match(r"(?i)^(?:https?:|"
                                                                               r"data:|[a-f0-9-]{16,}$)", s):
            out.append(s)
    elif isinstance(obj, dict):
        for v in list(obj.values())[:200]:
            _row_strings(v, out, depth + 1, limit)
    elif isinstance(obj, list):
        for v in obj[:500]:
            _row_strings(v, out, depth + 1, limit)


def row_values(*sources: Any, limit: int = 600) -> list[str]:
    """Short text values in her apps' data (never sent anywhere): a reply
    sentence that names one with a change verb is a claim."""
    out: list[str] = []
    for src in sources:
        _row_strings(src, out, 0, limit)
    seen: set[str] = set()
    uniq = []
    for v in out:
        k = v.lower()
        if k not in seen and k not in _STOP:
            seen.add(k)
            uniq.append(v)
    return uniq[:limit]


# -- two kinds of turn the model is not trusted with --------------------------------
#
# Lists of claim SHAPES on the model's reply always miss a shape ("Sure did!",
# "Hey! Leo Park is history.", a claim in Japanese). So this rule is
# structural, from her side:
#
# 1. A question about whether a change happened (:func:`status_turn`): "did
#    you add Leo Park?", "did that work?", "so Leo Park is off?". The truth is
#    a fact the bot keeps (the change log, ``integrate.actions``), so it is
#    answered from the log (:func:`status_answer`) and the model is never
#    asked. Should a path still reach the model, the whole reply is replaced.
# 2. Small talk (:func:`small_talk_turn`): "hi", "thanks", "good morning".
#    A reply to small talk may only be small talk: every sentence that is not
#    a question must be made only of conversational words
#    (:data:`_CHATTER_WORDS`). That is an ALLOW list: a new claim shape, a
#    name, a list item or any other language fails closed.
#
# Everything else keeps the earlier rules, with one narrowing: right after the
# honest no-change line, news that names none of her things and says nothing
# changed in her life ("The Lakers won after the game was delayed by rain.")
# reaches her.

_ST_LEAD = re.compile(
    r"(?i)^\W*(?:(?:so|and|but|wait|ok|okay|hey|um+|uh+|oh|well|then|hm+|puff|sorry|"
    r"quick\s+question|just\s+checking|real\s+quick|hold\s+on|remind\s+me)\W+)+")
_ST_AUX = re.compile(r"(?i)^(?:did|didn'?t|have|haven'?t|has|hasn'?t|had|is|isn'?t|are|"
                     r"aren'?t|was|wasn'?t|were|does|do)\b")
_ST_ASKS_BOT = re.compile(
    r"(?i)^(?:did|didn'?t|have|haven'?t|has|hasn'?t|had)\s+(?:you|u|ya|puff|the\s+bot)\s+"
    r"(?:(?:already|actually|really|just|ever|yet|go\s+ahead\s+and|manage\s+to|get\s+to|"
    r"remember\s+to|end\s+up|been\s+able\s+to)\s+)*(?P<rest>\S.*)$")
_ST_YOU_DID = re.compile(r"(?i)^(?:you|u|puff)\s+(?:(?:already|just|actually|really)\s+)*"
                         r"(?P<rest>\S.*)$")
_ST_BACK = re.compile(
    r"(?i)^(?:did|didn'?t|has|hasn'?t|have|haven'?t|had|is|isn'?t|was|wasn'?t|are|were|does)\s+"
    r"(?:it|that|this|those|these|they|everything|all\s+(?:of\s+)?(?:it|that))\b")
_ST_DONE_ONLY = re.compile(
    r"(?i)^(?:(?:all\s+)?(?:done|set|sorted|good|finished|saved|added|removed|gone|updated|"
    r"changed|fixed)|it\s+worked|that\s+worked|worked|went\s+through|got\s+it\s+in)$")
#: What "did that / is it ..." asks about when it asks about a change.
_ST_BACK_DONE = re.compile(
    r"(?i)^(?:(?:really|actually|all|now|already|finally)\s+)*(?:work|worked|working|go\s+through|"
    r"went\s+through|gone\s+through|happen|happened|stick|stuck|save|saved|land|landed|take|"
    r"took|done|finished|complete|completed|set|sorted|ok|okay|good|fixed|updated|changed|"
    r"there|gone|in|on|off|out|through)\b")
_ST_BACK_END = re.compile(r"(?i)\b(?:in|on|off|out|there|through|gone|done|set)\s*(?:now|yet|"
                          r"already)?\s*$")
_ST_WH = re.compile(r"(?i)^(?:who|whom|whose|what|what's|whats|which|when|where|why|how)\b")
#: Record-change verbs, base form: "did you ADD", "you REMOVED it?".
_ST_CHANGE_BASES = frozenset("""
add put remove delete take change set update edit mark move cancel schedule reschedule
book save log record note enter tick cross clear swap switch assign reassign drop invite
uninvite file include exclude erase scratch strike rename store register rsvp confirm
archive replace bump push pencil jot
""".split())
_ST_IRREGULAR = {"took": "take", "taken": "take", "put": "put", "set": "set", "wrote": "write",
                 "written": "write", "struck": "strike", "got": "get", "gotten": "get",
                 "made": "make", "did": "do", "done": "do", "sent": "send"}


def _st_bases(word: str) -> set:
    """Base forms *word* may be: "added" -> add, "moved" -> move, "took" -> take."""
    w = word.lower().strip("'")
    out = {w}
    if w in _ST_IRREGULAR:
        out.add(_ST_IRREGULAR[w])
    for suffix, repl in (("ied", "y"), ("ed", ""), ("ed", "e"), ("d", ""), ("ing", ""),
                         ("ing", "e"), ("s", "")):
        if w.endswith(suffix) and len(w) > len(suffix) + 1:
            base = w[:-len(suffix)] + repl
            out.add(base)
            if len(base) > 2 and base[-1] == base[-2]:
                out.add(base[:-1])                   # "dropped" -> "drop"
    return out


def _st_change_rest(rest: str, names: Any) -> bool:
    """*rest* (after "did you") asks about a change to her things."""
    rest = re.sub(r"(?i)^been\s+", "", rest.strip())
    words = rest.split()
    if not words:
        return False
    first = re.sub(r"[^\w']", "", words[0])
    bases = _st_bases(first)
    tail = " ".join(words[1:])
    for base in sorted(bases):
        if base in _ST_CHANGE_BASES and change_turn(f"{base} {tail}".strip(), names):
            return True
    if bases & _ST_CHANGE_BASES:
        # Writing she asked for ("did you write a poem?") is not a change.
        low = rest.lower()
        return not (_CONTENT_WORD.search(low) or _TEXT_THING.search(low) or _MATH.match(low))
    # "did you get Leo on the guest list?": her thing named after a place word.
    return _names_placed(rest, names) or bool(_FC_PLACED_ON.search(rest))


def _st_about_her_apps(before: Any, names: Any) -> bool:
    """The bot's previous message was about her apps: the honest line, a
    change it reported, or her apps and lists named."""
    text = str(before or "")
    if not text.strip():
        return False
    low = text.lower()
    return (_after_refusal(before) or "done in " in low or low.startswith("undone")
            or "what i put in" in low or mentions_her_things(before, names))


def _st_state_signal(text: str) -> bool:
    return bool(_FC_STATE_WORD.search(text) or _FC_NO_MORE.search(text)
                or _FC_SINCE.search(text) or _FC_INSTEAD.search(text)
                or re.search(r"(?i)\b(?:still|anymore|now|yet)\b", text)
                or _FC_LIST_EDIT.search(text))


def status_turn(asked: Any, names: Any = (), before: Any = None, rows: Any = ()) -> bool:
    """True when her message asks whether a change to her things happened:
    "did you add Leo Park to the guest list?", "have you taken eggs off?",
    "did that work?", "is it done?", "so Leo Park is off?", "you added it?".
    Such a question is answered from the change log, never by the model.

    *before* is the bot's previous message when the path has one: "did that
    work?" and "so Leo is off?" are about her apps only after the bot talked
    about them (or when there is no earlier message to go by)."""
    text = _plain(asked).strip()
    if not text or len(text) > 240 or "\n" in text:
        return False
    q = _ST_LEAD.sub("", text).strip()
    core = q.rstrip(" ?!.")
    if not core:
        return False
    is_q = text.rstrip().endswith("?") or bool(_ST_AUX.match(q))
    if not is_q or _ST_WH.match(q):
        return False
    m = _ST_ASKS_BOT.match(core)
    if m:
        return _st_change_rest(m.group("rest"), names)
    no_history = before is None or not str(before).strip()
    about_apps = _st_about_her_apps(before, names)
    if _ST_BACK.match(core):
        # "did that work?", "is it done?", "has it been added?" ("is it going
        # to rain?" is not about a change, whatever came before).
        rest = _ST_BACK.sub("", core, count=1).strip()
        return (about_apps or no_history) and bool(
            _ST_BACK_DONE.search(rest) or _ST_BACK_END.search(rest)
            or any(_st_bases(w) & _ST_CHANGE_BASES for w in _WORD_RE.findall(rest.lower())
                   if w.endswith(("ed", "en")) or w in _ST_IRREGULAR))
    if _ST_DONE_ONLY.match(core):
        return about_apps or no_history
    if text.rstrip().endswith("?"):
        m = _ST_YOU_DID.match(core)
        if m and _st_change_rest(m.group("rest"), names):
            return True                                  # "you added it?"
        named = (_names_hit(core, names) or _fc_life_word(core, rows)
                 or _fc_hers_named(core, names))
        if (about_apps or (named and names)) and _st_state_signal(core):
            return True                                  # "so Leo Park is off?"
    return False


#: Words that compare with before ("is Leo off the list NOW?"): a question
#: with one is about a change, not about what the app holds.
_SINCE_BEFORE = re.compile(r"(?i)\b(?:now|yet|anymore|any\s+more|still|already|no\s+longer)\b")


def row_read_question(asked: Any, rows: Any = ()) -> bool:
    """Her question names one of her rows (by its
    first name too: "is Leo on the list?") and asks what the app holds, not
    whether a change happened. It is answered from the app's rows, so the
    change-log answer (:func:`status_turn`) does not take it."""
    text = _plain(asked).strip()
    if not text or not _asked_question(text) or not rows:
        return False
    if grounding.change_question(text) or _SINCE_BEFORE.search(text):
        return False
    names = [str(r) for r in rows if isinstance(r, str) and len(_norm(r)) >= 3]
    return _named_row(text, names) is not None


def status_answer(*, keyless: bool = False) -> str:
    """The answer to :func:`status_turn`, from the change log only."""
    if keyless:
        return wording.NO_CHANGE_KEYLESS
    lines, minutes = actions.recent_changes()
    if not lines:
        return f"{wording.STATUS_NONE.format(minutes=minutes)} {wording.STATUS_CHECK}"
    return wording.STATUS_SOME.format(minutes=minutes, lines=" ".join(lines[-5:]))


#: Small talk she may send: greetings, thanks, goodbyes, "how are you".
_SMALL_TALK_WORDS = frozenset("""
hi hello hey hiya howdy yo heya hallo hola bonjour ciao salut ola gm gn morning afternoon
evening night good day there puff buddy friend pal thanks thank you ty thx tysm cheers much
so a lot very again appreciate it appreciated bye goodbye later see ya soon cya nite take
care how are doing going it's is how's hows whats what's up sup ok okay cool nice great
awesome perfect lovely alright sweet lol haha hehe yay wow please and too as well all
""".split())
#: The only words a reply to small talk may be made of (an ALLOW list).
_CHATTER_WORDS = _SMALL_TALK_WORDS | frozenset("""
welcome back anytime problem no worries pleasure glad happy help helping hope hoping
private personal home
wonderful fantastic fine well feel feeling here if need needs anything else something just
let me know ask asking what can could would like want do for your yours i i'm im i'd i'll
i've me my myself we we're our us am be been being the an or but to of with on in at
really ready whenever whatever assistant helper bot sure course take see you're youre
you'll you've that's thats that this today tonight weekend week enjoy have has had same
not too smile always nice meet meeting hear hearing chat chatting talk talking around
today's start started day's rest relax hug love fun time times way going doing been
things thing find how's everything
""".split())
_WORDISH = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)*|\d+")
_SELF_INTRO = re.compile(r"(?i)\b(?:i'm|i\s+am|this\s+is|my\s+name\s+is|call\s+me)\s+"
                         r"(?-i:[A-Z][\w-]*)(?:\s+(?-i:[A-Z][\w-]*))?")


def small_talk_turn(asked: Any) -> bool:
    """Her whole message is small talk: "hi", "thanks!", "good morning puff",
    "how are you?"."""
    words = _WORDISH.findall(_plain(asked).lower())
    return bool(words) and len(words) <= 8 and all(w in _SMALL_TALK_WORDS for w in words)


#: What else a reply to small talk may be, besides chatter (each one still
#: has to pass every older rule, read as English and carry no change word):
#: the bot saying what it can or cannot do, a wish for her, a suggestion it
#: hands her, or a general or history fact that names none of her things.
_ST_CAN = re.compile(
    r"(?i)^\W*(?:(?:hi|hello|hey|sure|of\s+course|absolutely)\W+)*(?:i|we)\s*(?:can(?:not|'t)?|"
    r"could(?:n't)?|would|'d|do\s+not|don't|did\s+not|didn't|have\s+not|haven't|'ve\s+not|"
    r"am\s+not|'m\s+not|won't|am\s+(?:happy|glad|"
    r"here)|'m\s+(?:happy|glad|here|always\s+here)|love\s+to|would\s+love)\b")
_ST_WISH = re.compile(
    r"(?i)^\W*(?:enjoy|have\s+(?:a|an|fun|some)|let\s+me\s+know|feel\s+free|take\s+care|"
    r"ask\s+me|good\s+luck|best\s+of\s+luck|hope\s+(?:you|your)|happy|congrats|"
    r"congratulations|sounds\s+(?:good|great|fun))\b")
_ST_SUGGEST = re.compile(
    r"(?i)\bhere(?:'s|\s+is|\s+are)\s+(?:a|an|some|one|two|three|my|today's)\s+"
    r"(?:[\w'-]+\s+){0,2}?(?:pick|picks|idea|ideas|tip|tips|suggestion|suggestions|recipe|"
    r"recipes|joke|jokes|fact|facts|quote|song|songs|thought|recommendation)s?\b")
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def _english_readable(sentence: str) -> bool:
    """The guard can read *sentence*: most of its words are English letters,
    and at least a third of them are common English or chatter words."""
    words = _WORDISH.findall(_plain(sentence).lower())
    words = [w for w in words if not w.isdigit()]
    if not words:
        return True
    latin = [w for w in words if _LATIN_WORD.fullmatch(w)]
    if len(latin) * 4 < len(words) * 3:
        return False
    common = sum(1 for w in latin if w in _FC_EN_WORDS or w in _CHATTER_WORDS)
    return len(latin) < 3 or common * 3 >= len(latin)


def _pure_wish(text: str, names: Any, rows: Any) -> bool:
    """A wish for her and nothing else ("Hope your day is off to a great
    start"): every clause with a state word is the wish itself, no first-person
    deed, and none of her things (her names, rows, or a plan, chore or list
    word) is in it."""
    if not _ST_WISH.match(text) or _FC_FIRST_PAST.search(text):
        return False
    if _fc_life_word(text, rows) or _fc_hers_named(text, names) or _names_hit(text, names):
        return False
    return all(_ST_WISH.match(c.strip()) for c in _FC_CLAUSES.split(text)
               if c.strip() and (_st_state_signal(c) or _FC_AMOUNT_MOVED.search(c)))


def _small_talk_ok(sentence: str, names: Any, rows: Any) -> bool:
    """A non-question sentence of a reply to small talk may reach her."""
    if _chatter_only(sentence):
        return True
    raw = str(sentence or "")
    text = _plain(_CHECK_MARKS.sub(" checked ", raw)).strip()
    if _CHECK_MARKS.search(raw) or _FC_ARROW.search(raw) or not _english_readable(text):
        return False
    # "Hope your day is off to a great start": a state word inside the wish
    # itself is the wish. Any other clause with one ("..., Leo is out now")
    # fails closed, and so does a wish about her things.
    if (_st_state_signal(text) or _FC_AMOUNT_MOVED.search(text)) \
            and not _pure_wish(text, names, rows):
        return False
    # History or a general fact that names none of her things ("Pluto was
    # removed from the list of planets in 2006"), read as the older rules do.
    if _fc_past_year(text) and not (_FC_YOUR_OUR.search(text) or _names_hit_folded(text, names)
                                    or _FC_FIRST.search(text)):
        return True
    if _FC_WORLD_FACT.search(text) and not (
            _fc_life_named(text, names, rows, reply=True) or _names_hit(text, names)
            or _FC_FIRST_PAST.search(text)):
        return True
    if _FC_PERFECT.search(text):
        return False
    return bool(_ST_CAN.match(text) or _ST_WISH.match(text) or _ST_SUGGEST.search(text))


def _chatter_only(sentence: str) -> bool:
    """*sentence* is made only of conversational words. The bot introducing
    itself by name ("I'm WeddingBot") is allowed; any other name is not."""
    text = _SELF_INTRO.sub(" i'm ", _plain(sentence))
    words = _WORDISH.findall(text.lower())
    return all(w in _CHATTER_WORDS for w in words)


def _fc_finish(parts: list[str], replacement: str) -> str:
    text = "".join(parts).strip()
    return text if text else replacement


def _judge(sentence: str, asked: Any, names: Any, rows: Any, strict: bool = False) -> str:
    """``"claim"``, ``"offer"`` or ``"ok"``: the older shape rules, then the
    fail-closed rules. *strict*: see :func:`_after_refusal`."""
    if _fc_is_offer(sentence, asked, names):
        return "offer"
    # A reply to small talk is small talk, plus the few
    # kinds :func:`_small_talk_ok` allows (an allow list: anything else,
    # in any shape or language, fails closed). Plain chatter and a pure wish
    # are read by the allow list alone.
    body = str(sentence or "").strip()
    small = asked is not None and small_talk_turn(asked)
    if small and body and (_chatter_only(body) or (
            _english_readable(body) and not _CHECK_MARKS.search(body)
            and _pure_wish(_plain(body).strip(), names, rows))):
        return "ok"
    if claims_app_action(sentence, asked, names):
        return "claim"
    verdict = _fc_sentence(sentence, asked, names, rows, strict)
    if verdict == "ok" and body and _unreadable_or_named(body, asked, names, rows):
        return "claim"
    if (verdict == "ok" and body and small
            and not body.endswith("?") and "have not changed anything" not in body.lower()
            and not _small_talk_ok(body, names, rows)):
        return "claim"
    return verdict


def _unreadable_or_named(body: str, asked: Any, names: Any, rows: Any) -> bool:
    """Structural (see :mod:`grounding`): a sentence in
    a language or script she did not write in, or a sentence the guard
    cannot read as English that names one of her things."""
    low = body.lower()
    if "have not changed anything" in low or wording.NO_APP_CHANGE.lower() in low:
        return False
    return (grounding.unreadable_switch(body, asked, _english_readable)
            or grounding.foreign_names_hers(body, names, rows, _english_readable))


def _asked_question(asked: Any) -> bool:
    """Her message is a question (any language: "?", "¿", or an English
    yes-or-no opening such as "did you remember to add bread")."""
    text = _plain(asked).strip()
    return grounding.is_question(text) or bool(_YES_NO_Q.match(text))


def scrub_app_claims(text: Any, honest: str = "", asked: Any = None,
                     names: Any = (), before: Any = None, rows: Any = (),
                     status: str = "") -> tuple[str, int]:
    """*text* with every claimed app action replaced by one honest sentence
    and every offer of a change taken out. ``(text, count)``; unchanged,
    character for character, when nothing claims or offers. *asked* is the
    owner's message (see :func:`claims_app_action`).

    Called only for a reply no app action preceded (every real action is
    answered before the model, from its own evidence). After an ask for a
    change to her things (:func:`change_turn`) the whole reply is the honest
    sentence. *before* is the bot's previous message, when known: a yes after
    it named her apps or lists is such an ask too. *rows* are short values
    from her apps (paired callers and the voice key only; never sent).

    *status* is what the change log says (``status_answer``), from the
    caller: a reply that only says yes or no to her question ("He was.",
    "Sí.") asserts the question itself, so it is replaced by that
    (:func:`grounding.asserts_her_question`)."""
    body = str(text or "")
    if not body.strip():
        return body, 0
    replacement = honest or wording.NO_APP_CHANGE
    if asked is not None and (change_turn(asked, names, before)
                              or status_turn(asked, names, before, rows)):
        if body.strip() == replacement.strip():
            return body, 0
        logger.info("integrate: a reply to a change ask that did not run was replaced")
        return replacement, 1
    if asked is not None and grounding.asserts_her_question(
            body, asked, names, rows, _SENTENCE_RE.findall, _asked_question(asked)):
        logger.info("integrate: a bare yes or no to her question was replaced by the change log")
        return (status or replacement), 1
    out: list[str] = []
    fixed = 0
    strict = _after_refusal(before)
    for sentence in _SENTENCE_RE.findall(body):
        verdict = _judge(sentence, asked, names, rows, strict)
        if verdict == "offer":
            fixed += 1
            if sentence.endswith("\n"):
                out.append("\n")
            continue
        if verdict == "claim":
            fixed += 1
            if not any(replacement in o for o in out):
                lead = sentence[:len(sentence) - len(sentence.lstrip())]
                out.append(lead + replacement + ("\n" if sentence.endswith("\n") else ""))
            continue
        out.append(sentence)
    if not fixed:
        return body, 0
    logger.info("integrate: a reply that claimed or offered an app action was corrected")
    return _fc_finish(out, replacement), fixed


def guard_app_claims(tokens: Any, honest: str = "", asked: Any = None, names: Any = (),
                     before: Any = None, rows: Any = (), status: str = ""):
    """:func:`scrub_app_claims` for a streamed reply: tokens are held until a
    sentence ends, then the sentence (or the honest one, or nothing for an
    offer) is passed on. After an ask for a change to her things, no token is
    passed on at all: the stream is the honest sentence alone."""
    replacement = honest or wording.NO_APP_CHANGE
    if asked is not None and (change_turn(asked, names, before)
                              or status_turn(asked, names, before, rows)):
        close = getattr(tokens, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001 - the upstream is being dropped
                pass
        logger.info("integrate: a streamed reply to a change ask that did not run was replaced")
        yield replacement
        return
    buf = ""
    said_honest = False
    said_any = False
    strict = _after_refusal(before)
    # While every sentence so far only says yes or no to her
    # question, nothing is passed on (the whole reply may assert it).
    held: Optional[list[str]] = (
        [] if grounding.may_assert(asked, names, rows, _asked_question(asked)) else None)

    def judge_one(sentence: str):
        nonlocal said_honest, said_any
        verdict = _judge(sentence, asked, names, rows, strict)
        if verdict == "offer":
            return
        if verdict == "claim":
            if not said_honest:
                said_honest = said_any = True
                yield sentence[:len(sentence) - len(sentence.lstrip())] + replacement
            return
        if sentence.strip():
            said_any = True
        yield sentence

    def judge(sentence: str):
        nonlocal held
        if held is not None:
            if not sentence.strip() or grounding.elliptical(sentence, asked):
                held.append(sentence)
                return
            waiting, held = held, None
            for s in waiting:
                yield from judge_one(s)
        yield from judge_one(sentence)

    got_words = False
    for token in tokens:
        buf += str(token or "")
        got_words = got_words or bool(buf.strip())
        while True:
            m = re.search(r"[.!?\n]", buf)
            if m is None:
                break
            end = m.end()
            while end < len(buf) and buf[end] in ".!?":
                end += 1
            if end == len(buf) and buf[end - 1] in ".!?":
                break                          # "..." may not be finished yet
            sentence, buf = buf[:end], buf[end:]
            yield from judge(sentence)
        if len(buf) > 2000:                    # no sentence end in sight
            yield from judge(buf)
            buf = ""
    if buf:
        yield from judge(buf)
    if not got_words:
        # The brain said nothing at all: that is not a claim to correct.
        logger.info("integrate: the streamed reply was empty")
        yield wording.NO_BRAIN_ANSWER
        return
    if held is not None and any(s.strip() for s in held):
        logger.info("integrate: a streamed bare yes or no to her question was replaced "
                    "by the change log")
        yield status or replacement
        return
    if not said_any:
        yield replacement


__all__ = ["SYSTEM_PROMPT", "asks_for_change", "change_turn", "choose", "claims_app_action",
           "ground", "guard_app_claims", "is_question", "is_yes", "mentions_her_things",
           "read_pick", "read_reply", "row_values", "scrub_app_claims", "small_talk_turn",
           "status_answer", "status_turn", "try_answer", "yes_to_her_things"]
