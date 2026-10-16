"""Tests for the Phase-5 knowledge / RAG layer.

These exercise the public reference implementations end to end:

* the document registry round-trips ingested-document metadata;
* the ingestion pipeline routes PII content to the vault zone and clean content
  to the knowledge zone;
* the dual-zone RAG returns provenance-labelled results and refuses vault-zone
  reads while the vault is locked;
* the auto-index scanner detects a new ``.zim`` pack in a folder and stops
  re-detecting it once it is recorded as indexed.

A real-ZIM integration test runs only when ``PUFFPAI_TEST_ZIM`` points at a small
local ``.zim`` file; it is skipped otherwise so the suite stays hermetic.

Run:
    pytest tests/test_knowledge.py
"""
from __future__ import annotations

import lzma
import os
import struct

import pytest

from puffpai.knowledge.ingestion import (
    KnowledgeIngestion, ContentSource, TargetZone,
)
from puffpai.knowledge import zim_reader
from puffpai.knowledge.registry import DocumentRegistry
from puffpai.knowledge.rag import DualZoneRAG, ResultSource
from puffpai.knowledge.auto_index import (
    PackIndex, find_new_packs, index_pack, STATUS_COMPLETE, STATUS_ERROR,
)
from puffpai.security.vault import Vault

_ZIM_MAGIC = 72173914


# ── test doubles ─────────────────────────────────────────────────────────────

class RecordingCollection:
    """Minimal ChromaDB-collection stand-in that records what was added."""

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.documents: list[str] = []
        self.metadatas: list[dict] = []

    def add(self, ids, documents, metadatas) -> None:
        self.ids.extend(ids)
        self.documents.extend(documents)
        self.metadatas.extend(metadatas)

    def count(self) -> int:
        return len(self.ids)


class _HashEmbed:
    """Deterministic, offline embedding function for hermetic RAG tests.

    Real retrieval quality is irrelevant here — the tests assert on provenance
    labelling and vault gating, not ranking — so a cheap character-bucket hash
    keeps the suite free of any model download.
    """

    def __init__(self) -> None:  # pragma: no cover - trivial
        pass

    def __call__(self, input):  # noqa: A002 - chroma's parameter name
        out = []
        for text in input:
            vec = [0.0] * 32
            for ch in (text or "").lower():
                vec[ord(ch) % 32] += 1.0
            out.append(vec)
        return out

    # ChromaDB's persistent-collection path uses these explicit hooks.
    def embed_query(self, input):  # noqa: A002
        return self(input)

    def embed_documents(self, input):  # noqa: A002
        return self(input)

    @staticmethod
    def name() -> str:
        return "puffpai_test_hashembed"

    def get_config(self) -> dict:
        return {}

    @staticmethod
    def build_from_config(config):  # pragma: no cover - chroma plumbing
        return _HashEmbed()


def _temp_collections(tmp_path):
    """Two real ChromaDB collections in a temp dir, using the offline embedder."""
    import chromadb

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    vault = client.get_or_create_collection(
        name="puffpai_vault", embedding_function=_HashEmbed())
    knowledge = client.get_or_create_collection(
        name="puffpai_knowledge", embedding_function=_HashEmbed())
    return vault, knowledge


# ── document registry ────────────────────────────────────────────────────────

def test_registry_roundtrip(tmp_path) -> None:
    vault, knowledge = _temp_collections(tmp_path)
    ingestion = KnowledgeIngestion(
        vault_collection=vault, knowledge_collection=knowledge)
    doc = ingestion.ingest_text(
        "Water boils at 100 degrees at sea level.",
        title="Boiling point", source=ContentSource.MANUAL_ENTRY)

    registry = DocumentRegistry(str(tmp_path / "registry.json"))
    registry.register(doc)

    assert registry.count == 1
    assert registry.total_chunks == doc.chunk_count
    assert registry.get(doc.doc_id)["title"] == "Boiling point"
    assert registry.search("boiling")[0]["doc_id"] == doc.doc_id

    # Persistence: a fresh instance reads the same file back.
    reloaded = DocumentRegistry(str(tmp_path / "registry.json"))
    assert reloaded.count == 1

    # delete() needs the collection holding the chunks: the row and the text go
    # together, or neither goes. See tests/test_document_deletion.py.
    assert reloaded.delete(doc.doc_id, knowledge) is True
    assert DocumentRegistry(str(tmp_path / "registry.json")).count == 0


