"""Adding knowledge after the build: search, look, say yes, download, learn.

One pipeline with two faces - ``puffpai sources search|add`` and the
dashboard's "Find more knowledge" panel - so they cannot disagree:

* **search** looks through BuildBot's own list, the Kiwix library, Project
  Gutenberg and OpenStax, and plans a Wikipedia topic book from the words. It
  asks the live libraries when this bot was set up to go online while
  building, and otherwise uses the dated lists that ship with BuildBot.
* **add** shows what the thing is, how big it is and its license, and does
  nothing until the owner says yes. A yes writes the book into this build's
  overlay (``source_registry.local.json`` in the state dir, marked
  ``owner_added``), then downloads it through :mod:`puffpai.source_fetch`,
  which checks it against the library's own SHA-256 before installing it.
* A Wikipedia topic book, a Project Gutenberg book or an OpenStax textbook
  arrives as text: it is saved as a book folder
  (:mod:`puffpai.knowledge.text_books`), answers questions a few seconds
  later, and is learned fully in the background. Adding a topic book that is
  already here grows it with articles it does not have. The wizard never
  waits for one: it records the order (:func:`start_in_background`) and the
  background learner fetches it (:func:`run_orders`), in this process and on
  every later start until it has arrived.
* It never takes an address from anyone. Ids come from a catalog; the only
  books that can be added are ones the catalog lists with a known license.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import threading
from typing import Any, Callable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: What an id from a catalog looks like. Anything else is refused at the edge.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,190}$")
MAX_QUERY = 200

ADD_COMMAND = "puffpai sources add {source_id}"


# ---------------------------------------------------------------------------
# what this bot was set up to allow
# ---------------------------------------------------------------------------

def recorded_posture() -> Optional[dict[str, Any]]:
    """The build posture the wizard recorded, or ``None`` before any build."""
    try:
        from puffpai import build_manifest
        manifest = build_manifest.read_manifest() or {}
    except Exception:  # noqa: BLE001 - no manifest is "not set up yet"
        return None
    posture = manifest.get("posture")
    if not isinstance(posture, dict) or not posture.get("build_network"):
        return None
    return dict(posture)


def online_allowed(posture: Optional[dict[str, Any]]) -> bool:
    if not posture:
        return False
    from puffpai.build_posture import gates
    return bool(gates(posture).get("may_download_packs"))


def knowledge_folder(*, create: bool = False) -> str:
    """The folder packs land in: the owner's data folder (``knowledge/my_data``),
    set to the default under the data dir if unset.

    Setting it (and creating it) is what makes ``puffpai knowledge sync`` and
    the background indexer find a book added after the build.
    """
    from puffpai import setup_ops
    from puffpai.config import get_config

    cfg = get_config()
    folder = (str(getattr(cfg.knowledge, "data_dir", "") or "").strip()
              or str(getattr(cfg.knowledge, "zim_dir", "") or "").strip())
    if folder:
        if create:
            os.makedirs(folder, exist_ok=True)
        return folder
    if not create:
        return setup_ops.default_pack_dir()
    folder = setup_ops.default_pack_dir()
    setup_ops.set_knowledge_config(folder, auto_index=bool(cfg.knowledge.auto_index),
                                   create_dir=True)
    return folder


# ---------------------------------------------------------------------------
# search and look
# ---------------------------------------------------------------------------

def clean_query(text: Any) -> str:
    raw = "".join(ch for ch in str(text or "") if ch.isprintable())
    return " ".join(raw.split())[:MAX_QUERY]


def search(words: Any, *, live: Optional[bool] = None, age: Any = None,
           getter: Any = None, on_library: Any = None) -> dict[str, Any]:
    """Books for these words. Never raises. Adds a plain ``lines`` list.

    ``on_library(library, state)`` hears each library asked online answer
    (``knowledge_resolver.ask_libraries``)."""
    from puffpai.catalogs import knowledge_resolver

    query = clean_query(words)
    posture = recorded_posture()
    allowed = online_allowed(posture)
    note = ""
    if live is None:
        live = allowed
    elif live and not allowed:
        live = False
        note = ("This bot was set up to stay offline while building, so this "
                "searched the list saved with BuildBot instead of the live library.")
    result = knowledge_resolver.find(query, live=bool(live), age=age, getter=getter,
                                     limit=15, on_library=on_library)
    if note:
        result["note"] = (note + " " + str(result.get("note") or "")).strip()
    result["query"] = query
    result["lines"] = search_lines(result)
    return result


def looked_in_words(result: dict[str, Any]) -> str:
    """Every place a search looked, in plain words (terminal and browser)."""
    where = ("the live Kiwix library" if result.get("searched") == "live"
             else f"the Kiwix list saved on {result.get('snapshot_date') or 'an unknown date'}")
    tiers = result.get("tiers") or {}
    others = [name for key, name in (("gutenberg", "Project Gutenberg"),
                                     ("openstax", "OpenStax"),
                                     ("wikibooks", "Wikibooks"),
                                     ("archive", "the Internet Archive"))
              if key in tiers]
    places = f"BuildBot's own list, {where}" + "".join(f", {o}" for o in others)
    if "wikipedia_topic" in tiers:
        places += ", and a Wikipedia book made from your words"
    return places


def from_place(cand: dict[str, Any]) -> str:
    """Where one result is fetched from, in plain words. A text book names its
    own library; everything else downloads from the Kiwix library."""
    if is_text_book(cand.get("id")):
        return str(cand.get("source_catalog") or "the library it was found in")
    return "the Kiwix library"


def search_lines(result: dict[str, Any]) -> list[str]:
    from puffpai.catalogs import knowledge_resolver

    lines: list[str] = []
    places = looked_in_words(result)
    if result.get("terms"):
        lines.append(f"Looked for: {', '.join(result['terms'])} (in {places}).")
    if result.get("note"):
        lines.append(str(result["note"]))
    for cand in result.get("candidates") or []:
        lines.extend(knowledge_resolver.candidate_lines(cand))
    return lines


def look(ident: Any) -> Optional[dict[str, Any]]:
    """One candidate by catalog id, with no network. ``None`` if unknown."""
    from puffpai.catalogs import knowledge_resolver

    text = str(ident or "").strip()
    if not ID_RE.match(text):
        return None
    return knowledge_resolver.candidate_by_id(text)


def look_for_add(ident: Any, posture: Optional[dict[str, Any]] = None,
                 *, getter: Any = None) -> Optional[dict[str, Any]]:
    """Like :func:`look`, and a Kiwix book newer than the saved list is asked
    for by name in the live library, when this bot may download at all.

    A live search can show a book the saved list does not have; without this,
    adding it said the library had nothing with that id, which was untrue.
    """
    from puffpai.catalogs import kiwix_opds, knowledge_resolver

    cand = look(ident)
    text = str(ident or "").strip()
    if cand is None and ID_RE.match(text) and is_text_book(text):
        posture = posture if posture is not None else recorded_posture()
        if not online_allowed(posture):
            return None
        return knowledge_resolver.candidate_by_id(text, live=True, reach_getter=getter)
    if cand is not None or not ID_RE.match(text) \
            or kiwix_opds.split_book_id(text) is None:
        return cand
    posture = posture if posture is not None else recorded_posture()
    if not online_allowed(posture):
        return None
    return knowledge_resolver.candidate_by_id(text, live=True, getter=getter)


#: Id beginnings of the books that arrive as text, not as one book file.
TEXT_BOOK_PREFIXES = ("wikipedia:", "gutenberg:", "openstax:", "wikibooks:",
                      "archive:")


def is_text_book(ident: Any) -> bool:
    return str(ident or "").strip().startswith(TEXT_BOOK_PREFIXES)


def consent_lines(cand: dict[str, Any], plan: Optional[dict[str, Any]] = None
                  ) -> list[str]:
    from puffpai.catalogs import knowledge_resolver, wikipedia_topics

    lines = knowledge_resolver.candidate_lines(cand)
    if plan is not None:
        lines.extend(wikipedia_topics.plan_lines(plan))
    if not is_text_book(cand.get("id")):
        lines.append("    Comes from: the Kiwix library (download.kiwix.org and the "
                     "mirrors it lists). Its fingerprint is checked before it is used.")
    elif cand.get("personal_use_only"):
        lines.append("    Free for personal use only: it is fetched by this computer "
                     "for you, and never included if you share or sell this bot.")
    lines.append("    After it arrives it is read on this computer; using it needs "
                 "no internet.")
    return lines


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

def drawbridge_block(cand: dict[str, Any], *, via: str) -> Optional[dict[str, Any]]:
    """Drawbridge's verdict on one book when it is ``block``, else None.

    Every path that downloads a book asks this after the owner's yes and
    before anything is fetched (this module's add, and the wizard's and the
    terminal's ``setup_ops.fetch_source``). The candidate's address is its
    library's information page, not the download (a book is fetched by its id
    from the library's own servers), so only the record is checked.
    """
    from puffpai import drawbridge
    record = {k: v for k, v in cand.items() if k != "url"}
    verdict = drawbridge.check_addition(
        {"kind": "book", "name": cand.get("name"), "candidate": record},
        by=str(via or "find_more")[:40])
    return verdict if verdict.get("verdict") == drawbridge.BLOCK else None


def _refuse(ident: str, reason: str, **extra: Any) -> dict[str, Any]:
    out = {"ok": False, "status": "refused", "id": ident, "reason": reason,
           "lines": [reason]}
    out.update(extra)
    return out


def add(ident: Any, *, size_acknowledged: bool, via: str,
        posture: Optional[dict[str, Any]] = None, getter: Any = None,
        transport: Any = None, resolver: Any = None,
        sleeper: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
        cancel: Optional[threading.Event] = None,
        on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
        learn_now: bool = True) -> dict[str, Any]:
    """Add one book the owner said yes to. Never raises.

    ``size_acknowledged`` must be the owner's own yes after seeing
    :func:`consent_lines`. Without it this returns ``needs_confirmation`` and
    the candidate, and writes nothing.
    """
    from puffpai import source_fetch, source_registry
    from puffpai.catalogs import kiwix_opds

    text = str(ident or "").strip()
    if not ID_RE.match(text):
        return _refuse(text[:60], "That is not an id from the library. Use one "
                                  "that `puffpai sources search` printed.")
    cand = look_for_add(text, posture, getter=getter)
    if cand is None:
        return _refuse(text, "Neither BuildBot's list nor the Kiwix library has "
                             "anything with that id.")
    if not cand.get("fetchable"):
        return _refuse(text, str(cand.get("why_not") or "That cannot be downloaded here."),
                       candidate=cand)
    posture = posture if posture is not None else recorded_posture()
    if not posture:
        return _refuse(text, "This bot has not been set up yet. Run `puffpai setup` "
                             "first, then add books.", candidate=cand)
    if not online_allowed(posture):
        return _refuse(text, "This bot was set up to stay offline while building, so "
                             "it does not download. Bring the book over on a USB "
                             "stick and put it in the knowledge folder.", candidate=cand)
    if size_acknowledged is True:
        # Drawbridge's one policy, after the owner's yes and before anything
        # is fetched.
        blocked = drawbridge_block(cand, via=via)
        if blocked is not None:
            return _refuse(text, str(blocked.get("line") or "It did not pass "
                                     "BuildBot's checks, so it was not added."),
                           candidate=cand, drawbridge=blocked)
    if is_text_book(text):
        return _add_text_book(cand, size_acknowledged=size_acknowledged is True,
                              getter=getter, cancel=cancel, on_progress=on_progress,
                              learn=learn_now)
    if size_acknowledged is not True:
        return {"ok": False, "status": "needs_confirmation", "id": text,
                "candidate": cand, "reason": "Say yes to the size and license first.",
                "lines": consent_lines(cand)}

    if kiwix_opds.split_book_id(text) is not None \
            and source_registry.get_source(text) is None:
        try:
            source_registry.add_owner_row(
                source_registry.row_from_candidate(cand, added_via=via))
        except (ValueError, OSError) as exc:
            return _refuse(text, f"Could not record your choice: {exc}", candidate=cand)

    try:
        folder = knowledge_folder(create=True)
    except OSError as exc:
        return _refuse(text, f"Could not prepare the knowledge folder: {exc}")
    kwargs: dict[str, Any] = {"size_acknowledged": True, "dest_dir": folder,
                              "transport": transport, "resolver": resolver,
                              "cancel": cancel, "on_progress": on_progress}
    if sleeper is not None:
        kwargs["sleeper"] = sleeper
    if clock is not None:
        kwargs["clock"] = clock
    result = source_fetch.fetch(text, posture, **kwargs)
    out = {"ok": bool(result.get("ok")), "status": result.get("status"), "id": text,
           "candidate": cand, "folder": folder, "result": result,
           "reason": result.get("reason")}
    if result.get("ok"):
        _order_done(text)
        out["lines"] = [f"Added {cand['name']}.",
                        f"  Saved in: {result.get('path')}",
                        f"  Checked: its fingerprint matches the one the library published."
                        if (result.get("provenance") or {}).get("sha256_pinned")
                        else "  Checked: size only (no published fingerprint).",
                        "  Next: it is learned when the bot next starts, or now with "
                        "`puffpai knowledge sync`."]
    else:
        out["lines"] = source_fetch.fetch_summary_lines(result)
    return out


def _pages_word(count: int) -> str:
    return "1 page" if count == 1 else f"{count} pages"


def _existing_titles(folder: str, ident: str) -> list[str]:
    from puffpai.knowledge import text_books
    book = text_books.read_book(text_books.book_path(folder, ident)) or {}
    return [str(i.get("title")) for i in book.get("items") or []]


def _topic_plan(ident: str, folder: str, getter: Any,
                reuse: bool = True) -> dict[str, Any]:
    """The articles a topic book would hold (or gain), found by code."""
    from puffpai.catalogs import wikipedia_topics

    have = _existing_titles(folder, ident)
    if reuse:
        kept = wikipedia_topics.remembered_plan(ident)
        if kept is not None and kept.get("have") == len(have):
            return kept
    cap = wikipedia_topics.GROW_ARTICLES if have else wikipedia_topics.MAX_ARTICLES
    plan = wikipedia_topics.discover(ident, getter=getter, have=have, cap=cap)
    plan["have"] = len(have)
    return plan


def _add_text_book(cand: dict[str, Any], *, size_acknowledged: bool, getter: Any,
                   cancel: Optional[threading.Event],
                   on_progress: Optional[Callable[[dict[str, Any]], None]],
                   learn: bool = True) -> dict[str, Any]:
    """A Wikipedia topic book, Gutenberg book or OpenStax textbook: plan, yes,
    fetch, save as a book folder, learn. Never raises."""
    from puffpai.catalogs import wikipedia_topics

    ident = str(cand["id"])
    try:
        folder = knowledge_folder(create=size_acknowledged)
    except OSError as exc:
        logger.warning("Knowledge folder not ready for %s: %s", ident, exc)
        return _refuse(ident, _SAVE_FAILED)
    topic = ident.startswith("wikipedia:")
    plan = None
    if topic:
        try:
            plan = _topic_plan(ident, folder, getter)
        except wikipedia_topics.WikipediaError as exc:
            return _refuse(ident, str(exc), candidate=cand)
        if not plan.get("count"):
            return _refuse(ident, "Wikipedia has no more articles about those words "
                                  "that BuildBot could check.", candidate=cand)
        cand = dict(cand, size_bytes=int(plan.get("estimate_bytes") or 0) or None)
    if not size_acknowledged:
        return {"ok": False, "status": "needs_confirmation", "id": ident,
                "candidate": cand, "plan": plan,
                "reason": "Say yes to the size and license first.",
                "lines": consent_lines(cand, plan)}
    lock = _claim_book(folder, ident)
    if lock is None:
        return _refuse(ident, _ALREADY_ADDING, candidate=cand)
    try:
        return _fetch_text_book(cand, ident, folder, plan, getter=getter,
                                cancel=cancel, on_progress=on_progress, learn=learn)
    finally:
        _release_book(lock)


#: Owner words for a book that could not be written to disk. The file system's
#: own message (a path and an error number) goes to the log, never the owner.
_SAVE_FAILED = ("This computer could not save the book. Check there is free "
                "space in the knowledge folder, then try again.")
_ALREADY_ADDING = ("This book is already being added. Wait for that to finish, "
                   "then look again.")


def _claim_book(folder: str, ident: str) -> Optional[str]:
    """One add per book at a time, across the terminal, the wizard and the
    browser (separate processes): an OS lock on a marker file in the knowledge
    folder. The lock is freed if the process holding it dies, so a claim left
    by an add that was killed is taken over at once. Returns the marker's
    path, or ``None`` when another add holds it."""
    from puffpai.knowledge.learn import BookLock

    safe = re.sub(r"[^A-Za-z0-9._-]", "_", ident)
    lock = BookLock(folder, f".adding_{safe}")
    try:
        if not lock.acquire():
            return None
    except OSError as exc:
        logger.warning("Could not claim %s: %s", ident, exc)
        return None
    with _claims_lock:
        _claims[lock.path] = lock
    return lock.path


_claims: dict[str, Any] = {}
_claims_lock = threading.Lock()


def _release_book(path: str) -> None:
    with _claims_lock:
        lock = _claims.pop(path, None)
    if lock is not None:
        lock.release()
    try:
        os.remove(path)
    except OSError:
        pass


def _fetch_text_book(cand: dict[str, Any], ident: str, folder: str,
                     plan: Optional[dict[str, Any]], *, getter: Any,
                     cancel: Optional[threading.Event],
                     on_progress: Optional[Callable[[dict[str, Any]], None]],
                     learn: bool) -> dict[str, Any]:
    from puffpai.catalogs import gutendex, internet_archive, openstax, wikibooks
    from puffpai.catalogs import wikipedia_topics
    from puffpai.knowledge import progress, text_books

    topic = ident.startswith("wikipedia:")
    writer = progress.Writer(text_books.learn_id(ident), str(cand.get("name") or ident)[:80])
    writer.update(force=True, stage="fetching", error="",
                  fetch={"done": 0, "total": int((plan or {}).get("count") or 0)})
    outer = on_progress

    def on_progress(info: dict[str, Any]) -> None:
        if info.get("total"):
            writer.update(stage="fetching", fetch={"done": int(info.get("done") or 0),
                                                   "total": int(info["total"])})
        if outer is not None:
            outer(info)

    try:
        if topic:
            meta = wikipedia_topics.book_meta(ident)
            pages = wikipedia_topics.fetch_pages(plan, getter=getter, cancel=cancel,
                                                 on_progress=on_progress)
        elif ident.startswith("gutenberg:"):
            meta = gutendex.book_meta(cand)
            pages = gutendex.pages(cand, getter=getter)
        elif ident.startswith("wikibooks:"):
            meta = wikibooks.book_meta(cand)
            pages = wikibooks.pages(cand, getter=getter, cancel=cancel,
                                    on_progress=on_progress)
        elif ident.startswith("archive:"):
            meta = internet_archive.book_meta(cand)
            pages = internet_archive.pages(cand, getter=getter)
        else:
            meta = openstax.book_meta(cand)
            pages = openstax.pages(cand, getter=getter, cancel=cancel,
                                   on_progress=on_progress)
        record = text_books.write_book(folder, meta, pages)
    except (wikipedia_topics.WikipediaError, gutendex.GutenbergError,
            openstax.OpenStaxError, wikibooks.WikibooksError,
            internet_archive.ArchiveError, text_books.BookError) as exc:
        _not_fetched(writer)
        return _refuse(ident, str(exc), candidate=cand)
    except OSError as exc:
        logger.warning("Saving %s failed: %s", ident, exc)
        _not_fetched(writer)
        return _refuse(ident, _SAVE_FAILED, candidate=cand)
    path = str(record.get("_path"))
    count = len(record.get("items") or [])
    added = int(record.get("_added") or 0)
    from puffpai.catalogs import knowledge_resolver
    lines = [f"Added {cand['name']}." if added == count else
             f"Added {added} more pages to {cand['name']} ({count} in all).",
             f"  Saved in: {path}",
             f"  Holds: {_pages_word(count)}, "
             f"{knowledge_resolver.size_words(int(record.get('total_bytes') or 0))} of text.",
             f"  Credit: {record.get('credit')}"]
    if record.get("stopped_at_limit"):
        lines.append("  It stopped at the 25 MB a book may be.")
    out = {"ok": True, "status": "added", "id": ident, "candidate": cand,
           "folder": folder, "path": path, "pages": count, "added": added,
           "learned": False, "text_book": True, "reason": ""}
    if learn:
        # The quick stage: seconds. The meaning stage runs in the background.
        got = text_books.learn(path)
        out["learned"] = got.get("status") == "complete"
        out["learn"] = got
        out["book_id"] = got.get("book_id")
        if out["learned"]:
            lines.append(f"  Learned: {_pages_word(int(got.get('docs') or 0))}. The bot "
                         f"can answer from it now, and learns it fully in the "
                         f"background.")
            _learn_rest_in_background(folder)
        else:
            lines.append("  It was saved but not learned yet. It is learned the "
                         "next time the bot starts, or now with "
                         "`puffpai knowledge sync`.")
    else:
        writer.update(force=True, stage="quick", quick={"done": 0, "total": count})
    out["lines"] = lines
    return out


def _not_fetched(writer: Any) -> None:
    writer.update(force=True, stage="error", error="not_fetched")


def _learn_rest_in_background(folder: str) -> None:
    """Hand the meaning stage (and anything else waiting) to the background
    learner. Never raises: a book that answers already is not lost if this
    cannot start; the next start picks it up."""
    try:
        from puffpai.knowledge.auto_index import start_background_index
        start_background_index(folder)
    except Exception as exc:  # noqa: BLE001 - learning is additive
        logger.warning("Could not start learning in the background: %s", exc)


# ---------------------------------------------------------------------------
# books ordered for the background (the wizard never waits for one)
# ---------------------------------------------------------------------------

ORDERS_FILE = "learning_orders.json"
_orders_lock = threading.Lock()


def _orders_path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), ORDERS_FILE)


def pending_orders() -> dict[str, dict[str, Any]]:
    """Books the owner said yes to that have not arrived yet: text books,
    and a Kiwix file she said yes to before her bot was set up."""
    from puffpai import fsutil
    try:
        data = fsutil.read_json(_orders_path(), {})
    except ValueError:
        return {}
    return {str(k): v for k, v in (data or {}).items()
            if isinstance(v, dict) and ID_RE.match(str(k))}


def _order_done(ident: str) -> None:
    """A book that arrived another way (the plan's own line) is no longer
    waiting."""
    with _orders_lock:
        orders = pending_orders()
        if orders.pop(ident, None) is not None:
            _save_orders(orders)


def _save_orders(orders: dict[str, dict[str, Any]]) -> None:
    from puffpai import fsutil
    path = _orders_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fsutil.atomic_write_json(path, orders)


class OrderBlocked(Exception):
    """Drawbridge said ``block`` to a book, so it was not ordered."""

    def __init__(self, verdict: dict[str, Any]):
        self.verdict = verdict
        self.line = str(verdict.get("line") or "It did not pass BuildBot's "
                                                "checks, so it was not added.")
        super().__init__(self.line)


def order_book(ident: str, name: str, *, via: str = "wizard",
               cand: Optional[dict[str, Any]] = None) -> str:
    """Record the owner's yes to a text book for the background. Returns the
    id it is learned under (for its progress lines).

    Every order is checked by Drawbridge first, whoever placed it (the wizard,
    add more, the terminal); a ``block`` raises :class:`OrderBlocked` and
    nothing is recorded. The background learner checks it again before it
    fetches (:func:`run_orders` goes through :func:`add`)."""
    from puffpai.knowledge import progress, text_books
    record = dict(cand) if isinstance(cand, dict) else dict(look(ident) or {})
    record.setdefault("id", ident)
    if not record.get("name"):
        record["name"] = str(name or ident)
    blocked = drawbridge_block(record, via=via)
    if blocked is None and name and str(name) != str(record.get("name")):
        # The name the owner is shown is checked too, not only the record's.
        blocked = drawbridge_block(dict(record, name=str(name)), via=via)
    if blocked is not None:
        raise OrderBlocked(blocked)
    lid = text_books.learn_id(ident)
    with _orders_lock:
        orders = pending_orders()
        orders[ident] = {"name": str(name or ident)[:120], "via": str(via)[:40]}
        _save_orders(orders)
    if is_text_book(ident) and (progress.read(lid) or {}).get("stage") in (
            None, "error", "fetch_stopped"):
        progress.Writer(lid, str(name or ident)[:80]).update(
            force=True, stage="waiting", error="")
    return lid


#: Which named outside service (a row on What leaves your computer) fetches
#: a text book, by the start of its id.
_ORDER_SERVICES = (("wikipedia:", "wikipedia_books"),
                   ("gutenberg:", "gutenberg_books"),
                   ("openstax:", "openstax_books"),
                   ("wikibooks:", "wikibooks_books"),
                   ("archive:", "archive_books"),
                   ("kiwix:", "library"))


def order_service(ident: Any) -> str:
    text = str(ident or "")
    return next((sid for prefix, sid in _ORDER_SERVICES
                 if text.startswith(prefix)), "")


def order_hold(ident: Any) -> str:
    """Why an ordered book is NOT fetched now ("not_set_up", "switched_off",
    "kept_offline"), or "" when it may be. The owner's yes to the book is not
    enough on its own: the bot must be made (a book she said yes to while
    finding things waits for the build), its website must be switched on,
    and a bot set to reach nothing outside this computer never fetches."""
    if recorded_posture() is None:
        return "not_set_up"
    try:
        from puffpai.security import egress_doors, outside_switches
        # A bot set to reach nothing outside is said first: with it, every
        # outside service also reads as off (the Bridge).
        if egress_doors.run_posture().get("run_egress") == "sealed":
            return "kept_offline"
        service = order_service(ident)
        if service and outside_switches.is_off(service):
            return "switched_off"
    except Exception:  # noqa: BLE001 - not known is not allowed
        return "kept_offline"
    return ""


def orders_note(bot: str = "Your bot") -> str:
    """The plain line said at start-up when a bot kept on the home network
    fetches books it was told to, so it never does so without saying so."""
    try:
        from puffpai.security import egress_doors
        if egress_doors.run_posture().get("run_egress") != "island":
            return ""
        fetchable = [i for i in pending_orders() if not order_hold(i)]
        if not fetchable:
            return ""
        hosts: list[str] = []
        for ident in fetchable:
            door = egress_doors.door(order_service(ident))
            hosts += [h for h in (door.hosts if door else ()) if h not in hosts]
    except Exception:  # noqa: BLE001
        return ""
    n = len(fetchable)
    books = "the book" if n == 1 else f"the {n} books"
    where = f" from {', '.join(hosts)}" if hosts else ""
    return (f"{bot} stays on your home network, except to fetch {books} you said "
            f"yes to, once{where}. You can switch that off on What leaves your "
            f"computer.")


def run_orders(*, via: str = "background") -> list[dict[str, Any]]:
    """Fetch every ordered text book (its quick stage follows). Never raises.

    An order stays until its book has arrived or cannot be had; a book another
    add is fetching right now keeps its order for the next run. A book whose
    website is switched off, or on a bot set to reach nothing outside this
    computer, is not fetched: it keeps waiting and says why.
    """
    out = []
    for ident in list(pending_orders()):
        hold = order_hold(ident)
        if hold:
            from puffpai.knowledge import progress, text_books
            name = str((pending_orders().get(ident) or {}).get("name") or ident)
            if is_text_book(ident):
                progress.Writer(text_books.learn_id(ident), name[:80]).update(
                    force=True, stage="waiting", error=hold)
            out.append({"id": ident, "ok": False, "reason": hold})
            continue
        try:
            got = add(ident, size_acknowledged=True, via=via, learn_now=False)
        except Exception as exc:  # noqa: BLE001 - one book never stops the rest
            logger.warning("Ordered book %s failed: %s", ident, exc)
            got = {"ok": False, "reason": str(exc)[:200]}
        out.append({"id": ident, **{k: v for k, v in got.items()
                                     if k in ("ok", "reason", "path")}})
        if not got.get("ok") and got.get("reason") == _ALREADY_ADDING:
            continue
        if not got.get("ok"):
            from puffpai.knowledge import progress, text_books
            name = str((pending_orders().get(ident) or {}).get("name") or ident)
            progress.Writer(text_books.learn_id(ident), name[:80]).update(
                force=True, stage="error", error="not_fetched")
        with _orders_lock:
            orders = pending_orders()
            orders.pop(ident, None)
            _save_orders(orders)
    return out


def start_in_background(ident: str, name: str, *, via: str = "wizard",
                        cand: Optional[dict[str, Any]] = None) -> str:
    """The wizard's add for a text book: order it and start the background
    learner, which fetches it, makes it answer, then learns it fully. Returns
    at once with the id its progress lines are under. Raises
    :class:`OrderBlocked` when Drawbridge blocks it (nothing is ordered)."""
    lid = order_book(ident, name, via=via, cand=cand)
    _learn_rest_in_background(knowledge_folder(create=True))
    return lid


# ---------------------------------------------------------------------------
# the dashboard's background job - one download at a time
# ---------------------------------------------------------------------------

_job_lock = threading.Lock()
_job: dict[str, Any] = {"state": "idle"}


def api_search(query: Any, *, live: Optional[bool] = None) -> dict[str, Any]:
    result = search(query, live=live)
    return {"success": True, "query": result.get("query"), "terms": result.get("terms"),
            "searched": result.get("searched"), "note": result.get("note"),
            "snapshot_date": result.get("snapshot_date"),
            "looked_in": looked_in_words(result),
            "candidates": [_public(c) for c in result.get("candidates") or []]}


def _public(cand: dict[str, Any]) -> dict[str, Any]:
    from puffpai.catalogs import knowledge_resolver

    keys = ("id", "kind", "name", "what_it_is", "source_catalog", "license",
            "commercial_ok", "size_bytes", "match_terms", "verified_at",
            "license_plain", "fetchable", "why_not", "reason", "audience",
            "size_ceiling", "personal_use_only")
    out = {k: cand.get(k) for k in keys}
    out["size_words"] = knowledge_resolver.download_words(cand)
    out["from_place"] = from_place(cand)
    out["text_book"] = is_text_book(cand.get("id"))
    return out


def api_add(ident: Any, *, size_acknowledged: Any,
            runner: Optional[Callable[..., dict[str, Any]]] = None,
            start_index: bool = True) -> dict[str, Any]:
    """Start one download the owner confirmed. Returns at once; poll the status."""
    text = str(ident or "").strip()
    if size_acknowledged is not True:
        cand = look_for_add(text) if ID_RE.match(text) else None
        return {"success": False, "status": "needs_confirmation",
                "message": "Say yes to the size and license first.",
                "candidate": _public(cand) if cand else None}
    pre = look_for_add(text) if ID_RE.match(text) else None
    if pre is None or not pre.get("fetchable"):
        return {"success": False, "status": "refused",
                "message": (pre or {}).get("why_not") or
                "That is not something the library lists."}
    with _job_lock:
        if _job.get("state") == "running":
            return {"success": False, "status": "busy",
                    "message": "Another book is downloading. Wait for it to finish."}
        _job.clear()
        _job.update({"state": "running", "id": text, "name": pre["name"],
                     "downloaded": 0, "total": pre.get("size_bytes")})

    def progress(info: dict[str, Any]) -> None:
        with _job_lock:
            _job["downloaded"] = int(info.get("downloaded") or 0)
            if info.get("total"):
                _job["total"] = int(info["total"])

    def work() -> None:
        try:
            outcome = (runner or add)(text, size_acknowledged=True, via="dashboard",
                                      on_progress=progress)
        except Exception as exc:  # noqa: BLE001 - a crashed job is reported, not raised
            outcome = {"ok": False, "reason": f"The download stopped: {exc}"}
        learning = 0
        if outcome.get("text_book"):
            learning = -1 if outcome.get("learned") else 0
        elif outcome.get("ok") and start_index:
            try:
                from puffpai.knowledge.auto_index import start_background_index
                learning, _thread = start_background_index(str(outcome.get("folder") or ""))
            except Exception as exc:  # noqa: BLE001 - indexing is additive
                logger.warning("Could not start learning the new book: %s", exc)
        with _job_lock:
            _job.update({"state": "done" if outcome.get("ok") else "failed",
                         "message": _done_message(outcome, learning)})

    threading.Thread(target=work, name="puffpai-knowledge-add", daemon=True).start()
    return {"success": True, "status": "started", "id": text}


def _done_message(outcome: dict[str, Any], learning: int) -> str:
    if not outcome.get("ok"):
        return str(outcome.get("reason") or "The download did not finish.")
    if learning < 0:
        return "Saved and learned. The bot can answer from it now."
    if learning:
        return "Downloaded and checked. The bot is learning it now."
    return "Downloaded and checked. It is learned the next time the bot starts."


def api_add_status() -> dict[str, Any]:
    with _job_lock:
        out = {"success": True, **dict(_job)}
    if out.get("state") == "done" and out.get("id"):
        out["learning"] = _learning_lines(str(out["id"]))
    return out


def _learning_lines(source_id: str) -> list[str]:
    """How far learning the book with this source has got, in the browser's words."""
    try:
        from puffpai.knowledge import progress
        from puffpai.knowledge.auto_index import PackIndex
        for rec in PackIndex().records():
            if rec.get("source_id") == source_id and rec.get("book_id"):
                found = progress.read(str(rec["book_id"]))
                return progress.plain_lines(found, "browser") if found else []
    except Exception:  # noqa: BLE001 - no lines rather than wrong ones
        return []
    return []


# ---------------------------------------------------------------------------
# what this build can do with knowledge, for the one source of truth
# ---------------------------------------------------------------------------

def ability_rows(pack_records: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
    """Ability rows (the shared shape) for knowledge. Read-only, never raises.

    The library row replaces the core one (same id), so the answer to "what
    can you do?" names the offline library once: available only when a book
    is really learned, "being learned now" while one is, and otherwise the
    way to find books. ``unknown`` when the pack index cannot be read.
    """
    from puffpai import build_abilities, plain_words
    how = {"id": "knowledge.search", "args": {}}
    try:
        if pack_records is None:
            from puffpai.knowledge.auto_index import PackIndex
            pack_records = PackIndex().records()
        # A book whose quick stage is done answers questions already, before
        # (or without) its deeper stage: it counts as learned.
        records = [dict(r, chunks=max(int(r.get("chunks") or 0), int(r.get("docs") or 0)))
                   if r.get("quick") == "complete" else r for r in pack_records]
        books = build_abilities.library_row(records, how)
        if books["status"] == build_abilities.AVAILABLE:
            books.pop("action", None)
            books["how_to_add"] = None
            still = _still_learning_lines()
            if still:
                books["label"] = f"{books['label']}. {' '.join(still)}"
        elif pack_records:
            # A book on disk that is not learned yet (or whose learning was
            # cut short) is finished by the sync.
            books = plain_words.with_action(
                books, {"id": "knowledge.sync", "args": {}})
    except Exception:  # noqa: BLE001 - an unreadable index is "we cannot tell"
        books = {"id": "knowledge_library", "label": build_abilities.LIBRARY_LABEL,
                 "status": "unknown", "how_to_add": None}
    add_more = {"id": "knowledge.add_more", "label": "Find and add more books",
                "status": "available", "how_to_add": None}
    return [books, add_more] + _book_service_rows()


#: The named outside services the book fetcher uses (egress door ids), in plain
#: words. Same ids as the door table's rows, so these replace its generic
#: "Use the outside website ..." lines: the running bot never reaches these on
#: its own, it fetches one book when the owner says yes.
_BOOK_SERVICES = (
    ("wikipedia_books", "Fetch Wikipedia articles about a subject and keep them "
                        "as a book, when you say yes"),
    ("gutenberg_books", "Fetch free old books from Project Gutenberg, when you "
                        "say yes"),
    ("openstax_books", "Fetch free OpenStax textbooks, when you say yes"),
    ("wikibooks_books", "Fetch free how-to books from Wikibooks, when you say yes"),
    ("archive_books", "Fetch old public-domain books from the Internet Archive, "
                      "when you say yes"),
)


def _book_service_rows() -> list[dict[str, Any]]:
    try:
        allowed = online_allowed(recorded_posture())
    except Exception:  # noqa: BLE001 - cannot tell is "unknown", never "available"
        return [{"id": "outside:" + ident, "label": label, "status": "unknown",
                 "how_to_add": None} for ident, label in _BOOK_SERVICES]
    return [{"id": "outside:" + ident, "label": label,
             "status": "available" if allowed else "not_set_up",
             "how_to_add": None} for ident, label in _BOOK_SERVICES]


def _still_learning_lines() -> list[str]:
    """One line per book still getting better, from the progress records."""
    try:
        from puffpai.knowledge import progress
        out = []
        for rec in progress.read_all():
            if rec.get("stage") in ("meaning", "paused", "ready"):
                lines = progress.plain_lines(rec, "chat")
                out.extend(lines[1:2])
        return out
    except Exception:  # noqa: BLE001 - no progress means nothing to add
        return []


# ---------------------------------------------------------------------------
# the CLI face
# ---------------------------------------------------------------------------

def add_cli_parser(subparsers: Any) -> None:
    """Register ``puffpai sources`` on the main parser."""
    sub = subparsers.add_parser(
        "sources", help="Find more knowledge and add it: search, add")
    actions = sub.add_subparsers(dest="sources_action")
    find = actions.add_parser("search", help="Look for books about something")
    find.add_argument("words", nargs="+", help="What it should know about")
    find.add_argument("--offline", action="store_true",
                      help="Search only the list saved with BuildBot")
    find.add_argument("--age", default=None,
                      help="The builder's age; books for children are shown first "
                           "for a young builder (nothing is hidden)")
    adder = actions.add_parser("add", help="Download one book and learn it")
    adder.add_argument("source_id", help="The id `puffpai sources search` printed")
    adder.add_argument("--yes", "-y", dest="yes", action="store_true",
                       help="Do not ask. Needs --accept-size as well")
    adder.add_argument("--accept-size", dest="accept_size", action="store_true",
                       help="With --yes: you accept the download size and license")
    sub.set_defaults(func=run_cli)


def _ask_yes(prompt: str) -> bool:
    try:
        answer = input(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def run_cli(args: argparse.Namespace) -> int:
    action = getattr(args, "sources_action", None)
    if action == "search":
        result = search(" ".join(args.words),
                        live=False if args.offline else None, age=args.age)
        for line in result["lines"] or ["Nothing found."]:
            print(line)
        return 0
    if action == "add":
        ident = str(args.source_id)
        first = add(ident, size_acknowledged=False, via="cli")
        if first.get("status") != "needs_confirmation":
            for line in first.get("lines") or [str(first.get("reason"))]:
                print(line)
            return 1
        for line in first["lines"]:
            print(line)
        if args.yes:
            if not args.accept_size:
                print("Not downloading: --yes also needs --accept-size, to say you "
                      "accept the size and license shown above.")
                return 1
        elif not _ask_yes("Download it now? [y/N] "):
            print("Nothing was downloaded.")
            return 1
        print("Downloading ...")
        done = add(ident, size_acknowledged=True, via="cli")
        for line in done.get("lines") or [str(done.get("reason"))]:
            print(line)
        return 0 if done.get("ok") else 1
    print("Use `puffpai sources search <words>` or `puffpai sources add <id>`.",
          file=sys.stderr)
    return 2


__all__ = ["ADD_COMMAND", "ID_RE", "TEXT_BOOK_PREFIXES", "is_text_book",
           "recorded_posture", "online_allowed",
           "knowledge_folder", "search", "search_lines", "look", "consent_lines",
           "add", "api_search", "api_add", "api_add_status", "ability_rows", "add_cli_parser",
           "run_cli"]
