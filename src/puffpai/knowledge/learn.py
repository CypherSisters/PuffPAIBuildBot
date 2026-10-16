"""Learning a book in two stages: answerable in seconds, better in the background.

**Quick** (required, first): the book's text goes into a word look-up
(:mod:`puffpai.knowledge.fts_index`, one SQLite file per book). A 10 MB book
takes a few seconds and can answer questions the moment it finishes.

**Meaning** (optional, in the background): the text is read back out of the
quick look-up (the book file is not walked again), cut into short passages
(:func:`puffpai.knowledge.chunking.chunk_windows`) and turned into the
reading helper's vectors, so questions worded differently from the book also
match. 128 passages per helper call, 1,024 per write, a checkpoint after
every write so a killed run picks up where it stopped, at most half the
processor, and a pause whenever someone is talking to the bot (in any
process: see :func:`puffpai.knowledge.progress.someone_talking`).

Passage ids carry the book id (``<book>:<article>:<n>``), so the same article
in two books never collides and removing one book never touches the other.

One book at a time per book: an OS file lock (freed by the OS if the process
dies) keeps two processes from learning the same book at once.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
import time
from typing import Any, Callable, Optional

from . import progress
from .chunking import chunk_windows
from .fts_index import (FTSBuilder, clean_labels, library_id_for, shard_labels,
                        shard_path_for, shard_status)

logger = logging.getLogger(__name__)

#: Share of the processor the meaning stage may use.
CPU_SHARE = 0.5
EMBED_BATCH = 128
WRITE_BATCH = 1024
#: Articles shorter than this are not worth a passage of their own.
MIN_ARTICLE_CHARS = 100
#: The meaning stage starts by itself when it would take at most this long
#: here (measured), or, before anything was timed here, when the book has at
#: most this much text. Anything bigger waits for the owner.
AUTO_MAX_SECONDS = 2 * 3600
AUTO_MAX_CHARS_UNTIMED = 60_000_000
#: Free space the quick stage needs, as a multiple of the book file's size.
QUICK_SPACE_FACTOR = 2.5
QUICK_SPACE_HEADROOM = 512 * 1024 * 1024
KNOWLEDGE_ZONE = "puffpai_knowledge"
#: How long a write waits for another writer of the same database (seconds).
BUSY_SECONDS = 30
#: One writer at a time for the passages store and the books' checkpoints in
#: this process (the background learner, a book the owner asked for, text books).
WRITE_LOCK = threading.Lock()
#: A write that still finds the database locked is tried again this often.
WRITE_TRIES = 4

# pack-index "meaning" states
M_PENDING = "pending"
M_RUNNING = "running"
M_DONE = "done"
M_STOPPED = "stopped"
M_OFF = "off"
M_NOT_STARTED = "not_started"
M_WAITING_HELPER = "waiting_helper"


# -- places ------------------------------------------------------------------------

def books_dir(cfg: Any = None) -> str:
    """Where the quick look-ups live: ``paths.fts_dir``, else ``<data>/data/knowledge_fts``.

    Under ``data/`` like the uploads, which the checkout's ignore list keeps
    out of git when the data folder is the checkout itself.
    """
    if cfg is None:
        from puffpai.config import get_config
        cfg = get_config()
    configured = str(getattr(getattr(cfg, "paths", None), "fts_dir", "") or "").strip()
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    return os.path.join(cfg.DATA_DIR, "data", "knowledge_fts")


def _stop_path(book_id: str) -> str:
    return os.path.join(progress.learning_dir(), f"{book_id}.stop")


def request_stop(book_id: str) -> None:
    """Ask whoever is learning *book_id* deeply to stop at the next passage batch."""
    try:
        os.makedirs(progress.learning_dir(), exist_ok=True)
        with open(_stop_path(book_id), "w", encoding="utf-8") as fh:
            fh.write("stop")
    except OSError:
        pass


def clear_stop(book_id: str) -> None:
    try:
        os.unlink(_stop_path(book_id))
    except OSError:
        pass


def stop_requested(book_id: str) -> bool:
    return os.path.exists(_stop_path(book_id))


class BookLock:
    """A per-book OS lock on ``<books_dir>/<book>.lock``. Freed if the process dies."""

    def __init__(self, folder: str, book_id: str) -> None:
        self.path = os.path.join(folder, f"{book_id}.lock")
        self._fh = None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc: Any) -> None:
        self.release()


# -- names -------------------------------------------------------------------------

def book_name(meta: dict[str, Any], provenance: Optional[dict[str, Any]]) -> str:
    """The book's name for people: its download label, else the title the
    book file gives itself, else words from its file name."""
    label = str((provenance or {}).get("source_label") or "").strip()
    if label:
        return label[:80]
    own = _zim_title(str(meta.get("filepath") or ""))
    if own:
        return own
    stem = os.path.splitext(str(meta.get("filename") or "book"))[0]
    words = [w for w in stem.replace("-", "_").split("_") if w and not w.isdigit()]
    return (" ".join(words).strip() or "A book")[:80].capitalize()


def _zim_title(path: str) -> str:
    """The book file's own title (its ``M/Title``), one short plain line, or ''.

    It comes from the file, so it is treated as data: control characters go,
    and it is cut to 60 characters.
    """
    if not path or not os.path.isfile(path):
        return ""
    try:
        from .zim_reader import ZIMReader
        with ZIMReader(path) as reader:
            art = reader.get_article_by_url("M/Title")
        raw = art.content.decode("utf-8", errors="replace") if art else ""
    except Exception:  # noqa: BLE001 - no title means the file name is used
        return ""
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in raw).split())
    return text[:60]


def labels_for(meta: dict[str, Any], provenance: Optional[dict[str, Any]]) -> dict[str, str]:
    prov = provenance or {}
    return clean_labels({"source_id": prov.get("source_id"),
                         "source_label": prov.get("source_label"),
                         "source_license": prov.get("source_license"),
                         "book_name": book_name(meta, provenance)})


# -- quick stage -------------------------------------------------------------------

def text_totals(shard_path: str) -> tuple[int, int]:
    """(articles worth learning, characters in them) from a finished look-up."""
    conn = sqlite3.connect(f"file:{shard_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT count(*), coalesce(sum(length(body)), 0) FROM fts "
                           "WHERE length(body) >= ?", (MIN_ARTICLE_CHARS,)).fetchone()
        return int(row[0] or 0), int(row[1] or 0)
    finally:
        conn.close()


def _has_room(folder: str, need: int) -> bool:
    try:
        os.makedirs(folder, exist_ok=True)
        return shutil.disk_usage(folder).free >= need
    except OSError:
        return True  # cannot tell: let the write itself fail honestly


def quick_stage(meta: dict[str, Any], *, fts_dir: str, labels: dict[str, str],
                writer: progress.Writer) -> Any:
    """Build (or resume, or skip) the book's word look-up. Returns the BuildSummary."""
    def tick(done: int, total: int, _indexed: int) -> None:
        writer.quick(done, total)
    return FTSBuilder().build(meta["filepath"], fts_dir, batch_size=500,
                              progress_cb=tick, labels=labels)


