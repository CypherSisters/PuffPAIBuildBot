"""PuffPAI auto-indexing — watch a ZIM folder and index new packs.

Auto-indexing is the primary way a user grows PuffPAI's knowledge: drop a ``.zim``
pack into the configured folder and PuffPAI indexes it into the Zone-2 knowledge
collection the next time it starts. Manual ``puffpai knowledge sync`` is the same
work in the foreground; the CLI power tool, not the everyday path.

Packs are identified by their ZIM header UUID, not by filename, so renaming or
moving a pack does not trigger a re-index. A small JSON pack index
(``knowledge_packs.json`` under the data dir) records which UUIDs are indexed,
their pack names, and aggregate document/chunk counts for the status view.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from puffpai import fsutil
from puffpai.logging_config import get_logger

from .discovery import discover_zim_files, get_zim_metadata

logger = get_logger(__name__)

_PACK_INDEX_FILENAME = "knowledge_packs.json"

STATUS_INDEXING = "indexing"
STATUS_COMPLETE = "complete"
STATUS_ERROR = "error"
# A pack whose content has been evicted from the knowledge store. Deliberately
# not COMPLETE: if the file is ever put back, it must be indexed again rather
# than trusted as already-present, because its chunks are gone.
STATUS_REVOKED = "revoked"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_pack_index_path() -> str:
    """Resolve the pack-index JSON path from config (against the data dir)."""
    try:
        from puffpai.config import get_config
        return os.path.join(get_config().DATA_DIR, _PACK_INDEX_FILENAME)
    except Exception:
        return os.path.abspath(_PACK_INDEX_FILENAME)


class PackIndex:
    """JSON-backed record of which ZIM packs have been indexed.

    Each record is keyed by the pack's ZIM UUID (hex) and stores its filename,
    last-known path, status, timestamps, and aggregate doc/chunk counts.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = path or default_pack_index_path()
        self._packs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            try:
                # Retried read: another process may be swapping the file in.
                data = json.loads(fsutil.read_text(self._path))
                if isinstance(data, dict):
                    self._packs = {str(k): v for k, v in data.items()
                                   if isinstance(v, dict)}
            except (json.JSONDecodeError, OSError):
                self._packs = {}

    def _save(self) -> None:
        # Retried swap: on Windows a reader holding the file open would
        # otherwise make this raise and stop a learner mid-book.
        fsutil.atomic_write_json(self._path, self._packs)

    def is_indexed(self, uuid_hex: str) -> bool:
        """True once a pack has finished indexing (a stale in-progress record
        does not count, so an interrupted run is retried)."""
        rec = self._packs.get(uuid_hex)
        return bool(rec and rec.get("status") == STATUS_COMPLETE)

    def get(self, uuid_hex: str) -> dict | None:
        return self._packs.get(uuid_hex)

    def mark(self, uuid_hex: str, **fields: Any) -> None:
        """Create or update a pack record and persist."""
        with self._lock:
            rec = self._packs.get(uuid_hex, {})
            rec.update(fields)
            self._packs[uuid_hex] = rec
            self._save()

    def find_by_path(self, path: str) -> tuple[str, dict] | None:
        """The (uuid, record) for a pack last seen at ``path``, or None.

        Revocation needs this: it holds a file path and has to reach the pack
        record so a revoked pack is not left marked complete.
        """
        wanted = os.path.normcase(os.path.abspath(str(path or "")))
        if not wanted:
            return None
        with self._lock:
            items = list(self._packs.items())
        for uuid_hex, rec in items:
            recorded = str(rec.get("path") or "")
            if recorded and os.path.normcase(os.path.abspath(recorded)) == wanted:
                return uuid_hex, rec
        return None

    def find_by_book(self, book_id: str) -> tuple[str, dict] | None:
        """The (uuid, record) for a book id (see :mod:`.learn`), or None."""
        with self._lock:
            items = list(self._packs.items())
        for uuid_hex, rec in items:
            if str(rec.get("book_id") or "") == str(book_id or "") and book_id:
                return uuid_hex, rec
        return None

    def records(self) -> list[dict]:
        """All pack records, newest-indexed first."""
        return sorted(self._packs.values(),
                      key=lambda r: r.get("indexed_at", ""), reverse=True)

    @property
    def in_progress(self) -> list[dict]:
        return [r for r in self._packs.values()
                if r.get("status") == STATUS_INDEXING]

    @property
    def total_chunks(self) -> int:
        return sum(int(r.get("chunks", 0)) for r in self._packs.values())

    @property
    def total_docs(self) -> int:
        return sum(int(r.get("docs", 0)) for r in self._packs.values())


