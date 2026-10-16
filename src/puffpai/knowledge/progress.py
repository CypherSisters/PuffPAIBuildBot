"""How far along learning each book is, and the only words used to say so.

One small JSON file per book at ``<state>/learning/<book id>.json``, written
by the one process learning that book (at most once a second, with
:func:`puffpai.fsutil.atomic_write`) and read by everything else: the API,
``puffpai knowledge status``, the dashboard card and the bot's own answer to
"are you done learning?". All of them turn a record into words with
:func:`plain_lines`, so they cannot disagree.

Time left is worked out from characters of text done, not articles: articles
vary from one line to a hundred thousand characters, and counting them was
27% wrong in the bench (characters: 5%). No time is given until it has been
measured on this computer: during a run, only after 3% of the text and 20
seconds; before a run, only if an earlier run here recorded its speed
(``rates.json``).

This module also holds the "someone is talking" heartbeat. The API writes it
on every chat turn; a learner in ANY process (the backend, or a terminal
``puffpai knowledge sync``) reads it and pauses.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import math
import os
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from puffpai import fsutil

#: A chat turn counts as "someone is talking" for this long after it starts.
TALK_WINDOW_SECONDS = 60.0
#: No time estimate before this share of the text and this many seconds.
ESTIMATE_MIN_SHARE = 0.03
ESTIMATE_MIN_SECONDS = 20.0
#: Time left is the speed over the last this-many seconds.
RATE_WINDOW_SECONDS = 60.0
#: A record in a running stage that has not been written for this long
#: belongs to a process that stopped.
STALE_SECONDS = 180.0
#: At most one progress write per this many seconds (unless forced).
WRITE_EVERY = 1.0

RUNNING = ("fetching", "quick", "ready", "meaning", "paused")
SURFACES = ("chat", "terminal", "browser")

_ID_OK = re.compile(r"^[a-z0-9_]{1,120}$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def learning_dir() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "learning")


def _record_path(book_id: str) -> str:
    if not _ID_OK.match(str(book_id or "")):
        raise ValueError("not a book id")
    return os.path.join(learning_dir(), f"{book_id}.json")


# -- the talking heartbeat ------------------------------------------------------------

def _talk_path() -> str:
    return os.path.join(learning_dir(), "talking.txt")


def note_talking() -> None:
    """Called on every chat turn: learning pauses for a while. Never raises."""
    try:
        fsutil.atomic_write(_talk_path(), f"{time.time():.3f}")
    except Exception:  # noqa: BLE001 - a missed beat only means no pause
        pass


def someone_talking(window: float = TALK_WINDOW_SECONDS) -> bool:
    """True when a chat turn started within *window* seconds (any process)."""
    try:
        stamp = float(fsutil.read_text(_talk_path()).strip() or 0)
    except (OSError, ValueError):
        return False
    return 0 <= time.time() - stamp < window


# -- measured speed on this computer ------------------------------------------------

def _rates_path() -> str:
    return os.path.join(learning_dir(), "rates.json")


#: Faster than any computer learns (the proof PC does about 40,000 a second).
MAX_CHARS_PER_S = 50_000_000.0


def measured_rate() -> Optional[float]:
    """Characters per second of deep learning measured here, or None."""
    data = fsutil.read_json(_rates_path(), {}) or {}
    try:
        rate = float(data.get("meaning_chars_per_s") or 0)
    except (TypeError, ValueError):
        return None
    # A damaged or hand-edited record (infinity, NaN, absurd speed) is not a
    # measurement: no number is better than a made-up one.
    return rate if math.isfinite(rate) and 0 < rate <= MAX_CHARS_PER_S else None


def record_rate(chars_per_s: float, *, cpu_share: float) -> None:
    if not math.isfinite(chars_per_s) or not 0 < chars_per_s <= MAX_CHARS_PER_S:
        return
    try:
        fsutil.atomic_write_json(_rates_path(), {
            "meaning_chars_per_s": round(float(chars_per_s), 1),
            "cpu_share": cpu_share, "measured_at": _now_iso()})
    except OSError:
        pass


def estimate_seconds(chars_total: int) -> Optional[int]:
    """How long learning *chars_total* would take here, or None if never timed."""
    rate = measured_rate()
    if not rate or chars_total <= 0:
        return None
    return int(chars_total / rate)


# -- reading ----------------------------------------------------------------------------

def read(book_id: str) -> Optional[dict[str, Any]]:
    try:
        rec = fsutil.read_json(_record_path(book_id))
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def read_all() -> list[dict[str, Any]]:
    """Every book's record, by name. Stale running records say ``stopped``.

    A book set aside (revoked) since it was learned is left out: it no
    longer answers, so it is not reported as ready.
    """
    folder = learning_dir()
    try:
        from .learn import set_aside
        hidden = set_aside()[0]
    except Exception:  # noqa: BLE001 - set_aside never raises; import guard only
        hidden = set()
    out = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json") or name == "rates.json":
            continue
        if name[:-5] in hidden:
            continue
        rec = read(name[:-5])
        if rec:
            out.append(settled(rec))
    return sorted(out, key=lambda r: str(r.get("name") or r.get("id") or "").lower())


def settled(rec: dict[str, Any], now: Optional[float] = None) -> dict[str, Any]:
    """*rec*, with a running stage nobody has written for a while shown as stopped."""
    if rec.get("stage") not in RUNNING:
        return rec
    try:
        stamp = datetime.fromisoformat(str(rec.get("updated_at"))).timestamp()
    except (TypeError, ValueError):
        stamp = 0.0
    if (now or time.time()) - stamp > STALE_SECONDS:
        rec = dict(rec)
        if rec["stage"] == "fetching":
            rec["stage"] = "fetch_stopped"
        else:
            rec["stage"] = "stopped" if rec.get("quick_ready") else "quick_stopped"
    return rec


def remove(book_id: str) -> None:
    try:
        os.unlink(_record_path(book_id))
    except (OSError, ValueError):
        pass


# -- writing -----------------------------------------------------------------------------

class Writer:
    """The one writer of one book's record. Every write is best-effort."""

    def __init__(self, book_id: str, name: str) -> None:
        self.path = _record_path(book_id)
        old = read(book_id) or {}
        self.rec: dict[str, Any] = {
            "id": book_id, "name": name,
            "stage": old.get("stage") or "quick",
            "quick_ready": bool(old.get("quick_ready")),
            "quick": old.get("quick") or {"done": 0, "total": 0},
            "meaning": old.get("meaning") or {},
            "resumed_from_pct": None, "error": "",
            "updated_at": _now_iso(),
        }
        self._last_write = 0.0
        self._run_start: Optional[float] = None
        self._run_chars0 = 0
        self._samples: deque = deque()

    def update(self, *, force: bool = False, **fields: Any) -> None:
        self.rec.update(fields)
        self.rec["updated_at"] = _now_iso()
        now = time.monotonic()
        if not force and now - self._last_write < WRITE_EVERY:
            return
        self._last_write = now
        try:
            fsutil.atomic_write_json(self.path, self.rec)
        except Exception:  # noqa: BLE001 - a progress write never stops learning
            pass

    # quick stage
    def quick(self, done: int, total: int) -> None:
        self.update(stage="quick", quick={"done": int(done), "total": int(total)})

    # meaning stage
    def start_meaning(self, *, articles_total: int, chars_total: int,
                      articles_done: int, chars_done: int) -> None:
        pct = int(100 * chars_done / chars_total) if chars_total else 0
        self._run_start = time.monotonic()
        self._run_chars0 = chars_done
        self._samples.clear()
        self._samples.append((self._run_start, chars_done))
        self.update(force=True, stage="meaning",
                    resumed_from_pct=(pct if chars_done > 0 else None),
                    meaning={"articles_done": articles_done,
                             "articles_total": articles_total,
                             "chars_done": chars_done, "chars_total": chars_total,
                             "seconds_left": None, "basis": None})

    def resume_clock(self) -> None:
        """After a pause: the speed window starts again (pauses are not work)."""
        now = time.monotonic()
        done = int((self.rec.get("meaning") or {}).get("chars_done") or 0)
        self._samples.clear()
        self._samples.append((now, done))

    def meaning(self, articles_done: int, chars_done: int, *,
                stage: str = "meaning") -> None:
        m = dict(self.rec.get("meaning") or {})
        m["articles_done"] = int(articles_done)
        m["chars_done"] = int(chars_done)
        now = time.monotonic()
        if stage == "meaning":
            self._samples.append((now, chars_done))
            while len(self._samples) > 2 and now - self._samples[1][0] > RATE_WINDOW_SECONDS:
                self._samples.popleft()
        m["seconds_left"], m["basis"] = self._seconds_left(now, m)
        self.update(stage=stage, meaning=m)

    def _seconds_left(self, now: float, m: dict[str, Any]) -> tuple:
        total = int(m.get("chars_total") or 0)
        done = int(m.get("chars_done") or 0)
        if self._run_start is None or total <= 0:
            return None, None
        if (done - self._run_chars0) < ESTIMATE_MIN_SHARE * total:
            return None, None
        if now - self._run_start < ESTIMATE_MIN_SECONDS or len(self._samples) < 2:
            return None, None
        (t0, c0), (t1, c1) = self._samples[0], self._samples[-1]
        if t1 - t0 <= 0 or c1 - c0 <= 0:
            return m.get("seconds_left"), m.get("basis")
        rate = (c1 - c0) / (t1 - t0)
        return int(max(0, total - done) / rate), "measured"