# -- meaning stage -----------------------------------------------------------------

def write_conn(path: str) -> sqlite3.Connection:
    """A connection that writes *path*, waiting up to BUSY_SECONDS for
    another writer instead of failing with "database is locked"."""
    conn = sqlite3.connect(path, timeout=BUSY_SECONDS)
    conn.execute(f"PRAGMA busy_timeout = {BUSY_SECONDS * 1000}")
    return conn


def locked(exc: BaseException) -> bool:
    return "database is locked" in str(exc).lower() or "database is busy" in str(exc).lower()


def write(fn: Callable[[], Any], *, tries: Optional[int] = None,
          sleep: Callable[[float], None] = time.sleep) -> Any:
    """Run one write under WRITE_LOCK; a write that finds the database locked
    (a writer in another process) is tried again after a pause."""
    tries = tries or WRITE_TRIES
    for n in range(tries):
        try:
            with WRITE_LOCK:
                return fn()
        except Exception as exc:  # noqa: BLE001 - sqlite3 or the store's own error
            if not locked(exc) or n == tries - 1:
                raise
            sleep(min(5.0 * (n + 1), 15.0))
    return None


def _checkpoint(conn: sqlite3.Connection) -> dict[str, int]:
    out = {"rowid": 0, "articles": 0, "chars": 0, "chunks": 0}
    for key in out:
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = ?",
                               (f"meaning_{key}",)).fetchone()
            out[key] = int(row[0]) if row and row[0] else 0
        except (sqlite3.Error, ValueError):
            pass
    return out