# ── ingestion zone routing ───────────────────────────────────────────────────

def test_ingestion_routes_pii_to_vault() -> None:
    vault, knowledge = RecordingCollection(), RecordingCollection()
    ingestion = KnowledgeIngestion(vault_collection=vault,
                                   knowledge_collection=knowledge)

    doc = ingestion.ingest_text(
        "Reach me at jordan@example.com or 555-123-4567.",
        title="Contact", source=ContentSource.MANUAL_ENTRY)

    assert doc.has_pii is True
    assert doc.target_zone == TargetZone.VAULT
    assert vault.count() > 0
    assert knowledge.count() == 0


def test_ingestion_routes_clean_to_knowledge() -> None:
    vault, knowledge = RecordingCollection(), RecordingCollection()
    ingestion = KnowledgeIngestion(vault_collection=vault,
                                   knowledge_collection=knowledge)

    doc = ingestion.ingest_text(
        "The sky is blue and rivers flow toward the sea.",
        title="Nature", source=ContentSource.MANUAL_ENTRY)

    assert doc.has_pii is False
    assert doc.target_zone == TargetZone.KNOWLEDGE
    assert knowledge.count() > 0
    assert vault.count() == 0


# ── dual-zone RAG ────────────────────────────────────────────────────────────

def test_rag_returns_provenance_labelled_results(tmp_path) -> None:
    vault, knowledge = _temp_collections(tmp_path)
    knowledge.add(
        ids=["k1", "k2"],
        documents=["Python is a high-level programming language.",
                   "The Nile is a river in Africa."],
        metadatas=[{"title": "Python"}, {"title": "Nile"}])

    rag = DualZoneRAG(vault_collection=vault, knowledge_collection=knowledge)
    response = rag.query("programming language", n_results=2)

    assert response.knowledge_count >= 1
    assert response.vault_count == 0
    assert all(r.source == ResultSource.KNOWLEDGE for r in response.results)
    assert all(r.provenance_label == "from knowledge base" for r in response.results)
    assert "[From Knowledge Base]" in response.context_string()


def test_rag_vault_refused_while_locked(tmp_path) -> None:
    vault_coll, knowledge_coll = _temp_collections(tmp_path)
    vault_coll.add(ids=["v1"], documents=["My account PIN is 4242."],
                   metadatas=[{"title": "PIN"}])

    locked_vault = Vault(vault_dir=str(tmp_path / "vault_locked"))
    assert locked_vault.is_unlocked is False

    rag = DualZoneRAG(vault_collection=vault_coll,
                      knowledge_collection=knowledge_coll, vault=locked_vault)

    # Even authenticated, a locked vault is never read.
    locked_response = rag.query("PIN", n_results=3, authenticated=True)
    assert locked_response.vault_count == 0

    # Once the vault is unlocked, the vault zone becomes readable.
    open_vault = Vault(vault_dir=str(tmp_path / "vault_open"))
    open_vault.create("correct horse battery staple")
    assert open_vault.is_unlocked is True

    rag_open = DualZoneRAG(vault_collection=vault_coll,
                           knowledge_collection=knowledge_coll, vault=open_vault)
    open_response = rag_open.query("PIN", n_results=3, authenticated=True)
    assert open_response.vault_count >= 1
    assert any(r.provenance_label == "from your vault"
               for r in open_response.results)


# ── auto-index scanner ───────────────────────────────────────────────────────

def _write_fake_zim(path: str, entry_count: int = 3) -> None:
    """Write a minimal 80-byte ZIM header good enough for discovery + metadata."""
    header = struct.pack("<IHH", _ZIM_MAGIC, 6, 0)          # magic, major, minor
    header += b"\x00" * 16                                    # uuid
    header += struct.pack("<II", entry_count, 1)             # entry/cluster counts
    header += b"\x00" * (80 - len(header))                   # pad to 80 bytes
    with open(path, "wb") as f:
        f.write(header)


