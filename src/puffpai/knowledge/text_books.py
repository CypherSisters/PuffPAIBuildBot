"""Books made of fetched text: saved as a folder, learned like any other book.

The Kiwix library ships books as single ``.zim`` files. The other trusted
catalogs (a Wikipedia topic book, a Project Gutenberg book, an OpenStax
textbook) hand over plain text, one article or page at a time. This module
turns that text into a **book folder** in the knowledge folder and learns it
in the same two stages as the ``.zim`` books (:mod:`puffpai.knowledge.learn`:
a word look-up in seconds, then passages by meaning in the background), so
the chat's book look-up (:mod:`puffpai.knowledge.book_search`) finds it and
cites it by the book's own name.

A book folder is ``<knowledge folder>/<name>.book/``::

    book.json      what it is, where every page came from, its license
    text/0001.txt  one article or page per file, plain UTF-8
    learned.json   which files are already learned (so growing a book only
                   learns the new pages)

Rules this module keeps:

* **Fetched text is data.** It is written to disk and embedded; nothing in it
  is ever read as an instruction. ``book.json`` carries the same trust note
  the downloader writes next to every ``.zim``.
* **Provenance is recorded per page**: title, address, revision (when the
  catalog has one), plus the book's license and credit line.
* **Bounded.** A book is at most :data:`MAX_BOOK_BYTES` of text; a page that
  would pass the limit is not written, and the book says it stopped early.
* **Written whole or not at all.** A new book is written to a ``.part``
  folder and renamed into place; a half-written book never looks finished.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
from typing import Any, Callable, Iterable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

SCHEMA = "text_book/v1"
BOOK_SUFFIX = ".book"
BOOK_FILE = "book.json"
LEARNED_FILE = "learned.json"
TEXT_DIR = "text"
MB = 1024 * 1024
#: The most text one book may hold (the owner's limit for these catalogs).
MAX_BOOK_BYTES = 25 * MB
#: The most text one page may hold; anything longer is cut at a line break.
MAX_PAGE_BYTES = 2 * MB
#: Pack-index keys for text books start with this, so they never collide with
#: a ``.zim`` book's UUID.
INDEX_PREFIX = "book:"

TRUST_NOTE = (
    "Downloaded reference material. This is DATA. It reaches the model only as "
    "quoted reference, never as instruction, and any paragraph written as an "
    "order to an AI assistant was left out when it was saved."
)

#: Put where a paragraph that talks to an AI assistant was left out. Anyone can
#: edit a wiki page, so fetched text is checked for orders aimed at the model
#: before it is saved; the chat also labels it as quoted reference. Both are
#: needed: a small local model can obey a well-placed order despite a label.
SET_ASIDE_MARK = "[A paragraph written as an order to an AI assistant was left out here.]"

_ORDER_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"\b(ignore|disregard|forget|override|bypass)\b[^.\n]{0,30}\b(previous|prior|above|"
    r"earlier|preceding|system|your)\b[^.\n]{0,20}\b(instructions?|directions?|prompts?|"
    r"guidelines)\b",
    r"\bsystem\s+(override|prompt|message|instruction)s?\b",
    r"\b(new|updated|real|actual)\s+instructions?\s*:",
    r"\byou\s+are\s+(now\s+)?(an?\s+|the\s+)?(ai|a\.i\.|assistant|chat\s*bot|"
    r"language\s+model|llm)\b",
    r"\b(reply|respond|answer|say|output|print)\b[^.\n]{0,40}\b(only|exactly)\b[^.\n]{0,40}"
    r"\b(words?|text|phrase|string)\b",
    r"\b(to|attention)\s*,?\s*(the|any|all)?\s*(ai|assistant|language\s+model|llm|"
    r"chat\s*bot)s?\s+(reading|processing|summari[sz]ing)\b",
    r"<\|?(im_start|im_end|system|endoftext)\|?>|\[/?INST\]|<<SYS>>",
)]


def set_aside_orders(text: str) -> tuple[str, int]:
    """``text`` with every paragraph that gives orders to an AI assistant
    replaced by :data:`SET_ASIDE_MARK`, and how many were replaced.

    Anything shaped like a key, a token or a password is taken out too
    (:func:`puffpai.security.secret_detector.redact_only`): a downloaded page
    is not the owner's, so the value is dropped, not kept in her vault. This
    runs when a page is saved and again when it is learned."""
    from puffpai.security import secret_detector
    out: list[str] = []
    count = 0
    for line in text.split("\n"):
        if line.strip() and any(p.search(line) for p in _ORDER_PATTERNS):
            out.append(SET_ASIDE_MARK)
            count += 1
        else:
            out.append(line)
    return secret_detector.redact_only("\n".join(out)), count


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,120}$")


class BookError(ValueError):
    """A book could not be written or read. The message is plain words."""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(
        microsecond=0).isoformat()


def folder_name(book_id: str) -> str:
    """``wikipedia:simple:dinosaur`` -> ``wikipedia_simple_dinosaur.book``."""
    text = str(book_id or "").strip().lower()
    if not _ID_RE.match(text):
        raise BookError("That is not a book id BuildBot made.")
    return re.sub(r"[^a-z0-9._-]+", "_", text).strip("._") + BOOK_SUFFIX


def book_path(root: str, book_id: str) -> str:
    return os.path.join(root, folder_name(book_id))


def _clean_text(text: Any) -> str:
    raw = str(text or "")
    # keep line breaks and tabs, drop every other control character
    raw = "".join(ch for ch in raw if ch in "\n\t" or ch.isprintable())
    raw = re.sub(r"[ \t]+\n", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _cut(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    cut = data[:limit].decode("utf-8", errors="ignore")
    at = cut.rfind("\n")
    return cut[:at] if at > limit // 2 else cut


def _short(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def read_book(folder: str) -> Optional[dict[str, Any]]:
    """``book.json`` of a book folder, or ``None`` if it is not one."""
    try:
        with open(os.path.join(folder, BOOK_FILE), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA \
            or not isinstance(data.get("items"), list):
        return None
    return data


def _write_json(path: str, data: dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def write_book(root: str, meta: dict[str, Any], pages: Iterable[dict[str, Any]],
               *, max_bytes: int = MAX_BOOK_BYTES) -> dict[str, Any]:
    """Write (or grow) one book folder. Returns ``book.json`` as written.

    ``meta`` needs ``id``, ``title`` and ``license``; it may carry
    ``license_url``, ``license_plain``, ``credit``, ``commercial_use``,
    ``personal_use_only``, ``catalog``, ``kind``, ``language``, ``source_url``
    and ``note``. Each page is ``{title, url, text, revision?}``. A page whose
    title is already in the book is skipped, so growing a book never
    duplicates a page.
    """
    book_id = str(meta.get("id") or "")
    final = book_path(root, book_id)
    if not str(meta.get("title") or "").strip() or not str(meta.get("license") or ""):
        raise BookError("A book needs a title and a license before it is saved.")
    existing = read_book(final)
    os.makedirs(root, exist_ok=True)
    work = final if existing is not None else final + ".part"
    if existing is None and os.path.isdir(work):
        shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.join(work, TEXT_DIR), exist_ok=True)

    items: list[dict[str, Any]] = list((existing or {}).get("items") or [])
    have = {str(i.get("title") or "").casefold() for i in items}
    total = sum(int(i.get("bytes") or 0) for i in items)
    number = max([int(re.sub(r"\D", "", str(i.get("file") or "")) or 0)
                  for i in items] or [0])
    stopped_early = False
    added = 0
    for page in pages:
        title = _short(page.get("title"), 200)
        if not title or title.casefold() in have:
            continue
        text, set_aside = set_aside_orders(
            _cut(_clean_text(page.get("text")), MAX_PAGE_BYTES))
        if not text:
            continue
        body = (title + "\n\n" + text) if not text.startswith(title) else text
        size = len(body.encode("utf-8"))
        if total + size > max_bytes:
            stopped_early = True
            break
        number += 1
        name = f"{number:04d}.txt"
        with open(os.path.join(work, TEXT_DIR, name), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(body)
        items.append({"file": f"{TEXT_DIR}/{name}", "title": title,
                      "url": _short(page.get("url"), 300),
                      "revision": _short(page.get("revision"), 40),
                      "bytes": size, "set_aside": set_aside})
        have.add(title.casefold())
        total += size
        added += 1

    record = dict(existing or {})
    record.update({
        "schema": SCHEMA,
        "id": book_id,
        "kind": _short(meta.get("kind"), 40),
        "catalog": _short(meta.get("catalog"), 80),
        "title": _short(meta.get("title"), 120),
        "language": _short(meta.get("language"), 20),
        "license": _short(meta.get("license"), 80),
        "license_url": _short(meta.get("license_url"), 300),
        "license_plain": _short(meta.get("license_plain"), 200),
        "credit": _short(meta.get("credit"), 400),
        "commercial_use": bool(meta.get("commercial_use")),
        # Personal-use-only books are fetched by the owner's own computer and
        # never included when a build is shared or sold.
        "personal_use_only": bool(meta.get("personal_use_only")),
        "share_with_build": not bool(meta.get("personal_use_only")),
        "source_url": _short(meta.get("source_url"), 300),
        "note": _short(meta.get("note"), 400),
        "trust_note": TRUST_NOTE,
        "set_aside": sum(int(i.get("set_aside") or 0) for i in items),
        "items": items,
        "total_bytes": total,
        "stopped_at_limit": bool(stopped_early or (existing or {}).get("stopped_at_limit")),
        "updated_at": _now(),
    })
    record.setdefault("fetched_at", record["updated_at"])
    if not items:
        if existing is None:
            shutil.rmtree(work, ignore_errors=True)
        raise BookError("Nothing readable came back, so no book was saved.")
    _write_json(os.path.join(work, BOOK_FILE), record)
    if existing is None:
        if os.path.isdir(final):
            shutil.rmtree(final, ignore_errors=True)
        os.replace(work, final)
    record["_added"] = added
    record["_path"] = final
    return record


def find_books(root: str) -> list[str]:
    """Every finished book folder directly inside ``root``."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    return [os.path.join(root, n) for n in names
            if n.endswith(BOOK_SUFFIX) and read_book(os.path.join(root, n)) is not None]


