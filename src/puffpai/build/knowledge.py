"""The build's one knowledge step: books for her goal, on the same one Yes.

Every build looks through the knowledge sites BuildBot already searches on
the Knowledge page (:func:`puffpai.knowledge.find_more.search`: BuildBot's
own list, the Kiwix library, Project Gutenberg, OpenStax, Wikibooks, the
Internet Archive and a Wikipedia book made from her words) with the goal's
and the jobs' search phrases (the ones :mod:`puffpai.build.apps` asked the
brain for), and keeps the best :data:`KEEP` that can be downloaded in
``build["knowledge"]``. Each is one tickable line on the Yes card; one
bigger than :data:`TICK_MAX` starts unticked and says its size. The ticked
ones are part of what her Yes covers (``yes.sha``). After the Yes they are
downloaded one at a time in the background (:func:`start_downloads`, with
``find_more.add``, which checks each with Drawbridge first), learned by the
usual knowledge path, and chat searches them on every turn as it does every
book. A site that does not answer never stops a build.

Item shape: ``{"id", "title", "site", "size_bytes", "size_words", "ticked",
"consent": [lines], "status": "" | "downloading" | "added" | "failed",
"reason"}``.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from puffpai.build import schema as S
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

KEEP = 5
#: Searches per build (the goal's phrase first, then one per job).
QUERIES_MAX = 4
#: Bigger than this starts unticked (she can still tick it).
TICK_MAX = 2 * 1024 ** 3
DOWNLOADING, ADDED, FAILED = "downloading", "added", "failed"

#: Tests hand in ``(words) -> find_more.search result`` and the adder.
SEARCH: Optional[Callable[[str], dict]] = None
ADD: Optional[Callable[..., dict]] = None
#: Tests run the downloads in their own thread (False: in the caller).
IN_BACKGROUND = True


def _search(words: str) -> dict:
    if SEARCH is not None:
        return SEARCH(words)
    from puffpai.knowledge import find_more
    return find_more.search(words)


def switched_off() -> bool:
    if SEARCH is not None:
        return False
    from puffpai.finding import books
    return books.off()


def queries(jobs: list[dict], goal: str) -> list[str]:
    """The goal's search phrase, then each job's first one (apps.py's own)."""
    from puffpai.build import apps
    got = apps.LAST_WORDS.get("words") if apps.LAST_WORDS.get("goal") == goal else None
    if not isinstance(got, dict):
        got = apps.search_words(jobs, goal, None)
    out: list[str] = []
    for words in [got.get("goal") or []] + [(p or [[]])[0] for p in
                                             (got.get("jobs") or {}).values()]:
        phrase = " ".join(str(w) for w in words or []).strip()
        if phrase and phrase not in out:
            out.append(phrase)
    return out[:QUERIES_MAX]


def _size(cand: dict) -> Optional[int]:
    for key in ("size_bytes", "size_ceiling"):
        value = cand.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def item_of(cand: dict) -> dict:
    from puffpai.catalogs import knowledge_resolver as kr
    from puffpai.knowledge import find_more
    size = _size(cand)
    site = find_more.from_place(cand)
    site = site[4:] if site.startswith("the ") else site
    consent = [line.strip() for line in find_more.consent_lines(cand)
               if not line.strip().startswith("To add it:")]
    return {"id": str(cand["id"]), "title": str(cand.get("name") or cand["id"])[:80],
            "site": site[:60], "size_bytes": size, "size_words": kr.download_words(cand),
            "ticked": size is None or size <= TICK_MAX, "consent": consent[:8],
            "status": "", "reason": ""}


def find(jobs: list[dict], goal: str) -> list[dict]:
    """The best :data:`KEEP` downloadable things for this build. Never raises."""
    if switched_off():
        return []
    from puffpai.catalogs import knowledge_resolver as kr
    seen: dict[str, dict] = {}
    topic = False
    for words in queries(jobs, goal):
        try:
            got = _search(words) or {}
        except Exception as exc:  # noqa: BLE001 - a site that does not answer never stops a build
            logger.info("build knowledge: %r not searched (%s)", words, type(exc).__name__)
            continue
        for cand in got.get("candidates") or []:
            if not isinstance(cand, dict) or not cand.get("id") or not cand.get("fetchable"):
                continue
            if kr.tier_of(cand) == "wikipedia_topic":
                if topic:
                    continue            # one Wikipedia book made from her words is enough
                topic = True
            have = seen.get(str(cand["id"]))
            if have is None or int(cand.get("score") or 0) > int(have.get("score") or 0):
                try:
                    cand["_fits"] = kr.fits_goal(cand, words)
                except Exception:  # noqa: BLE001
                    cand["_fits"] = False
                seen[str(cand["id"])] = cand
    # The search's own order: what is about the subject (its title holds the
    # words, the rule books.search uses) before what only shares a word, a
    # general encyclopedia last; the higher score first.
    best = sorted(seen.values(), key=lambda c: (bool(c.get("general")), not c.get("_fits"),
                                                -int(c.get("score") or 0)))[:KEEP]
    out = []
    for cand in best:
        try:
            out.append(item_of(cand))
        except Exception as exc:  # noqa: BLE001
            logger.info("build knowledge: %s left out (%s)", cand.get("id"), type(exc).__name__)
    return out


def merge(old: list[dict], new: list[dict]) -> list[dict]:
    """*new* after *old*; what she ticked and what arrived are kept."""
    out = [dict(i) for i in old or []]
    have = {i["id"] for i in out}
    out += [dict(i) for i in new if i["id"] not in have]
    return out


def plan(build: dict, jobs: list[dict], goal: str) -> None:
    """Set ``build["knowledge"]`` (kept items stay as they are)."""
    try:
        found = find(jobs, goal)
    except Exception as exc:  # noqa: BLE001 - never fails a build
        logger.info("build knowledge: not searched (%s)", type(exc).__name__)
        found = []
    items = merge(build.get("knowledge") or [], found)
    if items:
        build["knowledge"] = items
    if found:
        _narrate(f"Knowledge to add: {', '.join(i['title'] for i in found)}.")


def _narrate(text: str) -> None:
    try:
        from puffpai.build import pipeline
        pipeline._say(text)  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


def ticked(build: dict) -> list[str]:
    """The ids her Yes covers (part of ``yes.sha``)."""
    return sorted(i["id"] for i in build.get("knowledge") or [] if i.get("ticked"))


def set_tick(ident: str, on: bool) -> dict:
    """Tick or untick one line. The list changed, so it needs her Yes again."""
    from puffpai.build import yes as yes_mod

    def change(b: dict) -> None:
        item = next((i for i in b.get("knowledge") or [] if i.get("id") == ident), None)
        if item is None:
            raise KeyError(ident)
        before = yes_mod.sha(b)
        item["ticked"] = bool(on)
        if yes_mod.sha(b) != before:
            yes_mod.reset(b)
    return S.update_build(change)


# ── after the Yes ──────────────────────────────────────────────────────────

_running = threading.Lock()


def _set(ident: str, **changes: Any) -> None:
    def change(b: dict) -> None:
        for i in b.get("knowledge") or []:
            if i.get("id") == ident:
                i.update(changes)
    S.update_build(change)


def _plain(reason: Any) -> str:
    text = " ".join(str(reason or "").split())
    return (text.split(". ")[0].rstrip(".") + ".") if text else "It did not arrive."


def _todo(build: dict) -> list[str]:
    return [i["id"] for i in build.get("knowledge") or []
            if i.get("ticked") and i.get("status") != ADDED]


def download_all() -> list[dict]:
    """Download every ticked item her Yes covers, one at a time."""
    from puffpai.build import yes as yes_mod
    if not _running.acquire(blocking=False):
        return []                      # one download run at a time
    out = []
    try:
        while True:
            build = S.load_build()
            if not yes_mod.covers(build):
                break
            todo = [i for i in _todo(build) if i not in {o["id"] for o in out}]
            if not todo:
                break
            ident = todo[0]
            _set(ident, status=DOWNLOADING, reason="")
            got = _add_one(ident)
            state = ADDED if got.get("ok") else FAILED
            _set(ident, status=state, reason="" if state == ADDED else _plain(got.get("reason")))
            out.append({"id": ident, "status": state})
    finally:
        _running.release()
    return out


def _add_one(ident: str) -> dict:
    try:
        if ADD is not None:
            return ADD(ident, size_acknowledged=True, via="build") or {}
        from puffpai.knowledge import find_more
        got = find_more.add(ident, size_acknowledged=True, via="build") or {}
        if got.get("ok") and not got.get("text_book") and got.get("folder"):
            # A book file: learned by the usual background learner.
            from puffpai.knowledge.auto_index import start_background_index
            start_background_index(str(got["folder"]))
        return got
    except Exception as exc:  # noqa: BLE001 - one book never stops the rest
        logger.warning("build knowledge: %s not added (%s)", ident, type(exc).__name__)
        return {"ok": False, "reason": "The download stopped."}


def start_downloads() -> bool:
    """After her Yes: the ticked items, in the background, beside the app jobs."""
    if not _todo(S.load_build()):
        return False
    if not IN_BACKGROUND:
        download_all()
        return True
    threading.Thread(target=download_all, name="puffpai-build-knowledge", daemon=True).start()
    return True


def public(build: dict) -> list[dict]:
    keys = ("id", "title", "site", "size_bytes", "size_words", "ticked", "consent",
            "status", "reason")
    return [{k: i.get(k) for k in keys} for i in build.get("knowledge") or []]


__all__ = ["KEEP", "TICK_MAX", "download_all", "find", "item_of", "merge", "plan", "public",
           "queries", "set_tick", "start_downloads", "ticked"]