def test_autoindex_detects_new_pack(tmp_path) -> None:
    zdir = tmp_path / "packs"
    zdir.mkdir()
    zim_path = zdir / "sample_pack.zim"
    _write_fake_zim(str(zim_path))

    pack_index = PackIndex(path=str(tmp_path / "packs.json"))

    new_packs = find_new_packs(str(zdir), pack_index)
    assert len(new_packs) == 1
    assert new_packs[0]["filename"] == "sample_pack.zim"
    uuid_hex = new_packs[0]["uuid_hex"]

    # Once recorded as complete, it is no longer reported as new.
    pack_index.mark(uuid_hex, filename="sample_pack.zim",
                    status=STATUS_COMPLETE, docs=3, chunks=3)
    assert find_new_packs(str(zdir), pack_index) == []
    assert pack_index.is_indexed(uuid_hex) is True

    # A fresh PackIndex over the same file persists the record.
    assert PackIndex(path=str(tmp_path / "packs.json")).is_indexed(uuid_hex)


class _ZeroDocIngestion:
    """Ingestion stub that extracts nothing — models an empty/corrupt pack."""

    def ingest_zim(self, zim_path, max_articles=0):  # noqa: ARG002
        return []


def test_autoindex_zero_doc_pack_is_retryable(tmp_path) -> None:
    # A header-valid pack that yields 0 documents (corrupt/truncated download)
    # must NOT be recorded complete, or it would be skipped forever.
    zdir = tmp_path / "packs"
    zdir.mkdir()
    _write_fake_zim(str(zdir / "corrupt_pack.zim"))

    pack_index = PackIndex(path=str(tmp_path / "packs.json"))
    meta = find_new_packs(str(zdir), pack_index)[0]

    result = index_pack(meta, _ZeroDocIngestion(), pack_index)
    assert result["status"] == STATUS_ERROR
    assert result["status"] != STATUS_COMPLETE

    rec = pack_index.get(meta["uuid_hex"])
    assert rec["status"] == STATUS_ERROR
    assert rec["docs"] == 0
    assert rec["chunks"] == 0
    assert "corrupt" in rec.get("error", "").lower()

    # is_indexed stays False, so a rescan surfaces it again as new/retryable.
    assert pack_index.is_indexed(meta["uuid_hex"]) is False
    rescan = find_new_packs(str(zdir), pack_index)
    assert any(m["uuid_hex"] == meta["uuid_hex"] for m in rescan)

    # Persists across instances too.
    assert PackIndex(path=str(tmp_path / "packs.json")).is_indexed(
        meta["uuid_hex"]) is False


# ── cluster decompression bounds ─────────────────────────────────────────────
#
# A pack used to be something the owner put on the box by hand. source_fetch
# now downloads packs from registry-listed hosts into the watched ZIM dir,
# where auto_index picks them up unattended -- so the bytes _read_cluster
# parses are chosen by whoever controls that host. These tests pin the ceiling
# that keeps a crafted cluster from becoming an OOM kill.


def _cluster_body(*blobs: bytes) -> bytes:
    """A decompressed cluster body: 4-byte offset list, then the blob data."""
    offset_size = 4
    offsets, pos = [], offset_size * len(blobs)
    for blob in blobs:
        offsets.append(pos)
        pos += len(blob)
    return (b"".join(struct.pack("<I", o) for o in offsets)
            + b"".join(blobs))


