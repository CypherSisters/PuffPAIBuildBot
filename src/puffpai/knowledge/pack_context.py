"""The downloaded books, as chat context.

``puffpai knowledge sync`` and the background indexer put every book's
chunks in one Zone-2 collection (``puffpai_knowledge``), each stamped with
where it came from (``source_id``, ``source_label``, ``source_license``).
The chat pipeline's lookup (``api_server._get_rag_context``) only read the
collections listed in ``indexed_libraries.json`` plus uploads, so a book
downloaded and indexed for this build never reached an answer; the model
answered from memory instead. This module is the missing read.

Read-only, knowledge zone only (never the vault), and the chunk text stays
data: it is returned for the caller's quoted context block, labelled with the
book's own name so the answer can cite it.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

#: Shown when a chunk carries no label (a pack indexed before labels existed).
UNLABELLED = "Offline library"


def pack_hits(rag: Any, question: str, query_emb: Any = None, *,
              relevance: Callable[[Any, str], float],
              listed: Iterable[str] = (), n_results: int = 3) -> list[dict[str, Any]]:
    """Up to ``n_results`` chunks from the downloaded books, shaped like the
    other lookups' hits. ``[]`` when the collection is listed elsewhere (so it
    is not counted twice), empty, or unreadable."""
    collection = getattr(rag, "knowledge_collection", None)
    if collection is None:
        return []
    name = str(getattr(collection, "name", "") or "")
    if name and name in set(listed):
        return []
    try:
        if not collection.count():
            return []
        if query_emb is not None:
            found = collection.query(query_embeddings=query_emb, n_results=n_results)
        else:
            found = collection.query(query_texts=[question], n_results=n_results)
    except Exception:  # noqa: BLE001 - no store means no book context
        return []
    space = str((getattr(collection, "metadata", None) or {}).get("hnsw:space", "l2"))
    documents = (found.get("documents") or [[]])[0]
    metadatas = (found.get("metadatas") or [[]])[0] or [{}] * len(documents)
    distances = (found.get("distances") or [[]])[0] or [None] * len(documents)
    hits: list[dict[str, Any]] = []
    for text, meta, distance in zip(documents, metadatas, distances):
        meta = meta or {}
        hits.append({
            "text": text,
            "library": str(meta.get("source_label") or UNLABELLED),
            "collection": name,
            "distance": distance,
            "relevance": relevance(distance, space),
            "title": str(meta.get("title") or ""),
            "url": "",
            "is_user_upload": False,
            "source_id": str(meta.get("source_id") or ""),
            "source_license": str(meta.get("source_license") or ""),
            "book_id": str(meta.get("book_id") or ""),
        })
    return hits


__all__ = ["UNLABELLED", "pack_hits"]
