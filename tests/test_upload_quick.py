"""Uploads always work: the word look-up takes every upload, helper or not."""
from __future__ import annotations

import socket

import pytest

from puffpai import ingestion
from puffpai.knowledge import reading_helper, upload_search

TEXT = ("Our garden plan. The tomatoes go in the north bed in May. "
        "Water the peppers every second morning. ") * 30


@pytest.fixture
def no_helper(tmp_path, monkeypatch):
    monkeypatch.setattr(reading_helper, "chroma_cache_dir", lambda: str(tmp_path / "c"))
    monkeypatch.setattr(reading_helper, "helper_dir", lambda: str(tmp_path / "o"))
    monkeypatch.setattr(ingestion, "_upload_embedder", None)

    def no_network(*a, **k):
        raise AssertionError("an upload tried to go online")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(ingestion, "classify_document", lambda text: "note")


def test_an_upload_without_the_helper_is_accepted_and_answers(tmp_path, no_helper):
    path = tmp_path / "garden_plan.txt"
    path.write_text(TEXT, encoding="utf-8")
    result = ingestion.ingest_file(str(path), "garden_plan.txt", auto_apply=False)
    assert not result.error, result.error
    hits = ingestion.query_user_uploads("when do the tomatoes go in")
    assert hits and hits[0]["filename"] == "garden_plan.txt"
    assert "tomatoes" in hits[0]["snippet"]
    assert hits[0]["space"] == "cosine" and 0.35 <= 1 - hits[0]["distance"] <= 0.85
    found = ingestion.search_indexed_docs("peppers water")
    assert found and found[0]["filename"] == "garden_plan.txt"


def test_deleting_an_upload_takes_its_words_out_too(tmp_path, no_helper):
    path = tmp_path / "plan.txt"
    path.write_text(TEXT, encoding="utf-8")
    result = ingestion.ingest_file(str(path), "plan.txt", auto_apply=False)
    assert upload_search.count(result.file_id) > 0
    report = ingestion.delete_ingested_doc_report(result.file_id)
    assert report["catalog_removed"], report
    assert upload_search.count(result.file_id) == 0
    assert ingestion.query_user_uploads("tomatoes north bed") == []


def test_the_word_look_up_drops_common_words_and_ranks():
    upload_search.add("f1", "a.txt", ["The tomatoes go in May.", "Peppers need water."])
    upload_search.add("f2", "b.txt", ["Nothing about gardens here at all."])
    assert upload_search.search("what is the") == []
    hits = upload_search.search("when do tomatoes go in")
    assert [h["file_id"] for h in hits] == ["f1"]
    assert upload_search.remove("f1") == 2 and upload_search.search("tomatoes") == []
