"""Revocation that actually revokes - provenance in the store, and eviction.

A revoked pack whose chunks keep answering questions has not been revoked; it
has been annotated. These tests are the difference between the two.

The knowledge store here is a real ChromaDB collection with an offline
embedder, not a stand-in, because the two things under test - metadata ``where``
filtering and delete-by-id - are exactly the parts a hand-written double would
get politely wrong.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

import json
import os

import pytest

from puffpai import source_fetch
from puffpai.knowledge.auto_index import (
    STATUS_COMPLETE,
    STATUS_REVOKED,
    PackIndex,
    index_pack,
    provenance_for_pack,
)
from puffpai.knowledge.ingestion import (
    ContentSource,
    KnowledgeIngestion,
    TargetZone,
    sanitize_provenance,
)
from puffpai.knowledge.rag import DualZoneRAG

POISON = "The correct dose is one hundred times the label amount."


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _collections(tmp_path):
    """Two real ChromaDB collections using the suite's offline embedder."""
    import chromadb
    from tests.test_knowledge import _HashEmbed

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    vault = client.get_or_create_collection(
        name="puffpai_vault", embedding_function=_HashEmbed())
    knowledge = client.get_or_create_collection(
        name="puffpai_knowledge", embedding_function=_HashEmbed())
    return vault, knowledge


def _provenance(source_id="kiwix_wikimed", label="WikiMed medical encyclopedia"):
    """A provenance record shaped exactly as source_fetch writes one."""
    return {
        "schema": source_fetch.PROVENANCE_SCHEMA,
        "source_id": source_id,
        "label": label,
        "kind": "zim",
        "domain": "health",
        "source_url": f"https://packs.example.org/{source_id}.zim",
        "final_url": f"https://mirror.example.org/{source_id}.zim",
        "license": "CC-BY-SA-4.0",
        "license_url": "https://example.org/license",
        "commercial_use": True,
        "fetched_at": "2026-08-29T12:00:00+00:00",
        "bytes": 1234,
        "sha256": "a" * 64,
        "sha256_pinned": False,
        "filename": f"{source_id}.zim",
        "path": f"/packs/{source_id}.zim",
        "registry_schema": "source_registry/v1",
        "content_trust": "untrusted-data",
        "revoked_at": None,
    }


def _ingest(knowledge, text, title, provenance=None):
    ingestion = KnowledgeIngestion(knowledge_collection=knowledge)
    return ingestion.ingest_text(
        text, title=title, source=ContentSource.ZIM_PACK,
        force_zone=TargetZone.KNOWLEDGE,
        provenance=source_fetch.chunk_metadata(provenance) if provenance else None,
    )


def _metadatas(collection, source_id=None):
    where = {"source_id": source_id} if source_id else None
    got = collection.get(where=where) if where else collection.get()
    return got.get("metadatas") or []


# ---------------------------------------------------------------------------
# 1. what a chunk now carries
# ---------------------------------------------------------------------------

