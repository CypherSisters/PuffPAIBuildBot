"""How much BuildBot may ask each outside source, per build and per minute.

Two layers:

* **Per build** (a :class:`SearchBudget` from :func:`new_budget`): hard caps on
  how many calls one plan (or one Add-page search) may make to each source, so
  a build is polite even when every cache is cold.
* **Process-wide, per source**: what the source itself said about its limits on
  its last answer (``x-ratelimit-remaining``, ``-reset``, ``-resource``), plus
  the times of our own recent calls. Persisted at
  ``<state>/cache/search_budget.json`` so a restart does not forget that GitHub
  asked us to wait. GitHub's keyless search limit is shared by every device on
  the home network, so the headers on each answer are the only honest
  measure; ``/rate_limit`` is never asked as a pre-check (measured: it said 10
  left while search answered 403).

On a rate limit (a 429, or a 403 that says it is one) the source's reset time
decides: at most 60 seconds away, the owner sees ``... waiting N seconds`` and
the call is tried once more; further away, the source is marked *later* for
this build and the plan says it did not get to look there. Any other 403 (one
blocked project, say) refuses only that answer. Nothing here sends anything
itself.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Callable, Optional

MAX_WAIT_S = 60
WINDOW_S = 60.0
#: How a refusal names a rate limit in its body (GitHub: "API rate limit exceeded",
#: "You have exceeded a secondary rate limit").
_LIMIT_BODY_RE = re.compile(r"rate[ -]?limit", re.I)

#: Per build, in the wizard. The search caps are the spec's (GitHub's keyless
#: search allowance is 10 a minute for the whole home network). The reading
#: caps allow a close look at 20 or more finds per job, not 5: reading a find
#: costs no search allowance, only
#: a version pin from github.com and a few small files from the raw host.
CAPS_WIZARD: dict[str, int] = {
    "github_search": 10, "github_raw": 800, "github_git": 100, "github_core": 8,
    "github_core_pin": 3, "github_core_tree": 5, "github_core_tree_code": 3, "details": 100,
    "github_core_name": 2,
    "codeberg": 2, "codeberg_git": 20, "codeberg_raw": 120,
    "npm": 4, "npm_downloads": 30, "npm_raw": 30, "pypi": 3, "huggingface": 2,
    "commons": 2, "openverse": 2, "commons_thumb": 20, "openverse_thumb": 20,
    "wikipedia": 2, "web": 2, "github_zip": 100, "codeberg_zip": 20,
}
#: Per Add-page or chat search (one job at a time): smaller on purpose.
CAPS_ADD: dict[str, int] = {
    "github_search": 4, "github_raw": 200, "github_git": 25, "github_core": 3,
    "github_core_pin": 1, "github_core_tree": 2, "github_core_tree_code": 1, "details": 25,
    "github_core_name": 1,
    "codeberg": 1, "codeberg_git": 5, "codeberg_raw": 30,
    "npm": 2, "npm_downloads": 10, "npm_raw": 10, "pypi": 1, "huggingface": 1,
    "commons": 1, "openverse": 1, "commons_thumb": 10, "openverse_thumb": 10,
    "wikipedia": 1, "web": 1, "github_zip": 25, "codeberg_zip": 5,
}
#: Our own pace per source, before the source has to tell us (keyless numbers).
PER_MIN: dict[str, int] = {"github_search": 10, "openverse": 20, "openverse_thumb": 20}

#: The name a person reads for each source (owner-facing).
LABELS: dict[str, str] = {
    "github_search": "GitHub", "github_raw": "GitHub", "github_git": "GitHub",
    "github_core": "GitHub", "github_zip": "GitHub", "codeberg": "Codeberg",
    "codeberg_zip": "Codeberg", "codeberg_git": "Codeberg",
    "codeberg_raw": "Codeberg", "npm": "npm",
    "commons": "Wikimedia Commons", "openverse": "Openverse",
    "wikipedia": "Wikipedia", "web": "Web search",
}


#: Why BuildBot waits, which decides the words the owner reads.
WHY_ASKED = "asked"          # the site's answer said so (a rate limit it named)
WHY_PACE = "pace"            # BuildBot's own pace for that site
WHY_REFUSED = "refused"      # turned away with no reason given (the older fetch path)


def _seconds(n: int) -> str:
    return "1 second" if n == 1 else f"{n} seconds"


def waiting_line(source: str, seconds: int, why: str = WHY_ASKED) -> str:
    label = LABELS.get(source, "That site")
    wait = _seconds(int(seconds))
    if why == WHY_PACE:
        return f"BuildBot is waiting {wait} so it does not ask {label} too often."
    if why == WHY_REFUSED:
        return (f"{label} turned BuildBot away, so it is waiting {wait} "
                "before it tries once more.")
    return f"{label} asked BuildBot to slow down, so it is waiting {wait}."


def later_line(source: str) -> str:
    label = LABELS.get(source, "That site")
    return f"{label} is busy right now, so BuildBot did not get to look there this time."


# ---------------------------------------------------------------------------
# process-wide state, one record per source
# ---------------------------------------------------------------------------

_LOCK = threading.RLock()
_SHARED: dict[str, dict[str, Any]] = {}
_LOADED_FROM: Optional[str] = None


def state_path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "cache", "search_budget.json")


def _load() -> None:
    """Read the persisted file once per state directory (tests pin their own)."""
    global _LOADED_FROM
    path = state_path()
    if _LOADED_FROM == path:
        return
    _SHARED.clear()
    _LOADED_FROM = path
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict) or data.get("schema") != "search_budget/v1":
        return
    for source, rec in (data.get("sources") or {}).items():
        if isinstance(source, str) and isinstance(rec, dict):
            _SHARED[source] = {
                "remaining": rec.get("remaining") if isinstance(rec.get("remaining"), int) else None,
                "reset": float(rec["reset"]) if isinstance(rec.get("reset"), (int, float)) else None,
                "resource": str(rec.get("resource") or "")[:40],
                "calls": [float(t) for t in rec.get("calls") or [] if isinstance(t, (int, float))][-100:],
            }


def _save() -> None:
    path = state_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"schema": "search_budget/v1", "sources": _SHARED}, fh)
        os.replace(tmp, path)
    except OSError:
        pass  # a budget that cannot be saved still works in memory


def _rec(source: str) -> dict[str, Any]:
    _load()
    return _SHARED.setdefault(source, {"remaining": None, "reset": None, "resource": "",
                                       "calls": []})


def forget() -> None:
    """Drop the in-memory state (tests; the file is re-read on next use)."""
    global _LOADED_FROM
    with _LOCK:
        _SHARED.clear()
        _LOADED_FROM = None


def _int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class SearchBudget:
    """Per-build caps over the process-wide per-source state.

    ``remaining(source)`` is the calls left for this build (``None`` for a source
    with no cap); ``spent()`` what this build used; ``later(source)`` whether the
    source asked for a wait longer than :data:`MAX_WAIT_S`.
    """

    def __init__(self, *, wizard: bool, clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep,
                 caps: Optional[dict[str, int]] = None) -> None:
        self.wizard = bool(wizard)
        self.caps = dict(caps if caps is not None else (CAPS_WIZARD if wizard else CAPS_ADD))
        self._spent: dict[str, int] = {}
        self._later: set[str] = set()
        self.clock = clock
        self.sleeper = sleeper

    # -- the three pinned readers ------------------------------------------------
    def remaining(self, source: str) -> Optional[int]:
        cap = self.caps.get(source)
        if cap is None:
            return None
        if source in self._later:
            return 0
        return max(0, cap - self._spent.get(source, 0))

    def spent(self) -> dict[str, int]:
        return dict(self._spent)

    def later(self, source: str) -> bool:
        return source in self._later

    # -- used by the searchers -----------------------------------------------------
    def mark_later(self, source: str, progress: Optional[Callable[[str], None]] = None) -> None:
        if source not in self._later:
            self._later.add(source)
            if progress:
                progress(later_line(source))

    def _wait(self, source: str, seconds: float,
              progress: Optional[Callable[[str], None]], why: str = WHY_ASKED) -> None:
        whole = max(1, int(seconds + 0.999))
        if progress:
            progress(waiting_line(source, whole, why))
        self.sleeper(float(whole))

    def take(self, source: str, *pools: str,
             progress: Optional[Callable[[str], None]] = None) -> bool:
        """Spend one call from *source* (and any extra *pools*), pacing first.

        False, and nothing spent, when a cap is reached or the source is later.
        Waits (at most :data:`MAX_WAIT_S`) when the source's last answer said
        nothing is left until a reset that close, or when our own calls in the
        last minute reached the source's pace.
        """
        names = (source,) + tuple(pools)
        with _LOCK:
            if any(n in self._later for n in names):
                return False
            if any(self.remaining(n) == 0 for n in names):
                return False
            rec = _rec(source)
            now = self.clock()
            wait, why = 0.0, WHY_ASKED
            if rec.get("remaining") == 0 and rec.get("reset"):
                wait = max(wait, float(rec["reset"]) - now)
            pace = PER_MIN.get(source)
            recent = [t for t in rec["calls"] if now - t < WINDOW_S]
            if pace and len(recent) >= pace and recent[-pace] + WINDOW_S - now > wait:
                wait, why = recent[-pace] + WINDOW_S - now, WHY_PACE
            if wait > MAX_WAIT_S:
                self.mark_later(source, progress)
                return False
        if wait > 0:
            self._wait(source, wait, progress, why)
        with _LOCK:
            # Checked again where the call is counted: finds are read in
            # parallel, and another reader may have spent the last call (or the
            # source may have been marked later) while this one waited.
            if any(n in self._later for n in names) or any(self.remaining(n) == 0 for n in names):
                return False
            rec = _rec(source)
            now = self.clock()
            if rec.get("remaining") == 0 and rec.get("reset") and float(rec["reset"]) <= now:
                rec["remaining"] = None
            rec["calls"] = [t for t in rec["calls"] if now - t < WINDOW_S][-100:] + [now]
            for n in names:
                self._spent[n] = self._spent.get(n, 0) + 1
            _save()
        return True

    def observe(self, source: str, *, status: Optional[int], headers: Optional[dict],
                progress: Optional[Callable[[str], None]] = None,
                retried: bool = False, body: Optional[bytes] = None) -> str:
        """Read one answer. Returns ``"ok"``, ``"retry"`` (after waiting), ``"later"``
        or ``"refused"``.

        ``headers`` are the response headers with lower-case names (``fetch_small``
        returns them so). ``retried`` is True on the second try: then a rate
        limit marks the source later instead of waiting again.

        Only a rate limit slows the source down: a 429, or a 403 that says so
        (``x-ratelimit-remaining: 0``, a ``retry-after``, or a body naming a rate
        limit). Any other 403 ("Repository access blocked") is ``"refused"``:
        that one answer is refused, nothing waits and the source stays usable.
        With no headers at all (the older fetch path cannot report them) a 403
        cannot be told apart, so it is treated as a rate limit, the careful way.
        """
        headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        now = self.clock()
        with _LOCK:
            rec = _rec(source)
            remaining = _int(headers.get("x-ratelimit-remaining"))
            reset = _int(headers.get("x-ratelimit-reset"))
            if remaining is not None:
                rec["remaining"] = remaining
            if reset is not None:
                rec["reset"] = float(reset)
            if headers.get("x-ratelimit-resource"):
                rec["resource"] = str(headers["x-ratelimit-resource"])[:40]
            _save()
        if status not in (403, 429):
            return "ok"
        retry_after = _int(headers.get("retry-after"))
        says_limit = bool(body) and bool(_LIMIT_BODY_RE.search(
            bytes(body[:4096]).decode("utf-8", "replace")))
        if status == 403 and headers and remaining != 0 and retry_after is None \
                and not says_limit:
            return "refused"          # not a rate limit: only this answer is refused
        if retried:
            self.mark_later(source, progress)
            return "later"
        wait: Optional[float] = None
        # A 429 says "too many" by itself; a bare 403 with no headers (the older
        # fetch path) gives no reason, so the owner is not told GitHub asked.
        why = WHY_ASKED if headers or says_limit or status == 429 else WHY_REFUSED
        if retry_after is not None:
            wait = float(retry_after)
        elif reset is not None and (remaining == 0 or status == 429):
            wait = float(reset) - now
        elif not headers or says_limit:
            # No headers at all (the older fetch path), or a limit named only in
            # the body (GitHub's secondary limit): wait the longest we allow,
            # once, and count our own calls from here.
            wait = float(MAX_WAIT_S)
        if wait is None or wait > MAX_WAIT_S:
            self.mark_later(source, progress)
            return "later"
        self._wait(source, max(wait, 1.0), progress, why)
        return "retry"


def new_budget(*, wizard: bool, clock: Callable[[], float] = time.time,
               sleeper: Callable[[float], None] = time.sleep) -> SearchBudget:
    return SearchBudget(wizard=wizard, clock=clock, sleeper=sleeper)


__all__ = ["CAPS_ADD", "CAPS_WIZARD", "LABELS", "MAX_WAIT_S", "PER_MIN", "SearchBudget",
           "WHY_ASKED", "WHY_PACE", "WHY_REFUSED", "forget", "later_line", "new_budget",
           "state_path", "waiting_line"]