def _write_cluster_zim(path: str, clusters: list, ptr_overrides=None) -> None:
    """Write a ZIM whose cluster pointer table and clusters are real.

    Only what ``_read_cluster`` actually dereferences is built out -- header,
    cluster pointers, cluster bytes -- which is enough to drive decompression
    without constructing a full directory and URL index. ``ptr_overrides``
    corrupts chosen pointers to model a hostile offset table.
    """
    cluster_ptr_pos = 80
    ptrs, pos = [], cluster_ptr_pos + 8 * len(clusters)
    for c in clusters:
        ptrs.append(pos)
        pos += len(c)
    checksum_pos = pos
    for idx, value in (ptr_overrides or {}).items():
        ptrs[idx] = value

    header = struct.pack("<IHH", _ZIM_MAGIC, 6, 0)           # magic, major, minor
    header += b"\x00" * 16                                   # uuid
    header += struct.pack("<II", 0, len(clusters))           # entry/cluster counts
    header += struct.pack("<QQ", 0, 0)                       # url/title ptrs absent
    header += struct.pack("<QQ", cluster_ptr_pos, 0)         # cluster ptr, mime list
    header += struct.pack("<II", 0, 0)                       # main/layout page
    header += struct.pack("<Q", checksum_pos)
    assert len(header) == 80

    with open(path, "wb") as f:
        f.write(header)
        f.write(b"".join(struct.pack("<Q", ptr) for ptr in ptrs))
        f.write(b"".join(clusters))
        f.write(b"\x00" * 16)   # MD5 tail the completeness check expects to exist


def test_cluster_ceiling_is_the_documented_64_mib() -> None:
    # One named constant, not a literal repeated per compression branch.
    assert zim_reader._MAX_CLUSTER_BYTES == 64 * 1024 * 1024


def test_normal_lzma_cluster_still_reads(tmp_path) -> None:
    # The cap must not cost us ordinary packs: a real LZMA cluster under the
    # ceiling round-trips its blobs untouched.
    body = _cluster_body(b"<html>first article</html>", b"second blob")
    zim = tmp_path / "ok_lzma.zim"
    _write_cluster_zim(str(zim), [bytes([4]) + lzma.compress(body)])

    with zim_reader.ZIMReader(str(zim)) as reader:
        blobs = reader._read_cluster(0)

    assert blobs == [b"<html>first article</html>", b"second blob"]


def test_normal_uncompressed_cluster_still_reads(tmp_path) -> None:
    body = _cluster_body(b"plain bytes")
    zim = tmp_path / "ok_raw.zim"
    _write_cluster_zim(str(zim), [bytes([0]) + body])

    with zim_reader.ZIMReader(str(zim)) as reader:
        assert reader._read_cluster(0) == [b"plain bytes"]


def test_oversized_lzma_cluster_is_refused(tmp_path, monkeypatch) -> None:
    # A decompression bomb: ~1 MiB of zeros compresses to a few hundred bytes.
    # The ceiling is lowered rather than crafting a >64 MiB expansion, so the
    # test stays fast and light while driving the exact production branch.
    monkeypatch.setattr(zim_reader, "_MAX_CLUSTER_BYTES", 64 * 1024)

    bomb = lzma.compress(b"\x00" * (1024 * 1024))
    assert len(bomb) < 64 * 1024, "compressed bomb must pass the on-disk check"
    zim = tmp_path / "bomb.zim"
    _write_cluster_zim(str(zim), [bytes([4]) + bomb])

    with zim_reader.ZIMReader(str(zim)) as reader:
        with pytest.raises(zim_reader.ZIMClusterTooLargeError) as excinfo:
            reader._read_cluster(0)

    # One legible error naming the pack and the ceiling -- not a MemoryError,
    # and not a silently empty cluster.
    message = str(excinfo.value)
    assert "bomb.zim" in message
    assert "Oversized ZIM cluster" in message
    assert excinfo.value.cluster_num == 0
    assert excinfo.value.cap == 64 * 1024
    # It is a ValueError, which is what auto_index's pack scan already handles.
    assert isinstance(excinfo.value, ValueError)


