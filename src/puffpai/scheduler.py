"""The bot's clock: one background thread that runs what the owner turned on.

Ported from HomeBotBuild's ``scheduler.py`` (the engine and its Source
protocol; HBB's alarm, calendar and medication sources are household apps this
build does not have and are left out). BuildBot had no clock at all: the
Majordomo's ``schedule`` intent wrote a line nothing ever read.

A **source** is any object with these methods (duck-typed):

``name``
    Short identifier, used in logs and in the occurrence token.
``refresh()``
    Re-read whatever the source schedules, once per tick.
``occurrences()``
    Everything currently armed: ``{"id", "due_ms", "kind", "item", ...}``.
    Optional keys: ``sentence`` (what to say), ``late_ok_seconds`` (how late it
    may still run), ``announce_missed`` (default True).
``mark(occ, status, iso, ms)``
    Record ``fired``/``missed`` on the underlying row, in memory.
``sweep(now_ms, ring_timeout_ms)``
    Resolve anything left hanging. May be a no-op returning ``[]``.
``flush()``
    Persist, once, if anything changed.
``run(occ)`` *(optional)*
    Do the work for a fired occurrence. Without it the engine says
    ``occ["sentence"]`` through :mod:`puffpai.announcer`.

What the engine decides, so no source re-decides it:

* **At most once.** An occurrence token (``source:id@due_ms``) is persisted,
  and the source's own write lands BEFORE the work runs. Losing one update to
  a crash beats sending it twice after a restart.
* **Fired or missed.** Late by less than the grace (or the occurrence's own
  ``late_ok_seconds``) means run now. Later than that while the bot was off
  means missed: recorded, and announced only if the occurrence wants it.
* **Nothing from before it existed.** An occurrence that came due before its
  item was created is recorded as missed and never run (HBB's 01:12 rule).

Clock, announce sink and sources are all injected, so the policy is testable
without a thread or a wall clock.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

STATE_FILENAME = "scheduler.json"

#: Routines are daily; a 30-second tick is plenty and costs one small read.
DEFAULT_TICK_SECONDS = 30

#: How late an occurrence may be and still count as on time.
GRACE_SECONDS = 120

RING_TIMEOUT_SECONDS = 300
_RECENT_TOKENS_MAX = 200
MISSED_WINDOW_SECONDS = 24 * 3600
_FIRST_SEEN_MAX = 400
_CREATED_FIELDS = ("created_ms", "created_at", "approved_at")

STATUS_FIRED = "fired"
STATUS_MISSED = "missed"


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).astimezone().isoformat()


def created_ms(row) -> Optional[int]:
    """Epoch-ms at which *row* was created (or approved), or None."""
    if not isinstance(row, dict):
        return None
    for key in _CREATED_FIELDS:
        value = row.get(key)
        if value is None or value == "" or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            number = float(value)
            if number > 0:
                return int(number if number > 1e11 else number * 1000)
            continue
        try:
            text = str(value)
            when = datetime.fromisoformat(text[:-1] + "+00:00"
                                          if text.endswith("Z") else text)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.astimezone()
        return int(when.timestamp() * 1000)
    return None


def occurrence_item_key(source_name, occ) -> str:
    row = occ.get("item")
    ident = str(row.get("id") or "") if isinstance(row, dict) else ""
    if not ident:
        ident = str(occ.get("id") or "").split("@", 1)[0]
    return f"{source_name}:{ident}"


def missed_sentence(occurrences: list) -> str:
    labels: list[str] = []
    for occ in occurrences:
        label = str(occ.get("label") or occ.get("sentence") or "")
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return ""
    if len(labels) == 1:
        return f"I was switched off, so I missed this: {labels[0]}."
    return (f"I was switched off, so I missed {len(labels)} things: "
            + ", ".join(labels[:-1]) + f" and {labels[-1]}.")


def _state_dir() -> str:
    from puffpai.config import get_state_dir
    return get_state_dir()


class Scheduler:
    """Daemon thread that runs every source's due occurrences."""

    def __init__(self, sources=None, *, announce=None,
                 tick_seconds: int = DEFAULT_TICK_SECONDS,
                 grace_seconds: int = GRACE_SECONDS,
                 ring_timeout_seconds: int = RING_TIMEOUT_SECONDS,
                 missed_window_seconds: int = MISSED_WINDOW_SECONDS,
                 clock=None, state_path: Optional[str] = None):
        self.sources = list(sources) if sources is not None else None
        self._announce = announce
        self.tick_seconds = max(1, int(tick_seconds))
        self.grace_seconds = max(0, int(grace_seconds))
        self.ring_timeout_seconds = max(1, int(ring_timeout_seconds))
        self.missed_window_seconds = max(0, int(missed_window_seconds))
        self._now = clock or time.time
        self._state_path = state_path
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._state: Optional[dict] = None
        self._tick_lock = threading.Lock()

    # ── collaborators ──────────────────────────────────────────────────────
    def _get_sources(self) -> list:
        if self.sources is None:
            self.sources = build_default_sources()
        return self.sources

    def _say(self, text: str, source: str = "scheduler") -> None:
        if not text:
            return
        if self._announce is not None:
            try:
                self._announce(text)
            except Exception as exc:
                logger.warning("scheduler: announce sink failed: %s", exc)
            return
        try:
            from puffpai import announcer
            announcer.announce(text, source=source)
        except Exception as exc:
            logger.warning("scheduler: announce failed: %s", exc)

    # ── persisted cursor ───────────────────────────────────────────────────
    def state_path(self) -> str:
        if self._state_path is None:
            self._state_path = os.path.join(_state_dir(), STATE_FILENAME)
        return self._state_path

    def _load_state(self) -> dict:
        if self._state is not None:
            return self._state
        state: dict = {"last_checked": None, "recent_tokens": [], "first_seen": {}}
        try:
            path = self.state_path()
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as fh:
                    stored = json.load(fh)
                if isinstance(stored, dict):
                    state.update(stored)
        except Exception as exc:
            logger.debug("scheduler: state unreadable, starting fresh: %s", exc)
        if not isinstance(state.get("recent_tokens"), list):
            state["recent_tokens"] = []
        if not isinstance(state.get("first_seen"), dict):
            state["first_seen"] = {}
        self._state = state
        return state

    def _save_state(self) -> None:
        state = self._load_state()
        state["recent_tokens"] = list(state["recent_tokens"])[-_RECENT_TOKENS_MAX:]
        try:
            path = self.state_path()
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(tmp, path)
        except Exception as exc:
            logger.warning("scheduler: could not persist state: %s", exc)

    # ── the policy ─────────────────────────────────────────────────────────
    def tick(self) -> dict:
        """One pass over every source. Returns what it did."""
        with self._tick_lock:
            return self._tick()

    def _tick(self) -> dict:
        now = self._now()
        now_ms = int(now * 1000)
        stamp = iso(now)
        state = self._load_state()
        last_checked = state.get("last_checked")
        recent = list(state.get("recent_tokens") or [])
        was_absent = (last_checked is None
                      or (now - float(last_checked)) > max(self.grace_seconds,
                                                           2 * self.tick_seconds))
        window_floor_ms = (
            int(float(last_checked) * 1000) - self.missed_window_seconds * 1000
            if last_checked is not None else None)
        first_seen = dict(state.get("first_seen") or {})
        seen_keys = set()

        fired: list[tuple] = []
        missed: list[dict] = []
        silent: list[dict] = []
        resolved: list[dict] = []

        for source in self._get_sources():
            try:
                source.refresh()
            except Exception as exc:
                logger.warning("scheduler: source %s could not be read: %s",
                               getattr(source, "name", source), exc)
                continue
            dirty = False
            try:
                resolved_here = source.sweep(now_ms, self.ring_timeout_seconds * 1000)
            except Exception as exc:
                logger.warning("scheduler: sweep failed for %s: %s",
                               getattr(source, "name", "?"), exc)
                resolved_here = []
            if resolved_here:
                resolved.extend(resolved_here)
                dirty = True
            try:
                occurrences = source.occurrences()
            except Exception as exc:
                logger.warning("scheduler: source %s could not be listed: %s",
                               getattr(source, "name", "?"), exc)
                occurrences = []

            source_name = getattr(source, "name", "?")
            for occ in occurrences:
                due = occ.get("due_ms")
                if not due:
                    continue
                key = occurrence_item_key(source_name, occ)
                seen_keys.add(key)
                born = created_ms(occ.get("item"))
                if born is None:
                    if key in first_seen:
                        try:
                            born = int(first_seen[key])
                        except (TypeError, ValueError):
                            born = now_ms
                    else:
                        born = (int(float(last_checked) * 1000)
                                if (was_absent and last_checked is not None)
                                else now_ms)
                    first_seen[key] = born
                if due > now_ms:
                    continue
                token = f"{source_name}:{occ.get('id')}@{int(due)}"
                if token in recent:
                    continue
                late = (now_ms - int(due)) / 1000.0
                late_ok = max(self.grace_seconds,
                              int(occ.get("late_ok_seconds") or 0))
                quiet = False
                floor = born
                if window_floor_ms is not None:
                    floor = max(floor, window_floor_ms)
                if late <= self.grace_seconds:
                    # The ordinary case: a tick a few seconds after the due time.
                    status = STATUS_FIRED
                elif int(due) < floor:
                    # Came due before the item existed (or was approved), or
                    # is older than anything we could have been expected to
                    # see: never ours to run.
                    status, quiet = STATUS_MISSED, True
                elif late <= late_ok:
                    status = STATUS_FIRED
                else:
                    status = STATUS_MISSED if was_absent else STATUS_FIRED
                try:
                    source.mark(occ, status, stamp, now_ms)
                except Exception as exc:
                    logger.warning("scheduler: could not mark %s: %s", token, exc)
                    continue
                occ["status"] = status
                if quiet or (status == STATUS_MISSED
                             and occ.get("announce_missed") is False):
                    occ["announced"] = False
                    silent.append(occ)
                    logger.info("scheduler: %s (%s) recorded as missed, not run",
                                occ.get("id"), occ.get("kind"))
                elif status == STATUS_MISSED:
                    missed.append(occ)
                else:
                    # The engine's own clock, so the work says the same
                    # "now" the engine decided on (and tests can drive it).
                    occ["now_ms"] = now_ms
                    fired.append((source, occ))
                recent.append(token)
                dirty = True

            if dirty:
                # The write lands BEFORE any work runs: at most once.
                try:
                    source.flush()
                except Exception as exc:
                    logger.error("scheduler: could not persist %s: %s",
                                 getattr(source, "name", "?"), exc)

        state["last_checked"] = now
        state["recent_tokens"] = recent[-_RECENT_TOKENS_MAX:]
        if len(first_seen) > _FIRST_SEEN_MAX:
            first_seen = {k: v for k, v in first_seen.items() if k in seen_keys}
        state["first_seen"] = first_seen
        self._save_state()

        ran: list[dict] = []
        for source, occ in fired:
            logger.info("scheduler: running %s (%s)", occ.get("id"), occ.get("kind"))
            runner = getattr(source, "run", None)
            if callable(runner):
                try:
                    occ["result"] = runner(occ)
                except Exception as exc:
                    logger.warning("scheduler: %s failed: %s", occ.get("id"), exc)
                    occ["result"] = {"ok": False, "error": str(exc)}
            else:
                self._say(occ.get("sentence") or "")
            ran.append(occ)
        if missed:
            self._say(missed_sentence(missed))

        return {"fired": ran, "missed": missed, "resolved": resolved,
                "silent": silent}

    # ── thread plumbing ────────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # a broken tick must not end the thread
                logger.warning("scheduler: tick failed: %s", exc)
            self._stop.wait(self.tick_seconds)

    def start(self) -> "Scheduler":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="puffpai-scheduler", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        state = self._load_state()
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "tick_seconds": self.tick_seconds,
            "last_checked": state.get("last_checked"),
            "sources": [getattr(s, "name", "?") for s in (self.sources or [])],
        }


def build_default_sources() -> list:
    """Everything this build schedules. New kinds append one line here."""
    from puffpai import feeds, routines
    return [routines.RoutineSource(), feeds.FeedRefreshSource()]


_scheduler: Optional[Scheduler] = None
_scheduler_lock = threading.Lock()


def get_scheduler() -> Scheduler:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is None:
            _scheduler = Scheduler()
        return _scheduler


def start_scheduler() -> Optional[Scheduler]:
    """Start the singleton. None when ``PUFFPAI_SCHEDULER=0``."""
    if os.environ.get("PUFFPAI_SCHEDULER", "1").strip() == "0":
        return None
    return get_scheduler().start()


def reset_for_tests() -> None:
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None:
            _scheduler.stop()
        _scheduler = None


__all__ = ["Scheduler", "get_scheduler", "start_scheduler", "build_default_sources",
           "STATUS_FIRED", "STATUS_MISSED", "reset_for_tests"]