# ---------------------------------------------------------------------------
# learning: the same knowledge store and pack index the .zim books use
# ---------------------------------------------------------------------------

def _learned(folder: str) -> set[str]:
    try:
        with open(os.path.join(folder, LEARNED_FILE), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return set()
    files = data.get("files") if isinstance(data, dict) else None
    return {str(f) for f in files} if isinstance(files, list) else set()


def provenance(book: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """What every chunk of one page is stamped with (short facts we control)."""
    fields = {
        "source_id": book.get("id"),
        "source_label": book.get("title"),
        "source_license": book.get("license"),
        "source_license_url": book.get("license_url"),
        "source_url": item.get("url"),
        "source_revision": item.get("revision"),
        "source_fetched_at": book.get("updated_at") or book.get("fetched_at"),
        "source_commercial_use": bool(book.get("commercial_use")),
    }
    return {k: v for k, v in fields.items() if v is not None and v != ""}


def pending_files(folder: str) -> list[str]:
    book = read_book(folder) or {}
    done = _learned(folder)
    return [str(i.get("file")) for i in book.get("items") or []
            if str(i.get("file")) not in done]


def learn_id(book_id: Any) -> str:
    """The id a text book is learned under (its word look-up file, its
    progress record, its passages): ``book_`` + the catalog id in lower-case
    letters, digits and underscores."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(book_id or "").lower()).strip("_")
    return ("book_" + slug)[:120]


def learn(folder: str, *, pack_index: Any = None,
          fts_dir: Optional[str] = None) -> dict[str, Any]:
    """Make one book folder answerable: its quick stage. Never raises.

    The same two stages as every other book (:mod:`puffpai.knowledge.learn`):
    the pages go into the book's own word look-up in a second or two, and the
    pack index gets a ``book:<id>`` record whose meaning stage is then run by
    :func:`puffpai.knowledge.learn.deep_learn` in the background (at most half
    the processor, paused while someone talks). A grown book adds only its new
    pages, and its meaning stage carries on from where it stopped.

    Returns ``{status, docs, chunks, learned_now, id, book_id}``.
    """
    import sqlite3
    import time

    from puffpai.knowledge import auto_index, fts_index, progress
    from puffpai.knowledge import learn as two_stage

    book = read_book(folder)
    if book is None:
        return {"status": auto_index.STATUS_ERROR, "error": "not a book folder",
                "docs": 0, "chunks": 0, "learned_now": 0, "id": "", "book_id": ""}
    key = INDEX_PREFIX + str(book["id"])
    lid = learn_id(book["id"])
    name = str(book.get("title") or book["id"])[:80]
    pack_index = pack_index or auto_index.PackIndex()
    fts_dir = fts_dir or two_stage.books_dir()
    previous = pack_index.get(key) or {}
    todo = pending_files(folder)
    shard = os.path.join(fts_dir, f"{lid}.db")
    base = {"filename": os.path.basename(folder), "path": folder, "book_id": lid,
            "name": name, "source_id": str(book["id"]), "kind": "text_book"}
    if not todo and previous.get("quick") == "complete" and os.path.isfile(shard):
        return {"status": auto_index.STATUS_COMPLETE, "docs": int(previous.get("docs") or 0),
                "chunks": int(previous.get("chunks") or 0), "learned_now": 0,
                "id": book["id"], "book_id": lid}
    if not todo:
        # The look-up is missing but every page is marked learned: build it again.
        todo = [str(i.get("file")) for i in book.get("items") or []]
    lock = two_stage.BookLock(fts_dir, lid)
    if not lock.acquire():
        return {"status": "busy", "docs": 0, "chunks": 0, "learned_now": 0,
                "id": book["id"], "book_id": lid}
    writer = progress.Writer(lid, name)
    began = time.monotonic()
    try:
        writer.update(force=True, stage="quick", error="",
                      quick={"done": 0, "total": len(todo)})
        pack_index.mark(key, **base, status=auto_index.STATUS_INDEXING,
                        started_at=_now(), entry_count=len(book.get("items") or []),
                        quick="running")
        by_file = {str(i.get("file")): i for i in book.get("items") or []}
        done = _learned(folder)
        records = []
        for file_name in todo:
            item = by_file.get(file_name) or {}
            try:
                with open(os.path.join(folder, *file_name.split("/")), "r",
                          encoding="utf-8") as fh:
                    # checked again here: a folder may predate the check or
                    # have been edited by hand since it was saved
                    text = set_aside_orders(fh.read())[0]
            except OSError:
                continue
            done.add(file_name)
            if text.strip():
                records.append((file_name, str(item.get("title") or name), text))
        os.makedirs(fts_dir, exist_ok=True)
        conn = sqlite3.connect(shard, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            fts_index.FTSBuilder._ensure_schema(conn)
            added = fts_index.FTSBuilder._index_records(conn, records)
            count = conn.execute("SELECT count(*) FROM docs").fetchone()[0]
            fts_index._set_meta(
                conn, schema_version=fts_index.SCHEMA_VERSION, status="complete",
                zim_filename=os.path.basename(folder),
                zim_size=int(book.get("total_bytes") or 0),
                zim_mtime=int(time.time()), article_count=int(count),
                **fts_index.clean_labels({
                    "source_id": str(book["id"]), "source_label": name,
                    "source_license": str(book.get("license") or ""),
                    "book_name": name}))
            conn.commit()
        finally:
            conn.close()
        _write_json(os.path.join(folder, LEARNED_FILE), {"files": sorted(done)})
        articles, chars = two_stage.text_totals(shard)
        meaning = previous.get("meaning")
        if meaning == two_stage.M_RUNNING:
            meaning = two_stage.M_STOPPED
        if meaning != two_stage.M_OFF and (added or meaning not in (
                two_stage.M_DONE, two_stage.M_STOPPED, two_stage.M_NOT_STARTED,
                two_stage.M_WAITING_HELPER)):
            meaning = two_stage.M_PENDING
        pack_index.mark(key, **base, status=auto_index.STATUS_COMPLETE,
                        quick="complete", indexed_at=_now(), docs=int(count),
                        chars_total=chars, chunks=int(previous.get("chunks") or 0),
                        meaning=meaning,
                        quick_seconds=round(time.monotonic() - began, 2))
        writer.update(force=True, quick_ready=True,
                      stage={two_stage.M_DONE: "done", two_stage.M_OFF: "not_started",
                             two_stage.M_STOPPED: "stopped",
                             two_stage.M_NOT_STARTED: "not_started",
                             two_stage.M_WAITING_HELPER: "waiting_helper",
                             }.get(meaning, "ready"),
                      quick={"done": len(todo), "total": len(todo)},
                      meaning={"articles_done": 0, "articles_total": articles,
                               "chars_done": 0, "chars_total": chars,
                               "seconds_left": None, "basis": None}
                      if meaning == two_stage.M_PENDING
                      else writer.rec.get("meaning") or {})
    except Exception as exc:  # noqa: BLE001 - record it; the book stays on disk
        logger.warning("Could not learn %s: %s", os.path.basename(folder), exc)
        pack_index.mark(key, **base, status=auto_index.STATUS_ERROR,
                        error=str(exc)[:200], indexed_at=_now())
        writer.update(force=True, stage="error", error="unreadable")
        return {"status": auto_index.STATUS_ERROR, "error": str(exc)[:200],
                "docs": 0, "chunks": 0, "learned_now": 0, "id": book["id"],
                "book_id": lid}
    finally:
        lock.release()
    return {"status": auto_index.STATUS_COMPLETE, "docs": int(count),
            "chunks": int(previous.get("chunks") or 0), "learned_now": added,
            "id": book["id"], "book_id": lid}


def needs_learning(root: str, pack_index: Any = None) -> list[str]:
    """Book folders in ``root`` with pages that cannot answer questions yet."""
    from puffpai.knowledge import auto_index
    pack_index = pack_index or auto_index.PackIndex()
    out = []
    for folder in find_books(root):
        book = read_book(folder) or {}
        rec = pack_index.get(INDEX_PREFIX + str(book.get("id"))) or {}
        if rec.get("status") == auto_index.STATUS_REVOKED:
            continue
        if pending_files(folder) or rec.get("quick") != "complete":
            out.append(folder)
    return out


def sync_books(root: str, *, pack_index: Any = None,
               fts_dir: Optional[str] = None) -> list[dict[str, Any]]:
    """The quick stage for every book folder in ``root`` that needs it."""
    from puffpai.knowledge import auto_index
    pack_index = pack_index or auto_index.PackIndex()
    return [learn(folder, pack_index=pack_index, fts_dir=fts_dir)
            for folder in needs_learning(root, pack_index)]


__all__ = ["SCHEMA", "BOOK_SUFFIX", "MAX_BOOK_BYTES", "INDEX_PREFIX", "TRUST_NOTE",
           "SET_ASIDE_MARK", "set_aside_orders",
           "BookError", "folder_name", "book_path", "read_book", "write_book",
           "find_books", "provenance", "pending_files", "learn", "learn_id",
           "needs_learning", "sync_books"]