# -- words ----------------------------------------------------------------------------------

def duration_words(seconds: Optional[float]) -> str:
    """'less than a minute', 'about 2 minutes', 'about 3.5 hours'."""
    if seconds is None:
        return ""
    s = max(0.0, float(seconds))
    if s < 60:
        return "less than a minute"
    minutes = round(s / 60)
    if minutes < 90:
        return "about 1 minute" if minutes == 1 else f"about {minutes} minutes"
    hours = round(s / 1800) / 2
    text = f"{hours:g}"
    return f"about {text} hour" if hours == 1 else f"about {text} hours"


def _n(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _action(kind: str, name: str, surface: str) -> str:
    if kind == "start":
        return {"terminal": f'To start it, run: puffpai knowledge deeper "{name}"',
                "browser": "Press Learn it fully to start.",
                }.get(surface, "You can start it from the Knowledge page.")
    if kind == "carry_on":
        return {"terminal": "To carry on, run: puffpai knowledge sync",
                "browser": "Press Carry on to pick up where it left off.",
                }.get(surface, "It picks up where it left off the next time "
                               "your bot starts.")
    if kind == "helper":
        return "Run BuildBot's setup again to get it."
    return ""


def plain_lines(rec: dict[str, Any], surface: str = "chat") -> list[str]:
    """The words for one book's record. The only wording source for learning."""
    if not isinstance(rec, dict):
        return []
    surface = surface if surface in SURFACES else "chat"
    rec = settled(rec)
    name = str(rec.get("name") or "This book")
    stage = str(rec.get("stage") or "")
    ready = f"{name}: ready to answer questions."
    m = rec.get("meaning") or {}
    q = rec.get("quick") or {}
    counts = (f"{_n(m.get('articles_done'))} of {_n(m.get('articles_total'))} "
              "articles")
    pct = 0
    if m.get("chars_total"):
        pct = int(100 * int(m.get("chars_done") or 0) / max(1, int(m["chars_total"])))

    if stage == "waiting":
        if rec.get("error") == "switched_off":
            return [f"{name}: waiting. Its website is switched off on What "
                    "leaves your computer, so it is not fetched."]
        if rec.get("error") == "kept_offline":
            return [f"{name}: not fetched. This bot is set not to use the "
                    "internet."]
        return [f"{name}: waiting to be fetched. It starts in the background "
                "while your bot is running."]
    if stage == "fetching":
        f = rec.get("fetch") or {}
        got = (f"{_n(f.get('done'))} of {_n(f.get('total'))} pages so far"
               if f.get("total") else "starting")
        return [f"Fetching {name}: {got}.",
                "It can answer questions a few seconds after it arrives."]
    if stage == "fetch_stopped":
        return [f"Fetching {name} stopped part way.",
                "It carries on the next time your bot starts."]
    if stage == "quick":
        total = int(q.get("total") or 0)
        share = int(100 * int(q.get("done") or 0) / total) if total else 0
        return [f"Getting {name} ready to answer questions: {share}% done."]
    if stage == "quick_stopped":
        return [f"Getting {name} ready to answer questions stopped part way.",
                _action("carry_on", name, surface)]
    if stage == "done":
        return [f"{name}: fully learned."]
    if stage == "error":
        return [f"{name}: I could not learn it. {error_words(rec.get('error'))}"]
    lines = [ready]
    if stage == "ready":
        lines.append("Learning it fully, so it also matches questions worded "
                     "differently, starts in a moment.")
    elif stage == "meaning":
        left = duration_words(m.get("seconds_left")) if m.get("basis") == "measured" else ""
        if left:
            lines.append(f"Learning {name}: {counts}, {left} left.")
        else:
            lines.append(f"Learning {name}: {counts}. I will tell you how long "
                         "it takes once I have timed some of it.")
        if rec.get("resumed_from_pct") is not None:
            lines.append(f"Picked up where I left off: {rec['resumed_from_pct']}% "
                         "done.")
    elif stage == "paused":
        lines.append(f"Learning {name}: {counts}, paused while you talk to me.")
    elif stage == "stopped":
        lines.append(f"Learning it fully stopped part way: {pct}% done.")
        lines.append(_action("carry_on", name, surface))
    elif stage == "not_started":
        wait = duration_words(estimate_seconds(int(m.get("chars_total") or 0)))
        lines.append("Learning it fully, so it also matches questions worded "
                     "differently, has not started.")
        lines.append(f"It would take {wait} on this computer." if wait else
                     "I will tell you how long it takes once I have timed it "
                     "on this computer.")
        lines.append(_action("start", name, surface))
    elif stage == "waiting_helper":
        lines.append("To also match questions worded differently, it needs the "
                     "reading helper (83 MB), which is not on this computer.")
        lines.append(_action("helper", name, surface))
    return [line for line in lines if line]


_ERRORS = {
    "file_incomplete": "The book file is incomplete. Download it again.",
    "no_room": "There is not enough free space on this computer.",
    "empty": "The book file has no text I can read.",
    "not_fetched": "It could not be fetched. Look for it again under Find "
                   "more knowledge.",
}


def error_words(code: Any) -> str:
    return _ERRORS.get(str(code or ""), "Something went wrong while reading it.")


def all_lines(surface: str = "chat") -> list[str]:
    """Every book's lines, in name order."""
    out: list[str] = []
    for rec in read_all():
        out.extend(plain_lines(rec, surface))
    return out


# -- the bot's own answer -------------------------------------------------------------------

_ASKS = re.compile(
    r"\b(?:are|r)\s+(?:you|u)\s+(?:done|finished|still)\s+(?:learning|reading)"
    r"|\bhow\s+(?:far\s+along|much\s+longer|long\s+until)\b.*\b(?:learn|read|book)"
    r"|\b(?:done|finished)\s+(?:learning|reading)\s+(?:the|your|my)?\s*books?\b"
    r"|\bstill\s+learning\b",
    re.IGNORECASE)


def asks_about_learning(message: Any) -> bool:
    return bool(_ASKS.search(str(message or "")))


def chat_answer() -> Optional[str]:
    """What the bot says when asked how its learning is going, or None."""
    recs = read_all()
    if not recs:
        return None
    lines = []
    for rec in recs:
        lines.extend(plain_lines(rec, "chat"))
    return " ".join(lines)


__all__ = ["TALK_WINDOW_SECONDS", "Writer", "all_lines", "asks_about_learning",
           "chat_answer", "duration_words", "error_words", "estimate_seconds",
           "learning_dir", "measured_rate", "note_talking", "plain_lines", "read",
           "read_all", "record_rate", "remove", "settled", "someone_talking"]