def find_new_packs(zim_dir: str, pack_index: PackIndex) -> list[dict]:
    """Return metadata for every ``.zim`` in ``zim_dir`` not already indexed.

    A pack whose header can't be read is skipped with a warning rather than
    aborting the scan.
    """
    if not zim_dir or not os.path.isdir(zim_dir):
        return []

    new_packs: list[dict] = []
    for path in discover_zim_files(zim_dirs=[zim_dir]):
        try:
            meta = get_zim_metadata(path)
        except (ValueError, OSError) as exc:
            logger.warning("Skipping unreadable ZIM %s: %s",
                           os.path.basename(path), exc)
            continue
        if not pack_index.is_indexed(meta["uuid_hex"]):
            new_packs.append(meta)
    return new_packs


def provenance_for_pack(filepath: str) -> Optional[dict]:
    """Chunk-metadata provenance for a downloaded pack, or None.

    :mod:`puffpai.source_fetch` writes an audit sidecar next to every pack it
    downloads, so the join between "where this file came from" and "what is in
    the knowledge store" needs no wiring from the wizard: the file carries its
    own paperwork. A pack the owner placed by hand has no sidecar and gets no
    provenance, which is the honest answer rather than a default.
    """
    try:
        from puffpai import source_fetch
        record = source_fetch.read_provenance(filepath)
        return source_fetch.chunk_metadata(record)
    except Exception as exc:  # noqa: BLE001 - provenance is a bonus, never a blocker
        logger.debug("No provenance for %s: %s", filepath, exc)
        return None


def index_pack(meta: dict, ingestion, pack_index: PackIndex,
               *, max_articles: int = 0,
               provenance: Optional[dict] = None,
               progress=None) -> dict:
    """Index a single ZIM pack into the knowledge collection.

    Marks the pack ``indexing`` before it starts and ``complete`` (with counts)
    when it finishes, so an interrupted run leaves a record that ``find_new_packs``
    will retry.
    """
    uuid_hex = meta["uuid_hex"]
    filename = meta["filename"]
    if provenance is None:
        provenance = provenance_for_pack(meta["filepath"])
    source_id = str((provenance or {}).get("source_id") or "")
    pack_index.mark(uuid_hex, filename=filename, path=meta["filepath"],
                    status=STATUS_INDEXING, started_at=_now(),
                    entry_count=meta.get("entry_count", 0),
                    source_id=source_id)
    logger.info("Indexing knowledge pack: %s", filename)

    def _tick(stored: int) -> None:
        # So `puffpai knowledge status` can say how far a pack has got.
        pack_index.mark(uuid_hex, articles_so_far=int(stored),
                        progress_at=_now())
        if progress is not None:
            progress(meta, stored)
    try:
        ingestion.on_progress = _tick
    except Exception:  # noqa: BLE001 - an ingester that cannot report still indexes
        pass
    try:
        # Pass provenance only when there is some. An unlabelled pack must
        # produce exactly the call this made before provenance existed, so
        # nothing about a hand-placed pack or a user upload changes shape. When
        # there IS provenance we pass it and let a TypeError surface as an index
        # error rather than quietly indexing content that could never be
        # revoked - unlabelled-and-unrevokable is the failure worth being loud
        # about.
        extra: dict[str, Any] = {}
        if provenance:
            extra["provenance"] = provenance
        docs = ingestion.ingest_zim(meta["filepath"], max_articles=max_articles,
                                    **extra)
        chunks = sum(int(getattr(d, "chunk_count", 0)) for d in docs)

        # A run that extracts nothing is not "done" -- a header-valid but empty or
        # truncated pack would otherwise be recorded complete and never retried,
        # silently leaving a corrupt download un-indexed forever. Record it as the
        # retryable error status so the next scan picks it up again.
        if not docs:
            reason = "no documents extracted -- pack may be corrupt or incomplete"
            pack_index.mark(uuid_hex, filename=filename, path=meta["filepath"],
                            status=STATUS_ERROR, error=reason, indexed_at=_now(),
                            docs=0, chunks=0)
            logger.warning("Indexed no documents from %s: %s", filename, reason)
            return {"filename": filename, "status": STATUS_ERROR, "error": reason}

        pack_index.mark(uuid_hex, filename=filename, path=meta["filepath"],
                        status=STATUS_COMPLETE, indexed_at=_now(),
                        docs=len(docs), chunks=chunks, source_id=source_id)
        logger.info("Indexed %s: %d documents, %d chunks",
                    filename, len(docs), chunks)
        return {"filename": filename, "docs": len(docs), "chunks": chunks,
                "status": STATUS_COMPLETE}
    except Exception as exc:  # noqa: BLE001 - record and continue to next pack
        logger.warning("Failed to index %s: %s", filename, exc)
        pack_index.mark(uuid_hex, filename=filename, path=meta["filepath"],
                        status=STATUS_ERROR, error=str(exc)[:200],
                        indexed_at=_now())
        return {"filename": filename, "status": STATUS_ERROR, "error": str(exc)}


