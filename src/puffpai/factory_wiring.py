"""Connect the bot's parts to each other, once per process.

Called when the backend starts (:func:`puffpai.api_server.start_background_services`)
and when ``puffpai chat`` starts, so both answer from the same facts:

* the one list of what the bot can do (:mod:`puffpai.build_abilities`) hears
  from the live cards, the routines and the downloaded books;
* the routines read the live cards' stored values (never online) and pick a
  passage from the downloaded books for a daily update.

The app host's and the locked box's wiring
(``webapps.wiring.connect``, ``box.wiring.connect``) and Toolsmith's own
(``integrate.apply.connect``) run here, and then Drawbridge's checker registry
is CLOSED (:func:`puffpai.drawbridge.close_registration`), always, even when a
part failed: after this nobody can add or replace a checker. A part that is
not in this build is skipped; a part that exists but is broken raises.

Nothing here goes online or writes anything. Calling it twice is harmless.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def library_picker(rag_getter: Optional[Callable[[], Any]] = None
                   ) -> Callable[[list], Optional[dict]]:
    """A routines library picker over the same books the chat reads.

    :func:`puffpai.knowledge.book_search.pick_passage` is the check: it
    reads every book's word look-up (ready seconds after a book arrives)
    and, through *rag_getter* (the backend's own cached store), the meaning
    look-up. Books only, so an owner's own uploaded file is never read out
    on a card or aloud. Books learned before the word look-up existed are
    still read from the store's labelled passages.
    """
    from puffpai import routines

    def pick(terms: list) -> Optional[dict]:
        rag = None
        if rag_getter is not None:
            try:
                rag = rag_getter()
            except Exception:  # noqa: BLE001 - no store means word look-up only
                rag = None
        try:
            from puffpai.knowledge import book_search
            item = book_search.pick_passage(list(terms or []), rag=rag,
                                            request=routines.library_request(terms))
        except Exception as exc:  # noqa: BLE001 - a picker failure is no passage
            logger.debug("book passage not picked: %s", exc)
            item = None
        if item:
            return item
        if rag is not None:
            try:
                coll = rag.knowledge_collection
            except Exception:  # noqa: BLE001 - no store means no passage
                coll = None
            if coll is not None:
                return routines.pick_from_collection(coll, terms)
        return routines._default_library_picker(terms)

    return pick


def scheduled_messages_rows() -> list[dict[str, Any]]:
    """The routines replace the core "no scheduled messages" row.

    The routines clock runs inside the bot while it is running, and what it
    sends lands on the dashboard's messages card (and is spoken where a voice
    is set up). Same id as the core row, so it replaces it.
    """
    return [{"id": "scheduled_messages",
             "label": ("Send you messages on a schedule, like a daily update "
                       "or a reminder, on your dashboard while your bot runs"),
             "status": "available", "how_to_add": None}]


def _optional(modname: str):
    """Pinned optional import (design I-10): None when not in this build."""
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


#: This wiring runs once per process (checkers can be registered once).
_ROUND3 = {"wired": False}

#: The modules whose wiring is called before Drawbridge closes (guarded).
ROUND3_WIRING = ("puffpai.webapps.wiring", "puffpai.box.wiring", "puffpai.deps",
                 "puffpai.assemble.glue_runtime.check")


def connect_round3() -> list[str]:
    """The checkers' wiring, then close Drawbridge's registry (always)."""
    from puffpai import drawbridge
    done: list[str] = []
    if _ROUND3["wired"]:
        return done
    _ROUND3["wired"] = True
    try:
        for modname in ROUND3_WIRING:
            module = _optional(modname)
            if module is None:
                done.append(f"{modname}: not in this build yet")
                continue
            done += [str(x) for x in (module.connect() or [])]
        from puffpai.integrate import apply as integrate_apply
        done += integrate_apply.connect()
    finally:
        drawbridge.close_registration()
        done.append("drawbridge:closed")
    return done


def connect(*, rag_getter: Optional[Callable[[], Any]] = None) -> list[str]:
    """Register every provider and reader. Returns what was connected."""
    try:
        done = _connect_wave(rag_getter)
    finally:
        # Whatever happened above, these parts are wired and the
        # checker registry is closed before this returns.
        round3 = connect_round3()
    return done + round3


def _connect_wave(rag_getter: Optional[Callable[[], Any]]) -> list[str]:
    done: list[str] = []
    try:
        from puffpai import build_abilities, feeds, routines
        from puffpai.knowledge import find_more
    except Exception as exc:  # noqa: BLE001 - a part missing is a smaller bot
        logger.warning("factory wiring skipped: %s", exc)
        return done
    for provider in (feeds.ability_rows, routines.ability_rows,
                     find_more.ability_rows, scheduled_messages_rows):
        build_abilities.register_provider(provider)
        done.append(f"abilities:{provider.__module__}")
    # Web search (keyless Wikipedia, then sign-up keys) and looks.
    try:
        from puffpai import web_search
        from puffpai.looks import proposals as looks_proposals
        for provider in (web_search.ability_rows, looks_proposals.ability_rows):
            build_abilities.register_provider(provider)
            done.append(f"abilities:{provider.__module__}")
    except Exception as exc:  # noqa: BLE001 - a part missing is a smaller bot
        logger.warning("search/looks abilities skipped: %s", exc)
    routines.set_feed_reader(feeds.latest_readings)
    done.append("routines:feed_reader")
    routines.set_library_picker(library_picker(rag_getter))
    done.append("routines:library_picker")
    return done


__all__ = ["ROUND3_WIRING", "connect", "connect_round3", "library_picker"]
