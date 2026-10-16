"""Shared stand-ins for the book-learning tests (no network, no real model)."""
from __future__ import annotations

import os
import sqlite3

from puffpai.knowledge.fts_index import SCHEMA_VERSION, FTSBuilder, _set_meta

#: Words a person may never read about learning: the one list in
#: puffpai.plain_words, plus the learning-specific jargon.
#: Commands are text too.
LEARNING_EXTRA = ("embedding", "vector", "chunk", "fts", "onnx", "minilm",
                  "chroma")


def jargon_in(text: str) -> list[str]:
    from puffpai import plain_words
    return plain_words.check(str(text or ""), also=LEARNING_EXTRA)


def make_book(fts_dir: str, book_id: str, articles: list[tuple[str, str]], *,
              labels: dict | None = None) -> str:
    """A finished word look-up for a book of (title, body) articles."""
    os.makedirs(fts_dir, exist_ok=True)
    path = os.path.join(fts_dir, f"{book_id}.db")
    conn = sqlite3.connect(path)
    try:
        FTSBuilder._ensure_schema(conn)
        count = FTSBuilder._index_records(
            conn, [(f"A/{t.replace(' ', '_')}", t, b) for t, b in articles])
        _set_meta(conn, schema_version=SCHEMA_VERSION, status="complete",
                  zim_filename=f"{book_id}.zim", zim_size=1, zim_mtime=1,
                  article_count=count, **(labels or {}))
        conn.commit()
    finally:
        conn.close()
    return path


class FakeCollection:
    """Enough of a Chroma collection for the learner: upsert/get/delete/count/query."""

    name = "puffpai_knowledge"
    metadata = {"hnsw:space": "cosine"}

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.upserts = 0

    def upsert(self, ids, documents, metadatas, embeddings):
        assert len(ids) == len(documents) == len(metadatas) == len(embeddings)
        self.upserts += 1
        for i, d, m, e in zip(ids, documents, metadatas, embeddings):
            self.rows[i] = {"doc": d, "meta": dict(m), "emb": e}

    def count(self):
        return len(self.rows)

    def _match(self, meta, where):
        return all(meta.get(k) == v for k, v in (where or {}).items())

    def get(self, where=None, include=None, limit=None, offset=0, **_kw):
        ids = [i for i, r in self.rows.items() if self._match(r["meta"], where)]
        ids = ids[offset:offset + limit] if limit else ids[offset:]
        return {"ids": ids, "metadatas": [self.rows[i]["meta"] for i in ids],
                "documents": [self.rows[i]["doc"] for i in ids]}

    def delete(self, where=None, ids=None):
        doomed = list(ids or [])
        if where is not None:
            doomed += [i for i, r in self.rows.items() if self._match(r["meta"], where)]
        for i in doomed:
            self.rows.pop(i, None)

    def query(self, query_embeddings=None, query_texts=None, n_results=3):
        found = list(self.rows.values())[:n_results]
        return {"documents": [[r["doc"] for r in found]],
                "metadatas": [[r["meta"] for r in found]],
                "distances": [[0.2 for _ in found]]}


class FakeRag:
    def __init__(self, coll=None):
        self.knowledge_collection = coll or FakeCollection()


def fake_embedder(texts):
    return [[float(len(t) % 13), 1.0, 0.5] for t in texts]


ARTICLES = [
    ("Tyrannosaurus", "Tyrannosaurus rex was a huge meat-eating dinosaur. " * 40),
    ("Volcano", "A volcano erupts when magma rises through the crust. " * 30),
    ("Reptile", "Reptiles such as lizards bask in the sun to keep warm. " * 25),
    ("Short", "Too short."),
] + [(f"Filler {n}", f"Filler article number {n} talks about rocks and rivers. " * 12)
     for n in range(30)]