def needs_provenance(record: Optional[dict]) -> bool:
    """True when a pack's chunks are not identifiable and a re-index would help.

    The test is deliberately ``complete`` **and** ``source_id``, not either one.
    ``index_pack`` stamps ``source_id`` on the record the moment it starts, so a
    run killed halfway leaves a record that carries a source id while most of the
    pack's chunks in the store still carry none. Only a record that finished is
    evidence that the labelling finished with it.
    """
    if not isinstance(record, dict):
        return True
    return not (record.get("status") == STATUS_COMPLETE
                and str(record.get("source_id") or "").strip())


def plan_reindex(zim_dir: str, pack_index: PackIndex) -> dict[str, list[dict]]:
    """Sort the packs in ``zim_dir`` into what a re-index can and cannot fix.

    Returns three lists of pack metadata:

    ``reindex``
        Packs that arrived with a provenance sidecar but whose chunks are not
        yet identifiable. Re-indexing these is the whole point of the command.
    ``unlabellable``
        Packs with no download record. There is nothing to label them *with*, so
        a re-index would change nothing and the command says so instead of
        working for an hour and reporting a fix that did not happen.
    ``already_labelled``
        Packs whose chunks already carry their source. Skipped, which is what
        makes an interrupted run resumable: every pack that finished is simply
        not in ``reindex`` the next time.
    """
    plan: dict[str, list[dict]] = {"reindex": [], "unlabellable": [],
                                   "already_labelled": []}
    if not zim_dir or not os.path.isdir(zim_dir):
        return plan

    for path in discover_zim_files(zim_dirs=[zim_dir]):
        try:
            meta = get_zim_metadata(path)
        except (ValueError, OSError) as exc:
            logger.warning("Skipping unreadable ZIM %s: %s",
                           os.path.basename(path), exc)
            continue
        provenance = provenance_for_pack(meta["filepath"])
        if not provenance:
            plan["unlabellable"].append(meta)
        elif needs_provenance(pack_index.get(meta["uuid_hex"])):
            meta = dict(meta)
            meta["provenance"] = provenance
            plan["reindex"].append(meta)
        else:
            plan["already_labelled"].append(meta)
    return plan