def _save_checkpoint(conn: sqlite3.Connection, **values: int) -> None:
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [(f"meaning_{k}", str(int(v))) for k, v in values.items()])
    conn.commit()


def reset_checkpoint(shard_path: str) -> None:
    conn = write_conn(shard_path)
    try:
        def go() -> None:
            conn.execute("DELETE FROM meta WHERE key LIKE 'meaning_%'")
            conn.commit()
        write(go)
    finally:
        conn.close()


def meaning_stage(book_id: str, shard_path: str, collection: Any, embedder: Any, *,
                  labels: dict[str, str], writer: progress.Writer,
                  embed_batch: int = EMBED_BATCH, write_batch: int = WRITE_BATCH,
                  should_pause: Callable[[], bool] = progress.someone_talking,
                  should_stop: Optional[Callable[[], bool]] = None,
                  cpu_share: float = CPU_SHARE,
                  sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Turn the book's passages into vectors, resuming after the last checkpoint.

    Returns ``{"status": "done"|"stopped", "chunks": int, "chars_per_s":
    float|None, "active_seconds": float}`` (time spent working, pauses left out).
    """
    should_stop = should_stop or (lambda: stop_requested(book_id))
    articles_total, chars_total = text_totals(shard_path)
    wconn = write_conn(shard_path)
    rconn = sqlite3.connect(f"file:{shard_path}?mode=ro", uri=True)
    try:
        cp = _checkpoint(wconn)
        writer.start_meaning(articles_total=articles_total, chars_total=chars_total,
                             articles_done=cp["articles"], chars_done=cp["chars"])
        state = {"articles": cp["articles"], "chars": cp["chars"],
                 "chunks": cp["chunks"], "paused": 0.0}
        started = time.monotonic()
        chars_at_start = cp["chars"]
        extra = {k: v for k, v in labels.items() if k != "book_name"}

        def rate() -> Optional[float]:
            active = time.monotonic() - started - state["paused"]
            done = state["chars"] - chars_at_start
            return done / active if active >= 20 and done > 0 else None

        def result(status: str, measured: Optional[float]) -> dict[str, Any]:
            return {"status": status, "chunks": state["chunks"], "chars_per_s": measured,
                    "active_seconds": round(time.monotonic() - started - state["paused"], 2)}

        def wait_while_talking() -> bool:
            """Pause while someone talks. False when a stop came instead."""
            if not should_pause():
                return True
            began = time.monotonic()
            while should_pause():
                if should_stop():
                    return False
                writer.meaning(state["articles"], state["chars"], stage="paused")
                sleep(1.0)
            state["paused"] += time.monotonic() - began
            writer.resume_clock()
            return True

        def flush(buf: list[tuple]) -> bool:
            """Embed and write one buffer of passages. False when told to stop."""
            vectors: list = []
            for i in range(0, len(buf), embed_batch):
                if should_stop() or not wait_while_talking():
                    return False
                part = buf[i:i + embed_batch]
                vectors.extend(embedder([p[1] for p in part]))
                for item in part:
                    if item[3]:  # the article's last passage
                        state["articles"] += 1
                        state["chars"] += item[4]
                writer.meaning(state["articles"], state["chars"])
            write(lambda: collection.upsert(
                ids=[p[0] for p in buf], documents=[p[1] for p in buf],
                metadatas=[p[2] for p in buf],
                embeddings=[list(map(float, v)) for v in vectors]), sleep=sleep)
            state["chunks"] += len(buf)
            write(lambda: _save_checkpoint(wconn, rowid=buf[-1][5], articles=state["articles"],
                                           chars=state["chars"], chunks=state["chunks"]),
                  sleep=sleep)
            measured = rate()
            if measured and time.monotonic() - started >= 60:
                progress.record_rate(measured, cpu_share=cpu_share)
            return True

        cur = rconn.execute(
            "SELECT d.id, d.title, f.body FROM docs d JOIN fts f ON f.rowid = d.id "
            "WHERE d.id > ? AND length(f.body) >= ? ORDER BY d.id",
            (cp["rowid"], MIN_ARTICLE_CHARS))
        buf: list[tuple] = []
        for rowid, title, body in _rows(cur):
            pieces = chunk_windows(body)
            for n, piece in enumerate(pieces):
                meta = {"title": str(title or "")[:300], "zone": KNOWLEDGE_ZONE,
                        "source": "zim_pack", "chunk_index": n,
                        "total_chunks": len(pieces), "book_id": book_id,
                        "article_rowid": int(rowid), **extra}
                buf.append((f"{book_id}:{rowid}:{n}", piece, meta,
                            n == len(pieces) - 1, len(body), int(rowid)))
            if len(buf) >= write_batch:
                if not flush(buf):
                    return result("stopped", rate())
                buf = []
        if buf and not flush(buf):
            return result("stopped", rate())
        measured = rate()
        if measured:
            progress.record_rate(measured, cpu_share=cpu_share)
        return result("done", measured)
    finally:
        rconn.close()
        wconn.close()


def _rows(cur: sqlite3.Cursor):
    while True:
        rows = cur.fetchmany(256)
        if not rows:
            return
        yield from rows


def delete_legacy_rows(collection: Any, source_id: str, page: int = 5000) -> int:
    """Delete passages of *source_id* written before passages carried a book id.

    Rows that carry a book id belong to a book learned the new way (perhaps
    another edition with the same source) and are kept.
    """
    if not source_id:
        return 0
    doomed: list[str] = []
    offset = 0
    while True:
        got = collection.get(where={"source_id": source_id}, include=["metadatas"],
                             limit=page, offset=offset)
        ids = got.get("ids") or []
        metas = got.get("metadatas") or [{}] * len(ids)
        doomed.extend(i for i, m in zip(ids, metas) if not (m or {}).get("book_id"))
        if len(ids) < page:
            break
        offset += page
    for i in range(0, len(doomed), page):
        write(lambda: collection.delete(ids=doomed[i:i + page]))
    return len(doomed)


# -- whole books -------------------------------------------------------------------

def auto_deeper(chars_total: int) -> bool:
    """Whether the meaning stage may start without the owner asking."""
    est = progress.estimate_seconds(chars_total)
    if est is not None:
        return est <= AUTO_MAX_SECONDS
    return chars_total <= AUTO_MAX_CHARS_UNTIMED


def quick_learn(meta: dict[str, Any], *, pack_index: Any, fts_dir: Optional[str] = None,
                provenance: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Make one book answerable. Records the outcome on the pack index."""
    from .auto_index import STATUS_COMPLETE, STATUS_ERROR, STATUS_INDEXING, _now
    from .auto_index import provenance_for_pack
    fts_dir = fts_dir or books_dir()
    if provenance is None:
        provenance = provenance_for_pack(meta["filepath"])
    book_id = library_id_for(meta["filepath"])
    name = book_name(meta, provenance)
    labels = labels_for(meta, provenance)
    writer = progress.Writer(book_id, name)
    source_id = labels.get("source_id", "")
    uuid_hex = meta["uuid_hex"]
    old = pack_index.get(uuid_hex) or {}
    base = {"filename": meta["filename"], "path": meta["filepath"],
            "book_id": book_id, "name": name, "source_id": source_id}

    lock = BookLock(fts_dir, book_id)
    if not lock.acquire():
        return {"filename": meta["filename"], "book_id": book_id, "status": "busy"}
    try:
        size = int(meta.get("file_size_bytes") or 0)
        done_before = shard_status(shard_path_for(meta["filepath"], fts_dir)).get("status")
        if done_before != "complete" and not _has_room(
                fts_dir, int(size * QUICK_SPACE_FACTOR) + QUICK_SPACE_HEADROOM):
            writer.update(force=True, stage="error", error="no_room")
            pack_index.mark(uuid_hex, **base, status=STATUS_ERROR,
                            error="not enough free space", indexed_at=_now())
            return {"filename": meta["filename"], "book_id": book_id,
                    "status": STATUS_ERROR, "error": "no_room"}
        pack_index.mark(uuid_hex, **base, status=STATUS_INDEXING, started_at=_now(),
                        entry_count=meta.get("entry_count", 0), quick="running")
        writer.update(force=True, stage="quick", error="")
        began = time.monotonic()
        summary = quick_stage(meta, fts_dir=fts_dir, labels=labels, writer=writer)
        if not summary.ok:
            code = "file_incomplete" if "truncated" in (summary.error or "") else "unreadable"
            writer.update(force=True, stage="error", error=code)
            pack_index.mark(uuid_hex, **base, status=STATUS_ERROR,
                            error=(summary.error or "")[:200], indexed_at=_now())
            return {"filename": meta["filename"], "book_id": book_id,
                    "status": STATUS_ERROR, "error": code}
        articles, chars = text_totals(summary.shard_path)
        if articles == 0:
            writer.update(force=True, stage="error", error="empty")
            pack_index.mark(uuid_hex, **base, status=STATUS_ERROR,
                            error="no text in this book", indexed_at=_now())
            return {"filename": meta["filename"], "book_id": book_id,
                    "status": STATUS_ERROR, "error": "empty"}
        seconds = round(time.monotonic() - began, 2)
        meaning = old.get("meaning") if old.get("meaning") in (
            M_DONE, M_OFF, M_STOPPED, M_RUNNING) else M_PENDING
        if meaning == M_RUNNING:
            meaning = M_STOPPED
        pack_index.mark(uuid_hex, **base, status=STATUS_COMPLETE, quick="complete",
                        indexed_at=_now(), docs=articles, chars_total=chars,
                        chunks=int(old.get("chunks") or 0), meaning=meaning,
                        quick_seconds=seconds)
        writer.update(force=True, stage="ready" if meaning == M_PENDING else
                      ("done" if meaning == M_DONE else "stopped"
                       if meaning == M_STOPPED else "not_started"),
                      quick_ready=True,
                      meaning={"articles_done": 0, "articles_total": articles,
                               "chars_done": 0, "chars_total": chars,
                               "seconds_left": None, "basis": None}
                      if meaning != M_STOPPED else writer.rec.get("meaning") or {})
        return {"filename": meta["filename"], "book_id": book_id,
                "status": STATUS_COMPLETE, "docs": articles, "chars": chars,
                "seconds": seconds, "chunks": int(old.get("chunks") or 0)}
    finally:
        lock.release()


def deep_learn(book_id: str, *, pack_index: Any, rag: Any = None,
               fts_dir: Optional[str] = None, owner_asked: bool = False,
               cpu_share: float = CPU_SHARE,
               should_pause: Callable[[], bool] = progress.someone_talking) -> dict[str, Any]:
    """Run (or resume) the meaning stage for one book whose quick stage is done."""
    from . import reading_helper
    fts_dir = fts_dir or books_dir()
    found = pack_index.find_by_book(book_id)
    if found is None:
        return {"book_id": book_id, "status": "unknown"}
    uuid_hex, rec = found
    shard = os.path.join(fts_dir, f"{book_id}.db")
    if shard_status(shard).get("status") != "complete":
        return {"book_id": book_id, "status": "not_ready"}
    labels = shard_labels(shard)
    name = labels.get("book_name") or str(rec.get("name") or book_id)
    writer = progress.Writer(book_id, name)
    articles, chars = text_totals(shard)
    if not owner_asked:
        if rec.get("meaning") in (M_OFF, M_DONE):
            return {"book_id": book_id, "status": rec.get("meaning")}
        if rec.get("meaning") != M_STOPPED and not auto_deeper(chars):
            pack_index.mark(uuid_hex, meaning=M_NOT_STARTED)
            writer.update(force=True, stage="not_started", quick_ready=True,
                          meaning={"articles_done": 0, "articles_total": articles,
                                   "chars_done": 0, "chars_total": chars,
                                   "seconds_left": None, "basis": None})
            return {"book_id": book_id, "status": M_NOT_STARTED}
    embedder = reading_helper.get_embedder(threads=reading_helper.cpu_threads(cpu_share))
    if embedder is None:
        pack_index.mark(uuid_hex, meaning=M_WAITING_HELPER)
        writer.update(force=True, stage="waiting_helper", quick_ready=True)
        return {"book_id": book_id, "status": M_WAITING_HELPER}
    lock = BookLock(fts_dir, book_id)
    if not lock.acquire():
        return {"book_id": book_id, "status": "busy"}
    try:
        clear_stop(book_id)
        if rag is None:
            from .rag import DualZoneRAG
            rag = DualZoneRAG()
        collection = rag.knowledge_collection
        conn = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
        try:
            fresh = _checkpoint(conn)["rowid"] == 0
        finally:
            conn.close()
        if fresh:
            # A book learned before this stage existed has one passage per
            # article in the store; they are replaced, not doubled.
            try:
                delete_legacy_rows(collection, labels.get("source_id", ""))
            except Exception as exc:  # noqa: BLE001 - old rows only cost duplicates
                logger.debug("old passages of %s not removed: %s", book_id, exc)
        pack_index.mark(uuid_hex, meaning=M_RUNNING)
        writer.update(quick_ready=True)
        try:
            out = meaning_stage(book_id, shard, collection, embedder, labels=labels,
                                writer=writer, should_pause=should_pause,
                                cpu_share=cpu_share)
        except reading_helper.HelperMissing:
            pack_index.mark(uuid_hex, meaning=M_WAITING_HELPER)
            writer.update(force=True, stage="waiting_helper")
            return {"book_id": book_id, "status": M_WAITING_HELPER}
        except Exception as exc:  # noqa: BLE001 - recorded, and resumable
            logger.warning("Learning %s stopped: %s", book_id, exc)
            pack_index.mark(uuid_hex, meaning=M_STOPPED)
            writer.update(force=True, stage="stopped")
            return {"book_id": book_id, "status": M_STOPPED, "error": str(exc)[:200]}
        status = M_DONE if out["status"] == "done" else M_STOPPED
        spent = float(rec.get("meaning_seconds") or 0) + float(out.get("active_seconds") or 0)
        extra: dict[str, Any] = {}
        if status == M_DONE:
            # The record's started_at..indexed_at is what index_estimate reads
            # as "how long learning these passages took on this computer", so
            # it spans the whole of it: quick stage plus the deeper stage's
            # working time over every run (pauses and restarts left out).
            from datetime import datetime, timedelta, timezone
            end = datetime.now(timezone.utc)
            took = spent + float(rec.get("quick_seconds") or 0)
            extra = {"indexed_at": end.isoformat(),
                     "started_at": (end - timedelta(seconds=took)).isoformat()}
        pack_index.mark(uuid_hex, meaning=status, chunks=out["chunks"],
                        meaning_seconds=round(spent, 2), **extra)
        writer.update(force=True, stage="done" if status == M_DONE else "stopped")
        return {"book_id": book_id, "status": status, "chunks": out["chunks"],
                "chars_per_s": out.get("chars_per_s")}
    finally:
        lock.release()


def meaning_work(pack_index: Any) -> list[str]:
    """Book ids whose meaning stage may run now without the owner asking."""
    from . import reading_helper
    from .auto_index import STATUS_COMPLETE
    helper = None
    out = []
    for rec in pack_index.records():
        if rec.get("status") != STATUS_COMPLETE or rec.get("quick") != "complete":
            continue
        state = rec.get("meaning")
        if state == M_WAITING_HELPER:
            if helper is None:
                helper = reading_helper.is_ready()
            if not helper:
                continue
        elif state not in (M_PENDING, M_RUNNING, M_STOPPED):
            continue
        # A .zim book is a file; a text book (knowledge.text_books) a folder.
        if rec.get("book_id") and os.path.exists(str(rec.get("path") or "")):
            out.append(str(rec["book_id"]))
    return out


def find_book(text: str, pack_index: Any) -> Optional[tuple[str, dict[str, Any]]]:
    """A book by its id, its file name or words of its name (one match only)."""
    wanted = str(text or "").strip().lower()
    if not wanted:
        return None
    recs = [r for r in pack_index.records() if r.get("book_id")]
    for rec in recs:
        if wanted in (str(rec["book_id"]).lower(), str(rec.get("filename") or "").lower(),
                      str(rec.get("name") or "").lower()):
            return str(rec["book_id"]), rec
    words = wanted.split()
    hits = [r for r in recs
            if all(w in f"{r.get('name', '')} {r.get('filename', '')}".lower()
                   for w in words)]
    if len(hits) == 1:
        return str(hits[0]["book_id"]), hits[0]
    return None


def remove_book(book_id: str, *, pack_index: Any, rag: Any = None,
                fts_dir: Optional[str] = None, delete_file: bool = True,
                wait_seconds: float = 30.0) -> dict[str, Any]:
    """Forget a book: its look-up, its passages in the store, its progress, its file.

    Passages are deleted by book id (and, for books learned before book ids
    existed, by the download's source id). Returns what happened, in counts.
    """
    from .auto_index import STATUS_REVOKED, _now
    fts_dir = fts_dir or books_dir()
    found = pack_index.find_by_book(book_id)
    shard = os.path.join(fts_dir, f"{book_id}.db")
    labels = shard_labels(shard) if os.path.isfile(shard) else {}
    request_stop(book_id)
    lock = BookLock(fts_dir, book_id)
    deadline = time.monotonic() + wait_seconds
    while not lock.acquire():
        if time.monotonic() > deadline:
            return {"ok": False, "reason": "busy"}
        time.sleep(0.5)
    removed_rows = 0
    try:
        try:
            if rag is None:
                from .rag import DualZoneRAG
                rag = DualZoneRAG()
            coll = rag.knowledge_collection
            before = coll.count()
            coll.delete(where={"book_id": book_id})
            source_id = labels.get("source_id") or str((found or ("", {}))[1].get("source_id") or "")
            delete_legacy_rows(coll, source_id)
            removed_rows = max(0, before - coll.count())
        except Exception as exc:  # noqa: BLE001 - reported
            logger.warning("Could not remove %s from the store: %s", book_id, exc)
            return {"ok": False, "reason": "store"}
        for suffix in (".db", ".db-wal", ".db-shm"):
            try:
                os.unlink(os.path.join(fts_dir, book_id + suffix))
            except OSError:
                pass
        progress.remove(book_id)
        file_gone = False
        if found is not None:
            uuid_hex, rec = found
            path = str(rec.get("path") or "")
            if delete_file and path and os.path.isdir(path):
                # A text book is a folder of pages (knowledge.text_books).
                shutil.rmtree(path, ignore_errors=True)
                file_gone = not os.path.exists(path)
            elif delete_file and path:
                for p in (path, path + ".provenance.json"):
                    try:
                        os.unlink(p)
                        file_gone = file_gone or p == path
                    except OSError:
                        pass
            pack_index.mark(uuid_hex, status=STATUS_REVOKED, removed_at=_now(),
                            quick="removed", meaning=M_OFF, chunks=0)
        return {"ok": True, "rows": removed_rows, "file_removed": file_gone}
    finally:
        lock.release()
        clear_stop(book_id)
        try:
            os.unlink(lock.path)
        except OSError:
            pass



# -- books set aside (revoked) -------------------------------------------------------

def set_aside(pack_index: Any = None) -> tuple[set[str], set[str]]:
    """(book ids, source ids) of books whose record says they were set aside.

    ``source_fetch.revoke`` renames the file and marks the record revoked but
    does not know about the quick look-up, so every reader of the books checks
    this and treats such a book as gone. Reads the pack index fresh (another
    process may have revoked it a moment ago). Never raises.
    """
    books: set[str] = set()
    sources: set[str] = set()
    try:
        if pack_index is None:
            from .auto_index import PackIndex
            pack_index = PackIndex()
        from .auto_index import STATUS_REVOKED
        for rec in pack_index.records():
            if str(rec.get("status") or "") != STATUS_REVOKED:
                continue
            if rec.get("book_id"):
                books.add(str(rec["book_id"]))
            if rec.get("source_id"):
                sources.add(str(rec["source_id"]))
    except Exception as exc:  # noqa: BLE001 - an unreadable index hides nothing
        logger.warning("Could not read which books were set aside: %s", exc)
    return books, sources


def forget_set_aside(book_ids: Any, *, rag: Any = None,
                     fts_dir: Optional[str] = None) -> list[str]:
    """Delete the look-up, progress and (with *rag*) store rows of set-aside books.

    Best-effort and never waits: a book being worked on right now is skipped
    (readers still hide it through :func:`set_aside`). The set-aside file
    itself is left alone so a person can review or undo the revocation.
    Returns the book ids cleaned.
    """
    fts_dir = fts_dir or books_dir()
    done: list[str] = []
    for book_id in sorted({str(b) for b in (book_ids or ()) if b}):
        shard = os.path.join(fts_dir, f"{book_id}.db")
        if not os.path.isfile(shard) and progress.read(book_id) is None:
            continue
        lock = BookLock(fts_dir, book_id)
        if not lock.acquire():
            continue
        try:
            if rag is not None:
                try:
                    rag.knowledge_collection.delete(where={"book_id": book_id})
                except Exception as exc:  # noqa: BLE001 - rows by source id went at revoke
                    logger.warning("Could not clear %s from the store: %s", book_id, exc)
            for suffix in (".db", ".db-wal", ".db-shm"):
                try:
                    os.unlink(os.path.join(fts_dir, book_id + suffix))
                except OSError:
                    pass
            progress.remove(book_id)
            done.append(book_id)
        finally:
            lock.release()
            try:
                os.unlink(lock.path)
            except OSError:
                pass
    return done


__all__ = ["CPU_SHARE", "BookLock", "auto_deeper", "book_name", "books_dir",
           "clear_stop", "deep_learn", "find_book", "meaning_stage", "meaning_work",
           "quick_learn", "quick_stage", "remove_book", "request_stop",
           "reset_checkpoint", "forget_set_aside", "set_aside", "stop_requested",
           "text_totals"]
