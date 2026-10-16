"""What the backend's learning routes answer (kept out of api_server).

* ``GET /knowledge/learning`` -> :func:`payload`
* ``POST /knowledge/learning/deeper {"id", "on"}`` -> :func:`deeper`
* ``GET /knowledge/reading-helper`` -> :func:`helper_payload`

The routes sit behind the backend's API key and the dashboard's pairing
gate like every other ``/knowledge`` route. A request can name only a book
this build already learned (by its id from :func:`payload`) and a yes/no;
it can never name a file, a folder or an address.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from . import progress

logger = logging.getLogger(__name__)

_threads: dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()


def payload() -> dict[str, Any]:
    """Every book's record plus its lines in the browser's words."""
    books = []
    for rec in progress.read_all():
        stage = rec.get("stage")
        books.append({
            "id": rec.get("id"), "name": rec.get("name"), "stage": stage,
            "quick": rec.get("quick") or {}, "meaning": rec.get("meaning") or {},
            "running": stage in progress.RUNNING,
            "can_start": stage in ("not_started", "stopped"),
            "can_stop": stage in ("meaning", "paused", "ready"),
            "lines": progress.plain_lines(rec, "browser"),
        })
    return {"success": True, "books": books}


def deeper(body: Any, rag_getter: Optional[Callable[[], Any]] = None) -> tuple[dict, int]:
    """Start or stop one book's deeper stage. Returns (json, http status)."""
    from . import learn
    from .auto_index import PackIndex

    if not isinstance(body, dict):
        return {"success": False, "message": "Send a book and a yes or no."}, 400
    book_id = str(body.get("id") or "")
    on = body.get("on")
    if not isinstance(on, bool):
        return {"success": False, "message": "Send a book and a yes or no."}, 400
    pack_index = PackIndex()
    found = pack_index.find_by_book(book_id)
    if found is None or found[1].get("quick") != "complete":
        return {"success": False, "message": "That is not a book this bot has."}, 404
    uuid_hex, _rec = found
    if not on:
        learn.request_stop(book_id)
        pack_index.mark(uuid_hex, meaning=learn.M_OFF)
        return {"success": True, "stopping": True}, 200
    learn.clear_stop(book_id)
    pack_index.mark(uuid_hex, meaning=learn.M_PENDING)
    with _threads_lock:
        running = _threads.get(book_id)
        if running is not None and running.is_alive():
            return {"success": True, "started": False}, 200

        def work() -> None:
            try:
                rag = rag_getter() if rag_getter else None
                learn.deep_learn(book_id, pack_index=PackIndex(), rag=rag,
                                 owner_asked=True)
            except Exception as exc:  # noqa: BLE001 - recorded in the progress file
                logger.warning("Learning %s stopped: %s", book_id, exc)

        thread = threading.Thread(target=work, name=f"puffpai-learn-{book_id}",
                                  daemon=True)
        _threads[book_id] = thread
        thread.start()
    return {"success": True, "started": True}, 200


def helper_payload() -> dict[str, Any]:
    from . import reading_helper
    return {"success": True, **reading_helper.status(),
            "row": reading_helper.outside_row()}


__all__ = ["deeper", "helper_payload", "payload"]