class _ReplacingCollection:
    """A collection handle whose ``add`` replaces ids the store already holds.

    ChromaDB's ``add`` silently ignores an id already in the collection. That is
    the right default for ordinary indexing - it is what stops a re-run
    duplicating a pack - but it is fatal for a re-index, whose entire purpose is
    to put a source label on chunks the store already has. Without this, a
    re-index would read a million articles for an hour, write nothing, and
    report a fix that did not happen.

    Only ``add`` is redirected; everything else is the real collection. A handle
    with no ``upsert`` raises, which ``index_pack`` records as an index error -
    louder, and far better, than a silent no-op.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def add(self, **kwargs: Any) -> Any:
        return self._inner.upsert(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def reindex_packs(metas: list[dict], *, rag=None,
                  pack_index: PackIndex | None = None,
                  progress=None) -> list[dict]:
    """Learn whole packs again, the two-stage way, so they carry their source.

    Each pack goes through :func:`puffpai.knowledge.learn.quick_learn` (its
    word look-up, stamped with the source, license and fetch time from its
    download record) and then :func:`puffpai.knowledge.learn.deep_learn` from
    the start, which removes the pack's old unlabelled passages and writes new
    ones carrying its book id and source. A pack is never marked complete
    until its word look-up is whole; there is no cap on how much is read.

    ``progress`` is called with each pack's metadata before its work starts, so
    a caller sitting in front of a console can say what is happening.
    """
    import sqlite3

    from . import fts_index, learn

    pack_index = pack_index or PackIndex()
    if not metas:
        return []
    results: list[dict] = []
    for meta in metas:
        if progress is not None:
            progress(meta)
        prov = meta.get("provenance")
        got = learn.quick_learn(meta, pack_index=pack_index, provenance=prov)
        if got.get("status") != STATUS_COMPLETE:
            results.append({"filename": meta["filename"],
                            "status": got.get("status") or STATUS_ERROR,
                            "error": got.get("error") or "it could not be read"})
            continue
        book_id = str(got["book_id"])
        shard = os.path.join(learn.books_dir(), f"{book_id}.db")
        labels = learn.labels_for(meta, prov)
        conn = learn.write_conn(shard)
        try:  # a look-up finished before the download record existed
            fts_index._set_meta(conn, **labels)
            conn.commit()
        finally:
            conn.close()
        learn.reset_checkpoint(shard)
        pack_index.mark(meta["uuid_hex"], meaning=learn.M_PENDING,
                        source_id=labels.get("source_id", ""))
        if rag is None:
            from .rag import DualZoneRAG
            rag = DualZoneRAG()
        deep = learn.deep_learn(book_id, pack_index=pack_index, rag=rag,
                                owner_asked=True)
        results.append({"filename": meta["filename"], "status": STATUS_COMPLETE,
                        "book_id": book_id, "docs": int(got.get("docs") or 0),
                        "chunks": int(deep.get("chunks") or 0),
                        "meaning": deep.get("status")})
    return results


def _build_ingestion(rag, *, replace: bool = False):
    """Build a KnowledgeIngestion over the RAG's two zone collections.

    ``replace=True`` is the re-index mode: writes to the knowledge zone
    overwrite chunks the store already holds instead of being skipped. See
    :class:`_ReplacingCollection` for why that has to be asked for explicitly.
    """
    from .ingestion import KnowledgeIngestion
    knowledge = rag.knowledge_collection
    return KnowledgeIngestion(
        vault_collection=rag.vault_collection,
        knowledge_collection=(_ReplacingCollection(knowledge) if replace
                              else knowledge),
    )


def needs_quick_lookup(zim_dir: str, pack_index: PackIndex) -> list[dict]:
    """Packs marked complete before books had a word look-up.

    They answer only through the store, one passage per article, so they are
    learned again the new way. Readable packs only; never raises.
    """
    if not zim_dir or not os.path.isdir(zim_dir):
        return []
    out: list[dict] = []
    for path in discover_zim_files(zim_dirs=[zim_dir]):
        try:
            meta = get_zim_metadata(path)
        except (ValueError, OSError):
            continue
        rec = pack_index.get(meta["uuid_hex"]) or {}
        if rec.get("status") == STATUS_COMPLETE and rec.get("quick") != "complete":
            out.append(meta)
    return out


def quick_work(zim_dir: str, pack_index: PackIndex) -> list[dict]:
    """Every pack in ``zim_dir`` that cannot answer questions yet."""
    seen: set = set()
    out: list[dict] = []
    for meta in (find_new_packs(zim_dir, pack_index)
                 + needs_quick_lookup(zim_dir, pack_index)):
        if meta.get("uuid_hex") not in seen:
            seen.add(meta.get("uuid_hex"))
            out.append(meta)
    return out


def sync_new_packs(zim_dir: str, *, rag=None, pack_index: PackIndex | None = None,
                   max_articles: int = 0, progress=None) -> list[dict]:
    """Foreground: make every new pack in ``zim_dir`` answerable (the quick stage).

    Returns one result per pack (see :func:`puffpai.knowledge.learn.quick_learn`).
    The deeper stage is separate: :func:`puffpai.knowledge.learn.deep_learn`.
    ``rag``, ``max_articles`` and ``progress`` are accepted for older callers;
    a book is always learned whole.
    """
    from . import learn

    pack_index = pack_index or PackIndex()
    return [learn.quick_learn(meta, pack_index=pack_index)
            for meta in quick_work(zim_dir, pack_index)]


def _import_store_modules() -> None:
    """Import the vector store and its embedder on THIS thread, before any worker.

    The background worker and the foreground chat both import
    ``chromadb.utils.embedding_functions`` on first use. Two threads importing
    the same package tree at once can each hold one module's import lock and
    wait for the other's; Python breaks that with "deadlock detected by
    _ModuleLock", which kills every background index. Importing once here,
    first, means the worker only ever finds the modules already loaded.
    """
    try:
        import chromadb  # noqa: F401
        from chromadb.utils import embedding_functions  # noqa: F401
        from puffpai import ingestion  # noqa: F401
        from . import ingestion as _knowledge_ingestion, rag  # noqa: F401
        from . import book_search, learn, progress, reading_helper  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - the worker reports its own failure
        logger.debug("store modules not preloaded: %s", exc)


def _text_book_work(zim_dir: str, pack_index: PackIndex) -> tuple[int, int]:
    """(ordered text books not fetched yet, text books needing their quick stage)."""
    try:
        from . import find_more, text_books
        orders = len(find_more.pending_orders())
        folders = len(text_books.needs_learning(zim_dir, pack_index)) if zim_dir else 0
        return orders, folders
    except Exception as exc:  # noqa: BLE001 - text books are additive
        logger.debug("text book work unknown: %s", exc)
        return 0, 0


_worker_lock = threading.Lock()
_worker: dict[str, Any] = {"thread": None, "again": False}


def start_background_index(zim_dir: str, *, rag=None,
                           pack_index: PackIndex | None = None,
                           max_articles: int = 0) -> tuple[int, Optional[threading.Thread]]:
    """Learn new books on a daemon thread: ordered text books are fetched,
    every book's quick stage runs first, then the deeper stage for each book
    allowed to run it (at most half the processor, paused while someone
    talks -- see :mod:`.learn`).

    Returns ``(book_count, thread)``. When there is nothing to do the count is
    0 and the thread is ``None`` -- the caller prints no notice and chat starts
    instantly. The thread builds its own store handle so it never shares
    ChromaDB handles with the foreground chat session. One learner runs per
    process: a call while it runs asks it to look again before it stops.
    """
    from . import learn

    given = pack_index
    pack_index = pack_index or PackIndex()
    new_packs = find_new_packs(zim_dir, pack_index)
    seen = {m.get("uuid_hex") for m in new_packs}
    new_packs += [m for m in needs_quick_lookup(zim_dir, pack_index)
                  if m.get("uuid_hex") not in seen]
    orders, folders = _text_book_work(zim_dir, pack_index)
    deeper = learn.meaning_work(pack_index)
    count = len(new_packs) + orders + folders
    if not count and not deeper:
        return 0, None
    with _worker_lock:
        running = _worker["thread"]
        if running is not None and running.is_alive():
            _worker["again"] = True
            return count, running
    _import_store_modules()

    def _one_pass() -> None:
        from . import find_more, text_books
        # A fresh read each pass: another process may have added or learned
        # a book since the last one.
        pack_index = given or PackIndex()
        if find_more.pending_orders():
            find_more.run_orders()
        folder = zim_dir or find_more.knowledge_folder()
        text_books.sync_books(folder, pack_index=pack_index)
        for meta in quick_work(zim_dir, pack_index) if zim_dir else []:
            learn.quick_learn(meta, pack_index=pack_index)
        from .rag import DualZoneRAG
        worker_rag = rag or DualZoneRAG()
        for book_id in learn.meaning_work(pack_index):
            learn.deep_learn(book_id, pack_index=pack_index, rag=worker_rag)

    def _worker_main() -> None:
        try:
            while True:
                with _worker_lock:
                    _worker["again"] = False
                try:
                    _one_pass()
                except Exception as exc:  # noqa: BLE001 - background best-effort
                    logger.warning("Background learning stopped: %s", exc)
                with _worker_lock:
                    if not _worker["again"]:
                        _worker["thread"] = None
                        return
        finally:
            with _worker_lock:
                if _worker["thread"] is threading.current_thread():
                    _worker["thread"] = None

    thread = threading.Thread(target=_worker_main, name="puffpai-auto-index",
                              daemon=True)
    with _worker_lock:
        _worker["thread"] = thread
    thread.start()
    return count, thread


__all__ = [
    "PackIndex",
    "find_new_packs",
    "index_pack",
    "needs_provenance",
    "plan_reindex",
    "provenance_for_pack",
    "reindex_packs",
    "STATUS_REVOKED",
    "sync_new_packs",
    "start_background_index",
    "default_pack_index_path",
    "STATUS_INDEXING",
    "STATUS_COMPLETE",
    "STATUS_ERROR",
]
