"""Knowledge packs index in batches, report progress, and never race an import.

A 10 MB pack sat for 8m49s and was killed with 0
chunks stored, and every chat run logged "Background indexing stopped:
deadlock detected by _ModuleLock('chromadb.utils.embedding_functions')".

* One store write per article made that pack take about 23 minutes with no
  word on screen. Chunks now go to the store in batches (measured 1m40s for
  the same pack) and ``knowledge sync`` says how far it has got.
* The background worker and the chat both imported the embedder on first
  use, on two threads at once. The store modules are now imported on the
  calling thread before the worker starts.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import sys

from puffpai.knowledge import auto_index
from puffpai.knowledge.ingestion import KnowledgeIngestion, TargetZone


class Collection:
    def __init__(self):
        self.calls = []

    def add(self, ids, documents, metadatas):
        assert len(ids) == len(set(ids)), "one write must not repeat an id"
        self.calls.append(list(ids))


def test_batched_chunks_are_written_together_without_repeats():
    store = Collection()
    ki = KnowledgeIngestion(vault_collection=None, knowledge_collection=store)
    ki._batch = []
    for text in ("alpha " * 30, "beta " * 30, "alpha " * 30):
        ki.ingest_text(text, title="t", force_zone=TargetZone.KNOWLEDGE)
    assert store.calls == []
    ki._flush_batch()
    assert len(store.calls) == 1 and len(store.calls[0]) == 2


def test_a_full_batch_is_written_straight_away(monkeypatch):
    store = Collection()
    ki = KnowledgeIngestion(vault_collection=None, knowledge_collection=store)
    monkeypatch.setattr(KnowledgeIngestion, "ZIM_BATCH_CHUNKS", 2)
    ki._batch = []
    for n in range(5):
        ki.ingest_text(f"article number {n} " * 10, title="t",
                       force_zone=TargetZone.KNOWLEDGE)
    assert [len(c) for c in store.calls] == [2, 2]


def test_outside_a_pack_every_text_is_written_at_once():
    store = Collection()
    ki = KnowledgeIngestion(vault_collection=None, knowledge_collection=store)
    ki.ingest_text("one note " * 20, title="t", force_zone=TargetZone.KNOWLEDGE)
    assert len(store.calls) == 1


def test_index_pack_records_progress(tmp_path):
    index = auto_index.PackIndex(path=str(tmp_path / "packs.json"))
    seen = []

    class Ingestion:
        on_progress = None

        def ingest_zim(self, path, max_articles=0):
            self.on_progress(250)

            class Doc:
                chunk_count = 1
            return [Doc()]

    meta = {"uuid_hex": "u1", "filename": "small.zim", "filepath": "small.zim",
            "entry_count": 400}
    out = auto_index.index_pack(meta, Ingestion(), index,
                                progress=lambda m, n: seen.append(n))
    assert out["status"] == auto_index.STATUS_COMPLETE
    assert seen == [250]
    assert index.get("u1")["articles_so_far"] == 250


def test_store_modules_are_imported_before_the_worker_starts(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(auto_index, "find_new_packs",
                        lambda zim_dir, pack_index: [{"uuid_hex": "u"}])
    monkeypatch.setattr(auto_index, "_import_store_modules",
                        lambda: order.append("imported"))

    class Thread:
        def __init__(self, target, name, daemon):
            pass

        def start(self):
            order.append("started")
    monkeypatch.setattr(auto_index.threading, "Thread", Thread)
    count, _thread = auto_index.start_background_index(
        str(tmp_path), pack_index=auto_index.PackIndex(str(tmp_path / "p.json")))
    assert count == 1 and order == ["imported", "started"]


def test_the_real_import_loads_the_embedder_module():
    auto_index._import_store_modules()
    assert "chromadb.utils.embedding_functions" in sys.modules
