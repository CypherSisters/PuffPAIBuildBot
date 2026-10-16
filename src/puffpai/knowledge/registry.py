"""
PuffPAI Document Registry

JSON-backed metadata tracker for ingested documents. ChromaDB stores
chunks but has no concept of "documents" - this registry provides
document-level metadata (title, source, zone, date, chunk count) for
the Knowledge Manager UI panel.

Deleting a document is the part worth reading. A registry that drops the
record and leaves the text in the vector store has not deleted anything; it
has thrown away the only handle on content that keeps answering questions.
So :meth:`DocumentRegistry.delete_document` takes a collection handle, evicts
the chunks first, and drops the record only once the store has confirmed there
is nothing identifiable left. If the store cannot be reached, nothing changes -
a delete that orphans its own content is worse than a loud failure.

Usage:
    registry = DocumentRegistry("path/to/registry.json")
    registry.register(doc)          # after KnowledgeIngestion.ingest_*()
    docs = registry.list_documents()
    results = registry.search("python")
    preview = registry.deletion_preview("doc_abc123", collection)
    result = registry.delete_document("doc_abc123", collection)

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

# How many candidate chunk ids to ask the store about in one call.
_ID_PROBE_BATCH = 64
# A ceiling, so a malformed chunk_count can never turn one delete into a scan
# of the whole store. No single ingested document produces this many chunks.
_ID_PROBE_MAX = 100_000

# What clears chunks this build cannot identify. There is no command that
# removes them selectively - naming one would be the exact bug this module
# exists to fix - so the door named here is the one that actually works.
REBUILD_DOOR = (
    "Nothing in this build can remove them one at a time. What clears them is "
    "deleting the knowledge store directory (chromadb_path in your config) and "
    "indexing your documents again from scratch."
)


# ── chunk identification ─────────────────────────────────────────────────────
#
# KnowledgeIngestion writes each chunk under the id f"{doc_id}_{index}",
# counting from zero, and does not stamp doc_id into chunk metadata. The id is
# therefore the only thing tying a chunk to its document - which is lucky,
# because it is also the one label every document this build ever indexed
# already carries. Enumerating those ids works on documents indexed long before
# this code existed; a metadata filter alone would not.

def _collection_ids(collection: Any, **kwargs: Any) -> Optional[list[str]]:
    """Ids from one ``collection.get``, or None if the store would not answer.

    None and an empty list mean opposite things - "could not ask" versus "asked,
    and there are none" - and a delete that confuses them is how content ends up
    orphaned. ``include=[]`` asks for ids only, which is far cheaper on a large
    collection, but not every ChromaDB version accepts it; the plain call is the
    fallback, and only both failing counts as unreachable.
    """
    if collection is None:
        return None
    last_error: Optional[BaseException] = None
    call_shapes: tuple[dict[str, Any], ...] = ({"include": []}, {})
    for extra in call_shapes:
        try:
            got = collection.get(**kwargs, **extra)
        except Exception as exc:  # noqa: BLE001 - try the next call shape
            last_error = exc
            continue
        ids = (got or {}).get("ids") or []
        # get() returns a flat id list; a nested one would have come from query().
        if ids and isinstance(ids[0], list):
            ids = [i for group in ids for i in group]
        return [str(i) for i in ids]
    logger.warning("Knowledge store would not answer for %s: %s", kwargs, last_error)
    return None


def document_chunk_ids(collection: Any, doc_id: str,
                       expected_chunks: Optional[int] = None, *,
                       metadata_key: str = "doc_id",
                       id_template: str = "{doc}_{i}") -> Optional[list[str]]:
    """Ids of every chunk this build can prove belongs to ``doc_id``.

    Two passes, unioned. A metadata filter on ``metadata_key`` catches every
    chunk whose writer labelled it, and is skipped silently if the store rejects
    the filter. The id probe walks ``doc_id_0``, ``doc_id_1``, ... asking the
    store which of them exist, and is the pass that works on documents indexed
    before any labelling existed.

    ``metadata_key`` and ``id_template`` exist because the two write paths in
    this build name chunks differently - Zone-1/2 ingestion writes
    ``{doc}_{i}``, the uploads collection writes ``{doc}_chunk_{i}`` under a
    ``file_id`` label - and one eviction implementation is worth more than two.

    Returns None if the store would not answer the id probe at all - the caller
    must treat that as "unreachable", never as "nothing to delete".
    """
    doc = str(doc_id or "").strip()
    if not doc or collection is None:
        return None

    found: list[str] = []
    seen: set[str] = set()

    labelled = _collection_ids(collection, where={metadata_key: doc})
    for cid in labelled or []:
        if cid not in seen:
            seen.add(cid)
            found.append(cid)

    # Probe until a whole batch comes back empty AND we are past whatever the
    # registry says the chunk count was. A sparse run (a chunk deleted by hand)
    # must not stop the walk early.
    floor = max(int(expected_chunks or 0), _ID_PROBE_BATCH)
    start = 0
    while start < _ID_PROBE_MAX:
        batch = [id_template.format(doc=doc, i=i)
                 for i in range(start, start + _ID_PROBE_BATCH)]
        got = _collection_ids(collection, ids=batch)
        if got is None:
            return None
        for cid in got:
            if cid not in seen:
                seen.add(cid)
                found.append(cid)
        start += _ID_PROBE_BATCH
        if not got and start >= floor:
            break
    return found


def evict_document(collection: Any, doc_id: str,
                   expected_chunks: Optional[int] = None, *,
                   metadata_key: str = "doc_id",
                   id_template: str = "{doc}_{i}") -> dict[str, Any]:
    """Delete every identifiable chunk of ``doc_id`` from a collection.

    Reports what it found, what it removed, what is still there, and how many
    chunks the registry expected but no id could be matched to. ``ok`` is True
    only when the store was reachable, the re-count afterwards shows none left,
    and nothing was left unidentified - because "mostly deleted" is exactly the
    outcome this whole exercise exists to stop being called a delete.
    """
    doc = str(doc_id or "").strip()
    expected = max(int(expected_chunks or 0), 0)
    if not doc:
        return {"ok": False, "found": None, "evicted": 0, "remaining": None,
                "unidentified": expected,
                "reason": "No document id was given, so nothing could be removed."}

    before = document_chunk_ids(collection, doc, expected,
                                metadata_key=metadata_key,
                                id_template=id_template)
    if before is None:
        return {"ok": False, "found": None, "evicted": 0, "remaining": None,
                "unidentified": expected,
                "reason": (
                    "The knowledge store could not be reached, so nothing was "
                    "removed. The text is still there and still answering."
                )}

    unidentified = max(expected - len(before), 0)

    if not before:
        if expected == 0:
            return {"ok": True, "found": 0, "evicted": 0, "remaining": 0,
                    "unidentified": 0,
                    "reason": "Nothing from this document was in the knowledge store."}
        return {"ok": False, "found": 0, "evicted": 0, "remaining": 0,
                "unidentified": expected,
                "reason": (
                    f"The record says {expected} chunks were indexed, but none of "
                    f"them carry an id this build can match to this document. They "
                    f"are still in the knowledge store and still answering. "
                    f"{REBUILD_DOOR}"
                )}

    try:
        collection.delete(ids=before)
    except Exception as exc:  # noqa: BLE001 - the store's refusal is the report
        return {"ok": False, "found": len(before), "evicted": 0,
                "remaining": len(before), "unidentified": unidentified,
                "reason": f"The knowledge store refused the delete: {exc}"}

    after = document_chunk_ids(collection, doc, expected,
                               metadata_key=metadata_key,
                               id_template=id_template)
    if after is None:
        return {"ok": False, "found": len(before), "evicted": None,
                "remaining": None, "unidentified": unidentified,
                "reason": (
                    "The delete was sent but the store would not confirm what is "
                    "left, so this cannot be called a completed delete."
                )}

    evicted = len(before) - len(after)
    if after:
        return {"ok": False, "found": len(before), "evicted": evicted,
                "remaining": len(after), "unidentified": unidentified,
                "reason": (
                    f"Removed {evicted} of {len(before)} chunks; {len(after)} are "
                    f"still in the knowledge store and still answering."
                )}
    if unidentified:
        return {"ok": False, "found": len(before), "evicted": evicted,
                "remaining": 0, "unidentified": unidentified,
                "reason": (
                    f"Removed {evicted} chunks. {unidentified} more were indexed "
                    f"from this document before chunks carried ids this build can "
                    f"match; they are still in the knowledge store and still "
                    f"answering. {REBUILD_DOOR}"
                )}
    return {"ok": True, "found": len(before), "evicted": evicted, "remaining": 0,
            "unidentified": 0,
            "reason": f"Removed {evicted} chunks from the knowledge store."}


class DocumentRegistry:
    """Track ingested documents with JSON file persistence.

    Each document entry stores:
      - doc_id: unique document identifier (from content hash)
      - title: display title
      - source: how it was added (e.g. document_upload, zim_pack)
      - zone: which zone it was stored in (vault or knowledge)
      - ingested_at: ISO timestamp
      - chunk_count: number of chunks created
      - file_origin: original file path (if applicable)
      - has_pii: whether PII was detected
    """

    def __init__(self, registry_path: str):
        """Initialize with path to the JSON registry file.

        Args:
            registry_path: Absolute or relative path to the registry JSON.
                Created automatically if it doesn't exist.
        """
        self._path = registry_path
        self._documents: list[dict] = []
        self._load()

    def _load(self) -> None:
        """Load the registry from disk."""
        if os.path.exists(self._path):
            try:
                with open(self._path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self._documents = data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                self._documents = []
        else:
            self._documents = []

    def _save(self) -> None:
        """Persist the registry to disk."""
        os.makedirs(os.path.dirname(self._path) or '.', exist_ok=True)
        with open(self._path, 'w', encoding='utf-8') as f:
            json.dump(self._documents, f, indent=2, ensure_ascii=False)

    def register(self, doc) -> dict:
        """Register an ingested document.

        Args:
            doc: An IngestedDocument dataclass from the ingestion pipeline.

        Returns:
            The registered document dict.
        """
        entry = {
            'doc_id': doc.doc_id,
            'title': doc.title,
            'source': doc.source.value if hasattr(doc.source, 'value') else str(doc.source),
            'zone': doc.target_zone.value if hasattr(doc.target_zone, 'value') else str(doc.target_zone),
            'ingested_at': doc.ingested_at,
            'chunk_count': doc.chunk_count,
            'file_origin': doc.file_origin,
            'has_pii': doc.has_pii,
        }

        # Replace if same doc_id exists (re-upload)
        self._documents = [d for d in self._documents if d['doc_id'] != doc.doc_id]
        self._documents.append(entry)
        self._save()
        return entry

    def list_documents(self, zone: str | None = None) -> list[dict]:
        """List all registered documents, optionally filtered by zone.

        Args:
            zone: Filter by zone value (e.g. "puffpai_vault", "puffpai_knowledge").

        Returns:
            List of document dicts, newest first.
        """
        docs = self._documents
        if zone:
            docs = [d for d in docs if d.get('zone') == zone]
        return sorted(docs, key=lambda d: d.get('ingested_at', ''), reverse=True)

    def search(self, query: str) -> list[dict]:
        """Search documents by title or file origin.

        Args:
            query: Case-insensitive search string.

        Returns:
            Matching document dicts, newest first.
        """
        q = query.lower()
        results = []
        for doc in self._documents:
            title = (doc.get('title') or '').lower()
            origin = (doc.get('file_origin') or '').lower()
            if q in title or q in origin:
                results.append(doc)
        return sorted(results, key=lambda d: d.get('ingested_at', ''), reverse=True)

    def get(self, doc_id: str) -> dict | None:
        """Get a document by ID.

        Args:
            doc_id: The document ID.

        Returns:
            Document dict, or None if not found.
        """
        for doc in self._documents:
            if doc['doc_id'] == doc_id:
                return doc
        return None

    # ── deletion ────────────────────────────────────────────────────────────

    def deletion_preview(self, doc_id: str,
                         collection: Any = None) -> dict[str, Any]:
        """What a delete would really do, worked out before anyone confirms it.

        The owner is entitled to know whether the text will stop answering
        questions BEFORE they press the button, not to find out afterwards.
        Returns ``message`` - one paragraph fit to put in a confirmation box -
        alongside the counts it was built from.
        """
        entry = self.get(doc_id)
        if entry is None:
            return {"ok": False, "doc_id": doc_id, "title": "", "reachable": None,
                    "identified": 0, "unidentified": 0, "expected": 0,
                    "message": "No document with that id is registered."}

        title = str(entry.get("title") or "Untitled")
        expected = max(int(entry.get("chunk_count") or 0), 0)
        ids = document_chunk_ids(collection, doc_id, expected)

        if ids is None:
            return {"ok": False, "doc_id": doc_id, "title": title,
                    "reachable": False, "identified": 0,
                    "unidentified": expected, "expected": expected,
                    "message": (
                        f"The knowledge store cannot be reached, so '{title}' "
                        f"cannot be deleted right now. Its record and its text "
                        f"both stay exactly as they are. Nothing will change if "
                        f"you continue."
                    )}

        identified = len(ids)
        unidentified = max(expected - identified, 0)
        if unidentified:
            return {"ok": False, "doc_id": doc_id, "title": title,
                    "reachable": True, "identified": identified,
                    "unidentified": unidentified, "expected": expected,
                    "message": (
                        f"Delete '{title}'?\n\n"
                        f"This removes the record and {identified} of its "
                        f"{expected} chunks from the knowledge store. The other "
                        f"{unidentified} were indexed before chunks carried ids "
                        f"this build can match, so they stay in the store and "
                        f"keep answering questions. {REBUILD_DOOR}\n\n"
                        f"This cannot be undone."
                    )}
        if identified == 0:
            return {"ok": True, "doc_id": doc_id, "title": title,
                    "reachable": True, "identified": 0, "unidentified": 0,
                    "expected": expected,
                    "message": (
                        f"Delete '{title}'?\n\n"
                        f"Its text is not in the knowledge store, so this removes "
                        f"the record only. This cannot be undone."
                    )}
        return {"ok": True, "doc_id": doc_id, "title": title, "reachable": True,
                "identified": identified, "unidentified": 0, "expected": expected,
                "message": (
                    f"Delete '{title}'?\n\n"
                    f"This removes the record and all {identified} of its chunks "
                    f"from the knowledge store. Its text stops answering "
                    f"questions. This cannot be undone."
                )}

    def delete_document(self, doc_id: str,
                        collection: Any = None) -> dict[str, Any]:
        """Remove a document's chunks from the store, then its registry row.

        In that order, and the second step only happens when the store has
        confirmed the first one left nothing identifiable behind. A record whose
        content is still in the store is at least a record; content in the store
        with no record is unfindable text answering questions forever.

        Returns a dict with ``ok``, ``registry_removed``, ``chunks_evicted``,
        ``chunks_remaining``, ``unidentified``, ``reason`` and ``limits``.
        ``ok`` is True only when the row and every chunk went together.
        """
        entry = self.get(doc_id)
        if entry is None:
            return {"ok": False, "doc_id": doc_id, "title": "",
                    "registry_removed": False, "chunks_evicted": 0,
                    "chunks_remaining": None, "unidentified": 0,
                    "reason": "No document with that id is registered.",
                    "limits": []}

        title = str(entry.get("title") or "Untitled")
        expected = max(int(entry.get("chunk_count") or 0), 0)

        if collection is None:
            return {"ok": False, "doc_id": doc_id, "title": title,
                    "registry_removed": False, "chunks_evicted": 0,
                    "chunks_remaining": None, "unidentified": expected,
                    "reason": (
                        "No knowledge store was available, so nothing was deleted. "
                        "Dropping the record on its own would leave this text in "
                        "the store with no way to find it again. Nothing changed."
                    ),
                    "limits": []}

        eviction = evict_document(collection, doc_id, expected)
        limits: list[str] = []
        registry_removed = False

        # Anything of this document still in the store - matchable or not -
        # means the record has to stay. It is the only handle on that text.
        still_there = (eviction["remaining"] or 0) + (eviction["unidentified"] or 0)

        if still_there == 0:
            self._documents = [d for d in self._documents
                               if d['doc_id'] != doc_id]
            self._save()
            registry_removed = True
        else:
            # Correct the record to what is actually left. Without this the
            # entry keeps claiming its original chunk_count, so a second attempt
            # measures itself against chunks the first attempt already removed,
            # reports them as unidentifiable, and refuses to call itself done -
            # a delete that can never succeed no matter how often it is retried.
            if still_there != expected:
                entry["chunk_count"] = still_there
                self._save()
            limits.append(
                "The record was kept, because its text is still in the knowledge "
                "store and the record is the only way to find it again."
            )

        if not eviction["ok"]:
            limits.insert(0, str(eviction["reason"]))

        ok = bool(eviction["ok"]) and registry_removed
        if ok:
            reason = (
                f"Deleted '{title}'. The record is gone and "
                f"{eviction['evicted']} chunks were removed from the knowledge "
                f"store."
            )
        else:
            reason = f"'{title}' was not fully deleted."
        return {"ok": ok, "doc_id": doc_id, "title": title,
                "registry_removed": registry_removed,
                "chunks_evicted": eviction["evicted"],
                "chunks_remaining": eviction["remaining"],
                "unidentified": eviction["unidentified"],
                "reason": reason, "limits": limits}

    def delete(self, doc_id: str, collection: Any = None) -> bool:
        """Delete a document - registry row and chunks together.

        Thin boolean wrapper over :meth:`delete_document`. True means the row
        AND every chunk of it are gone; anything less is False, including a
        partial eviction, because a half-deleted document is still answering
        questions. Callers that need to say what happened should use
        :meth:`delete_document` and read its ``limits``.

        Args:
            doc_id: The document ID to remove.
            collection: The ChromaDB collection holding this document's chunks.
                Without it there is no way to remove the text, so nothing is
                removed at all and this returns False.
        """
        return bool(self.delete_document(doc_id, collection)["ok"])

    @property
    def count(self) -> int:
        """Total number of registered documents."""
        return len(self._documents)

    @property
    def total_chunks(self) -> int:
        """Sum of chunk_count across all documents."""
        return sum(d.get('chunk_count', 0) for d in self._documents)