def test_absurd_cluster_size_is_refused_before_the_read(tmp_path) -> None:
    # A corrupt/hostile offset table alone is enough: cluster 0's end is taken
    # from cluster 1's pointer, so a bogus pointer turns the read into a 1 TiB
    # allocation request. It must be refused without ever calling read().
    body = _cluster_body(b"harmless")
    clusters = [bytes([0]) + body, bytes([0]) + body]
    zim = tmp_path / "bad_offsets.zim"
    _write_cluster_zim(str(zim), clusters, ptr_overrides={1: 2 ** 40})

    with zim_reader.ZIMReader(str(zim)) as reader:
        reader._ensure_cluster_ptrs()   # load pointers before read() is booby-trapped

        def _explode(size=-1):  # pragma: no cover - must never run
            raise AssertionError(f"read({size}) attempted despite the ceiling")

        real_read = reader._fh.read
        reader._fh.read = _explode      # type: ignore[method-assign]
        try:
            with pytest.raises(zim_reader.ZIMClusterTooLargeError) as excinfo:
                reader._read_cluster(0)
        finally:
            reader._fh.read = real_read  # type: ignore[method-assign]

    assert excinfo.value.requested > zim_reader._MAX_CLUSTER_BYTES


def test_oversized_cluster_propagates_out_of_get_article(tmp_path,
                                                         monkeypatch) -> None:
    # get_article degrades every other cluster failure to empty content so one
    # bad article cannot kill a pack. The size refusal must NOT be degraded, or
    # a hostile pack would just look empty instead of being named.
    monkeypatch.setattr(zim_reader, "_MAX_CLUSTER_BYTES", 64 * 1024)
    zim = tmp_path / "bomb_article.zim"
    _write_cluster_zim(
        str(zim), [bytes([4]) + lzma.compress(b"\x00" * (1024 * 1024))])

    entry = zim_reader.DirectoryEntry(
        mime_type_idx=0, mime_type="text/html", namespace="C",
        url="Article", title="Article", cluster_number=0, blob_number=0)

    with zim_reader.ZIMReader(str(zim)) as reader:
        monkeypatch.setattr(reader, "_read_directory_entry", lambda idx: entry)
        with pytest.raises(zim_reader.ZIMClusterTooLargeError):
            reader.get_article(0)


class _OversizedPackIngestion:
    """Ingestion stub standing in for a pack whose cluster exceeds the cap."""

    def ingest_zim(self, zim_path, max_articles=0):  # noqa: ARG002
        raise zim_reader.ZIMClusterTooLargeError(
            zim_path, 7, 2 ** 31, zim_reader._MAX_CLUSTER_BYTES)


def test_autoindex_records_oversized_pack_as_unindexable(tmp_path) -> None:
    # The whole point of raising: the indexer files the pack and keeps going,
    # rather than dying on a MemoryError partway through the scan.
    zdir = tmp_path / "packs"
    zdir.mkdir()
    _write_fake_zim(str(zdir / "hostile_pack.zim"))

    pack_index = PackIndex(path=str(tmp_path / "packs.json"))
    meta = find_new_packs(str(zdir), pack_index)[0]

    result = index_pack(meta, _OversizedPackIngestion(), pack_index)

    assert result["status"] == STATUS_ERROR
    assert "Oversized ZIM cluster" in result["error"]

    rec = pack_index.get(meta["uuid_hex"])
    assert rec["status"] == STATUS_ERROR
    # Not marked indexed, so the pack is never treated as successfully ingested.
    assert pack_index.is_indexed(meta["uuid_hex"]) is False


# ── optional real-ZIM integration ────────────────────────────────────────────

@pytest.mark.skipif(not os.environ.get("PUFFPAI_TEST_ZIM"),
                    reason="set PUFFPAI_TEST_ZIM to a small .zim to run")
def test_real_zim_ingest_integration(tmp_path) -> None:
    zim_path = os.environ["PUFFPAI_TEST_ZIM"]
    if not os.path.isfile(zim_path):
        pytest.skip(f"PUFFPAI_TEST_ZIM not found: {zim_path}")

    from puffpai.knowledge.zim_reader import ZIMReader

    # Read-only parse of the real archive.
    with ZIMReader(zim_path) as reader:
        assert reader.entry_count > 0

    # Index a handful of articles into a temp store with the offline embedder.
    _, knowledge = _temp_collections(tmp_path)
    ingestion = KnowledgeIngestion(knowledge_collection=knowledge)
    docs = ingestion.ingest_zim(zim_path, max_articles=3)
    assert len(docs) >= 1
    assert all(d.target_zone == TargetZone.KNOWLEDGE for d in docs)
    assert knowledge.count() > 0
