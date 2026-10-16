"""Deleting a document has to delete the document.

The bug these tests exist to keep dead: the Knowledge Manager dropped the
registry row and left every chunk in ChromaDB, while the confirmation box said
the chunks would go away at the next re-index - a re-index that only labels
chunks and has never evicted one. So a removed medical letter kept answering
questions for the life of the install, and the screen said it was gone.

The knowledge store here is a real ChromaDB collection with an offline
embedder, not a stand-in, because the parts under test - get-by-id, get-by-
metadata and delete-by-id - are exactly what a hand-written double would get
politely wrong. The doubles that DO appear are for failure modes a real store
will not perform on cue: a store that will not answer, and a store whose delete
half-works.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

import json

import pytest

from puffpai.knowledge.ingestion import (
    ContentSource,
    KnowledgeIngestion,
    TargetZone,
)
from puffpai.knowledge.rag import DualZoneRAG
from puffpai.knowledge.registry import (
    DocumentRegistry,
    document_chunk_ids,
    evict_document,
)

SECRET = "The biopsy result was negative and the follow up is in October."


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _collections(tmp_path):
    import chromadb
    from tests.test_knowledge import _HashEmbed

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    vault = client.get_or_create_collection(
        name="puffpai_vault", embedding_function=_HashEmbed())
    knowledge = client.get_or_create_collection(
        name="puffpai_knowledge", embedding_function=_HashEmbed())
    return vault, knowledge


def _ingest_and_register(tmp_path, knowledge, text, title="Letter"):
    """Index a document into Zone 2 and register it, as the UI path does."""
    ingestion = KnowledgeIngestion(knowledge_collection=knowledge)
    doc = ingestion.ingest_text(
        text, title=title, source=ContentSource.DOCUMENT_UPLOAD,
        force_zone=TargetZone.KNOWLEDGE)
    registry = DocumentRegistry(str(tmp_path / "registry.json"))
    registry.register(doc)
    return registry, doc


class _MuteCollection:
    """A store that will not answer. Not "answers empty" - will not answer."""

    def get(self, **kwargs):
        raise RuntimeError("knowledge store unreachable")

    def delete(self, **kwargs):  # pragma: no cover - must never be reached
        raise AssertionError("nothing may be deleted from an unreachable store")


class _HalfDeletingCollection:
    """Wraps a real collection but drops only the first half of any delete."""

    def __init__(self, inner):
        self._inner = inner

    def get(self, **kwargs):
        return self._inner.get(**kwargs)

    def delete(self, ids=None, **kwargs):
        keep = list(ids or [])
        self._inner.delete(ids=keep[: max(len(keep) // 2, 0)])


class _RefusingDeleteCollection:
    def __init__(self, inner):
        self._inner = inner

    def get(self, **kwargs):
        return self._inner.get(**kwargs)

    def delete(self, **kwargs):
        raise RuntimeError("read-only store")


# ---------------------------------------------------------------------------
# 1. the text stops answering
# ---------------------------------------------------------------------------

def test_deleted_document_no_longer_answers_questions(tmp_path):
    """The whole point. Query returns it, delete it, query does not."""
    vault, knowledge = _collections(tmp_path)
    registry, doc = _ingest_and_register(tmp_path, knowledge, SECRET)

    rag = DualZoneRAG(vault_collection=vault, knowledge_collection=knowledge)
    before = rag.query("biopsy result follow up", n_results=5)
    assert any("biopsy" in r.text for r in before.results), \
        "the document should be answering before it is deleted"

    result = registry.delete_document(doc.doc_id, knowledge)
    assert result["ok"] is True

    after = rag.query("biopsy result follow up", n_results=5)
    assert not any("biopsy" in r.text for r in after.results), \
        "a deleted document must not keep answering questions"
    assert knowledge.count() == 0


def test_row_and_chunks_go_together(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    registry, doc = _ingest_and_register(
        tmp_path, knowledge, "\n\n".join(f"Paragraph {i}. " * 40
                                         for i in range(10)))
    assert doc.chunk_count > 1
    assert len(document_chunk_ids(knowledge, doc.doc_id)) == doc.chunk_count

    result = registry.delete_document(doc.doc_id, knowledge)

    assert result["ok"] is True
    assert result["registry_removed"] is True
    assert result["chunks_evicted"] == doc.chunk_count
    assert result["chunks_remaining"] == 0
    assert registry.get(doc.doc_id) is None
    assert document_chunk_ids(knowledge, doc.doc_id) == []
    # And it survives a reload - the row is gone from disk, not just from RAM.
    assert DocumentRegistry(str(tmp_path / "registry.json")).count == 0


# ---------------------------------------------------------------------------
# 2. a delete that did not happen is never reported as one
# ---------------------------------------------------------------------------

def test_partial_eviction_is_not_success_and_keeps_the_row(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    registry, doc = _ingest_and_register(
        tmp_path, knowledge, "\n\n".join(f"Paragraph {i}. " * 40
                                         for i in range(10)))
    assert doc.chunk_count >= 4

    result = registry.delete_document(
        doc.doc_id, _HalfDeletingCollection(knowledge))

    assert result["ok"] is False
    assert result["chunks_remaining"] > 0
    assert "still in the knowledge store" in result["reason"] + \
        " ".join(result["limits"])
    # The row stays: it is the only handle left on text that is still in there.
    assert result["registry_removed"] is False
    assert registry.get(doc.doc_id) is not None


def test_a_store_that_refuses_the_delete_changes_nothing(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    registry, doc = _ingest_and_register(tmp_path, knowledge, SECRET)

    result = registry.delete_document(
        doc.doc_id, _RefusingDeleteCollection(knowledge))

    assert result["ok"] is False
    assert result["chunks_evicted"] == 0
    assert registry.get(doc.doc_id) is not None
    assert len(document_chunk_ids(knowledge, doc.doc_id)) == doc.chunk_count


def test_unreachable_store_leaves_both_the_row_and_the_chunks(tmp_path):
    """The orphan case. Dropping the row here would leave the text in the
    store with nothing left that names it."""
    _vault, knowledge = _collections(tmp_path)
    registry, doc = _ingest_and_register(tmp_path, knowledge, SECRET)

    result = registry.delete_document(doc.doc_id, _MuteCollection())

    assert result["ok"] is False
    assert result["registry_removed"] is False
    assert result["chunks_remaining"] is None
    assert "could not be reached" in result["reason"] + \
        " ".join(result["limits"])
    assert registry.get(doc.doc_id) is not None
    assert len(document_chunk_ids(knowledge, doc.doc_id)) == doc.chunk_count
    # And on disk, too.
    assert DocumentRegistry(str(tmp_path / "registry.json")).count == 1


def test_no_collection_at_all_deletes_nothing(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    registry, doc = _ingest_and_register(tmp_path, knowledge, SECRET)

    assert registry.delete(doc.doc_id) is False
    assert registry.get(doc.doc_id) is not None
    assert knowledge.count() == doc.chunk_count


def test_bool_delete_is_false_for_anything_less_than_gone(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    registry, doc = _ingest_and_register(
        tmp_path, knowledge, "\n\n".join(f"Paragraph {i}. " * 40
                                         for i in range(10)))
    assert registry.delete(doc.doc_id, _HalfDeletingCollection(knowledge)) is False
    assert registry.delete(doc.doc_id, knowledge) is True


# ---------------------------------------------------------------------------
# 3. chunks this build cannot identify are counted, not quietly left
# ---------------------------------------------------------------------------

def test_chunks_written_before_matchable_ids_are_counted_and_named(tmp_path):
    """A document indexed by an older writer: its chunks carry neither the
    doc_id label nor the {doc_id}_{n} ids, so nothing can pick them out."""
    _vault, knowledge = _collections(tmp_path)
    knowledge.add(
        ids=["legacy-chunk-a", "legacy-chunk-b"],
        documents=[SECRET, "The second half of the same old letter."],
        metadatas=[{"title": "Old letter"}, {"title": "Old letter"}],
    )

    registry = DocumentRegistry(str(tmp_path / "registry.json"))
    registry._documents = [{
        "doc_id": "doc_oldletter", "title": "Old letter",
        "source": "document_upload", "zone": "puffpai_knowledge",
        "ingested_at": "2025-01-01T00:00:00", "chunk_count": 2,
        "file_origin": None, "has_pii": False,
    }]
    registry._save()

    preview = registry.deletion_preview("doc_oldletter", knowledge)
    assert preview["ok"] is False
    assert preview["identified"] == 0
    assert preview["unidentified"] == 2
    assert "keep answering questions" in preview["message"]
    assert "indexing your documents again from scratch" in preview["message"]

    result = registry.delete_document("doc_oldletter", knowledge)
    assert result["ok"] is False
    assert result["unidentified"] == 2
    assert knowledge.count() == 2, "unidentifiable chunks are still there"
    joined = result["reason"] + " ".join(result["limits"])
    assert "still answering" in joined


def test_a_partly_identifiable_document_reports_the_shortfall(tmp_path):
    """Half the chunks match the id scheme, half do not. The delete removes
    what it can and says exactly what it could not."""
    _vault, knowledge = _collections(tmp_path)
    registry, doc = _ingest_and_register(tmp_path, knowledge, SECRET)
    entry = registry.get(doc.doc_id)
    # The record remembers three chunks; only the real ones are matchable.
    entry["chunk_count"] = doc.chunk_count + 2
    registry._save()

    result = registry.delete_document(doc.doc_id, knowledge)

    assert result["ok"] is False
    assert result["chunks_evicted"] == doc.chunk_count
    assert result["unidentified"] == 2
    assert "2 more were indexed" in " ".join(
        [result["reason"]] + result["limits"])


# ---------------------------------------------------------------------------
# 4. the confirmation says what will really happen
# ---------------------------------------------------------------------------

def test_preview_promises_removal_only_when_removal_is_possible(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    registry, doc = _ingest_and_register(tmp_path, knowledge, SECRET)

    preview = registry.deletion_preview(doc.doc_id, knowledge)

    assert preview["ok"] is True
    assert preview["identified"] == doc.chunk_count
    assert preview["unidentified"] == 0
    assert "stops answering" in preview["message"]
    assert "cannot be undone" in preview["message"]
    # The retired lie must not come back in any form.
    assert "re-index" not in preview["message"].lower()
    assert "remain in chromadb" not in preview["message"].lower()


def test_preview_refuses_when_the_store_is_unreachable(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    registry, doc = _ingest_and_register(tmp_path, knowledge, SECRET)

    preview = registry.deletion_preview(doc.doc_id, _MuteCollection())

    assert preview["ok"] is False
    assert preview["reachable"] is False
    assert "cannot be reached" in preview["message"]
    assert "Nothing will change" in preview["message"]


def test_the_old_reindex_promise_is_gone_from_the_ui(tmp_path):
    """The sentence that pointed at a remedy which does not exist."""
    pytest.importorskip("tkinter")
    import inspect

    from puffpai.ui import knowledge_view

    src = inspect.getsource(knowledge_view)
    assert "remain in ChromaDB until the next re-index" not in src


def test_preview_message_is_console_safe(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    registry, doc = _ingest_and_register(tmp_path, knowledge, SECRET)
    for coll in (knowledge, _MuteCollection()):
        message = registry.deletion_preview(doc.doc_id, coll)["message"]
        message.encode("ascii")  # raises if a non-ASCII char sneaks in


# ---------------------------------------------------------------------------
# 5. the uploads path (ingestion.delete_ingested_doc)
# ---------------------------------------------------------------------------

def _upload_env(tmp_path, monkeypatch, collection):
    """Point the uploads catalog, folder and collection at temp doubles."""
    from puffpai import ingestion as ing

    catalog = tmp_path / "doc_index.json"
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(ing, "_doc_index", lambda: catalog)
    monkeypatch.setattr(ing, "_upload_dir", lambda: uploads)
    monkeypatch.setattr(ing, "_get_user_uploads_collection",
                        lambda: collection)
    return ing, catalog, uploads


def _seed_upload(ing, catalog, uploads, collection, file_id="doc1.txt"):
    collection.add(
        ids=[f"{file_id}_chunk_0", f"{file_id}_chunk_1"],
        documents=[SECRET, "Second half of the letter."],
        metadatas=[{"file_id": file_id, "chunk_index": 0},
                   {"file_id": file_id, "chunk_index": 1}],
    )
    catalog.write_text(json.dumps([{"id": file_id, "filename": file_id}]),
                       encoding="utf-8")
    (uploads / file_id).write_text(SECRET, encoding="utf-8")


def test_upload_delete_removes_chunks_catalog_and_file(tmp_path, monkeypatch):
    _vault, knowledge = _collections(tmp_path)
    ing, catalog, uploads = _upload_env(tmp_path, monkeypatch, knowledge)
    _seed_upload(ing, catalog, uploads, knowledge)

    report = ing.delete_ingested_doc_report("doc1.txt")

    assert report["ok"] is True
    assert report["chunks_evicted"] == 2
    assert json.loads(catalog.read_text(encoding="utf-8")) == []
    assert not (uploads / "doc1.txt").exists()
    assert knowledge.count() == 0


def test_upload_delete_keeps_everything_when_the_store_is_unreachable(
        tmp_path, monkeypatch):
    """The old code wrote the catalog first and swallowed the ChromaDB error,
    which is how a 'deleted' document kept answering with no record left."""
    _vault, knowledge = _collections(tmp_path)
    ing, catalog, uploads = _upload_env(tmp_path, monkeypatch, knowledge)
    _seed_upload(ing, catalog, uploads, knowledge)
    monkeypatch.setattr(ing, "_get_user_uploads_collection",
                        lambda: _MuteCollection())

    report = ing.delete_ingested_doc_report("doc1.txt")

    assert report["ok"] is False
    assert report["catalog_removed"] is False
    assert report["file_removed"] is False
    assert json.loads(catalog.read_text(encoding="utf-8"))[0]["id"] == "doc1.txt"
    assert (uploads / "doc1.txt").exists()
    assert knowledge.count() == 2
    assert ing.delete_ingested_doc("doc1.txt") is False


def test_upload_delete_partial_eviction_is_not_success(tmp_path, monkeypatch):
    _vault, knowledge = _collections(tmp_path)
    ing, catalog, uploads = _upload_env(tmp_path, monkeypatch, knowledge)
    _seed_upload(ing, catalog, uploads, knowledge)
    monkeypatch.setattr(ing, "_get_user_uploads_collection",
                        lambda: _HalfDeletingCollection(knowledge))

    report = ing.delete_ingested_doc_report("doc1.txt")

    assert report["ok"] is False
    assert report["chunks_remaining"] == 1
    assert json.loads(catalog.read_text(encoding="utf-8"))[0]["id"] == "doc1.txt"


def test_upload_delete_endpoint_reports_the_truth(tmp_path, monkeypatch):
    """The API must not answer success:true over content still in the store."""
    from puffpai import api_server

    monkeypatch.setattr(
        "puffpai.ingestion.delete_ingested_doc_report",
        lambda doc_id: {
            "ok": False, "doc_id": doc_id, "catalog_removed": False,
            "chunks_evicted": 1, "chunks_remaining": 3, "file_removed": False,
            "reason": "'x' was not fully deleted.",
            "limits": ["3 are still in the knowledge store and still answering."],
        })
    api_server.app.config["TESTING"] = True
    client = api_server.app.test_client()
    from tests.test_api import _auth

    resp = client.delete("/ingest/document/doc1.txt", headers=_auth())
    body = resp.get_json()

    assert resp.status_code == 200
    assert body["success"] is False
    assert body["chunks_remaining"] == 3
    assert "still answering" in " ".join(body["limits"])


# ---------------------------------------------------------------------------
# 6. eviction reporting, on its own
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_id", ["", "   ", None])
def test_evict_document_refuses_an_empty_id(bad_id):
    result = evict_document(object(), bad_id)
    assert result["ok"] is False
    assert result["evicted"] == 0


def test_document_chunk_ids_returns_none_when_the_store_is_mute():
    assert document_chunk_ids(_MuteCollection(), "doc_x") is None
    assert document_chunk_ids(None, "doc_x") is None