def test_chunks_from_a_fetched_pack_carry_source_id_and_license(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    _ingest(knowledge, "Aspirin is a common analgesic. " * 20, "Aspirin",
            _provenance())

    metas = _metadatas(knowledge, "kiwix_wikimed")
    assert metas, "chunks should be findable by source id"
    for meta in metas:
        assert meta["source_id"] == "kiwix_wikimed"
        assert meta["source_license"] == "CC-BY-SA-4.0"
        assert meta["source_label"] == "WikiMed medical encyclopedia"
        assert meta["source_fetched_at"] == "2026-08-29T12:00:00+00:00"
        assert meta["source_sha256"] == "a" * 64
        assert meta["source_sha256_pinned"] is False
        # The pipeline's own keys are untouched.
        assert meta["zone"] == "puffpai_knowledge"
        assert meta["title"] == "Aspirin"


def test_a_multi_chunk_document_labels_every_chunk(tmp_path):
    """Eviction is per-chunk, so a document that labels only its first chunk
    would leave the rest behind."""
    _vault, knowledge = _collections(tmp_path)
    long_text = "\n\n".join(f"Paragraph {i}. " * 40 for i in range(12))
    doc = _ingest(knowledge, long_text, "Long article", _provenance())
    assert doc.chunk_count > 1
    assert len(_metadatas(knowledge, "kiwix_wikimed")) == doc.chunk_count


def test_chunks_without_provenance_gain_no_invented_metadata(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    _ingest(knowledge, "A note the owner typed. " * 20, "Owner note")

    metas = _metadatas(knowledge)
    assert metas
    for meta in metas:
        for key in meta:
            assert not key.startswith("source_"), f"invented {key}"
        assert meta["source"] == "zim_pack"  # the pre-existing key, unchanged
        assert meta["title"] == "Owner note"


def test_final_url_is_not_stamped_on_chunks():
    """A redirect target is a string the far end chose. It stays out of metadata."""
    stamped = source_fetch.chunk_metadata(_provenance())
    assert "final_url" not in json.dumps(stamped)
    assert "mirror.example.org" not in json.dumps(stamped)


def test_chunk_metadata_of_an_unlabelled_record_is_none():
    assert source_fetch.chunk_metadata(None) is None
    assert source_fetch.chunk_metadata({}) is None
    assert source_fetch.chunk_metadata({"label": "no id here"}) is None


def test_provenance_cannot_overwrite_the_keys_the_pipeline_owns(tmp_path):
    """A source that could rewrite its own zone would be choosing where it
    lands. Reserved keys are dropped, not merged."""
    hostile = {
        "source_id": "evil", "zone": "puffpai_vault", "title": "Trusted",
        "source": "manual_entry", "chunk_index": 99,
    }
    clean = sanitize_provenance(hostile)
    assert clean == {"source_id": "evil"}

    _vault, knowledge = _collections(tmp_path)
    ingestion = KnowledgeIngestion(knowledge_collection=knowledge)
    ingestion.ingest_text("Some text. " * 20, title="Real title",
                          source=ContentSource.ZIM_PACK,
                          force_zone=TargetZone.KNOWLEDGE,
                          provenance=hostile)
    for meta in _metadatas(knowledge):
        assert meta["zone"] == "puffpai_knowledge"
        assert meta["title"] == "Real title"
        assert meta["source"] == "zim_pack"


def test_sanitize_drops_values_the_store_cannot_hold():
    clean = sanitize_provenance({
        "source_id": "ok", "nested": {"a": 1}, "listy": [1, 2],
        "nothing": None, "blank": "   ", "num": 7, "flag": True,
        "long": "x" * 500,
    })
    assert clean == {"source_id": "ok", "num": 7, "flag": True,
                     "long": "x" * 200}


# ---------------------------------------------------------------------------
# 2. a retrieved answer can name itself
# ---------------------------------------------------------------------------

def test_a_retrieved_chunk_cites_its_source_and_license(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    _ingest(knowledge, "Aspirin is a common analgesic. " * 20, "Aspirin",
            _provenance())

    rag = DualZoneRAG(knowledge_collection=knowledge)
    response = rag.query("aspirin", n_results=3)
    assert response.results
    top = response.results[0]
    assert top.source_id == "kiwix_wikimed"
    assert top.citation == "WikiMed medical encyclopedia (CC-BY-SA-4.0)"
    assert "WikiMed medical encyclopedia (CC-BY-SA-4.0)" in response.context_string()
    assert response.cited_sources() == [
        ("kiwix_wikimed", "WikiMed medical encyclopedia (CC-BY-SA-4.0)")]
    assert response.unattributed_count == 0


def test_an_unlabelled_chunk_cites_nothing_rather_than_something_vague(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    _ingest(knowledge, "Aspirin is a common analgesic. " * 20, "Aspirin")

    rag = DualZoneRAG(knowledge_collection=knowledge)
    response = rag.query("aspirin", n_results=3)
    assert response.results
    assert response.results[0].citation == ""
    assert response.results[0].source_id == ""
    assert "Source:" not in response.context_string()
    assert response.cited_sources() == []
    assert response.unattributed_count == len(response.results)


# ---------------------------------------------------------------------------
# 3. eviction
# ---------------------------------------------------------------------------

def _two_packs(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    _ingest(knowledge, f"{POISON} " * 20, "Dosage",
            _provenance("kiwix_wikimed", "WikiMed medical encyclopedia"))
    _ingest(knowledge, "Telescopes gather light from distant stars. " * 20,
            "Telescopes",
            _provenance("kiwix_astronomy_stackexchange", "Astronomy Stack Exchange"))
    return knowledge


def test_evicting_one_source_leaves_the_others_intact(tmp_path):
    knowledge = _two_packs(tmp_path)
    before = knowledge.count()
    assert before > 1

    result = source_fetch.evict_source("kiwix_wikimed", knowledge)
    assert result["ok"] is True
    assert result["evicted"] > 0
    assert result["remaining"] == 0

    assert _metadatas(knowledge, "kiwix_wikimed") == []
    assert _metadatas(knowledge, "kiwix_astronomy_stackexchange")
    assert knowledge.count() == before - result["evicted"]


def test_revoked_content_no_longer_comes_back_from_a_query(tmp_path):
    """The whole point. Not 'is marked revoked' - does not answer."""
    knowledge = _two_packs(tmp_path)
    rag = DualZoneRAG(knowledge_collection=knowledge)

    before = rag.query("dose label amount", n_results=5)
    assert any(POISON in r.text for r in before.results)

    source_fetch.evict_source("kiwix_wikimed", knowledge)

    after = rag.query("dose label amount", n_results=5)
    assert not any(POISON in r.text for r in after.results)
    assert after.results, "the other pack should still answer"


def test_evicting_an_unknown_source_reports_finding_nothing(tmp_path):
    knowledge = _two_packs(tmp_path)
    result = source_fetch.evict_source("never_downloaded", knowledge)
    assert result["ok"] is False
    assert result["found"] == 0
    assert result["evicted"] == 0


def test_eviction_without_a_store_never_claims_success():
    result = source_fetch.evict_source("kiwix_wikimed", None)
    assert result["ok"] is False
    assert result["evicted"] == 0
    assert result["found"] is None
    assert "could not be reached" in result["reason"]


def test_a_partial_eviction_never_reports_success(tmp_path):
    """A store that accepts a delete and keeps the rows is the nightmare case."""
    knowledge = _two_packs(tmp_path)

    class HalfDeletingCollection:
        def __init__(self, real):
            self._real = real
            self.deleted = []

        def get(self, **kwargs):
            return self._real.get(**kwargs)

        def delete(self, ids):
            # Deletes all but one - the shape of a store that quietly fails.
            partial = ids[:-1]
            self.deleted.extend(partial)
            if partial:
                self._real.delete(ids=partial)

    half = HalfDeletingCollection(knowledge)
    result = source_fetch.evict_source("kiwix_wikimed", half)
    assert result["ok"] is False
    assert result["remaining"] == 1
    assert "still in the knowledge store" in result["reason"]


def test_a_store_that_refuses_the_delete_never_reports_success(tmp_path):
    knowledge = _two_packs(tmp_path)

    class RefusingCollection:
        def __init__(self, real):
            self._real = real

        def get(self, **kwargs):
            return self._real.get(**kwargs)

        def delete(self, ids):
            raise RuntimeError("read-only store")

    result = source_fetch.evict_source("kiwix_wikimed", RefusingCollection(knowledge))
    assert result["ok"] is False
    assert result["evicted"] == 0
    assert "refused the delete" in result["reason"]


def test_a_store_that_will_not_answer_never_reports_success(tmp_path):
    class MuteCollection:
        def get(self, **kwargs):
            raise RuntimeError("store is down")

        def delete(self, ids):  # pragma: no cover - never reached
            raise AssertionError("should not have tried to delete")

    result = source_fetch.evict_source("kiwix_wikimed", MuteCollection())
    assert result["ok"] is False
    assert result["found"] is None


# ---------------------------------------------------------------------------
# 4. revoke: the whole job, reported honestly
# ---------------------------------------------------------------------------

def _artifact(tmp_path, source_id="kiwix_wikimed"):
    """A downloaded artifact with its provenance sidecar, as fetch would leave it."""
    path = tmp_path / f"{source_id}.zim"
    path.write_bytes(b"pack bytes")
    record = _provenance(source_id)
    record["path"] = str(path)
    with open(str(path) + source_fetch.PROVENANCE_SUFFIX, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    return str(path)


def test_revoke_sets_aside_evicts_and_marks_the_pack_record(tmp_path):
    knowledge = _two_packs(tmp_path)
    artifact = _artifact(tmp_path)

    packs = PackIndex(str(tmp_path / "packs.json"))
    packs.mark("uuid-1", filename="kiwix_wikimed.zim", path=artifact,
               status=STATUS_COMPLETE, chunks=4, source_id="kiwix_wikimed")

    result = source_fetch.revoke(
        artifact, "poisoned upstream",
        knowledge_collection=knowledge, pack_index=packs,
    )

    assert result["ok"] is True
    assert result["limits"] == []
    assert result["chunks_evicted"] > 0
    # 1. file set aside, not destroyed
    assert not os.path.exists(artifact)
    assert os.path.exists(artifact + ".revoked")
    # 2. chunks gone, others untouched
    assert _metadatas(knowledge, "kiwix_wikimed") == []
    assert _metadatas(knowledge, "kiwix_astronomy_stackexchange")
    # 3. pack record no longer counts as indexed
    assert packs.get("uuid-1")["status"] == STATUS_REVOKED
    assert packs.is_indexed("uuid-1") is False
    # and the audit trail records what happened
    assert result["provenance"]["revoked_reason"] == "poisoned upstream"
    assert result["provenance"]["chunks_evicted"] == result["chunks_evicted"]


def test_revoke_of_a_pack_indexed_before_provenance_says_it_cannot_identify(tmp_path):
    """The honest-limit case. Content indexed before labels existed."""
    _vault, knowledge = _collections(tmp_path)
    _ingest(knowledge, f"{POISON} " * 20, "Dosage")  # no provenance: unlabelled
    artifact = _artifact(tmp_path)

    packs = PackIndex(str(tmp_path / "packs.json"))
    packs.mark("uuid-old", filename="kiwix_wikimed.zim", path=artifact,
               status=STATUS_COMPLETE, chunks=7)

    result = source_fetch.revoke(
        artifact, knowledge_collection=knowledge, pack_index=packs)

    assert result["ok"] is False
    assert result["limits"]
    limit = result["limits"][0]
    assert "7 chunks were indexed" in limit
    assert "cannot tell its chunks apart" in limit
    # The door it names has to be a door that opens: `knowledge reindex` is a
    # real subcommand, and the .revoked rename has to come off first or the
    # re-index will not even see the pack.
    assert "puffpai knowledge reindex" in limit
    assert ".revoked" in limit
    # It did not pretend, and it did not quietly delete someone else's chunks.
    assert knowledge.count() > 0
    lines = source_fetch.revocation_summary_lines(result)
    assert any("Still true:" in line for line in lines)
    assert "Partly revoked" in lines[0]


def test_revoke_of_a_never_indexed_pack_says_there_was_nothing_to_evict(tmp_path):
    _vault, knowledge = _collections(tmp_path)
    artifact = _artifact(tmp_path)
    packs = PackIndex(str(tmp_path / "packs.json"))

    result = source_fetch.revoke(
        artifact, knowledge_collection=knowledge, pack_index=packs)
    assert result["ok"] is False
    assert "nothing to evict" in " ".join(result["limits"])


def test_revoke_of_an_artifact_with_no_source_id_states_the_limit(tmp_path):
    path = tmp_path / "handplaced.zim"
    path.write_bytes(b"bytes")
    record = {"schema": source_fetch.PROVENANCE_SCHEMA, "label": "Hand placed"}
    with open(str(path) + source_fetch.PROVENANCE_SUFFIX, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    _vault, knowledge = _collections(tmp_path)

    result = source_fetch.revoke(str(path), knowledge_collection=knowledge)
    assert result["ok"] is False
    assert "no source id" in " ".join(result["limits"])


def test_revoke_reports_a_partial_eviction_as_incomplete(tmp_path):
    knowledge = _two_packs(tmp_path)
    artifact = _artifact(tmp_path)

    class HalfDeletingCollection:
        def __init__(self, real):
            self._real = real

        def get(self, **kwargs):
            return self._real.get(**kwargs)

        def delete(self, ids):
            partial = ids[:-1]
            if partial:
                self._real.delete(ids=partial)

    result = source_fetch.revoke(
        artifact, knowledge_collection=HalfDeletingCollection(knowledge))
    assert result["ok"] is False
    assert result["eviction"]["remaining"] == 1
    assert "still in the knowledge store" in " ".join(result["limits"])
    # The file is still set aside even though the eviction was incomplete.
    assert os.path.exists(artifact + ".revoked")


def test_revocation_summary_lines_are_ascii(tmp_path):
    knowledge = _two_packs(tmp_path)
    artifact = _artifact(tmp_path)
    result = source_fetch.revoke(artifact, "test", knowledge_collection=knowledge)
    for line in source_fetch.revocation_summary_lines(result):
        line.encode("ascii")


# ---------------------------------------------------------------------------
# 5. the join: a downloaded pack carries its own paperwork
# ---------------------------------------------------------------------------

def test_a_downloaded_pack_is_discovered_to_have_provenance(tmp_path):
    artifact = _artifact(tmp_path)
    found = provenance_for_pack(artifact)
    assert found is not None
    assert found["source_id"] == "kiwix_wikimed"
    assert found["source_license"] == "CC-BY-SA-4.0"


def test_a_hand_placed_pack_has_no_provenance_to_discover(tmp_path):
    path = tmp_path / "handplaced.zim"
    path.write_bytes(b"bytes")
    assert provenance_for_pack(str(path)) is None


def test_index_pack_stamps_discovered_provenance_onto_chunks(tmp_path):
    """The wizard wires nothing: the sidecar on disk is the join."""
    artifact = _artifact(tmp_path, "kiwix_appropedia")
    seen = {}

    class RecordingIngestion:
        def ingest_zim(self, path, max_articles=0, provenance=None):
            seen["provenance"] = provenance

            class Doc:
                chunk_count = 3
            return [Doc()]

    packs = PackIndex(str(tmp_path / "packs.json"))
    meta = {"uuid_hex": "uuid-2", "filename": "kiwix_appropedia.zim",
            "filepath": artifact, "entry_count": 10}
    result = index_pack(meta, RecordingIngestion(), packs)

    assert result["status"] == STATUS_COMPLETE
    assert seen["provenance"]["source_id"] == "kiwix_appropedia"
    assert packs.get("uuid-2")["source_id"] == "kiwix_appropedia"


def test_index_pack_of_an_unlabelled_pack_passes_no_provenance_argument(tmp_path):
    """Old ingestion objects must keep working, so the kwarg is not sent at all."""
    path = tmp_path / "handplaced.zim"
    path.write_bytes(b"bytes")

    class LegacyIngestion:
        def ingest_zim(self, path, max_articles=0):  # no provenance parameter
            class Doc:
                chunk_count = 2
            return [Doc()]

    packs = PackIndex(str(tmp_path / "packs.json"))
    meta = {"uuid_hex": "uuid-3", "filename": "handplaced.zim",
            "filepath": str(path), "entry_count": 1}
    result = index_pack(meta, LegacyIngestion(), packs)
    assert result["status"] == STATUS_COMPLETE
    assert packs.get("uuid-3")["source_id"] == ""


def test_pack_index_finds_a_record_by_path(tmp_path):
    packs = PackIndex(str(tmp_path / "packs.json"))
    packs.mark("uuid-4", filename="a.zim", path=str(tmp_path / "a.zim"),
               status=STATUS_COMPLETE)
    found = packs.find_by_path(str(tmp_path / "a.zim"))
    assert found is not None and found[0] == "uuid-4"
    assert packs.find_by_path(str(tmp_path / "nope.zim")) is None


@pytest.mark.parametrize("bogus", [None, "", "no_such_file.zim"])
def test_revoke_still_refuses_a_path_we_never_fetched(tmp_path, monkeypatch, bogus):
    monkeypatch.chdir(tmp_path)
    result = source_fetch.revoke(bogus)
    assert result["ok"] is False
    assert result["limits"] == []
    assert os.listdir(tmp_path) == []


# ---------------------------------------------------------------------------
# 6. the whole loop, using a real fetch to produce the sidecar
# ---------------------------------------------------------------------------

def test_download_index_answer_revoke_silence(tmp_path):
    """One pass through the real chain, with no hand-made fixtures.

    fetch writes the sidecar -> auto_index discovers it -> chunks carry the
    label -> a query cites it -> revoke evicts it -> the same query no longer
    returns it. If any link in that chain is fixture-shaped rather than real,
    this test is where it shows.
    """
    from tests.test_sourcing import ONLINE, FakeNet, _registry, _robots, _body_route

    zim_dir = tmp_path / "zim"
    zim_dir.mkdir()
    net = FakeNet({
        "https://packs.example.org/robots.txt": _robots("User-agent: *\nAllow: /\n"),
        "https://packs.example.org/zim/test_pack.zim": _body_route(b"pack bytes"),
    })
    fetched = source_fetch.fetch(
        "test_pack", ONLINE, size_acknowledged=True, dest_dir=str(zim_dir),
        registry=_registry(), transport=net, resolver=lambda h: ["93.184.216.34"],
        sleeper=lambda _s: None,
    )
    assert fetched["ok"] is True

    # auto_index finds the paperwork the download left behind. Nothing wired it.
    discovered = provenance_for_pack(fetched["path"])
    assert discovered is not None
    assert discovered["source_id"] == "test_pack"
    assert discovered["source_license"] == "CC-BY-SA-4.0"

    _vault, knowledge = _collections(tmp_path)
    ingestion = KnowledgeIngestion(knowledge_collection=knowledge)
    ingestion.ingest_text(
        f"{POISON} " * 20, title="Dosage", source=ContentSource.ZIM_PACK,
        force_zone=TargetZone.KNOWLEDGE, provenance=discovered,
    )

    rag = DualZoneRAG(knowledge_collection=knowledge)
    answer = rag.query("dose label amount", n_results=3)
    assert any(POISON in r.text for r in answer.results)
    assert answer.cited_sources() == [("test_pack", "Test Pack (CC-BY-SA-4.0)")]

    packs = PackIndex(str(tmp_path / "packs.json"))
    packs.mark("uuid-e2e", filename="test_pack.zim", path=fetched["path"],
               status=STATUS_COMPLETE, chunks=1, source_id="test_pack")

    revoked = source_fetch.revoke(
        fetched["path"], "supply chain concern",
        knowledge_collection=knowledge, pack_index=packs,
    )
    assert revoked["ok"] is True, revoked["limits"]

    silent = rag.query("dose label amount", n_results=3)
    assert not any(POISON in r.text for r in silent.results)
    assert packs.is_indexed("uuid-e2e") is False
    assert os.path.exists(fetched["path"] + ".revoked")
